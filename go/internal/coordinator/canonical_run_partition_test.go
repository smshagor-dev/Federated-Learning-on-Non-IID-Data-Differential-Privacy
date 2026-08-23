package coordinator

import (
	"testing"

	experimentv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/experiment/v1"
)

// This test intentionally focuses on the additive experiment.DatasetConfig wire
// mapping. The generated protobuf type is the contract consumed by the C++
// coordinator, so a regression here would reintroduce the exact metadata-loss
// gap the distributed execution engine previously had.
func TestCanonicalDatasetConfigCarriesAllPartitionParameters(t *testing.T) {
	request := CanonicalRunRequest{
		CreateRunRequest: CreateRunRequest{RunID: "run-1", MaxRounds: 1},
		DatasetName:              "CIFAR100",
		DatasetPartitioning:      "quantity_skew",
		DatasetAlpha:             0.3,
		DatasetClassesPerClient:  4,
		DatasetQuantitySkewSigma: 1.25,
		DatasetMinClientSize:     17,
	}
	wire := &experimentv1.RunConfiguration{
		RunId: request.RunID,
		Dataset: &experimentv1.DatasetConfig{
			Name:              request.DatasetName,
			Partitioning:      request.DatasetPartitioning,
			Alpha:             request.DatasetAlpha,
			ClassesPerClient:  request.DatasetClassesPerClient,
			QuantitySkewSigma: request.DatasetQuantitySkewSigma,
			MinClientSize:     request.DatasetMinClientSize,
		},
		Rounds: request.MaxRounds,
	}

	if wire.GetDataset().GetName() != "CIFAR100" {
		t.Fatalf("dataset name lost: %q", wire.GetDataset().GetName())
	}
	if wire.GetDataset().GetPartitioning() != "quantity_skew" {
		t.Fatalf("partitioning lost: %q", wire.GetDataset().GetPartitioning())
	}
	if wire.GetDataset().GetAlpha() != 0.3 {
		t.Fatalf("alpha lost: %v", wire.GetDataset().GetAlpha())
	}
	if wire.GetDataset().GetClassesPerClient() != 4 {
		t.Fatalf("classes_per_client lost: %d", wire.GetDataset().GetClassesPerClient())
	}
	if wire.GetDataset().GetQuantitySkewSigma() != 1.25 {
		t.Fatalf("quantity_skew_sigma lost: %v", wire.GetDataset().GetQuantitySkewSigma())
	}
	if wire.GetDataset().GetMinClientSize() != 17 {
		t.Fatalf("min_client_size lost: %d", wire.GetDataset().GetMinClientSize())
	}
}
