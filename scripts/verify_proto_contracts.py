from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_FIELDS = {
    "proto/experiment/experiment.proto": {
        "RunConfiguration": {
            "run_id": 1,
            "dataset": 2,
            "model": 3,
            "privacy": 4,
            "algorithm": 5,
            "rounds": 6,
        },
    },
    "proto/worker/worker.proto": {
        "TensorManifest": {
            "name": 1,
            "shape": 2,
            "dtype": 3,
            "byte_length": 4,
            "checksum": 5,
        },
        "ClientTask": {
            "run_id": 1,
            "round_id": 2,
            "client_id": 3,
            "model_version": 4,
            "algorithm": 5,
            "nonce": 8,
            "trace_id": 9,
            "model_manifest": 10,
        },
        "ClientResult": {
            "run_id": 1,
            "round_id": 2,
            "client_id": 3,
            "base_model_version": 4,
            "local_step_count": 5,
            "sample_count": 6,
            "algorithm": 7,
            "update_format": 8,
            "tensor_manifest": 9,
            "update_norm": 11,
            "completion_timestamp": 12,
            "nonce": 13,
            "worker_id": 14,
            "trace_id": 15,
        },
        "WorkerCapability": {
            "device": 1,
            "cpu_count": 2,
            "gpu_available": 3,
            "gpu_count": 4,
            "available_memory_bytes": 5,
            "supported_model_formats": 6,
            "supported_algorithms": 7,
        },
        "RegisterWorkerRequest": {
            "worker_id": 1,
            "capability": 2,
            "trace_id": 3,
        },
        "WorkerHeartbeatRequest": {
            "worker_id": 1,
            "status": 2,
            "current_task_id": 3,
            "trace_id": 4,
        },
    },
    "proto/coordinator/coordinator.proto": {
        "RoundState": {
            "run_id": 1,
            "round_id": 2,
            "state": 3,
            "reason": 4,
        },
        "RunState": {
            "run_id": 1,
            "state": 2,
            "reason": 3,
        },
        "ErrorDetail": {
            "code": 1,
            "message": 2,
            "retryable": 3,
            "details": 4,
        },
        "ModelManifest": {
            "model_id": 1,
            "model_version": 2,
            "architecture_name": 3,
            "tensors": 4,
            "checksum": 5,
            "total_bytes": 6,
            "artifact_reference": 7,
            "schema_version": 8,
        },
        "OptimizerConfig": {
            "algorithm": 1,
            "weighting": 2,
            "server_lr": 3,
            "beta1": 4,
            "beta2": 5,
            "tau": 6,
            "contribution_cap": 7,
        },
        "CreateRunRequest": {
            "config": 1,
            "optimizer": 2,
            "target_clients_per_round": 3,
            "total_clients": 4,
            "max_rounds": 5,
            "round_timeout_seconds": 6,
            "minimum_valid_results": 7,
            "client_selection_seed": 8,
            "trace_id": 9,
        },
        "RunDetails": {
            "run_id": 1,
            "state": 2,
            "current_round": 3,
            "max_rounds": 4,
            "model_version": 5,
            "algorithm": 6,
            "registered_workers": 7,
            "healthy_workers": 8,
            "created_at": 9,
            "updated_at": 10,
        },
        "ClientTrainingTask": {
            "task_available": 1,
            "task": 2,
            "task_id": 3,
            "lease_id": 4,
            "lease_expires_at": 5,
            "local_epochs": 6,
            "batch_size": 7,
            "learning_rate": 8,
            "momentum": 9,
            "weight_decay": 10,
            "fedprox_mu": 11,
            "global_control_variate": 12,
            "client_control_variate": 13,
        },
        "SubmitClientResultRequest": {
            "worker_id": 1,
            "task_id": 2,
            "lease_id": 3,
            "result": 4,
            "client_control_variate_delta": 5,
            "refreshed_client_control_variate": 6,
        },
        "SubmitClientResultResponse": {
            "accepted": 1,
            "reason": 2,
            "error": 3,
        },
    },
    "proto/privacy/privacy.proto": {
        "PrivacyLedger": {
            "run_id": 1,
            "mode": 2,
            "epsilon": 3,
            "delta": 4,
            "noise_multiplier": 5,
            "clipping_bound": 6,
        },
    },
    "proto/events/events.proto": {
        "CoordinatorEvent": {
            "event_id": 1,
            "run_id": 2,
            "round_id": 3,
            "event_type": 4,
            "timestamp": 5,
            "trace_id": 6,
            "payload_json": 7,
        },
    },
    "proto/common/artifact.proto": {
        "ArtifactReference": {
            "uri": 1,
            "checksum": 2,
            "media_type": 3,
        },
    },
}


# NOTE: these regexes assume no nested message/enum declarations (i.e. no
# type is declared *inside* a message body). That invariant is deliberate:
# it keeps this dependency-free parser correct without needing a real
# protobuf parser. If a future change nests a message or enum inside
# another message, this script must be upgraded (or the nesting avoided).
MESSAGE_RE = re.compile(r"message\s+(?P<name>\w+)\s*\{(?P<body>.*?)\}", re.S)
FIELD_RE = re.compile(
    r"(?:repeated\s+)?(?:\w+(?:\.\w+)*|map<[^>]+>)\s+(?P<name>\w+)\s*=\s*(?P<number>\d+)"
)
ENUM_RE = re.compile(r"enum\s+(?P<name>\w+)\s*\{(?P<body>.*?)\}", re.S)
ENUM_VALUE_RE = re.compile(r"(?P<name>\w+)\s*=\s*(?P<number>-?\d+)")

# Enum *value* numbers to guard, closing the gap noted in
# docs/known-limitations.md ("enum values, not just field numbers, are not
# yet asserted"). Field-number checks above only assert message shape;
# this additionally asserts the wire-level integer each enum name maps to,
# since renumbering an enum value silently changes wire meaning exactly
# like renumbering a field would.
EXPECTED_ENUMS = {
    "proto/worker/worker.proto": {
        "WorkerStatus": {
            "WORKER_STATUS_UNSPECIFIED": 0,
            "WORKER_STATUS_REGISTERING": 1,
            "WORKER_STATUS_IDLE": 2,
            "WORKER_STATUS_BUSY": 3,
            "WORKER_STATUS_UNHEALTHY": 4,
            "WORKER_STATUS_DISCONNECTED": 5,
            "WORKER_STATUS_DRAINING": 6,
        },
    },
    "proto/events/events.proto": {
        "CoordinatorEventType": {
            "COORDINATOR_EVENT_TYPE_UNSPECIFIED": 0,
            "COORDINATOR_EVENT_TYPE_RUN_CREATED": 1,
            "COORDINATOR_EVENT_TYPE_RUN_COMPLETED": 22,
        },
    },
}


def parse_messages(path: Path) -> dict[str, dict[str, int]]:
    text = path.read_text(encoding="utf-8")
    messages: dict[str, dict[str, int]] = {}
    for message in MESSAGE_RE.finditer(text):
        fields: dict[str, int] = {}
        for field in FIELD_RE.finditer(message.group("body")):
            fields[field.group("name")] = int(field.group("number"))
        messages[message.group("name")] = fields
    return messages


def parse_enums(path: Path) -> dict[str, dict[str, int]]:
    text = path.read_text(encoding="utf-8")
    enums: dict[str, dict[str, int]] = {}
    for enum in ENUM_RE.finditer(text):
        values: dict[str, int] = {}
        for value in ENUM_VALUE_RE.finditer(enum.group("body")):
            values[value.group("name")] = int(value.group("number"))
        enums[enum.group("name")] = values
    return enums


def main() -> int:
    failures: list[str] = []
    for relative, messages in EXPECTED_FIELDS.items():
        actual = parse_messages(ROOT / relative)
        for message_name, expected_fields in messages.items():
            if message_name not in actual:
                failures.append(f"{relative}: missing message {message_name}")
                continue
            for field_name, field_number in expected_fields.items():
                actual_number = actual[message_name].get(field_name)
                if actual_number != field_number:
                    failures.append(
                        f"{relative}: {message_name}.{field_name} expected "
                        f"{field_number}, found {actual_number}"
                    )
    for relative, enums in EXPECTED_ENUMS.items():
        actual = parse_enums(ROOT / relative)
        for enum_name, expected_values in enums.items():
            if enum_name not in actual:
                failures.append(f"{relative}: missing enum {enum_name}")
                continue
            for value_name, value_number in expected_values.items():
                actual_number = actual[enum_name].get(value_name)
                if actual_number != value_number:
                    failures.append(
                        f"{relative}: {enum_name}.{value_name} expected "
                        f"{value_number}, found {actual_number}"
                    )
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print("protobuf contract compatibility checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
