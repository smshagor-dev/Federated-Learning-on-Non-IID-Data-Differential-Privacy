package observability

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSecurityAuditJournalStartsEmpty(t *testing.T) {
	journal, err := NewSecurityAuditJournal(filepath.Join(t.TempDir(), "audit.jsonl"))
	if err != nil {
		t.Fatalf("NewSecurityAuditJournal: %v", err)
	}
	if journal.Size() != 0 {
		t.Fatalf("expected empty journal, got size %d", journal.Size())
	}
}

func TestSecurityAuditJournalAppendAndList(t *testing.T) {
	journal, err := NewSecurityAuditJournal(filepath.Join(t.TempDir(), "audit.jsonl"))
	if err != nil {
		t.Fatalf("NewSecurityAuditJournal: %v", err)
	}
	ok, reason := journal.Append(SecurityAuditRecord{
		SafeActorID:  "go-api",
		ActorRole:    "service",
		Action:       "SuspendWorker",
		ResourceType: "worker_identity",
		ResourceID:   "worker-1",
		Outcome:      OutcomeAccepted,
		Reason:       "administrative_suspension",
	})
	if !ok {
		t.Fatalf("append rejected: %s", reason)
	}
	result := journal.List(SecurityAuditListFilters{})
	if len(result.Records) != 1 {
		t.Fatalf("expected 1 record, got %d", len(result.Records))
	}
	if result.Records[0].RecordID == "" || result.Records[0].PayloadChecksum == "" {
		t.Fatalf("expected record_id/payload_checksum to be assigned: %+v", result.Records[0])
	}
}

func TestSecurityAuditJournalRestartPersistence(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audit.jsonl")
	journal, err := NewSecurityAuditJournal(path)
	if err != nil {
		t.Fatalf("NewSecurityAuditJournal: %v", err)
	}
	journal.Append(SecurityAuditRecord{Action: "RotateCoordinatorSigningKey", Outcome: OutcomeAccepted})

	restarted, err := NewSecurityAuditJournal(path)
	if err != nil {
		t.Fatalf("restart NewSecurityAuditJournal: %v", err)
	}
	if restarted.Size() != 1 {
		t.Fatalf("expected restarted journal to reload 1 record, got %d", restarted.Size())
	}
}

func TestSecurityAuditJournalFiltering(t *testing.T) {
	journal, err := NewSecurityAuditJournal(filepath.Join(t.TempDir(), "audit.jsonl"))
	if err != nil {
		t.Fatalf("NewSecurityAuditJournal: %v", err)
	}
	journal.Append(SecurityAuditRecord{SafeActorID: "go-api", Action: "SuspendWorker", ResourceType: "worker_identity", Outcome: OutcomeAccepted})
	journal.Append(SecurityAuditRecord{SafeActorID: "worker-service", Action: "RevokeWorkerSigningKey", ResourceType: "worker_signing_key", ResourceID: "key-1", Outcome: OutcomeRejected})

	byAction := journal.List(SecurityAuditListFilters{Action: "RevokeWorkerSigningKey"})
	if len(byAction.Records) != 1 || byAction.Records[0].ResourceID != "key-1" {
		t.Fatalf("action filter did not isolate the matching record: %+v", byAction)
	}
	byOutcome := journal.List(SecurityAuditListFilters{Outcome: OutcomeRejected})
	if len(byOutcome.Records) != 1 {
		t.Fatalf("outcome filter did not isolate the matching record: %+v", byOutcome)
	}
	byActor := journal.List(SecurityAuditListFilters{ActorID: "go-api"})
	if len(byActor.Records) != 1 || byActor.Records[0].Action != "SuspendWorker" {
		t.Fatalf("actor filter did not isolate the matching record: %+v", byActor)
	}
}

func TestSecurityAuditJournalPagination(t *testing.T) {
	journal, err := NewSecurityAuditJournal(filepath.Join(t.TempDir(), "audit.jsonl"))
	if err != nil {
		t.Fatalf("NewSecurityAuditJournal: %v", err)
	}
	journal.Append(SecurityAuditRecord{Action: "SuspendWorker", Outcome: OutcomeAccepted})
	journal.Append(SecurityAuditRecord{Action: "ActivateWorker", Outcome: OutcomeAccepted})
	firstPage := journal.List(SecurityAuditListFilters{Limit: 1})
	if len(firstPage.Records) != 1 || firstPage.NextCursor == "" {
		t.Fatalf("expected a page of 1 with a non-empty cursor, got %+v", firstPage)
	}
}

func TestSecurityAuditJournalCorruptionRecovery(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audit.jsonl")
	journal, err := NewSecurityAuditJournal(path)
	if err != nil {
		t.Fatalf("NewSecurityAuditJournal: %v", err)
	}
	journal.Append(SecurityAuditRecord{Action: "SuspendWorker", Outcome: OutcomeAccepted})

	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatalf("open for corruption append: %v", err)
	}
	file.WriteString("not json\n")
	file.Close()

	reloaded, err := NewSecurityAuditJournal(path)
	if err != nil {
		t.Fatalf("reload after corruption: %v", err)
	}
	if reloaded.Size() != 1 {
		t.Fatalf("expected 1 valid record to survive corruption, got %d", reloaded.Size())
	}
	if reloaded.RecoveredLineCount() != 1 {
		t.Fatalf("expected 1 recovered line, got %d", reloaded.RecoveredLineCount())
	}
}

func TestSecurityAuditJournalRotationAndRetention(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "audit.jsonl")
	journal, err := NewSecurityAuditJournal(path)
	if err != nil {
		t.Fatalf("NewSecurityAuditJournal: %v", err)
	}
	journal.maxBytes = 200
	journal.maxRetained = 2
	for i := 0; i < 20; i++ {
		journal.Append(SecurityAuditRecord{Action: "Heartbeat", ResourceID: "worker", Outcome: OutcomeAccepted})
	}
	if _, err := os.Stat(path + ".1"); err != nil {
		t.Fatalf("expected rotation to produce a .1 file: %v", err)
	}
	if _, err := os.Stat(path + ".3"); err == nil {
		t.Fatalf("expected retention count of 2 to never keep a third generation")
	}
}
