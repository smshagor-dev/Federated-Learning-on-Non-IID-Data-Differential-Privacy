"""Privacy engineering public API.

The package namespace is intentionally lazy. Lightweight consumers such as the
privacy ledger and v3 validation helpers must not import optional Opacus,
metrics, or the legacy client-level accountant just by importing the package.
Concrete modules are loaded only when their public symbols are requested.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .accounting import (
        AdaptiveClippingAccountant,
        SampleLevelAccountant,
        UserLevelAccountant,
        UserLevelAccountantState,
        opacus_capabilities,
    )
    from .adaptive_clipping import (
        AdaptiveClipConfig,
        AdaptiveClipController,
        AdaptiveClipStepResult,
    )
    from .compatibility import (
        ALGORITHMS,
        SAMPLE_LEVEL_DP_COMPATIBILITY,
        USER_LEVEL_DP_COMPATIBILITY,
        CompatibilityEntry,
        CompatibilityStatus,
        hybrid_status,
        is_usable,
        sample_level_status,
        user_level_status,
    )
    from .config import (
        PRIVACY_SAFE_WEIGHTING_STRATEGIES,
        SUPPORTED_ACCOUNTANTS,
        AdaptiveClippingConfig,
        PrivacyBudgetPolicy,
        PrivacyConfig,
        PrivacyMode,
        PrivacyValidationResult,
        SampleLevelDPConfig,
        SamplePrivacyBudgetPolicy,
        UserLevelDPConfig,
        build_privacy_config,
        validate_privacy_config,
    )
    from .ledger import (
        AdaptiveClippingLedgerEntry,
        MechanismProjection,
        PrivacyLedger,
        PrivacyProjection,
        SampleLevelLedgerEntry,
        UserLevelLedgerEntry,
    )
    from .metrics import (
        ensure_metrics_server_started,
        record_sample_level_training_rejected,
        record_sample_level_training_success,
    )
    from .secure_random import (
        SecureRandomTaskRejectedError,
        SecureRandomUnavailableError,
        opacus_secure_mode_available,
        require_opacus_secure_mode,
        require_secure_random,
        secure_random_available,
        worker_reports_secure_random_support,
    )

__all__ = [
    "ALGORITHMS",
    "PRIVACY_SAFE_WEIGHTING_STRATEGIES",
    "SAMPLE_LEVEL_DP_COMPATIBILITY",
    "SUPPORTED_ACCOUNTANTS",
    "USER_LEVEL_DP_COMPATIBILITY",
    "AdaptiveClipConfig",
    "AdaptiveClipController",
    "AdaptiveClipStepResult",
    "AdaptiveClippingAccountant",
    "AdaptiveClippingConfig",
    "AdaptiveClippingLedgerEntry",
    "CompatibilityEntry",
    "CompatibilityStatus",
    "MechanismProjection",
    "PrivacyBudgetPolicy",
    "PrivacyConfig",
    "PrivacyLedger",
    "PrivacyMode",
    "PrivacyProjection",
    "PrivacyValidationResult",
    "SampleLevelAccountant",
    "SampleLevelDPConfig",
    "SampleLevelLedgerEntry",
    "SamplePrivacyBudgetPolicy",
    "SecureRandomTaskRejectedError",
    "SecureRandomUnavailableError",
    "UserLevelAccountant",
    "UserLevelAccountantState",
    "UserLevelDPConfig",
    "UserLevelLedgerEntry",
    "build_privacy_config",
    "ensure_metrics_server_started",
    "hybrid_status",
    "is_usable",
    "opacus_capabilities",
    "opacus_secure_mode_available",
    "record_sample_level_training_rejected",
    "record_sample_level_training_success",
    "require_opacus_secure_mode",
    "require_secure_random",
    "sample_level_status",
    "secure_random_available",
    "user_level_status",
    "validate_privacy_config",
    "worker_reports_secure_random_support",
]

_LAZY_EXPORTS = {
    "AdaptiveClippingAccountant": ("accounting", "AdaptiveClippingAccountant"),
    "SampleLevelAccountant": ("accounting", "SampleLevelAccountant"),
    "UserLevelAccountant": ("accounting", "UserLevelAccountant"),
    "UserLevelAccountantState": ("accounting", "UserLevelAccountantState"),
    "opacus_capabilities": ("accounting", "opacus_capabilities"),
    "AdaptiveClipConfig": ("adaptive_clipping", "AdaptiveClipConfig"),
    "AdaptiveClipController": ("adaptive_clipping", "AdaptiveClipController"),
    "AdaptiveClipStepResult": ("adaptive_clipping", "AdaptiveClipStepResult"),
    "ALGORITHMS": ("compatibility", "ALGORITHMS"),
    "SAMPLE_LEVEL_DP_COMPATIBILITY": (
        "compatibility",
        "SAMPLE_LEVEL_DP_COMPATIBILITY",
    ),
    "USER_LEVEL_DP_COMPATIBILITY": (
        "compatibility",
        "USER_LEVEL_DP_COMPATIBILITY",
    ),
    "CompatibilityEntry": ("compatibility", "CompatibilityEntry"),
    "CompatibilityStatus": ("compatibility", "CompatibilityStatus"),
    "hybrid_status": ("compatibility", "hybrid_status"),
    "is_usable": ("compatibility", "is_usable"),
    "sample_level_status": ("compatibility", "sample_level_status"),
    "user_level_status": ("compatibility", "user_level_status"),
    "PRIVACY_SAFE_WEIGHTING_STRATEGIES": (
        "config",
        "PRIVACY_SAFE_WEIGHTING_STRATEGIES",
    ),
    "SUPPORTED_ACCOUNTANTS": ("config", "SUPPORTED_ACCOUNTANTS"),
    "AdaptiveClippingConfig": ("config", "AdaptiveClippingConfig"),
    "PrivacyBudgetPolicy": ("config", "PrivacyBudgetPolicy"),
    "PrivacyConfig": ("config", "PrivacyConfig"),
    "PrivacyMode": ("config", "PrivacyMode"),
    "PrivacyValidationResult": ("config", "PrivacyValidationResult"),
    "SampleLevelDPConfig": ("config", "SampleLevelDPConfig"),
    "SamplePrivacyBudgetPolicy": ("config", "SamplePrivacyBudgetPolicy"),
    "UserLevelDPConfig": ("config", "UserLevelDPConfig"),
    "build_privacy_config": ("config", "build_privacy_config"),
    "validate_privacy_config": ("config", "validate_privacy_config"),
    "AdaptiveClippingLedgerEntry": ("ledger", "AdaptiveClippingLedgerEntry"),
    "MechanismProjection": ("ledger", "MechanismProjection"),
    "PrivacyLedger": ("ledger", "PrivacyLedger"),
    "PrivacyProjection": ("ledger", "PrivacyProjection"),
    "SampleLevelLedgerEntry": ("ledger", "SampleLevelLedgerEntry"),
    "UserLevelLedgerEntry": ("ledger", "UserLevelLedgerEntry"),
    "ensure_metrics_server_started": ("metrics", "ensure_metrics_server_started"),
    "record_sample_level_training_rejected": (
        "metrics",
        "record_sample_level_training_rejected",
    ),
    "record_sample_level_training_success": (
        "metrics",
        "record_sample_level_training_success",
    ),
    "SecureRandomTaskRejectedError": (
        "secure_random",
        "SecureRandomTaskRejectedError",
    ),
    "SecureRandomUnavailableError": ("secure_random", "SecureRandomUnavailableError"),
    "opacus_secure_mode_available": ("secure_random", "opacus_secure_mode_available"),
    "require_opacus_secure_mode": ("secure_random", "require_opacus_secure_mode"),
    "require_secure_random": ("secure_random", "require_secure_random"),
    "secure_random_available": ("secure_random", "secure_random_available"),
    "worker_reports_secure_random_support": (
        "secure_random",
        "worker_reports_secure_random_support",
    ),
}


def __getattr__(name: str) -> object:
    try:
        module_name, symbol_name = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f"{__name__}.{module_name}"), symbol_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()).union(_LAZY_EXPORTS))
