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


MESSAGE_RE = re.compile(r"message\s+(?P<name>\w+)\s*\{(?P<body>.*?)\}", re.S)
FIELD_RE = re.compile(
    r"(?:repeated\s+)?(?:\w+(?:\.\w+)*|map<[^>]+>)\s+(?P<name>\w+)\s*=\s*(?P<number>\d+)"
)


def parse_messages(path: Path) -> dict[str, dict[str, int]]:
    text = path.read_text(encoding="utf-8")
    messages: dict[str, dict[str, int]] = {}
    for message in MESSAGE_RE.finditer(text):
        fields: dict[str, int] = {}
        for field in FIELD_RE.finditer(message.group("body")):
            fields[field.group("name")] = int(field.group("number"))
        messages[message.group("name")] = fields
    return messages


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
    if failures:
        for failure in failures:
            print(failure)
        return 1
    print("protobuf contract compatibility checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
