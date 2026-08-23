package execution

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"
)

func TestInMemoryRepositoryRejectsStaleRevision(t *testing.T) {
	repo := NewInMemoryRepository()
	created, err := repo.Create(context.Background(), Record{
		ID:        "exec-1",
		Status:    StatusCreated,
		CreatedAt: time.Unix(1, 0).UTC(),
		UpdatedAt: time.Unix(1, 0).UTC(),
	})
	if err != nil {
		t.Fatal(err)
	}
	created.Status = StatusRunning
	updated, err := repo.Update(context.Background(), created, created.Revision)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Revision != 2 {
		t.Fatalf("revision = %d, want 2", updated.Revision)
	}
	created.Status = StatusFailed
	if _, err := repo.Update(context.Background(), created, created.Revision); !errors.Is(err, ErrRevisionConflict) {
		t.Fatalf("stale update error = %v, want ErrRevisionConflict", err)
	}
}

func TestFileRepositorySurvivesReload(t *testing.T) {
	path := filepath.Join(t.TempDir(), "executions.json")
	repo, err := NewFileRepository(path)
	if err != nil {
		t.Fatal(err)
	}
	created, err := repo.Create(context.Background(), Record{
		ID:        "exec-durable",
		Status:    StatusCreated,
		CreatedAt: time.Unix(2, 0).UTC(),
		UpdatedAt: time.Unix(2, 0).UTC(),
	})
	if err != nil {
		t.Fatal(err)
	}
	created.Status = StatusPaused
	if _, err := repo.Update(context.Background(), created, created.Revision); err != nil {
		t.Fatal(err)
	}

	reloaded, err := NewFileRepository(path)
	if err != nil {
		t.Fatal(err)
	}
	record, ok, err := reloaded.Get(context.Background(), "exec-durable")
	if err != nil {
		t.Fatal(err)
	}
	if !ok || record.Status != StatusPaused || record.Revision != 2 {
		t.Fatalf("reloaded record = %#v, ok=%v", record, ok)
	}
}

func TestJournalPersistsAndFiltersEvents(t *testing.T) {
	path := filepath.Join(t.TempDir(), "execution-events.jsonl")
	journal, err := NewJournal(path)
	if err != nil {
		t.Fatal(err)
	}
	for _, event := range []Event{
		{EventID: "a1", ExecutionID: "exec-a", Type: "CREATED", Timestamp: time.Unix(1, 0).UTC()},
		{EventID: "b1", ExecutionID: "exec-b", Type: "CREATED", Timestamp: time.Unix(2, 0).UTC()},
		{EventID: "a2", ExecutionID: "exec-a", Type: "STARTED", Timestamp: time.Unix(3, 0).UTC()},
	} {
		if err := journal.Append(event); err != nil {
			t.Fatal(err)
		}
	}

	reloaded, err := NewJournal(path)
	if err != nil {
		t.Fatal(err)
	}
	events, err := reloaded.List("exec-a", 1)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) != 1 || events[0].EventID != "a2" {
		t.Fatalf("events = %#v", events)
	}
}
