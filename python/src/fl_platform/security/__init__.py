"""Security groundwork scaffolds, plus the Secure Transport and Worker
Identity Hardening slice's real modules: transport.py (mTLS),
signing_identity.py (Ed25519 worker identities), and
capability_statement.py (signed capability statements)."""

from .audit import AuditEvent, AuditLog
from .capability_statement import (
    SCHEMA_VERSION,
    CapabilityStatementError,
    CapabilityStatementPayload,
    SignedCapabilityStatement,
    VerificationResult,
    sign_capability_statement,
    verify_capability_statement,
)
from .envelope import (
    EnvelopeValidationResult,
    SignedEnvelope,
    sign_envelope,
    verify_envelope,
)
from .nonce import NonceReplayGuard
from .secure_aggregation import (
    SecureAggregationConfig,
    SecureAggregationValidationResult,
    validate_secure_aggregation_config,
)
from .signing_identity import (
    SigningIdentityError,
    WorkerSigningIdentity,
    generate_signing_identity,
    load_signing_identity,
    save_signing_identity,
    verify_key_from_hex,
)
from .transport import (
    TransportConfigurationError,
    TransportMode,
    WorkerTLSConfig,
    build_channel_credentials,
    build_secure_channel,
    transport_mode_for,
)

__all__ = [
    "SCHEMA_VERSION",
    "AuditEvent",
    "AuditLog",
    "CapabilityStatementError",
    "CapabilityStatementPayload",
    "EnvelopeValidationResult",
    "NonceReplayGuard",
    "SecureAggregationConfig",
    "SecureAggregationValidationResult",
    "SignedCapabilityStatement",
    "SignedEnvelope",
    "SigningIdentityError",
    "TransportConfigurationError",
    "TransportMode",
    "VerificationResult",
    "WorkerSigningIdentity",
    "WorkerTLSConfig",
    "build_channel_credentials",
    "build_secure_channel",
    "generate_signing_identity",
    "load_signing_identity",
    "save_signing_identity",
    "sign_capability_statement",
    "sign_envelope",
    "transport_mode_for",
    "validate_secure_aggregation_config",
    "verify_capability_statement",
    "verify_envelope",
    "verify_key_from_hex",
]
