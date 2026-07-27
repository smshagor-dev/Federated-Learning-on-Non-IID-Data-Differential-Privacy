package observability

// Durable, security-specific audit journal -- Security Events, Metrics,
// and Durable Audit Journal slice, requirements 8/9 ("a durable
// security-specific audit journal", "keep security events and audit
// records conceptually separate"). See docs/security-audit-journal.md.
//
// Additive, not a replacement for AuditRepository (audit.go/
// repository.go): every security HTTP mutation keeps writing to that
// general-purpose, pre-existing repository unchanged (zero regression
// risk to that path) and *additionally* writes a richer record here.
// GET /api/v1/security/audit reads from this journal (real pagination +
// filtering); AuditRepository keeps serving every other domain
// untouched. Same JSONL/rotation/skip-and-recover persistence shape as
// SecurityEventJournal -- see that file's comment for the corruption
// policy rationale.

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"sync"
)

// SecurityAuditRecord is the durable, security-specific audit shape --
// actor + action + resource + outcome, distinct from SecurityEvent (see
// this file's package comment). Mirrors
// cpp/coordinator/include/fl_coordinator/security_audit_journal.hpp's
// SecurityAuditRecord field-for-field.
type SecurityAuditRecord struct {
	SchemaVersion   int               `json:"schema_version"`
	RecordID        string            `json:"record_id"`
	Timestamp       string            `json:"timestamp"`
	SafeActorID     string            `json:"safe_actor_id"`
	ActorRole       string            `json:"actor_role"`
	Action          string            `json:"action"`
	ResourceType    string            `json:"resource_type"`
	ResourceID      string            `json:"resource_id"`
	Outcome         string            `json:"outcome"`
	Reason          string            `json:"reason"`
	RequestID       string            `json:"request_id"`
	TraceID         string            `json:"trace_id"`
	SafeDetails     map[string]string `json:"safe_details"`
	PayloadChecksum string            `json:"payload_checksum"`
}

// SecurityAuditJournal persists SecurityAuditRecord entries. Safe for
// concurrent use.
type SecurityAuditJournal struct {
	mu             sync.Mutex
	path           string
	maxBytes       int64
	maxRetained    int
	nextSequence   uint64
	recoveredLines int
	rotations      int
	records        []SecurityAuditRecord
}

func NewSecurityAuditJournal(path string) (*SecurityAuditJournal, error) {
	journal := &SecurityAuditJournal{
		path:         path,
		maxBytes:     defaultSecurityJournalMaxBytes,
		maxRetained:  defaultSecurityJournalMaxRetained,
		nextSequence: 1,
	}
	if err := journal.load(); err != nil {
		return nil, err
	}
	return journal, nil
}

func (j *SecurityAuditJournal) load() error {
	j.records = nil
	j.recoveredLines = 0
	j.nextSequence = 1
	if _, statErr := os.Stat(j.path + ".1"); statErr == nil {
		j.rotations = 1
	} else {
		j.rotations = 0
	}
	file, err := os.Open(j.path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("failed to open security audit journal %s: %w", j.path, err)
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			continue
		}
		record, ok := parseSecurityAuditLine(line)
		if !ok {
			j.recoveredLines++
			continue
		}
		j.records = append(j.records, record)
		if sequence, err := strconv.ParseUint(record.RecordID, 10, 64); err == nil && sequence+1 > j.nextSequence {
			j.nextSequence = sequence + 1
		}
	}
	return scanner.Err()
}

func parseSecurityAuditLine(line string) (SecurityAuditRecord, bool) {
	var record SecurityAuditRecord
	if err := json.Unmarshal([]byte(line), &record); err != nil {
		return SecurityAuditRecord{}, false
	}
	if record.RecordID == "" || record.Timestamp == "" || record.Action == "" {
		return SecurityAuditRecord{}, false
	}
	if computeSecurityAuditChecksum(record) != record.PayloadChecksum {
		return SecurityAuditRecord{}, false
	}
	return record, true
}

func canonicalSecurityAuditPayload(record SecurityAuditRecord) []byte {
	safeDetails := record.SafeDetails
	if safeDetails == nil {
		safeDetails = map[string]string{}
	}
	payload := map[string]any{
		"action":         record.Action,
		"actor_role":     record.ActorRole,
		"outcome":        record.Outcome,
		"reason":         record.Reason,
		"request_id":     record.RequestID,
		"resource_id":    record.ResourceID,
		"resource_type":  record.ResourceType,
		"safe_actor_id":  record.SafeActorID,
		"safe_details":   safeDetails,
		"schema_version": record.SchemaVersion,
		"trace_id":       record.TraceID,
	}
	return canonicalJSON(payload)
}

func computeSecurityAuditChecksum(record SecurityAuditRecord) string {
	return fnv1aHex(canonicalSecurityAuditPayload(record))
}

func (j *SecurityAuditJournal) nextRecordID() string {
	id := fmt.Sprintf("%020d", j.nextSequence)
	j.nextSequence++
	return id
}

// Append fills RecordID/Timestamp/PayloadChecksum if not already set
// and appends. Returns false (with a reason) rather than an error for
// an empty Action -- audit-logging failure must never block the
// mutation it is recording.
func (j *SecurityAuditJournal) Append(record SecurityAuditRecord) (bool, string) {
	j.mu.Lock()
	defer j.mu.Unlock()

	if record.Timestamp == "" {
		record.Timestamp = nowISO8601()
	}
	if record.RecordID == "" {
		record.RecordID = j.nextRecordID()
	}
	if record.Action == "" {
		return false, "action is required"
	}
	record.PayloadChecksum = computeSecurityAuditChecksum(record)

	if err := j.maybeRotate(); err != nil {
		return false, err.Error()
	}
	line, err := json.Marshal(record)
	if err != nil {
		return false, err.Error()
	}
	if err := j.appendLine(line); err != nil {
		return false, err.Error()
	}
	j.records = append(j.records, record)
	return true, "ok"
}

func (j *SecurityAuditJournal) appendLine(line []byte) error {
	if dir := filepath.Dir(j.path); dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return err
		}
	}
	file, err := os.OpenFile(j.path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = file.Write(append(line, '\n'))
	return err
}

func (j *SecurityAuditJournal) maybeRotate() error {
	info, err := os.Stat(j.path)
	if err != nil {
		return nil
	}
	if info.Size() < j.maxBytes {
		return nil
	}
	for generation := j.maxRetained; generation >= 1; generation-- {
		var source string
		if generation == 1 {
			source = j.path
		} else {
			source = fmt.Sprintf("%s.%d", j.path, generation-1)
		}
		destination := fmt.Sprintf("%s.%d", j.path, generation)
		if generation == j.maxRetained {
			_ = os.Remove(destination)
		}
		if _, statErr := os.Stat(source); statErr == nil {
			if err := os.Rename(source, destination); err != nil {
				return err
			}
		}
	}
	j.records = nil
	j.rotations++
	return nil
}

// SecurityAuditListFilters implements requirement 10 ("audit pagination
// and filtering") for real -- cursor + limit + exact-match filters on
// actor/action/resource_type/outcome + a time-range window.
type SecurityAuditListFilters struct {
	AfterRecordID string
	Limit         int
	ActorID       string
	Action        string
	ResourceType  string
	Outcome       string
	SinceUnixS    float64 // 0 == no lower bound
	UntilUnixS    float64 // 0 == no upper bound
}

type SecurityAuditListResult struct {
	Records    []SecurityAuditRecord
	NextCursor string
}

func (j *SecurityAuditJournal) List(filters SecurityAuditListFilters) SecurityAuditListResult {
	j.mu.Lock()
	defer j.mu.Unlock()

	limit := filters.Limit
	if limit <= 0 {
		limit = 100
	}
	var sinceTS, untilTS string
	if filters.SinceUnixS > 0 {
		sinceTS = unixSecondsToISO8601(filters.SinceUnixS)
	}
	if filters.UntilUnixS > 0 {
		untilTS = unixSecondsToISO8601(filters.UntilUnixS)
	}

	result := SecurityAuditListResult{Records: []SecurityAuditRecord{}}
	pastCursor := filters.AfterRecordID == ""
	for _, record := range j.records {
		if !pastCursor {
			if record.RecordID == filters.AfterRecordID {
				pastCursor = true
			}
			continue
		}
		if filters.ActorID != "" && record.SafeActorID != filters.ActorID {
			continue
		}
		if filters.Action != "" && record.Action != filters.Action {
			continue
		}
		if filters.ResourceType != "" && record.ResourceType != filters.ResourceType {
			continue
		}
		if filters.Outcome != "" && record.Outcome != filters.Outcome {
			continue
		}
		if sinceTS != "" && record.Timestamp < sinceTS {
			continue
		}
		if untilTS != "" && record.Timestamp > untilTS {
			continue
		}
		if len(result.Records) >= limit {
			result.NextCursor = result.Records[len(result.Records)-1].RecordID
			return result
		}
		result.Records = append(result.Records, record)
	}
	return result
}

func (j *SecurityAuditJournal) RecoveredLineCount() int {
	j.mu.Lock()
	defer j.mu.Unlock()
	return j.recoveredLines
}

func (j *SecurityAuditJournal) Size() int {
	j.mu.Lock()
	defer j.mu.Unlock()
	return len(j.records)
}

// LastRecordTimestamp returns the most recently appended record's
// timestamp, or "" if the journal is empty.
func (j *SecurityAuditJournal) LastRecordTimestamp() string {
	j.mu.Lock()
	defer j.mu.Unlock()
	if len(j.records) == 0 {
		return ""
	}
	return j.records[len(j.records)-1].Timestamp
}

// HasRotated reports whether this journal has ever rotated -- see
// SecurityEventJournal.HasRotated's identical doc comment.
func (j *SecurityAuditJournal) HasRotated() bool {
	j.mu.Lock()
	defer j.mu.Unlock()
	return j.rotations > 0
}
