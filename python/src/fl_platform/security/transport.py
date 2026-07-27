"""mTLS channel construction for the Python worker's gRPC connection to
the C++ coordinator — Secure Transport and Worker Identity Hardening
slice work. See docs/mtls.md.

Deliberately independent of ``fl_platform.worker.coordinator_client``
(which needs the generated protobuf bindings on ``sys.path`` before it
can even be imported — see ``ensure_generated_on_path``): this module
only needs ``grpc`` itself, so its credential-construction logic is
directly unit-testable without any protobuf-generation step, and
``GrpcCoordinatorClient`` wires it in as a thin integration point.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class TransportMode(StrEnum):
    INSECURE_DEVELOPMENT = "insecure_development"
    TLS = "tls"
    MTLS = "mtls"


class TransportConfigurationError(RuntimeError):
    """Raised when TLS/mTLS channel construction cannot proceed —
    never caught to silently fall back to an insecure channel. A caller
    that cannot tolerate this exception should not have requested a
    secure transport mode."""


@dataclass(slots=True, frozen=True)
class WorkerTLSConfig:
    """Everything needed to build a secure channel to the coordinator.

    ``client_cert_path``/``client_key_path`` are this worker's own
    identity, presented to the coordinator during the handshake — both
    must be set together for mTLS, or both left empty for TLS-only
    (server-authenticated, no client certificate presented).
    ``expected_server_name`` is the identity the coordinator's
    certificate must present (this project's certificates use a URI SAN
    identity, e.g. ``spiffe://federated-platform/service/coordinator`` —
    see docs/development-pki.md — not a DNS name matching the dial
    address, so this should almost always be set explicitly in a real
    deployment).
    """

    trusted_ca_path: str
    client_cert_path: str = ""
    client_key_path: str = ""
    expected_server_name: str = ""


def _read_bytes(path: str, label: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as error:
        raise TransportConfigurationError(
            f"failed to read {label} at '{path}': {error}"
        ) from error


def transport_mode_for(config: WorkerTLSConfig | None) -> TransportMode:
    """What mode a channel built from ``config`` will actually use —
    safe to record in audit metadata / worker capability statements
    without needing to actually build the channel first."""
    if config is None:
        return TransportMode.INSECURE_DEVELOPMENT
    if config.client_cert_path:
        return TransportMode.MTLS
    return TransportMode.TLS


def build_channel_credentials(config: WorkerTLSConfig) -> Any:
    """Loads certificates/keys/CA from disk and returns real
    ``grpc.ChannelCredentials`` — never a silent fallback to plaintext
    on any loading failure (raises :class:`TransportConfigurationError`
    instead, matching the closure-gate requirement "fail closed")."""
    import grpc  # noqa: PLC0415 - deferred, matches the rest of this codebase's grpc imports

    root_certificates = _read_bytes(config.trusted_ca_path, "trusted CA")

    has_cert = bool(config.client_cert_path)
    has_key = bool(config.client_key_path)
    if has_cert != has_key:
        raise TransportConfigurationError(
            "client_cert_path and client_key_path must both be set (mTLS) or both "
            "empty (server-only TLS), not one without the other"
        )

    private_key: bytes | None = None
    certificate_chain: bytes | None = None
    if has_cert and has_key:
        private_key = _read_bytes(config.client_key_path, "worker private key")
        certificate_chain = _read_bytes(config.client_cert_path, "worker certificate")

    return grpc.ssl_channel_credentials(
        root_certificates=root_certificates,
        private_key=private_key,
        certificate_chain=certificate_chain,
    )


def build_secure_channel(address: str, config: WorkerTLSConfig) -> Any:
    """Builds a real ``grpc.secure_channel`` for ``address``. Raises
    :class:`TransportConfigurationError` on any credential-loading
    failure; never returns an insecure channel."""
    import grpc  # noqa: PLC0415

    credentials = build_channel_credentials(config)
    options: tuple[tuple[str, str], ...] = ()
    if config.expected_server_name:
        # grpc's documented mechanism for verifying the server presents
        # a specific identity, independent of whatever hostname the
        # dial address itself contains -- this project's server
        # certificates carry a URI SAN identity, not necessarily a DNS
        # name matching the dial address (see WorkerTLSConfig's doc
        # comment), so this must be set explicitly rather than relying
        # on default SNI-derived-from-address behavior.
        options = (("grpc.ssl_target_name_override", config.expected_server_name),)
    return grpc.secure_channel(address, credentials, options=options)
