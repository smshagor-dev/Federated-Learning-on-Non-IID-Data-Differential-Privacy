package observability

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSecurityEventJournalStartsEmpty(t *testing.T) {
	journal, err := NewSecurityEventJournal(filepath.Join(t.TempDir(), "events.jsonl"))
	if err != nil {
		t.Fatalf("NewSecurityEventJournal: %v", err)
	}
	if journal.Size() != 0 {
		t.Fatalf("expected empty journal, got size %d", journal.Size())
	}
}

func TestSecurityEventJournalEmitAndList(t *testing.T) {
	journal, err := NewSecurityEventJournal(filepath.Join(t.TempDir(), "events.jsonl"))
	if err != nil {
		t.Fatalf("NewSecurityEventJournal: %v", err)
	}
	ok, reason := journal.Emit(SecurityEvent{
		SchemaVersion: SecurityEventSchemaVersion,
		SourceService: "go-api",
		EventType:     EventSecurityPermissionDenied,
		WorkerID:      "worker-1",
	})
	if !ok {
		t.Fatalf("emit rejected: %s", reason)
	}
	if journal.Size() != 1 {
		t.Fatalf("expected size 1, got %d", journal.Size())
	}
	result := journal.List(SecurityEventListFilters{})
	if len(result.Events) != 1 {
		t.Fatalf("expected 1 listed event, got %d", len(result.Events))
	}
	if result.Events[0].EventID == "" || result.Events[0].Timestamp == "" || result.Events[0].PayloadChecksum == "" {
		t.Fatalf("expected event_id/timestamp/payload_checksum to be assigned: %+v", result.Events[0])
	}
}

func TestSecurityEventJournalRestartPersistence(t *testing.T) {
	path := filepath.Join(t.TempDir(), "events.jsonl")
	journal, err := NewSecurityEventJournal(path)
	if err != nil {
		t.Fatalf("NewSecurityEventJournal: %v", err)
	}
	if ok, reason := journal.Emit(SecurityEvent{SchemaVersion: SecurityEventSchemaVersion, SourceService: "go-api", EventType: EventWorkerRegistered}); !ok {
		t.Fatalf("emit rejected: %s", reason)
	}

	restarted, err := NewSecurityEventJournal(path)
	if err != nil {
		t.Fatalf("restart NewSecurityEventJournal: %v", err)
	}
	if restarted.Size() != 1 {
		t.Fatalf("expected restarted journal to reload 1 event, got %d", restarted.Size())
	}
	if restarted.RecoveredLineCount() != 0 {
		t.Fatalf("expected 0 recovered lines on a clean file, got %d", restarted.RecoveredLineCount())
	}
}

func TestSecurityEventJournalCursorPagination(t *testing.T) {
	journal, err := NewSecurityEventJournal(filepath.Join(t.TempDir(), "events.jsonl"))
	if err != nil {
		t.Fatalf("NewSecurityEventJournal: %v", err)
	}
	journal.Emit(SecurityEvent{SchemaVersion: SecurityEventSchemaVersion, SourceService: "go-api", EventType: EventWorkerRegistered})
	journal.Emit(SecurityEvent{SchemaVersion: SecurityEventSchemaVersion, SourceService: "go-api", EventType: EventWorkerActivated})

	firstPage := journal.List(SecurityEventListFilters{Limit: 1})
	if len(firstPage.Events) != 1 || firstPage.NextCursor == "" {
		t.Fatalf("expected page of 1 with a non-empty cursor, got %+v", firstPage)
	}
	secondPage := journal.List(SecurityEventListFilters{AfterEventID: firstPage.NextCursor})
	if len(secondPage.Events) != 1 || secondPage.Events[0].EventType != EventWorkerActivated {
		t.Fatalf("expected cursor to resume at the second event, got %+v", secondPage)
	}
}

func TestSecurityEventJournalSeverityFilter(t *testing.T) {
	journal, err := NewSecurityEventJournal(filepath.Join(t.TempDir(), "events.jsonl"))
	if err != nil {
		t.Fatalf("NewSecurityEventJournal: %v", err)
	}
	journal.Emit(SecurityEvent{SchemaVersion: SecurityEventSchemaVersion, SourceService: "go-api", EventType: EventWorkerRegistered})
	result := journal.List(SecurityEventListFilters{MinSeverity: SeverityCritical})
	if len(result.Events) != 0 {
		t.Fatalf("expected no events at CRITICAL minimum severity, got %d", len(result.Events))
	}
}

func TestSecurityEventJournalInvalidEventIsDropped(t *testing.T) {
	journal, err := NewSecurityEventJournal(filepath.Join(t.TempDir(), "events.jsonl"))
	if err != nil {
		t.Fatalf("NewSecurityEventJournal: %v", err)
	}
	ok, _ := journal.Emit(SecurityEvent{SchemaVersion: SecurityEventSchemaVersion, SourceService: ""})
	if ok {
		t.Fatalf("expected emit of an event with no source_service to be rejected")
	}
	if journal.Size() != 0 {
		t.Fatalf("expected invalid event not to be persisted")
	}
}

func TestSecurityEventJournalCorruptionRecovery(t *testing.T) {
	path := filepath.Join(t.TempDir(), "events.jsonl")
	journal, err := NewSecurityEventJournal(path)
	if err != nil {
		t.Fatalf("NewSecurityEventJournal: %v", err)
	}
	journal.Emit(SecurityEvent{SchemaVersion: SecurityEventSchemaVersion, SourceService: "go-api", EventType: EventWorkerRegistered})

	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatalf("open for corruption append: %v", err)
	}
	file.WriteString("not valid json at all\n")
	file.WriteString(`{"schema_version":1,"event_id":"x"}` + "\n")
	file.Close()

	reloaded, err := NewSecurityEventJournal(path)
	if err != nil {
		t.Fatalf("reload after corruption: %v", err)
	}
	if reloaded.Size() != 1 {
		t.Fatalf("expected 1 valid record to survive corruption, got %d", reloaded.Size())
	}
	if reloaded.RecoveredLineCount() != 2 {
		t.Fatalf("expected 2 recovered lines, got %d", reloaded.RecoveredLineCount())
	}
}

func TestSecurityEventJournalRotationAndRetention(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "events.jsonl")
	journal, err := NewSecurityEventJournal(path)
	if err != nil {
		t.Fatalf("NewSecurityEventJournal: %v", err)
	}
	journal.maxBytes = 200
	journal.maxRetained = 2
	for i := 0; i < 20; i++ {
		journal.Emit(SecurityEvent{
			SchemaVersion: SecurityEventSchemaVersion,
			SourceService: "go-api",
			EventType:     EventHeartbeatAccepted,
			WorkerID:      "worker",
		})
	}
	if _, err := os.Stat(path + ".1"); err != nil {
		t.Fatalf("expected rotation to produce a .1 file: %v", err)
	}
	if _, err := os.Stat(path + ".3"); err == nil {
		t.Fatalf("expected retention count of 2 to never keep a third generation")
	}
	if journal.Size() >= 20 {
		t.Fatalf("expected the active file's in-memory view to no longer hold every event")
	}
}
