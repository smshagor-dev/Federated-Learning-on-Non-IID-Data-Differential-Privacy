package projects

import (
	"context"
	"slices"
	"sync"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/storage"
)

type Repository interface {
	Create(ctx context.Context, project Project) (Project, error)
	List(ctx context.Context) ([]Project, error)
	Get(ctx context.Context, id string) (Project, bool, error)
}

type InMemoryRepository struct {
	mu    sync.RWMutex
	items map[string]Project
}

func NewInMemoryRepository() *InMemoryRepository {
	return &InMemoryRepository{items: map[string]Project{}}
}

func (r *InMemoryRepository) Create(_ context.Context, project Project) (Project, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[project.ID] = project
	return project, nil
}

func (r *InMemoryRepository) List(_ context.Context) ([]Project, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	projects := make([]Project, 0, len(r.items))
	for _, item := range r.items {
		projects = append(projects, item)
	}
	return projects, nil
}

func (r *InMemoryRepository) Get(_ context.Context, id string) (Project, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	project, ok := r.items[id]
	return project, ok, nil
}

type FileRepository struct {
	mu    sync.RWMutex
	path  string
	items map[string]Project
}

func NewFileRepository(path string) (*FileRepository, error) {
	repo := &FileRepository{
		path:  path,
		items: map[string]Project{},
	}
	var items []Project
	if err := storage.LoadJSON(path, &items); err != nil {
		return nil, err
	}
	for _, item := range items {
		repo.items[item.ID] = item
	}
	return repo, nil
}

func (r *FileRepository) Create(_ context.Context, project Project) (Project, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[project.ID] = project
	return project, r.persistLocked()
}

func (r *FileRepository) List(_ context.Context) ([]Project, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	projects := make([]Project, 0, len(r.items))
	for _, item := range r.items {
		projects = append(projects, item)
	}
	slices.SortFunc(projects, func(a, b Project) int {
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
	return projects, nil
}

func (r *FileRepository) Get(_ context.Context, id string) (Project, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	project, ok := r.items[id]
	return project, ok, nil
}

func (r *FileRepository) persistLocked() error {
	items := make([]Project, 0, len(r.items))
	for _, item := range r.items {
		items = append(items, item)
	}
	slices.SortFunc(items, func(a, b Project) int {
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
