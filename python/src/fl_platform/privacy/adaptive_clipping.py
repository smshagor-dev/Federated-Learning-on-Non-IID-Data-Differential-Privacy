from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AdaptiveClipConfig:
    initial_clip: float = 1.0
    target_quantile: float = 0.5
    learning_rate: float = 0.2
    min_clip: float = 1e-3
    max_clip: float = 1e3


class AdaptiveClipController:
    """Deterministic scalar adaptive clipping shell.

    Uses a simple quantile-inspired signed update on clipping rate observations.
    """

    def __init__(self, config: AdaptiveClipConfig) -> None:
        if config.initial_clip <= 0:
            raise ValueError("initial_clip must be positive")
        self._config = config
        self._clip = config.initial_clip

    @property
    def clip_value(self) -> float:
        return self._clip

    def step(self, observed_clipping_rate: float) -> float:
        if not 0.0 <= observed_clipping_rate <= 1.0:
            raise ValueError("observed_clipping_rate must be in [0, 1]")
        error = observed_clipping_rate - self._config.target_quantile
        scale = 1.0 - self._config.learning_rate * error
        self._clip *= max(scale, 1e-6)
        self._clip = min(max(self._clip, self._config.min_clip), self._config.max_clip)
        return self._clip
