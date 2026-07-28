from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .registry import _specification_from_payload
from .specification import ExperimentSpecification

COMMAND_SCHEMA_VERSION = 1


class CommandType(StrEnum):
    VALIDATE_EXPERIMENT_SPECIFICATION = "ValidateExperimentSpecification"
    CREATE_EXPERIMENT = "CreateExperiment"
    START_SYNTHETIC_EXPERIMENT = "StartSyntheticExperiment"
    CANCEL_EXPERIMENT = "CancelExperiment"
    GET_COMMAND_STATUS = "GetCommandStatus"
    GET_WRITER_HEALTH = "GetWriterHealth"


class CommandStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    CONFLICT = "CONFLICT"
    NOT_FOUND = "NOT_FOUND"
    PERMISSION_CONTEXT_REJECTED = "PERMISSION_CONTEXT_REJECTED"
    EXPIRED = "EXPIRED"
    CANCELED = "CANCELED"
    STORAGE_DEGRADED = "STORAGE_DEGRADED"
    CORRUPTION_DETECTED = "CORRUPTION_DETECTED"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(slots=True)
class ActorReference:
    actor_id: str
    actor_email: str
    actor_role: str


@dataclass(slots=True)
class CommandEnvelope:
    schema_version: int
    command_id: str
    command_type: CommandType
    request_timestamp: str
    expiry_timestamp: str
    caller_service: str
    actor: ActorReference
    permission_context: list[str] = field(default_factory=list)
    idempotency_key: str = ""
    expected_experiment_version: int | None = None
    request_payload_hash: str = ""
    correlation_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def canonical_payload_hash(self) -> str:
        return sha256_json(self.payload)


@dataclass(slots=True)
class CommandResult:
    schema_version: int
    command_id: str
    command_type: CommandType
    status: CommandStatus
    durable_completion: bool
    experiment_id: str = ""
    experiment_record_version: int | None = None
    specification_hash: str = ""
    previous_state: str = ""
    current_state: str = ""
    idempotent_replay: bool = False
    reason_code: str = ""
    validation_errors: list[str] = field(default_factory=list)
    completion_timestamp: str = ""
    response_payload_hash: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def finalize(self) -> CommandResult:
        self.response_payload_hash = sha256_json(self.payload)
        return self


def sha256_json(payload: Any) -> str:
    canonical = _canonical_json(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_json(payload: Any) -> str:
    if payload is None:
        return "null"
    if payload is True:
        return "true"
    if payload is False:
        return "false"
    if isinstance(payload, str):
        return json.dumps(payload, ensure_ascii=True)
    if isinstance(payload, Decimal):
        return format(payload, "f")
    if isinstance(payload, int):
        return str(payload)
    if isinstance(payload, float):
        return json.dumps(payload, ensure_ascii=True, allow_nan=False)
    if isinstance(payload, list):
        return "[" + ",".join(_canonical_json(item) for item in payload) + "]"
    if isinstance(payload, dict):
        items = []
        for key in sorted(payload):
            encoded_key = json.dumps(str(key), ensure_ascii=True)
            encoded_value = _canonical_json(payload[key])
            items.append(f"{encoded_key}:{encoded_value}")
        return "{" + ",".join(items) + "}"
    raise TypeError(f"unsupported canonical json value: {type(payload)!r}")


def parse_command_envelope(payload: dict[str, Any]) -> CommandEnvelope:
    required_keys = {
        "schema_version",
        "command_id",
        "command_type",
        "request_timestamp",
        "expiry_timestamp",
        "caller_service",
        "actor",
        "permission_context",
        "idempotency_key",
        "expected_experiment_version",
        "request_payload_hash",
        "correlation_id",
        "payload",
    }
    _require_exact_keys(payload, required_keys)
    actor_payload = _expect_dict(payload["actor"], "actor")
    _require_exact_keys(actor_payload, {"actor_id", "actor_email", "actor_role"})
    return CommandEnvelope(
        schema_version=int(payload["schema_version"]),
        command_id=str(payload["command_id"]),
        command_type=CommandType(str(payload["command_type"])),
        request_timestamp=str(payload["request_timestamp"]),
        expiry_timestamp=str(payload["expiry_timestamp"]),
        caller_service=str(payload["caller_service"]),
        actor=ActorReference(
            actor_id=str(actor_payload["actor_id"]),
            actor_email=str(actor_payload["actor_email"]),
            actor_role=str(actor_payload["actor_role"]),
        ),
        permission_context=[str(item) for item in payload["permission_context"]],
        idempotency_key=str(payload["idempotency_key"]),
        expected_experiment_version=(
            None
            if payload["expected_experiment_version"] is None
            else int(payload["expected_experiment_version"])
        ),
        request_payload_hash=str(payload["request_payload_hash"]),
        correlation_id=str(payload["correlation_id"]),
        payload=_expect_dict(payload["payload"], "payload"),
    )


def parse_experiment_specification(payload: dict[str, Any]) -> ExperimentSpecification:
    return _specification_from_payload(payload)


def command_result_to_json(result: CommandResult) -> dict[str, Any]:
    payload = asdict(result)
    payload["command_type"] = result.command_type.value
    payload["status"] = result.status.value
    return payload


def command_envelope_to_json(command: CommandEnvelope) -> dict[str, Any]:
    payload = asdict(command)
    payload["command_type"] = command.command_type.value
    return payload


def _expect_dict(payload: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{field_name} must be an object")
    return payload


def _require_exact_keys(payload: dict[str, Any], required_keys: set[str]) -> None:
    actual_keys = set(payload)
    unknown = actual_keys - required_keys
    missing = required_keys - actual_keys
    if missing:
        raise ValueError(f"missing required fields: {sorted(missing)}")
    if unknown:
        raise ValueError(f"unknown fields: {sorted(unknown)}")
