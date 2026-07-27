"""Trusted coordinator signing-key bundle -- Coordinator-Signed Tasks
slice, Work Package K, strengthened in the Security Administration
slice, Work Packages E/F. See docs/trusted-coordinator-key-bundle.md.

A worker must never learn to trust a coordinator signing key from the
very task whose authenticity is in question -- that would be
self-referential and unverifiable. Instead, the coordinator writes its
current ACTIVE (and any GRACE_PERIOD) public key(s) to a bundle file at
startup/rotation (see cpp/coordinator/main.cpp's
FL_COORDINATOR_SIGNING_KEY_BUNDLE_PATH and
cpp/coordinator/src/trusted_key_bundle.cpp), delivered to workers out
of band -- the same "delivered like the CA cert, never fetched over
the connection being authenticated" trust model
docs/development-pki.md already uses for TLS. This module only ever
reads that file directly from local disk; it deliberately has no
RPC-based loading path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock


class CoordinatorTrustBundleError(RuntimeError):
    """Raised on a missing or malformed trust-bundle file -- never
    silently treated as "no trusted keys" (that would make every
    coordinator task fail closed for an unrelated reason, but should
    still be a loud, diagnosable error, not a silent empty bundle)."""


_SUPPORTED_SCHEMA_VERSION = 1


def _fnv1a_hash(data: bytes) -> int:
    """Must byte-for-byte match trusted_key_bundle.cpp's fnv1a_hash --
    accidental-corruption detection only, not a cryptographic integrity
    guarantee (see trusted_key_bundle.hpp's "Bundle signature" note)."""
    hash_value = 1469598103934665603
    for byte in data:
        hash_value ^= byte
        hash_value = (hash_value * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return hash_value


def _hash_to_hex(hash_value: int) -> str:
    return f"{hash_value:016x}"


def _verify_bundle_checksum(raw_text: str) -> bool:
    """Re-derives the checksum exactly the way trusted_key_bundle.cpp's
    write_trusted_key_bundle constructed it: the checksum is computed
    over the JSON body *up to and including* its final '}' -- before
    the ",\"checksum\":\"...\"" field was appended and a new closing
    brace added. Reconstructing that exact byte layout (not
    re-serializing the parsed JSON, which could reorder keys or
    reformat numbers differently) is what lets this be a real,
    independent verification rather than a tautology."""
    marker = ',"checksum":"'
    idx = raw_text.rfind(marker)
    if idx == -1:
        return False
    body = raw_text[:idx] + "}"
    checksum_start = idx + len(marker)
    checksum_end = raw_text.find('"', checksum_start)
    if checksum_end == -1:
        return False
    checksum_value = raw_text[checksum_start:checksum_end]
    return _hash_to_hex(_fnv1a_hash(body.encode("utf-8"))) == checksum_value


@dataclass(slots=True, frozen=True)
class TrustedCoordinatorKey:
    signing_key_id: str
    public_key_hex: str
    status: str
    public_key_fingerprint: str = ""
    created_at_unix_s: float = 0.0
    expires_at_unix_s: float = 0.0
    grace_period_end_unix_s: float = 0.0
    revoked_at_unix_s: float = 0.0


@dataclass(slots=True, frozen=True)
class TrustedCoordinatorKeyBundle:
    schema_version: int
    coordinator_identity: str
    bundle_version: int
    generated_at_unix_s: float
    active_signing_key_id: str
    keys: dict[str, TrustedCoordinatorKey] = field(default_factory=dict)


def load_trusted_coordinator_key_bundle(
    path: str | Path,
) -> TrustedCoordinatorKeyBundle:
    """Loads and validates the full bundle file (schema version,
    checksum) written by the coordinator. Raises
    CoordinatorTrustBundleError if the file is missing, unreadable,
    checksum-mismatched, or malformed. This is the strengthened
    Security Administration slice loader; load_trusted_coordinator_keys
    below is kept for existing callers that only need the plain
    signing_key_id -> TrustedCoordinatorKey mapping.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise CoordinatorTrustBundleError(
            f"no trusted-coordinator-key bundle found at {file_path} -- a worker "
            "cannot verify any coordinator-signed task without one"
        )
    try:
        raw_text = file_path.read_text(encoding="utf-8")
    except OSError as error:
        raise CoordinatorTrustBundleError(
            f"failed to read trusted-coordinator-key bundle at {file_path}: {error}"
        ) from error

    if not _verify_bundle_checksum(raw_text):
        raise CoordinatorTrustBundleError(
            f"trusted-coordinator-key bundle at {file_path} failed checksum "
            "verification -- the file is corrupt or was truncated"
        )

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise CoordinatorTrustBundleError(
            f"failed to parse trusted-coordinator-key bundle at {file_path}: {error}"
        ) from error
    if not isinstance(raw, dict) or not isinstance(raw.get("keys"), list):
        raise CoordinatorTrustBundleError(
            f"trusted-coordinator-key bundle at {file_path} is not in the expected "
            "shape"
        )

    schema_version = int(raw.get("schema_version", 0))
    if schema_version != _SUPPORTED_SCHEMA_VERSION:
        raise CoordinatorTrustBundleError(
            f"trusted-coordinator-key bundle at {file_path} has unsupported "
            f"schema_version {schema_version}"
        )

    keys: dict[str, TrustedCoordinatorKey] = {}
    active_count = 0
    for entry in raw["keys"]:
        try:
            key = TrustedCoordinatorKey(
                signing_key_id=str(entry["signing_key_id"]),
                public_key_hex=str(entry["public_key_hex"]),
                status=str(entry["status"]),
                public_key_fingerprint=str(entry.get("public_key_fingerprint", "")),
                created_at_unix_s=float(entry.get("created_at_unix_s", 0.0)),
                expires_at_unix_s=float(entry.get("expires_at_unix_s", 0.0)),
                grace_period_end_unix_s=float(
                    entry.get("grace_period_end_unix_s", 0.0)
                ),
                revoked_at_unix_s=float(entry.get("revoked_at_unix_s", 0.0)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CoordinatorTrustBundleError(
                f"malformed key entry in trusted-coordinator-key bundle at "
                f"{file_path}: {error}"
            ) from error
        if key.status == "active":
            active_count += 1
        keys[key.signing_key_id] = key
    if active_count > 1:
        raise CoordinatorTrustBundleError(
            f"trusted-coordinator-key bundle at {file_path} declares {active_count} "
            "ACTIVE keys -- at most one is ever valid"
        )

    return TrustedCoordinatorKeyBundle(
        schema_version=schema_version,
        coordinator_identity=str(raw.get("coordinator_identity", "")),
        bundle_version=int(raw.get("bundle_version", 0)),
        generated_at_unix_s=float(raw.get("generated_at_unix_s", 0.0)),
        active_signing_key_id=str(raw.get("active_signing_key_id", "")),
        keys=keys,
    )


def load_trusted_coordinator_keys(path: str | Path) -> dict[str, TrustedCoordinatorKey]:
    """Loads the bundle file written by the coordinator, keyed by
    signing_key_id. Raises CoordinatorTrustBundleError if the file is
    missing, unreadable, or malformed. Thin convenience wrapper over
    load_trusted_coordinator_key_bundle for callers that only need the
    key map, not the bundle's own metadata."""
    return load_trusted_coordinator_key_bundle(path).keys


@dataclass(slots=True, frozen=True)
class BundleReloadResult:
    accepted: bool
    changed: bool
    reason: str = "ok"


class TrustedCoordinatorKeyBundleReloader:
    """Stateful worker-side bundle loader -- Security Administration
    slice, Work Package F. Tracks the last-*validated* bundle_version
    and rejects a reload that would move backwards (a rollback attempt
    -- accidental or malicious) or that fails validation for any other
    reason, always keeping whatever bundle was previously valid rather
    than replacing it with something unverified. Thread-safe: reload()
    only replaces the in-memory bundle after the new one has been
    fully validated, under a lock, so a concurrent reader of
    current_keys()/current_bundle() never observes a partially-applied
    reload.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = Lock()
        # The very first load must succeed -- there is no "previous
        # valid bundle" to fall back to yet, matching
        # load_trusted_coordinator_key_bundle's existing fail-loud
        # convention.
        self._bundle = load_trusted_coordinator_key_bundle(self._path)

    def current_bundle(self) -> TrustedCoordinatorKeyBundle:
        with self._lock:
            return self._bundle

    def current_keys(self) -> dict[str, TrustedCoordinatorKey]:
        with self._lock:
            return self._bundle.keys

    def reload(self) -> BundleReloadResult:
        """Re-reads and validates the bundle file. On success with a
        newer bundle_version, replaces the in-memory bundle and returns
        accepted=True, changed=True. On success with the *same*
        bundle_version (nothing changed on disk), returns
        accepted=True, changed=False. On any validation failure
        (missing file, checksum mismatch, corrupt JSON, duplicate
        ACTIVE keys, or an older bundle_version than currently held --
        a rollback attempt), the previous valid bundle is kept
        unchanged and this returns accepted=False with `reason` set."""
        with self._lock:
            try:
                candidate = load_trusted_coordinator_key_bundle(self._path)
            except CoordinatorTrustBundleError as error:
                return BundleReloadResult(
                    accepted=False, changed=False, reason=str(error)
                )

            if candidate.bundle_version < self._bundle.bundle_version:
                return BundleReloadResult(
                    accepted=False,
                    changed=False,
                    reason=(
                        f"candidate bundle_version {candidate.bundle_version} is "
                        "older than the currently trusted bundle_version "
                        f"{self._bundle.bundle_version} -- rejecting a rollback"
                    ),
                )
            if candidate.bundle_version == self._bundle.bundle_version:
                return BundleReloadResult(accepted=True, changed=False)

            self._bundle = candidate
            return BundleReloadResult(accepted=True, changed=True)
