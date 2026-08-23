package execution

import (
	"context"
	"fmt"
	"strings"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
)

type DistributedDriver struct {
	client coordinator.Client
}

func NewDistributedDriver(client coordinator.Client) *DistributedDriver {
	return &DistributedDriver{client: client}
}

func (d *DistributedDriver) ensureConfigured() error {
	if d == nil || d.client == nil {
		return ErrBackendNotConfigured
	}
	return nil
}

func (d *DistributedDriver) Create(ctx context.Context, executionID string, spec Spec, traceID string) (Snapshot, error) {
	if err := d.ensureConfigured(); err != nil {
		return Snapshot{}, err
	}
	if err := spec.Validate(); err != nil {
		return Snapshot{}, err
	}
	if spec.Backend != BackendDistributed {
		return Snapshot{}, fmt.Errorf("%w: distributed driver received backend %q", ErrUnsupportedMapping, spec.Backend)
	}
	if err := validateDistributedMapping(spec); err != nil {
		return Snapshot{}, err
	}
	if err := d.preflightSecurity(ctx, spec, traceID); err != nil {
		return Snapshot{}, err
	}

	request, err := canonicalCoordinatorRequest(executionID, spec)
	if err != nil {
		return Snapshot{}, err
	}
	coordinatorSnapshot, err := coordinator.CreateCanonicalRun(ctx, d.client, request)
	if err != nil {
		return Snapshot{}, err
	}
	return snapshotFromCoordinator(coordinatorSnapshot)
}

func (d *DistributedDriver) Start(ctx context.Context, backendRunID, traceID string) (Snapshot, error) {
	if err := d.ensureConfigured(); err != nil {
		return Snapshot{}, err
	}
	snapshot, err := d.client.StartRun(ctx, backendRunID, traceID)
	if err != nil {
		return Snapshot{}, err
	}
	return snapshotFromCoordinator(snapshot)
}

func (d *DistributedDriver) Pause(ctx context.Context, backendRunID, reason, traceID string) (Snapshot, error) {
	if err := d.ensureConfigured(); err != nil {
		return Snapshot{}, err
	}
	snapshot, err := d.client.PauseRun(ctx, backendRunID, reason, traceID)
	if err != nil {
		return Snapshot{}, err
	}
	return snapshotFromCoordinator(snapshot)
}

func (d *DistributedDriver) Resume(ctx context.Context, backendRunID, traceID string) (Snapshot, error) {
	if err := d.ensureConfigured(); err != nil {
		return Snapshot{}, err
	}
	snapshot, err := d.client.ResumeRun(ctx, backendRunID, traceID)
	if err != nil {
		return Snapshot{}, err
	}
	return snapshotFromCoordinator(snapshot)
}

func (d *DistributedDriver) Cancel(ctx context.Context, backendRunID, reason, traceID string) (Snapshot, error) {
	if err := d.ensureConfigured(); err != nil {
		return Snapshot{}, err
	}
	snapshot, err := d.client.CancelRun(ctx, backendRunID, reason, traceID)
	if err != nil {
		return Snapshot{}, err
	}
	return snapshotFromCoordinator(snapshot)
}

func (d *DistributedDriver) Get(ctx context.Context, backendRunID string) (Snapshot, error) {
	if err := d.ensureConfigured(); err != nil {
		return Snapshot{}, err
	}
	snapshot, err := d.client.GetRun(ctx, backendRunID)
	if err != nil {
		return Snapshot{}, err
	}
	return snapshotFromCoordinator(snapshot)
}

func (d *DistributedDriver) ListWorkers(ctx context.Context) ([]Worker, error) {
	if err := d.ensureConfigured(); err != nil {
		return nil, err
	}
	workers, err := d.client.ListWorkers(ctx)
	if err != nil {
		return nil, err
	}
	result := make([]Worker, 0, len(workers))
	for _, worker := range workers {
		result = append(result, Worker{
			WorkerID:            worker.WorkerID,
			Status:              worker.Status,
			Device:              worker.Device,
			CPUCount:            worker.CPUCount,
			GPUAvailable:        worker.GPUAvailable,
			GPUCount:            worker.GPUCount,
			SupportedAlgorithms: append([]string(nil), worker.SupportedAlgorithms...),
			LastHeartbeatUnixS:  worker.LastHeartbeatUnixS,
		})
	}
	return result, nil
}

func validateDistributedMapping(spec Spec) error {
	switch spec.Dataset.Partition.Strategy {
	case "iid", "dirichlet":
	default:
		return fmt.Errorf(
			"%w: distributed coordinator RunConfiguration currently carries only partitioning and alpha; strategy %q requires additional wire fields",
			ErrUnsupportedMapping,
			spec.Dataset.Partition.Strategy,
		)
	}
	switch spec.Federation.SchedulingMode {
	case SchedulingSynchronous, SchedulingDeadlineSemiSynchronous:
	default:
		return fmt.Errorf("%w: distributed scheduling mode %q is not wired to the coordinator", ErrUnsupportedMapping, spec.Federation.SchedulingMode)
	}
	if spec.Security.SecureAggregation {
		return fmt.Errorf("%w: secure aggregation is process-wide on the current coordinator, not an enforceable per-run setting", ErrUnsupportedMapping)
	}
	return nil
}

func (d *DistributedDriver) preflightSecurity(ctx context.Context, spec Spec, traceID string) error {
	if !spec.Security.RequireAuthenticatedWorkers && !spec.Security.RequireSignedTasks && !spec.Security.RequireSignedResults {
		return nil
	}
	if spec.Security.RequireSignedResults {
		return fmt.Errorf("%w: per-run signed-result enforcement is not yet exposed as a verifiable coordinator capability", ErrSecurityPreflight)
	}
	if spec.Security.RequireAuthenticatedWorkers {
		status, err := d.client.GetTransportSecurityStatus(ctx, traceID)
		if err != nil {
			return fmt.Errorf("%w: transport status: %v", ErrSecurityPreflight, err)
		}
		if !status.MutualTLSEnforced {
			return fmt.Errorf("%w: worker authentication requested but coordinator is not enforcing mutual TLS", ErrSecurityPreflight)
		}
	}
	if spec.Security.RequireSignedTasks {
		trust, err := d.client.GetSecurityTrustModel(ctx, traceID)
		if err != nil {
			return fmt.Errorf("%w: trust model: %v", ErrSecurityPreflight, err)
		}
		if strings.TrimSpace(trust.ActiveCoordinatorSigningKeyID) == "" {
			return fmt.Errorf("%w: signed tasks requested but coordinator has no active signing key", ErrSecurityPreflight)
		}
	}
	return nil
}

func canonicalCoordinatorRequest(executionID string, spec Spec) (coordinator.CanonicalRunRequest, error) {
	hash, err := spec.Hash()
	if err != nil {
		return coordinator.CanonicalRunRequest{}, err
	}
	privacy := coordinator.PrivacyConfig{
		Mode: coordinator.PrivacyMode(spec.Privacy.Mode),
		SampleLevel: coordinator.SampleLevelDPConfig{
			NoiseMultiplier: spec.Privacy.SampleLevel.NoiseMultiplier,
			MaxGradNorm:     spec.Privacy.SampleLevel.MaxGradNorm,
			TargetDelta:     spec.Privacy.SampleLevel.TargetDelta,
			Accountant:      spec.Privacy.SampleLevel.Accountant,
			PoissonSampling: spec.Privacy.SampleLevel.PoissonSampling,
			EpsilonBudget:   spec.Privacy.SampleLevel.EpsilonBudget,
		},
		UserLevel: coordinator.UserLevelDPConfig{
			NoiseMultiplier:      spec.Privacy.UserLevel.NoiseMultiplier,
			TargetDelta:          spec.Privacy.UserLevel.TargetDelta,
			Accountant:           spec.Privacy.UserLevel.Accountant,
			InitialClippingBound: spec.Privacy.UserLevel.InitialClippingBound,
			WeightingStrategy:    spec.Privacy.UserLevel.WeightingStrategy,
			SecureRandom:         spec.Privacy.UserLevel.SecureRandom,
			EpsilonBudget:        spec.Privacy.UserLevel.EpsilonBudget,
		},
		AdaptiveClipping: coordinator.AdaptiveClippingConfig{
			Enabled:              spec.Privacy.AdaptiveClipping.Enabled,
			TargetQuantile:       spec.Privacy.AdaptiveClipping.TargetQuantile,
			ClipLearningRate:     spec.Privacy.AdaptiveClipping.ClipLearningRate,
			InitialClip:          spec.Privacy.AdaptiveClipping.InitialClip,
			MinClip:              spec.Privacy.AdaptiveClipping.MinClip,
			MaxClip:              spec.Privacy.AdaptiveClipping.MaxClip,
			CountNoiseMultiplier: spec.Privacy.AdaptiveClipping.CountNoiseMultiplier,
			TargetDelta:          spec.Privacy.AdaptiveClipping.TargetDelta,
			EpsilonBudget:        spec.Privacy.AdaptiveClipping.EpsilonBudget,
		},
		WarningThresholdFraction: spec.Privacy.WarningThresholdFraction,
	}

	tensors := make([]coordinator.TensorSpec, 0, len(spec.Model.Tensors))
	for _, tensor := range spec.Model.Tensors {
		tensors = append(tensors, coordinator.TensorSpec{Name: tensor.Name, Shape: append([]uint64(nil), tensor.Shape...)})
	}
	return coordinator.CanonicalRunRequest{
		CreateRunRequest: coordinator.CreateRunRequest{
			RunID:                 executionID,
			Algorithm:             strings.ToLower(spec.Algorithm.Name),
			Weighting:             spec.Federation.Weighting,
			TotalClients:          spec.Federation.TotalClients,
			TargetClientsPerRound: spec.Federation.TargetClientsPerRound,
			MaxRounds:             spec.Federation.Rounds,
			MinimumValidResults:   spec.Federation.MinimumValidResults,
			ClientSelectionSeed:   spec.Federation.ClientSelectionSeed,
			RoundTimeoutSeconds:   spec.Federation.RoundTimeoutSeconds,
			ServerLR:              spec.Optimizer.ServerLR,
			ClientIDs:             append([]string(nil), spec.Federation.ClientIDs...),
			LocalEpochs:           spec.Federation.LocalEpochs,
			BatchSize:             spec.Federation.BatchSize,
			LearningRate:          spec.Optimizer.LearningRate,
			Momentum:              spec.Optimizer.Momentum,
			WeightDecay:           spec.Optimizer.WeightDecay,
			FedProxMu:             spec.Algorithm.Mu,
			TaskLeaseSeconds:      spec.Federation.TaskLeaseSeconds,
			MaxTaskRetries:        spec.Federation.MaxTaskRetries,
			ModelManifest: coordinator.ModelManifest{
				ModelID:      spec.Model.Name,
				ModelVersion: spec.Model.Version,
				Tensors:      tensors,
				AggregationManifest: coordinator.AggregationManifest{
					SharedParameterNames:       append([]string(nil), spec.Model.Aggregation.SharedParameterNames...),
					PersonalizedParameterNames: append([]string(nil), spec.Model.Aggregation.PersonalizedParameterNames...),
					FrozenParameterNames:       append([]string(nil), spec.Model.Aggregation.FrozenParameterNames...),
					SchemaHash:                 spec.Model.Aggregation.SchemaHash,
				},
			},
			RequestID: executionID + ":" + hash,
			Privacy:   privacy,
		},
		DatasetName:         spec.Dataset.Name,
		DatasetPartitioning: spec.Dataset.Partition.Strategy,
		DatasetAlpha:        spec.Dataset.Partition.Alpha,
		ModelName:           spec.Model.Name,
		ModelUpdateFormat:   spec.Model.UpdateFormat,
		AlgorithmMu:         spec.Algorithm.Mu,
	}, nil
}

func snapshotFromCoordinator(snapshot coordinator.RunSnapshot) (Snapshot, error) {
	status, err := statusFromCoordinator(snapshot.State)
	if err != nil {
		return Snapshot{}, err
	}
	return Snapshot{
		BackendRunID:      snapshot.RunID,
		Status:            status,
		CurrentRound:      snapshot.CurrentRound,
		MaxRounds:         snapshot.MaxRounds,
		ModelVersion:      snapshot.ModelVersion,
		RegisteredWorkers: snapshot.RegisteredWorkers,
		HealthyWorkers:    snapshot.HealthyWorkers,
	}, nil
}

func statusFromCoordinator(state coordinator.RunState) (Status, error) {
	switch strings.ToUpper(strings.TrimSpace(string(state))) {
	case "CREATED", "READY":
		return StatusCreated, nil
	case "STARTING":
		return StatusStarting, nil
	case "RUNNING":
		return StatusRunning, nil
	case "PAUSING":
		return StatusPausing, nil
	case "PAUSED":
		return StatusPaused, nil
	case "RESUMING":
		return StatusResuming, nil
	case "CANCELING", "CANCELLING":
		return StatusCanceling, nil
	case "CANCELED", "CANCELLED":
		return StatusCanceled, nil
	case "COMPLETED":
		return StatusCompleted, nil
	case "FAILED":
		return StatusFailed, nil
	default:
		return "", fmt.Errorf("%w: unknown coordinator state %q", ErrUnsupportedMapping, state)
	}
}
