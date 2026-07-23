from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PrivacyMode(StrEnum):
    NONE = "none"
    SAMPLE_LEVEL_DP = "sample_level_dp"
    USER_LEVEL_DP = "user_level_dp"
    HYBRID_DP = "hybrid_dp"


@dataclass(slots=True)
class SampleLevelDPConfig:
    mode: PrivacyMode = PrivacyMode.SAMPLE_LEVEL_DP
    noise_multiplier: float = 1.0
    max_grad_norm: float = 1.0
    target_delta: float = 1e-5
    accountant: str = "rdp"
    per_layer_clipping: bool = False


@dataclass(slots=True)
class UserLevelDPConfig:
    mode: PrivacyMode = PrivacyMode.USER_LEVEL_DP
    noise_multiplier: float = 1.0
    max_update_norm: float = 1.0
    target_delta: float = 1e-5
    accountant: str = "rdp"
    client_sampling_rate: float = 0.1


@dataclass(slots=True)
class PrivacyValidationResult:
    valid: bool
    warnings: list[str]


def build_privacy_config(payload: dict[str, Any]) -> SampleLevelDPConfig | UserLevelDPConfig | dict[str, Any]:
    mode = PrivacyMode(payload.get("mode", PrivacyMode.NONE))
    if mode == PrivacyMode.SAMPLE_LEVEL_DP:
        return SampleLevelDPConfig(
            noise_multiplier=float(payload["noise_multiplier"]),
            max_grad_norm=float(payload["max_grad_norm"]),
            target_delta=float(payload.get("target_delta", 1e-5)),
            accountant=str(payload.get("accountant", "rdp")),
            per_layer_clipping=bool(payload.get("per_layer_clipping", False)),
        )
    if mode == PrivacyMode.USER_LEVEL_DP:
        return UserLevelDPConfig(
            noise_multiplier=float(payload["noise_multiplier"]),
            max_update_norm=float(payload["max_update_norm"]),
            target_delta=float(payload.get("target_delta", 1e-5)),
            accountant=str(payload.get("accountant", "rdp")),
            client_sampling_rate=float(payload.get("client_sampling_rate", 0.1)),
        )
    if mode == PrivacyMode.HYBRID_DP:
        return {
            "mode": PrivacyMode.HYBRID_DP,
            "sample_level": build_privacy_config(
                {
                    "mode": PrivacyMode.SAMPLE_LEVEL_DP,
                    **payload.get("sample_level", {}),
                }
            ),
            "user_level": build_privacy_config(
                {
                    "mode": PrivacyMode.USER_LEVEL_DP,
                    **payload.get("user_level", {}),
                }
            ),
        }
    return {"mode": PrivacyMode.NONE}


def validate_privacy_config(config: SampleLevelDPConfig | UserLevelDPConfig | dict[str, Any]) -> PrivacyValidationResult:
    warnings: list[str] = []
    if isinstance(config, SampleLevelDPConfig):
        if config.noise_multiplier <= 0:
            return PrivacyValidationResult(False, ["sample-level DP requires positive noise_multiplier"])
        if config.max_grad_norm <= 0:
            return PrivacyValidationResult(False, ["sample-level DP requires positive max_grad_norm"])
        if config.accountant not in {"rdp", "prv"}:
            warnings.append("unknown sample-level accountant; future integration may reject it")
        return PrivacyValidationResult(True, warnings)
    if isinstance(config, UserLevelDPConfig):
        if config.noise_multiplier <= 0:
            return PrivacyValidationResult(False, ["user-level DP requires positive noise_multiplier"])
        if config.max_update_norm <= 0:
            return PrivacyValidationResult(False, ["user-level DP requires positive max_update_norm"])
        if not 0 < config.client_sampling_rate <= 1:
            return PrivacyValidationResult(False, ["user-level DP requires client_sampling_rate in (0,1]"])
        return PrivacyValidationResult(True, warnings)

    mode = config.get("mode", PrivacyMode.NONE)
    if mode == PrivacyMode.HYBRID_DP:
        sample_result = validate_privacy_config(config["sample_level"])
        user_result = validate_privacy_config(config["user_level"])
        return PrivacyValidationResult(
            valid=sample_result.valid and user_result.valid,
            warnings=sample_result.warnings + user_result.warnings,
        )
    return PrivacyValidationResult(True, ["privacy disabled"])
