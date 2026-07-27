#!/usr/bin/env bash
# Thin ergonomic wrapper around issue-service-cert.sh for worker
# identities — see that script for the actual issuance logic and
# docs/development-pki.md for the full workflow.
#
# Usage: scripts/pki/issue-worker-cert.sh <worker-id> [ca_dir] [output_dir] [days]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_ID="${1:?usage: issue-worker-cert.sh <worker-id> [ca_dir] [output_dir] [days]}"
shift

exec "${SCRIPT_DIR}/issue-service-cert.sh" "worker/${WORKER_ID}" "$@"
