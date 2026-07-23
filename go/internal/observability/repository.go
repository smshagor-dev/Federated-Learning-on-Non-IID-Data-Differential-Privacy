package observability

import (
	"context"
	"slices"
	"sync"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/storage"
)

type AuditRepository interface {
	Append(ctx context.Context, event AuditEvent) (AuditEvent, error)
	List(ctx context.Context, limit int) ([]AuditEvent, error)
}

type InMemoryAuditRepository struct {
	mu    sync.RWMutex
	items []AuditEvent
}

func NewInMemoryAuditRepository() *InMemoryAuditRepository {
	return &InMemoryAuditRepository{items: make([]AuditEvent, 0)}
}

func (r *InMemoryAuditRepository) Append(_ context.Context, event AuditEvent) (AuditEvent, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items = append(r.items, event)
	return event, nil
}

func (r *InMemoryAuditRepository) List(_ context.Context, limit int) ([]AuditEvent, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return limitEvents(r.items, limit), nil
}

type FileAuditRepository struct {
	mu    sync.RWMutex
	path  string
	items []AuditEvent
}

func NewFileAuditRepository(path string) (*FileAuditRepository, error) {
	repo := &FileAuditRepository{
		path:  path,
		items: make([]AuditEvent, 0),
	}
	if err := storage.LoadJSON(path, &repo.items); err != nil {
		return nil, err
	}
	slices.SortFunc(repo.items, compareAuditEvents)
	return repo, nil
}

func (r *FileAuditRepository) Append(_ context.Context, event AuditEvent) (AuditEvent, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items = append(r.items, event)
	slices.SortFunc(r.items, compareAuditEvents)
	return event, storage.SaveJSON(r.path, r.items)
}

func (r *FileAuditRepository) List(_ context.Context, limit int) ([]AuditEvent, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return limitEvents(r.items, limit), nil
}

func compareAuditEvents(a, b AuditEvent) int {
	switch {
	case a.Timestamp.After(b.Timestamp):
		return -1
	case a.Timestamp.Before(b.Timestamp):
		return 1
	case a.ID > b.ID:
		return -1
	case a.ID < b.ID:
		return 1
	default:
		return 0
	}
}

func limitEvents(items []AuditEvent, limit int) []AuditEvent {
	if limit <= 0 || limit > len(items) {
		limit = len(items)
	}
	result := make([]AuditEvent, 0, limit)
	for i := 0; i < limit; i++ {
		result = append(result, items[i])
	}
	return result
}
