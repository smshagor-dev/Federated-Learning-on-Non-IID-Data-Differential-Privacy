package execution

import (
	"bufio"
	"encoding/json"
	"errors"
	"os"
)

// AppendUnique appends event only when its EventID is not already present in
// the durable journal.
func (j *Journal) AppendUnique(event Event) (bool, error) {
	count, err := j.AppendUniqueBatch([]Event{event})
	return count == 1, err
}

// AppendUniqueBatch appends only event IDs not already present in the durable
// journal. The existing journal is scanned once and newly accepted events are
// written under the same mutex with one fsync. Backend event ingestion uses
// this so a large coordinator batch does not rescan the complete JSONL file for
// every individual event.
//
// Journal insertion deliberately precedes backend-cursor persistence. If the
// process stops between those operations, replayed events are skipped here and
// the cursor can advance later without duplicating operator-visible history.
func (j *Journal) AppendUniqueBatch(events []Event) (int, error) {
	if j == nil || len(events) == 0 {
		return 0, nil
	}
	for _, event := range events {
		if event.EventID == "" {
			return 0, errors.New("execution journal event_id is required for unique append")
		}
	}

	j.mu.Lock()
	defer j.mu.Unlock()

	seen := make(map[string]struct{})
	file, err := os.Open(j.path)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return 0, err
	}
	if err == nil {
		scanner := bufio.NewScanner(file)
		buffer := make([]byte, 64*1024)
		scanner.Buffer(buffer, 4*1024*1024)
		for scanner.Scan() {
			var existing Event
			if unmarshalErr := json.Unmarshal(scanner.Bytes(), &existing); unmarshalErr != nil {
				_ = file.Close()
				return 0, unmarshalErr
			}
			if existing.EventID != "" {
				seen[existing.EventID] = struct{}{}
			}
		}
		if scanErr := scanner.Err(); scanErr != nil {
			_ = file.Close()
			return 0, scanErr
		}
		if closeErr := file.Close(); closeErr != nil {
			return 0, closeErr
		}
	}

	type encodedEvent struct {
		id   string
		data []byte
	}
	pending := make([]encodedEvent, 0, len(events))
	for _, event := range events {
		if _, exists := seen[event.EventID]; exists {
			continue
		}
		encoded, marshalErr := json.Marshal(event)
		if marshalErr != nil {
			return 0, marshalErr
		}
		pending = append(pending, encodedEvent{id: event.EventID, data: encoded})
		seen[event.EventID] = struct{}{}
	}
	if len(pending) == 0 {
		return 0, nil
	}

	appendFile, err := os.OpenFile(j.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return 0, err
	}
	for _, event := range pending {
		if _, writeErr := appendFile.Write(append(event.data, '\n')); writeErr != nil {
			_ = appendFile.Close()
			return 0, writeErr
		}
	}
	if err := appendFile.Sync(); err != nil {
		_ = appendFile.Close()
		return 0, err
	}
	if err := appendFile.Close(); err != nil {
		return 0, err
	}
	return len(pending), nil
}
