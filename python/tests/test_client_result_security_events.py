"""Work Package O: SubmitClientResult was, until this slice, the one
signed-message RPC with no worker-side local security event at all --
the worker learned the coordinator's accept/reject verdict on its own
signed submission but never recorded it. These tests exercise
GrpcCoordinatorClient._emit_client_result_outcome_event via the real
submit_result() call path, against a fake stub, asserting on the real
local SecurityEventJournal contents (not a mock of the emission call).

Skips (not fails) when the generated protobuf bindings or grpcio aren't
available -- same convention as test_grpc_coordinator_client.py.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import torch
from fl_platform.privacy import SampleLevelLedgerEntry
from fl_platform.rpc import generated_root_exists
from fl_platform.security.security_event import (
    EVENT_CLIENT_RESULT_ACCEPTED,
    EVENT_CLIENT_RESULT_REJECTED,
    EVENT_PRIVACY_RECORD_ACCEPTED,
    EVENT_PRIVACY_RECORD_REJECTED,
    SecurityEvent,
)
from fl_platform.worker.coordinator_client import (
    ClientTrainingTask,
    GrpcCoordinatorClient,
    RunSpec,
)


def _grpc_available() -> bool:
    if not generated_root_exists():
        return False
    try:
        import grpc  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


class ConfigurableSubmitResultStub:
    """Returns a canned SubmitClientResultResponse the test controls,
    instead of RecordingStub's hardcoded accepted=True."""

    def __init__(self, *, accepted: bool, rejection_code: str = "") -> None:
        self.accepted = accepted
        self.rejection_code = rejection_code
        self.last_submit_request: object | None = None

    def SubmitClientResult(self, request: object) -> object:  # noqa: N802
        self.last_submit_request = request
        from coordinator import coordinator_pb2  # noqa: PLC0415

        return coordinator_pb2.SubmitClientResultResponse(
            accepted=self.accepted,
            reason="" if self.accepted else "rejected",
            rejection_code=self.rejection_code,
        )


def _make_client(
    journal_path: Path, *, accepted: bool, rejection_code: str = ""
) -> tuple[GrpcCoordinatorClient, ConfigurableSubmitResultStub]:
    client = GrpcCoordinatorClient(
        "127.0.0.1:0", security_event_journal_path=str(journal_path)
    )
    stub = ConfigurableSubmitResultStub(
        accepted=accepted, rejection_code=rejection_code
    )
    client._stub = stub  # type: ignore[assignment] # substituting the real stub for a fake one, by design
    return client, stub


def _submit(
    client: GrpcCoordinatorClient,
    *,
    sample_level_privacy: SampleLevelLedgerEntry | None = None,
) -> None:
    spec = RunSpec(run_id="run-1", algorithm="fedavg")
    task = ClientTrainingTask(
        has_task=True,
        task_id="task-1",
        lease_id="lease-1",
        client_id="client-a",
        round_id=1,
        model_version="v0",
        algorithm="fedavg",
    )
    client.submit_result(
        spec,
        worker_id="worker-a",
        task=task,
        delta={"weight": torch.tensor([1.0])},
        sample_count=16,
        update_id="update-1",
        nonce="nonce-1",
        now=0.0,
        sample_level_privacy=sample_level_privacy,
    )


def _emitted_events(client: GrpcCoordinatorClient) -> list[SecurityEvent]:
    # Read via the client's own live journal instance (the one
    # submit_result actually wrote through), not a second SecurityEventJournal
    # opened on the same path -- a fresh instance would only see events
    # written before its own construction, since the journal loads its
    # in-memory event list once at __init__ and this test never restarts
    # the client mid-test.
    journal = client._security_event_journal  # noqa: SLF001 - test-only introspection
    assert journal is not None
    return journal.list(limit=100)["events"]


def _privacy_entry() -> SampleLevelLedgerEntry:
    return SampleLevelLedgerEntry(
        run_id="run-1",
        round_id=1,
        client_id="client-a",
        epsilon=1.5,
        delta=1e-6,
        noise_multiplier=0.9,
        sample_rate=0.25,
        steps=4,
        accountant="rdp",
        recorded_at="2026-01-01T00:00:00Z",
        entry_id="entry-abc-123",
    )


@unittest.skipUnless(
    _grpc_available(), "generated protobuf bindings/grpcio not available"
)
class ClientResultSecurityEventTests(unittest.TestCase):
    def test_accepted_submission_emits_client_result_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, _stub = _make_client(Path(tmp) / "events.jsonl", accepted=True)
            _submit(client)

            event_types = [e.event_type for e in _emitted_events(client)]
            self.assertIn(EVENT_CLIENT_RESULT_ACCEPTED, event_types)
            self.assertNotIn(EVENT_CLIENT_RESULT_REJECTED, event_types)
            self.assertNotIn(EVENT_PRIVACY_RECORD_ACCEPTED, event_types)

    def test_accepted_submission_with_privacy_record_emits_both_accepted_events(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, _stub = _make_client(Path(tmp) / "events.jsonl", accepted=True)
            _submit(client, sample_level_privacy=_privacy_entry())

            event_types = [e.event_type for e in _emitted_events(client)]
            self.assertIn(EVENT_CLIENT_RESULT_ACCEPTED, event_types)
            self.assertIn(EVENT_PRIVACY_RECORD_ACCEPTED, event_types)

    def test_rejected_submission_with_generic_reason_emits_client_result_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, _stub = _make_client(
                Path(tmp) / "events.jsonl",
                accepted=False,
                rejection_code="payload_hash_mismatch",
            )
            _submit(client)

            events = _emitted_events(client)
            event_types = [e.event_type for e in events]
            self.assertIn(EVENT_CLIENT_RESULT_REJECTED, event_types)
            self.assertNotIn(EVENT_PRIVACY_RECORD_REJECTED, event_types)
            rejected = next(
                e for e in events if e.event_type == EVENT_CLIENT_RESULT_REJECTED
            )
            self.assertEqual(rejected.reason_code, "payload_hash_mismatch")

    def test_rejected_submission_with_privacy_reason_emits_privacy_record_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, _stub = _make_client(
                Path(tmp) / "events.jsonl",
                accepted=False,
                rejection_code="privacy_payload_hash_mismatch",
            )
            _submit(client, sample_level_privacy=_privacy_entry())

            event_types = [e.event_type for e in _emitted_events(client)]
            self.assertIn(EVENT_PRIVACY_RECORD_REJECTED, event_types)
            self.assertNotIn(EVENT_CLIENT_RESULT_REJECTED, event_types)

    def test_rejected_submission_with_budget_contradiction_routes_to_privacy_rejected(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client, _stub = _make_client(
                Path(tmp) / "events.jsonl",
                accepted=False,
                rejection_code="budget_decision_contradiction",
            )
            _submit(client, sample_level_privacy=_privacy_entry())

            event_types = [e.event_type for e in _emitted_events(client)]
            self.assertIn(EVENT_PRIVACY_RECORD_REJECTED, event_types)


if __name__ == "__main__":
    unittest.main()
