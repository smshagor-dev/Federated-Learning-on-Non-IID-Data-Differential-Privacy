package research

import (
	"bytes"
	"encoding/json"
	"testing"
)

func TestHashJSONCanonicalizesStructLikePythonContract(t *testing.T) {
	noise := 1.0
	delta := 1e-5
	clip := 1.5
	alpha := 0.3

	spec := ExperimentSpecification{
		SchemaVersion:    1,
		ExperimentID:     "expresearch001",
		ExperimentName:   "FedAvg privacy comparison",
		ResearchQuestion: "How do privacy layers affect convergence on one fixed dataset?",
		Dataset: DatasetConfiguration{
			DatasetID:                  "cifar10",
			DatasetVersion:             "1.0",
			DatasetChecksum:            "sha256:cifar10-demo",
			SplitSeed:                  7,
			TrainSplitFraction:         0.8,
			ValidationSplitFraction:    0.1,
			TestSplitFraction:          0.1,
			PreprocessingConfiguration: map[string]any{},
		},
		Partition: PartitionConfiguration{
			Strategy:              PartitionDirichlet,
			NumClients:            5,
			Seed:                  11,
			MinimumClientSamples:  4,
			Alpha:                 &alpha,
			PartitionManifestHash: "manifest-hash-123",
		},
		Model: ModelConfiguration{
			ModelID:            "groupnorm_cnn",
			ModelVersion:       "v1",
			InitializationSeed: 19,
		},
		Algorithm: AlgorithmConfiguration{
			AlgorithmID: "fedavg",
			Parameters:  map[string]any{},
		},
		Privacy: PrivacyConfiguration{
			PrivacyMode:       PrivacyUserLevel,
			NoiseMultiplier:   &noise,
			TargetDelta:       &delta,
			UserLevelClipNorm: &clip,
			ClientWeighting:   "uniform",
		},
		SecureAggregation: SecureAggregationConfiguration{
			Provider:                 SecureAggregationNoDropoutExperimental,
			DropoutRecoveryRequested: false,
		},
		AdaptiveClipping: AdaptiveClippingConfiguration{
			Mode: AdaptiveClippingDisabled,
		},
		Runtime: RuntimeLimits{
			MaxRounds:               3,
			LocalEpochs:             1,
			BatchSize:               8,
			LearningRate:            0.01,
			EvaluationFrequency:     1,
			SelectedClientsPerRound: 3,
		},
		Seeds: SeedConfiguration{
			Seeds:                []int{1, 2, 3},
			PartitionSeed:        11,
			WorkerAssignmentSeed: 13,
			CoordinatorSeed:      17,
		},
		DeterminismLevel: DeterminismStrictCPU,
		Tags:             []string{},
	}

	structHash, err := hashJSON(map[string]any{
		"specification":             spec,
		"client_specification_hash": "",
	})
	if err != nil {
		t.Fatalf("hash struct payload: %v", err)
	}

	mapHash, err := hashJSON(map[string]any{
		"specification":             spec.CanonicalPayload(),
		"client_specification_hash": "",
	})
	if err != nil {
		t.Fatalf("hash canonical payload: %v", err)
	}

	if structHash != mapHash {
		t.Fatalf("expected canonical hashes to match, got %s != %s", structHash, mapHash)
	}
	wireHash, err := hashPayloadAsJSONWireValue(map[string]any{
		"specification":             spec,
		"client_specification_hash": "",
	})
	if err != nil {
		t.Fatalf("hash wire payload: %v", err)
	}
	if structHash != wireHash {
		t.Fatalf("expected canonical hash to match wire payload hash, got %s != %s", structHash, wireHash)
	}
}

func hashPayloadAsJSONWireValue(payload any) (string, error) {
	body, err := json.Marshal(map[string]any{"payload": payload})
	if err != nil {
		return "", err
	}
	var envelope map[string]any
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	if err := decoder.Decode(&envelope); err != nil {
		return "", err
	}
	return hashJSON(envelope["payload"])
}
