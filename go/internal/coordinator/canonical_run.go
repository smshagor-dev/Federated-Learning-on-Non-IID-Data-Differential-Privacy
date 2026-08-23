package coordinator

import (
	"context"
	"errors"

	coordinatorv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/coordinator/v1"
	experimentv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/experiment/v1"
)

var ErrCanonicalRunUnsupported = errors.New("coordinator client does not support canonical run creation")

// CanonicalRunRequest extends the long-lived CreateRunRequest without changing
// the existing Client interface. It carries the experiment metadata that was
// previously dropped by the Go gRPC mapper: dataset identity/partitioning,
// model identity/update format, and algorithm-specific mu.
type CanonicalRunRequest struct {
	CreateRunRequest
	DatasetName              string
	DatasetPartitioning      string
	DatasetAlpha             float64
	DatasetClassesPerClient  uint32
	DatasetQuantitySkewSigma float64
	DatasetMinClientSize     uint32
	ModelName                string
	ModelUpdateFormat        string
	AlgorithmMu              float64
}

// CanonicalRunCreator is an additive capability. Existing coordinator.Client
// implementations remain source-compatible; callers that require full
// execution-spec fidelity must explicitly require this capability and fail
// closed when it is absent.
type CanonicalRunCreator interface {
	CreateCanonicalRun(ctx context.Context, request CanonicalRunRequest) (RunSnapshot, error)
}

func CreateCanonicalRun(ctx context.Context, client Client, request CanonicalRunRequest) (RunSnapshot, error) {
	creator, ok := client.(CanonicalRunCreator)
	if !ok {
		return RunSnapshot{}, ErrCanonicalRunUnsupported
	}
	return creator.CreateCanonicalRun(ctx, request)
}

func (c *GrpcClient) CreateCanonicalRun(ctx context.Context, request CanonicalRunRequest) (RunSnapshot, error) {
	privacy := privacyConfigToWire(request.Privacy)
	response, err := c.stub.CreateRun(ctx, &coordinatorv1.CreateRunRequest{
		Config: &experimentv1.RunConfiguration{
			RunId: request.RunID,
			Dataset: &experimentv1.DatasetConfig{
				Name:              request.DatasetName,
				Partitioning:      request.DatasetPartitioning,
				Alpha:             request.DatasetAlpha,
				ClassesPerClient:  request.DatasetClassesPerClient,
				QuantitySkewSigma: request.DatasetQuantitySkewSigma,
				MinClientSize:     request.DatasetMinClientSize,
			},
			Model: &experimentv1.ModelConfig{
				Name:         request.ModelName,
				UpdateFormat: request.ModelUpdateFormat,
			},
			Privacy: privacy,
			Algorithm: &experimentv1.AlgorithmConfig{
				Name: request.Algorithm,
				Mu:   request.AlgorithmMu,
			},
			Rounds: request.MaxRounds,
		},
		Optimizer: &coordinatorv1.OptimizerConfig{
			Algorithm: request.Algorithm,
			Weighting: request.Weighting,
			ServerLr:  request.ServerLR,
		},
		TargetClientsPerRound: request.TargetClientsPerRound,
		TotalClients:          request.TotalClients,
		MaxRounds:             request.MaxRounds,
		RoundTimeoutSeconds:   request.RoundTimeoutSeconds,
		MinimumValidResults:   request.MinimumValidResults,
		ClientSelectionSeed:   request.ClientSelectionSeed,
		ClientIds:             request.ClientIDs,
		LocalEpochs:           request.LocalEpochs,
		BatchSize:             request.BatchSize,
		LearningRate:          request.LearningRate,
		Momentum:              request.Momentum,
		WeightDecay:           request.WeightDecay,
		FedproxMu:             request.AlgorithmMu,
		TaskLeaseSeconds:      request.TaskLeaseSeconds,
		MaxTaskRetries:        request.MaxTaskRetries,
		ModelManifest:         modelManifestToWire(request.ModelManifest),
		RequestId:             request.RequestID,
		PrivacyConfig:         privacy,
	})
	if err != nil {
		return RunSnapshot{}, mapGrpcError(err)
	}
	return toRunSnapshot(
		response.GetState(),
		response.GetRunId(),
		0,
		request.MaxRounds,
		request.ModelManifest.ModelVersion,
		request.Algorithm,
		0,
		0,
	), nil
}
