"""Tests for fl_platform.security.transport -- the Python worker's mTLS
channel construction (Secure Transport and Worker Identity Hardening
slice). Uses a real local gRPC server secured with certificates
generated fresh via the `cryptography` package for every test run
(never depending on scripts/pki's gitignored output existing on disk),
proving genuine interoperability, not a mocked handshake.
"""

from __future__ import annotations

import datetime
import socket
import unittest
from concurrent import futures
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from fl_platform.security.transport import (
    TransportConfigurationError,
    TransportMode,
    WorkerTLSConfig,
    build_channel_credentials,
    build_secure_channel,
    transport_mode_for,
)


def _generate_ca() -> tuple[bytes, x509.Certificate, ec.EllipticCurvePrivateKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "test-dev-root-ca")]
    )
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(hours=24))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    return pem, cert, key


def _issue_leaf(
    ca_cert: x509.Certificate,
    ca_key: ec.EllipticCurvePrivateKey,
    common_name: str,
    dns_names: list[str],
    tmp_dir: Path,
) -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    san_entries: list[x509.GeneralName] = [x509.DNSName(name) for name in dns_names]
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(hours=1))
        .not_valid_after(now + datetime.timedelta(hours=24))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
    )
    cert = builder.sign(ca_key, hashes.SHA256())

    cert_path = tmp_dir / f"{common_name}.cert.pem"
    key_path = tmp_dir / f"{common_name}.key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return str(cert_path), str(key_path)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _HealthServicer:
    """A minimal stand-in for CoordinatorServiceServicer -- only what's
    needed to prove an RPC round-trips over the secured channel, not a
    full coordinator implementation."""


def _start_real_server(
    server_cert_path: str,
    server_key_path: str,
    ca_pem: bytes,
    *,
    require_client_auth: bool,
):
    import grpc
    from fl_platform.rpc import ensure_generated_on_path

    ensure_generated_on_path()
    from coordinator import (  # type: ignore[import-not-found]
        coordinator_pb2,
        coordinator_pb2_grpc,
    )

    class HealthServicer(coordinator_pb2_grpc.CoordinatorServiceServicer):
        def Health(self, request, context):  # noqa: N802 - grpc-generated method name
            return coordinator_pb2.HealthResponse(status="SERVING")

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    coordinator_pb2_grpc.add_CoordinatorServiceServicer_to_server(
        HealthServicer(), server
    )

    server_key = Path(server_key_path).read_bytes()
    server_cert = Path(server_cert_path).read_bytes()
    credentials = grpc.ssl_server_credentials(
        [(server_key, server_cert)],
        root_certificates=ca_pem if require_client_auth else None,
        require_client_auth=require_client_auth,
    )
    port = _free_port()
    server.add_secure_port(f"127.0.0.1:{port}", credentials)
    server.start()
    return server, f"127.0.0.1:{port}"


class RealMTLSHandshakeTests(unittest.TestCase):
    """One genuine end-to-end mTLS RPC round trip -- not mocked."""

    def test_health_rpc_succeeds_over_real_mtls(self) -> None:
        import tempfile

        from fl_platform.rpc import ensure_generated_on_path

        ensure_generated_on_path()
        from coordinator import coordinator_pb2  # type: ignore[import-not-found]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            ca_pem, ca_cert, ca_key = _generate_ca()
            ca_path = tmp_dir / "ca.cert.pem"
            ca_path.write_bytes(ca_pem)

            server_cert_path, server_key_path = _issue_leaf(
                ca_cert, ca_key, "coordinator", ["localhost", "127.0.0.1"], tmp_dir
            )
            client_cert_path, client_key_path = _issue_leaf(
                ca_cert, ca_key, "worker-1", ["localhost", "127.0.0.1"], tmp_dir
            )

            server, address = _start_real_server(
                server_cert_path, server_key_path, ca_pem, require_client_auth=True
            )
            try:
                config = WorkerTLSConfig(
                    trusted_ca_path=str(ca_path),
                    client_cert_path=client_cert_path,
                    client_key_path=client_key_path,
                    expected_server_name="localhost",
                )
                channel = build_secure_channel(address, config)
                from coordinator import (  # type: ignore[import-not-found]
                    coordinator_pb2_grpc,
                )

                stub = coordinator_pb2_grpc.CoordinatorServiceStub(channel)
                response = stub.Health(
                    coordinator_pb2.HealthRequest(trace_id="test"), timeout=5.0
                )
                self.assertEqual(response.status, "SERVING")
                channel.close()
            finally:
                server.stop(grace=None)

    def test_untrusted_ca_is_rejected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            real_ca_pem, real_ca_cert, real_ca_key = _generate_ca()
            wrong_ca_pem, wrong_ca_cert, wrong_ca_key = _generate_ca()

            server_cert_path, server_key_path = _issue_leaf(
                real_ca_cert, real_ca_key, "coordinator", ["localhost"], tmp_dir
            )
            client_cert_path, client_key_path = _issue_leaf(
                wrong_ca_cert, wrong_ca_key, "worker-1", ["localhost"], tmp_dir
            )
            wrong_ca_path = tmp_dir / "wrong-ca.cert.pem"
            wrong_ca_path.write_bytes(wrong_ca_pem)

            server, address = _start_real_server(
                server_cert_path,
                server_key_path,
                real_ca_pem,
                require_client_auth=False,
            )
            try:
                config = WorkerTLSConfig(
                    trusted_ca_path=str(wrong_ca_path),  # client trusts the WRONG ca
                    expected_server_name="localhost",
                )
                channel = build_secure_channel(address, config)
                from fl_platform.rpc import ensure_generated_on_path

                ensure_generated_on_path()
                from coordinator import (  # type: ignore[import-not-found]
                    coordinator_pb2,
                    coordinator_pb2_grpc,
                )

                stub = coordinator_pb2_grpc.CoordinatorServiceStub(channel)
                request = coordinator_pb2.HealthRequest(trace_id="test")
                with self.assertRaises(Exception):  # noqa: B017 - grpc.RpcError, real rejection
                    stub.Health(request, timeout=5.0)
                channel.close()
            finally:
                server.stop(grace=None)


class ChannelCredentialConstructionTests(unittest.TestCase):
    def test_rejects_missing_ca_file(self) -> None:
        config = WorkerTLSConfig(trusted_ca_path="/does/not/exist.pem")
        with self.assertRaises(TransportConfigurationError):
            build_channel_credentials(config)

    def test_rejects_client_cert_without_key(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            ca_pem, _ca_cert, _ca_key = _generate_ca()
            ca_path = tmp_dir / "ca.cert.pem"
            ca_path.write_bytes(ca_pem)
            config = WorkerTLSConfig(
                trusted_ca_path=str(ca_path),
                client_cert_path="/some/cert.pem",
                client_key_path="",
            )
            with self.assertRaises(TransportConfigurationError):
                build_channel_credentials(config)

    def test_builds_real_credentials_for_valid_tls_only_config(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            ca_pem, _ca_cert, _ca_key = _generate_ca()
            ca_path = tmp_dir / "ca.cert.pem"
            ca_path.write_bytes(ca_pem)
            config = WorkerTLSConfig(trusted_ca_path=str(ca_path))
            credentials = build_channel_credentials(config)
            self.assertIsNotNone(credentials)


class TransportModeTests(unittest.TestCase):
    def test_none_config_is_insecure_development(self) -> None:
        self.assertEqual(transport_mode_for(None), TransportMode.INSECURE_DEVELOPMENT)

    def test_config_without_client_cert_is_tls(self) -> None:
        config = WorkerTLSConfig(trusted_ca_path="/some/ca.pem")
        self.assertEqual(transport_mode_for(config), TransportMode.TLS)

    def test_config_with_client_cert_is_mtls(self) -> None:
        config = WorkerTLSConfig(
            trusted_ca_path="/some/ca.pem",
            client_cert_path="/some/cert.pem",
            client_key_path="/some/key.pem",
        )
        self.assertEqual(transport_mode_for(config), TransportMode.MTLS)


if __name__ == "__main__":
    unittest.main()
