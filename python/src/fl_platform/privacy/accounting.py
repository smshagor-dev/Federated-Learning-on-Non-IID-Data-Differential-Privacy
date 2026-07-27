"""Privacy accountants for the two mechanisms that need one, kept
strictly separate per the Critical Privacy Rule (see
docs/privacy-mathematics.md): sample-level DP and user-level DP protect
different neighboring relations and must never be combined into one
epsilon.

* :class:`SampleLevelAccountant` wraps Opacus's own accountant
  (``opacus.accountants.create_accountant``) — real per-sample DP-SGD
  accounting must come from Opacus itself, not a reimplementation. Only
  accountant types the installed Opacus version actually supports are
  exposed; see ``SUPPORTED_ACCOUNTANTS``.
* :class:`UserLevelAccountant` is dedicated to user-level (client-level)
  DP and does not reuse Opacus's accountant, which is built for
  per-sample subsampling, not per-client subsampling. It wraps
  ``federated.dp_accountant.MomentsAccountant`` — the legacy prototype's
  RDP moments accountant for exactly the client-level subsampled-Gaussian
  model this mechanism uses. Reused (not reimplemented) only after
  validating its assumptions: see
  ``python/tests/test_privacy_accounting.py``'s golden parity test, which
  confirms its per-order RDP values match Opacus's own ``compute_rdp``
  exactly (to float precision) at every shared integer order. The one
  known limitation is that its order search is integer-only where
  Opacus's is fractional (151 orders vs ~69), so it reports a valid but
  measurably more conservative (higher) epsilon than Opacus would for
  the same (noise_multiplier, sample_rate, steps) — documented, not
  hidden.
* :class:`AdaptiveClippingAccountant` accounts the *count query* that
  drives adaptive clipping (Andrew et al., 2021): each round, the
  (privatized) count of clients whose update exceeded the current clip
  bound is a bounded-sensitivity (=1 per client) query answered under the
  ordinary (non-subsampled) Gaussian mechanism, composed over rounds.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from typing import Any

from federated.dp_accountant import MomentsAccountant

SUPPORTED_ACCOUNTANTS = ("rdp", "prv", "gdp")


def opacus_capabilities() -> tuple[bool, str]:
    """Truthfully probes whether Opacus is actually importable in this
    process, for worker capability advertisement (see
    docs/worker-privacy-capabilities.md) — the coordinator only assigns
    sample-level/hybrid-DP tasks to workers that advertise
    supports_sample_level_dp, so this must reflect real installed state,
    never a hardcoded True. Returns (installed, version); version is ""
    when not installed. Uses importlib.metadata rather than importing
    opacus itself, so calling this doesn't pay Opacus's import cost for
    a worker that will never train privately.
    """
    try:
        return True, importlib.metadata.version("opacus")
    except importlib.metadata.PackageNotFoundError:
        return False, ""


def _create_opacus_accountant(mechanism: str) -> Any:
    from opacus.accountants import (
        create_accountant,  # noqa: PLC0415 - optional heavy dep
    )

    if mechanism not in SUPPORTED_ACCOUNTANTS:
        raise ValueError(
            f"unsupported accountant type '{mechanism}'; installed Opacus supports "
            f"{SUPPORTED_ACCOUNTANTS}. Do not invent unsupported accountant behavior."
        )
    return create_accountant(mechanism=mechanism)


class SampleLevelAccountant:
    """Real per-sample DP accounting via Opacus. One instance per
    (client, run) — sample-level DP protects one training example within
    one client's own local dataset, so accounting is inherently local to
    that client, never shared across clients or folded into a run-wide
    total (see docs/privacy-mathematics.md).
    """

    def __init__(self, mechanism: str = "rdp") -> None:
        self._mechanism = mechanism
        self._accountant = _create_opacus_accountant(mechanism)

    def step(self, *, noise_multiplier: float, sample_rate: float) -> None:
        self._accountant.step(
            noise_multiplier=noise_multiplier, sample_rate=sample_rate
        )

    def get_epsilon(self, delta: float) -> float:
        return float(self._accountant.get_epsilon(delta=delta))

    @property
    def mechanism(self) -> str:
        return self._mechanism


@dataclass(slots=True)
class UserLevelAccountantState:
    """Checkpointable state for :class:`UserLevelAccountant` — see
    docs/coordinator-recovery.md's privacy-state extension. Reconstructing
    an accountant from this state and replaying no further steps must
    report the exact same epsilon as before a checkpoint/restart.
    """

    noise_multiplier: float
    sample_rate: float
    target_delta: float
    steps: int


class UserLevelAccountant:
    """Dedicated client-level (user-level) DP accountant — see module
    docstring for why this does not reuse Opacus's accountant.
    """

    def __init__(
        self,
        *,
        noise_multiplier: float,
        sample_rate: float,
        target_delta: float = 1e-5,
    ) -> None:
        self._noise_multiplier = noise_multiplier
        self._sample_rate = sample_rate
        self._target_delta = target_delta
        self._accountant = MomentsAccountant(
            noise_multiplier=noise_multiplier,
            sample_rate=sample_rate,
            target_delta=target_delta,
        )

    def step(self, num_rounds: int = 1) -> None:
        self._accountant.step(num_rounds)

    def get_epsilon(self, delta: float | None = None) -> float:
        return float(self._accountant.get_epsilon(delta))

    @property
    def steps(self) -> int:
        return int(self._accountant.steps)

    def to_state(self) -> UserLevelAccountantState:
        return UserLevelAccountantState(
            noise_multiplier=self._noise_multiplier,
            sample_rate=self._sample_rate,
            target_delta=self._target_delta,
            steps=self._accountant.steps,
        )

    @classmethod
    def from_state(cls, state: UserLevelAccountantState) -> UserLevelAccountant:
        accountant = cls(
            noise_multiplier=state.noise_multiplier,
            sample_rate=state.sample_rate,
            target_delta=state.target_delta,
        )
        accountant.step(state.steps)
        return accountant


class AdaptiveClippingAccountant:
    """Accounts the privatized over-threshold-count query that drives
    adaptive clipping (Andrew et al., 2021). Every round, the count is a
    query of sensitivity 1 (one client can change it by at most 1)
    answered by the ordinary (non-subsampled) Gaussian mechanism — no
    client subsampling term, since every client already selected for the
    round contributes exactly one bit to the count.
    """

    def __init__(
        self, *, count_noise_multiplier: float, target_delta: float = 1e-5
    ) -> None:
        self._noise_multiplier = count_noise_multiplier
        self._target_delta = target_delta
        # sample_rate=1.0: reuses MomentsAccountant's plain-Gaussian branch
        # (q == 1.0 -> alpha / (2 sigma^2), the standard Gaussian-mechanism
        # RDP with no subsampling amplification) rather than a second
        # from-scratch implementation of the same well-known formula.
        self._accountant = MomentsAccountant(
            noise_multiplier=count_noise_multiplier,
            sample_rate=1.0,
            target_delta=target_delta,
        )

    def step(self, num_rounds: int = 1) -> None:
        self._accountant.step(num_rounds)

    def get_epsilon(self, delta: float | None = None) -> float:
        return float(self._accountant.get_epsilon(delta))

    @property
    def steps(self) -> int:
        return int(self._accountant.steps)
