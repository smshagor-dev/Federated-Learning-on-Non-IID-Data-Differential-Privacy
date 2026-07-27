package datasets

import (
	"context"
	"slices"
	"sync"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/storage"
)

type Repository interface {
	Create(ctx context.Context, dataset Dataset) (Dataset, error)
	List(ctx context.Context) ([]Dataset, error)
	Get(ctx context.Context, datasetID string) (Dataset, bool, error)
	Update(ctx context.Context, dataset Dataset) (Dataset, error)

	CreatePartition(ctx context.Context, partition Partition) (Partition, error)
	GetPartition(ctx context.Context, partitionID string) (Partition, bool, error)
	ListPartitions(ctx context.Context, datasetID string) ([]Partition, error)
}

type InMemoryRepository struct {
	mu         sync.RWMutex
	datasets   map[string]Dataset
	partitions map[string]Partition
}

func NewInMemoryRepository() *InMemoryRepository {
	return &InMemoryRepository{datasets: map[string]Dataset{}, partitions: map[string]Partition{}}
}

func (r *InMemoryRepository) Create(_ context.Context, dataset Dataset) (Dataset, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.datasets[dataset.DatasetID] = dataset
	return dataset, nil
}

func (r *InMemoryRepository) List(_ context.Context) ([]Dataset, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return sortedDatasets(r.datasets), nil
}

func (r *InMemoryRepository) Get(_ context.Context, datasetID string) (Dataset, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	dataset, ok := r.datasets[datasetID]
	return dataset, ok, nil
}

func (r *InMemoryRepository) Update(_ context.Context, dataset Dataset) (Dataset, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.datasets[dataset.DatasetID] = dataset
	return dataset, nil
}

func (r *InMemoryRepository) CreatePartition(_ context.Context, partition Partition) (Partition, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.partitions[partition.PartitionID] = partition
	return partition, nil
}

func (r *InMemoryRepository) GetPartition(_ context.Context, partitionID string) (Partition, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	partition, ok := r.partitions[partitionID]
	return partition, ok, nil
}

func (r *InMemoryRepository) ListPartitions(_ context.Context, datasetID string) ([]Partition, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return filteredPartitions(r.partitions, datasetID), nil
}

type FileRepository struct {
	mu             sync.RWMutex
	datasetsPath   string
	partitionsPath string
	datasets       map[string]Dataset
	partitions     map[string]Partition
}

func NewFileRepository(datasetsPath, partitionsPath string) (*FileRepository, error) {
	repo := &FileRepository{
		datasetsPath:   datasetsPath,
		partitionsPath: partitionsPath,
		datasets:       map[string]Dataset{},
		partitions:     map[string]Partition{},
	}
	var datasetItems []Dataset
	if err := storage.LoadJSON(datasetsPath, &datasetItems); err != nil {
		return nil, err
	}
	for _, item := range datasetItems {
		repo.datasets[item.DatasetID] = item
	}
	var partitionItems []Partition
	if err := storage.LoadJSON(partitionsPath, &partitionItems); err != nil {
		return nil, err
	}
	for _, item := range partitionItems {
		repo.partitions[item.PartitionID] = item
	}
	return repo, nil
}

func (r *FileRepository) Create(_ context.Context, dataset Dataset) (Dataset, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.datasets[dataset.DatasetID] = dataset
	return dataset, storage.SaveJSON(r.datasetsPath, sortedDatasets(r.datasets))
}

func (r *FileRepository) List(_ context.Context) ([]Dataset, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return sortedDatasets(r.datasets), nil
}

func (r *FileRepository) Get(_ context.Context, datasetID string) (Dataset, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	dataset, ok := r.datasets[datasetID]
	return dataset, ok, nil
}

func (r *FileRepository) Update(_ context.Context, dataset Dataset) (Dataset, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.datasets[dataset.DatasetID] = dataset
	return dataset, storage.SaveJSON(r.datasetsPath, sortedDatasets(r.datasets))
}

func (r *FileRepository) CreatePartition(_ context.Context, partition Partition) (Partition, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.partitions[partition.PartitionID] = partition
	return partition, storage.SaveJSON(r.partitionsPath, sortedPartitions(r.partitions))
}

func (r *FileRepository) GetPartition(_ context.Context, partitionID string) (Partition, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	partition, ok := r.partitions[partitionID]
	return partition, ok, nil
}

func (r *FileRepository) ListPartitions(_ context.Context, datasetID string) ([]Partition, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return filteredPartitions(r.partitions, datasetID), nil
}

func sortedDatasets(items map[string]Dataset) []Dataset {
	datasets := make([]Dataset, 0, len(items))
	for _, item := range items {
		datasets = append(datasets, item)
	}
	slices.SortFunc(datasets, func(a, b Dataset) int {
		switch {
		case a.DatasetID < b.DatasetID:
			return -1
		case a.DatasetID > b.DatasetID:
			return 1
		default:
			return 0
		}
	})
	return datasets
}

func sortedPartitions(items map[string]Partition) []Partition {
	partitions := make([]Partition, 0, len(items))
	for _, item := range items {
		partitions = append(partitions, item)
	}
	slices.SortFunc(partitions, func(a, b Partition) int {
		switch {
		case a.PartitionID < b.PartitionID:
			return -1
		case a.PartitionID > b.PartitionID:
			return 1
		default:
			return 0
		}
	})
	return partitions
}

func filteredPartitions(items map[string]Partition, datasetID string) []Partition {
	all := sortedPartitions(items)
	if datasetID == "" {
		return all
	}
	filtered := make([]Partition, 0, len(all))
	for _, partition := range all {
		if partition.DatasetID == datasetID {
			filtered = append(filtered, partition)
		}
	}
	return filtered
}
