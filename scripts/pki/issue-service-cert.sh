#!/usr/bin/env bash
# Issues a development leaf certificate for a named platform service
# (coordinator, go-api) or, via issue-worker-cert.sh (which sources this
# script), a worker — see docs/development-pki.md. Every issued
# certificate carries a URI SAN identity in the form:
#   spiffe://federated-platform/service/<name>
#   spiffe://federated-platform/worker/<worker-id>
# This is a SPIFFE-style identity convention, not a claim that full
# SPIFFE/SPIRE infrastructure is implemented — see docs/mtls.md.
#
# Usage: scripts/pki/issue-service-cert.sh <service-name> [ca_dir] [output_dir] [days]
#   service-name: e.g. "coordinator" or "go-api" (also accepts
#                 "worker/<worker-id>" when called from issue-worker-cert.sh)
#   ca_dir:       defaults to certs/dev/ca
#   output_dir:   defaults to certs/dev/services/<service-name>
#                 (or certs/dev/workers/<worker-id> for worker identities)
#   days:         certificate lifetime, defaults to 90 (dev default —
#                 short enough that rotation is exercised routinely, per
#                 docs/key-management.md's rotation documentation
#                 requirement, not left untested by a decade-long cert)
set -euo pipefail

# This machine's system-default openssl.cnf can point at a broken path
# (observed: a stale PostgreSQL ODBC install's config location) that has
# nothing to do with this project — every openssl invocation below
# either passes -config explicitly (the `ca` subcommand) or needs no
# config at all (`ecparam`, `req -new -subj` for a CSR), so the system
# default is never actually needed; pointing OPENSSL_CONF at an empty
# file avoids it being consulted (and failing) at all.
export OPENSSL_CONF=/dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

SERVICE_NAME="${1:?usage: issue-service-cert.sh <service-name> [ca_dir] [output_dir] [days]}"
CA_DIR="${2:-${REPO_ROOT}/certs/dev/ca}"
DAYS="${4:-90}"

if [ ! -f "${CA_DIR}/ca.key.pem" ]; then
    echo "No development CA found at ${CA_DIR} — run generate-dev-ca.sh first." >&2
    exit 1
fi

case "${SERVICE_NAME}" in
    worker/*)
        WORKER_ID="${SERVICE_NAME#worker/}"
        if [ -z "${WORKER_ID}" ]; then
            echo "worker identity requires a non-empty worker id (worker/<id>)" >&2
            exit 1
        fi
        URI_SAN="spiffe://federated-platform/worker/${WORKER_ID}"
        DEFAULT_OUTPUT_DIR="${REPO_ROOT}/certs/dev/workers/${WORKER_ID}"
        CN="worker-${WORKER_ID}"
        EXT_KEY_USAGE="clientAuth"
        ;;
    *)
        URI_SAN="spiffe://federated-platform/service/${SERVICE_NAME}"
        DEFAULT_OUTPUT_DIR="${REPO_ROOT}/certs/dev/services/${SERVICE_NAME}"
        CN="${SERVICE_NAME}"
        # Services that only ever dial out (go-api as a coordinator
        # client) still get serverAuth+clientAuth issued uniformly here
        # — it is cheap, avoids a second code path, and every service in
        # this platform is a gRPC client of something at some point
        # (see docs/mtls.md's transport diagram).
        EXT_KEY_USAGE="serverAuth,clientAuth"
        ;;
esac

OUTPUT_DIR="${3:-${DEFAULT_OUTPUT_DIR}}"
mkdir -p "${OUTPUT_DIR}"

KEY_PATH="${OUTPUT_DIR}/tls.key.pem"
CSR_PATH="${OUTPUT_DIR}/tls.csr.pem"
CERT_PATH="${OUTPUT_DIR}/tls.cert.pem"
EXT_CNF_PATH="${OUTPUT_DIR}/ext.cnf"

if [ -f "${KEY_PATH}" ]; then
    echo "A certificate already exists at ${OUTPUT_DIR} — refusing to overwrite it." >&2
    echo "Use revoke-cert.sh then re-issue, or pick a different output_dir, for rotation." >&2
    exit 1
fi

openssl ecparam -name prime256v1 -genkey -noout -out "${KEY_PATH}"
chmod 600 "${KEY_PATH}"

# CN is set inside this config file, not via -subj on the command line —
# -subj "/CN=..." would work on Linux/macOS/CI, but Git Bash for Windows
# (MSYS) auto-converts argv entries that look like POSIX absolute paths
# ("/CN=...") into Windows paths, silently corrupting it. Routing it
# through a generated config file sidesteps that platform quirk entirely
# rather than fighting MSYS's path-conversion heuristics.
cat > "${EXT_CNF_PATH}" <<EOF
[req]
distinguished_name = req_distinguished_name
prompt = no

[req_distinguished_name]
CN = ${CN}

[server_ext]
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = ${EXT_KEY_USAGE}
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
subjectAltName = @alt_names

[alt_names]
URI.1 = ${URI_SAN}
DNS.1 = localhost
DNS.2 = ${CN}
IP.1 = 127.0.0.1
EOF

openssl req -new -key "${KEY_PATH}" -out "${CSR_PATH}" -config "${EXT_CNF_PATH}"

openssl ca -config "${CA_DIR}/openssl-ca.cnf" -batch -notext \
    -days "${DAYS}" -in "${CSR_PATH}" -out "${CERT_PATH}" \
    -extfile "${EXT_CNF_PATH}" -extensions server_ext

rm -f "${CSR_PATH}"

echo "Issued certificate for identity ${URI_SAN}"
echo "  ${KEY_PATH}  (private — Git-ignored)"
echo "  ${CERT_PATH} (public)"
openssl x509 -in "${CERT_PATH}" -noout -subject -dates -serial -fingerprint -sha256
