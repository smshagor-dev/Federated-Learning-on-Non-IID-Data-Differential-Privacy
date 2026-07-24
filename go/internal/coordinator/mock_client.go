package coordinator

import (
	"context"
	"fmt"
	"sync"
)

// MockClient is a real, deterministic in-memory stand-in for a live
// coordinator, used by Go-side tests that need to exercise the
// application service layer's coordinator-calling code paths without a
// running C++ (or CLI-bridge) coordinator process. It implements the
// same idempotency/rejection rules the real coordinator does (see
// cpp/coordinator/src/run_manager.cpp) at a level of detail sufficient
// for Go-side HTTP-to-gRPC mapping and error-mapping tests — it does not
// re-implement aggregation, checkpointing, or SCAFFOLD state.
type MockClient struct {
	mu       sync.Mutex
	healthy  bool
	runs     map[string]*RunSnapshot
	events   map[string][]Event
	failNext error
}

func NewMockClient() *MockClient {
	return &MockClient{
		healthy: true,
		runs:    make(map[string]*RunSnapshot),
		events:  make(map[string][]Event),
	}
}

// SetUnavailable makes the next call fail with ErrUnavailable, for
// testing "coordinator unavailable" handling paths. Automatically resets
// after one call.
func (m *MockClient) SetUnavailable() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.failNext = ErrUnavailable
}

func (m *MockClient) consumeFailure() error {
	if m.failNext != nil {
		err := m.failNext
		m.failNext = nil
		return err
	}
	return nil
}

func (m *MockClient) Health(_ context.Context) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return "", err
	}
	if !m.healthy {
		return "", ErrUnavailable
	}
	return "ok", nil
}

func (m *MockClient) CreateRun(_ context.Context, request CreateRunRequest) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	if _, exists := m.runs[request.RunID]; exists {
		return RunSnapshot{}, &RejectedError{Reason: fmt.Sprintf("duplicate run_id: %s", request.RunID)}
	}
	snapshot := &RunSnapshot{
		RunID:        request.RunID,
		State:        "CREATED",
		MaxRounds:    request.MaxRounds,
		Algorithm:    request.Algorithm,
		ModelVersion: "v0",
	}
	m.runs[request.RunID] = snapshot
	m.publish(request.RunID, "RUN_CREATED")
	return *snapshot, nil
}

func (m *MockClient) get(runID string) (*RunSnapshot, error) {
	run, ok := m.runs[runID]
	if !ok {
		return nil, ErrRunNotFound
	}
	return run, nil
}

func (m *MockClient) StartRun(_ context.Context, runID, _ string) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	run, err := m.get(runID)
	if err != nil {
		return RunSnapshot{}, err
	}
	switch run.State {
	case "RUNNING", "WAITING_FOR_CLIENTS":
		return *run, nil // idempotent
	case "COMPLETED", "FAILED", "CANCELED", "PAUSED":
		return RunSnapshot{}, &RejectedError{Reason: fmt.Sprintf("cannot start a run in state %s", run.State)}
	}
	run.State = "RUNNING"
	m.publish(runID, "RUN_STARTED")
	return *run, nil
}

func (m *MockClient) PauseRun(_ context.Context, runID, _, _ string) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	run, err := m.get(runID)
	if err != nil {
		return RunSnapshot{}, err
	}
	if run.State == "PAUSED" {
		return *run, nil // idempotent
	}
	if run.State != "RUNNING" && run.State != "WAITING_FOR_CLIENTS" {
		return RunSnapshot{}, &RejectedError{Reason: fmt.Sprintf("cannot pause a run in state %s", run.State)}
	}
	run.State = "PAUSED"
	m.publish(runID, "RUN_PAUSED")
	return *run, nil
}

func (m *MockClient) ResumeRun(_ context.Context, runID, _ string) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	run, err := m.get(runID)
	if err != nil {
		return RunSnapshot{}, err
	}
	if run.State == "RUNNING" {
		return *run, nil // idempotent
	}
	if run.State != "PAUSED" {
		return RunSnapshot{}, &RejectedError{Reason: fmt.Sprintf("cannot resume a run in state %s", run.State)}
	}
	run.State = "RUNNING"
	m.publish(runID, "RUN_RESUMED")
	return *run, nil
}

func (m *MockClient) CancelRun(_ context.Context, runID, _, _ string) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	run, err := m.get(runID)
	if err != nil {
		return RunSnapshot{}, err
	}
	if run.State == "CANCELED" {
		return *run, nil // idempotent
	}
	if run.State == "COMPLETED" || run.State == "FAILED" {
		return RunSnapshot{}, &RejectedError{Reason: fmt.Sprintf("cannot cancel a run in terminal state %s", run.State)}
	}
	run.State = "CANCELED"
	m.publish(runID, "RUN_CANCELED")
	return *run, nil
}

func (m *MockClient) GetRun(_ context.Context, runID string) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	run, err := m.get(runID)
	if err != nil {
		return RunSnapshot{}, err
	}
	return *run, nil
}

func (m *MockClient) PollEvents(_ context.Context, runID, afterEventID string) ([]Event, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return nil, err
	}
	all := m.events[runID]
	if afterEventID == "" {
		return append([]Event(nil), all...), nil
	}
	for index, event := range all {
		if event.EventID == afterEventID {
			return append([]Event(nil), all[index+1:]...), nil
		}
	}
	return append([]Event(nil), all...), nil
}

func (m *MockClient) publish(runID, eventType string) {
	sequence := len(m.events[runID]) + 1
	m.events[runID] = append(m.events[runID], Event{
		EventID: fmt.Sprintf("%s:%d", runID, sequence),
		RunID:   runID,
		Type:    eventType,
	})
}
