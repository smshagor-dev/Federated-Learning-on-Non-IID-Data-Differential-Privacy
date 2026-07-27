package coordinator

import (
	"context"
	"testing"

	"google.golang.org/grpc"

	coordinatorv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/coordinator/v1"
)

// recordingStub embeds the generated client interface (nil) so it
// satisfies CoordinatorServiceClient without implementing every method —
// this test only exercises CreateRun. Calling any other method would
// nil-pointer-panic, which is fine: no other method is invoked here.
type recordingStub struct {
	coordinatorv1.CoordinatorServiceClient
	lastCreateRunRequest *coordinatorv1.CreateRunRequest
	response             *coordinatorv1.CreateRunResponse
}

func (s *recordingStub) CreateRun(_ context.Context, in *coordinatorv1.CreateRunRequest, _ ...grpc.CallOption) (*coordinatorv1.CreateRunResponse, error) {
	s.lastCreateRunRequest = in
	if s.response != nil {
		return s.response, nil
	}
	return &coordinatorv1.CreateRunResponse{RunId: in.GetConfig().GetRunId(), State: "CREATED"}, nil
}

// TestGrpcClientCreateRunMapsAllWireFields is a regression test for the
// CreateRun wire-mapping gap (see docs/create-run-wire-mapping.md):
// client_ids and training hyperparameters previously had no Go->wire
// mapping at all, so AcquireTask could never select a real client
// through the live gRPC path. This asserts every field GrpcClient.CreateRun
// is responsible for forwarding actually reaches the wire request.
func TestGrpcClientCreateRunMapsAllWireFields(t *testing.T) {
	stub := &recordingStub{}
	client := &GrpcClient{stub: stub}

	request := CreateRunRequest{
		RunID:                 "run-1",
		Algorithm:             "fedprox",
		Weighting:             "sample_count",
		TotalClients:          3,
		TargetClientsPerRound: 2,
		MaxRounds:             5,
		MinimumValidResults:   2,
		ClientSelectionSeed:   42,
		RoundTimeoutSeconds:   120,
		ServerLR:              1.0,
		ClientIDs:             []string{"client-a", "client-b", "client-c"},
		LocalEpochs:           3,
		BatchSize:             16,
		LearningRate:          0.05,
		Momentum:              0.9,
		WeightDecay:           1e-4,
		FedProxMu:             0.01,
		TaskLeaseSeconds:      90,
		MaxTaskRetries:        5,
		RequestID:             "req-1",
		ModelManifest: ModelManifest{
			ModelID:      "toy",
			ModelVersion: "v0",
			Tensors:      []TensorSpec{{Name: "weight", Shape: []uint64{4}}},
			AggregationManifest: AggregationManifest{
				SharedParameterNames: []string{"weight"},
				SchemaHash:           "hash-1",
			},
		},
	}

	if _, err := client.CreateRun(context.Background(), request); err != nil {
		t.Fatalf("CreateRun: unexpected error: %v", err)
	}

	wire := stub.lastCreateRunRequest
	if wire == nil {
		t.Fatal("CreateRun: stub never received a request")
	}

	if got := wire.GetClientIds(); len(got) != 3 || got[0] != "client-a" || got[2] != "client-c" {
		t.Errorf("ClientIds not mapped: got %v", got)
	}
	if wire.GetLocalEpochs() != 3 {
		t.Errorf("LocalEpochs = %d, want 3", wire.GetLocalEpochs())
	}
	if wire.GetBatchSize() != 16 {
		t.Errorf("BatchSize = %d, want 16", wire.GetBatchSize())
	}
	if wire.GetLearningRate() != 0.05 {
		t.Errorf("LearningRate = %v, want 0.05", wire.GetLearningRate())
	}
	if wire.GetMomentum() != 0.9 {
		t.Errorf("Momentum = %v, want 0.9", wire.GetMomentum())
	}
	if wire.GetWeightDecay() != 1e-4 {
		t.Errorf("WeightDecay = %v, want 1e-4", wire.GetWeightDecay())
	}
	if wire.GetFedproxMu() != 0.01 {
		t.Errorf("FedproxMu = %v, want 0.01", wire.GetFedproxMu())
	}
	if wire.GetTaskLeaseSeconds() != 90 {
		t.Errorf("TaskLeaseSeconds = %d, want 90", wire.GetTaskLeaseSeconds())
	}
	if wire.GetMaxTaskRetries() != 5 {
		t.Errorf("MaxTaskRetries = %d, want 5", wire.GetMaxTaskRetries())
	}
	if wire.GetRequestId() != "req-1" {
		t.Errorf("RequestId = %q, want %q", wire.GetRequestId(), "req-1")
	}

	manifest := wire.GetModelManifest()
	if manifest == nil {
		t.Fatal("ModelManifest not set on wire request")
	}
	if manifest.GetModelId() != "toy" || manifest.GetModelVersion() != "v0" {
		t.Errorf("ModelManifest model_id/model_version = %q/%q, want toy/v0", manifest.GetModelId(), manifest.GetModelVersion())
	}
	if len(manifest.GetTensors()) != 1 || manifest.GetTensors()[0].GetName() != "weight" {
		t.Errorf("ModelManifest tensors not mapped: %+v", manifest.GetTensors())
	}
	if got := manifest.GetTensors()[0].GetShape(); len(got) != 1 || got[0] != 4 {
		t.Errorf("ModelManifest tensor shape = %v, want [4]", got)
	}
	aggManifest := manifest.GetAggregationManifest()
	if aggManifest == nil || len(aggManifest.GetSharedParameterNames()) != 1 ||
		aggManifest.GetSharedParameterNames()[0] != "weight" || aggManifest.GetSchemaHash() != "hash-1" {
		t.Errorf("AggregationManifest not mapped: %+v", aggManifest)
	}
}
