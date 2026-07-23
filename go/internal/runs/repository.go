package runs

import (
	"context"
	"slices"
	"sync"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/storage"
)

type Repository interface {
	Create(ctx context.Context, run Run) (Run, error)
	List(ctx context.Context) ([]Run, error)
	Get(ctx context.Context, id string) (Run, bool, error)
	Update(ctx context.Context, run Run) (Run, error)
}

type InMemoryRepository struct {
	mu    sync.RWMutex
	items map[string]Run
}

func NewInMemoryRepository() *InMemoryRepository {
	return &InMemoryRepository{items: map[string]Run{}}
}

func (r *InMemoryRepository) Create(_ context.Context, run Run) (Run, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[run.ID] = run
	return run, nil
}

func (r *InMemoryRepository) List(_ context.Context) ([]Run, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	items := make([]Run, 0, len(r.items))
	for _, item := range r.items {
		items = append(items, item)
	}
	return items, nil
}

func (r *InMemoryRepository) Get(_ context.Context, id string) (Run, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	item, ok := r.items[id]
	return item, ok, nil
}

func (r *InMemoryRepository) Update(_ context.Context, run Run) (Run, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[run.ID] = run
	return run, nil
}

type FileRepository struct {
	mu    sync.RWMutex
	path  string
	items map[string]Run
}

func NewFileRepository(path string) (*FileRepository, error) {
	repo := &FileRepository{
		path:  path,
		items: map[string]Run{},
	}
	var items []Run
	if err := storage.LoadJSON(path, &items); err != nil {
		return nil, err
	}
	for _, item := range items {
		repo.items[item.ID] = item
	}
	return repo, nil
}

func (r *FileRepository) Create(_ context.Context, run Run) (Run, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[run.ID] = run
	return run, r.persistLocked()
}

func (r *FileRepository) List(_ context.Context) ([]Run, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	items := make([]Run, 0, len(r.items))
	for _, item := range r.items {
		items = append(items, item)
	}
	slices.SortFunc(items, func(a, b Run) int {
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

func (r *FileRepository) Get(_ context.Context, id string) (Run, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	item, ok := r.items[id]
	return item, ok, nil
}

func (r *FileRepository) Update(_ context.Context, run Run) (Run, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[run.ID] = run
	return run, r.persistLocked()
}

func (r *FileRepository) persistLocked() error {
	items := make([]Run, 0, len(r.items))
	for _, item := range r.items {
		items = append(items, item)
	}
	slices.SortFunc(items, func(a, b Run) int {
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
