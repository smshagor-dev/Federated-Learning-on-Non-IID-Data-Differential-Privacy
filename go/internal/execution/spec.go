package execution

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
)

const CurrentSchemaVersion = 1

type Backend string

const (
	BackendLocal       Backend = "local"
	BackendDistributed Backend = "distributed"
)

type SchedulingMode string

const (
	SchedulingSynchronous             SchedulingMode = "synchronous"
	SchedulingDeadlineSemiSynchronous SchedulingMode = "deadline_based_semi_synchronous"
	SchedulingBufferedAsynchronous    SchedulingMode = "buffered_asynchronous"
	SchedulingStalenessAwareAsync     SchedulingMode = "staleness_aware_asynchronous"
)

type PrivacyMode string

const (
	PrivacyNone        PrivacyMode = "none"
	PrivacySampleLevel PrivacyMode = "sample_level_dp"
	PrivacyUserLevel   PrivacyMode = "user_level_dp"
	PrivacyHybrid      PrivacyMode = "hybrid_dp"
)

type PartitionSpec struct {
	Strategy          string  `json:"strategy"`
	Alpha             float64 `json:"alpha,omitempty"`
	ClassesPerClient  uint32  `json:"classes_per_client,omitempty"`
	QuantitySkewSigma float64 `json:"quantity_skew_sigma,omitempty"`
	MinimumClientSize uint32  `json:"minimum_client_size,omitempty"`
}

type DatasetSpec struct {
	Name      string        `json:"name"`
	Reference string        `json:"reference,omitempty"`
	Partition PartitionSpec `json:"partition"`
}

type TensorSpec struct {
	Name  string   `json:"name"`
	Shape []uint64 `json:"shape"`
}

type AggregationManifest struct {
	SharedParameterNames       []string `json:"shared_parameter_names,omitempty"`
	PersonalizedParameterNames []string `json:"personalized_parameter_names,omitempty"`
	FrozenParameterNames       []string `json:"frozen_parameter_names,omitempty"`
	SchemaHash                 string   `json:"schema_hash,omitempty"`
}

type ModelSpec struct {
	Name                string              `json:"name"`
	Version             string              `json:"version"`
	ArchitectureName    string              `json:"architecture_name,omitempty"`
	UpdateFormat        string              `json:"update_format"`
	CheckpointReference string              `json:"checkpoint_reference,omitempty"`
	Checksum            string              `json:"checksum,omitempty"`
	Tensors             []TensorSpec        `json:"tensors"`
	Aggregation         AggregationManifest `json:"aggregation"`
}

type AlgorithmSpec struct {
	Name string  `json:"name"`
	Mu   float64 `json:"mu,omitempty"`
}

type OptimizerSpec struct {
	LearningRate float64 `json:"learning_rate"`
	Momentum     float64 `json:"momentum,omitempty"`
	WeightDecay  float64 `json:"weight_decay,omitempty"`
	ServerLR     float64 `json:"server_lr"`
}

type FederationSpec struct {
	TotalClients          uint32         `json:"total_clients"`
	ClientIDs             []string       `json:"client_ids"`
	TargetClientsPerRound uint32         `json:"target_clients_per_round"`
	MinimumValidResults   uint32         `json:"minimum_valid_results"`
	Rounds                uint32         `json:"rounds"`
	LocalEpochs           uint32         `json:"local_epochs"`
	BatchSize             uint32         `json:"batch_size"`
	Weighting             string         `json:"weighting"`
	ClientSelectionSeed   uint64         `json:"client_selection_seed"`
	SchedulingMode        SchedulingMode `json:"scheduling_mode"`
	RoundTimeoutSeconds   uint32         `json:"round_timeout_seconds"`
	TaskLeaseSeconds      uint32         `json:"task_lease_seconds"`
	MaxTaskRetries        uint32         `json:"max_task_retries"`
	BufferSize            uint32         `json:"buffer_size,omitempty"`
	MaximumStaleness      uint32         `json:"maximum_staleness,omitempty"`
	CarryoverLateResults  bool           `json:"carryover_late_results,omitempty"`
}

type SampleLevelPrivacySpec struct {
	NoiseMultiplier float64 `json:"noise_multiplier"`
	MaxGradNorm     float64 `json:"max_grad_norm"`
	TargetDelta     float64 `json:"target_delta"`
	Accountant      string  `json:"accountant"`
	PoissonSampling bool    `json:"poisson_sampling"`
	EpsilonBudget   float64 `json:"epsilon_budget,omitempty"`
}

type UserLevelPrivacySpec struct {
	NoiseMultiplier      float64 `json:"noise_multiplier"`
	TargetDelta          float64 `json:"target_delta"`
	Accountant           string  `json:"accountant"`
	InitialClippingBound float64 `json:"initial_clipping_bound"`
	WeightingStrategy    string  `json:"weighting_strategy"`
	SecureRandom         bool    `json:"secure_random"`
	EpsilonBudget        float64 `json:"epsilon_budget,omitempty"`
}

type AdaptiveClippingSpec struct {
	Enabled              bool    `json:"enabled"`
	TargetQuantile       float64 `json:"target_quantile,omitempty"`
	ClipLearningRate     float64 `json:"clip_learning_rate,omitempty"`
	InitialClip          float64 `json:"initial_clip,omitempty"`
	MinClip              float64 `json:"min_clip,omitempty"`
	MaxClip              float64 `json:"max_clip,omitempty"`
	CountNoiseMultiplier float64 `json:"count_noise_multiplier,omitempty"`
	TargetDelta          float64 `json:"target_delta,omitempty"`
	EpsilonBudget        float64 `json:"epsilon_budget,omitempty"`
}

type PrivacySpec struct {
	Mode                     PrivacyMode               `json:"mode"`
	SampleLevel              SampleLevelPrivacySpec    `json:"sample_level,omitempty"`
	UserLevel                UserLevelPrivacySpec      `json:"user_level,omitempty"`
	AdaptiveClipping         AdaptiveClippingSpec      `json:"adaptive_clipping,omitempty"`
	WarningThresholdFraction float64                   `json:"warning_threshold_fraction,omitempty"`
}

type EvaluationSpec struct {
	EvaluateGlobal        bool `json:"evaluate_global"`
	EvaluatePerClient     bool `json:"evaluate_per_client"`
	EvaluateFairness      bool `json:"evaluate_fairness"`
	EvaluationBatchSize   uint32 `json:"evaluation_batch_size"`
}

type ArtifactSpec struct {
	Root                 string `json:"root"`
	PersistCheckpoints   bool   `json:"persist_checkpoints"`
	PersistRoundMetrics  bool   `json:"persist_round_metrics"`
	PersistClientMetrics bool   `json:"persist_client_metrics"`
	PersistEvents        bool   `json:"persist_events"`
}

type SecuritySpec struct {
	RequireAuthenticatedWorkers bool `json:"require_authenticated_workers"`
	RequireSignedTasks          bool `json:"require_signed_tasks"`
	RequireSignedResults        bool `json:"require_signed_results"`
	SecureAggregation           bool `json:"secure_aggregation"`
}

type Spec struct {
	SchemaVersion int            `json:"schema_version"`
	Name          string         `json:"name"`
	Backend       Backend        `json:"backend"`
	Dataset       DatasetSpec    `json:"dataset"`
	Model         ModelSpec      `json:"model"`
	Algorithm     AlgorithmSpec  `json:"algorithm"`
	Optimizer     OptimizerSpec  `json:"optimizer"`
	Federation    FederationSpec `json:"federation"`
	Privacy       PrivacySpec    `json:"privacy"`
	Evaluation    EvaluationSpec `json:"evaluation"`
	Artifacts     ArtifactSpec   `json:"artifacts"`
	Security      SecuritySpec   `json:"security"`
}

var (
	ErrInvalidSpec = errors.New("invalid execution specification")
)

func (s Spec) Validate() error {
	var problems []string
	if s.SchemaVersion != CurrentSchemaVersion {
		problems = append(problems, fmt.Sprintf("schema_version must be %d", CurrentSchemaVersion))
	}
	if strings.TrimSpace(s.Name) == "" {
		problems = append(problems, "name is required")
	}
	if s.Backend != BackendLocal && s.Backend != BackendDistributed {
		problems = append(problems, "backend must be local or distributed")
	}
	problems = append(problems, validateDataset(s.Dataset)...)
	problems = append(problems, validateModel(s.Model)...)
	problems = append(problems, validateAlgorithm(s.Algorithm)...)
	problems = append(problems, validateOptimizer(s.Optimizer)...)
	problems = append(problems, validateFederation(s.Backend, s.Federation)...)
	problems = append(problems, validatePrivacy(s.Privacy, s.Federation)...)
	if s.Evaluation.EvaluationBatchSize == 0 {
		problems = append(problems, "evaluation.evaluation_batch_size must be positive")
	}
	if strings.TrimSpace(s.Artifacts.Root) == "" {
		problems = append(problems, "artifacts.root is required")
	}
	if s.Backend == BackendDistributed && s.Security.SecureAggregation {
		// The current C++ coordinator can enable secure aggregation only as a
		// process-wide setting. A canonical execution spec must never pretend a
		// per-run flag was enforced when it was not wired end to end.
		problems = append(problems, "security.secure_aggregation is not yet per-run configurable on the distributed backend")
	}
	if len(problems) != 0 {
		sort.Strings(problems)
		return fmt.Errorf("%w: %s", ErrInvalidSpec, strings.Join(problems, "; "))
	}
	return nil
}

func validateDataset(dataset DatasetSpec) []string {
	var problems []string
	if strings.TrimSpace(dataset.Name) == "" {
		problems = append(problems, "dataset.name is required")
	}
	switch dataset.Partition.Strategy {
	case "iid":
	case "dirichlet":
		if !positiveFinite(dataset.Partition.Alpha) {
			problems = append(problems, "dataset.partition.alpha must be positive for dirichlet")
		}
	case "pathological":
		if dataset.Partition.ClassesPerClient == 0 {
			problems = append(problems, "dataset.partition.classes_per_client must be positive for pathological")
		}
	case "quantity_skew":
		if !positiveFinite(dataset.Partition.QuantitySkewSigma) {
			problems = append(problems, "dataset.partition.quantity_skew_sigma must be positive for quantity_skew")
		}
	default:
		problems = append(problems, "dataset.partition.strategy must be iid, dirichlet, pathological, or quantity_skew")
	}
	return problems
}

func validateModel(model ModelSpec) []string {
	var problems []string
	if strings.TrimSpace(model.Name) == "" {
		problems = append(problems, "model.name is required")
	}
	if strings.TrimSpace(model.Version) == "" {
		problems = append(problems, "model.version is required")
	}
	if strings.TrimSpace(model.UpdateFormat) == "" {
		problems = append(problems, "model.update_format is required")
	}
	if len(model.Tensors) == 0 {
		problems = append(problems, "model.tensors must contain the concrete global model manifest")
	}
	seen := map[string]struct{}{}
	for index, tensor := range model.Tensors {
		name := strings.TrimSpace(tensor.Name)
		if name == "" {
			problems = append(problems, fmt.Sprintf("model.tensors[%d].name is required", index))
			continue
		}
		if _, exists := seen[name]; exists {
			problems = append(problems, fmt.Sprintf("model tensor %q is duplicated", name))
		}
		seen[name] = struct{}{}
		if len(tensor.Shape) == 0 {
			problems = append(problems, fmt.Sprintf("model tensor %q must declare a shape", name))
		}
		for _, dimension := range tensor.Shape {
			if dimension == 0 {
				problems = append(problems, fmt.Sprintf("model tensor %q has a zero dimension", name))
				break
			}
		}
	}
	for _, name := range model.Aggregation.SharedParameterNames {
		if _, exists := seen[name]; !exists {
			problems = append(problems, fmt.Sprintf("shared parameter %q is absent from model.tensors", name))
		}
	}
	for _, name := range model.Aggregation.PersonalizedParameterNames {
		if _, exists := seen[name]; !exists {
			problems = append(problems, fmt.Sprintf("personalized parameter %q is absent from model.tensors", name))
		}
	}
	for _, name := range model.Aggregation.FrozenParameterNames {
		if _, exists := seen[name]; !exists {
			problems = append(problems, fmt.Sprintf("frozen parameter %q is absent from model.tensors", name))
		}
	}
	return problems
}

func validateAlgorithm(algorithm AlgorithmSpec) []string {
	var problems []string
	switch strings.ToLower(strings.TrimSpace(algorithm.Name)) {
	case "fedavg", "fedprox", "scaffold", "fedsam", "ditto", "per_fedavg":
	default:
		problems = append(problems, "algorithm.name is unsupported")
	}
	if strings.EqualFold(algorithm.Name, "fedprox") && algorithm.Mu < 0 {
		problems = append(problems, "algorithm.mu must be non-negative")
	}
	return problems
}

func validateOptimizer(optimizer OptimizerSpec) []string {
	var problems []string
	if !positiveFinite(optimizer.LearningRate) {
		problems = append(problems, "optimizer.learning_rate must be positive")
	}
	if optimizer.Momentum < 0 || optimizer.Momentum > 1 || math.IsNaN(optimizer.Momentum) {
		problems = append(problems, "optimizer.momentum must lie in [0,1]")
	}
	if optimizer.WeightDecay < 0 || math.IsNaN(optimizer.WeightDecay) || math.IsInf(optimizer.WeightDecay, 0) {
		problems = append(problems, "optimizer.weight_decay must be finite and non-negative")
	}
	if !positiveFinite(optimizer.ServerLR) {
		problems = append(problems, "optimizer.server_lr must be positive")
	}
	return problems
}

func validateFederation(backend Backend, federation FederationSpec) []string {
	var problems []string
	if federation.TotalClients == 0 {
		problems = append(problems, "federation.total_clients must be positive")
	}
	if federation.TargetClientsPerRound == 0 || federation.TargetClientsPerRound > federation.TotalClients {
		problems = append(problems, "federation.target_clients_per_round must lie in [1,total_clients]")
	}
	if federation.MinimumValidResults == 0 || federation.MinimumValidResults > federation.TargetClientsPerRound {
		problems = append(problems, "federation.minimum_valid_results must lie in [1,target_clients_per_round]")
	}
	if federation.Rounds == 0 {
		problems = append(problems, "federation.rounds must be positive")
	}
	if federation.LocalEpochs == 0 {
		problems = append(problems, "federation.local_epochs must be positive")
	}
	if federation.BatchSize == 0 {
		problems = append(problems, "federation.batch_size must be positive")
	}
	if federation.Weighting != "uniform" && federation.Weighting != "sample_count" {
		problems = append(problems, "federation.weighting must be uniform or sample_count")
	}
	if federation.RoundTimeoutSeconds == 0 {
		problems = append(problems, "federation.round_timeout_seconds must be positive")
	}
	if federation.TaskLeaseSeconds == 0 {
		problems = append(problems, "federation.task_lease_seconds must be positive")
	}
	if backend == BackendDistributed {
		if uint32(len(federation.ClientIDs)) != federation.TotalClients {
			problems = append(problems, "distributed executions require exactly total_clients unique client_ids")
		}
		seen := map[string]struct{}{}
		for _, clientID := range federation.ClientIDs {
			clientID = strings.TrimSpace(clientID)
			if clientID == "" {
				problems = append(problems, "distributed client_ids must be non-empty")
				continue
			}
			if _, exists := seen[clientID]; exists {
				problems = append(problems, fmt.Sprintf("distributed client_id %q is duplicated", clientID))
			}
			seen[clientID] = struct{}{}
		}
	}
	switch federation.SchedulingMode {
	case SchedulingSynchronous:
	case SchedulingDeadlineSemiSynchronous:
		if federation.MinimumValidResults >= federation.TargetClientsPerRound {
			problems = append(problems, "semi-synchronous scheduling requires minimum_valid_results < target_clients_per_round")
		}
	case SchedulingBufferedAsynchronous, SchedulingStalenessAwareAsync:
		if backend == BackendDistributed {
			problems = append(problems, fmt.Sprintf("distributed backend does not yet map scheduling mode %q to the coordinator", federation.SchedulingMode))
		}
	default:
		problems = append(problems, "federation.scheduling_mode is unsupported")
	}
	return problems
}

func validatePrivacy(privacy PrivacySpec, federation FederationSpec) []string {
	var problems []string
	switch privacy.Mode {
	case PrivacyNone:
	case PrivacySampleLevel:
		problems = append(problems, validateSamplePrivacy(privacy.SampleLevel)...)
	case PrivacyUserLevel:
		problems = append(problems, validateUserPrivacy(privacy.UserLevel)...)
	case PrivacyHybrid:
		problems = append(problems, validateSamplePrivacy(privacy.SampleLevel)...)
		problems = append(problems, validateUserPrivacy(privacy.UserLevel)...)
	default:
		problems = append(problems, "privacy.mode is unsupported")
	}
	if privacy.WarningThresholdFraction < 0 || privacy.WarningThresholdFraction > 1 || math.IsNaN(privacy.WarningThresholdFraction) {
		problems = append(problems, "privacy.warning_threshold_fraction must lie in [0,1]")
	}
	if (privacy.Mode == PrivacyUserLevel || privacy.Mode == PrivacyHybrid) && federation.Weighting != "uniform" {
		problems = append(problems, "user-level privacy requires uniform federation weighting")
	}
	if privacy.AdaptiveClipping.Enabled {
		if privacy.Mode != PrivacyUserLevel && privacy.Mode != PrivacyHybrid {
			problems = append(problems, "adaptive clipping requires user-level or hybrid privacy")
		}
		if privacy.AdaptiveClipping.TargetQuantile <= 0 || privacy.AdaptiveClipping.TargetQuantile >= 1 {
			problems = append(problems, "adaptive_clipping.target_quantile must lie in (0,1)")
		}
		if !positiveFinite(privacy.AdaptiveClipping.InitialClip) {
			problems = append(problems, "adaptive_clipping.initial_clip must be positive")
		}
		if !positiveFinite(privacy.AdaptiveClipping.MinClip) || !positiveFinite(privacy.AdaptiveClipping.MaxClip) || privacy.AdaptiveClipping.MinClip > privacy.AdaptiveClipping.MaxClip {
			problems = append(problems, "adaptive_clipping clip bounds are invalid")
		}
	}
	return problems
}

func validateSamplePrivacy(spec SampleLevelPrivacySpec) []string {
	var problems []string
	if !positiveFinite(spec.NoiseMultiplier) {
		problems = append(problems, "sample_level.noise_multiplier must be positive")
	}
	if !positiveFinite(spec.MaxGradNorm) {
		problems = append(problems, "sample_level.max_grad_norm must be positive")
	}
	if spec.TargetDelta <= 0 || spec.TargetDelta >= 1 || math.IsNaN(spec.TargetDelta) {
		problems = append(problems, "sample_level.target_delta must lie in (0,1)")
	}
	if !supportedAccountant(spec.Accountant) {
		problems = append(problems, "sample_level.accountant must be rdp, prv, or gdp")
	}
	if spec.EpsilonBudget < 0 || math.IsNaN(spec.EpsilonBudget) || math.IsInf(spec.EpsilonBudget, 0) {
		problems = append(problems, "sample_level.epsilon_budget must be finite and non-negative")
	}
	return problems
}

func validateUserPrivacy(spec UserLevelPrivacySpec) []string {
	var problems []string
	if !positiveFinite(spec.NoiseMultiplier) {
		problems = append(problems, "user_level.noise_multiplier must be positive")
	}
	if spec.TargetDelta <= 0 || spec.TargetDelta >= 1 || math.IsNaN(spec.TargetDelta) {
		problems = append(problems, "user_level.target_delta must lie in (0,1)")
	}
	if !supportedAccountant(spec.Accountant) {
		problems = append(problems, "user_level.accountant must be rdp, prv, or gdp")
	}
	if !positiveFinite(spec.InitialClippingBound) {
		problems = append(problems, "user_level.initial_clipping_bound must be positive")
	}
	if spec.WeightingStrategy == "" {
		problems = append(problems, "user_level.weighting_strategy is required")
	}
	if spec.EpsilonBudget < 0 || math.IsNaN(spec.EpsilonBudget) || math.IsInf(spec.EpsilonBudget, 0) {
		problems = append(problems, "user_level.epsilon_budget must be finite and non-negative")
	}
	return problems
}

func supportedAccountant(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "rdp", "prv", "gdp":
		return true
	default:
		return false
	}
}

func positiveFinite(value float64) bool {
	return value > 0 && !math.IsNaN(value) && !math.IsInf(value, 0)
}

func (s Spec) Hash() (string, error) {
	if err := s.Validate(); err != nil {
		return "", err
	}
	raw, err := json.Marshal(s)
	if err != nil {
		return "", err
	}
	var canonical map[string]any
	if err := json.Unmarshal(raw, &canonical); err != nil {
		return "", err
	}
	encoded, err := json.Marshal(canonical)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(encoded)
	return hex.EncodeToString(digest[:]), nil
}
