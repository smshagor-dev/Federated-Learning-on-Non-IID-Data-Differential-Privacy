"""Worker-side enforcement of the sample-level DP epsilon budget — closes
the gap documented in docs/privacy-budget-policies.md's "Sample-level
DP's budget" section: prior to this module, ``SampleLevelDPConfig.epsilon_budget``
flowed into the ledger projection's ``budget_remaining`` field but
nothing actually stopped training when it was reached.

The worker remains authoritative for its own sample-level accountant
(see docs/privacy-mathematics.md) — this module operates directly
against the real Opacus accountant instance a training call already
owns (``privacy_engine.accountant``), never a separate parallel
accountant that could drift from the one actually driving DP-SGD noise.

Policy names are deliberately distinct from the coordinator-side
``PrivacyBudgetPolicy`` (WARN_ONLY/STOP_BEFORE_EXCEEDING/
STOP_AFTER_CURRENT_ROUND/FAIL_RUN, see config.py): those are
per-*round*, coordinator-scoped policies for user-level DP and adaptive
clipping. Sample-level DP is trained per-*task* inside one worker
process call, so its policy is round/run-agnostic and named
accordingly: WARN_ONLY / STOP_BEFORE_EXCEEDING / STOP_AFTER_CURRENT_TASK
/ FAIL_TASK.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# The single canonical definition lives in config.py (alongside the
# other privacy config enums, and mirroring proto's SampleBudgetPolicy —
# see config.py's own doc comment for the WARN_ONLY/STOP_BEFORE_EXCEEDING/
# STOP_AFTER_CURRENT_TASK/FAIL_TASK semantics); re-exported here so
# callers of this module don't need a separate import.
from fl_platform.privacy.config import SamplePrivacyBudgetPolicy

__all__ = [
    "BLOCKING_OUTCOMES",
    "SampleBudgetDecision",
    "SampleBudgetEnforcer",
    "SampleBudgetEnforcerState",
    "SampleBudgetOutcome",
    "SampleLevelBudgetExceededError",
    "SamplePrivacyBudgetPolicy",
    "accountant_state_hash",
    "checkpoint_enforcer",
    "project_next_epsilon",
    "restore_enforcer",
]


class SampleBudgetOutcome(StrEnum):
    ALLOWED = "allowed"
    WARNED = "warned"
    STOPPED_BEFORE_STEP = "stopped_before_step"
    STOPPED_AFTER_TASK = "stopped_after_task"
    FAILED_TASK = "failed_task"
    REFUSED_BEFORE_TRAINING = "refused_before_training"


#: Outcomes that mean "do not take/continue this step" — used by the
#: training loop to decide whether to keep iterating.
BLOCKING_OUTCOMES = frozenset(
    {
        SampleBudgetOutcome.STOPPED_BEFORE_STEP,
        SampleBudgetOutcome.FAILED_TASK,
        SampleBudgetOutcome.REFUSED_BEFORE_TRAINING,
    }
)


@dataclass(slots=True, frozen=True)
class SampleBudgetDecision:
    """One enforcement decision. ``accountant_state_hash`` is a safe,
    non-secret fingerprint of the accountant's history (never the
    history itself) suitable for the "accountant state hash" field the
    Secure Aggregation category's coordinator-side verification
    requires — see docs/secure-aggregation-architecture.md."""

    outcome: SampleBudgetOutcome
    policy: SamplePrivacyBudgetPolicy
    budget: float
    current_epsilon: float
    projected_epsilon: float | None
    accountant_state_hash: str
    reason: str


class SampleLevelBudgetExceededError(RuntimeError):
    """Raised when a hard policy (FAIL_TASK, STOP_BEFORE_EXCEEDING with
    zero safely-trained steps, or a refused already-stopped client)
    forbids submitting an update. Caught in worker/service.py exactly
    like TaskCancelled/TaskDeadlineExceeded — a structured, expected
    task-failure reason, not a crash."""

    def __init__(self, decision: SampleBudgetDecision) -> None:
        self.decision = decision
        super().__init__(
            f"sample-level privacy budget enforcement blocked this task: "
            f"{decision.outcome.value} ({decision.reason})"
        )


def _canonical_state_bytes(state_dict: dict[str, Any]) -> bytes:
    # Opacus's state_dict() values (a list of (float, float, int) tuples)
    # are already JSON-serializable once tuples become lists; sort_keys
    # makes the hash independent of dict insertion order.
    return json.dumps(state_dict, sort_keys=True, default=list).encode("utf-8")


def accountant_state_hash(accountant: Any) -> str:
    """A safe, non-secret sha256 fingerprint of an Opacus accountant's
    full step history. Not a secret — the history itself (noise
    multipliers, sample rates, step counts) is exactly what a
    SampleLevelLedgerEntry already reports; this is a fingerprint for
    tamper/consistency checking (matching values -> matching history),
    not confidential material."""
    return hashlib.sha256(_canonical_state_bytes(accountant.state_dict())).hexdigest()


def _clone_accountant(accountant: Any) -> Any:
    from opacus.accountants import create_accountant  # noqa: PLC0415

    state = accountant.state_dict()
    clone = create_accountant(mechanism=state["mechanism"])
    clone.load_state_dict(state)
    return clone


def project_next_epsilon(
    accountant: Any,
    *,
    noise_multiplier: float,
    sample_rate: float,
    target_delta: float,
) -> float:
    """Pre-step projection: what would this accountant's epsilon become
    if one more step were taken with these parameters? Computed on a
    throwaway clone — the real accountant is never mutated by a
    projection, only by an actual training step."""
    clone = _clone_accountant(accountant)
    clone.step(noise_multiplier=noise_multiplier, sample_rate=sample_rate)
    return float(clone.get_epsilon(delta=target_delta))


class SampleBudgetEnforcer:
    """One instance per (worker process, client_id) — sample-level DP is
    inherently per-client (see accounting.py's module docstring), so
    enforcement state is scoped the same way. Reused across every
    private-training task this worker process executes for that client,
    which is what makes STOP_AFTER_CURRENT_TASK's "refuse future tasks"
    behavior meaningful within one worker process's lifetime.

    Cross-worker-process / cross-restart continuity (a client trained by
    a *different* worker process in a later round) is not implemented by
    this enforcer alone — see docs/known-limitations.md's note on the
    scope of this closure-gate pass.
    """

    def __init__(
        self,
        *,
        client_id: str,
        policy: SamplePrivacyBudgetPolicy,
        epsilon_budget: float,
        target_delta: float,
    ) -> None:
        self._client_id = client_id
        self._policy = policy
        self._epsilon_budget = epsilon_budget
        self._target_delta = target_delta
        self._stopped_for_future_tasks = False
        self._last_decision: SampleBudgetDecision | None = None

    @property
    def policy(self) -> SamplePrivacyBudgetPolicy:
        return self._policy

    @property
    def epsilon_budget(self) -> float:
        return self._epsilon_budget

    @property
    def stopped_for_future_tasks(self) -> bool:
        return self._stopped_for_future_tasks

    @property
    def last_decision(self) -> SampleBudgetDecision | None:
        return self._last_decision

    def _budget_unset(self) -> bool:
        # Matches every other mechanism's "<=0 means unset" convention
        # (see run_manager.cpp's budget_remaining and config.py's
        # epsilon_budget doc comments) — 0 is never treated as "budget
        # of zero, block immediately".
        return self._epsilon_budget <= 0.0

    def refuse_if_already_stopped(self) -> None:
        """Call before starting a new private-training task for this
        client. Raises if a prior task already triggered
        STOP_AFTER_CURRENT_TASK or FAIL_TASK for this (worker process,
        client) pair — "no silent continuation after hard exhaustion"
        applies across tasks, not just within one."""
        if not self._stopped_for_future_tasks:
            return
        assert self._last_decision is not None
        decision = SampleBudgetDecision(
            outcome=SampleBudgetOutcome.REFUSED_BEFORE_TRAINING,
            policy=self._policy,
            budget=self._epsilon_budget,
            current_epsilon=self._last_decision.current_epsilon,
            projected_epsilon=None,
            accountant_state_hash=self._last_decision.accountant_state_hash,
            reason=(
                f"client '{self._client_id}' already exhausted its sample-level "
                "privacy budget in a prior task on this worker; refusing further "
                "private training before it starts"
            ),
        )
        self._last_decision = decision
        raise SampleLevelBudgetExceededError(decision)

    def check_before_step(
        self,
        accountant: Any,
        *,
        noise_multiplier: float,
        sample_rate: float,
    ) -> SampleBudgetDecision:
        """Pre-step projection, computed for every policy (for
        visibility) but only actually blocking under
        STOP_BEFORE_EXCEEDING."""
        current_epsilon = float(accountant.get_epsilon(delta=self._target_delta))
        state_hash = accountant_state_hash(accountant)
        if self._budget_unset():
            decision = SampleBudgetDecision(
                outcome=SampleBudgetOutcome.ALLOWED,
                policy=self._policy,
                budget=self._epsilon_budget,
                current_epsilon=current_epsilon,
                projected_epsilon=None,
                accountant_state_hash=state_hash,
                reason="no sample-level epsilon_budget configured",
            )
            self._last_decision = decision
            return decision

        projected = project_next_epsilon(
            accountant,
            noise_multiplier=noise_multiplier,
            sample_rate=sample_rate,
            target_delta=self._target_delta,
        )
        if (
            self._policy == SamplePrivacyBudgetPolicy.STOP_BEFORE_EXCEEDING
            and projected >= self._epsilon_budget
        ):
            decision = SampleBudgetDecision(
                outcome=SampleBudgetOutcome.STOPPED_BEFORE_STEP,
                policy=self._policy,
                budget=self._epsilon_budget,
                current_epsilon=current_epsilon,
                projected_epsilon=projected,
                accountant_state_hash=state_hash,
                reason=(
                    f"projected epsilon {projected:.6f} would meet or exceed budget "
                    f"{self._epsilon_budget:.6f}; step refused before it was taken"
                ),
            )
            self._last_decision = decision
            return decision

        decision = SampleBudgetDecision(
            outcome=SampleBudgetOutcome.ALLOWED,
            policy=self._policy,
            budget=self._epsilon_budget,
            current_epsilon=current_epsilon,
            projected_epsilon=projected,
            accountant_state_hash=state_hash,
            reason="within budget",
        )
        self._last_decision = decision
        return decision

    def check_after_step(self, accountant: Any) -> SampleBudgetDecision:
        """Post-step accountant check — the real, mutated accountant's
        current epsilon, after a step has actually been taken."""
        current_epsilon = float(accountant.get_epsilon(delta=self._target_delta))
        state_hash = accountant_state_hash(accountant)
        if self._budget_unset() or current_epsilon < self._epsilon_budget:
            decision = SampleBudgetDecision(
                outcome=SampleBudgetOutcome.ALLOWED,
                policy=self._policy,
                budget=self._epsilon_budget,
                current_epsilon=current_epsilon,
                projected_epsilon=None,
                accountant_state_hash=state_hash,
                reason="within budget",
            )
            self._last_decision = decision
            return decision

        reason = (
            f"epsilon {current_epsilon:.6f} met or exceeded budget "
            f"{self._epsilon_budget:.6f} after this step"
        )
        if self._policy == SamplePrivacyBudgetPolicy.WARN_ONLY:
            outcome = SampleBudgetOutcome.WARNED
        elif self._policy == SamplePrivacyBudgetPolicy.STOP_AFTER_CURRENT_TASK:
            outcome = SampleBudgetOutcome.STOPPED_AFTER_TASK
            self._stopped_for_future_tasks = True
        else:
            # FAIL_TASK, and STOP_BEFORE_EXCEEDING as defense-in-depth
            # (the pre-step check above should already have prevented
            # reaching this point under that policy — see
            # docs/privacy-budget-policies.md's identical
            # defense-in-depth note for the C++-side policies).
            outcome = SampleBudgetOutcome.FAILED_TASK
            self._stopped_for_future_tasks = True

        decision = SampleBudgetDecision(
            outcome=outcome,
            policy=self._policy,
            budget=self._epsilon_budget,
            current_epsilon=current_epsilon,
            projected_epsilon=None,
            accountant_state_hash=state_hash,
            reason=reason,
        )
        self._last_decision = decision
        if outcome == SampleBudgetOutcome.FAILED_TASK:
            raise SampleLevelBudgetExceededError(decision)
        return decision


@dataclass(slots=True, frozen=True)
class SampleBudgetEnforcerState:
    """Checkpointable state for a :class:`SampleBudgetEnforcer` — the
    ``accountant_state`` field is Opacus's own real ``state_dict()``
    output (history + mechanism), not a reimplementation. Restoring via
    :func:`restore_accountant_from_state` uses Opacus's own
    ``load_state_dict``, which *replaces* history rather than appending
    to it — restoring can never double-count steps, unlike a
    replay-every-step-again approach would risk."""

    client_id: str
    policy: SamplePrivacyBudgetPolicy
    epsilon_budget: float
    target_delta: float
    stopped_for_future_tasks: bool
    accountant_state: dict[str, Any]
    last_decision: SampleBudgetDecision | None


def checkpoint_enforcer(
    enforcer: SampleBudgetEnforcer, accountant: Any
) -> SampleBudgetEnforcerState:
    return SampleBudgetEnforcerState(
        client_id=enforcer._client_id,  # noqa: SLF001 - same-module state extraction
        policy=enforcer.policy,
        epsilon_budget=enforcer.epsilon_budget,
        target_delta=enforcer._target_delta,  # noqa: SLF001
        stopped_for_future_tasks=enforcer.stopped_for_future_tasks,
        accountant_state=accountant.state_dict(),
        last_decision=enforcer.last_decision,
    )


def restore_enforcer(
    state: SampleBudgetEnforcerState,
) -> tuple[SampleBudgetEnforcer, Any]:
    """Restores both the enforcer and a real Opacus accountant from a
    checkpointed state, via Opacus's own load_state_dict (never a manual
    replay of ``.step()`` calls, which would double-count)."""
    from opacus.accountants import create_accountant  # noqa: PLC0415

    accountant = create_accountant(mechanism=state.accountant_state["mechanism"])
    accountant.load_state_dict(state.accountant_state)
    enforcer = SampleBudgetEnforcer(
        client_id=state.client_id,
        policy=state.policy,
        epsilon_budget=state.epsilon_budget,
        target_delta=state.target_delta,
    )
    enforcer._stopped_for_future_tasks = state.stopped_for_future_tasks  # noqa: SLF001
    enforcer._last_decision = state.last_decision  # noqa: SLF001
    return enforcer, accountant
