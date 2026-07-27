package observability

// Durable, append-only, cross-language-readable security-event journal
// for Go-originated events -- Security Events, Metrics, and Durable
// Audit Journal slice, Work Package D/E. See docs/security-events.md.
//
// Mirrors cpp/coordinator/include/fl_coordinator/security_event_journal.hpp's
// design: JSON Lines, one record per line, size-based rotation,
// skip-and-recover corruption policy (a malformed or checksum-failing
// line is dropped and counted, not fatal). Records Go-layer-only events
// (permission denials, idempotency replay/conflict, mutation accepted/
// rejected, audit access) that never reach the C++ coordinator --
// coordinator-originated events are fetched separately via
// SecurityClient.ListSecurityEvents and merged by the HTTP handler, not
// stored a second time here.

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"time"
)

const (
	defaultSecurityJournalMaxBytes    = 10 * 1024 * 1024
	defaultSecurityJournalMaxRetained = 5
)

// SecurityEventJournal persists Go-originated SecurityEvent records.
// Safe for concurrent use.
type SecurityEventJournal struct {
	mu             sync.Mutex
	path           string
	maxBytes       int64
	maxRetained    int
	nextSequence   uint64
	recoveredLines int
	rotations      int
	events         []SecurityEvent
}

// NewSecurityEventJournal loads path (if it exists) and returns a ready
// journal. A read/open failure at the filesystem level returns an
// error; a corrupt line within the file is skipped and counted, not
// treated as fatal (see the package doc comment).
func NewSecurityEventJournal(path string) (*SecurityEventJournal, error) {
	journal := &SecurityEventJournal{
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

func (j *SecurityEventJournal) load() error {
	j.events = nil
	j.recoveredLines = 0
	j.nextSequence = 1
	// A rotated .1 file surviving a restart is itself evidence rotation
	// has happened at least once for this journal -- cheaper and more
	// honest than trying to reconstruct an exact historical count.
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
		return fmt.Errorf("failed to open security event journal %s: %w", j.path, err)
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for scanner.Scan() {
		line := scanner.Text()
		if line == "" {
			continue
		}
		event, ok := parseSecurityEventLine(line)
		if !ok {
			j.recoveredLines++
			continue
		}
		j.events = append(j.events, event)
		if sequence, err := strconv.ParseUint(event.EventID, 10, 64); err == nil && sequence+1 > j.nextSequence {
			j.nextSequence = sequence + 1
		}
	}
	return scanner.Err()
}

func parseSecurityEventLine(line string) (SecurityEvent, bool) {
	var event SecurityEvent
	if err := json.Unmarshal([]byte(line), &event); err != nil {
		return SecurityEvent{}, false
	}
	if event.EventID == "" || event.Timestamp == "" {
		return SecurityEvent{}, false
	}
	if computeSecurityEventChecksum(event) != event.PayloadChecksum {
		return SecurityEvent{}, false
	}
	return event, true
}

// canonicalSecurityEventPayload mirrors canonical_security_event_payload_json
// (C++)/canonical_security_event_payload_json (Python): every field
// except event_id/timestamp/payload_checksum, key-sorted, compact JSON.
func canonicalSecurityEventPayload(event SecurityEvent) []byte {
	safeDetails := event.SafeDetails
	if safeDetails == nil {
		safeDetails = map[string]string{}
	}
	payload := map[string]any{
		"actor_type":          event.ActorType,
		"event_type":          event.EventType,
		"outcome":             event.Outcome,
		"reason_code":         event.ReasonCode,
		"request_id":          event.RequestID,
		"round_id":            event.RoundID,
		"run_id":              event.RunID,
		"safe_actor_id":       event.SafeActorID,
		"safe_details":        safeDetails,
		"safe_signing_key_id": event.SafeSigningKeyID,
		"safe_subject_id":     event.SafeSubjectID,
		"schema_version":      event.SchemaVersion,
		"severity":            event.Severity,
		"source_component":    event.SourceComponent,
		"source_service":      event.SourceService,
		"subject_type":        event.SubjectType,
		"task_id":             event.TaskID,
		"trace_id":            event.TraceID,
		"worker_id":           event.WorkerID,
	}
	return canonicalJSON(payload)
}

// canonicalJSON produces deterministic, key-sorted compact JSON for a
// flat map[string]any (values are strings, ints, uints, or a nested
// map[string]string) -- Go's encoding/json already sorts map[string]T
// keys automatically when marshaling, so this is a thin, explicit
// wrapper documenting that reliance rather than a hand-rolled encoder.
func canonicalJSON(payload map[string]any) []byte {
	blob, err := json.Marshal(payload)
	if err != nil {
		// payload is always built from this package's own known-safe
		// types (strings/ints/map[string]string) -- a marshal failure
		// here would indicate a programming error, not bad input.
		panic(fmt.Sprintf("canonicalJSON: unexpected marshal failure: %v", err))
	}
	return blob
}

// computeSecurityEventChecksum: FNV-1a 64-bit hex digest of the
// canonical payload -- same algorithm/constants as every other checksum
// in this codebase's persistence layer. Corruption/tamper-in-transit
// detection only, not a cryptographic MAC.
func computeSecurityEventChecksum(event SecurityEvent) string {
	return fnv1aHex(canonicalSecurityEventPayload(event))
}

func fnv1aHex(data []byte) string {
	var hash uint64 = 1469598103934665603
	for _, b := range data {
		hash ^= uint64(b)
		hash *= 1099511628211
	}
	return fmt.Sprintf("%016x", hash)
}

func nowISO8601() string {
	return time.Now().UTC().Format("2006-01-02T15:04:05Z")
}

// unixSecondsToISO8601 formats a Unix-seconds timestamp the same way
// nowISO8601 does, so a since/until filter bound can be compared
// lexicographically against a record's Timestamp string.
func unixSecondsToISO8601(unixSeconds float64) string {
	return time.Unix(int64(unixSeconds), 0).UTC().Format("2006-01-02T15:04:05Z")
}

func (j *SecurityEventJournal) nextEventID() string {
	id := fmt.Sprintf("%020d", j.nextSequence)
	j.nextSequence++
	return id
}

// Emit fills EventID/Timestamp/PayloadChecksum if not already set,
// validates, and appends. Returns an error only for a genuine
// filesystem failure -- an invalid event is dropped (caller should log
// event.EventType + the returned reason), matching the C++/Python
// SecurityEventSink contract of never blocking the caller's actual
// security decision.
func (j *SecurityEventJournal) Emit(event SecurityEvent) (bool, string) {
	j.mu.Lock()
	defer j.mu.Unlock()

	if event.Timestamp == "" {
		event.Timestamp = nowISO8601()
	}
	if event.EventID == "" {
		event.EventID = j.nextEventID()
	}
	if ok, reason := ValidateSecurityEvent(event); !ok {
		return false, reason
	}
	event.PayloadChecksum = computeSecurityEventChecksum(event)

	if err := j.maybeRotate(); err != nil {
		return false, err.Error()
	}
	line, err := json.Marshal(event)
	if err != nil {
		return false, err.Error()
	}
	if err := j.appendLine(line); err != nil {
		return false, err.Error()
	}
	j.events = append(j.events, event)
	return true, "ok"
}

func (j *SecurityEventJournal) appendLine(line []byte) error {
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

func (j *SecurityEventJournal) maybeRotate() error {
	info, err := os.Stat(j.path)
	if err != nil {
		return nil // does not exist yet -- nothing to rotate
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
	j.events = nil
	j.rotations++
	return nil
}

// SecurityEventListFilters mirrors the C++/Python journal's ListFilters.
type SecurityEventListFilters struct {
	AfterEventID string
	Limit        int
	MinSeverity  string
	SubjectType  string
	EventType    string
}

// SecurityEventListResult mirrors the C++/Python journal's ListResult.
type SecurityEventListResult struct {
	Events     []SecurityEvent
	NextCursor string
}

var severityRank = map[string]int{
	SeverityInfo: 0, SeverityWarning: 1, SeverityHigh: 2, SeverityCritical: 3,
}

// List serves only this journal's in-memory (currently-active, not yet
// rotated) records -- same scope limitation as the C++/Python journals.
func (j *SecurityEventJournal) List(filters SecurityEventListFilters) SecurityEventListResult {
	j.mu.Lock()
	defer j.mu.Unlock()

	limit := filters.Limit
	if limit <= 0 {
		limit = 100
	}
	minRank := -1
	if filters.MinSeverity != "" {
		minRank = severityRank[filters.MinSeverity]
	}

	result := SecurityEventListResult{Events: []SecurityEvent{}}
	pastCursor := filters.AfterEventID == ""
	for _, event := range j.events {
		if !pastCursor {
			if event.EventID == filters.AfterEventID {
				pastCursor = true
			}
			continue
		}
		if minRank >= 0 && severityRank[event.Severity] < minRank {
			continue
		}
		if filters.SubjectType != "" && event.SubjectType != filters.SubjectType {
			continue
		}
		if filters.EventType != "" && event.EventType != filters.EventType {
			continue
		}
		if len(result.Events) >= limit {
			result.NextCursor = result.Events[len(result.Events)-1].EventID
			return result
		}
		result.Events = append(result.Events, event)
	}
	return result
}

func (j *SecurityEventJournal) RecoveredLineCount() int {
	j.mu.Lock()
	defer j.mu.Unlock()
	return j.recoveredLines
}

func (j *SecurityEventJournal) Size() int {
	j.mu.Lock()
	defer j.mu.Unlock()
	return len(j.events)
}

// LastRecordTimestamp returns the most recently appended event's
// timestamp, or "" if the journal is empty -- used by the security
// overview endpoint to report journal health/lag (Work Package B/C).
func (j *SecurityEventJournal) LastRecordTimestamp() string {
	j.mu.Lock()
	defer j.mu.Unlock()
	if len(j.events) == 0 {
		return ""
	}
	return j.events[len(j.events)-1].Timestamp
}

// HasRotated reports whether this journal has ever rotated (in this
// process's lifetime, or evidenced by a surviving .1 file across a
// restart) -- a coarse "retention is active" signal for the overview
// endpoint, not an exact historical rotation count.
func (j *SecurityEventJournal) HasRotated() bool {
	j.mu.Lock()
	defer j.mu.Unlock()
	return j.rotations > 0
}

// sortedEventsByID is a small helper for merging Go-local and
// coordinator-relayed events (used by handleSecurityEvents) --
// event_id is a zero-padded decimal string, so lexicographic sort
// matches numeric order.
func sortedEventsByID(events []SecurityEvent) []SecurityEvent {
	sorted := make([]SecurityEvent, len(events))
	copy(sorted, events)
	sort.Slice(sorted, func(i, k int) bool {
		return sorted[i].EventID < sorted[k].EventID
	})
	return sorted
}
