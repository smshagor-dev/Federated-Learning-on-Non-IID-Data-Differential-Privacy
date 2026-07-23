package experiments

import (
	"context"
	"slices"
	"sync"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/storage"
)

type Repository interface {
	Create(ctx context.Context, experiment Experiment) (Experiment, error)
	List(ctx context.Context) ([]Experiment, error)
	Get(ctx context.Context, id string) (Experiment, bool, error)
	Update(ctx context.Context, experiment Experiment) (Experiment, error)
}

type InMemoryRepository struct {
	mu    sync.RWMutex
	items map[string]Experiment
}

func NewInMemoryRepository() *InMemoryRepository {
	return &InMemoryRepository{items: map[string]Experiment{}}
}

func (r *InMemoryRepository) Create(_ context.Context, experiment Experiment) (Experiment, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[experiment.ID] = experiment
	return experiment, nil
}

func (r *InMemoryRepository) List(_ context.Context) ([]Experiment, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	items := make([]Experiment, 0, len(r.items))
	for _, item := range r.items {
		items = append(items, item)
	}
	return items, nil
}

func (r *InMemoryRepository) Get(_ context.Context, id string) (Experiment, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	item, ok := r.items[id]
	return item, ok, nil
}

func (r *InMemoryRepository) Update(_ context.Context, experiment Experiment) (Experiment, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[experiment.ID] = experiment
	return experiment, nil
}

type FileRepository struct {
	mu    sync.RWMutex
	path  string
	items map[string]Experiment
}

func NewFileRepository(path string) (*FileRepository, error) {
	repo := &FileRepository{
		path:  path,
		items: map[string]Experiment{},
	}
	var items []Experiment
	if err := storage.LoadJSON(path, &items); err != nil {
		return nil, err
	}
	for _, item := range items {
		repo.items[item.ID] = item
	}
	return repo, nil
}

func (r *FileRepository) Create(_ context.Context, experiment Experiment) (Experiment, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[experiment.ID] = experiment
	return experiment, r.persistLocked()
}

func (r *FileRepository) List(_ context.Context) ([]Experiment, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	items := make([]Experiment, 0, len(r.items))
	for _, item := range r.items {
		items = append(items, item)
	}
	slices.SortFunc(items, func(a, b Experiment) int {
		switch {
		case a.CreatedAt.Before(b.CreatedAt):
			return -1
		case a.CreatedAt.After(b.CreatedAt):
			return 1
		case a.ID < b.ID:
			return -1
		case a.ID > b.ID:
			return 1
		default:
			return 0
		}
	})
	return items, nil
}

func (r *FileRepository) Get(_ context.Context, id string) (Experiment, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	item, ok := r.items[id]
	return item, ok, nil
}

func (r *FileRepository) Update(_ context.Context, experiment Experiment) (Experiment, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[experiment.ID] = experiment
	return experiment, r.persistLocked()
}

func (r *FileRepository) persistLocked() error {
	items := make([]Experiment, 0, len(r.items))
	for _, item := range r.items {
		items = append(items, item)
	}
	slices.SortFunc(items, func(a, b Experiment) int {
		switch {
		case a.CreatedAt.Before(b.CreatedAt):
			return -1
		case a.CreatedAt.After(b.CreatedAt):
			return 1
		case a.ID < b.ID:
			return -1
		case a.ID > b.ID:
			return 1
		default:
			return 0
		}
	})
	return storage.SaveJSON(r.path, items)
}
