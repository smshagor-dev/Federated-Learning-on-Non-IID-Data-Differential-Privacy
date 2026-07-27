package datasets

import (
	"context"
	"path/filepath"
	"testing"
)

func TestInMemoryRepositoryDatasetAndPartitionLifecycle(t *testing.T) {
	ctx := context.Background()
	repo := NewInMemoryRepository()
	dataset := Dataset{DatasetID: "mnist-iid", Name: "MNIST", TaskType: "classification", NumClasses: 10, TrainSampleCount: 60000}
	if _, err := repo.Create(ctx, dataset); err != nil {
		t.Fatalf("create: %v", err)
	}
	got, ok, err := repo.Get(ctx, "mnist-iid")
	if err != nil || !ok {
		t.Fatalf("get: ok=%v err=%v", ok, err)
	}
	if got.NumClasses != 10 {
		t.Fatalf("unexpected num_classes: %d", got.NumClasses)
	}

	partition := Partition{PartitionID: "p1", DatasetID: "mnist-iid", Strategy: "iid", NumClients: 4}
	if _, err := repo.CreatePartition(ctx, partition); err != nil {
		t.Fatalf("create partition: %v", err)
	}
	gotPartition, ok, err := repo.GetPartition(ctx, "p1")
	if err != nil || !ok {
		t.Fatalf("get partition: ok=%v err=%v", ok, err)
	}
	if gotPartition.NumClients != 4 {
		t.Fatalf("unexpected num_clients: %d", gotPartition.NumClients)
	}

	list, err := repo.ListPartitions(ctx, "mnist-iid")
	if err != nil || len(list) != 1 {
		t.Fatalf("list partitions: len=%d err=%v", len(list), err)
	}
	empty, err := repo.ListPartitions(ctx, "other-dataset")
	if err != nil || len(empty) != 0 {
		t.Fatalf("expected no partitions for unrelated dataset, got %d", len(empty))
	}
}

func TestFileRepositoryPersistsAcrossReopen(t *testing.T) {
	ctx := context.Background()
	dir := t.TempDir()
	datasetsPath := filepath.Join(dir, "datasets.json")
	partitionsPath := filepath.Join(dir, "partitions.json")

	repo, err := NewFileRepository(datasetsPath, partitionsPath)
	if err != nil {
		t.Fatalf("new file repository: %v", err)
	}
	if _, err := repo.Create(ctx, Dataset{DatasetID: "mnist-iid", NumClasses: 10, TrainSampleCount: 60000}); err != nil {
		t.Fatalf("create dataset: %v", err)
	}
	if _, err := repo.CreatePartition(ctx, Partition{PartitionID: "p1", DatasetID: "mnist-iid", Strategy: "iid", NumClients: 4}); err != nil {
		t.Fatalf("create partition: %v", err)
	}

	reopened, err := NewFileRepository(datasetsPath, partitionsPath)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	if _, ok, err := reopened.Get(ctx, "mnist-iid"); err != nil || !ok {
		t.Fatalf("get dataset after reopen: ok=%v err=%v", ok, err)
	}
	if _, ok, err := reopened.GetPartition(ctx, "p1"); err != nil || !ok {
		t.Fatalf("get partition after reopen: ok=%v err=%v", ok, err)
	}
}

func TestDatasetCanTransitionTo(t *testing.T) {
	dataset := Dataset{Status: StatusDraft}
	if !dataset.CanTransitionTo(StatusValidated) {
		t.Fatalf("expected DRAFT -> VALIDATED to be allowed")
	}
	if dataset.CanTransitionTo(StatusActive) {
		t.Fatalf("expected DRAFT -> ACTIVE to be rejected")
	}
}
