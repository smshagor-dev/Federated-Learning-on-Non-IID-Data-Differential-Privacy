"""Tests for fl_platform.worker.__main__'s production-path wiring --
Security Runtime Completion and Release Evidence slice, Work Package B.

Before this slice, WorkerConfig declared tls_enabled/tls_ca_cert_path
but __main__.py never read them when constructing GrpcCoordinatorClient
-- a deployed worker container always connected insecure and never
loaded a signing identity or security-event journal, regardless of
configuration. These tests cover the three small, pure helper functions
this slice added to close that gap: _build_tls_config,
_load_or_generate_signing_identity, and the background security-event
flush thread (_start_security_event_flush_thread /
_run_security_event_flush_loop). They do not exercise a live gRPC
connection -- that is covered by the Docker-based runtime-validation
harness (scripts/security-validation/groups/event_centralization.py).
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import grpc
from fl_platform.worker import __main__ as worker_main
from fl_platform.worker.configuration import WorkerConfig
from fl_platform.worker.coordinator_client import (
    CoordinatorRejectedError,
    CoordinatorUnavailableError,
    GrpcCoordinatorClient,
)


class _FakeRpcError(grpc.RpcError):
    """A minimal stand-in for the real grpc.RpcError raised by a live
    grpc channel -- exercising _grpc_call's translation logic does not
    require an actual gRPC connection, only something that responds to
    .code()/.details() the way a real RpcError does."""

    def __init__(
        self, code: grpc.StatusCode, details: str = "simulated failure"
    ) -> None:
        self._code = code
        self._details = details

    def code(self) -> grpc.StatusCode:
        return self._code

    def details(self) -> str:
        return self._details


class GrpcCallErrorTranslationTests(unittest.TestCase):
    """Security Runtime Completion and Release Evidence slice, Work
    Package B: before this slice, every self._stub.XxxRPC(...) call
    site in GrpcCoordinatorClient let a raw grpc.RpcError propagate
    uncaught -- WorkerService.run()'s retry logic (service.py) only
    ever catches CoordinatorUnavailableError/CoordinatorRejectedError,
    never a raw grpc.RpcError, so a worker using the real gRPC client
    would have crashed uncaught on the first transient coordinator
    failure. _grpc_call is the fix; these tests exercise it directly
    without needing a live gRPC channel (object.__new__ bypasses
    __init__, which is safe here since _grpc_call only reads its own
    arguments plus a deferred `import grpc`, never any other instance
    state)."""

    def setUp(self) -> None:
        self.client: GrpcCoordinatorClient = object.__new__(GrpcCoordinatorClient)

    def test_unavailable_status_maps_to_coordinator_unavailable_error(self) -> None:
        def rpc(request: object) -> object:
            raise _FakeRpcError(grpc.StatusCode.UNAVAILABLE)

        with self.assertRaises(CoordinatorUnavailableError):
            self.client._grpc_call(rpc, None)

    def test_deadline_exceeded_maps_to_coordinator_unavailable_error(self) -> None:
        def rpc(request: object) -> object:
            raise _FakeRpcError(grpc.StatusCode.DEADLINE_EXCEEDED)

        with self.assertRaises(CoordinatorUnavailableError):
            self.client._grpc_call(rpc, None)

    def test_permission_denied_maps_to_coordinator_rejected_error(self) -> None:
        def rpc(request: object) -> object:
            raise _FakeRpcError(grpc.StatusCode.PERMISSION_DENIED)

        with self.assertRaises(CoordinatorRejectedError):
            self.client._grpc_call(rpc, None)

    def test_invalid_argument_maps_to_coordinator_rejected_error(self) -> None:
        def rpc(request: object) -> object:
            raise _FakeRpcError(grpc.StatusCode.INVALID_ARGUMENT)

        with self.assertRaises(CoordinatorRejectedError):
            self.client._grpc_call(rpc, None)

    def test_successful_call_passes_the_response_through_unchanged(self) -> None:
        def rpc(request: object) -> str:
            return "real-response"

        self.assertEqual(self.client._grpc_call(rpc, None), "real-response")


class BuildTlsConfigTests(unittest.TestCase):
    def test_tls_disabled_by_default(self) -> None:
        config = WorkerConfig(worker_id="worker-1")
        self.assertIsNone(worker_main._build_tls_config(config))

    def test_tls_enabled_builds_a_real_config(self) -> None:
        config = WorkerConfig(
            worker_id="worker-1",
            tls_enabled=True,
            tls_ca_cert_path="/certs/ca.pem",
            tls_client_cert_path="/certs/worker.cert.pem",
            tls_client_key_path="/certs/worker.key.pem",
            tls_server_name="coordinator",
        )
        tls = worker_main._build_tls_config(config)
        self.assertIsNotNone(tls)
        assert tls is not None
        self.assertEqual(tls.trusted_ca_path, "/certs/ca.pem")
        self.assertEqual(tls.client_cert_path, "/certs/worker.cert.pem")
        self.assertEqual(tls.client_key_path, "/certs/worker.key.pem")
        self.assertEqual(tls.expected_server_name, "coordinator")


class LoadOrGenerateSigningIdentityTests(unittest.TestCase):
    def test_signing_disabled_by_default(self) -> None:
        config = WorkerConfig(worker_id="worker-1")
        self.assertIsNone(worker_main._load_or_generate_signing_identity(config))

    def test_first_boot_generates_and_persists_a_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = WorkerConfig(worker_id="worker-1", signing_key_dir=tmp)
            identity = worker_main._load_or_generate_signing_identity(config)
            self.assertIsNotNone(identity)
            assert identity is not None
            self.assertEqual(identity.worker_id, "worker-1")
            self.assertTrue(
                (Path(tmp) / "worker-1.signing-key.pem").exists(),
                "the generated private key must be persisted to disk",
            )

    def test_second_boot_reloads_the_same_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = WorkerConfig(worker_id="worker-1", signing_key_dir=tmp)
            first = worker_main._load_or_generate_signing_identity(config)
            second = worker_main._load_or_generate_signing_identity(config)
            assert first is not None and second is not None
            self.assertEqual(
                first.key_id,
                second.key_id,
                "a worker's signing identity must be stable across restarts, "
                "not regenerated on every boot",
            )

    def test_two_different_workers_get_independent_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity_a = worker_main._load_or_generate_signing_identity(
                WorkerConfig(worker_id="worker-a", signing_key_dir=tmp)
            )
            identity_b = worker_main._load_or_generate_signing_identity(
                WorkerConfig(worker_id="worker-b", signing_key_dir=tmp)
            )
            assert identity_a is not None and identity_b is not None
            self.assertNotEqual(identity_a.key_id, identity_b.key_id)


class SecurityEventFlushThreadTests(unittest.TestCase):
    def test_no_thread_started_when_journal_path_unset(self) -> None:
        config = WorkerConfig(worker_id="worker-1")

        class UnusedClient:
            def submit_security_events(self, worker_id: str) -> None:
                raise AssertionError("must not be called when disabled")

        result = worker_main._start_security_event_flush_thread(
            config,
            UnusedClient(),  # type: ignore[arg-type]
        )
        self.assertIsNone(result)

    def test_flush_loop_calls_submit_security_events_periodically(self) -> None:
        config = WorkerConfig(
            worker_id="worker-1",
            security_event_journal_path="/tmp/does-not-matter.jsonl",
            security_event_flush_interval_seconds=0.05,
        )
        calls: list[str] = []

        class RecordingClient:
            def submit_security_events(self, worker_id: str) -> None:
                calls.append(worker_id)

        result = worker_main._start_security_event_flush_thread(
            config,
            RecordingClient(),  # type: ignore[arg-type]
        )
        self.assertIsNotNone(result)
        assert result is not None
        thread, stop_event = result
        try:
            time.sleep(0.22)
        finally:
            stop_event.set()
            thread.join(timeout=2.0)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(
            len(calls), 2, "expected multiple flush attempts within the sleep window"
        )
        self.assertTrue(all(worker_id == "worker-1" for worker_id in calls))

    def test_flush_loop_survives_an_exception_and_keeps_retrying(self) -> None:
        config = WorkerConfig(
            worker_id="worker-1",
            security_event_journal_path="/tmp/does-not-matter.jsonl",
            security_event_flush_interval_seconds=0.05,
        )
        call_count = 0

        class ThrowingClient:
            def submit_security_events(self, worker_id: str) -> None:
                nonlocal call_count
                call_count += 1
                raise RuntimeError("simulated coordinator-unavailable failure")

        result = worker_main._start_security_event_flush_thread(
            config,
            ThrowingClient(),  # type: ignore[arg-type]
        )
        assert result is not None
        thread, stop_event = result
        try:
            time.sleep(0.22)
            self.assertTrue(
                thread.is_alive(),
                "a flush failure must never crash the background thread",
            )
        finally:
            stop_event.set()
            thread.join(timeout=2.0)
        self.assertGreaterEqual(call_count, 2)

    def test_stop_event_halts_the_loop_promptly(self) -> None:
        config = WorkerConfig(
            worker_id="worker-1",
            security_event_journal_path="/tmp/does-not-matter.jsonl",
            security_event_flush_interval_seconds=5.0,
        )

        class RecordingClient:
            def submit_security_events(self, worker_id: str) -> None:
                pass

        result = worker_main._start_security_event_flush_thread(
            config,
            RecordingClient(),  # type: ignore[arg-type]
        )
        assert result is not None
        thread, stop_event = result
        stop_event.set()
        thread.join(timeout=2.0)
        self.assertFalse(
            thread.is_alive(),
            "setting stop_event must halt the loop without waiting out the "
            "full flush interval",
        )


class HealthPollLoopRegistrationTests(unittest.TestCase):
    """Security Runtime Completion and Release Evidence slice: before
    this fix, a worker container started with no run_id configured (the
    default in docker-compose.dev.yml) never called register_worker() at
    all -- only WorkerService.run()'s training-loop path did. Caught by
    this slice's live Docker Compose validation, where worker-1 never
    appeared in the coordinator's identity registry despite the
    container running and its own health checks succeeding."""

    def test_registers_before_entering_the_health_poll_loop(self) -> None:
        calls: list[str] = []

        class FakeClient:
            def register_worker(self, spec: object, worker_id: str, now: float) -> None:
                calls.append(f"register:{worker_id}")

            def health(self) -> str:
                calls.append("health")
                raise KeyboardInterrupt

        config = WorkerConfig(worker_id="worker-1", heartbeat_interval_seconds=0.0)
        result = worker_main._run_health_poll_loop(config, FakeClient())  # type: ignore[arg-type]
        self.assertEqual(result, 0)
        self.assertEqual(calls, ["register:worker-1", "health"])

    def test_registration_failure_does_not_prevent_the_poll_loop_from_starting(
        self,
    ) -> None:
        calls: list[str] = []

        class FailingRegisterClient:
            def register_worker(self, spec: object, worker_id: str, now: float) -> None:
                raise CoordinatorUnavailableError("simulated: coordinator not up yet")

            def health(self) -> str:
                calls.append("health")
                raise KeyboardInterrupt

        config = WorkerConfig(worker_id="worker-1", heartbeat_interval_seconds=0.0)
        result = worker_main._run_health_poll_loop(
            config,
            FailingRegisterClient(),  # type: ignore[arg-type]
        )
        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            ["health"],
            "the health-poll loop must still run even when startup registration fails",
        )


if __name__ == "__main__":
    unittest.main()
