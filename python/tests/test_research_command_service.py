from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

from test_experiment_registry import _base_specification

from fl_platform.research import (
    ResearchCommandHTTPServer,
    ResearchCommandService,
    StaticBearerCommandAuthenticator,
)
from fl_platform.research.command_contracts import (
    COMMAND_SCHEMA_VERSION,
    ActorReference,
    CommandEnvelope,
    CommandStatus,
    CommandType,
    command_result_to_json,
    sha256_json,
)


def _timestamp(offset_seconds: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _command(
    command_type: CommandType,
    payload: dict[str, Any],
    *,
    command_id: str = "cmd-1",
    idempotency_key: str = "",
    expected_experiment_version: int | None = None,
) -> CommandEnvelope:
    return CommandEnvelope(
        schema_version=COMMAND_SCHEMA_VERSION,
        command_id=command_id,
        command_type=command_type,
        request_timestamp=_timestamp(-5),
        expiry_timestamp=_timestamp(120),
        caller_service="go-control-plane",
        actor=ActorReference(
            actor_id="user-researcher",
            actor_email="researcher@fl-platform.dev",
            actor_role="researcher",
        ),
        permission_context=["research.experiments.create"],
        idempotency_key=idempotency_key,
        expected_experiment_version=expected_experiment_version,
        request_payload_hash=sha256_json(payload),
        correlation_id="corr-1",
        payload=payload,
    )


class ResearchCommandServiceTests(unittest.TestCase):
    def test_validate_returns_authoritative_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ResearchCommandService(Path(temp_dir) / "research")
            spec = _base_specification()
            command = _command(
                CommandType.VALIDATE_EXPERIMENT_SPECIFICATION,
                {
                    "specification": spec.canonical_payload()
                    | {"specification_hash": ""},
                    "client_specification_hash": spec.compute_hash(),
                },
            )
            result = service.execute(command)
            self.assertEqual(result.status, CommandStatus.SUCCEEDED)
            self.assertEqual(result.specification_hash, spec.compute_hash())

    def test_create_replays_exact_idempotent_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ResearchCommandService(Path(temp_dir) / "research")
            spec = _base_specification()
            payload = {
                "specification": spec.canonical_payload() | {"specification_hash": ""},
                "client_specification_hash": spec.compute_hash(),
            }
            first = service.execute(
                _command(
                    CommandType.CREATE_EXPERIMENT,
                    payload,
                    command_id="cmd-create-1",
                    idempotency_key="create-key",
                )
            )
            second = service.execute(
                _command(
                    CommandType.CREATE_EXPERIMENT,
                    payload,
                    command_id="cmd-create-2",
                    idempotency_key="create-key",
                )
            )
            self.assertEqual(first.status, CommandStatus.SUCCEEDED)
            self.assertTrue(second.idempotent_replay)
            self.assertEqual(first.experiment_id, second.experiment_id)
            self.assertEqual(first.specification_hash, second.specification_hash)

    def test_validate_rejects_bad_specification_hash_after_payload_hash_accepts(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ResearchCommandService(Path(temp_dir) / "research")
            spec = _base_specification()
            command = _command(
                CommandType.VALIDATE_EXPERIMENT_SPECIFICATION,
                {
                    "specification": spec.canonical_payload()
                    | {"specification_hash": ""},
                    "client_specification_hash": "wrong-specification-hash",
                },
            )
            result = service.execute(command)
            self.assertEqual(result.status, CommandStatus.VALIDATION_FAILED)
            self.assertEqual(result.reason_code, "specification_hash_mismatch")

    def test_validate_rejects_bad_payload_hash_before_specification_validation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ResearchCommandService(Path(temp_dir) / "research")
            spec = _base_specification()
            payload = {
                "specification": spec.canonical_payload() | {"specification_hash": ""},
                "client_specification_hash": spec.compute_hash(),
            }
            command = _command(
                CommandType.VALIDATE_EXPERIMENT_SPECIFICATION,
                payload,
            )
            command.request_payload_hash = "wrong-payload-hash"
            result = service.execute(command)
            self.assertEqual(result.status, CommandStatus.VALIDATION_FAILED)
            self.assertEqual(result.reason_code, "request_payload_hash_mismatch")

    def test_create_bad_payload_hash_blocks_mutation_before_spec_validation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "research"
            service = ResearchCommandService(root)
            spec = _base_specification()
            payload = {
                "specification": spec.canonical_payload() | {"specification_hash": ""},
                "client_specification_hash": "wrong-specification-hash",
            }
            command = _command(
                CommandType.CREATE_EXPERIMENT,
                payload,
                idempotency_key="create-key",
            )
            command.request_payload_hash = "wrong-payload-hash"
            result = service.execute(command)
            self.assertEqual(result.status, CommandStatus.VALIDATION_FAILED)
            self.assertEqual(result.reason_code, "request_payload_hash_mismatch")
            self.assertFalse((root / "experiments" / spec.experiment_id).exists())

    def test_start_and_cancel_flow_persist_durable_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ResearchCommandService(Path(temp_dir) / "research")
            spec = _base_specification()
            create = service.execute(
                _command(
                    CommandType.CREATE_EXPERIMENT,
                    {
                        "specification": spec.canonical_payload()
                        | {"specification_hash": ""},
                        "client_specification_hash": spec.compute_hash(),
                    },
                    command_id="cmd-create",
                    idempotency_key="create-key",
                )
            )
            started = service.execute(
                _command(
                    CommandType.START_SYNTHETIC_EXPERIMENT,
                    {
                        "experiment_id": spec.experiment_id,
                        "execution_mode": "SYNTHETIC_TEST_EXECUTION",
                    },
                    command_id="cmd-start",
                    idempotency_key="start-key",
                    expected_experiment_version=create.experiment_record_version,
                )
            )
            canceled = service.execute(
                _command(
                    CommandType.CANCEL_EXPERIMENT,
                    {
                        "experiment_id": spec.experiment_id,
                        "reason": "stop any remaining unstarted seeds",
                    },
                    command_id="cmd-cancel",
                    idempotency_key="cancel-key",
                    expected_experiment_version=started.experiment_record_version,
                )
            )
            self.assertEqual(started.status, CommandStatus.SUCCEEDED)
            self.assertIn(
                started.current_state,
                {"COMPLETED", "COMPLETED_WITH_PARTIAL_RUNS", "RUNNING"},
            )
            self.assertEqual(canceled.status, CommandStatus.SUCCEEDED)

    def test_status_lookup_reads_persisted_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ResearchCommandService(Path(temp_dir) / "research")
            spec = _base_specification()
            created = service.execute(
                _command(
                    CommandType.CREATE_EXPERIMENT,
                    {
                        "specification": spec.canonical_payload()
                        | {"specification_hash": ""},
                        "client_specification_hash": spec.compute_hash(),
                    },
                    command_id="cmd-create-status",
                    idempotency_key="create-key",
                )
            )
            status = service.execute(
                _command(
                    CommandType.GET_COMMAND_STATUS,
                    {"status_command_id": created.command_id},
                    command_id="cmd-status",
                )
            )
            self.assertEqual(status.command_id, created.command_id)
            self.assertEqual(status.status, CommandStatus.SUCCEEDED)


class ResearchCommandHTTPServerTests(unittest.TestCase):
    def test_http_server_authenticates_and_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ResearchCommandService(Path(temp_dir) / "research")
            server = ResearchCommandHTTPServer(
                ("127.0.0.1", 0),
                service,
                StaticBearerCommandAuthenticator(
                    expected_bearer_secret="top-secret",
                    expected_service_identity="go-control-plane",
                ),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                command = _command(
                    CommandType.GET_WRITER_HEALTH,
                    {},
                    command_id="cmd-health",
                )
                payload = command_result_to_json(service.execute(command))
                self.assertEqual(payload["status"], "SUCCEEDED")

                bad_request = {
                    "schema_version": 1,
                    "command_id": "bad",
                    "command_type": "GetWriterHealth",
                    "request_timestamp": _timestamp(-5),
                    "expiry_timestamp": _timestamp(120),
                    "caller_service": "go-control-plane",
                    "actor": {
                        "actor_id": "user-researcher",
                        "actor_email": "researcher@fl-platform.dev",
                        "actor_role": "researcher",
                    },
                    "permission_context": [],
                    "idempotency_key": "",
                    "expected_experiment_version": None,
                    "request_payload_hash": sha256_json({}),
                    "correlation_id": "corr-2",
                    "payload": {},
                    "unexpected": True,
                }
                conn = HTTPConnection(
                    str(server.server_address[0]), server.server_address[1]
                )
                conn.request(
                    "POST",
                    "/internal/research/commands",
                    body=json.dumps(bad_request),
                    headers={
                        "Authorization": "Bearer top-secret",
                        "X-Service-Identity": "go-control-plane",
                        "Content-Type": "application/json",
                    },
                )
                response = conn.getresponse()
                self.assertEqual(response.status, 400)
            finally:
                server.shutdown()
                thread.join(timeout=2)
