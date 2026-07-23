from __future__ import annotations

from dataclasses import dataclass

from fl_platform.execution import ExecutionMode

ASYNC_INCOMPATIBLE_MESSAGE = (
    "secure aggregation scaffold is not compatible with asynchronous modes yet"
)
SEMI_SYNC_WARNING = (
    "semi-synchronous secure aggregation requires stricter cohort compatibility "
    "in a future milestone"
)
DROPOUT_WARNING = (
    "dropout recovery is declared but not yet backed by a cryptographic "
    "recovery protocol"
)


@dataclass(slots=True)
class SecureAggregationConfig:
    enabled: bool = False
    minimum_cohort_size: int = 2
    dropout_recovery: bool = False
    execution_mode: ExecutionMode = ExecutionMode.SYNCHRONOUS


@dataclass(slots=True)
class SecureAggregationValidationResult:
    valid: bool
    warnings: list[str]


def validate_secure_aggregation_config(
    config: SecureAggregationConfig,
) -> SecureAggregationValidationResult:
    warnings: list[str] = []
    if not config.enabled:
        return SecureAggregationValidationResult(True, ["secure aggregation disabled"])
    if config.minimum_cohort_size < 2:
        return SecureAggregationValidationResult(
            False,
            ["secure aggregation requires minimum_cohort_size >= 2"],
        )
    if config.execution_mode in {
        ExecutionMode.BUFFERED_ASYNCHRONOUS,
        ExecutionMode.STALENESS_AWARE_ASYNCHRONOUS,
    }:
        return SecureAggregationValidationResult(
            False,
            [ASYNC_INCOMPATIBLE_MESSAGE],
        )
    if config.execution_mode == ExecutionMode.DEADLINE_BASED_SEMI_SYNCHRONOUS:
        warnings.append(SEMI_SYNC_WARNING)
    if config.dropout_recovery:
        warnings.append(DROPOUT_WARNING)
    return SecureAggregationValidationResult(True, warnings)
