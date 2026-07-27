// Package coordinator defines the Go control plane's abstraction over
// the C++ federated coordinator, plus two implementations: a real gRPC
// client (grpc_client.go) and an in-memory mock (mock_client.go) for
// tests that don't need a live coordinator.
//
// Application services (go/internal/application) depend only on the
// Client interface below, never on gRPC types directly — HTTP handlers
// call application services, which call this interface, which calls
// gRPC. See docs/go-coordinator-integration.md.
package coordinator

import (
	"context"
	"time"
)

// RunState mirrors the coordinator's RunState enum as a plain string
// (e.g. "RUNNING", "COMPLETED") rather than importing the generated
// protobuf enum type into application-layer code.
type RunState string

type RunSnapshot struct {
	RunID             string   `json:"run_id"`
	State             RunState `json:"state"`
	CurrentRound      uint64   `json:"current_round"`
	MaxRounds         uint32   `json:"max_rounds"`
	ModelVersion      string   `json:"model_version"`
	Algorithm         string   `json:"algorithm"`
	RegisteredWorkers uint32   `json:"registered_workers"`
	HealthyWorkers    uint32   `json:"healthy_workers"`
}

// TensorSpec mirrors fl.worker.v1.TensorManifest's shape-defining fields
// (name + shape only — byte_length/checksum are populated by whoever
// actually serializes tensor values, not by run configuration). See
// docs/create-run-wire-mapping.md.
type TensorSpec struct {
	Name  string
	Shape []uint64
}

// AggregationManifest mirrors fl.coordinator.v1.AggregationManifest — see
// docs/aggregation-manifests.md. A zero-value AggregationManifest (no
// names declared) means "no manifest declared for this run," which the
// coordinator treats permissively (accepts any submitted tensor name),
// matching FedAvg/FedProx/SCAFFOLD's pre-existing single-tensor tests.
type AggregationManifest struct {
	SharedParameterNames       []string
	PersonalizedParameterNames []string
	FrozenParameterNames       []string
	SchemaHash                 string
}

// ModelManifest mirrors fl.coordinator.v1.ModelManifest's run-configuration
// fields. This defines the coordinator's initial global model — an empty
// Tensors slice means a zero-tensor global model, which is not sufficient
// for a real distributed training run to produce a meaningful aggregate
// (see docs/create-run-wire-mapping.md).
type ModelManifest struct {
	ModelID             string
	ModelVersion        string
	Tensors             []TensorSpec
	AggregationManifest AggregationManifest
}

// SampleLevelDPConfig/UserLevelDPConfig/AdaptiveClippingConfig/PrivacyConfig
// mirror fl.privacy.v1's identically-named messages. See
// docs/privacy-mathematics.md for the Critical Privacy Rule these three
// mechanisms must never violate: their epsilon/delta values are never
// combined into one number anywhere in this package or the HTTP layer
// built on top of it.
type SampleLevelDPConfig struct {
	NoiseMultiplier float64
	MaxGradNorm     float64
	TargetDelta     float64
	Accountant      string // "rdp" | "prv" | "gdp"
	PoissonSampling bool
	EpsilonBudget   float64 // 0 = unset, no budget enforced
}

type UserLevelDPConfig struct {
	NoiseMultiplier      float64
	TargetDelta          float64
	Accountant           string
	InitialClippingBound float64
	WeightingStrategy    string
	SecureRandom         bool
	EpsilonBudget        float64
}

type AdaptiveClippingConfig struct {
	Enabled              bool
	TargetQuantile       float64
	ClipLearningRate     float64
	InitialClip          float64
	MinClip              float64
	MaxClip              float64
	CountNoiseMultiplier float64
	TargetDelta          float64
	EpsilonBudget        float64
}

// PrivacyMode mirrors fl.privacy.v1.PrivacyMode as a plain string ("" /
// "unspecified" and "none" both mean "not private" — see
// privacyModeToWire's mapping in mapper.go).
type PrivacyMode string

const (
	PrivacyModeNone        PrivacyMode = "none"
	PrivacyModeSampleLevel PrivacyMode = "sample_level_dp"
	PrivacyModeUserLevel   PrivacyMode = "user_level_dp"
	PrivacyModeHybrid      PrivacyMode = "hybrid_dp"
)

type PrivacyConfig struct {
	Mode                     PrivacyMode
	SampleLevel              SampleLevelDPConfig
	UserLevel                UserLevelDPConfig
	AdaptiveClipping         AdaptiveClippingConfig
	WarningThresholdFraction float64
}

type CreateRunRequest struct {
	RunID                 string
	Algorithm             string
	Weighting             string
	TotalClients          uint32
	TargetClientsPerRound uint32
	MaxRounds             uint32
	MinimumValidResults   uint32
	ClientSelectionSeed   uint64
	RoundTimeoutSeconds   uint32
	ServerLR              float64
	// Fields below close the CreateRun wire-mapping gap (see
	// docs/create-run-wire-mapping.md): without ClientIDs in particular,
	// the coordinator's AcquireTask could never select a client, so no
	// worker could ever receive a real task through the live gRPC path.
	ClientIDs        []string
	LocalEpochs      uint32
	BatchSize        uint32
	LearningRate     float64
	Momentum         float64
	WeightDecay      float64
	FedProxMu        float64
	TaskLeaseSeconds uint32
	MaxTaskRetries   uint32
	ModelManifest    ModelManifest
	RequestID        string
	// Zero-value (Mode == "") means "no privacy_config sent at all" —
	// mapped to PRIVACY_MODE_UNSPECIFIED on the wire, unchanged
	// pre-existing (non-private) behavior. See docs/hybrid-dp.md.
	Privacy PrivacyConfig
}

// SampleLevelLedgerEntry/UserLevelLedgerEntry/AdaptiveClippingLedgerEntry
// mirror fl.privacy.v1's identically-named messages — one accounting
// step for exactly one mechanism. Never merge fields across these three
// types into a combined view; see PrivacyMetricsSnapshot's doc comment
// for the one place a cross-mechanism *summary* (not combination) is
// appropriate.
type SampleLevelLedgerEntry struct {
	RunID           string  `json:"run_id"`
	RoundID         uint64  `json:"round_id"`
	ClientID        string  `json:"client_id"`
	Epsilon         float64 `json:"epsilon"`
	Delta           float64 `json:"delta"`
	NoiseMultiplier float64 `json:"noise_multiplier"`
	SampleRate      float64 `json:"sample_rate"`
	Steps           uint64  `json:"steps"`
	Accountant      string  `json:"accountant"`
	RecordedAt      string  `json:"recorded_at"`
	EntryID         string  `json:"entry_id"`
}

type UserLevelLedgerEntry struct {
	RunID           string  `json:"run_id"`
	RoundID         uint64  `json:"round_id"`
	Epsilon         float64 `json:"epsilon"`
	Delta           float64 `json:"delta"`
	NoiseMultiplier float64 `json:"noise_multiplier"`
	ClippingBound   float64 `json:"clipping_bound"`
	NumClients      uint32  `json:"num_clients"`
}

type AdaptiveClippingLedgerEntry struct {
	RunID                         string  `json:"run_id"`
	RoundID                       uint64  `json:"round_id"`
	Epsilon                       float64 `json:"epsilon"`
	Delta                         float64 `json:"delta"`
	ClipValue                     float64 `json:"clip_value"`
	ObservedOverThresholdFraction float64 `json:"observed_over_threshold_fraction"`
}

// PrivacyLedger bundles all three mechanisms' full histories for one run
// — mirrors fl.coordinator.v1.GetPrivacyLedgerResponse. Each slice is
// independently empty when that mechanism isn't active; the three are
// never zipped/merged into per-round rows (a round with hybrid DP active
// has one user-level entry but as many sample-level entries as clients
// that round — they don't line up 1:1).
type PrivacyLedger struct {
	SampleLevelEntries []SampleLevelLedgerEntry      `json:"sample_level_entries"`
	UserLevelEntries   []UserLevelLedgerEntry        `json:"user_level_entries"`
	ClippingEntries    []AdaptiveClippingLedgerEntry `json:"clipping_entries"`
	NextPageToken      string                        `json:"next_page_token,omitempty"`
}

// PrivacyMetricsSnapshot mirrors fl.privacy.v1.PrivacyMetricsSnapshot — a
// point-in-time summary across all three (independent) mechanisms. Each
// mechanism's Has*/Epsilon/Delta trio is populated only if that
// mechanism is active; SampleEpsilon is the worst-case (max) epsilon
// across clients that have submitted an entry so far (a documented
// summarization choice, not a cross-mechanism combination — see
// cpp/coordinator/include/fl_coordinator/run_manager.hpp's
// PrivacyMetricsSnapshot doc comment, which this mirrors exactly).
type PrivacyMetricsSnapshot struct {
	RunID            string  `json:"run_id"`
	RoundID          uint64  `json:"round_id"`
	HasSampleLevel   bool    `json:"has_sample_level"`
	SampleEpsilon    float64 `json:"sample_epsilon"`
	SampleDelta      float64 `json:"sample_delta"`
	HasUserLevel     bool    `json:"has_user_level"`
	UserEpsilon      float64 `json:"user_epsilon"`
	UserDelta        float64 `json:"user_delta"`
	HasClipping      bool    `json:"has_clipping"`
	ClippingEpsilon  float64 `json:"clipping_epsilon"`
	ClippingDelta    float64 `json:"clipping_delta"`
	CurrentClipValue float64 `json:"current_clip_value"`
}

// PrivacyProjection mirrors fl.coordinator.v1.PrivacyProjection — a
// one-step-ahead preview. *BudgetRemaining is a pointer, nil when that
// mechanism's epsilon_budget is unset (0) on the C++ side (which reports
// +Inf over gRPC/protobuf — a valid IEEE754 double there, but Go's
// encoding/json refuses to marshal +Inf/NaN at all, so it is translated
// to nil here rather than crashing every response that contains it). nil
// is never confusable with "budget exhausted" (that would be a small or
// negative *value*), and omitempty drops the key entirely when nil so
// API consumers see its absence rather than a null they might
// mishandle.
type PrivacyProjection struct {
	HasSampleLevel               bool     `json:"has_sample_level"`
	SampleCurrentEpsilon         float64  `json:"sample_current_epsilon"`
	SampleProjectedNextEpsilon   float64  `json:"sample_projected_next_epsilon"`
	SampleBudgetRemaining        *float64 `json:"sample_budget_remaining,omitempty"`
	HasUserLevel                 bool     `json:"has_user_level"`
	UserCurrentEpsilon           float64  `json:"user_current_epsilon"`
	UserProjectedNextEpsilon     float64  `json:"user_projected_next_epsilon"`
	UserBudgetRemaining          *float64 `json:"user_budget_remaining,omitempty"`
	HasClipping                  bool     `json:"has_clipping"`
	ClippingCurrentEpsilon       float64  `json:"clipping_current_epsilon"`
	ClippingProjectedNextEpsilon float64  `json:"clipping_projected_next_epsilon"`
	ClippingBudgetRemaining      *float64 `json:"clipping_budget_remaining,omitempty"`
}

type Event struct {
	EventID      string `json:"event_id"`
	RunID        string `json:"run_id"`
	RoundID      uint64 `json:"round_id"`
	Type         string `json:"type"`
	ClientID     string `json:"client_id,omitempty"`
	WorkerID     string `json:"worker_id,omitempty"`
	ModelVersion string `json:"model_version,omitempty"`
	Timestamp    string `json:"timestamp,omitempty"`
	TraceID      string `json:"trace_id,omitempty"`
	Reason       string `json:"reason,omitempty"`
	// Populated for events that carry structured context beyond the
	// fixed fields above — e.g. a PRIVACY_BUDGET_WARNING/
	// PRIVACY_BUDGET_EXCEEDED event's "mechanism"/"policy" keys (see
	// docs/privacy-budget-policies.md). Never contains tensor payloads
	// or raw per-client norms — the C++ coordinator's own EventBus
	// forbids that at the source.
	Metadata map[string]string `json:"metadata,omitempty"`
}

// PersonalizationMetricRecord mirrors fl.coordinator.v1.PersonalizationMetricRecord
// (see proto/coordinator/coordinator.proto and Python's
// fl_platform.personalization.store). the Algorithm Expansion phase: submitted by workers
// running Ditto/Per-FedAvg (or any algorithm that trains a personalized
// model) alongside their training result; FedAvg/FedProx/SCAFFOLD/FedSAM
// clients never submit one, so a run with none of these is a normal,
// common case, not an error — see docs/fairness-metrics.md.
type PersonalizationMetricRecord struct {
	ClientID                  string  `json:"client_id"`
	RoundID                   uint64  `json:"round_id"`
	Algorithm                 string  `json:"algorithm"`
	GlobalLocalAccuracy       float64 `json:"global_local_accuracy"`
	PersonalizedLocalAccuracy float64 `json:"personalized_local_accuracy"`
	GlobalLocalLoss           float64 `json:"global_local_loss"`
	PersonalizedLocalLoss     float64 `json:"personalized_local_loss"`
	SampleCount               uint64  `json:"sample_count"`
	PersonalizedImprovement   float64 `json:"personalized_improvement"`
	PersonalizedModelVersion  uint32  `json:"personalized_model_version"`
	RecordedAt                string  `json:"recorded_at"`
	HasPersonalizedModel      bool    `json:"has_personalized_model"`
}

// WorkerPrivacyCapabilities mirrors fl.privacy.v1.WorkerPrivacyCapabilities
// — what a worker advertised at registration time. See
// docs/worker-privacy-capabilities.md: the coordinator's compatible-
// worker-only task assignment depends on this being truthful, not
// optimistic.
type WorkerPrivacyCapabilities struct {
	SupportsSampleLevelDP bool     `json:"supports_sample_level_dp"`
	OpacusVersion         string   `json:"opacus_version"`
	SupportedAccountants  []string `json:"supported_accountants"`
	SupportsSecureRandom  bool     `json:"supports_secure_random"`
}

// WorkerSummary mirrors fl.coordinator.v1.WorkerSummary — one registered
// worker's identity, status, and capabilities (including privacy).
type WorkerSummary struct {
	WorkerID            string                    `json:"worker_id"`
	Status              string                    `json:"status"`
	Device              string                    `json:"device"`
	CPUCount            uint32                    `json:"cpu_count"`
	GPUAvailable        bool                      `json:"gpu_available"`
	GPUCount            uint32                    `json:"gpu_count"`
	SupportedAlgorithms []string                  `json:"supported_algorithms"`
	Privacy             WorkerPrivacyCapabilities `json:"privacy"`
	RegisteredAtUnixS   float64                   `json:"registered_at_unix_s"`
	LastHeartbeatUnixS  float64                   `json:"last_heartbeat_unix_s"`
}

// Client is what the Go control plane needs from a federated coordinator.
// Deliberately narrow: no tensor payloads ever cross this interface (the
// Go service must not aggregate or proxy model tensors — see
// docs/go-coordinator-integration.md).
type Client interface {
	Health(ctx context.Context) (string, error)
	CreateRun(ctx context.Context, request CreateRunRequest) (RunSnapshot, error)
	StartRun(ctx context.Context, runID, traceID string) (RunSnapshot, error)
	PauseRun(ctx context.Context, runID, reason, traceID string) (RunSnapshot, error)
	ResumeRun(ctx context.Context, runID, traceID string) (RunSnapshot, error)
	CancelRun(ctx context.Context, runID, reason, traceID string) (RunSnapshot, error)
	GetRun(ctx context.Context, runID string) (RunSnapshot, error)

	// PollEvents returns events for runID published after afterEventID
	// (empty string: from the beginning of what's retained). The HTTP
	// layer's SSE/WebSocket handler calls this in a loop rather than
	// holding a single long-lived gRPC stream per browser connection —
	// see docs/event-streaming.md for why that's the simpler, more
	// reliable choice for the Coordinator Runtime phase's scope.
	PollEvents(ctx context.Context, runID, afterEventID string) ([]Event, error)

	// GetPersonalizationSummary returns the latest personalization metric
	// record per client that has ever submitted one for runID (empty
	// slice, not an error, for runs with none — see
	// PersonalizationMetricRecord's doc comment).
	GetPersonalizationSummary(ctx context.Context, runID string) ([]PersonalizationMetricRecord, error)

	// GetPrivacyMetrics/GetPrivacyLedger/GetPrivacyProjection are safe to
	// call for any run, private or not — a non-private run reports
	// Has*=false everywhere rather than erroring, so absence of privacy
	// is just as visible via this API as its presence. See
	// docs/privacy-ledger.md.
	GetPrivacyMetrics(ctx context.Context, runID string) (PrivacyMetricsSnapshot, error)
	// pageSize == 0 requests every entry unpaginated (the common case —
	// see PrivacyLedger's doc comment on why these histories are
	// typically small).
	GetPrivacyLedger(ctx context.Context, runID, pageToken string, pageSize uint32) (PrivacyLedger, error)
	GetPrivacyProjection(ctx context.Context, runID string) (PrivacyProjection, error)

	// ListWorkers returns every worker that has ever registered with the
	// coordinator process (run-agnostic, like RegisterWorker/Heartbeat) —
	// see docs/worker-privacy-capabilities.md. Empty slice, not an error,
	// when no worker has registered yet.
	ListWorkers(ctx context.Context) ([]WorkerSummary, error)

	// SecurityClient — see security_client.go for every type referenced
	// below. Every method here calls one of the coordinator's
	// ADMIN_CONTROL RPCs (docs/rpc-security-policy.md): they require this
	// process's own go-api mTLS certificate identity, are rejected
	// (ErrPermissionDenied) for anything else, and never carry private
	// key/nonce/secret material in either direction — see
	// docs/security-api.md.
	SecurityClient
}

// SecurityClient is split out from Client so security_client.go can be
// read as one self-contained unit (types + interface + both
// implementations live together) — see docs/security-api.md.
type SecurityClient interface {
	GetTransportSecurityStatus(ctx context.Context, traceID string) (TransportSecurityStatus, error)
	GetSecurityTrustModel(ctx context.Context, traceID string) (SecurityTrustModel, error)

	ListWorkerIdentities(ctx context.Context, traceID string) ([]WorkerIdentitySummary, error)
	GetWorkerIdentity(ctx context.Context, workerID, traceID string) (WorkerIdentitySummary, error)
	SuspendWorker(ctx context.Context, request WorkerLifecycleRequest) (WorkerLifecycleResult, error)
	ActivateWorker(ctx context.Context, request WorkerLifecycleRequest) (WorkerLifecycleResult, error)
	RevokeWorker(ctx context.Context, request WorkerLifecycleRequest) (WorkerLifecycleResult, error)

	ListWorkerSigningKeys(ctx context.Context, workerID, traceID string) ([]WorkerSigningKeySummary, error)
	RevokeWorkerSigningKey(ctx context.Context, request RevokeWorkerSigningKeyRequest) (WorkerSigningKeyRevocationResult, error)

	ListCoordinatorSigningKeys(ctx context.Context, traceID string) ([]CoordinatorSigningKeySummary, error)
	RotateCoordinatorSigningKey(ctx context.Context, request RotateCoordinatorSigningKeyRequest) (RotateCoordinatorSigningKeyResult, error)
	RevokeCoordinatorSigningKey(ctx context.Context, request RevokeCoordinatorSigningKeyRequest) (RevokeCoordinatorSigningKeyResult, error)

	// Security Events, Metrics, and Durable Audit Journal slice
	// (docs/security-events.md).
	ListSecurityEvents(ctx context.Context, request ListSecurityEventsRequest) (ListSecurityEventsResult, error)

	// Web Security Center, Event Centralization, and Security CI slice
	// (docs/security-event-source-health.md).
	GetSecurityEventSourceHealth(ctx context.Context, traceID string) (SecurityEventSourceHealthResult, error)

	// Secure User-Level DP Operations, Observability, and Release
	// Evidence slice (docs/secure-user-level-operations-audit.md).
	GetSecureUserLevelPrivacyStatus(ctx context.Context, traceID string) (SecureUserLevelPrivacyCapability, error)
	GetSecureUserLevelPrivacyHealth(ctx context.Context, traceID string) (SecureUserLevelPrivacyHealth, error)
	GetSecureUserLevelPrivacyBudget(ctx context.Context, runID, traceID string) (SecureUserLevelPrivacyBudget, error)
	ListSecureUserLevelPrivacyRounds(ctx context.Context, request ListSecureUserLevelPrivacyRoundsRequest) (ListSecureUserLevelPrivacyRoundsResult, error)
	GetSecureUserLevelPrivacyRound(ctx context.Context, runID string, roundID uint64, traceID string) (SecureUserLevelPrivacyRound, bool, error)
}

// Config for constructing a real gRPC client.
type Config struct {
	Address        string
	Insecure       bool
	DialTimeout    time.Duration
	RequestTimeout time.Duration
	// TLS supplies certificate/key/CA paths for the connection. Must be
	// non-nil (and fully valid) whenever Insecure is false — see
	// docs/mtls.md. Ignored when Insecure is true; NewGrpcClient never
	// consults it in that case, matching the closure-gate requirement
	// that insecure mode is a distinct, explicit opt-in, not merely "TLS
	// config happened to be empty".
	TLS *TLSConfig
}

// DefaultConfig returns an *insecure* development configuration —
// callers that want TLS/mTLS must set Insecure: false and populate TLS
// explicitly; there is no "secure by convention" default here, since a
// silently-secure-if-you-remember-to-configure-it default is exactly
// the kind of accidental insecurity this closure-gate work exists to
// close. See docs/mtls.md's "Insecure mode must require an explicit
// environment variable or command-line option" requirement — the
// equivalent Go-side requirement is that DefaultConfig's insecure
// choice is visible and named, not buried.
func DefaultConfig(address string) Config {
	return Config{
		Address:        address,
		Insecure:       true,
		DialTimeout:    5 * time.Second,
		RequestTimeout: 10 * time.Second,
	}
}
