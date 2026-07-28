from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .command_contracts import (
    COMMAND_SCHEMA_VERSION,
    CommandEnvelope,
    CommandResult,
    CommandStatus,
    CommandType,
    command_result_to_json,
    parse_experiment_specification,
)
from .registry import (
    BoundedExperimentOrchestrator,
    ExperimentConflictError,
    ExperimentCorruptionError,
    ExperimentRegistry,
    ExperimentState,
    ResearchRegistryError,
    RunState,
    SyntheticExecutionAdapter,
    SyntheticExecutionResult,
    build_environment_manifest,
)
from .specification import (
    ExperimentSpecificationError,
    validate_experiment_specification,
)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


class _DefaultSyntheticAdapter:
    def execute(  # type: ignore[no-untyped-def]
        self, specification, run_record
    ) -> SyntheticExecutionResult:
        return SyntheticExecutionResult(
            completed=True,
            summary={
                "execution_mode": "SYNTHETIC_TEST_EXECUTION",
                "seed": run_record.seed,
                "status": "completed",
            },
            metrics=[
                {
                    "scope": "GLOBAL",
                    "name": "accuracy",
                    "value": 0.75 + (run_record.seed * 0.01),
                    "unit": "ratio",
                    "round": specification.runtime.max_rounds,
                    "model_version": specification.model.model_version,
                    "source_component": "synthetic-command-service",
                    "tags": ["SYNTHETIC_TEST_EXECUTION"],
                }
            ],
            events=[
                (
                    "RUN_COMPLETED",
                    "synthetic command service completed bounded test execution",
                )
            ],
            artifact_payloads={
                "synthetic-execution.txt": "SYNTHETIC_TEST_EXECUTION completed"
            },
        )


class ResearchCommandService:
    def __init__(
        self,
        root: str | Path,
        *,
        adapter: SyntheticExecutionAdapter | None = None,
    ) -> None:
        self.root = Path(root)
        self.registry = ExperimentRegistry(self.root)
        self.orchestrator = BoundedExperimentOrchestrator(
            self.registry, adapter or _DefaultSyntheticAdapter()
        )
        self.commands_root = self.root / "commands"
        self.command_results_root = self.commands_root / "by-id"
        self.idempotency_root = self.commands_root / "idempotency"
        self.audit_path = self.commands_root / "audit.jsonl"
        self.metrics_path = self.commands_root / "metrics.jsonl"
        self._lock = threading.Lock()

    def execute(
        self, command: CommandEnvelope, *, payload_hash_override: str | None = None
    ) -> CommandResult:
        if command.schema_version != COMMAND_SCHEMA_VERSION:
            return self._result(
                command,
                CommandStatus.VALIDATION_FAILED,
                durable=False,
                reason_code="unsupported_schema_version",
                validation_errors=["unsupported schema_version"],
            )
        if not command.command_id.strip():
            return self._result(
                command,
                CommandStatus.VALIDATION_FAILED,
                durable=False,
                reason_code="invalid_command_id",
                validation_errors=["command_id is required"],
            )
        if command.request_payload_hash != (
            payload_hash_override or command.canonical_payload_hash()
        ):
            return self._result(
                command,
                CommandStatus.VALIDATION_FAILED,
                durable=False,
                reason_code="request_payload_hash_mismatch",
                validation_errors=["request_payload_hash does not match payload"],
            )
        if _parse_utc(command.expiry_timestamp) < datetime.now(UTC):
            return self._persist_result(
                command,
                self._result(
                    command,
                    CommandStatus.EXPIRED,
                    durable=True,
                    reason_code="command_expired",
                ),
            )
        if command.command_type == CommandType.GET_COMMAND_STATUS:
            return self.get_command_status(command)
        if command.command_type == CommandType.GET_WRITER_HEALTH:
            return self._persist_result(command, self.get_writer_health(command))

        idempotency_key = command.idempotency_key.strip()
        if (
            command.command_type
            in {
                CommandType.CREATE_EXPERIMENT,
                CommandType.START_SYNTHETIC_EXPERIMENT,
                CommandType.CANCEL_EXPERIMENT,
            }
            and not idempotency_key
        ):
            return self._result(
                command,
                CommandStatus.VALIDATION_FAILED,
                durable=False,
                reason_code="missing_idempotency_key",
                validation_errors=["idempotency_key is required"],
            )

        if idempotency_key:
            cached = self._replay_idempotent_result(command)
            if cached is not None:
                cached.idempotent_replay = True
                return cached

        try:
            if command.command_type == CommandType.VALIDATE_EXPERIMENT_SPECIFICATION:
                result = self.validate_experiment_specification(command)
            elif command.command_type == CommandType.CREATE_EXPERIMENT:
                result = self.create_experiment(command)
            elif command.command_type == CommandType.START_SYNTHETIC_EXPERIMENT:
                result = self.start_synthetic_experiment(command)
            elif command.command_type == CommandType.CANCEL_EXPERIMENT:
                result = self.cancel_experiment(command)
            else:
                result = self._result(
                    command,
                    CommandStatus.VALIDATION_FAILED,
                    durable=False,
                    reason_code="unsupported_command_type",
                    validation_errors=["unsupported command_type"],
                )
        except ExperimentSpecificationError as error:
            result = self._result(
                command,
                CommandStatus.VALIDATION_FAILED,
                durable=False,
                reason_code="semantic_validation_failed",
                validation_errors=[str(error)],
            )
        except ExperimentConflictError as error:
            result = self._result(
                command,
                CommandStatus.CONFLICT,
                durable=True,
                reason_code="idempotency_or_version_conflict",
                validation_errors=[str(error)],
            )
        except ExperimentCorruptionError as error:
            result = self._result(
                command,
                CommandStatus.CORRUPTION_DETECTED,
                durable=True,
                reason_code="corruption_detected",
                validation_errors=[str(error)],
            )
        except FileNotFoundError:
            result = self._result(
                command,
                CommandStatus.NOT_FOUND,
                durable=True,
                reason_code="record_not_found",
            )
        except ResearchRegistryError as error:
            result = self._result(
                command,
                CommandStatus.VALIDATION_FAILED,
                durable=False,
                reason_code="research_registry_error",
                validation_errors=[str(error)],
            )
        except Exception:
            result = self._result(
                command,
                CommandStatus.INTERNAL_ERROR,
                durable=False,
                reason_code="internal_error",
            )
        return self._persist_result(command, result)

    def validate_experiment_specification(
        self, command: CommandEnvelope
    ) -> CommandResult:
        payload = command.payload
        specification_payload = _require_dict(payload, "specification")
        specification = parse_experiment_specification(specification_payload)
        validated = validate_experiment_specification(specification)
        expected_hash = validated.compute_hash()
        client_hash = str(payload.get("client_specification_hash", "")).strip()
        if client_hash and client_hash != expected_hash:
            return self._result(
                command,
                CommandStatus.VALIDATION_FAILED,
                durable=False,
                reason_code="specification_hash_mismatch",
                validation_errors=["client_specification_hash does not match"],
            )
        self._append_audit(
            command,
            event_type="VALIDATE_EXPERIMENT_SPECIFICATION",
            experiment_id=validated.experiment_id,
        )
        return self._result(
            command,
            CommandStatus.SUCCEEDED,
            durable=True,
            experiment_id=validated.experiment_id,
            specification_hash=expected_hash,
            payload={
                "valid": True,
                "compatibility_status": "SUPPORTED_FOR_SYNTHETIC_TEST_EXECUTION",
                "specification_hash": expected_hash,
                "validation_errors": [],
            },
        )

    def create_experiment(self, command: CommandEnvelope) -> CommandResult:
        specification = parse_experiment_specification(
            _require_dict(command.payload, "specification")
        )
        validated = validate_experiment_specification(specification)
        expected_hash = validated.compute_hash()
        client_hash = str(command.payload.get("client_specification_hash", "")).strip()
        if client_hash and client_hash != expected_hash:
            return self._result(
                command,
                CommandStatus.VALIDATION_FAILED,
                durable=False,
                reason_code="specification_hash_mismatch",
                validation_errors=["client_specification_hash does not match"],
            )
        record = self.registry.create_experiment(
            validated,
            actor=command.actor.actor_email or command.actor.actor_id,
            idempotency_key=command.idempotency_key,
            environment_manifest=build_environment_manifest(validated),
        )
        self._append_audit(
            command, event_type="CREATE_EXPERIMENT", experiment_id=record.experiment_id
        )
        return self._result(
            command,
            CommandStatus.SUCCEEDED,
            durable=True,
            experiment_id=record.experiment_id,
            experiment_record_version=record.record_version,
            specification_hash=record.specification_hash,
            current_state=record.current_state,
            payload={
                "experiment": asdict(record),
                "declared_seed_count": record.declared_seed_count,
                "synthetic_execution_only": True,
            },
        )

    def start_synthetic_experiment(self, command: CommandEnvelope) -> CommandResult:
        experiment_id = str(command.payload.get("experiment_id", "")).strip()
        execution_mode = str(command.payload.get("execution_mode", "")).strip()
        if execution_mode != "SYNTHETIC_TEST_EXECUTION":
            return self._result(
                command,
                CommandStatus.VALIDATION_FAILED,
                durable=False,
                experiment_id=experiment_id,
                reason_code="unsupported_execution_mode",
                validation_errors=["execution_mode must be SYNTHETIC_TEST_EXECUTION"],
            )
        before = self.registry.get_registry_record(experiment_id)
        if before.current_state in {
            ExperimentState.COMPLETED.value,
            ExperimentState.CANCELED.value,
            ExperimentState.CORRUPTED.value,
        }:
            return self._result(
                command,
                CommandStatus.CONFLICT,
                durable=True,
                experiment_id=experiment_id,
                experiment_record_version=before.record_version,
                previous_state=before.current_state,
                current_state=before.current_state,
                reason_code="experiment_not_startable",
            )
        if (
            command.expected_experiment_version is not None
            and before.record_version != command.expected_experiment_version
        ):
            raise ExperimentConflictError("stale experiment record version")
        after = self.orchestrator.execute_experiment(experiment_id)
        self._append_audit(
            command,
            event_type="START_SYNTHETIC_EXPERIMENT",
            experiment_id=experiment_id,
        )
        self._append_metric(command, "synthetic_start", 1.0)
        return self._result(
            command,
            CommandStatus.SUCCEEDED,
            durable=True,
            experiment_id=experiment_id,
            experiment_record_version=after.record_version,
            specification_hash=after.specification_hash,
            previous_state=before.current_state,
            current_state=after.current_state,
            payload={"experiment": asdict(after), "execution_mode": execution_mode},
        )

    def cancel_experiment(self, command: CommandEnvelope) -> CommandResult:
        experiment_id = str(command.payload.get("experiment_id", "")).strip()
        reason = str(command.payload.get("reason", "")).strip()[:240]
        before = self.registry.get_registry_record(experiment_id)
        if before.current_state in {
            ExperimentState.CANCELED.value,
            ExperimentState.COMPLETED.value,
            ExperimentState.COMPLETED_WITH_PARTIAL_RUNS.value,
        }:
            return self._result(
                command,
                CommandStatus.SUCCEEDED,
                durable=True,
                experiment_id=experiment_id,
                experiment_record_version=before.record_version,
                previous_state=before.current_state,
                current_state=before.current_state,
                reason_code="already_terminal",
                payload={"experiment": asdict(before)},
            )
        expected = (
            command.expected_experiment_version
            if command.expected_experiment_version is not None
            else before.record_version
        )
        if before.current_state == ExperimentState.CANCEL_REQUESTED.value:
            current = self.registry.get_registry_record(experiment_id)
            return self._result(
                command,
                CommandStatus.SUCCEEDED,
                durable=True,
                experiment_id=experiment_id,
                experiment_record_version=current.record_version,
                previous_state=before.current_state,
                current_state=current.current_state,
                reason_code="already_cancel_requested",
                payload={"experiment": asdict(current)},
            )
        requested = self.registry.request_cancel(
            experiment_id,
            actor=command.actor.actor_email or command.actor.actor_id,
            expected_version=expected,
        )
        final_record = requested
        if all(
            run.current_state in {RunState.CANCELED.value, RunState.COMPLETED.value}
            for run in self.registry.list_run_records(experiment_id)
        ):
            final_record = self.orchestrator._reconcile_experiment_state(experiment_id)
        self.registry.append_event(
            experiment_id,
            event_type="EXPERIMENT_CANCELLATION_REASON",
            actor=command.actor.actor_email or command.actor.actor_id,
            reason=reason or "cancellation requested",
        )
        self._append_audit(
            command, event_type="CANCEL_EXPERIMENT", experiment_id=experiment_id
        )
        self._append_metric(command, "cancel_request", 1.0)
        return self._result(
            command,
            CommandStatus.SUCCEEDED,
            durable=True,
            experiment_id=experiment_id,
            experiment_record_version=final_record.record_version,
            specification_hash=final_record.specification_hash,
            previous_state=before.current_state,
            current_state=final_record.current_state,
            payload={"experiment": asdict(final_record)},
        )

    def get_command_status(self, command: CommandEnvelope) -> CommandResult:
        status_command_id = str(command.payload.get("status_command_id", "")).strip()
        result_path = self.command_results_root / f"{status_command_id}.json"
        if not result_path.exists():
            return self._result(
                command,
                CommandStatus.NOT_FOUND,
                durable=True,
                reason_code="command_result_not_found",
            )
        stored = json.loads(result_path.read_text(encoding="utf-8"))
        return CommandResult(
            schema_version=int(stored["schema_version"]),
            command_id=str(stored["command_id"]),
            command_type=CommandType(str(stored["command_type"])),
            status=CommandStatus(str(stored["status"])),
            durable_completion=bool(stored["durable_completion"]),
            experiment_id=str(stored.get("experiment_id", "")),
            experiment_record_version=stored.get("experiment_record_version"),
            specification_hash=str(stored.get("specification_hash", "")),
            previous_state=str(stored.get("previous_state", "")),
            current_state=str(stored.get("current_state", "")),
            idempotent_replay=bool(stored.get("idempotent_replay", False)),
            reason_code=str(stored.get("reason_code", "")),
            validation_errors=[
                str(item) for item in stored.get("validation_errors", [])
            ],
            completion_timestamp=str(stored.get("completion_timestamp", "")),
            response_payload_hash=str(stored.get("response_payload_hash", "")),
            payload=_coerce_dict(stored.get("payload", {})),
        )

    def get_writer_health(self, command: CommandEnvelope) -> CommandResult:
        health = self.writer_health_payload()
        return self._result(
            command,
            CommandStatus.SUCCEEDED,
            durable=True,
            payload=health,
        )

    def writer_health_payload(self) -> dict[str, Any]:
        recovery = self.registry.recover()
        experiments = self.registry.list_experiments()
        active_experiment_count = sum(
            1
            for item in experiments
            if item.current_state
            in {
                ExperimentState.READY.value,
                ExperimentState.RUNNING.value,
                ExperimentState.CANCEL_REQUESTED.value,
            }
        )
        active_synthetic_execution_count = sum(
            1
            for item in experiments
            if item.current_state == ExperimentState.RUNNING.value
        )
        pending_cancellation_count = sum(
            1
            for item in experiments
            if item.current_state == ExperimentState.CANCEL_REQUESTED.value
        )
        lost_run_count = 0
        for item in experiments:
            for run in self.registry.list_run_records(item.experiment_id):
                if run.current_state == RunState.LOST.value:
                    lost_run_count += 1
        last_successful_command = self._tail_jsonl(self.audit_path)
        last_failed_command = self._tail_jsonl(
            self.metrics_path, metric_name="command_failure"
        )
        degraded = recovery["corrupted_experiment_count"] > 0
        return {
            "service_status": "HEALTHY" if not degraded else "DEGRADED",
            "command_service_available": True,
            "registry_root_readable": self.root.exists(),
            "registry_root_writable": self.root.exists() or self.root.parent.exists(),
            "lock_manager_status": "LOCAL_MUTEX",
            "idempotency_store_status": "READY",
            "registry_scan_status": "OK",
            "corruption_count": recovery["corrupted_experiment_count"],
            "active_experiment_count": active_experiment_count,
            "active_synthetic_execution_count": active_synthetic_execution_count,
            "pending_cancellation_count": pending_cancellation_count,
            "lost_run_count": lost_run_count,
            "last_successful_command": last_successful_command,
            "last_failed_command": last_failed_command,
            "last_successful_write": last_successful_command.get("timestamp", ""),
            "last_failed_write": last_failed_command.get("timestamp", ""),
            "degraded": degraded,
            "degraded_reason_class": (
                "research_registry_corruption" if degraded else ""
            ),
        }

    def _persist_result(
        self, command: CommandEnvelope, result: CommandResult
    ) -> CommandResult:
        result.finalize()
        result_path = self.command_results_root / f"{command.command_id}.json"
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(
            json.dumps(command_result_to_json(result), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        if command.idempotency_key:
            idempotency_path = self._idempotency_path(command)
            idempotency_path.parent.mkdir(parents=True, exist_ok=True)
            idempotency_path.write_text(
                json.dumps(
                    {
                        "schema_version": COMMAND_SCHEMA_VERSION,
                        "command_id": command.command_id,
                        "command_type": command.command_type.value,
                        "request_payload_hash": command.request_payload_hash,
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        if result.status != CommandStatus.SUCCEEDED:
            self._append_metric(command, "command_failure", 1.0)
        return result

    def _replay_idempotent_result(
        self, command: CommandEnvelope
    ) -> CommandResult | None:
        idempotency_path = self._idempotency_path(command)
        if not idempotency_path.exists():
            return None
        cached = json.loads(idempotency_path.read_text(encoding="utf-8"))
        if cached["request_payload_hash"] != command.request_payload_hash:
            raise ExperimentConflictError(
                "idempotency key reuse conflicts with a different request body"
            )
        return self.get_command_status(
            CommandEnvelope(
                schema_version=COMMAND_SCHEMA_VERSION,
                command_id=command.command_id,
                command_type=CommandType.GET_COMMAND_STATUS,
                request_timestamp=command.request_timestamp,
                expiry_timestamp=command.expiry_timestamp,
                caller_service=command.caller_service,
                actor=command.actor,
                permission_context=[],
                idempotency_key="",
                expected_experiment_version=None,
                request_payload_hash="",
                correlation_id=command.correlation_id,
                payload={"status_command_id": str(cached["command_id"])},
            )
        )

    def _idempotency_path(self, command: CommandEnvelope) -> Path:
        return (
            self.idempotency_root
            / command.command_type.value
            / f"{command.idempotency_key}.json"
        )

    def _result(
        self,
        command: CommandEnvelope,
        status: CommandStatus,
        *,
        durable: bool,
        experiment_id: str = "",
        experiment_record_version: int | None = None,
        specification_hash: str = "",
        previous_state: str = "",
        current_state: str = "",
        reason_code: str = "",
        validation_errors: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> CommandResult:
        return CommandResult(
            schema_version=COMMAND_SCHEMA_VERSION,
            command_id=command.command_id,
            command_type=command.command_type,
            status=status,
            durable_completion=durable,
            experiment_id=experiment_id,
            experiment_record_version=experiment_record_version,
            specification_hash=specification_hash,
            previous_state=previous_state,
            current_state=current_state,
            reason_code=reason_code,
            validation_errors=list(validation_errors or []),
            completion_timestamp=_utc_now(),
            payload=dict(payload or {}),
        )

    def _append_audit(
        self, command: CommandEnvelope, *, event_type: str, experiment_id: str
    ) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": _utc_now(),
                        "command_id": command.command_id,
                        "correlation_id": command.correlation_id,
                        "caller_service": command.caller_service,
                        "event_type": event_type,
                        "experiment_id": experiment_id,
                        "actor": asdict(command.actor),
                    },
                    sort_keys=True,
                )
                + "\n"
            )

    def _append_metric(
        self, command: CommandEnvelope, metric_name: str, value: float
    ) -> None:
        self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
        with self.metrics_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": _utc_now(),
                        "command_id": command.command_id,
                        "metric_name": metric_name,
                        "numeric_value": value,
                    },
                    sort_keys=True,
                )
                + "\n"
            )

    def _tail_jsonl(
        self, path: Path, *, metric_name: str | None = None
    ) -> dict[str, Any]:
        if not path.exists():
            return {}
        lines = path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            payload = _coerce_dict(json.loads(line))
            if metric_name and payload.get("metric_name") != metric_name:
                continue
            return payload
        return {}


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ResearchRegistryError(f"{key} must be an object")
    return value


def _coerce_dict(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ResearchRegistryError("payload must be an object")
    return payload
