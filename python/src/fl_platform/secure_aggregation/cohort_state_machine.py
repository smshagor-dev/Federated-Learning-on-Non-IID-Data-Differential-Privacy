"""Secure aggregation provider identifier, session configuration
contract, and cohort state machine -- Python mirror of
cpp/coordinator/include/fl_coordinator/secure_aggregation_session.hpp.
See that header's docstring for the full rationale (this file is the
*contract* every future RPC handler must honor, not a live handler
itself -- see docs/secure-aggregation-protocol-foundation.md's Tier 1/
Tier 2 scope split for why the live wire path is explicitly deferred).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fl_platform.secure_aggregation.fixed_point_encoding import FixedPointEncodingProfile

# -- Provider ----------------------------------------------------------------
# Explicit per run, never an implicit default (Work Package B: "no silent
# fallback to NONE"). The experimental/no-dropout/honest-client-dependent
# qualifier is part of the name itself, not a footnote.
PROVIDER_NONE = "NONE"
PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL = "SECAGG_NO_DROPOUT_EXPERIMENTAL"

# -- Cohort lifecycle ----------------------------------------------------
STATE_COHORT_FORMING = "COHORT_FORMING"
STATE_KEY_ADVERTISEMENT = "KEY_ADVERTISEMENT"
STATE_COHORT_FROZEN = "COHORT_FROZEN"
STATE_MASKED_UPDATE_COLLECTION = "MASKED_UPDATE_COLLECTION"
STATE_AGGREGATE_VALIDATION = "AGGREGATE_VALIDATION"
STATE_COMPLETED = "COMPLETED"
STATE_ABORTED = "ABORTED"
STATE_FAILED = "FAILED"

_TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_ABORTED, STATE_FAILED})

# The one, explicit forward-progress allow-list (Work Package D) -- a
# flat mapping of exactly one legal next state per non-terminal state,
# mirroring cpp/coordinator/src/secure_aggregation_session.cpp's
# is_allowed_forward_transition switch statement pair-for-pair.
_ALLOWED_FORWARD_TRANSITIONS = {
    STATE_COHORT_FORMING: STATE_KEY_ADVERTISEMENT,
    STATE_KEY_ADVERTISEMENT: STATE_COHORT_FROZEN,
    STATE_COHORT_FROZEN: STATE_MASKED_UPDATE_COLLECTION,
    STATE_MASKED_UPDATE_COLLECTION: STATE_AGGREGATE_VALIDATION,
    STATE_AGGREGATE_VALIDATION: STATE_COMPLETED,
}

# -- Abort reasons ---------------------------------------------------------
ABORT_REASON_NONE = "none"
ABORT_REASON_DROPOUT = "dropout"
ABORT_REASON_DEADLINE_EXCEEDED = "deadline_exceeded"
ABORT_REASON_COHORT_MISMATCH = "cohort_mismatch"
ABORT_REASON_ENCODING_REJECTED = "encoding_rejected"
ABORT_REASON_OVERFLOW_REJECTED = "overflow_rejected"
ABORT_REASON_MASK_CANCELLATION_FAILED = "mask_cancellation_failed"
ABORT_REASON_COORDINATOR_RESTART = "coordinator_restart"
ABORT_REASON_SESSION_EXPIRED = "session_expired"
ABORT_REASON_MANUAL_ABORT = "manual_abort"
ABORT_REASON_INVALID_TRANSITION_REQUESTED = "invalid_transition_requested"


class CohortStateMachineError(Exception):
    pass


@dataclass(slots=True)
class CohortStateTransition:
    from_state: str
    to_state: str
    timestamp_unix_s: float
    reason: str = ""


class CohortStateMachine:
    """Explicit transition validation and history -- the only way to
    change ``state`` is through ``transition_to``/``abort``/``fail``,
    each of which checks the from/to pair against
    ``_ALLOWED_FORWARD_TRANSITIONS`` before mutating anything (Work
    Package D: "no implicit transition").
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._state = STATE_COHORT_FORMING
        self._history: list[CohortStateTransition] = []
        self._abort_reason = ABORT_REASON_NONE
        self._failure_reason = ""

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_terminal(self) -> bool:
        return self._state in _TERMINAL_STATES

    @property
    def history(self) -> list[CohortStateTransition]:
        return list(self._history)

    @property
    def abort_reason(self) -> str:
        return self._abort_reason

    @property
    def failure_reason(self) -> str:
        return self._failure_reason

    def transition_to(self, next_state: str, timestamp_unix_s: float, reason: str = "") -> None:
        allowed_next = _ALLOWED_FORWARD_TRANSITIONS.get(self._state)
        if allowed_next != next_state:
            raise CohortStateMachineError(
                f"CohortStateMachine[{self.session_id}]: illegal transition from "
                f"{self._state} to {next_state} (forward progress only, one step at a "
                "time, never out of a terminal state)"
            )
        self._history.append(CohortStateTransition(self._state, next_state, timestamp_unix_s, reason))
        self._state = next_state

    def abort(self, reason: str, timestamp_unix_s: float, detail: str = "") -> None:
        if self.is_terminal:
            raise CohortStateMachineError(
                f"CohortStateMachine[{self.session_id}]: cannot abort a session already "
                f"in terminal state {self._state}"
            )
        if reason == ABORT_REASON_NONE:
            raise CohortStateMachineError(
                f"CohortStateMachine[{self.session_id}]: abort() requires a specific "
                "abort reason, not ABORT_REASON_NONE"
            )
        recorded_reason = f"abort:{reason}" + (f" - {detail}" if detail else "")
        self._history.append(CohortStateTransition(self._state, STATE_ABORTED, timestamp_unix_s, recorded_reason))
        self._state = STATE_ABORTED
        self._abort_reason = reason

    def fail(self, reason: str, timestamp_unix_s: float) -> None:
        # Deliberately unconditional, matching the C++ implementation:
        # a FAILED marking must never itself be blocked by the state
        # machine's own transition table.
        self._history.append(CohortStateTransition(self._state, STATE_FAILED, timestamp_unix_s, reason))
        self._state = STATE_FAILED
        self._failure_reason = reason


@dataclass(slots=True)
class SecureAggregationSessionConfig:
    """Versioned session configuration -- Work Package C's field list,
    mirroring cpp/coordinator/include/fl_coordinator/secure_aggregation_session.hpp's
    ``SecureAggregationSessionConfig`` struct field-for-field.
    ``session_configuration_hash`` is populated by a canonical SHA-256
    hash function that lives in the (not-yet-written) crypto-primitive-
    dependent module, not here -- see this package's ``__init__.py`` for
    why.
    """

    schema_version: int = 1
    protocol_version: int = 1
    provider: str = PROVIDER_NONE

    session_id: str = ""
    run_id: str = ""
    round_id: int = 0
    model_version: str = ""
    aggregation_algorithm: str = ""

    cohort_size: int = 0
    minimum_cohort_size: int = 0
    ordered_participant_ids: list[str] = field(default_factory=list)

    tensor_manifest_hash: str = ""
    model_manifest_hash: str = ""

    fixed_point_profile: FixedPointEncodingProfile = field(default_factory=FixedPointEncodingProfile)
    domain_profile: str = "ring_mod_2_64"

    scale_factor: float = 0.0
    max_absolute_update_bound: float = 0.0
    max_client_weight: int = 0
    max_aggregate_bound: int = 0

    mask_generator_profile: str = "chacha20_ietf"
    key_agreement_profile: str = "x25519"
    key_derivation_profile: str = "hkdf_sha256"

    session_created_at_unix_s: float = 0.0
    key_advertisement_deadline_unix_s: float = 0.0
    masked_update_deadline_unix_s: float = 0.0
    session_expiry_unix_s: float = 0.0

    coordinator_signing_key_id: str = ""
    session_configuration_hash: str = ""
