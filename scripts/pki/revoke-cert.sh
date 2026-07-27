#!/usr/bin/env bash
# Revokes a development-PKI leaf certificate and regenerates the CA's
# CRL — see docs/development-pki.md's revocation workflow and
# docs/key-management.md. This only updates the CA's own revocation
# records (index.txt + a regenerated crl.pem); it does NOT by itself
# notify a running coordinator that a worker/service is revoked — the
# coordinator's worker identity registry (Work Package G) is the
# authoritative, live revocation check for RPC-time rejection. This
# script's job is solely the PKI-layer bookkeeping a real deployment
# would use to populate that registry's revoked_at/revocation_reason
# fields from.
#
# Usage: scripts/pki/revoke-cert.sh <cert_path> <reason> [ca_dir]
#   reason: one of the OpenSSL CRL reason codes:
#           unspecified, keyCompromise, CACompromise, affiliationChanged,
#           superseded, cessationOfOperation, certificateHold
set -euo pipefail

# See issue-service-cert.sh's identical note: avoids consulting a broken
# system-default openssl.cnf for calls below that don't pass -config.
export OPENSSL_CONF=/dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CERT_PATH="${1:?usage: revoke-cert.sh <cert_path> <reason> [ca_dir]}"
REASON="${2:?usage: revoke-cert.sh <cert_path> <reason> [ca_dir]}"
CA_DIR="${3:-${REPO_ROOT}/certs/dev/ca}"

case "${REASON}" in
    unspecified|keyCompromise|CACompromise|affiliationChanged|superseded|cessationOfOperation|certificateHold)
        ;;
    *)
        echo "unrecognized revocation reason '${REASON}'; must be one of: unspecified, " \
             "keyCompromise, CACompromise, affiliationChanged, superseded, " \
             "cessationOfOperation, certificateHold" >&2
        exit 1
        ;;
esac

if [ ! -f "${CERT_PATH}" ]; then
    echo "certificate not found: ${CERT_PATH}" >&2
    exit 1
fi
if [ ! -f "${CA_DIR}/ca.key.pem" ]; then
    echo "No development CA found at ${CA_DIR} — nothing to revoke against." >&2
    exit 1
fi

SUBJECT="$(openssl x509 -in "${CERT_PATH}" -noout -subject)"
SERIAL="$(openssl x509 -in "${CERT_PATH}" -noout -serial | cut -d= -f2)"

openssl ca -config "${CA_DIR}/openssl-ca.cnf" -revoke "${CERT_PATH}" \
    -crl_reason "${REASON}"

openssl ca -config "${CA_DIR}/openssl-ca.cnf" -gencrl -out "${CA_DIR}/crl.pem"

echo "Revoked: ${SUBJECT} (serial ${SERIAL}), reason=${REASON}"
echo "CRL regenerated at ${CA_DIR}/crl.pem"
