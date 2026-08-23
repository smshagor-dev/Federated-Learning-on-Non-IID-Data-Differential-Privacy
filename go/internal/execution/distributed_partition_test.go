package execution

import "testing"

func TestCanonicalCoordinatorRequestCarriesAdvancedPartitionParameters(t *testing.T) {
	spec := Spec{
		SchemaVersion: CurrentSchemaVersion,
		Name:          "partition-parity",
		Backend:       BackendDistributed,
		Dataset: DatasetSpec{
			Name: "CIFAR100",
			Partition: PartitionSpec{
				Strategy:          "pathological",
				Alpha:             0.25,
				ClassesPerClient:  3,
				QuantitySkewSigma: 1.5,
				MinimumClientSize: 11,
			},
		},
		Model: ModelSpec{
			Name:         "toy",
			Version:      "v1",
			UpdateFormat: "dense",
			Tensors:      []TensorSpec{{Name: "weight", Shape: []uint64{8}}},
		},
		Algorithm: AlgorithmSpec{Name: "fedavg"},
		Optimizer: OptimizerSpec{LearningRate: 0.01, ServerLR: 1},
		Federation: FederationSpec{
			TotalClients:          2,
			ClientIDs:             []string{"client-a", "client-b"},
			TargetClientsPerRound: 1,
			MinimumValidResults:   1,
			Rounds:                1,
			LocalEpochs:           1,
			BatchSize:             8,
			Weighting:             "uniform",
			SamplingStrategy:      SamplingFixedWithoutReplacement,
			ClientSelectionSeed:   42,
			SchedulingMode:        SchedulingSynchronous,
			RoundTimeoutSeconds:   30,
			TaskLeaseSeconds:      15,
		},
		Privacy: PrivacySpec{Mode: PrivacyNone},
		Evaluation: EvaluationSpec{EvaluationBatchSize: 8},
		Artifacts: ArtifactSpec{Root: "artifacts"},
	}

	request, err := canonicalCoordinatorRequest("exec-1", spec)
	if err != nil {
		t.Fatalf("canonicalCoordinatorRequest returned error: %v", err)
	}
	if request.DatasetPartitioning != "pathological" {
		t.Fatalf("partitioning = %q", request.DatasetPartitioning)
	}
	if request.DatasetClassesPerClient != 3 {
		t.Fatalf("classes_per_client = %d", request.DatasetClassesPerClient)
	}
	if request.DatasetQuantitySkewSigma != 1.5 {
		t.Fatalf("quantity_skew_sigma = %v", request.DatasetQuantitySkewSigma)
	}
	if request.DatasetMinClientSize != 11 {
		t.Fatalf("min_client_size = %d", request.DatasetMinClientSize)
	}
}

func TestDistributedMappingAcceptsAllCanonicalPartitionStrategies(t *testing.T) {
	for _, strategy := range []string{"iid", "dirichlet", "pathological", "quantity_skew"} {
		spec := Spec{
			Dataset: DatasetSpec{Partition: PartitionSpec{Strategy: strategy}},
			Federation: FederationSpec{SchedulingMode: SchedulingSynchronous},
		}
		if err := validateDistributedMapping(spec); err != nil {
			t.Fatalf("strategy %q unexpectedly rejected: %v", strategy, err)
		}
	}
}
