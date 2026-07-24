#!/usr/bin/env bash
set -euo pipefail

# Milestone 3 change from Milestone 2: Go and Python now *consume* these
# generated bindings for real gRPC (not just a freshness/descriptor check),
# so each language's stubs go to a fixed, predictable location inside that
# language's own module/package rather than an arbitrary scratch dir:
#   - Go:     go/generated/<pkg>/*.go        (must sit inside the go module)
#   - Python: python/src/fl_platform/generated/<pkg>/*.py
#             (resolved via fl_platform.rpc.ensure_generated_on_path())
#   - C++:    cpp/generated/<pkg>/*.{h,cc}   (CI-only consumer; see below)
# All three remain gitignored and are regenerated on demand; see
# docs/protobuf-generation.md for the full policy.

descriptor_out_dir="${1:-generated}"

if ! command -v protoc >/dev/null 2>&1; then
  echo "protoc is required. Supported version: libprotoc 25.x or newer." >&2
  exit 127
fi

mapfile -t proto_files < <(find proto -name '*.proto' | sort)

mkdir -p "$descriptor_out_dir/descriptors"
protoc --proto_path=proto \
  --descriptor_set_out="$descriptor_out_dir/descriptors/fl_contracts.pb" \
  --include_imports \
  "${proto_files[@]}"

# --- C++ ---------------------------------------------------------------
# Plain message codegen only needs protoc. The gRPC C++ service stubs also
# need protoc-gen-grpc-cpp (part of a full gRPC C++ install), which this
# script does not assume is present locally — see
# docs/known-limitations.md for why the gRPC C++ build only runs in CI.
mkdir -p cpp/generated
protoc --proto_path=proto --cpp_out=cpp/generated "${proto_files[@]}"
if command -v grpc_cpp_plugin >/dev/null 2>&1; then
  protoc --proto_path=proto \
    --grpc_out=cpp/generated \
    --plugin=protoc-gen-grpc="$(command -v grpc_cpp_plugin)" \
    proto/coordinator/coordinator.proto
else
  echo "info: grpc_cpp_plugin not found; skipped C++ gRPC service stub generation (message types were still generated)." >&2
fi

# --- Python --------------------------------------------------------------
# Prefer grpcio-tools (bundles its own protoc + the grpc_python plugin);
# fall back to plain protoc if grpcio-tools isn't installed, generating
# message types only (no gRPC service stubs).
mkdir -p python/src/fl_platform/generated
if python -c "import grpc_tools.protoc" >/dev/null 2>&1; then
  python -m grpc_tools.protoc \
    --proto_path=proto \
    --python_out=python/src/fl_platform/generated \
    --grpc_python_out=python/src/fl_platform/generated \
    --pyi_out=python/src/fl_platform/generated \
    "${proto_files[@]}"
else
  echo "warning: grpcio-tools not installed (pip install grpcio grpcio-tools); generating Python message types only, no gRPC stubs." >&2
  protoc --proto_path=proto --python_out=python/src/fl_platform/generated "${proto_files[@]}"
fi

# --- Go ------------------------------------------------------------------
if command -v protoc-gen-go >/dev/null 2>&1; then
  mkdir -p go/generated
  protoc --proto_path=proto \
    --go_out=go/generated --go_opt=module=github.com/smshagor-dev/federated-learning-super-system/go/generated \
    "${proto_files[@]}"
  if command -v protoc-gen-go-grpc >/dev/null 2>&1; then
    protoc --proto_path=proto \
      --go-grpc_out=go/generated --go-grpc_opt=module=github.com/smshagor-dev/federated-learning-super-system/go/generated \
      "${proto_files[@]}"
  else
    echo "warning: protoc-gen-go-grpc not found; skipped Go gRPC service stub generation (message types were still generated)." >&2
  fi
else
  echo "warning: protoc-gen-go not found (go install google.golang.org/protobuf/cmd/protoc-gen-go@latest); skipped Go binding generation." >&2
fi
