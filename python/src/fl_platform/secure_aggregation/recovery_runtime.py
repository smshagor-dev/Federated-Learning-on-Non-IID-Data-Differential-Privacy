"""Fail-closed live admission state for secure-aggregation recovery shares.

The gRPC adapter is intentionally thin: it authenticates the holder, maps the
protobuf payload to :class:`RecoverySharePayload`, then delegates here.  This
module owns the protocol policy and the volatile threshold collector so the
security rules are independently testable without a network stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fl_platform.secure_aggregation.dropout_recovery import (
    DropoutRecoveryError,
    recover_ephemeral_private_key,
)
from fl_platform.secure_aggregation.recovery_wire import RecoverySharePayload
from fl_platform.secure_aggregation.threshold_recovery import (
    RecoveredSecret,
    RecoveryShareReceipt,
    ThresholdRecoveryCoordinator,
    ThresholdRecoveryError,
)


class RecoveryAdmissionError(RuntimeError):
    """Raised when a recovery share violates the live recovery contract."""


@dataclass(frozen=True, slots=True)
class RecoverySessionView:
    session_id: str
    run_id: str
    round_id: int
    model_version: str
    cohort_commitment: str
    participant_public_keys: dict[str, bytes]
    submitted_contributors: frozenset[str]
    privacy_mode: str = "none"

    @property
    def participants(self) -> frozenset[str]:
        return frozenset(self.participant_public_keys)

    @property
    def missing_contributors(self) -> frozenset[str]:
        return self.participants - self.submitted_contributors

    def validate(self) -> None:
        if not self.session_id or not self.run_id or not self.model_version:
            raise RecoveryAdmissionError(
                "session view has empty run/session/model binding"
            )
        if not self.cohort_commitment:
            raise RecoveryAdmissionError("session view has no frozen cohort commitment")
        if len(self.participant_public_keys) < 3:
            raise RecoveryAdmissionError(
                "threshold recovery requires a cohort of at least 3"
            )
        if any(
            not worker_id or len(public_key) != 32
            for worker_id, public_key in self.participant_public_keys.items()
        ):
            raise RecoveryAdmissionError(
                "session view contains an invalid X25519 public key"
            )
        if not self.submitted_contributors <= self.participants:
            raise RecoveryAdmissionError(
                "submitted contributors are outside the frozen cohort"
            )


@dataclass(frozen=True, slots=True)
class RecoveryAdmissionResult:
    accepted: bool
    share_count: int
    threshold: int
    recoverable: bool
    recovered_secret: RecoveredSecret | None = None


@dataclass(slots=True)
class LiveRecoveryRegistry:
    """Volatile per-session recovery-share collectors.

    Raw Shamir shares live only inside ``collectors``.  ``snapshot_receipts``
    exposes commitment-only metadata suitable for durable persistence; callers
    must never serialize ``collectors`` itself.
    """

    collectors: dict[str, ThresholdRecoveryCoordinator] = field(default_factory=dict)

    def submit(
        self,
        payload: RecoverySharePayload,
        session: RecoverySessionView,
    ) -> RecoveryAdmissionResult:
        payload.validate()
        session.validate()
        self._validate_binding(payload, session)
        share = payload.to_recovery_share()

        collector = self.collectors.setdefault(
            session.session_id,
            ThresholdRecoveryCoordinator(session_id=session.session_id),
        )
        try:
            collector.submit(share)
        except ThresholdRecoveryError as exc:
            raise RecoveryAdmissionError(str(exc)) from exc

        submitted = collector.submitted.get(
            (payload.owner_worker_id, payload.generation), {}
        )
        recoverable = collector.can_recover(payload.owner_worker_id, payload.generation)
        if not recoverable:
            return RecoveryAdmissionResult(
                accepted=True,
                share_count=len(submitted),
                threshold=payload.threshold,
                recoverable=False,
            )

        ordered = tuple(sorted(submitted.values(), key=lambda item: item.index))
        try:
            recovered = recover_ephemeral_private_key(
                ordered,
                expected_public_key_raw=session.participant_public_keys[
                    payload.owner_worker_id
                ],
            )
        except (ThresholdRecoveryError, DropoutRecoveryError) as exc:
            raise RecoveryAdmissionError(str(exc)) from exc
        return RecoveryAdmissionResult(
            accepted=True,
            share_count=len(submitted),
            threshold=payload.threshold,
            recoverable=True,
            recovered_secret=recovered,
        )

    def snapshot_receipts(self, session_id: str) -> tuple[RecoveryShareReceipt, ...]:
        collector = self.collectors.get(session_id)
        if collector is None:
            return ()
        return collector.snapshot()

    @staticmethod
    def _validate_binding(
        payload: RecoverySharePayload,
        session: RecoverySessionView,
    ) -> None:
        if payload.session_id != session.session_id:
            raise RecoveryAdmissionError("recovery share session_id mismatch")
        if payload.run_id != session.run_id or payload.round_id != session.round_id:
            raise RecoveryAdmissionError("recovery share run/round mismatch")
        if payload.model_version != session.model_version:
            raise RecoveryAdmissionError("recovery share model_version mismatch")
        if payload.cohort_commitment != session.cohort_commitment:
            raise RecoveryAdmissionError(
                "recovery share frozen cohort commitment mismatch"
            )
        if session.privacy_mode != "none":
            raise RecoveryAdmissionError(
                "live threshold recovery currently supports non-private secure "
                "rounds only"
            )

        missing = session.missing_contributors
        if len(missing) != 1:
            raise RecoveryAdmissionError(
                "initial live recovery supports exactly one missing contributor"
            )
        if payload.owner_worker_id not in missing:
            raise RecoveryAdmissionError(
                "recovery owner is not the missing contributor"
            )
        if payload.holder_worker_id not in session.submitted_contributors:
            raise RecoveryAdmissionError(
                "recovery holder must be a surviving submitted contributor"
            )
        if payload.secret_length != 32:
            raise RecoveryAdmissionError(
                "recovery secret must be a 32-byte ephemeral X25519 private key"
            )

        expected_total_shares = len(session.participants) - 1
        if payload.total_shares != expected_total_shares:
            raise RecoveryAdmissionError(
                "recovery total_shares must equal frozen cohort size minus the owner"
            )
        if payload.threshold > len(session.submitted_contributors):
            raise RecoveryAdmissionError(
                "recovery threshold exceeds the currently surviving contributors"
            )


__all__ = [
    "LiveRecoveryRegistry",
    "RecoveryAdmissionError",
    "RecoveryAdmissionResult",
    "RecoverySessionView",
]
