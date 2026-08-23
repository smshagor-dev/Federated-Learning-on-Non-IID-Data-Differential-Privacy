package application

import (
	"context"
	"fmt"
	"strings"
	"time"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

type ExecutionService struct {
	repo        executiondomain.Repository
	drivers     executiondomain.DriverRegistry
	journal     *executiondomain.Journal
	experiments *ExperimentService
	clock       Clock
	audit       *AuditService
}

func NewExecutionService(
	repo executiondomain.Repository,
	drivers executiondomain.DriverRegistry,
	journal *executiondomain.Journal,
	experiments *ExperimentService,
	clock Clock,
	audit *AuditService,
) *ExecutionService {
	if clock == nil {
		clock = time.Now
	}
	if drivers == nil {
		drivers = executiondomain.DriverRegistry{}
	}
	return &ExecutionService{
		repo:        repo,
		drivers:     drivers,
		journal:     journal,
		experiments: experiments,
		clock:       clock,
		audit:       audit,
	}
}

func (s *ExecutionService) Create(ctx context.Context, experimentID string, spec executiondomain.Spec) (executiondomain.Record, error) {
	if s == nil || s.repo == nil {
		return executiondomain.Record{}, executiondomain.ErrBackendNotConfigured
	}
	if err := spec.Validate(); err != nil {
		return executiondomain.Record{}, err
	}
	if experimentID != "" && s.experiments != nil {
		if _, err := s.experiments.Get(ctx, experimentID); err != nil {
			return executiondomain.Record{}, err
		}
	}
	hash, err := spec.Hash()
	if err != nil {
		return executiondomain.Record{}, err
	}
	now := s.clock().UTC()
	record := executiondomain.Record{
		ID:           fmt.Sprintf("exec-%d", now.UnixNano()),
		ExperimentID: experimentID,
		Backend:      spec.Backend,
		Spec:         spec,
		SpecHash:     hash,
		Status:       executiondomain.StatusCreated,
		MaxRounds:    spec.Federation.Rounds,
		Revision:     1,
		CreatedAt:    now,
		UpdatedAt:    now,
	}
	created, err := s.repo.Create(ctx, record)
	if err != nil {
		return executiondomain.Record{}, err
	}
	s.appendEvent(created, "EXECUTION_CREATED", "", "", nil)
	if s.audit != nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "execution.create", "execution", created.ID, "success", map[string]any{
			"backend":   created.Backend,
			"spec_hash": created.SpecHash,
		})
	}
	return created, nil
}

func (s *ExecutionService) List(ctx context.Context) ([]executiondomain.Record, error) {
	if s == nil || s.repo == nil {
		return nil, executiondomain.ErrBackendNotConfigured
	}
	return s.repo.List(ctx)
}

func (s *ExecutionService) Get(ctx context.Context, id string) (executiondomain.Record, error) {
	if s == nil || s.repo == nil {
		return executiondomain.Record{}, executiondomain.ErrBackendNotConfigured
	}
	record, ok, err := s.repo.Get(ctx, id)
	if err != nil {
		return executiondomain.Record{}, err
	}
	if !ok {
		return executiondomain.Record{}, ErrNotFound
	}
	return record, nil
}

func (s *ExecutionService) Start(ctx context.Context, id, traceID string) (executiondomain.Record, error) {
	record, err := s.Get(ctx, id)
	if err != nil {
		return executiondomain.Record{}, err
	}
	if !executiondomain.CanRequestStart(record.Status) {
		return record, ErrInvalidTransition
	}
	driver, err := s.drivers.Require(record.Backend)
	if err != nil {
		return record, err
	}

	record, err = s.changeStatus(ctx, record, executiondomain.StatusStarting, "EXECUTION_STARTING", traceID, "")
	if err != nil {
		return executiondomain.Record{}, err
	}

	if record.BackendRunID == "" {
		snapshot, createErr := driver.Create(ctx, record.ID, record.Spec, traceID)
		if createErr != nil {
			return s.restoreAfterOperationFailure(ctx, record, executiondomain.StatusCreated, "EXECUTION_START_FAILED", traceID, createErr)
		}
		record = s.applySnapshot(record, snapshot)
		record.Status = executiondomain.StatusStarting
		record.LastError = ""
		record.UpdatedAt = s.clock().UTC()
		record, err = s.repo.Update(ctx, record, record.Revision)
		if err != nil {
			return executiondomain.Record{}, err
		}
		s.appendEvent(record, "BACKEND_RUN_CREATED", traceID, "", nil)
	}

	snapshot, startErr := driver.Start(ctx, record.BackendRunID, traceID)
	if startErr != nil {
		return s.restoreAfterOperationFailure(ctx, record, executiondomain.StatusCreated, "EXECUTION_START_FAILED", traceID, startErr)
	}
	record = s.applySnapshot(record, snapshot)
	if record.Status != executiondomain.StatusRunning && record.Status != executiondomain.StatusCompleted {
		return s.restoreAfterOperationFailure(ctx, record, executiondomain.StatusCreated, "EXECUTION_START_FAILED", traceID, fmt.Errorf("backend returned unexpected start state %s", record.Status))
	}
	now := s.clock().UTC()
	if record.StartedAt == nil {
		record.StartedAt = &now
	}
	record.LastError = ""
	record.UpdatedAt = now
	record, err = s.repo.Update(ctx, record, record.Revision)
	if err != nil {
		return executiondomain.Record{}, err
	}
	s.appendEvent(record, "EXECUTION_STARTED", traceID, "", nil)
	s.auditLifecycle(ctx, "execution.start", record)
	return record, nil
}

func (s *ExecutionService) Pause(ctx context.Context, id, reason, traceID string) (executiondomain.Record, error) {
	record, err := s.Get(ctx, id)
	if err != nil {
		return executiondomain.Record{}, err
	}
	if !executiondomain.CanRequestPause(record.Status) {
		return record, ErrInvalidTransition
	}
	driver, err := s.drivers.Require(record.Backend)
	if err != nil {
		return record, err
	}
	record, err = s.changeStatus(ctx, record, executiondomain.StatusPausing, "EXECUTION_PAUSING", traceID, reason)
	if err != nil {
		return executiondomain.Record{}, err
	}
	snapshot, operationErr := driver.Pause(ctx, record.BackendRunID, reason, traceID)
	if operationErr != nil {
		return s.restoreAfterOperationFailure(ctx, record, executiondomain.StatusRunning, "EXECUTION_PAUSE_FAILED", traceID, operationErr)
	}
	record = s.applySnapshot(record, snapshot)
	if record.Status != executiondomain.StatusPaused {
		return s.restoreAfterOperationFailure(ctx, record, executiondomain.StatusRunning, "EXECUTION_PAUSE_FAILED", traceID, fmt.Errorf("backend returned unexpected pause state %s", record.Status))
	}
	record.LastError = ""
	record.UpdatedAt = s.clock().UTC()
	record, err = s.repo.Update(ctx, record, record.Revision)
	if err != nil {
		return executiondomain.Record{}, err
	}
	s.appendEvent(record, "EXECUTION_PAUSED", traceID, reason, nil)
	s.auditLifecycle(ctx, "execution.pause", record)
	return record, nil
}

func (s *ExecutionService) Resume(ctx context.Context, id, traceID string) (executiondomain.Record, error) {
	record, err := s.Get(ctx, id)
	if err != nil {
		return executiondomain.Record{}, err
	}
	if !executiondomain.CanRequestResume(record.Status) {
		return record, ErrInvalidTransition
	}
	driver, err := s.drivers.Require(record.Backend)
	if err != nil {
		return record, err
	}
	record, err = s.changeStatus(ctx, record, executiondomain.StatusResuming, "EXECUTION_RESUMING", traceID, "")
	if err != nil {
		return executiondomain.Record{}, err
	}
	snapshot, operationErr := driver.Resume(ctx, record.BackendRunID, traceID)
	if operationErr != nil {
		return s.restoreAfterOperationFailure(ctx, record, executiondomain.StatusPaused, "EXECUTION_RESUME_FAILED", traceID, operationErr)
	}
	record = s.applySnapshot(record, snapshot)
	if record.Status != executiondomain.StatusRunning && record.Status != executiondomain.StatusCompleted {
		return s.restoreAfterOperationFailure(ctx, record, executiondomain.StatusPaused, "EXECUTION_RESUME_FAILED", traceID, fmt.Errorf("backend returned unexpected resume state %s", record.Status))
	}
	record.LastError = ""
	record.UpdatedAt = s.clock().UTC()
	record, err = s.repo.Update(ctx, record, record.Revision)
	if err != nil {
		return executiondomain.Record{}, err
	}
	s.appendEvent(record, "EXECUTION_RESUMED", traceID, "", nil)
	s.auditLifecycle(ctx, "execution.resume", record)
	return record, nil
}

func (s *ExecutionService) Cancel(ctx context.Context, id, reason, traceID string) (executiondomain.Record, error) {
	record, err := s.Get(ctx, id)
	if err != nil {
		return executiondomain.Record{}, err
	}
	if !executiondomain.CanRequestCancel(record.Status) {
		return record, ErrInvalidTransition
	}
	driver, err := s.drivers.Require(record.Backend)
	if err != nil {
		return record, err
	}
	previous := record.Status
	record, err = s.changeStatus(ctx, record, executiondomain.StatusCanceling, "EXECUTION_CANCELING", traceID, reason)
	if err != nil {
		return executiondomain.Record{}, err
	}
	if record.BackendRunID == "" {
		record.Status = executiondomain.StatusCanceled
		now := s.clock().UTC()
		record.CompletedAt = &now
		record.UpdatedAt = now
		record, err = s.repo.Update(ctx, record, record.Revision)
		if err != nil {
			return executiondomain.Record{}, err
		}
		s.appendEvent(record, "EXECUTION_CANCELED", traceID, reason, nil)
		return record, nil
	}
	snapshot, operationErr := driver.Cancel(ctx, record.BackendRunID, reason, traceID)
	if operationErr != nil {
		return s.restoreAfterOperationFailure(ctx, record, previous, "EXECUTION_CANCEL_FAILED", traceID, operationErr)
	}
	record = s.applySnapshot(record, snapshot)
	if record.Status != executiondomain.StatusCanceled {
		return s.restoreAfterOperationFailure(ctx, record, previous, "EXECUTION_CANCEL_FAILED", traceID, fmt.Errorf("backend returned unexpected cancel state %s", record.Status))
	}
	now := s.clock().UTC()
	record.CompletedAt = &now
	record.LastError = ""
	record.UpdatedAt = now
	record, err = s.repo.Update(ctx, record, record.Revision)
	if err != nil {
		return executiondomain.Record{}, err
	}
	s.appendEvent(record, "EXECUTION_CANCELED", traceID, reason, nil)
	s.auditLifecycle(ctx, "execution.cancel", record)
	return record, nil
}

func (s *ExecutionService) Reconcile(ctx context.Context, id string) (executiondomain.Record, error) {
	record, err := s.Get(ctx, id)
	if err != nil {
		return executiondomain.Record{}, err
	}
	if record.BackendRunID == "" || record.Terminal() {
		return record, nil
	}
	driver, err := s.drivers.Require(record.Backend)
	if err != nil {
		return record, err
	}
	snapshot, err := driver.Get(ctx, record.BackendRunID)
	if err != nil {
		return record, err
	}
	beforeStatus := record.Status
	beforeRound := record.CurrentRound
	record = s.applySnapshot(record, snapshot)
	now := s.clock().UTC()
	record.UpdatedAt = now
	if record.Terminal() && record.CompletedAt == nil {
		record.CompletedAt = &now
	}
	if record.Status == beforeStatus && record.CurrentRound == beforeRound && record.ModelVersion == snapshot.ModelVersion && record.RegisteredWorkers == snapshot.RegisteredWorkers && record.HealthyWorkers == snapshot.HealthyWorkers {
		return record, nil
	}
	record, err = s.repo.Update(ctx, record, record.Revision)
	if err != nil {
		return executiondomain.Record{}, err
	}
	s.appendEvent(record, "EXECUTION_RECONCILED", "", "", map[string]string{
		"previous_status": string(beforeStatus),
		"previous_round":  fmt.Sprintf("%d", beforeRound),
	})
	return record, nil
}

func (s *ExecutionService) Events(_ context.Context, id string, limit int) ([]executiondomain.Event, error) {
	if s == nil || s.journal == nil {
		return nil, nil
	}
	return s.journal.List(id, limit)
}

func (s *ExecutionService) Workers(ctx context.Context, backend executiondomain.Backend) ([]executiondomain.Worker, error) {
	driver, err := s.drivers.Require(backend)
	if err != nil {
		return nil, err
	}
	return driver.ListWorkers(ctx)
}

func (s *ExecutionService) changeStatus(ctx context.Context, record executiondomain.Record, status executiondomain.Status, eventType, traceID, reason string) (executiondomain.Record, error) {
	record.Status = status
	record.LastError = ""
	record.UpdatedAt = s.clock().UTC()
	updated, err := s.repo.Update(ctx, record, record.Revision)
	if err != nil {
		return executiondomain.Record{}, err
	}
	s.appendEvent(updated, eventType, traceID, reason, nil)
	return updated, nil
}

func (s *ExecutionService) restoreAfterOperationFailure(ctx context.Context, record executiondomain.Record, restore executiondomain.Status, eventType, traceID string, operationErr error) (executiondomain.Record, error) {
	record.Status = restore
	record.LastError = operationErr.Error()
	record.UpdatedAt = s.clock().UTC()
	updated, persistErr := s.repo.Update(ctx, record, record.Revision)
	if persistErr != nil {
		return executiondomain.Record{}, fmt.Errorf("backend operation failed (%v) and state restore failed: %w", operationErr, persistErr)
	}
	s.appendEvent(updated, eventType, traceID, operationErr.Error(), nil)
	return updated, operationErr
}

func (s *ExecutionService) applySnapshot(record executiondomain.Record, snapshot executiondomain.Snapshot) executiondomain.Record {
	if snapshot.BackendRunID != "" {
		record.BackendRunID = snapshot.BackendRunID
	}
	record.Status = snapshot.Status
	record.CurrentRound = snapshot.CurrentRound
	if snapshot.MaxRounds != 0 {
		record.MaxRounds = snapshot.MaxRounds
	}
	if snapshot.ModelVersion != "" {
		record.ModelVersion = snapshot.ModelVersion
	}
	record.RegisteredWorkers = snapshot.RegisteredWorkers
	record.HealthyWorkers = snapshot.HealthyWorkers
	return record
}

func (s *ExecutionService) appendEvent(record executiondomain.Record, eventType, traceID, reason string, metadata map[string]string) {
	if s == nil || s.journal == nil {
		return
	}
	eventID := fmt.Sprintf("%s-r%d-%s", record.ID, record.Revision, strings.ToLower(eventType))
	_ = s.journal.Append(executiondomain.Event{
		EventID:      eventID,
		ExecutionID:  record.ID,
		Type:         eventType,
		Status:       record.Status,
		Round:        record.CurrentRound,
		BackendRunID: record.BackendRunID,
		Reason:       reason,
		TraceID:      traceID,
		Metadata:     metadata,
		Timestamp:    s.clock().UTC(),
	})
}

func (s *ExecutionService) auditLifecycle(ctx context.Context, action string, record executiondomain.Record) {
	if s == nil || s.audit == nil {
		return
	}
	_ = s.audit.Record(ctx, actorFromContext(ctx), action, "execution", record.ID, "success", map[string]any{
		"backend":        record.Backend,
		"backend_run_id": record.BackendRunID,
		"status":         record.Status,
		"round":          record.CurrentRound,
	})
}
