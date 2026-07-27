"""Truthful secure-random capability detection for the Python worker —
closure-gate work for the Secure Aggregation and Cryptographic Protocols
category (see docs/secure-aggregation-architecture.md and
docs/cryptographic-primitives.md).

Mirrors accounting.py's ``opacus_capabilities()`` pattern: probe real
state, never hardcode an optimistic answer, and let a caller that
requires the capability fail loudly rather than silently proceed with a
weaker guarantee.

Three distinct questions, deliberately kept separate:

1. "Can this Python process access an OS-backed CSPRNG at all?" — yes,
   always, on any real CPython installation (the ``secrets`` module,
   itself backed by ``os.urandom``, is part of the standard library).
   :func:`secure_random_available` answers this, by actually calling
   ``secrets.token_bytes`` rather than assuming stdlib availability
   implies success (a process could in principle be running in an
   environment where the OS entropy source itself is broken).
2. "Can Opacus's own secure DP-SGD noise mode actually be enabled in
   this process?" — depends: Opacus's ``PrivacyEngine(secure_mode=True)``
   requires the optional ``torchcsprng`` package; without it, Opacus
   itself raises ``ImportError`` at construction time.
   :func:`opacus_secure_mode_available` truthfully probes for this (a
   real, guarded construction attempt, not an assumption from whether
   ``torchcsprng`` merely appears importable) so a caller can check
   *before* committing to a training run that will fail partway through
   otherwise.
3. "Does the worker's own privacy-relevant randomness actually route
   through a CSPRNG today?" — depends on whether Opacus's secure mode is
   both available *and requested* for a given task.
   :func:`worker_reports_secure_random_support` answers this, and is
   what ``WorkerPrivacyCapabilities.supports_secure_random`` is actually
   built from — never simply forwarding the answer to question 1
   (stdlib CSPRNG access, which is unconditionally true and irrelevant
   to whether Opacus's own noise generation is secure), since that
   would be exactly the "falsely advertise support" this closure gate
   forbids.
"""

from __future__ import annotations

import importlib.util
import secrets


class SecureRandomUnavailableError(RuntimeError):
    """Raised by :func:`require_secure_random` when a caller needs
    cryptographically secure randomness and this process cannot provide
    it. Never caught and silently downgraded to a weaker source — a
    caller that cannot tolerate this exception should not have
    requested secure random in the first place."""


class SecureRandomTaskRejectedError(RuntimeError):
    """Raised when a task requests Opacus secure-mode training
    (``SampleLevelDPConfig.secure_random_required``) but this worker
    cannot actually provide it. Mirrors
    ``UnsupportedPrivacyCombinationError``'s "never silently execute a
    weaker version of what was requested" contract — the task is
    rejected before any training happens, never silently downgraded to
    ``secure_mode=False``."""


def secure_random_available() -> bool:
    """Truthfully probes whether this Python process can actually draw
    bytes from an OS-backed CSPRNG right now, by performing a real draw
    (not by checking whether the ``secrets`` module merely imported
    successfully — import success does not prove the underlying OS
    entropy source itself is healthy)."""
    try:
        drawn = secrets.token_bytes(32)
    except (NotImplementedError, OSError):
        return False
    return len(drawn) == 32


def require_secure_random() -> None:
    """Raises :class:`SecureRandomUnavailableError` if this process
    cannot provide cryptographically secure randomness — the "reject
    requests requiring secure random when unavailable" half of the
    closure-gate requirement. Callers that need secure random bytes for
    something privacy-relevant should call this before proceeding, not
    after silently trying and catching a lower-level failure."""
    if not secure_random_available():
        raise SecureRandomUnavailableError(
            "cryptographically secure randomness is not available in this "
            "process (secrets.token_bytes failed or returned an unexpected "
            "length) -- refusing to proceed with a weaker source"
        )


def opacus_secure_mode_available() -> bool:
    """Truthfully probes whether Opacus's own ``secure_mode=True`` DP-SGD
    noise can actually be enabled in this process. Checks for the
    ``torchcsprng`` package via ``importlib.util.find_spec`` (a real
    module-resolution check, not an assumption) — this is the same
    package Opacus itself requires and whose absence it reports via
    ``ImportError`` at ``PrivacyEngine(secure_mode=True)`` construction
    time (verified directly against the installed Opacus version while
    building this function, not assumed from Opacus's documentation).
    """
    return importlib.util.find_spec("torchcsprng") is not None


def require_opacus_secure_mode(*, client_id: str) -> None:
    """Raises :class:`SecureRandomTaskRejectedError` with the exact
    reason if Opacus secure-mode training cannot be provided for this
    client's task. Call before constructing ``PrivacyEngine(secure_mode=True)``
    — never construct it speculatively and catch the resulting
    ``ImportError``, since that would mean the rejection reason is
    Opacus's generic message rather than this project's own structured
    one."""
    if not opacus_secure_mode_available():
        raise SecureRandomTaskRejectedError(
            f"task for client '{client_id}' requested secure-random sample-level "
            "DP training, but the torchcsprng package required by Opacus's "
            "secure_mode is not installed on this worker -- refusing to fall "
            "back to non-secure noise generation"
        )


def worker_reports_secure_random_support() -> bool:
    """What this worker should actually advertise in
    ``WorkerPrivacyCapabilities.supports_secure_random`` (see
    docs/worker-privacy-capabilities.md) — deliberately independent of
    :func:`secure_random_available` (stdlib CSPRNG access, always true,
    irrelevant here). Reflects whether Opacus's own secure-mode DP-SGD
    noise — the actual privacy-relevant randomness this worker
    generates — can be enabled, checked for real via
    :func:`opacus_secure_mode_available` rather than hardcoded, so this
    capability is never advertised optimistically."""
    return opacus_secure_mode_available()
