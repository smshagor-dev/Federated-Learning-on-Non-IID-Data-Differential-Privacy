#!/usr/bin/env bash
set -euo pipefail

out_dir="${1:-generated}"
mkdir -p "$out_dir"
echo "Milestone 1 placeholder for protobuf generation."
echo "Expected future usage: protoc --proto_path=proto ..."
