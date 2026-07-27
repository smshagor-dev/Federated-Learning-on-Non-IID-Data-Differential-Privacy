package models

import (
	"context"
	"slices"
	"sync"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/storage"
)

type Repository interface {
	Create(ctx context.Context, model Model) (Model, error)
	List(ctx context.Context) ([]Model, error)
	Get(ctx context.Context, name, version string) (Model, bool, error)
	Update(ctx context.Context, model Model) (Model, error)
}

type InMemoryRepository struct {
	mu    sync.RWMutex
	items map[string]Model
}

func NewInMemoryRepository() *InMemoryRepository {
	return &InMemoryRepository{items: map[string]Model{}}
}

func (r *InMemoryRepository) Create(_ context.Context, model Model) (Model, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[model.ID()] = model
	return model, nil
}

func (r *InMemoryRepository) List(_ context.Context) ([]Model, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return sortedModels(r.items), nil
}

func (r *InMemoryRepository) Get(_ context.Context, name, version string) (Model, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	model, ok := r.items[name+"__"+version]
	return model, ok, nil
}

func (r *InMemoryRepository) Update(_ context.Context, model Model) (Model, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[model.ID()] = model
	return model, nil
}

type FileRepository struct {
	mu    sync.RWMutex
	path  string
	items map[string]Model
}

func NewFileRepository(path string) (*FileRepository, error) {
	repo := &FileRepository{path: path, items: map[string]Model{}}
	var items []Model
	if err := storage.LoadJSON(path, &items); err != nil {
		return nil, err
	}
	for _, item := range items {
		repo.items[item.ID()] = item
	}
	return repo, nil
}

func (r *FileRepository) Create(_ context.Context, model Model) (Model, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[model.ID()] = model
	return model, r.persistLocked()
}

func (r *FileRepository) List(_ context.Context) ([]Model, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return sortedModels(r.items), nil
}

func (r *FileRepository) Get(_ context.Context, name, version string) (Model, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	model, ok := r.items[name+"__"+version]
	return model, ok, nil
}

func (r *FileRepository) Update(_ context.Context, model Model) (Model, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[model.ID()] = model
	return model, r.persistLocked()
}

func (r *FileRepository) persistLocked() error {
	return storage.SaveJSON(r.path, sortedModels(r.items))
}

func sortedModels(items map[string]Model) []Model {
	models := make([]Model, 0, len(items))
	for _, item := range items {
		models = append(models, item)
	}
	slices.SortFunc(models, func(a, b Model) int {
		if a.Name != b.Name {
			if a.Name < b.Name {
				return -1
			}
			return 1
		}
		switch {
		case a.Version < b.Version:
			return -1
		case a.Version > b.Version:
			return 1
		default:
			return 0
		}
	})
	return models
}
