from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExecutionMode(StrEnum):
    SYNCHRONOUS = "synchronous"
    DEADLINE_BASED_SEMI_SYNCHRONOUS = "deadline_based_semi_synchronous"
    BUFFERED_ASYNCHRONOUS = "buffered_asynchronous"
    STALENESS_AWARE_ASYNCHRONOUS = "staleness_aware_asynchronous"


@dataclass(slots=True)
class SchedulingConfig:
    mode: ExecutionMode = ExecutionMode.SYNCHRONOUS
    target_clients: int = 1
    minimum_clients: int = 1
    round_deadline_s: float | None = None
    buffer_size: int | None = None
    maximum_staleness: int | None = None
    carryover_late_results: bool = False


@dataclass(slots=True)
class SchedulingValidationResult:
    valid: bool
    warnings: list[str]


def validate_scheduling_config(config: SchedulingConfig) -> SchedulingValidationResult:
    warnings: list[str] = []
    if config.target_clients <= 0:
        return SchedulingValidationResult(False, ["target_clients must be positive"])
    if config.minimum_clients <= 0:
        return SchedulingValidationResult(False, ["minimum_clients must be positive"])
    if config.minimum_clients > config.target_clients:
        return SchedulingValidationResult(False, ["minimum_clients must not exceed target_clients"])

    if config.mode == ExecutionMode.SYNCHRONOUS:
        if config.round_deadline_s is not None:
            warnings.append("synchronous mode ignores round_deadline_s")
        if config.buffer_size is not None:
            warnings.append("synchronous mode ignores buffer_size")
        return SchedulingValidationResult(True, warnings)

    if config.mode == ExecutionMode.DEADLINE_BASED_SEMI_SYNCHRONOUS:
        if config.round_deadline_s is None or config.round_deadline_s <= 0:
            return SchedulingValidationResult(False, ["semi-synchronous mode requires positive round_deadline_s"])
        return SchedulingValidationResult(True, warnings)

    if config.mode == ExecutionMode.BUFFERED_ASYNCHRONOUS:
        if config.buffer_size is None or config.buffer_size <= 0:
            return SchedulingValidationResult(False, ["buffered asynchronous mode requires positive buffer_size"])
        return SchedulingValidationResult(True, warnings)

    if config.mode == ExecutionMode.STALENESS_AWARE_ASYNCHRONOUS:
        if config.maximum_staleness is None or config.maximum_staleness < 0:
            return SchedulingValidationResult(False, ["staleness-aware mode requires non-negative maximum_staleness"])
        if config.buffer_size is None or config.buffer_size <= 0:
            warnings.append("staleness-aware mode usually pairs with a positive buffer_size")
        return SchedulingValidationResult(True, warnings)

    return SchedulingValidationResult(True, warnings)
