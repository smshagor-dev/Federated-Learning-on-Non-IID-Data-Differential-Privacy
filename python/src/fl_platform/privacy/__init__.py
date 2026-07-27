"""Privacy Engineering: sample-level DP (Opacus), user-level DP
(dedicated RDP accountant + coordinator-side clipping/noise), and
adaptive clipping (itself a DP mechanism). See docs/privacy-mathematics.md
for the Critical Privacy Rule these modules exist to enforce: separate
epsilon/delta per mechanism, never combined.
"""

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
