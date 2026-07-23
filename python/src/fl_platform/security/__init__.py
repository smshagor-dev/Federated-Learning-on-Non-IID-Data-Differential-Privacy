"""Security groundwork scaffolds."""

from .audit import AuditEvent, AuditLog
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

__all__ = [
    "AuditEvent",
    "AuditLog",
    "EnvelopeValidationResult",
    "NonceReplayGuard",
    "SecureAggregationConfig",
    "SecureAggregationValidationResult",
    "SignedEnvelope",
    "sign_envelope",
    "validate_secure_aggregation_config",
    "verify_envelope",
]
