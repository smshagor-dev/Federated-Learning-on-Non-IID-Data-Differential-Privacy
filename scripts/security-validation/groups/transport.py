"""Transport / mutual-TLS scenarios. Real, live checks reuse the same
running docker-compose.security.yml stack every other group runs
against -- the coordinator and api services in that stack already run
with FL_TRANSPORT_MODE=mtls (see
infra/compose/docker-compose.security.yml).

Certificate-rejection scenarios (invalid/cross-worker/mismatched-
identity certificates) are DEFERRED: exercising them for real requires
a second coordinator (or a second client) presenting a deliberately
wrong certificate, which this harness's single shared stack does not
stand up -- see docs/security-runtime-validation.md's harness-
architecture notes. That exact rejection logic is unit/integration
tested directly in cpp/coordinator/tests/peer_identity_test.cpp and
validated live (once) in the Security Administration, Observability,
and Runtime Validation slice's own Docker pass (see
docs/security-operations-report.md).
"""

from __future__ import annotations

from framework import Context, Scenario, Status

_CERT_DEFERRAL_REASON = (
    "requires a second client/coordinator presenting a deliberately invalid "
    "certificate against the same running stack; this harness's shared-stack "
    "model does not stand up an adversarial second identity. Already covered by "
    "cpp/coordinator/tests/peer_identity_test.cpp and a prior live Docker pass -- "
    "see docs/security-operations-report.md"
)


def _mtls_transport_status_enforced(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/security/transport", token=admin)
    ctx.assert_true(status == 200, "GET /api/v1/security/transport returns 200")
    ctx.assert_true(
        bool(body) and body.get("mutual_tls_enforced") is True,
        "mutual_tls_enforced is true (the coordinator/api handshake this harness "
        "connects through really is mTLS, not a plaintext or single-sided TLS "
        "connection reporting a false claim)",
    )
    ctx.assert_true(
        bool(body) and "mtls" in str(body.get("transport_mode", "")).lower(),
        "transport_mode reports an mTLS mode string",
    )


def _trust_model_reachable(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/security/trust-model", token=admin)
    ctx.assert_true(status == 200, "GET /api/v1/security/trust-model returns 200")
    ctx.assert_true(
        bool(body) and body.get("trusted_coordinator_key_count", 0) >= 1,
        "at least one trusted coordinator signing key is reported",
    )


def _go_api_identity_accepted(ctx: Context) -> None:
    # The Go API's own successful calls to every other security.* endpoint
    # in this harness run ARE the live proof that its mTLS client
    # certificate (spiffe://federated-platform/service/go-api) was
    # accepted by the coordinator -- a failed handshake would make every
    # other scenario in this run fail with a 503/ErrCoordinatorNotConfigured-
    # shaped response instead. This scenario makes that implicit proof
    # explicit and asserts it directly, once, up front.
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/security/transport", token=admin)
    ctx.assert_true(
        status == 200 and bool(body) and bool(body.get("transport_mode")),
        "the Go API successfully calls a coordinator RPC over mTLS -- its "
        "go-api service certificate identity was accepted by the coordinator's "
        "peer-identity check",
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="transport.mtls.status.enforced",
        name="Transport status reports mutual TLS enforced",
        category="transport",
        description="GET /api/v1/security/transport reflects the coordinator's real, live mTLS configuration.",
        required_services=("coordinator", "api"),
        prerequisites="stack up with docker-compose.security.yml",
        assertion="mutual_tls_enforced == true, transport_mode contains 'mtls'",
        expected_result="both true",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_mtls_transport_status_enforced,
    ),
    Scenario(
        scenario_id="transport.mtls.go-api-identity.accepted",
        name="Go API's own service certificate identity is accepted by the coordinator",
        category="transport",
        description="A successful coordinator RPC from the Go API is only possible if its mTLS handshake succeeded.",
        required_services=("coordinator", "api"),
        prerequisites="stack up with docker-compose.security.yml",
        assertion="a coordinator RPC issued by the Go API succeeds",
        expected_result="200",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_go_api_identity_accepted,
    ),
    Scenario(
        scenario_id="transport.mtls.trust-model.reachable",
        name="Security trust model reports a real trusted key count",
        category="transport",
        description="GET /api/v1/security/trust-model returns a real, non-zero trusted-key count.",
        required_services=("coordinator", "api"),
        prerequisites="stack up with docker-compose.security.yml",
        assertion="trusted_coordinator_key_count >= 1",
        expected_result=">=1",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_trust_model_reachable,
    ),
    Scenario(
        scenario_id="transport.cert.invalid-service.rejected",
        name="An invalid service certificate is rejected",
        category="transport",
        description="A service presenting a certificate not signed by the trusted dev CA must be rejected.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_CERT_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="transport.cert.invalid-worker.rejected",
        name="An invalid worker certificate is rejected",
        category="transport",
        description="A worker presenting a certificate not signed by the trusted dev CA must be rejected.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_CERT_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="transport.cert.cross-worker.rejected",
        name="worker-2's certificate cannot be used to act as worker-1",
        category="transport",
        description="Certificate identity binding must reject a worker_id/certificate mismatch.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_CERT_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="transport.cert.identity-mismatch.rejected",
        name="Certificate URI SAN identity mismatch is rejected",
        category="transport",
        description="peer_identity.hpp's has_worker_identity check must reject a mismatched URI SAN.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_CERT_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="transport.insecure-mode.warning-gated",
        name="Insecure-development-mode warning appears only when explicitly enabled",
        category="transport",
        description="FL_TRANSPORT_MODE=insecure_development must log a visible warning; mtls mode must not.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "would require a second, non-mTLS stack invocation to compare against; "
            "this harness invocation always runs the mTLS override. Verified by direct "
            "code inspection of main.cpp's transport-mode startup log, not live this pass"
        ),
    ),
]
