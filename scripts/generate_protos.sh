#!/usr/bin/env bash
set -euo pipefail

out_dir="${1:-generated}"
if ! command -v protoc >/dev/null 2>&1; then
  echo "protoc is required. Supported version: libprotoc 25.x or newer." >&2
  exit 127
fi

mapfile -t proto_files < <(find proto -name '*.proto' | sort)

mkdir -p "$out_dir/descriptors" "$out_dir/cpp" "$out_dir/python"

protoc --proto_path=proto \
  --descriptor_set_out="$out_dir/descriptors/fl_contracts.pb" \
  --include_imports \
  "${proto_files[@]}"

protoc --proto_path=proto --cpp_out="$out_dir/cpp" "${proto_files[@]}"
protoc --proto_path=proto --python_out="$out_dir/python" "${proto_files[@]}"

if command -v protoc-gen-go >/dev/null 2>&1; then
  mkdir -p "$out_dir/go"
  protoc --proto_path=proto --go_out="$out_dir/go" "${proto_files[@]}"
else
  echo "warning: protoc-gen-go not found; skipped Go binding generation." >&2
fi
