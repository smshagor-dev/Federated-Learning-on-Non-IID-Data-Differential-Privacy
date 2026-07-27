"""Worker-side signing-key rotation state -- Signing-Key Lifecycle
slice, Work Package F. See docs/key-rotation.md.

Separate from signing_identity.py's save_signing_identity/
load_signing_identity (which key private-key files by worker_id alone,
one file per worker) because rotation needs multiple keys to coexist on
disk during a grace period: the still-valid previous key and the new
preferred key. Files here are keyed by (worker_id, key_id) instead --
signing_identity.py itself is not modified (per the standing "do not
rewrite working signed capabilities/envelopes without a proven defect"
instruction; nothing there is broken, this is an additive capability).
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

import nacl.encoding
import nacl.exceptions
import nacl.signing

from fl_platform.security.signing_identity import WorkerSigningIdentity


def _key_id_for(verify_key: nacl.signing.VerifyKey) -> str:
    """Must byte-for-byte match signing_identity.py's private
    _key_id_for -- duplicated rather than imported across the module
    boundary (that helper is intentionally private there); both
    computations are the same one-line derivation (first 8 bytes of the
    raw public key, hex-encoded) so there is no real drift risk."""
    return bytes(verify_key)[:8].hex()


__all__ = [
    "KeyRotationStateError",
    "WorkerKeyRotationState",
    "generate_rotated_signing_identity",
    "load_keyed_signing_identity",
    "load_rotation_state",
    "save_keyed_signing_identity",
    "save_rotation_state",
]


class KeyRotationStateError(RuntimeError):
    """Raised on any rotation-state loading/saving failure. Never
    caught to silently proceed with an inconsistent local key state."""


def _private_key_path(directory: str | Path, worker_id: str, key_id: str) -> Path:
    return Path(directory) / f"{worker_id}.{key_id}.signing-key.pem"


def _public_key_path(directory: str | Path, worker_id: str, key_id: str) -> Path:
    return Path(directory) / f"{worker_id}.{key_id}.signing-key.pub"


def generate_rotated_signing_identity(worker_id: str) -> WorkerSigningIdentity:
    """Generates a fresh Ed25519 keypair to rotate to -- a thin,
    intention-revealing wrapper over signing_identity.generate_signing_identity
    (reused unchanged, not re-implemented)."""
    from fl_platform.security.signing_identity import generate_signing_identity

    return generate_signing_identity(worker_id)


def save_keyed_signing_identity(
    identity: WorkerSigningIdentity, directory: str | Path
) -> Path:
    """Persists a private signing key keyed by (worker_id, key_id) so
    multiple keys for the same worker can coexist on disk during a
    grace period -- unlike signing_identity.save_signing_identity,
    which always overwrites the single per-worker file. Same
    restrictive-permissions-best-effort behavior on Windows (see that
    function's docstring for why chmod there is advisory only)."""
    dir_path = Path(directory)
    dir_path.mkdir(parents=True, exist_ok=True)

    private_key_path = _private_key_path(dir_path, identity.worker_id, identity.key_id)
    public_key_path = _public_key_path(dir_path, identity.worker_id, identity.key_id)

    private_key_path.write_bytes(
        identity.signing_key.encode(encoder=nacl.encoding.RawEncoder)
    )
    with contextlib.suppress(OSError):
        os.chmod(private_key_path, stat.S_IRUSR | stat.S_IWUSR)

    public_key_path.write_text(identity.public_key_hex() + "\n", encoding="utf-8")
    return private_key_path


def load_keyed_signing_identity(
    worker_id: str, key_id: str, directory: str | Path
) -> WorkerSigningIdentity:
    """Loads a previously-saved, key-id-keyed private signing key.
    Raises KeyRotationStateError if missing or malformed -- never
    silently generates a fresh identity in its place."""
    private_key_path = _private_key_path(Path(directory), worker_id, key_id)
    if not private_key_path.exists():
        raise KeyRotationStateError(
            f"no signing key found for worker '{worker_id}' key '{key_id}' at "
            f"{private_key_path}"
        )
    try:
        raw = private_key_path.read_bytes()
        signing_key = nacl.signing.SigningKey(raw, encoder=nacl.encoding.RawEncoder)
    except (OSError, nacl.exceptions.CryptoError, ValueError) as error:
        raise KeyRotationStateError(
            f"failed to load signing key for worker '{worker_id}' key '{key_id}': "
            f"{error}"
        ) from error
    derived_key_id = _key_id_for(signing_key.verify_key)
    if derived_key_id != key_id:
        raise KeyRotationStateError(
            f"signing key at {private_key_path} does not derive to the expected key_id "
            f"'{key_id}' (got '{derived_key_id}') -- refusing to load a mismatched key"
        )
    return WorkerSigningIdentity(
        worker_id=worker_id, signing_key=signing_key, key_id=derived_key_id
    )


@dataclass(slots=True, frozen=True)
class WorkerKeyRotationState:
    """Local, restart-safe record of which signing key a worker process
    currently considers "preferred" (the one register_worker/submit_result
    should sign with going forward), and which prior key (if any) is
    still retained during its grace period. Written atomically
    (temp-file-then-replace) -- see save_rotation_state."""

    worker_id: str
    current_key_id: str
    previous_key_id: str = ""
    grace_period_end_unix_s: float = 0.0


def load_rotation_state(path: str | Path) -> WorkerKeyRotationState | None:
    """Returns None if no state file exists yet (a worker that has
    never rotated) -- raises KeyRotationStateError if the file exists
    but is malformed, never silently discarding it (a silent discard
    could make a worker forget which key is actually preferred)."""
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise KeyRotationStateError(
            f"failed to load rotation state from {file_path}: {error}"
        ) from error
    try:
        return WorkerKeyRotationState(
            worker_id=raw["worker_id"],
            current_key_id=raw["current_key_id"],
            previous_key_id=raw.get("previous_key_id", ""),
            grace_period_end_unix_s=float(raw.get("grace_period_end_unix_s", 0.0)),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise KeyRotationStateError(
            f"malformed rotation state in {file_path}: {error}"
        ) from error


def save_rotation_state(state: WorkerKeyRotationState, path: str | Path) -> None:
    """Atomic temp-file-then-replace write, matching every other
    persistence class in this codebase's convention (never a partial
    write left in place if the process is killed mid-write)."""
    file_path = Path(path)
    if file_path.parent != Path():
        file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "worker_id": state.worker_id,
        "current_key_id": state.current_key_id,
        "previous_key_id": state.previous_key_id,
        "grace_period_end_unix_s": state.grace_period_end_unix_s,
    }
    with tempfile.NamedTemporaryFile(
        "w", dir=file_path.parent or ".", delete=False, suffix=".tmp", encoding="utf-8"
    ) as handle:
        json.dump(payload, handle)
        temp_path = Path(handle.name)
    temp_path.replace(file_path)
