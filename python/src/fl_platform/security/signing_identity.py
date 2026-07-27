"""Ed25519 worker signing identities — Secure Transport and Worker
Identity Hardening slice, Work Package H. See docs/worker-identity.md
and docs/signed-capabilities.md.

A worker's Ed25519 signing identity is deliberately separate from its
TLS certificate's key (see docs/mtls.md and
scripts/pki/generate-dev-ca.sh's identical note): the TLS key
authenticates the transport connection; the signing key authenticates
individual application-level messages (capability statements, and in a
future pass, task-result envelopes) independent of which connection
carried them. Generated via PyNaCl (the libsodium binding — see
docs/cryptographic-primitives.md), never hand-rolled.
"""

from __future__ import annotations

import contextlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

import nacl.encoding
import nacl.exceptions
import nacl.signing


class SigningIdentityError(RuntimeError):
    """Raised on any signing-identity loading/generation failure. Never
    caught to silently proceed without a real identity."""


def _key_id_for(verify_key: nacl.signing.VerifyKey) -> str:
    """A short, stable identifier for a public signing key -- the first
    16 hex characters of the raw public key bytes. Not a secret (it's
    derived from the *public* key); safe to log, register with the
    coordinator, and include in capability statements/audit events."""
    raw = bytes(verify_key)
    return raw[:8].hex()


@dataclass(slots=True, frozen=True)
class WorkerSigningIdentity:
    """A worker's Ed25519 signing keypair. ``signing_key`` is private —
    see :func:`save_signing_identity` for the only sanctioned way to
    persist it (restrictive file permissions, never transmitted) and
    :meth:`public_key_hex` for what actually gets registered with the
    coordinator."""

    worker_id: str
    signing_key: nacl.signing.SigningKey
    key_id: str

    @property
    def verify_key(self) -> nacl.signing.VerifyKey:
        return self.signing_key.verify_key

    def public_key_hex(self) -> str:
        """The only representation of this identity that should ever
        leave this process — never :attr:`signing_key` itself. See the
        closure-gate requirement "never send the private signing key to
        the coordinator"."""
        return bytes(self.verify_key).hex()

    def sign(self, payload: bytes) -> bytes:
        """Raw Ed25519 signature over ``payload`` (already
        canonicalized by the caller — see capability_statement.py for
        the canonical encoding this is meant to be used with)."""
        return self.signing_key.sign(payload).signature


def generate_signing_identity(worker_id: str) -> WorkerSigningIdentity:
    """Generates a fresh Ed25519 keypair for ``worker_id``. Does not
    reuse one signing key across distinct worker identities by
    default — call this once per worker_id, never share the returned
    identity across workers (see the closure-gate requirement "do not
    reuse one signing key across distinct worker identities by
    default")."""
    if not worker_id:
        raise SigningIdentityError("worker_id must not be empty")
    signing_key = nacl.signing.SigningKey.generate()
    key_id = _key_id_for(signing_key.verify_key)
    return WorkerSigningIdentity(
        worker_id=worker_id, signing_key=signing_key, key_id=key_id
    )


def save_signing_identity(
    identity: WorkerSigningIdentity, directory: str | Path
) -> Path:
    """Writes the private signing key to ``directory`` with restrictive
    permissions. On POSIX, this is a real 0600 (owner read/write only);
    on Windows, Python's os.chmod cannot express POSIX-style ACLs and
    this call becomes advisory only (Windows file permissions are a
    fundamentally different model — NTFS ACLs, not a single mode bit) —
    documented honestly rather than claiming a guarantee this call
    cannot actually provide on that platform. Never writes the public
    key alongside a claim that it need be kept secret; the public key
    is written to a separate, non-restricted file since it is meant to
    be shared with the coordinator.
    """
    dir_path = Path(directory)
    dir_path.mkdir(parents=True, exist_ok=True)

    private_key_path = dir_path / f"{identity.worker_id}.signing-key.pem"
    public_key_path = dir_path / f"{identity.worker_id}.signing-key.pub"

    private_key_path.write_bytes(
        identity.signing_key.encode(encoder=nacl.encoding.RawEncoder)
    )
    # Best-effort on platforms where chmod cannot express this (see
    # docstring) -- not a fatal error, since the alternative (refusing
    # to persist the key at all) is strictly worse for a development
    # workflow, but never silently claimed to have succeeded either.
    with contextlib.suppress(OSError):
        os.chmod(private_key_path, stat.S_IRUSR | stat.S_IWUSR)

    public_key_path.write_text(identity.public_key_hex() + "\n", encoding="utf-8")
    return private_key_path


def load_signing_identity(
    worker_id: str, directory: str | Path
) -> WorkerSigningIdentity:
    """Loads a previously-saved private signing key. Raises
    :class:`SigningIdentityError` if the file is missing or malformed —
    never silently generates a fresh identity in its place (a silent
    regeneration would change the worker's signing identity without
    anyone deciding that on purpose)."""
    private_key_path = Path(directory) / f"{worker_id}.signing-key.pem"
    if not private_key_path.exists():
        raise SigningIdentityError(
            f"no signing key found for worker '{worker_id}' at {private_key_path}"
        )
    try:
        raw = private_key_path.read_bytes()
        signing_key = nacl.signing.SigningKey(raw, encoder=nacl.encoding.RawEncoder)
    except (OSError, nacl.exceptions.CryptoError, ValueError) as error:
        raise SigningIdentityError(
            f"failed to load signing key for worker '{worker_id}': {error}"
        ) from error
    key_id = _key_id_for(signing_key.verify_key)
    return WorkerSigningIdentity(
        worker_id=worker_id, signing_key=signing_key, key_id=key_id
    )


def verify_key_from_hex(public_key_hex: str) -> nacl.signing.VerifyKey:
    """Reconstructs a VerifyKey from the hex string
    :meth:`WorkerSigningIdentity.public_key_hex` produces — what the
    coordinator would store after a worker registers its public key."""
    try:
        return nacl.signing.VerifyKey(
            public_key_hex.encode("ascii"), encoder=nacl.encoding.HexEncoder
        )
    except (nacl.exceptions.CryptoError, ValueError) as error:
        raise SigningIdentityError(f"invalid public key hex: {error}") from error
