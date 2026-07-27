"""Adaptive clipping controller — quantile-based dynamic clip bound
(Andrew et al., 2021), made a real DP mechanism by privatizing the
over-threshold count with Gaussian noise before it ever influences the
clip bound. See docs/adaptive-clipping.md.

Required behavior (see docs/adaptive-clipping.md for the full 10-step
sequence): each round, count how many cohort members' updates exceeded
the current clip bound, add Gaussian noise to that count (never expose
the raw count anywhere — not via Go, not via logs, not via the web UI),
derive a noisy over-threshold fraction, and move the clip bound toward
whatever value would make that noisy fraction match target_quantile.
The privatized count query is itself accounted by
:class:`fl_platform.privacy.accounting.AdaptiveClippingAccountant` — a
dedicated accountant, entirely separate from sample-level/user-level
accounting (Critical Privacy Rule).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fl_platform.privacy.accounting import AdaptiveClippingAccountant


@dataclass(slots=True)
class AdaptiveClipConfig:
    initial_clip: float = 1.0
    target_quantile: float = 0.5
    learning_rate: float = 0.2
    min_clip: float = 1e-3
    max_clip: float = 1e3
    count_noise_multiplier: float = 1.0
    target_delta: float = 1e-5


@dataclass(slots=True, frozen=True)
class AdaptiveClipStepResult:
    clip_value: float
    noisy_over_threshold_fraction: float
    epsilon: float
    delta: float


class AdaptiveClipController:
    """Quantile-based dynamic clipping bound, privatized via a noised
    over-threshold count — see module docstring. Deterministic (seeded)
    noise generation is for tests only; runtime use must pass a
    non-deterministic generator (the default, ``np.random.default_rng()``
    with OS entropy) — see docs/user-level-dp.md's noise-generation
    requirements, which apply equally here.
    """

    def __init__(
        self,
        config: AdaptiveClipConfig,
        *,
        rng: np.random.Generator | None = None,
    ) -> None:
        if config.initial_clip <= 0:
            raise ValueError("initial_clip must be positive")
        if not config.min_clip <= config.initial_clip <= config.max_clip:
            raise ValueError("min_clip <= initial_clip <= max_clip must hold")
        self._config = config
        self._clip = config.initial_clip
        self._rng = rng if rng is not None else np.random.default_rng()
        self._accountant = AdaptiveClippingAccountant(
            count_noise_multiplier=config.count_noise_multiplier,
            target_delta=config.target_delta,
        )

    @property
    def clip_value(self) -> float:
        return self._clip

    def step(
        self, *, over_threshold_count: int, cohort_size: int
    ) -> AdaptiveClipStepResult:
        """Advances the clip bound by one round.

        ``over_threshold_count`` (how many of ``cohort_size`` clients'
        updates exceeded the current clip bound) is the sensitive
        quantity — it is immediately privatized below and never
        returned or logged in raw form. Only the noisy fraction and the
        resulting clip value are ever exposed.
        """
        if cohort_size <= 0:
            raise ValueError("cohort_size must be positive")
        if not 0 <= over_threshold_count <= cohort_size:
            raise ValueError("over_threshold_count must be in [0, cohort_size]")

        # Sensitivity of the count query is 1 (one client can change it
        # by at most 1), so noise std = count_noise_multiplier directly.
        noise = self._rng.normal(0.0, self._config.count_noise_multiplier)
        noisy_count = over_threshold_count + noise
        noisy_fraction = float(np.clip(noisy_count / cohort_size, 0.0, 1.0))

        # over_threshold_fraction > target_quantile means too many
        # clients are being clipped -> the bound is too low -> raise it
        # (positive error increases scale). The opposite (too few
        # clipped) lowers it. This is the mirror image of Andrew et
        # al.'s "fraction below the clip" convention, since this
        # controller tracks the "fraction over the clip" instead.
        error = noisy_fraction - self._config.target_quantile
        scale = 1.0 + self._config.learning_rate * error
        self._clip *= max(scale, 1e-6)
        self._clip = min(max(self._clip, self._config.min_clip), self._config.max_clip)

        self._accountant.step(1)
        return AdaptiveClipStepResult(
            clip_value=self._clip,
            noisy_over_threshold_fraction=noisy_fraction,
            epsilon=self._accountant.get_epsilon(),
            delta=self._config.target_delta,
        )

    @property
    def epsilon(self) -> float:
        return self._accountant.get_epsilon()

    @property
    def steps(self) -> int:
        return self._accountant.steps
