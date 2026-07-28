package application

import (
	"context"
	"errors"
	"fmt"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/datasets"
)

var (
	ErrDatasetAlreadyRegistered = errors.New("dataset already registered")
	ErrInvalidDatasetTransition = errors.New("invalid dataset status transition")
	// ErrDatasetNotReadyToValidate mirrors Python's validate() rejection
	// of a dataset with no training samples or non-positive num_classes.
	ErrDatasetNotReadyToValidate = errors.New("dataset is not ready to validate")
	ErrPartitionAlreadyExists    = errors.New("partition already exists")
	ErrInvalidPartitionManifest  = errors.New("invalid partition manifest")
)

var validPartitionStrategies = map[string]bool{
	"iid":           true,
	"dirichlet":     true,
	"pathological":  true,
	"quantity_skew": true,
}

type DatasetService struct {
	repo  datasets.Repository
	clock Clock
	audit *AuditService
}

func (s *DatasetService) Register(ctx context.Context, dataset datasets.Dataset) (datasets.Dataset, error) {
	if _, exists, err := s.repo.Get(ctx, dataset.DatasetID); err != nil {
		return datasets.Dataset{}, err
	} else if exists {
		return datasets.Dataset{}, fmt.Errorf("%w: %s", ErrDatasetAlreadyRegistered, dataset.DatasetID)
	}
	now := float64(s.clock().UTC().Unix())
	dataset.Status = datasets.StatusDraft
	dataset.CreatedAt = now
	dataset.UpdatedAt = now
	item, err := s.repo.Create(ctx, dataset)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "dataset.register", "dataset", item.DatasetID, "success", map[string]any{"task_type": item.TaskType})
	}
	return item, err
}

func (s *DatasetService) List(ctx context.Context) ([]datasets.Dataset, error) {
	return s.repo.List(ctx)
}

func (s *DatasetService) Get(ctx context.Context, datasetID string) (datasets.Dataset, error) {
	dataset, ok, err := s.repo.Get(ctx, datasetID)
	if err != nil {
		return datasets.Dataset{}, err
	}
	if !ok {
		return datasets.Dataset{}, ErrNotFound
	}
	return dataset, nil
}

func (s *DatasetService) Validate(ctx context.Context, datasetID string) (datasets.Dataset, error) {
	dataset, err := s.Get(ctx, datasetID)
	if err != nil {
		return datasets.Dataset{}, err
	}
	if dataset.TrainSampleCount <= 0 {
		return datasets.Dataset{}, fmt.Errorf("%w: %s has no training samples", ErrDatasetNotReadyToValidate, datasetID)
	}
	if dataset.NumClasses <= 0 {
		return datasets.Dataset{}, fmt.Errorf("%w: %s has non-positive num_classes", ErrDatasetNotReadyToValidate, datasetID)
	}
	return s.transition(ctx, dataset, datasets.StatusValidated)
}

func (s *DatasetService) Activate(ctx context.Context, datasetID string) (datasets.Dataset, error) {
	dataset, err := s.Get(ctx, datasetID)
	if err != nil {
		return datasets.Dataset{}, err
	}
	return s.transition(ctx, dataset, datasets.StatusActive)
}

func (s *DatasetService) Deprecate(ctx context.Context, datasetID string) (datasets.Dataset, error) {
	dataset, err := s.Get(ctx, datasetID)
	if err != nil {
		return datasets.Dataset{}, err
	}
	return s.transition(ctx, dataset, datasets.StatusDeprecated)
}

func (s *DatasetService) transition(ctx context.Context, dataset datasets.Dataset, next datasets.Status) (datasets.Dataset, error) {
	if !dataset.CanTransitionTo(next) {
		return datasets.Dataset{}, fmt.Errorf("%w: %s cannot go %s -> %s", ErrInvalidDatasetTransition, dataset.DatasetID, dataset.Status, next)
	}
	dataset.Status = next
	dataset.UpdatedAt = float64(s.clock().UTC().Unix())
	updated, err := s.repo.Update(ctx, dataset)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "dataset.transition", "dataset", updated.DatasetID, "success", map[string]any{"status": string(updated.Status)})
	}
	return updated, err
}

// CreatePartition records a partition manifest (see datasets.Partition's
// doc comment: Go stores per-client sample counts and manifest shape,
// not actual sample index assignments — those require the real dataset
// and are computed by Python). Performs the same structural checks
// Python's create_partition does before delegating to a strategy
// builder: a known strategy name, dirichlet requiring alpha,
// pathological requiring classes_per_client, and a positive client
// count.
func (s *DatasetService) CreatePartition(ctx context.Context, partition datasets.Partition) (datasets.Partition, error) {
	dataset, err := s.Get(ctx, partition.DatasetID)
	if err != nil {
		return datasets.Partition{}, err
	}
	if !validPartitionStrategies[partition.Strategy] {
		return datasets.Partition{}, fmt.Errorf("%w: unsupported strategy %q", ErrInvalidPartitionManifest, partition.Strategy)
	}
	if partition.Strategy == "dirichlet" && partition.Alpha == nil {
		return datasets.Partition{}, fmt.Errorf("%w: dirichlet partitioning requires alpha", ErrInvalidPartitionManifest)
	}
	if partition.Strategy == "pathological" && partition.ClassesPerClient == nil {
		return datasets.Partition{}, fmt.Errorf("%w: pathological partitioning requires classes_per_client", ErrInvalidPartitionManifest)
	}
	if partition.Strategy == "quantity_skew" && partition.QuantitySkewSigma == nil {
		return datasets.Partition{}, fmt.Errorf("%w: quantity_skew partitioning requires quantity_skew_sigma", ErrInvalidPartitionManifest)
	}
	if partition.NumClients <= 0 {
		return datasets.Partition{}, fmt.Errorf("%w: num_clients must be > 0", ErrInvalidPartitionManifest)
	}
	if _, exists, err := s.repo.GetPartition(ctx, partition.PartitionID); err != nil {
		return datasets.Partition{}, err
	} else if exists {
		return datasets.Partition{}, fmt.Errorf("%w: %s", ErrPartitionAlreadyExists, partition.PartitionID)
	}
	_ = dataset // referenced dataset must exist; its own fields aren't otherwise needed here
	partition.CreatedAt = float64(s.clock().UTC().Unix())
	created, err := s.repo.CreatePartition(ctx, partition)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "dataset.partition.create", "dataset_partition", created.PartitionID, "success", map[string]any{"dataset_id": created.DatasetID, "strategy": created.Strategy})
	}
	return created, err
}

func (s *DatasetService) GetPartition(ctx context.Context, partitionID string) (datasets.Partition, error) {
	partition, ok, err := s.repo.GetPartition(ctx, partitionID)
	if err != nil {
		return datasets.Partition{}, err
	}
	if !ok {
		return datasets.Partition{}, ErrNotFound
	}
	return partition, nil
}

func (s *DatasetService) ListPartitions(ctx context.Context, datasetID string) ([]datasets.Partition, error) {
	return s.repo.ListPartitions(ctx, datasetID)
}
