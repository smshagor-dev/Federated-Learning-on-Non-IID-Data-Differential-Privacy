"""Shared, versioned security-event schema -- Security Events, Metrics,
and Durable Audit Journal slice, Work Package B/C. See
docs/security-events.md and docs/security-observability-inventory.md.

Mirrors, field-for-field,
cpp/coordinator/include/fl_coordinator/security_event.hpp and
go/internal/observability/security_event.go. Enum values are plain
string constants (not a Python Enum) so they round-trip through JSON/
logs without a lookup table, matching
go/internal/security.Permission's string-typed convention.

A SecurityEvent never carries: private keys, raw signatures, raw
nonces, raw certificate PEM, full signed payloads, tensor values,
dataset samples, privacy noise, or secret-share data -- only
identifiers, small bounded strings, and a handful of scalars. Every
``safe_*`` field name is a reminder: put an opaque ID or short label
there, never a secret or raw credential.

Canonical serialization follows docs/canonical-security-serialization.md's
established rule (``json.dumps(payload, sort_keys=True,
separators=(",", ":"), ensure_ascii=True)``). This byte-for-byte matches
the C++ encoder (security_event.cpp's ``canonical_security_event_payload_json``)
only for ASCII string content -- the C++ encoder deliberately uses a
simplified per-byte (not full UTF-8-aware) escape for non-ASCII bytes,
since every field in this schema is expected to be an ASCII identifier
or reason code in practice. See security_event.cpp's header comment for
the same caveat stated on that side.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

SCHEMA_VERSION = 1
MAX_REASON_CODE_LENGTH = 128
MAX_DETAIL_KEYS = 10
MAX_DETAIL_VALUE_LENGTH = 256

# -- Severity --------------------------------------------------------------
SEVERITY_INFO = "INFO"
SEVERITY_WARNING = "WARNING"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"
SEVERITIES = frozenset(
    {SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_HIGH, SEVERITY_CRITICAL}
)

# -- Outcome -----------------------------------------------------------------
OUTCOME_ACCEPTED = "ACCEPTED"
OUTCOME_REJECTED = "REJECTED"
OUTCOME_COMPLETED = "COMPLETED"
OUTCOME_FAILED = "FAILED"
OUTCOME_BLOCKED = "BLOCKED"
OUTCOME_CANCELED = "CANCELED"
OUTCOMES = frozenset(
    {
        OUTCOME_ACCEPTED,
        OUTCOME_REJECTED,
        OUTCOME_COMPLETED,
        OUTCOME_FAILED,
        OUTCOME_BLOCKED,
        OUTCOME_CANCELED,
    }
)

# -- Actor type ----------------------------------------------------------
ACTOR_TYPE_USER = "USER"
ACTOR_TYPE_SERVICE = "SERVICE"
ACTOR_TYPE_WORKER = "WORKER"
ACTOR_TYPE_COORDINATOR = "COORDINATOR"
ACTOR_TYPE_SYSTEM = "SYSTEM"
ACTOR_TYPES = frozenset(
    {
        ACTOR_TYPE_USER,
        ACTOR_TYPE_SERVICE,
        ACTOR_TYPE_WORKER,
        ACTOR_TYPE_COORDINATOR,
        ACTOR_TYPE_SYSTEM,
    }
)

# -- Subject type --------------------------------------------------------
SUBJECT_TYPE_TRANSPORT = "TRANSPORT"
SUBJECT_TYPE_CERTIFICATE = "CERTIFICATE"
SUBJECT_TYPE_WORKER_IDENTITY = "WORKER_IDENTITY"
SUBJECT_TYPE_WORKER_SIGNING_KEY = "WORKER_SIGNING_KEY"
SUBJECT_TYPE_COORDINATOR_SIGNING_KEY = "COORDINATOR_SIGNING_KEY"
SUBJECT_TYPE_CAPABILITY = "CAPABILITY"
SUBJECT_TYPE_HEARTBEAT = "HEARTBEAT"
SUBJECT_TYPE_CLIENT_RESULT = "CLIENT_RESULT"
SUBJECT_TYPE_PRIVACY_RECORD = "PRIVACY_RECORD"
SUBJECT_TYPE_TRAINING_TASK = "TRAINING_TASK"
SUBJECT_TYPE_REPLAY_STATE = "REPLAY_STATE"
SUBJECT_TYPE_TASK_LEASE = "TASK_LEASE"
SUBJECT_TYPE_AUDIT_QUERY = "AUDIT_QUERY"
SUBJECT_TYPE_SECURITY_MUTATION = "SECURITY_MUTATION"
# Secure User-Level DP Operations, Observability, and Release Evidence
# slice, Work Area B -- mirrors
# fl::coordinator::SecuritySubjectType::kSecureAggregationSession
# (cpp/coordinator/include/fl_coordinator/security_event.hpp), added here
# for the first time because no worker-side secure-aggregation event has
# ever been emitted from Python before this slice.
SUBJECT_TYPE_SECURE_AGGREGATION_SESSION = "SECURE_AGGREGATION_SESSION"
SUBJECT_TYPES = frozenset(
    {
        SUBJECT_TYPE_TRANSPORT,
        SUBJECT_TYPE_CERTIFICATE,
        SUBJECT_TYPE_WORKER_IDENTITY,
        SUBJECT_TYPE_WORKER_SIGNING_KEY,
        SUBJECT_TYPE_COORDINATOR_SIGNING_KEY,
        SUBJECT_TYPE_CAPABILITY,
        SUBJECT_TYPE_HEARTBEAT,
        SUBJECT_TYPE_CLIENT_RESULT,
        SUBJECT_TYPE_PRIVACY_RECORD,
        SUBJECT_TYPE_TRAINING_TASK,
        SUBJECT_TYPE_REPLAY_STATE,
        SUBJECT_TYPE_TASK_LEASE,
        SUBJECT_TYPE_AUDIT_QUERY,
        SUBJECT_TYPE_SECURITY_MUTATION,
        SUBJECT_TYPE_SECURE_AGGREGATION_SESSION,
    }
)

# -- Event type registry (Work Package C) -----------------------------------
EVENT_TRANSPORT_MTLS_STARTED = "TRANSPORT_MTLS_STARTED"
EVENT_TRANSPORT_MTLS_FAILED = "TRANSPORT_MTLS_FAILED"
EVENT_TRANSPORT_INSECURE_DEVELOPMENT_STARTED = "TRANSPORT_INSECURE_DEVELOPMENT_STARTED"
EVENT_PEER_CERTIFICATE_ACCEPTED = "PEER_CERTIFICATE_ACCEPTED"
EVENT_PEER_CERTIFICATE_REJECTED = "PEER_CERTIFICATE_REJECTED"
EVENT_CERTIFICATE_IDENTITY_MISMATCH = "CERTIFICATE_IDENTITY_MISMATCH"
EVENT_CERTIFICATE_FINGERPRINT_REJECTED = "CERTIFICATE_FINGERPRINT_REJECTED"
EVENT_CERTIFICATE_EXPIRED = "CERTIFICATE_EXPIRED"

EVENT_WORKER_REGISTERED = "WORKER_REGISTERED"
EVENT_WORKER_REGISTRATION_REJECTED = "WORKER_REGISTRATION_REJECTED"
EVENT_WORKER_SUSPENDED = "WORKER_SUSPENDED"
EVENT_WORKER_ACTIVATED = "WORKER_ACTIVATED"
EVENT_WORKER_REVOKED = "WORKER_REVOKED"
EVENT_WORKER_STATUS_RPC_REJECTED = "WORKER_STATUS_RPC_REJECTED"
EVENT_ACTIVE_LEASE_CANCELED = "ACTIVE_LEASE_CANCELED"

EVENT_WORKER_KEY_MIGRATED = "WORKER_KEY_MIGRATED"
EVENT_WORKER_KEY_REGISTERED = "WORKER_KEY_REGISTERED"
EVENT_WORKER_KEY_ROTATION_REQUESTED = "WORKER_KEY_ROTATION_REQUESTED"
EVENT_WORKER_KEY_ROTATION_ACCEPTED = "WORKER_KEY_ROTATION_ACCEPTED"
EVENT_WORKER_KEY_ROTATION_REJECTED = "WORKER_KEY_ROTATION_REJECTED"
EVENT_WORKER_KEY_GRACE_STARTED = "WORKER_KEY_GRACE_STARTED"
EVENT_WORKER_KEY_EXPIRED = "WORKER_KEY_EXPIRED"
EVENT_WORKER_KEY_REVOKED = "WORKER_KEY_REVOKED"
EVENT_MESSAGE_REJECTED_BY_KEY_STATE = "MESSAGE_REJECTED_BY_KEY_STATE"

EVENT_CAPABILITY_ACCEPTED = "CAPABILITY_ACCEPTED"
EVENT_CAPABILITY_REJECTED = "CAPABILITY_REJECTED"
EVENT_HEARTBEAT_ACCEPTED = "HEARTBEAT_ACCEPTED"
EVENT_HEARTBEAT_REJECTED = "HEARTBEAT_REJECTED"
EVENT_CLIENT_RESULT_ACCEPTED = "CLIENT_RESULT_ACCEPTED"
EVENT_CLIENT_RESULT_REJECTED = "CLIENT_RESULT_REJECTED"
EVENT_PRIVACY_RECORD_ACCEPTED = "PRIVACY_RECORD_ACCEPTED"
EVENT_PRIVACY_RECORD_REJECTED = "PRIVACY_RECORD_REJECTED"
EVENT_SIGNATURE_VERIFICATION_FAILED = "SIGNATURE_VERIFICATION_FAILED"
EVENT_PAYLOAD_HASH_MISMATCH = "PAYLOAD_HASH_MISMATCH"
EVENT_MESSAGE_REPLAY_REJECTED = "MESSAGE_REPLAY_REJECTED"
EVENT_MESSAGE_SEQUENCE_REJECTED = "MESSAGE_SEQUENCE_REJECTED"
EVENT_MESSAGE_EXPIRED = "MESSAGE_EXPIRED"

EVENT_COORDINATOR_SIGNING_KEY_LOADED = "COORDINATOR_SIGNING_KEY_LOADED"
EVENT_COORDINATOR_SIGNING_KEY_REJECTED = "COORDINATOR_SIGNING_KEY_REJECTED"
EVENT_COORDINATOR_TASK_SIGNED = "COORDINATOR_TASK_SIGNED"
EVENT_COORDINATOR_TASK_SIGNING_FAILED = "COORDINATOR_TASK_SIGNING_FAILED"
EVENT_COORDINATOR_TASK_ISSUED = "COORDINATOR_TASK_ISSUED"
EVENT_COORDINATOR_TASK_VERIFIED = "COORDINATOR_TASK_VERIFIED"
EVENT_COORDINATOR_TASK_REJECTED = "COORDINATOR_TASK_REJECTED"
EVENT_COORDINATOR_TASK_REPLAY_REJECTED = "COORDINATOR_TASK_REPLAY_REJECTED"
EVENT_DUPLICATE_TASK_EXECUTION_BLOCKED = "DUPLICATE_TASK_EXECUTION_BLOCKED"
EVENT_ACCEPTED_TASK_RECOVERY_STARTED = "ACCEPTED_TASK_RECOVERY_STARTED"
EVENT_ACCEPTED_TASK_RECOVERY_COMPLETED = "ACCEPTED_TASK_RECOVERY_COMPLETED"
EVENT_TASK_REISSUED = "TASK_REISSUED"

EVENT_SECURITY_PERMISSION_DENIED = "SECURITY_PERMISSION_DENIED"
EVENT_SECURITY_MUTATION_ACCEPTED = "SECURITY_MUTATION_ACCEPTED"
EVENT_SECURITY_MUTATION_REJECTED = "SECURITY_MUTATION_REJECTED"
EVENT_IDEMPOTENCY_REPLAY_ACCEPTED = "IDEMPOTENCY_REPLAY_ACCEPTED"
EVENT_IDEMPOTENCY_CONFLICT_REJECTED = "IDEMPOTENCY_CONFLICT_REJECTED"
EVENT_SECURITY_AUDIT_ACCESSED = "SECURITY_AUDIT_ACCESSED"

# Secure User-Level DP Operations, Observability, and Release Evidence
# slice, Work Area B/C -- mirrors the bounded, representative subset of
# SecurityEventType added to
# cpp/coordinator/include/fl_coordinator/security_event.hpp. Only the
# event a worker process itself performs is emitted from here
# (worker-side clipping); attestation accept/reject is the coordinator's
# own verification decision and is emitted only by C++
# (source_service="coordinator") -- emitting a second, worker-side
# self-report of the same decision would be exactly the semantically
# duplicate event this slice's own Work Area D instruction forbids.
EVENT_SECURE_USER_LEVEL_DP_CLIPPING_APPLIED = "SECURE_USER_LEVEL_DP_CLIPPING_APPLIED"

# Default severity per event type (Work Package C). Mirrors
# cpp/coordinator/src/security_event.cpp's default_severity switch
# exactly -- keep the two in sync by hand (no shared codegen exists for
# this mapping).
_CRITICAL_EVENTS = frozenset(
    {EVENT_COORDINATOR_TASK_SIGNING_FAILED, EVENT_COORDINATOR_SIGNING_KEY_REJECTED}
)
_HIGH_EVENTS = frozenset(
    {
        EVENT_PEER_CERTIFICATE_REJECTED,
        EVENT_CERTIFICATE_IDENTITY_MISMATCH,
        EVENT_CERTIFICATE_FINGERPRINT_REJECTED,
        EVENT_WORKER_REVOKED,
        EVENT_WORKER_KEY_REVOKED,
        EVENT_SIGNATURE_VERIFICATION_FAILED,
        EVENT_PAYLOAD_HASH_MISMATCH,
        EVENT_MESSAGE_REPLAY_REJECTED,
        EVENT_COORDINATOR_TASK_REPLAY_REJECTED,
        EVENT_TRANSPORT_MTLS_FAILED,
    }
)
_WARNING_EVENTS = frozenset(
    {
        EVENT_WORKER_REGISTRATION_REJECTED,
        EVENT_WORKER_SUSPENDED,
        EVENT_WORKER_STATUS_RPC_REJECTED,
        EVENT_WORKER_KEY_ROTATION_REJECTED,
        EVENT_WORKER_KEY_EXPIRED,
        EVENT_MESSAGE_REJECTED_BY_KEY_STATE,
        EVENT_CAPABILITY_REJECTED,
        EVENT_HEARTBEAT_REJECTED,
        EVENT_CLIENT_RESULT_REJECTED,
        EVENT_PRIVACY_RECORD_REJECTED,
        EVENT_MESSAGE_SEQUENCE_REJECTED,
        EVENT_MESSAGE_EXPIRED,
        EVENT_COORDINATOR_TASK_REJECTED,
        EVENT_DUPLICATE_TASK_EXECUTION_BLOCKED,
        EVENT_ACCEPTED_TASK_RECOVERY_STARTED,
        EVENT_TRANSPORT_INSECURE_DEVELOPMENT_STARTED,
        EVENT_SECURITY_PERMISSION_DENIED,
        EVENT_SECURITY_MUTATION_REJECTED,
        EVENT_IDEMPOTENCY_CONFLICT_REJECTED,
    }
)


def default_severity(event_type: str) -> str:
    if event_type in _CRITICAL_EVENTS:
        return SEVERITY_CRITICAL
    if event_type in _HIGH_EVENTS:
        return SEVERITY_HIGH
    if event_type in _WARNING_EVENTS:
        return SEVERITY_WARNING
    return SEVERITY_INFO


@dataclass(slots=True)
class SecurityEvent:
    source_service: str
    event_type: str
    schema_version: int = SCHEMA_VERSION
    event_id: str = ""  # assigned by the journal on emit(), not the caller
    severity: str = SEVERITY_INFO
    timestamp: str = ""  # ISO-8601 UTC, assigned by the journal if empty
    source_component: str = ""
    actor_type: str = ACTOR_TYPE_SYSTEM
    safe_actor_id: str = ""
    subject_type: str = SUBJECT_TYPE_SECURITY_MUTATION
    safe_subject_id: str = ""
    worker_id: str = ""
    run_id: str = ""
    round_id: int = 0
    task_id: str = ""
    safe_signing_key_id: str = ""
    request_id: str = ""
    trace_id: str = ""
    outcome: str = OUTCOME_ACCEPTED
    reason_code: str = ""
    safe_details: dict[str, str] = field(default_factory=dict)
    payload_checksum: str = ""  # assigned by the journal on emit(), not the caller


@dataclass(slots=True, frozen=True)
class SecurityEventValidationResult:
    valid: bool
    reason: str


def validate_security_event(event: SecurityEvent) -> SecurityEventValidationResult:
    if event.schema_version != SCHEMA_VERSION:
        return SecurityEventValidationResult(False, "unsupported schema_version")
    if not event.source_service:
        return SecurityEventValidationResult(False, "source_service is required")
    if event.event_type and event.event_type not in _ALL_EVENT_TYPES:
        return SecurityEventValidationResult(
            False, f"unrecognized event_type '{event.event_type}'"
        )
    if event.severity not in SEVERITIES:
        return SecurityEventValidationResult(
            False, f"unrecognized severity '{event.severity}'"
        )
    if event.outcome not in OUTCOMES:
        return SecurityEventValidationResult(
            False, f"unrecognized outcome '{event.outcome}'"
        )
    if event.actor_type not in ACTOR_TYPES:
        return SecurityEventValidationResult(
            False, f"unrecognized actor_type '{event.actor_type}'"
        )
    if event.subject_type not in SUBJECT_TYPES:
        return SecurityEventValidationResult(
            False, f"unrecognized subject_type '{event.subject_type}'"
        )
    if len(event.reason_code) > MAX_REASON_CODE_LENGTH:
        return SecurityEventValidationResult(
            False, "reason_code exceeds maximum length"
        )
    if len(event.safe_details) > MAX_DETAIL_KEYS:
        return SecurityEventValidationResult(False, "safe_details has too many keys")
    for key, value in event.safe_details.items():
        if len(value) > MAX_DETAIL_VALUE_LENGTH:
            return SecurityEventValidationResult(
                False, f"safe_details value exceeds maximum length for key '{key}'"
            )
    return SecurityEventValidationResult(True, "ok")


_ALL_EVENT_TYPES = frozenset(
    {
        EVENT_TRANSPORT_MTLS_STARTED,
        EVENT_TRANSPORT_MTLS_FAILED,
        EVENT_TRANSPORT_INSECURE_DEVELOPMENT_STARTED,
        EVENT_PEER_CERTIFICATE_ACCEPTED,
        EVENT_PEER_CERTIFICATE_REJECTED,
        EVENT_CERTIFICATE_IDENTITY_MISMATCH,
        EVENT_CERTIFICATE_FINGERPRINT_REJECTED,
        EVENT_CERTIFICATE_EXPIRED,
        EVENT_WORKER_REGISTERED,
        EVENT_WORKER_REGISTRATION_REJECTED,
        EVENT_WORKER_SUSPENDED,
        EVENT_WORKER_ACTIVATED,
        EVENT_WORKER_REVOKED,
        EVENT_WORKER_STATUS_RPC_REJECTED,
        EVENT_ACTIVE_LEASE_CANCELED,
        EVENT_WORKER_KEY_MIGRATED,
        EVENT_WORKER_KEY_REGISTERED,
        EVENT_WORKER_KEY_ROTATION_REQUESTED,
        EVENT_WORKER_KEY_ROTATION_ACCEPTED,
        EVENT_WORKER_KEY_ROTATION_REJECTED,
        EVENT_WORKER_KEY_GRACE_STARTED,
        EVENT_WORKER_KEY_EXPIRED,
        EVENT_WORKER_KEY_REVOKED,
        EVENT_MESSAGE_REJECTED_BY_KEY_STATE,
        EVENT_CAPABILITY_ACCEPTED,
        EVENT_CAPABILITY_REJECTED,
        EVENT_HEARTBEAT_ACCEPTED,
        EVENT_HEARTBEAT_REJECTED,
        EVENT_CLIENT_RESULT_ACCEPTED,
        EVENT_CLIENT_RESULT_REJECTED,
        EVENT_PRIVACY_RECORD_ACCEPTED,
        EVENT_PRIVACY_RECORD_REJECTED,
        EVENT_SIGNATURE_VERIFICATION_FAILED,
        EVENT_PAYLOAD_HASH_MISMATCH,
        EVENT_MESSAGE_REPLAY_REJECTED,
        EVENT_MESSAGE_SEQUENCE_REJECTED,
        EVENT_MESSAGE_EXPIRED,
        EVENT_COORDINATOR_SIGNING_KEY_LOADED,
        EVENT_COORDINATOR_SIGNING_KEY_REJECTED,
        EVENT_COORDINATOR_TASK_SIGNED,
        EVENT_COORDINATOR_TASK_SIGNING_FAILED,
        EVENT_COORDINATOR_TASK_ISSUED,
        EVENT_COORDINATOR_TASK_VERIFIED,
        EVENT_COORDINATOR_TASK_REJECTED,
        EVENT_COORDINATOR_TASK_REPLAY_REJECTED,
        EVENT_DUPLICATE_TASK_EXECUTION_BLOCKED,
        EVENT_ACCEPTED_TASK_RECOVERY_STARTED,
        EVENT_ACCEPTED_TASK_RECOVERY_COMPLETED,
        EVENT_TASK_REISSUED,
        EVENT_SECURITY_PERMISSION_DENIED,
        EVENT_SECURITY_MUTATION_ACCEPTED,
        EVENT_SECURITY_MUTATION_REJECTED,
        EVENT_IDEMPOTENCY_REPLAY_ACCEPTED,
        EVENT_IDEMPOTENCY_CONFLICT_REJECTED,
        EVENT_SECURITY_AUDIT_ACCESSED,
        EVENT_SECURE_USER_LEVEL_DP_CLIPPING_APPLIED,
    }
)


def canonical_security_event_payload_json(event: SecurityEvent) -> str:
    """Canonical JSON encoding of every field except event_id/timestamp/
    payload_checksum -- see this module's docstring for the exact rule
    and its cross-language-parity caveat."""
    payload = {
        "actor_type": event.actor_type,
        "event_type": event.event_type,
        "outcome": event.outcome,
        "reason_code": event.reason_code,
        "request_id": event.request_id,
        "round_id": event.round_id,
        "run_id": event.run_id,
        "safe_actor_id": event.safe_actor_id,
        "safe_details": dict(event.safe_details),
        "safe_signing_key_id": event.safe_signing_key_id,
        "safe_subject_id": event.safe_subject_id,
        "schema_version": event.schema_version,
        "severity": event.severity,
        "source_component": event.source_component,
        "source_service": event.source_service,
        "subject_type": event.subject_type,
        "task_id": event.task_id,
        "trace_id": event.trace_id,
        "worker_id": event.worker_id,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _fnv1a_hash(data: bytes) -> int:
    # 64-bit FNV-1a -- same algorithm/constants as every checksum in this
    # codebase's C++ persistence layer (idempotency_store.cpp,
    # security_event.cpp, etc.). Corruption/tamper-in-transit detection
    # only, explicitly not a cryptographic MAC.
    hash_value = 1469598103934665603
    mask = (1 << 64) - 1
    for byte in data:
        hash_value ^= byte
        hash_value = (hash_value * 1099511628211) & mask
    return hash_value


def compute_security_event_checksum(event: SecurityEvent) -> str:
    payload = canonical_security_event_payload_json(event).encode("utf-8")
    return format(_fnv1a_hash(payload), "016x")
