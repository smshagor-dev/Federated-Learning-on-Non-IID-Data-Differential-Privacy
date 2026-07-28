package application

import (
	"context"
	"errors"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/datasets"
)

func newDatasetService() *DatasetService {
	return &DatasetService{repo: datasets.NewInMemoryRepository(), clock: fixedClock, audit: &AuditService{}}
}

func TestDatasetServiceRegisterRejectsDuplicate(t *testing.T) {
	ctx := context.Background()
	service := newDatasetService()
	dataset := datasets.Dataset{DatasetID: "mnist-iid", NumClasses: 10, TrainSampleCount: 60000}
	if _, err := service.Register(ctx, dataset); err != nil {
		t.Fatalf("register: %v", err)
	}
	if _, err := service.Register(ctx, dataset); !errors.Is(err, ErrDatasetAlreadyRegistered) {
		t.Fatalf("expected ErrDatasetAlreadyRegistered, got %v", err)
	}
}

func TestDatasetServiceValidateRejectsZeroSamples(t *testing.T) {
	ctx := context.Background()
	service := newDatasetService()
	if _, err := service.Register(ctx, datasets.Dataset{DatasetID: "empty-ds", NumClasses: 10, TrainSampleCount: 0}); err != nil {
		t.Fatalf("register: %v", err)
	}
	if _, err := service.Validate(ctx, "empty-ds"); !errors.Is(err, ErrDatasetNotReadyToValidate) {
		t.Fatalf("expected ErrDatasetNotReadyToValidate, got %v", err)
	}
}

func TestDatasetServiceValidateRejectsNonPositiveNumClasses(t *testing.T) {
	ctx := context.Background()
	service := newDatasetService()
	if _, err := service.Register(ctx, datasets.Dataset{DatasetID: "bad-classes", NumClasses: 0, TrainSampleCount: 1000}); err != nil {
		t.Fatalf("register: %v", err)
	}
	if _, err := service.Validate(ctx, "bad-classes"); !errors.Is(err, ErrDatasetNotReadyToValidate) {
		t.Fatalf("expected ErrDatasetNotReadyToValidate, got %v", err)
	}
}

func TestDatasetServiceFullLifecycle(t *testing.T) {
	ctx := context.Background()
	service := newDatasetService()
	if _, err := service.Register(ctx, datasets.Dataset{DatasetID: "mnist-iid", NumClasses: 10, TrainSampleCount: 60000}); err != nil {
		t.Fatalf("register: %v", err)
	}
	validated, err := service.Validate(ctx, "mnist-iid")
	if err != nil {
		t.Fatalf("validate: %v", err)
	}
	if validated.Status != datasets.StatusValidated {
		t.Fatalf("expected VALIDATED, got %s", validated.Status)
	}
	activated, err := service.Activate(ctx, "mnist-iid")
	if err != nil {
		t.Fatalf("activate: %v", err)
	}
	if activated.Status != datasets.StatusActive {
		t.Fatalf("expected ACTIVE, got %s", activated.Status)
	}
}

func TestDatasetServiceCreatePartitionRequiresKnownStrategy(t *testing.T) {
	ctx := context.Background()
	service := newDatasetService()
	if _, err := service.Register(ctx, datasets.Dataset{DatasetID: "mnist-iid", NumClasses: 10, TrainSampleCount: 60000}); err != nil {
		t.Fatalf("register: %v", err)
	}
	_, err := service.CreatePartition(ctx, datasets.Partition{
		PartitionID: "p1", DatasetID: "mnist-iid", Strategy: "not-a-strategy", NumClients: 4,
	})
	if !errors.Is(err, ErrInvalidPartitionManifest) {
		t.Fatalf("expected ErrInvalidPartitionManifest, got %v", err)
	}
}

func TestDatasetServiceCreatePartitionDirichletRequiresAlpha(t *testing.T) {
	ctx := context.Background()
	service := newDatasetService()
	if _, err := service.Register(ctx, datasets.Dataset{DatasetID: "mnist-iid", NumClasses: 10, TrainSampleCount: 60000}); err != nil {
		t.Fatalf("register: %v", err)
	}
	_, err := service.CreatePartition(ctx, datasets.Partition{
		PartitionID: "p1", DatasetID: "mnist-iid", Strategy: "dirichlet", NumClients: 4,
	})
	if !errors.Is(err, ErrInvalidPartitionManifest) {
		t.Fatalf("expected ErrInvalidPartitionManifest for missing alpha, got %v", err)
	}
}

func TestDatasetServiceCreatePartitionQuantitySkewRequiresSigma(t *testing.T) {
	ctx := context.Background()
	service := newDatasetService()
	if _, err := service.Register(ctx, datasets.Dataset{DatasetID: "mnist-iid", NumClasses: 10, TrainSampleCount: 60000}); err != nil {
		t.Fatalf("register: %v", err)
	}
	_, err := service.CreatePartition(ctx, datasets.Partition{
		PartitionID: "p1", DatasetID: "mnist-iid", Strategy: "quantity_skew", NumClients: 4,
	})
	if !errors.Is(err, ErrInvalidPartitionManifest) {
		t.Fatalf("expected ErrInvalidPartitionManifest for missing quantity_skew_sigma, got %v", err)
	}
}

func TestDatasetServiceCreatePartitionQuantitySkewSucceeds(t *testing.T) {
	ctx := context.Background()
	service := newDatasetService()
	if _, err := service.Register(ctx, datasets.Dataset{DatasetID: "mnist-iid", NumClasses: 10, TrainSampleCount: 60000}); err != nil {
		t.Fatalf("register: %v", err)
	}
	sigma := 0.8
	partition := datasets.Partition{
		PartitionID:       "p-quantity-skew",
		DatasetID:         "mnist-iid",
		Strategy:          "quantity_skew",
		NumClients:        4,
		QuantitySkewSigma: &sigma,
	}
	if _, err := service.CreatePartition(ctx, partition); err != nil {
		t.Fatalf("create quantity_skew partition: %v", err)
	}
}

func TestDatasetServiceCreatePartitionSucceedsAndRejectsDuplicateID(t *testing.T) {
	ctx := context.Background()
	service := newDatasetService()
	if _, err := service.Register(ctx, datasets.Dataset{DatasetID: "mnist-iid", NumClasses: 10, TrainSampleCount: 60000}); err != nil {
		t.Fatalf("register: %v", err)
	}
	partition := datasets.Partition{PartitionID: "p1", DatasetID: "mnist-iid", Strategy: "iid", NumClients: 4}
	if _, err := service.CreatePartition(ctx, partition); err != nil {
		t.Fatalf("create partition: %v", err)
	}
	if _, err := service.CreatePartition(ctx, partition); !errors.Is(err, ErrPartitionAlreadyExists) {
		t.Fatalf("expected ErrPartitionAlreadyExists, got %v", err)
	}

	list, err := service.ListPartitions(ctx, "mnist-iid")
	if err != nil || len(list) != 1 {
		t.Fatalf("list partitions: len=%d err=%v", len(list), err)
	}
}

func TestDatasetServiceCreatePartitionRequiresExistingDataset(t *testing.T) {
	ctx := context.Background()
	service := newDatasetService()
	_, err := service.CreatePartition(ctx, datasets.Partition{
		PartitionID: "p1", DatasetID: "does-not-exist", Strategy: "iid", NumClients: 4,
	})
	if !errors.Is(err, ErrNotFound) {
		t.Fatalf("expected ErrNotFound, got %v", err)
	}
}
