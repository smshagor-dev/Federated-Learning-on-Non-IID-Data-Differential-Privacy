# Experiment Command Existing Mutation Audit

As of July 28, 2026, the authoritative mutation logic lives in
`python/src/fl_platform/research/registry.py`.

## Reusable Directly

- Experiment specification validation:
  - `validate_experiment_specification`
- Experiment creation:
  - `ExperimentRegistry.create_experiment`
- Immutable specification snapshot persistence:
  - `ExperimentRegistry.create_experiment`
- Run-record creation:
  - `ExperimentRegistry._initialize_run`
- Event append:
  - `ExperimentRegistry.append_event`
- Metric append:
  - `ExperimentRegistry.append_metric`
- Artifact registration and sanitation:
  - `ExperimentRegistry._write_registered_artifact`
- Cancellation transition:
  - `ExperimentRegistry.request_cancel`
- Retry lineage creation:
  - `ExperimentRegistry.create_retry_attempt`
- Restart recovery:
  - `ExperimentRegistry.recover`
- Corruption detection:
  - `ExperimentRegistry.detect_and_mark_corruption`
- Synthetic orchestration start:
  - `BoundedExperimentOrchestrator.execute_experiment`

## Requires Thin Wrapper

- Public-command validation:
  - needs schema/version/auth/expiry/payload-hash handling around the
    existing Python validators
- Durable command idempotency:
  - implemented as a command-service wrapper over the existing registry
- Command status:
  - implemented by persisting typed command results, not by modifying the
    registry model
- Writer health:
  - implemented as a bounded command-service health projection over
    registry recovery and scan behavior

## Requires Transaction Tightening

- Synthetic start:
  - existing orchestration semantics are reusable, but the command
    service must guard execution mode, expected version, and replay
    handling
- Cancellation replay:
  - existing cancellation is reusable, but the command layer must make
    duplicate public retries replay-safe

## Deferred

- Public retry command exposure:
  - the underlying retry lineage primitive exists, but a complete public
    command contract, authorization policy, and replay contract were not
    finalized in this pass
- Full production transport hardening:
  - the current local/dev implementation uses a bounded shared secret,
    not mTLS
