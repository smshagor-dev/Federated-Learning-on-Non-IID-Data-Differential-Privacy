"""Tests for fl_platform.security.security_event -- Security Events,
Metrics, and Durable Audit Journal slice."""

from __future__ import annotations

import unittest

from fl_platform.security.security_event import (
    ACTOR_TYPE_SERVICE,
    EVENT_COORDINATOR_TASK_SIGNING_FAILED,
    EVENT_MESSAGE_REPLAY_REJECTED,
    EVENT_WORKER_REGISTERED,
    EVENT_WORKER_SUSPENDED,
    MAX_DETAIL_KEYS,
    MAX_DETAIL_VALUE_LENGTH,
    MAX_REASON_CODE_LENGTH,
    OUTCOME_COMPLETED,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SUBJECT_TYPE_WORKER_IDENTITY,
    SecurityEvent,
    canonical_security_event_payload_json,
    compute_security_event_checksum,
    default_severity,
    validate_security_event,
)


class DefaultSeverityTests(unittest.TestCase):
    def test_critical_mapping(self) -> None:
        self.assertEqual(
            default_severity(EVENT_COORDINATOR_TASK_SIGNING_FAILED), SEVERITY_CRITICAL
        )

    def test_high_mapping(self) -> None:
        self.assertEqual(default_severity(EVENT_MESSAGE_REPLAY_REJECTED), SEVERITY_HIGH)

    def test_warning_mapping(self) -> None:
        self.assertEqual(default_severity(EVENT_WORKER_SUSPENDED), SEVERITY_WARNING)

    def test_info_default(self) -> None:
        self.assertEqual(default_severity(EVENT_WORKER_REGISTERED), SEVERITY_INFO)


class ValidationTests(unittest.TestCase):
    def test_minimal_event_is_valid(self) -> None:
        event = SecurityEvent(
            source_service="coordinator", event_type=EVENT_WORKER_REGISTERED
        )
        result = validate_security_event(event)
        self.assertTrue(result.valid, result.reason)

    def test_missing_source_service_is_invalid(self) -> None:
        event = SecurityEvent(source_service="", event_type=EVENT_WORKER_REGISTERED)
        self.assertFalse(validate_security_event(event).valid)

    def test_unrecognized_severity_is_invalid(self) -> None:
        event = SecurityEvent(
            source_service="coordinator", event_type=EVENT_WORKER_REGISTERED
        )
        event.severity = "NOT_A_REAL_SEVERITY"
        self.assertFalse(validate_security_event(event).valid)

    def test_reason_code_length_bound(self) -> None:
        event = SecurityEvent(
            source_service="coordinator", event_type=EVENT_WORKER_REGISTERED
        )
        event.reason_code = "x" * (MAX_REASON_CODE_LENGTH + 1)
        self.assertFalse(validate_security_event(event).valid)

    def test_too_many_detail_keys(self) -> None:
        event = SecurityEvent(
            source_service="coordinator", event_type=EVENT_WORKER_REGISTERED
        )
        event.safe_details = {f"k{i}": "v" for i in range(MAX_DETAIL_KEYS + 1)}
        self.assertFalse(validate_security_event(event).valid)

    def test_detail_value_length_bound(self) -> None:
        event = SecurityEvent(
            source_service="coordinator", event_type=EVENT_WORKER_REGISTERED
        )
        event.safe_details = {"k": "x" * (MAX_DETAIL_VALUE_LENGTH + 1)}
        self.assertFalse(validate_security_event(event).valid)


def _fixture_event() -> SecurityEvent:
    return SecurityEvent(
        source_service="coordinator",
        source_component="worker_registry",
        event_type=EVENT_WORKER_SUSPENDED,
        severity=SEVERITY_WARNING,
        actor_type=ACTOR_TYPE_SERVICE,
        safe_actor_id="go-api",
        subject_type=SUBJECT_TYPE_WORKER_IDENTITY,
        safe_subject_id="worker-1",
        worker_id="worker-1",
        outcome=OUTCOME_COMPLETED,
        reason_code="administrative_suspension",
    )


class CanonicalSerializationTests(unittest.TestCase):
    def test_checksum_is_deterministic(self) -> None:
        event = _fixture_event()
        self.assertEqual(
            compute_security_event_checksum(event),
            compute_security_event_checksum(event),
        )

    def test_changing_a_checksummed_field_changes_the_checksum(self) -> None:
        event = _fixture_event()
        checksum = compute_security_event_checksum(event)
        event.reason_code = "different_reason"
        self.assertNotEqual(compute_security_event_checksum(event), checksum)

    def test_event_id_and_timestamp_are_excluded_from_the_checksum(self) -> None:
        event = _fixture_event()
        checksum = compute_security_event_checksum(event)
        event.event_id = "00000000000000000042"
        event.timestamp = "2026-01-01T00:00:00Z"
        self.assertEqual(compute_security_event_checksum(event), checksum)

    def test_canonical_payload_never_includes_event_id_or_checksum(self) -> None:
        payload = canonical_security_event_payload_json(_fixture_event())
        self.assertNotIn('"event_id"', payload)
        self.assertNotIn('"payload_checksum"', payload)

    def test_cross_language_golden_fixture(self) -> None:
        """A real, independently-generated cross-language fixture, not a
        tautological self-check: the expected canonical JSON string and
        checksum below were produced by *actually running* a small C++
        program linked against fl_coordinator (security_event.cpp's
        canonical_security_event_payload_json/compute_security_event_checksum)
        over this exact fixture event, then pasted verbatim here -- the
        same "paste a real golden vector" methodology
        docs/canonical-security-serialization.md documents for
        capability_statement_verifier_test.cpp's kGoldenPayloadJson. If
        the two encoders ever disagreed on a single byte, this assertion
        would fail first."""
        event = _fixture_event()
        expected_json = (
            '{"actor_type":"SERVICE","event_type":"WORKER_SUSPENDED","outcome":"COMPLETED",'
            '"reason_code":"administrative_suspension","request_id":"","round_id":0,"run_id":"",'
            '"safe_actor_id":"go-api","safe_details":{},"safe_signing_key_id":"",'
            '"safe_subject_id":"worker-1","schema_version":1,"severity":"WARNING",'
            '"source_component":"worker_registry","source_service":"coordinator",'
            '"subject_type":"WORKER_IDENTITY","task_id":"","trace_id":"","worker_id":"worker-1"}'
        )
        expected_checksum = "2a1507521d258521"
        self.assertEqual(canonical_security_event_payload_json(event), expected_json)
        self.assertEqual(compute_security_event_checksum(event), expected_checksum)


if __name__ == "__main__":
    unittest.main()
