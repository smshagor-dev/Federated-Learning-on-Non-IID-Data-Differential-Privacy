"""Privacy configuration dataclasses — mirror
proto/privacy/privacy.proto's PrivacyConfig/SampleLevelDPConfig/
UserLevelDPConfig/AdaptiveClippingConfig field-for-field, so the Python
worker's config and the wire message never drift. See
docs/privacy-mathematics.md for the Critical Privacy Rule these exist to
enforce: sample-level, user-level, and adaptive-clipping epsilon/delta
are tracked as entirely separate fields everywhere, never summed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class PrivacyMode(StrEnum):
    NONE = "none"
    SAMPLE_LEVEL_DP = "sample_level_dp"
    USER_LEVEL_DP = "user_level_dp"
    HYBRID_DP = "hybrid_dp"


class PrivacyBudgetPolicy(StrEnum):
    WARN_ONLY = "warn_only"
    STOP_BEFORE_EXCEEDING = "stop_before_exceeding"
    STOP_AFTER_CURRENT_ROUND = "stop_after_current_round"
    FAIL_RUN = "fail_run"


class SamplePrivacyBudgetPolicy(StrEnum):
    """Worker-side, per-task policy for SampleLevelDPConfig.epsilon_budget
    — see budget_enforcement.py for the enforcement logic and why this is
    a distinct enum from PrivacyBudgetPolicy above (that one is
    coordinator-side, per-round; this one is worker-side, per-task)."""

    #: Never blocks anything; only ever produces a WARNED decision once
    #: the budget is reached. The safe default, matching
    #: PrivacyBudgetPolicy's own WARN_ONLY default.
    WARN_ONLY = "warn_only"
    #: Preventive: checked before each optimizer step, using a projected
    #: epsilon (what epsilon would become if this step were taken)
    #: computed without mutating the real accountant. If the projection
    #: would meet or exceed budget, that step (and every later step in
    #: this task) is never taken.
    STOP_BEFORE_EXCEEDING = "stop_before_exceeding"
    #: Reactive: this task's already-in-progress training is allowed to
    #: run to completion and submit its result. Once the post-step
    #: epsilon meets or exceeds budget, this client is refused any
    #: *further* private-training task in this worker process (see
    #: SampleBudgetEnforcer.refuse_if_already_stopped).
    STOP_AFTER_CURRENT_TASK = "stop_after_current_task"
    #: Reactive, strict: the moment the post-step epsilon meets or
    #: exceeds budget, the task fails outright — no partial delta is
    #: submitted, unlike STOP_BEFORE_EXCEEDING's "keep what was safely
    #: trained so far" behavior.
    FAIL_TASK = "fail_task"


# Only accountant types the installed Opacus version actually supports —
# see accounting.py's SUPPORTED_ACCOUNTANTS. Kept as a separate
# module-level constant (not imported from accounting.py) so importing
# this module never requires importing torch/opacus.
SUPPORTED_ACCOUNTANTS = ("rdp", "prv", "gdp")

# fl_core::WeightingStrategyType's wire strings (see
# cpp/core/include/fl_core/aggregation.hpp / coordinator_service.cpp's
# weighting_from_wire) minus plain "sample_count": user-level DP rejects
# unrestricted sample-count weighting because an unbounded per-client
# sample count gives unbounded per-client sensitivity in the weighted
# sum, breaking the clipping bound's sensitivity guarantee. See
# docs/user-level-dp.md.
PRIVACY_SAFE_WEIGHTING_STRATEGIES = (
    "uniform",
    "capped_sample_count",
    "normalized_bounded",
)


@dataclass(slots=True)
class SampleLevelDPConfig:
    noise_multiplier: float = 1.0
    max_grad_norm: float = 1.0
    target_delta: float = 1e-5
    accountant: str = "rdp"
    poisson_sampling: bool = True
    epsilon_budget: float = 0.0  # 0 = unset, no budget enforced
    sample_budget_policy: SamplePrivacyBudgetPolicy = (
        SamplePrivacyBudgetPolicy.WARN_ONLY
    )
    # Secure Transport and Worker Identity Hardening slice: when True,
    # the worker must enable Opacus's own secure_mode (torchcsprng-backed
    # DP-SGD noise) for this task, and must reject the task outright
    # (never silently train with secure_mode=False) if torchcsprng is
    # not installed — see fl_platform.privacy.secure_random.
    secure_random_required: bool = False


@dataclass(slots=True)
class UserLevelDPConfig:
    noise_multiplier: float = 1.0
    target_delta: float = 1e-5
    accountant: str = "rdp"
    initial_clipping_bound: float = 1.0
    weighting_strategy: str = "uniform"
    secure_random: bool = False
    epsilon_budget: float = 0.0


@dataclass(slots=True)
class AdaptiveClippingConfig:
    enabled: bool = False
    target_quantile: float = 0.5
    clip_learning_rate: float = 0.2
    initial_clip: float = 1.0
    min_clip: float = 1e-3
    max_clip: float = 1e3
    count_noise_multiplier: float = 1.0
    target_delta: float = 1e-5
    epsilon_budget: float = 0.0


@dataclass(slots=True)
class PrivacyConfig:
    mode: PrivacyMode = PrivacyMode.NONE
    sample_level: SampleLevelDPConfig | None = None
    user_level: UserLevelDPConfig | None = None
    adaptive_clipping: AdaptiveClippingConfig = field(
        default_factory=AdaptiveClippingConfig
    )
    budget_policy: PrivacyBudgetPolicy = PrivacyBudgetPolicy.WARN_ONLY
    warning_threshold_fraction: float = 0.8
    schema_version: int = 1


@dataclass(slots=True)
class PrivacyValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _validate_sample_level(config: SampleLevelDPConfig) -> list[str]:
    errors = []
    if config.noise_multiplier <= 0:
        errors.append("sample-level DP requires positive noise_multiplier")
    if config.max_grad_norm <= 0:
        errors.append("sample-level DP requires positive max_grad_norm")
    if not 0 < config.target_delta < 1:
        errors.append("sample-level DP requires target_delta in (0,1)")
    if config.accountant not in SUPPORTED_ACCOUNTANTS:
        errors.append(
            f"unsupported sample-level accountant '{config.accountant}'; "
            f"installed Opacus supports {SUPPORTED_ACCOUNTANTS}"
        )
    return errors


def _validate_user_level(config: UserLevelDPConfig) -> list[str]:
    errors = []
    if config.noise_multiplier <= 0:
        errors.append("user-level DP requires positive noise_multiplier")
    if config.initial_clipping_bound <= 0:
        errors.append("user-level DP requires positive initial_clipping_bound")
    if not 0 < config.target_delta < 1:
        errors.append("user-level DP requires target_delta in (0,1)")
    if config.accountant not in SUPPORTED_ACCOUNTANTS:
        errors.append(
            f"unsupported user-level accountant '{config.accountant}'; "
            f"installed Opacus supports {SUPPORTED_ACCOUNTANTS}"
        )
    if config.weighting_strategy not in PRIVACY_SAFE_WEIGHTING_STRATEGIES:
        errors.append(
            f"weighting_strategy '{config.weighting_strategy}' is not privacy-safe "
            f"for user-level DP; must be one of {PRIVACY_SAFE_WEIGHTING_STRATEGIES} "
            "(sample-count weighting has unbounded per-client sensitivity)"
        )
    return errors


def _validate_adaptive_clipping(config: AdaptiveClippingConfig) -> list[str]:
    if not config.enabled:
        return []
    errors = []
    if not 0 < config.target_quantile < 1:
        errors.append("adaptive clipping requires target_quantile in (0,1)")
    if config.clip_learning_rate <= 0:
        errors.append("adaptive clipping requires positive clip_learning_rate")
    if config.initial_clip <= 0:
        errors.append("adaptive clipping requires positive initial_clip")
    if not 0 < config.min_clip <= config.initial_clip <= config.max_clip:
        errors.append("adaptive clipping requires min_clip <= initial_clip <= max_clip")
    if config.count_noise_multiplier <= 0:
        errors.append("adaptive clipping requires positive count_noise_multiplier")
    return errors


def validate_privacy_config(config: PrivacyConfig) -> PrivacyValidationResult:
    """Validates one PrivacyConfig. Never inspects epsilon budgets across
    mechanisms together — each mechanism's own errors/warnings are
    independent, matching the Critical Privacy Rule's separation."""
    errors: list[str] = []
    warnings: list[str] = []

    if config.mode == PrivacyMode.NONE:
        # Adaptive clipping is independently toggleable (its own
        # `enabled` flag) — still validated even when no DP mode is
        # otherwise active, rather than short-circuiting entirely.
        errors.extend(_validate_adaptive_clipping(config.adaptive_clipping))
        warnings.append("privacy disabled")
        return PrivacyValidationResult(
            valid=not errors, errors=errors, warnings=warnings
        )

    if config.mode in (PrivacyMode.SAMPLE_LEVEL_DP, PrivacyMode.HYBRID_DP):
        if config.sample_level is None:
            errors.append(f"mode {config.mode} requires sample_level config")
        else:
            errors.extend(_validate_sample_level(config.sample_level))

    if config.mode in (PrivacyMode.USER_LEVEL_DP, PrivacyMode.HYBRID_DP):
        if config.user_level is None:
            errors.append(f"mode {config.mode} requires user_level config")
        else:
            errors.extend(_validate_user_level(config.user_level))

    errors.extend(_validate_adaptive_clipping(config.adaptive_clipping))

    if config.mode == PrivacyMode.SAMPLE_LEVEL_DP and config.user_level is not None:
        warnings.append(
            "user_level config set but mode is sample_level_dp; it will not be applied"
        )
    if config.mode == PrivacyMode.USER_LEVEL_DP and config.sample_level is not None:
        warnings.append(
            "sample_level config set but mode is user_level_dp; it will not be applied"
        )

    return PrivacyValidationResult(valid=not errors, errors=errors, warnings=warnings)


def build_privacy_config(payload: dict[str, Any]) -> PrivacyConfig:
    mode = PrivacyMode(payload.get("mode", PrivacyMode.NONE))
    sample_level = None
    user_level = None
    if mode in (PrivacyMode.SAMPLE_LEVEL_DP, PrivacyMode.HYBRID_DP):
        raw = payload.get("sample_level", {})
        sample_level = SampleLevelDPConfig(
            noise_multiplier=float(raw.get("noise_multiplier", 1.0)),
            max_grad_norm=float(raw.get("max_grad_norm", 1.0)),
            target_delta=float(raw.get("target_delta", 1e-5)),
            accountant=str(raw.get("accountant", "rdp")),
            poisson_sampling=bool(raw.get("poisson_sampling", True)),
            epsilon_budget=float(raw.get("epsilon_budget", 0.0)),
            sample_budget_policy=SamplePrivacyBudgetPolicy(
                raw.get("sample_budget_policy", SamplePrivacyBudgetPolicy.WARN_ONLY)
            ),
            secure_random_required=bool(raw.get("secure_random_required", False)),
        )
    if mode in (PrivacyMode.USER_LEVEL_DP, PrivacyMode.HYBRID_DP):
        raw = payload.get("user_level", {})
        user_level = UserLevelDPConfig(
            noise_multiplier=float(raw.get("noise_multiplier", 1.0)),
            target_delta=float(raw.get("target_delta", 1e-5)),
            accountant=str(raw.get("accountant", "rdp")),
            initial_clipping_bound=float(raw.get("initial_clipping_bound", 1.0)),
            weighting_strategy=str(raw.get("weighting_strategy", "uniform")),
            secure_random=bool(raw.get("secure_random", False)),
            epsilon_budget=float(raw.get("epsilon_budget", 0.0)),
        )
    clip_raw = payload.get("adaptive_clipping", {})
    adaptive_clipping = AdaptiveClippingConfig(
        enabled=bool(clip_raw.get("enabled", False)),
        target_quantile=float(clip_raw.get("target_quantile", 0.5)),
        clip_learning_rate=float(clip_raw.get("clip_learning_rate", 0.2)),
        initial_clip=float(clip_raw.get("initial_clip", 1.0)),
        min_clip=float(clip_raw.get("min_clip", 1e-3)),
        max_clip=float(clip_raw.get("max_clip", 1e3)),
        count_noise_multiplier=float(clip_raw.get("count_noise_multiplier", 1.0)),
        target_delta=float(clip_raw.get("target_delta", 1e-5)),
        epsilon_budget=float(clip_raw.get("epsilon_budget", 0.0)),
    )
    return PrivacyConfig(
        mode=mode,
        sample_level=sample_level,
        user_level=user_level,
        adaptive_clipping=adaptive_clipping,
        budget_policy=PrivacyBudgetPolicy(
            payload.get("budget_policy", PrivacyBudgetPolicy.WARN_ONLY)
        ),
        warning_threshold_fraction=float(
            payload.get("warning_threshold_fraction", 0.8)
        ),
    )
