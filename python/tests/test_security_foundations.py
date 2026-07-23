import unittest

from fl_platform.execution import ExecutionMode
from fl_platform.security import (
    AuditEvent,
    AuditLog,
    NonceReplayGuard,
    SecureAggregationConfig,
    sign_envelope,
    validate_secure_aggregation_config,
    verify_envelope,
)


class SecurityFoundationTests(unittest.TestCase):
    def test_signed_envelope_verifies(self) -> None:
        guard = NonceReplayGuard()
        envelope = sign_envelope(
            payload={"run_id": "run-1", "round_id": 4},
            nonce="nonce-1",
            trace_id="trace-1",
            issued_at="2026-07-22T20:00:00Z",
            secret="top-secret",
        )
        result = verify_envelope(envelope, "top-secret", nonce_guard=guard, scope="run-1")
        self.assertTrue(result.valid)

    def test_replayed_nonce_is_rejected(self) -> None:
        guard = NonceReplayGuard()
        envelope = sign_envelope(
            payload={"run_id": "run-2"},
            nonce="nonce-2",
            trace_id="trace-2",
            issued_at="2026-07-22T20:05:00Z",
            secret="top-secret",
        )
        first = verify_envelope(envelope, "top-secret", nonce_guard=guard, scope="run-2")
        second = verify_envelope(envelope, "top-secret", nonce_guard=guard, scope="run-2")
        self.assertTrue(first.valid)
        self.assertFalse(second.valid)
        self.assertEqual(second.reason, "replayed nonce")

    def test_signature_mismatch_is_rejected(self) -> None:
        envelope = sign_envelope(
            payload={"run_id": "run-3"},
            nonce="nonce-3",
            trace_id="trace-3",
            issued_at="2026-07-22T20:10:00Z",
            secret="secret-a",
        )
        result = verify_envelope(envelope, "secret-b")
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "signature mismatch")

    def test_audit_log_filters(self) -> None:
        log = AuditLog()
        log.append(
            AuditEvent(
                event_type="task.accepted",
                actor_id="worker-1",
                timestamp="2026-07-22T20:15:00Z",
                run_id="run-4",
                round_id=1,
            )
        )
        log.append(
            AuditEvent(
                event_type="task.rejected",
                actor_id="worker-2",
                timestamp="2026-07-22T20:16:00Z",
                run_id="run-4",
                round_id=1,
            )
        )
        self.assertEqual(len(log.filter_by_run("run-4")), 2)
        self.assertEqual(len(log.filter_by_type("task.rejected")), 1)

    def test_secure_aggregation_rejects_async_mode(self) -> None:
        result = validate_secure_aggregation_config(
            SecureAggregationConfig(
                enabled=True,
                minimum_cohort_size=4,
                execution_mode=ExecutionMode.BUFFERED_ASYNCHRONOUS,
            )
        )
        self.assertFalse(result.valid)

    def test_secure_aggregation_semisync_warns(self) -> None:
        result = validate_secure_aggregation_config(
            SecureAggregationConfig(
                enabled=True,
                minimum_cohort_size=4,
                dropout_recovery=True,
                execution_mode=ExecutionMode.DEADLINE_BASED_SEMI_SYNCHRONOUS,
            )
        )
        self.assertTrue(result.valid)
        self.assertGreaterEqual(len(result.warnings), 1)


if __name__ == "__main__":
    unittest.main()
