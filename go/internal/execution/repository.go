package execution

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"sort"
	"sync"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/storage"
)

var ErrRevisionConflict = errors.New("execution revision conflict")

type Repository interface {
	Create(ctx context.Context, record Record) (Record, error)
	List(ctx context.Context) ([]Record, error)
	Get(ctx context.Context, id string) (Record, bool, error)
	Update(ctx context.Context, record Record, expectedRevision uint64) (Record, error)
}

type FileRepository struct {
	mu    sync.RWMutex
	path  string
	items map[string]Record
}

func NewFileRepository(path string) (*FileRepository, error) {
	repository := &FileRepository{path: path, items: map[string]Record{}}
	var records []Record
	if err := storage.LoadJSON(path, &records); err != nil {
		return nil, err
	}
	for _, record := range records {
		repository.items[record.ID] = record
	}
	return repository, nil
}

func (r *FileRepository) Create(_ context.Context, record Record) (Record, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, exists := r.items[record.ID]; exists {
		return Record{}, ErrRevisionConflict
	}
	if record.Revision == 0 {
		record.Revision = 1
	}
	r.items[record.ID] = record
	if err := r.persistLocked(); err != nil {
		delete(r.items, record.ID)
		return Record{}, err
	}
	return record, nil
}

func (r *FileRepository) List(_ context.Context) ([]Record, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	records := make([]Record, 0, len(r.items))
	for _, record := range r.items {
		records = append(records, record)
	}
	sort.Slice(records, func(i, j int) bool {
		if records[i].CreatedAt.Equal(records[j].CreatedAt) {
			return records[i].ID < records[j].ID
		}
		return records[i].CreatedAt.Before(records[j].CreatedAt)
	})
	return records, nil
}

func (r *FileRepository) Get(_ context.Context, id string) (Record, bool, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	record, ok := r.items[id]
	return record, ok, nil
}

func (r *FileRepository) Update(_ context.Context, record Record, expectedRevision uint64) (Record, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	current, exists := r.items[record.ID]
	if !exists || current.Revision != expectedRevision {
		return Record{}, ErrRevisionConflict
	}
	previous := current
	record.Revision = expectedRevision + 1
	r.items[record.ID] = record
	if err := r.persistLocked(); err != nil {
		r.items[record.ID] = previous
		return Record{}, err
	}
	return record, nil
}

func (r *FileRepository) persistLocked() error {
	records := make([]Record, 0, len(r.items))
	for _, record := range r.items {
		records = append(records, record)
	}
	sort.Slice(records, func(i, j int) bool {
		if records[i].CreatedAt.Equal(records[j].CreatedAt) {
			return records[i].ID < records[j].ID
		}
		return records[i].CreatedAt.Before(records[j].CreatedAt)
	})
	return storage.SaveJSON(r.path, records)
}

type Journal struct {
	mu   sync.Mutex
	path string
}

func NewJournal(path string) (*Journal, error) {
	if path == "" {
		return nil, errors.New("execution journal path is required")
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return nil, err
	}
	if err := file.Close(); err != nil {
		return nil, err
	}
	return &Journal{path: path}, nil
}

func (j *Journal) Append(event Event) error {
	if j == nil {
		return nil
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		return err
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	file, err := os.OpenFile(j.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer file.Close()
	if _, err := file.Write(append(encoded, '\n')); err != nil {
		return err
	}
	return file.Sync()
}

func (j *Journal) List(executionID string, limit int) ([]Event, error) {
	if j == nil {
		return nil, nil
	}
	j.mu.Lock()
	defer j.mu.Unlock()
	file, err := os.Open(j.path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	defer file.Close()

	var events []Event
	scanner := bufio.NewScanner(file)
	buffer := make([]byte, 64*1024)
	scanner.Buffer(buffer, 4*1024*1024)
	for scanner.Scan() {
		var event Event
		if err := json.Unmarshal(scanner.Bytes(), &event); err != nil {
			return nil, err
		}
		if executionID == "" || event.ExecutionID == executionID {
			events = append(events, event)
		}
	}
	if err := scanner.Err(); err != nil {
		return nil, err
	}
	if limit > 0 && len(events) > limit {
		events = events[len(events)-limit:]
	}
	return events, nil
}
