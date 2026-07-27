package coordinator

// Secure User-Level DP Operations, Observability, and Release Evidence
// slice (docs/secure-user-level-operations-audit.md): typed Go bindings
// for the coordinator's new read-only ADMIN_CONTROL RPCs backing the
// secure user-level DP privacy runtime. Every type here mirrors a wire
// message that already excludes clear updates, individual norms,
// clipping factors, individual weights, noise tensors/state, masked
// bytes, shared secrets, keys, and nonces at the C++ layer (see
// proto/coordinator/coordinator.proto's message comments) -- this file
// does not perform its own redaction; role-based projection happens one
// layer up in the HTTP handlers (Work Areas J/K), same division of
// labor as security_client.go.

import (
	"context"

	coordinatorv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/coordinator/v1"
)

// SecureUserLevelPrivacyCapability mirrors
// fl.coordinator.v1.SecureUserLevelPrivacyCapabilityInfo -- the static
// mechanism description (unchanging design facts), never per-run state.
type SecureUserLevelPrivacyCapability struct {
	Available          bool     `json:"available"`
	Provider           string   `json:"provider"`
	AdjacencyModel     string   `json:"adjacency_model"`
	SamplingAssumption string   `json:"sampling_assumption"`
	SensitivityFormula string   `json:"sensitivity_formula"`
	NoisePlacement     string   `json:"noise_placement"`
	FixedWeight        float64  `json:"fixed_weight"`
	TrustLimitations   []string `json:"trust_limitations"`
}

// SecureUserLevelPrivacyHealth mirrors
// fl.coordinator.v1.SecureUserLevelPrivacyHealthResponse. Every
// *Status field is one of "ok"/"degraded"/"unavailable" -- never a raw
// error string (see the proto message's own comment).
type SecureUserLevelPrivacyHealth struct {
	Capability              SecureUserLevelPrivacyCapability `json:"capability"`
	ProviderStatus          string                           `json:"provider_status"`
	NoiseProviderStatus     string                           `json:"noise_provider_status"`
	AccountantStatus        string                           `json:"accountant_status"`
	LedgerStatus            string                           `json:"ledger_status"`
	EventJournalStatus      string                           `json:"event_journal_status"`
	LastSuccessfulRoundAt   string                           `json:"last_successful_round_at,omitempty"`
	ActiveRunsWithUserLevel uint64                           `json:"active_runs_with_user_level_dp"`
	ReconciliationRequired  bool                             `json:"reconciliation_required"`
	DegradedReason          string                           `json:"degraded_reason,omitempty"`
	CheckedAtUnixS          float64                          `json:"checked_at_unix_s"`
}

// SecureUserLevelPrivacyBudget mirrors
// fl.coordinator.v1.SecureUserLevelPrivacyBudgetResponse for one run.
type SecureUserLevelPrivacyBudget struct {
	RunID            string  `json:"run_id"`
	BudgetConfigured bool    `json:"budget_configured"`
	EpsilonSpent     float64 `json:"epsilon_spent"`
	EpsilonBudget    float64 `json:"epsilon_budget"`
	EpsilonRemaining float64 `json:"epsilon_remaining"`
	TargetDelta      float64 `json:"target_delta"`
	RoundsCommitted  uint64  `json:"rounds_committed"`
}

// SecureUserLevelPrivacyRound mirrors
// fl.coordinator.v1.SecureUserLevelPrivacyRoundSummary -- one already-
// committed accounting step, never a clear update or individual norm.
type SecureUserLevelPrivacyRound struct {
	RunID             string  `json:"run_id"`
	RoundID           uint64  `json:"round_id"`
	EpsilonAfterRound float64 `json:"epsilon_after_round"`
	TargetDelta       float64 `json:"target_delta"`
	NoiseMultiplier   float64 `json:"noise_multiplier"`
	ClippingBound     float64 `json:"clipping_bound"`
	NumClients        uint32  `json:"num_clients"`
	CommittedAtUnixS  float64 `json:"committed_at_unix_s"`
}

type ListSecureUserLevelPrivacyRoundsRequest struct {
	RunID       string
	AfterCursor string
	Limit       uint32
	TraceID     string
}

type ListSecureUserLevelPrivacyRoundsResult struct {
	Rounds     []SecureUserLevelPrivacyRound `json:"rounds"`
	NextCursor string                        `json:"next_cursor,omitempty"`
}

func wireSecureUserLevelPrivacyCapability(wire *coordinatorv1.SecureUserLevelPrivacyCapabilityInfo) SecureUserLevelPrivacyCapability {
	return SecureUserLevelPrivacyCapability{
		Available:          wire.GetAvailable(),
		Provider:           wire.GetProvider(),
		AdjacencyModel:     wire.GetAdjacencyModel(),
		SamplingAssumption: wire.GetSamplingAssumption(),
		SensitivityFormula: wire.GetSensitivityFormula(),
		NoisePlacement:     wire.GetNoisePlacement(),
		FixedWeight:        wire.GetFixedWeight(),
		TrustLimitations:   wire.GetTrustLimitations(),
	}
}

func wireSecureUserLevelPrivacyRound(wire *coordinatorv1.SecureUserLevelPrivacyRoundSummary) SecureUserLevelPrivacyRound {
	return SecureUserLevelPrivacyRound{
		RunID:             wire.GetRunId(),
		RoundID:           wire.GetRoundId(),
		EpsilonAfterRound: wire.GetEpsilonAfterRound(),
		TargetDelta:       wire.GetTargetDelta(),
		NoiseMultiplier:   wire.GetNoiseMultiplier(),
		ClippingBound:     wire.GetClippingBound(),
		NumClients:        wire.GetNumClients(),
		CommittedAtUnixS:  wire.GetCommittedAtUnixS(),
	}
}

func (c *GrpcClient) GetSecureUserLevelPrivacyHealth(ctx context.Context, traceID string) (SecureUserLevelPrivacyHealth, error) {
	response, err := c.stub.GetSecureUserLevelPrivacyHealth(ctx, &coordinatorv1.GetSecureUserLevelPrivacyHealthRequest{TraceId: traceID})
	if err != nil {
		return SecureUserLevelPrivacyHealth{}, mapSecurityGrpcError(err)
	}
	return SecureUserLevelPrivacyHealth{
		Capability:              wireSecureUserLevelPrivacyCapability(response.GetCapability()),
		ProviderStatus:          response.GetProviderStatus(),
		NoiseProviderStatus:     response.GetNoiseProviderStatus(),
		AccountantStatus:        response.GetAccountantStatus(),
		LedgerStatus:            response.GetLedgerStatus(),
		EventJournalStatus:      response.GetEventJournalStatus(),
		LastSuccessfulRoundAt:   response.GetLastSuccessfulRoundAt(),
		ActiveRunsWithUserLevel: response.GetActiveRunsWithUserLevelDp(),
		ReconciliationRequired:  response.GetReconciliationRequired(),
		DegradedReason:          response.GetDegradedReason(),
		CheckedAtUnixS:          response.GetCheckedAtUnixS(),
	}, nil
}

// GetSecureUserLevelPrivacyStatus is a thin reshaping of
// GetSecureUserLevelPrivacyHealth's embedded capability field -- a
// deliberate simplification (no separate C++ RPC exists for "status"
// alone, since status is a strict subset of what health already
// returns; see docs/secure-user-level-operations-audit.md).
func (c *GrpcClient) GetSecureUserLevelPrivacyStatus(ctx context.Context, traceID string) (SecureUserLevelPrivacyCapability, error) {
	health, err := c.GetSecureUserLevelPrivacyHealth(ctx, traceID)
	if err != nil {
		return SecureUserLevelPrivacyCapability{}, err
	}
	return health.Capability, nil
}

func (c *GrpcClient) GetSecureUserLevelPrivacyBudget(ctx context.Context, runID, traceID string) (SecureUserLevelPrivacyBudget, error) {
	response, err := c.stub.GetSecureUserLevelPrivacyBudget(ctx, &coordinatorv1.GetSecureUserLevelPrivacyBudgetRequest{
		RunId: runID, TraceId: traceID,
	})
	if err != nil {
		return SecureUserLevelPrivacyBudget{}, mapSecurityGrpcError(err)
	}
	return SecureUserLevelPrivacyBudget{
		RunID:            response.GetRunId(),
		BudgetConfigured: response.GetBudgetConfigured(),
		EpsilonSpent:     response.GetEpsilonSpent(),
		EpsilonBudget:    response.GetEpsilonBudget(),
		EpsilonRemaining: response.GetEpsilonRemaining(),
		TargetDelta:      response.GetTargetDelta(),
		RoundsCommitted:  response.GetRoundsCommitted(),
	}, nil
}

func (c *GrpcClient) ListSecureUserLevelPrivacyRounds(ctx context.Context, request ListSecureUserLevelPrivacyRoundsRequest) (ListSecureUserLevelPrivacyRoundsResult, error) {
	response, err := c.stub.ListSecureUserLevelPrivacyRounds(ctx, &coordinatorv1.ListSecureUserLevelPrivacyRoundsRequest{
		RunId: request.RunID, AfterCursor: request.AfterCursor, Limit: request.Limit, TraceId: request.TraceID,
	})
	if err != nil {
		return ListSecureUserLevelPrivacyRoundsResult{}, mapSecurityGrpcError(err)
	}
	rounds := make([]SecureUserLevelPrivacyRound, 0, len(response.GetRounds()))
	for _, wire := range response.GetRounds() {
		rounds = append(rounds, wireSecureUserLevelPrivacyRound(wire))
	}
	return ListSecureUserLevelPrivacyRoundsResult{Rounds: rounds, NextCursor: response.GetNextCursor()}, nil
}

// GetSecureUserLevelPrivacyRound returns (round, found, error) -- found
// is false (not an error) when the run has no committed round with that
// round_id, matching GetWorkerIdentity-style "not found is not itself a
// transport/permission error" precedent elsewhere in this package.
func (c *GrpcClient) GetSecureUserLevelPrivacyRound(ctx context.Context, runID string, roundID uint64, traceID string) (SecureUserLevelPrivacyRound, bool, error) {
	response, err := c.stub.GetSecureUserLevelPrivacyRound(ctx, &coordinatorv1.GetSecureUserLevelPrivacyRoundRequest{
		RunId: runID, RoundId: roundID, TraceId: traceID,
	})
	if err != nil {
		return SecureUserLevelPrivacyRound{}, false, mapSecurityGrpcError(err)
	}
	if !response.GetFound() {
		return SecureUserLevelPrivacyRound{}, false, nil
	}
	return wireSecureUserLevelPrivacyRound(response.GetRound()), true, nil
}
