package execution

import (
	"path/filepath"
	"testing"
	"time"
)

func TestJournalAppendUniqueRejectsDuplicateEventID(t *testing.T) {
	journal, err := NewJournal(filepath.Join(t.TempDir(), "events.jsonl"))
	if err != nil {
		t.Fatalf("new journal: %v", err)
	}
	event := Event{
		EventID:     "exec-1-backend-event-7",
		ExecutionID: "exec-1",
		Type:        "COORDINATOR_TASK_FAILED",
		Round:       4,
		Timestamp:   time.Date(2026, 8, 23, 15, 10, 0, 0, time.UTC),
	}
	appended, err := journal.AppendUnique(event)
	if err != nil {
		t.Fatalf("first append: %v", err)
	}
	if !appended {
		t.Fatal("first append was unexpectedly treated as duplicate")
	}
	appended, err = journal.AppendUnique(event)
	if err != nil {
		t.Fatalf("duplicate append: %v", err)
	}
	if appended {
		t.Fatal("duplicate event_id was appended")
	}
	events, err := journal.List("exec-1", 0)
	if err != nil {
		t.Fatalf("list events: %v", err)
	}
	if len(events) != 1 {
		t.Fatalf("events=%d, want 1", len(events))
	}
}
