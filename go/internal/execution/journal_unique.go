package execution

import (
	"bufio"
	"encoding/json"
	"errors"
	"os"
)

// AppendUnique appends event only when its EventID is not already present in
// the durable journal. The check and append share Journal's mutex, making the
// operation process-local atomic. This is used for backend events so the
// journal can be written before the backend resume cursor is persisted: after
// a crash, replaying the same backend event is harmless and the cursor can
// advance without duplicating the operator-visible event history.
func (j *Journal) AppendUnique(event Event) (bool, error) {
	if j == nil {
		return false, nil
	}
	if event.EventID == "" {
		return false, errors.New("execution journal event_id is required for unique append")
	}
	encoded, err := json.Marshal(event)
	if err != nil {
		return false, err
	}

	j.mu.Lock()
	defer j.mu.Unlock()

	file, err := os.Open(j.path)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return false, err
	}
	if err == nil {
		scanner := bufio.NewScanner(file)
		buffer := make([]byte, 64*1024)
		scanner.Buffer(buffer, 4*1024*1024)
		for scanner.Scan() {
			var existing Event
			if unmarshalErr := json.Unmarshal(scanner.Bytes(), &existing); unmarshalErr != nil {
				_ = file.Close()
				return false, unmarshalErr
			}
			if existing.EventID == event.EventID {
				_ = file.Close()
				return false, nil
			}
		}
		if scanErr := scanner.Err(); scanErr != nil {
			_ = file.Close()
			return false, scanErr
		}
		if closeErr := file.Close(); closeErr != nil {
			return false, closeErr
		}
	}

	appendFile, err := os.OpenFile(j.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		return false, err
	}
	if _, err := appendFile.Write(append(encoded, '\n')); err != nil {
		_ = appendFile.Close()
		return false, err
	}
	if err := appendFile.Sync(); err != nil {
		_ = appendFile.Close()
		return false, err
	}
	if err := appendFile.Close(); err != nil {
		return false, err
	}
	return true, nil
}
