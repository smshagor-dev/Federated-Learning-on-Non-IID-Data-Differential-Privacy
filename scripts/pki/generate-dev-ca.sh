#!/usr/bin/env bash
# Development-only root CA generator for the Secure Transport and Worker
# Identity Hardening slice — see docs/development-pki.md and
# docs/mtls.md. NOT for production use: the private key is written to
# disk in plaintext, exactly like every other artifact this script
# produces, which is why the whole output directory is Git-ignored (see
# .gitignore's "certs/dev*" entry) and why this script refuses to run
# against anything other than the local dev output path unless
# explicitly overridden.
#
# Usage: scripts/pki/generate-dev-ca.sh [output_dir]
#   output_dir defaults to certs/dev (relative to the repository root).
set -euo pipefail

# Avoids consulting a broken system-default openssl.cnf observed on some
# machines (e.g. a stale PostgreSQL ODBC install's config location) for
# the one call below (`req -x509`) that doesn't pass -config itself for
# every setting it needs beyond what -config already supplies -- see
# issue-service-cert.sh for the fuller version of this note.
export OPENSSL_CONF=/dev/null

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUTPUT_DIR="${1:-${REPO_ROOT}/certs/dev}"
CA_DIR="${OUTPUT_DIR}/ca"

# Under Git Bash for Windows, $CA_DIR is a POSIX-style path (e.g.
# /tmp/...); command-line arguments referencing it are auto-translated
# to a real Windows path by MSYS, but a path *embedded in a config
# file's contents* is not, since MSYS only translates argv. openssl.cnf
# below needs a path the native (non-MSYS) openssl.exe can actually
# open, so it's translated explicitly via `cygpath` when available;
# real Linux/macOS/CI environments have no cygpath and CA_DIR is already
# a native path there, so this is a no-op outside Git Bash for Windows.
if command -v cygpath >/dev/null 2>&1; then
    CA_DIR_NATIVE="$(cygpath -m "${CA_DIR}")"
else
    CA_DIR_NATIVE="${CA_DIR}"
fi

if [ -f "${CA_DIR}/ca.key.pem" ]; then
    echo "A development CA already exists at ${CA_DIR} — refusing to overwrite it." >&2
    echo "Delete ${CA_DIR} first if you intend to regenerate the whole PKI (this" >&2
    echo "invalidates every certificate issued from it)." >&2
    exit 1
fi

mkdir -p "${CA_DIR}"
: > "${CA_DIR}/index.txt"
echo 1000 > "${CA_DIR}/serial.txt"
# CRL number file: revoke-cert.sh needs this for `openssl ca -gencrl`.
echo 1000 > "${CA_DIR}/crlnumber.txt"

cat > "${CA_DIR}/openssl-ca.cnf" <<EOF
[ca]
default_ca = dev_ca

[dev_ca]
dir              = ${CA_DIR_NATIVE}
database         = \$dir/index.txt
serial           = \$dir/serial.txt
new_certs_dir    = \$dir
certificate      = \$dir/ca.cert.pem
private_key      = \$dir/ca.key.pem
default_md       = sha256
default_days     = 30
policy           = policy_loose
email_in_dn      = no
copy_extensions  = copy
crlnumber        = \$dir/crlnumber.txt
default_crl_days = 30

[policy_loose]
countryName            = optional
stateOrProvinceName    = optional
organizationName       = optional
organizationalUnitName = optional
commonName             = supplied
emailAddress           = optional

[req]
distinguished_name = req_distinguished_name
x509_extensions    = v3_ca
prompt             = no

[req_distinguished_name]
O  = Federated Learning Platform (development only)
CN = federated-platform-dev-root-ca

[v3_ca]
basicConstraints       = critical, CA:TRUE, pathlen:0
keyUsage                = critical, keyCertSign, cRLSign
subjectKeyIdentifier    = hash
authorityKeyIdentifier  = keyid:always,issuer:always
EOF

# EC P-256, not the workers' separate Ed25519 signing identity — TLS
# certificate keys and worker signing keys are deliberately different
# key material for a different purpose (see docs/worker-identity.md and
# Work Package H's "separate from its TLS certificate key" requirement).
# P-256 is used, not Ed25519, specifically so the two never look
# interchangeable.
openssl ecparam -name prime256v1 -genkey -noout -out "${CA_DIR}/ca.key.pem"
chmod 600 "${CA_DIR}/ca.key.pem"

openssl req -config "${CA_DIR}/openssl-ca.cnf" -x509 -new -nodes \
    -key "${CA_DIR}/ca.key.pem" -sha256 -days "${FL_DEV_CA_DAYS:-3650}" \
    -out "${CA_DIR}/ca.cert.pem"

echo "Development root CA created at ${CA_DIR}"
echo "  ca.key.pem  (private — NEVER commit; already covered by .gitignore's certs/dev entry)"
echo "  ca.cert.pem (public — safe to distribute to services/workers as the trust anchor)"
openssl x509 -in "${CA_DIR}/ca.cert.pem" -noout -subject -dates -fingerprint -sha256
