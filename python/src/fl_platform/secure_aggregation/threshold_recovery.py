"""Threshold recovery primitives for secure-aggregation dropout handling.

This module provides Shamir secret sharing over a large prime field for
recovering opaque mask seeds after client dropout. Shares are explicitly bound
to a session, secret owner, holder, threshold, generation, and secret digest.
It is protocol state, not a network transport or universal async-secagg claim.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field

# 2**521 - 1 is a Mersenne prime and comfortably contains an opaque 64-byte
# seed. The field choice is versioned in share records so it cannot silently
# change across a persisted/restarted recovery session.
FIELD_ID = "mersenne-521-v1"
_FIELD_PRIME = (1 << 521) - 1
_MAX_SECRET_BYTES = 64


class ThresholdRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class RecoveryShare:
    session_id: str
    owner_id: str
    holder_id: str
    generation: int
    threshold: int
    total_shares: int
    index: int
    value: int
    secret_digest: str
    field_id: str = FIELD_ID


@dataclass(frozen=True)
class RecoveredSecret:
    session_id: str
    owner_id: str
    generation: int
    secret: bytes
    contributing_holders: tuple[str, ...]


def _context_digest(
    secret: bytes,
    *,
    session_id: str,
    owner_id: str,
    generation: int,
) -> str:
    digest = hashlib.sha256()
    digest.update(b"fl-platform-secagg-threshold-v1\x00")
    digest.update(session_id.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(owner_id.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(str(generation).encode("ascii"))
    digest.update(b"\x00")
    digest.update(secret)
    return digest.hexdigest()


def _validate_secret(secret: bytes) -> None:
    if not secret:
        raise ValueError("secret must not be empty")
    if len(secret) > _MAX_SECRET_BYTES:
        raise ValueError(f"secret must be at most {_MAX_SECRET_BYTES} bytes")
    if int.from_bytes(secret, "big") >= _FIELD_PRIME:
        raise ValueError("secret does not fit the configured recovery field")


def create_recovery_shares(
    secret: bytes,
    *,
    session_id: str,
    owner_id: str,
    holder_ids: tuple[str, ...],
    threshold: int,
    generation: int = 0,
) -> tuple[RecoveryShare, ...]:
    """Create threshold-bound shares for one opaque dropout-recovery secret."""
    _validate_secret(secret)
    if not session_id or not owner_id:
        raise ValueError("session_id and owner_id must not be empty")
    if generation < 0:
        raise ValueError("generation must be non-negative")
    if not holder_ids or any(not holder for holder in holder_ids):
        raise ValueError("holder_ids must contain non-empty identities")
    if len(set(holder_ids)) != len(holder_ids):
        raise ValueError("holder_ids must be unique")
    if not 2 <= threshold <= len(holder_ids):
        raise ValueError("threshold must be in [2, number of holders]")

    coefficients = [int.from_bytes(secret, "big")]
    coefficients.extend(
        secrets.randbelow(_FIELD_PRIME) for _ in range(threshold - 1)
    )
    digest = _context_digest(
        secret,
        session_id=session_id,
        owner_id=owner_id,
        generation=generation,
    )

    shares: list[RecoveryShare] = []
    for index, holder_id in enumerate(holder_ids, start=1):
        x = index
        value = 0
        power = 1
        for coefficient in coefficients:
            value = (value + coefficient * power) % _FIELD_PRIME
            power = (power * x) % _FIELD_PRIME
        shares.append(
            RecoveryShare(
                session_id=session_id,
                owner_id=owner_id,
                holder_id=holder_id,
                generation=generation,
                threshold=threshold,
                total_shares=len(holder_ids),
                index=index,
                value=value,
                secret_digest=digest,
            )
        )
    return tuple(shares)


def _mod_inverse(value: int) -> int:
    value %= _FIELD_PRIME
    if value == 0:
        raise ThresholdRecoveryError("cannot invert zero in recovery field")
    return pow(value, _FIELD_PRIME - 2, _FIELD_PRIME)


def reconstruct_recovery_secret(shares: tuple[RecoveryShare, ...]) -> RecoveredSecret:
    """Reconstruct and context-verify an opaque recovery secret."""
    if not shares:
        raise ThresholdRecoveryError("at least one recovery share is required")
    first = shares[0]
    if first.field_id != FIELD_ID:
        raise ThresholdRecoveryError("unsupported recovery field")
    if len(shares) < first.threshold:
        raise ThresholdRecoveryError("insufficient recovery shares")

    seen_indices: set[int] = set()
    seen_holders: set[str] = set()
    for share in shares:
        if share.field_id != first.field_id:
            raise ThresholdRecoveryError("recovery share field mismatch")
        if (
            share.session_id != first.session_id
            or share.owner_id != first.owner_id
            or share.generation != first.generation
            or share.threshold != first.threshold
            or share.total_shares != first.total_shares
            or share.secret_digest != first.secret_digest
        ):
            raise ThresholdRecoveryError("recovery share context mismatch")
        if not 1 <= share.index <= first.total_shares:
            raise ThresholdRecoveryError("recovery share index is outside the cohort")
        if not 0 <= share.value < _FIELD_PRIME:
            raise ThresholdRecoveryError("recovery share value is outside the field")
        if share.index in seen_indices or share.holder_id in seen_holders:
            raise ThresholdRecoveryError("duplicate recovery share")
        seen_indices.add(share.index)
        seen_holders.add(share.holder_id)

    selected = shares[: first.threshold]
    secret_value = 0
    for i, share_i in enumerate(selected):
        numerator = 1
        denominator = 1
        x_i = share_i.index
        for j, share_j in enumerate(selected):
            if i == j:
                continue
            x_j = share_j.index
            numerator = (numerator * (-x_j)) % _FIELD_PRIME
            denominator = (denominator * (x_i - x_j)) % _FIELD_PRIME
        lagrange = numerator * _mod_inverse(denominator) % _FIELD_PRIME
        secret_value = (secret_value + share_i.value * lagrange) % _FIELD_PRIME

    byte_length = max(1, (secret_value.bit_length() + 7) // 8)
    secret = secret_value.to_bytes(byte_length, "big")
    digest = _context_digest(
        secret,
        session_id=first.session_id,
        owner_id=first.owner_id,
        generation=first.generation,
    )
    if not secrets.compare_digest(digest, first.secret_digest):
        raise ThresholdRecoveryError("reconstructed secret failed digest validation")
    return RecoveredSecret(
        session_id=first.session_id,
        owner_id=first.owner_id,
        generation=first.generation,
        secret=secret,
        contributing_holders=tuple(share.holder_id for share in selected),
    )


@dataclass(slots=True)
class ThresholdRecoveryCoordinator:
    """Restartable share collector for one secure-aggregation session."""

    session_id: str
    submitted: dict[tuple[str, int], dict[str, RecoveryShare]] = field(
        default_factory=dict
    )

    def submit(self, share: RecoveryShare) -> None:
        if share.session_id != self.session_id:
            raise ThresholdRecoveryError("share belongs to a different session")
        key = (share.owner_id, share.generation)
        owner_shares = self.submitted.setdefault(key, {})
        if share.holder_id in owner_shares:
            if owner_shares[share.holder_id] == share:
                return
            raise ThresholdRecoveryError("holder submitted conflicting recovery share")
        if owner_shares:
            reference = next(iter(owner_shares.values()))
            if (
                share.threshold != reference.threshold
                or share.total_shares != reference.total_shares
                or share.secret_digest != reference.secret_digest
                or share.field_id != reference.field_id
            ):
                raise ThresholdRecoveryError("share conflicts with recovery session metadata")
        owner_shares[share.holder_id] = share

    def can_recover(self, owner_id: str, generation: int = 0) -> bool:
        owner_shares = self.submitted.get((owner_id, generation), {})
        if not owner_shares:
            return False
        threshold = next(iter(owner_shares.values())).threshold
        return len(owner_shares) >= threshold

    def recover(self, owner_id: str, generation: int = 0) -> RecoveredSecret:
        owner_shares = self.submitted.get((owner_id, generation), {})
        if not owner_shares:
            raise ThresholdRecoveryError("no recovery shares submitted for owner")
        ordered = tuple(
            sorted(owner_shares.values(), key=lambda share: share.index)
        )
        return reconstruct_recovery_secret(ordered)

    def snapshot(self) -> tuple[RecoveryShare, ...]:
        """Return deterministic persisted state suitable for restart restoration."""
        shares = [
            share
            for owner_shares in self.submitted.values()
            for share in owner_shares.values()
        ]
        return tuple(
            sorted(
                shares,
                key=lambda share: (
                    share.owner_id,
                    share.generation,
                    share.index,
                    share.holder_id,
                ),
            )
        )

    @classmethod
    def restore(
        cls, session_id: str, shares: tuple[RecoveryShare, ...]
    ) -> ThresholdRecoveryCoordinator:
        coordinator = cls(session_id=session_id)
        for share in shares:
            coordinator.submit(share)
        return coordinator


__all__ = [
    "FIELD_ID",
    "RecoveredSecret",
    "RecoveryShare",
    "ThresholdRecoveryCoordinator",
    "ThresholdRecoveryError",
    "create_recovery_shares",
    "reconstruct_recovery_secret",
]
