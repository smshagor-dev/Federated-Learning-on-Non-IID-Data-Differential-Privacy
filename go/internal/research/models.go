package research

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
)

type PartitionStrategy string

const (
	PartitionIID          PartitionStrategy = "iid"
	PartitionDirichlet    PartitionStrategy = "dirichlet"
	PartitionPathological PartitionStrategy = "pathological"
	PartitionQuantitySkew PartitionStrategy = "quantity_skew"
)

type PrivacyMode string

const (
	PrivacyNone        PrivacyMode = "none"
	PrivacySampleLevel PrivacyMode = "sample_level_dp"
	PrivacyUserLevel   PrivacyMode = "user_level_dp"
	PrivacyHybrid      PrivacyMode = "hybrid_dp"
)

type SecureAggregationProvider string

const (
	SecureAggregationNone                  SecureAggregationProvider = "none"
	SecureAggregationNoDropoutExperimental SecureAggregationProvider = "SECAGG_NO_DROPOUT_EXPERIMENTAL"
)

type AdaptiveClippingMode string

const (
	AdaptiveClippingDisabled AdaptiveClippingMode = "disabled"
	AdaptiveClippingEnabled  AdaptiveClippingMode = "enabled"
)

type DeterminismLevel string

const (
	DeterminismStrictCPU             DeterminismLevel = "STRICT_CPU"
	DeterminismBestEffortAccelerator DeterminismLevel = "BEST_EFFORT_ACCELERATOR"
	DeterminismPerformance           DeterminismLevel = "PERFORMANCE"
)

type ExperimentState string

const (
	ExperimentStateCreated                  ExperimentState = "CREATED"
	ExperimentStateValidated                ExperimentState = "VALIDATED"
	ExperimentStatePreparing                ExperimentState = "PREPARING"
	ExperimentStateReady                    ExperimentState = "READY"
	ExperimentStateRunning                  ExperimentState = "RUNNING"
	ExperimentStateCancelRequested          ExperimentState = "CANCEL_REQUESTED"
	ExperimentStateCanceled                 ExperimentState = "CANCELED"
	ExperimentStateCompleted                ExperimentState = "COMPLETED"
	ExperimentStateCompletedWithPartialRuns ExperimentState = "COMPLETED_WITH_PARTIAL_RUNS"
	ExperimentStateFailed                   ExperimentState = "FAILED"
	ExperimentStateBlocked                  ExperimentState = "BLOCKED"
	ExperimentStateCorrupted                ExperimentState = "CORRUPTED"
)

type RunState string

const (
	RunStateCreated    RunState = "CREATED"
	RunStatePreparing  RunState = "PREPARING"
	RunStateRunning    RunState = "RUNNING"
	RunStateEvaluating RunState = "EVALUATING"
	RunStateCompleted  RunState = "COMPLETED"
	RunStateFailed     RunState = "FAILED"
	RunStateCanceled   RunState = "CANCELED"
	RunStateBlocked    RunState = "BLOCKED"
	RunStateLost       RunState = "LOST"
	RunStateCorrupted  RunState = "CORRUPTED"
)

type InclusionStatus string

const (
	InclusionIncluded InclusionStatus = "INCLUDED"
	InclusionExcluded InclusionStatus = "EXCLUDED"
)

type MetricScope string

const (
	MetricScopeGlobal            MetricScope = "GLOBAL"
	MetricScopeRound             MetricScope = "ROUND"
	MetricScopeAggregateClient   MetricScope = "AGGREGATE_CLIENT"
	MetricScopePrivacy           MetricScope = "PRIVACY"
	MetricScopeSecureAggregation MetricScope = "SECURE_AGGREGATION"
	MetricScopeRuntime           MetricScope = "RUNTIME"
)

type DatasetConfiguration struct {
	DatasetID                  string         `json:"dataset_id"`
	DatasetVersion             string         `json:"dataset_version"`
	DatasetChecksum            string         `json:"dataset_checksum"`
	SplitSeed                  int            `json:"split_seed"`
	TrainSplitFraction         float64        `json:"train_split_fraction"`
	ValidationSplitFraction    float64        `json:"validation_split_fraction"`
	TestSplitFraction          float64        `json:"test_split_fraction"`
	PreprocessingConfiguration map[string]any `json:"preprocessing_configuration"`
}

type PartitionConfiguration struct {
	Strategy              PartitionStrategy `json:"strategy"`
	NumClients            int               `json:"num_clients"`
	Seed                  int               `json:"seed"`
	MinimumClientSamples  int               `json:"minimum_client_samples"`
	Alpha                 *float64          `json:"alpha"`
	ClassesPerClient      *int              `json:"classes_per_client"`
	QuantitySkewSigma     *float64          `json:"quantity_skew_sigma"`
	PartitionManifestHash string            `json:"partition_manifest_hash"`
}

type ModelConfiguration struct {
	ModelID            string `json:"model_id"`
	ModelVersion       string `json:"model_version"`
	InitializationSeed int    `json:"initialization_seed"`
}

type AlgorithmConfiguration struct {
	AlgorithmID string         `json:"algorithm_id"`
	Parameters  map[string]any `json:"parameters"`
}

type PrivacyConfiguration struct {
	PrivacyMode            PrivacyMode `json:"privacy_mode"`
	NoiseMultiplier        *float64    `json:"noise_multiplier"`
	TargetDelta            *float64    `json:"target_delta"`
	UserLevelClipNorm      *float64    `json:"user_level_clip_norm"`
	SampleLevelMaxGradNorm *float64    `json:"sample_level_max_grad_norm"`
	EpsilonBudget          *float64    `json:"epsilon_budget"`
	CombinedEpsilon        *float64    `json:"combined_epsilon"`
	ClientWeighting        string      `json:"client_weighting"`
}

type SecureAggregationConfiguration struct {
	Provider                 SecureAggregationProvider `json:"provider"`
	DropoutRecoveryRequested bool                      `json:"dropout_recovery_requested"`
}

type AdaptiveClippingConfiguration struct {
	Mode                     AdaptiveClippingMode `json:"mode"`
	InitialBound             *float64             `json:"initial_bound"`
	MinBound                 *float64             `json:"min_bound"`
	MaxBound                 *float64             `json:"max_bound"`
	TargetQuantile           *float64             `json:"target_quantile"`
	LearningRate             *float64             `json:"learning_rate"`
	IndicatorNoiseMultiplier *float64             `json:"indicator_noise_multiplier"`
}

type RuntimeLimits struct {
	MaxRounds               int     `json:"max_rounds"`
	LocalEpochs             int     `json:"local_epochs"`
	BatchSize               int     `json:"batch_size"`
	LearningRate            float64 `json:"learning_rate"`
	EvaluationFrequency     int     `json:"evaluation_frequency"`
	SelectedClientsPerRound int     `json:"selected_clients_per_round"`
}

type SeedConfiguration struct {
	Seeds                []int `json:"seeds"`
	PartitionSeed        int   `json:"partition_seed"`
	WorkerAssignmentSeed int   `json:"worker_assignment_seed"`
	CoordinatorSeed      int   `json:"coordinator_seed"`
}

type ExperimentSpecification struct {
	SchemaVersion     int                            `json:"schema_version"`
	ExperimentID      string                         `json:"experiment_id"`
	ExperimentName    string                         `json:"experiment_name"`
	ResearchQuestion  string                         `json:"research_question"`
	Dataset           DatasetConfiguration           `json:"dataset"`
	Partition         PartitionConfiguration         `json:"partition"`
	Model             ModelConfiguration             `json:"model"`
	Algorithm         AlgorithmConfiguration         `json:"algorithm"`
	Privacy           PrivacyConfiguration           `json:"privacy"`
	SecureAggregation SecureAggregationConfiguration `json:"secure_aggregation"`
	AdaptiveClipping  AdaptiveClippingConfiguration  `json:"adaptive_clipping"`
	Runtime           RuntimeLimits                  `json:"runtime"`
	Seeds             SeedConfiguration              `json:"seeds"`
	DeterminismLevel  DeterminismLevel               `json:"determinism_level"`
	Tags              []string                       `json:"tags"`
	CreationTimestamp string                         `json:"creation_timestamp"`
	SpecificationHash string                         `json:"specification_hash"`
}

func (s ExperimentSpecification) CanonicalPayload() map[string]any {
	return map[string]any{
		"schema_version":     s.SchemaVersion,
		"experiment_id":      s.ExperimentID,
		"experiment_name":    s.ExperimentName,
		"research_question":  s.ResearchQuestion,
		"dataset":            s.Dataset,
		"partition":          s.Partition,
		"model":              s.Model,
		"algorithm":          s.Algorithm,
		"privacy":            s.Privacy,
		"secure_aggregation": s.SecureAggregation,
		"adaptive_clipping":  s.AdaptiveClipping,
		"runtime":            s.Runtime,
		"seeds":              s.Seeds,
		"determinism_level":  s.DeterminismLevel,
		"tags":               s.Tags,
		"creation_timestamp": s.CreationTimestamp,
		"specification_hash": "",
	}
}

func (s ExperimentSpecification) ComputeHash() (string, error) {
	blob, err := json.Marshal(s.CanonicalPayload())
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(blob)
	return hex.EncodeToString(digest[:]), nil
}

type ExperimentRegistryRecord struct {
	SchemaVersion             int                       `json:"schema_version"`
	ExperimentID              string                    `json:"experiment_id"`
	DisplayName               string                    `json:"display_name"`
	ResearchQuestion          string                    `json:"research_question"`
	SpecificationHash         string                    `json:"specification_hash"`
	DatasetID                 string                    `json:"dataset_id"`
	DatasetVersion            string                    `json:"dataset_version"`
	DatasetChecksum           string                    `json:"dataset_checksum"`
	PartitionManifestHash     string                    `json:"partition_manifest_hash"`
	ModelID                   string                    `json:"model_id"`
	AlgorithmID               string                    `json:"algorithm_id"`
	PrivacyMode               PrivacyMode               `json:"privacy_mode"`
	SecureAggregationEnabled  bool                      `json:"secure_aggregation_enabled"`
	SecureAggregationProvider SecureAggregationProvider `json:"secure_aggregation_provider"`
	AdaptiveClippingEnabled   bool                      `json:"adaptive_clipping_enabled"`
	DeclaredSeedCount         int                       `json:"declared_seed_count"`
	CurrentState              ExperimentState           `json:"current_state"`
	SuccessfulRunCount        int                       `json:"successful_run_count"`
	FailedRunCount            int                       `json:"failed_run_count"`
	CanceledRunCount          int                       `json:"canceled_run_count"`
	BlockedRunCount           int                       `json:"blocked_run_count"`
	CreatedAt                 string                    `json:"created_at"`
	UpdatedAt                 string                    `json:"updated_at"`
	CreatedActor              string                    `json:"created_actor"`
	RecordVersion             int                       `json:"record_version"`
	StorageFormatVersion      int                       `json:"storage_format_version"`
	ArtifactManifestHash      string                    `json:"artifact_manifest_hash"`
	EnvironmentManifestHash   string                    `json:"environment_manifest_hash"`
	Degraded                  bool                      `json:"degraded"`
	DegradedReason            string                    `json:"degraded_reason"`
}

type AttemptHistoryRecord struct {
	Attempt       int      `json:"attempt"`
	State         RunState `json:"state"`
	StartedAt     string   `json:"started_at"`
	CompletedAt   string   `json:"completed_at"`
	FailureReason string   `json:"failure_reason"`
}

type ExperimentRunRecord struct {
	SchemaVersion           int                    `json:"schema_version"`
	ExperimentID            string                 `json:"experiment_id"`
	SpecificationHash       string                 `json:"specification_hash"`
	Seed                    int                    `json:"seed"`
	RunID                   string                 `json:"run_id"`
	RunAttempt              int                    `json:"run_attempt"`
	CurrentState            RunState               `json:"current_state"`
	PartitionManifestHash   string                 `json:"partition_manifest_hash"`
	ModelInitializationSeed int                    `json:"model_initialization_seed"`
	TrainingSeed            int                    `json:"training_seed"`
	WorkerAssignmentSeed    int                    `json:"worker_assignment_seed"`
	StartTimestamp          string                 `json:"start_timestamp"`
	CompletionTimestamp     string                 `json:"completion_timestamp"`
	LastHeartbeat           string                 `json:"last_heartbeat"`
	CurrentRound            int                    `json:"current_round"`
	ExpectedRoundCount      int                    `json:"expected_round_count"`
	ModelVersion            string                 `json:"model_version"`
	EnvironmentManifestHash string                 `json:"environment_manifest_hash"`
	MetricSchemaVersion     int                    `json:"metric_schema_version"`
	ResultSummaryHash       string                 `json:"result_summary_hash"`
	FailureCount            int                    `json:"failure_count"`
	RetryLineage            []int                  `json:"retry_lineage"`
	InclusionStatus         InclusionStatus        `json:"inclusion_status"`
	ExclusionReason         string                 `json:"exclusion_reason"`
	ArtifactManifestHash    string                 `json:"artifact_manifest_hash"`
	RecordVersion           int                    `json:"record_version"`
	AttemptHistory          []AttemptHistoryRecord `json:"attempt_history"`
}

type ArtifactManifestEntry struct {
	ArtifactID         string `json:"artifact_id"`
	RelativePath       string `json:"relative_path"`
	ArtifactType       string `json:"artifact_type"`
	SchemaVersion      int    `json:"schema_version"`
	MIMEType           string `json:"mime_type"`
	ByteSize           int    `json:"byte_size"`
	SHA256Checksum     string `json:"sha256_checksum"`
	CreatedAt          string `json:"created_at"`
	Producer           string `json:"producer"`
	SanitizationStatus string `json:"sanitization_status"`
	RetentionClass     string `json:"retention_class"`
	PublicSafe         bool   `json:"public_safe"`
}

type ArtifactManifest struct {
	SchemaVersion int                     `json:"schema_version"`
	Entries       []ArtifactManifestEntry `json:"entries"`
	ManifestHash  string                  `json:"manifest_hash"`
}

type EnvironmentManifest struct {
	SchemaVersion             int               `json:"schema_version"`
	GeneratedAt               string            `json:"generated_at"`
	OperatingSystem           string            `json:"operating_system"`
	Architecture              string            `json:"architecture"`
	CPUSummary                string            `json:"cpu_summary"`
	GPUSummary                string            `json:"gpu_summary"`
	PythonVersion             string            `json:"python_version"`
	NumPyVersion              string            `json:"numpy_version"`
	PyTorchVersion            string            `json:"pytorch_version"`
	OpacusVersion             string            `json:"opacus_version"`
	GoVersion                 string            `json:"go_version"`
	NodeVersion               string            `json:"node_version"`
	DependencyLockfileHashes  map[string]string `json:"dependency_lockfile_hashes"`
	ThreadSettings            map[string]string `json:"thread_settings"`
	DeterminismPolicy         string            `json:"determinism_policy"`
	SecureAggregationProvider string            `json:"secure_aggregation_provider"`
	GitRevision               string            `json:"git_revision"`
	DirtyWorkingTree          bool              `json:"dirty_working_tree"`
	SanitizedDiffSummaryHash  string            `json:"sanitized_diff_summary_hash"`
	ManifestHash              string            `json:"manifest_hash"`
}

type EventRecord struct {
	SchemaVersion  int    `json:"schema_version"`
	ExperimentID   string `json:"experiment_id"`
	RunID          string `json:"run_id"`
	Seed           *int   `json:"seed"`
	Sequence       int64  `json:"sequence"`
	Timestamp      string `json:"timestamp"`
	EventType      string `json:"event_type"`
	Actor          string `json:"actor"`
	Reason         string `json:"reason"`
	RecordChecksum string `json:"record_checksum"`
}

type MetricRecord struct {
	SchemaVersion   int         `json:"schema_version"`
	ExperimentID    string      `json:"experiment_id"`
	RunID           string      `json:"run_id"`
	Seed            int         `json:"seed"`
	MetricScope     MetricScope `json:"metric_scope"`
	MetricName      string      `json:"metric_name"`
	NumericValue    float64     `json:"numeric_value"`
	Unit            string      `json:"unit"`
	Round           int         `json:"round"`
	ModelVersion    string      `json:"model_version"`
	Timestamp       string      `json:"timestamp"`
	SourceComponent string      `json:"source_component"`
	Tags            []string    `json:"tags"`
	RecordChecksum  string      `json:"record_checksum"`
}

type RuntimeHealth struct {
	Status                string `json:"status"`
	RegistryStorageStatus string `json:"registry_storage_status"`
	EventJournalStatus    string `json:"event_journal_status"`
	MetricJournalStatus   string `json:"metric_journal_status"`
	ArtifactStoreStatus   string `json:"artifact_store_status"`
	ActiveExperimentCount int    `json:"active_experiment_count"`
	ActiveRunCount        int    `json:"active_run_count"`
	CorruptionCount       int    `json:"corruption_count"`
	DegradedReason        string `json:"degraded_reason,omitempty"`
}
