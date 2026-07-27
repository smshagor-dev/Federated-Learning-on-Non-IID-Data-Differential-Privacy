"""Tests for fl_platform.security.signed_envelope's
WorkerSecurityEventFields/WorkerSecurityEventBatchFields/
security_event_batch_payload_hash_input -- Web Security Center, Event
Centralization, and Security CI slice, Work Package L. See
docs/security-event-centralization.md.

Mirrors test_signed_envelope.py's RotationHashTests structure exactly:
canonical sort order, a cross-language golden fixture (also embedded in
cpp/coordinator/tests/signed_envelope_verifier_test.cpp's
kGoldenBatchJson), determinism, tamper detection, and a signature round
trip. This module never verifies signatures (only the C++ coordinator
does) -- accept/reject behavior against a live coordinator is covered
by coordinator_service_test.cpp's SubmitWorkerSecurityEvents block.
"""

from __future__ import annotations

import unittest

from fl_platform.security.signed_envelope import (
    MESSAGE_STREAM_SECURITY_EVENTS,
    MESSAGE_TYPE_SECURITY_EVENT_BATCH,
    EnvelopeFields,
    WorkerSecurityEventBatchFields,
    WorkerSecurityEventFields,
    security_event_batch_payload_hash_input,
    sha256_hex,
    sign_envelope,
)
from fl_platform.security.signing_identity import generate_signing_identity


def _event_fields(**overrides) -> WorkerSecurityEventFields:
    defaults = {
        "event_type": "WORKER_KEY_ROTATION_ACCEPTED",
        "severity": "INFO",
        "timestamp": "2026-01-01T00:00:00Z",
        "actor_type": "WORKER",
        "safe_actor_id": "worker-1",
        "subject_type": "WORKER_SIGNING_KEY",
        "safe_subject_id": "key-2",
        "outcome": "ACCEPTED",
        "source_component": "signing_key_rotation",
        "safe_signing_key_id": "key-2",
        "safe_details": {"previous_key": "key-1"},
    }
    defaults.update(overrides)
    return WorkerSecurityEventFields(**defaults)


def _batch_fields(**overrides) -> WorkerSecurityEventBatchFields:
    defaults = {
        "worker_id": "worker-1",
        "events": (_event_fields(),),
        "queue_depth_hint": 2,
    }
    defaults.update(overrides)
    return WorkerSecurityEventBatchFields(**defaults)


class SecurityEventBatchHashTests(unittest.TestCase):
    def test_canonical_bytes_are_sorted_alphabetically(self) -> None:
        hash_input = security_event_batch_payload_hash_input(_batch_fields())
        self.assertLess(hash_input.find('"events"'), hash_input.find('"worker_id"'))

    def test_golden_hash_matches_the_cross_language_fixture(self) -> None:
        # This exact string is also embedded in
        # signed_envelope_verifier_test.cpp's kGoldenBatchJson.
        hash_input = security_event_batch_payload_hash_input(_batch_fields())
        expected = (
            '{"events":[{"actor_type":"WORKER","event_type":"WORKER_KEY_ROTATION_ACCEPTED",'
            '"outcome":"ACCEPTED","reason_code":"","request_id":"","round_id":0,'
            '"run_id":"","safe_actor_id":"worker-1","safe_details":{"previous_key":'
            '"key-1"},"safe_signing_key_id":"key-2","safe_subject_id":"key-2",'
            '"schema_version":1,"severity":"INFO","source_component":'
            '"signing_key_rotation","subject_type":"WORKER_SIGNING_KEY","task_id":"",'
            '"timestamp":"2026-01-01T00:00:00Z","trace_id":""}],"queue_depth_hint":2,'
            '"schema_version":1,"worker_id":"worker-1"}'
        )
        self.assertEqual(hash_input, expected)

    def test_determinism(self) -> None:
        a = security_event_batch_payload_hash_input(_batch_fields())
        b = security_event_batch_payload_hash_input(_batch_fields())
        self.assertEqual(a, b)

    def test_adding_an_event_changes_the_hash(self) -> None:
        original = security_event_batch_payload_hash_input(_batch_fields())
        with_extra = security_event_batch_payload_hash_input(
            _batch_fields(
                events=(_event_fields(), _event_fields(event_type="HEARTBEAT_ACCEPTED"))
            )
        )
        self.assertNotEqual(original, with_extra)

    def test_event_order_is_preserved_not_sorted(self) -> None:
        first = _event_fields(event_type="HEARTBEAT_ACCEPTED")
        second = _event_fields(event_type="WORKER_KEY_ROTATION_ACCEPTED")
        forward = security_event_batch_payload_hash_input(
            _batch_fields(events=(first, second))
        )
        reversed_order = security_event_batch_payload_hash_input(
            _batch_fields(events=(second, first))
        )
        self.assertNotEqual(
            forward,
            reversed_order,
            "submission order must be part of what gets signed, unlike "
            "client_result's tensor/metric lists which are canonically re-sorted",
        )

    def test_empty_safe_details_canonicalizes_to_empty_object(self) -> None:
        hash_input = security_event_batch_payload_hash_input(
            _batch_fields(events=(_event_fields(safe_details=None),))
        )
        self.assertIn('"safe_details":{}', hash_input)

    def test_valid_signature_round_trip(self) -> None:
        identity = generate_signing_identity("worker-1")
        hash_input = security_event_batch_payload_hash_input(_batch_fields())
        fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_SECURITY_EVENT_BATCH,
            worker_id="worker-1",
            message_stream=MESSAGE_STREAM_SECURITY_EVENTS,
            sequence_number=1,
            signing_key_id=identity.key_id,
            payload_hash=sha256_hex(hash_input),
            issued_at=4000.0,
            expires_at=4060.0,
        )
        signed = sign_envelope(fields, identity)
        self.assertEqual(len(signed.signature_hex), 128)


if __name__ == "__main__":
    unittest.main()
