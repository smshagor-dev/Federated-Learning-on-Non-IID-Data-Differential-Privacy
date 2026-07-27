#!/usr/bin/env bash
# Automated end-to-end verification of the development PKI scripts
# (generate-dev-ca.sh / issue-service-cert.sh / issue-worker-cert.sh /
# revoke-cert.sh / inspect-certificates.py) -- Coordinator Transport
# Verification and Message Authenticity slice, Work Package D. See
# docs/development-pki.md.
#
# This does NOT touch certs/dev (the real development PKI used by the
# rest of the project's manual/Docker validation workflows) -- it
# creates its own throwaway CA and certificates in a fresh temp
# directory, exercises the full issue/inspect/revoke/CRL lifecycle
# against them, and deletes every private key it created before
# exiting, regardless of pass or fail (see the cleanup trap below).
# Safe to run repeatedly and safe to run in CI.
#
# Usage: scripts/pki/verify-pki.sh
# Exit code: 0 if every check passes, 1 on the first failure.
set -euo pipefail

export OPENSSL_CONF=/dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fl-pki-verify.XXXXXX")"
CA_DIR="${WORK_DIR}/ca"
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/fl-pki-verify-logs.XXXXXX")"

FAILURES=0

cleanup() {
    # Runs on every exit path (success, failure, or interruption) --
    # WORK_DIR holds real private key material (CA key + four leaf
    # keys) for the lifetime of this script only, and none of it is
    # meant to outlive the run. LOG_DIR holds only openssl's own
    # diagnostic text (no private key material ever passes through
    # stdout/stderr in these scripts) but is still transient scratch
    # output, so it is removed too.
    rm -rf "${WORK_DIR}" "${LOG_DIR}"
}
trap cleanup EXIT

pass() {
    echo "  PASS: $1"
}

fail() {
    echo "  FAIL: $1" >&2
    FAILURES=$((FAILURES + 1))
}

section() {
    echo ""
    echo "== $1 =="
}

section "1. Generating temporary development CA at ${WORK_DIR}"
if "${SCRIPT_DIR}/generate-dev-ca.sh" "${WORK_DIR}" >"${LOG_DIR}/ca.log" 2>&1; then
    pass "generate-dev-ca.sh succeeded"
else
    fail "generate-dev-ca.sh failed"
    cat "${LOG_DIR}/ca.log" >&2
    exit 1
fi
if [ -f "${CA_DIR}/ca.cert.pem" ] && [ -f "${CA_DIR}/ca.key.pem" ]; then
    pass "CA cert and key were written"
else
    fail "CA cert/key not found after generate-dev-ca.sh"
    exit 1
fi

section "2. Issuing coordinator, go-api, and two worker certificates"
"${SCRIPT_DIR}/issue-service-cert.sh" coordinator "${CA_DIR}" "${WORK_DIR}/services/coordinator" >"${LOG_DIR}/issue-coordinator.log" 2>&1
"${SCRIPT_DIR}/issue-service-cert.sh" go-api "${CA_DIR}" "${WORK_DIR}/services/go-api" >"${LOG_DIR}/issue-go-api.log" 2>&1
"${SCRIPT_DIR}/issue-worker-cert.sh" worker-1 "${CA_DIR}" "${WORK_DIR}/workers/worker-1" >"${LOG_DIR}/issue-worker-1.log" 2>&1
"${SCRIPT_DIR}/issue-worker-cert.sh" worker-2 "${CA_DIR}" "${WORK_DIR}/workers/worker-2" >"${LOG_DIR}/issue-worker-2.log" 2>&1

COORD_CERT="${WORK_DIR}/services/coordinator/tls.cert.pem"
GOAPI_CERT="${WORK_DIR}/services/go-api/tls.cert.pem"
WORKER1_CERT="${WORK_DIR}/workers/worker-1/tls.cert.pem"
WORKER2_CERT="${WORK_DIR}/workers/worker-2/tls.cert.pem"

for cert_path in "${COORD_CERT}" "${GOAPI_CERT}" "${WORKER1_CERT}" "${WORKER2_CERT}"; do
    if [ -f "${cert_path}" ]; then
        pass "issued ${cert_path#"${WORK_DIR}/"}"
    else
        fail "expected certificate not found: ${cert_path}"
    fi
done

section "3. Inspecting URI SAN identities"
INSPECT_OUTPUT="$(python "${SCRIPT_DIR}/inspect-certificates.py" \
    "${COORD_CERT}" "${GOAPI_CERT}" "${WORKER1_CERT}" "${WORKER2_CERT}")"
echo "${INSPECT_OUTPUT}" | sed 's/^/  /'

check_san() {
    local expected="$1"
    if echo "${INSPECT_OUTPUT}" | grep -qF "${expected}"; then
        pass "found expected URI SAN identity: ${expected}"
    else
        fail "expected URI SAN identity not found: ${expected}"
    fi
}
check_san "spiffe://federated-platform/service/coordinator"
check_san "spiffe://federated-platform/service/go-api"
check_san "spiffe://federated-platform/worker/worker-1"
check_san "spiffe://federated-platform/worker/worker-2"

section "4. Validating certificate chains against the CA"
for cert_path in "${COORD_CERT}" "${GOAPI_CERT}" "${WORKER1_CERT}" "${WORKER2_CERT}"; do
    label="${cert_path#"${WORK_DIR}/"}"
    if openssl verify -CAfile "${CA_DIR}/ca.cert.pem" "${cert_path}" >/dev/null 2>&1; then
        pass "chain validates: ${label}"
    else
        fail "chain does NOT validate: ${label}"
    fi
done

section "5. Revoking worker-2 and regenerating the CRL"
if "${SCRIPT_DIR}/revoke-cert.sh" "${WORKER2_CERT}" keyCompromise "${CA_DIR}" >"${LOG_DIR}/revoke.log" 2>&1; then
    pass "revoke-cert.sh succeeded"
else
    fail "revoke-cert.sh failed"
    cat "${LOG_DIR}/revoke.log" >&2
fi
if [ -f "${CA_DIR}/crl.pem" ]; then
    pass "CRL file was written"
else
    fail "CRL file not found after revocation"
fi

section "6. Verifying revoked status"
WORKER2_SERIAL="$(openssl x509 -in "${WORKER2_CERT}" -noout -serial | cut -d= -f2)"
if grep -qi "^R\b.*${WORKER2_SERIAL}" "${CA_DIR}/index.txt"; then
    pass "worker-2 (serial ${WORKER2_SERIAL}) is marked Revoked in index.txt"
else
    fail "worker-2 (serial ${WORKER2_SERIAL}) is NOT marked Revoked in index.txt"
fi
if openssl verify -CAfile "${CA_DIR}/ca.cert.pem" -crl_check \
    -CRLfile "${CA_DIR}/crl.pem" "${WORKER2_CERT}" >"${LOG_DIR}/crl-check-worker-2.log" 2>&1; then
    fail "openssl verify -crl_check unexpectedly ACCEPTED the revoked worker-2 certificate"
else
    pass "openssl verify -crl_check correctly REJECTS the revoked worker-2 certificate"
fi
# worker-1 was never revoked -- confirm the CRL check does not
# over-reject unrelated, still-valid certificates from the same CA.
if openssl verify -CAfile "${CA_DIR}/ca.cert.pem" -crl_check \
    -CRLfile "${CA_DIR}/crl.pem" "${WORKER1_CERT}" >/dev/null 2>&1; then
    pass "openssl verify -crl_check still ACCEPTS the non-revoked worker-1 certificate"
else
    fail "worker-1 (never revoked) was unexpectedly rejected by -crl_check"
fi

section "7. Confirming no dev-PKI private key material is Git-tracked"
# Belt-and-suspenders check for the standing rule that certs/dev (and
# this script's own throwaway WORK_DIR, which lives outside the repo
# entirely) must never end up committed -- see .gitignore's
# "certs/dev/" and "certs/dev*/" entries and docs/development-pki.md.
TRACKED_KEYS="$(cd "${REPO_ROOT}" && git ls-files -- 'certs/dev*' '*.key.pem' 2>/dev/null || true)"
if [ -z "${TRACKED_KEYS}" ]; then
    pass "no certs/dev* or *.key.pem files are tracked by git"
else
    fail "the following private-key-shaped paths are tracked by git: ${TRACKED_KEYS}"
fi

section "8. Cleanup"
echo "  (private key material at ${WORK_DIR} will be deleted by the exit trap)"

echo ""
if [ "${FAILURES}" -eq 0 ]; then
    echo "verify-pki.sh: all checks passed"
    exit 0
else
    echo "verify-pki.sh: ${FAILURES} check(s) failed" >&2
    exit 1
fi
