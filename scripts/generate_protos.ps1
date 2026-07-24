param(
    [string]$DescriptorOutDir = "generated"
)

$ErrorActionPreference = "Stop"

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

$protoc = Get-Command protoc -ErrorAction SilentlyContinue
if (-not $protoc) {
    throw "protoc is required. Supported version: libprotoc 25.x or newer."
}

$protoFiles = Get-ChildItem -Path "proto" -Recurse -Filter "*.proto" |
    Sort-Object FullName |
    ForEach-Object { $_.FullName }

New-Item -ItemType Directory -Force -Path "$DescriptorOutDir/descriptors" | Out-Null
& protoc --proto_path=proto `
    --descriptor_set_out="$DescriptorOutDir/descriptors/fl_contracts.pb" `
    --include_imports `
    @protoFiles

# --- C++ ---------------------------------------------------------------
New-Item -ItemType Directory -Force -Path "cpp/generated" | Out-Null
& protoc --proto_path=proto --cpp_out="cpp/generated" @protoFiles
$grpcCppPlugin = Get-Command grpc_cpp_plugin -ErrorAction SilentlyContinue
if ($grpcCppPlugin) {
    & protoc --proto_path=proto `
        --grpc_out="cpp/generated" `
        --plugin=protoc-gen-grpc="$($grpcCppPlugin.Source)" `
        proto/coordinator/coordinator.proto
} else {
    Write-Host "info: grpc_cpp_plugin not found; skipped C++ gRPC service stub generation (message types were still generated)."
}

# --- Python --------------------------------------------------------------
New-Item -ItemType Directory -Force -Path "python/src/fl_platform/generated" | Out-Null
$grpcToolsAvailable = $false
try {
    python -c "import grpc_tools.protoc" 2>$null
    if ($LASTEXITCODE -eq 0) { $grpcToolsAvailable = $true }
} catch {}

if ($grpcToolsAvailable) {
    python -m grpc_tools.protoc `
        --proto_path=proto `
        --python_out=python/src/fl_platform/generated `
        --grpc_python_out=python/src/fl_platform/generated `
        --pyi_out=python/src/fl_platform/generated `
        @protoFiles
} else {
    Write-Warning "grpcio-tools not installed (pip install grpcio grpcio-tools); generating Python message types only, no gRPC stubs."
    & protoc --proto_path=proto --python_out="python/src/fl_platform/generated" @protoFiles
}

# --- Go ------------------------------------------------------------------
$goPlugin = Get-Command protoc-gen-go -ErrorAction SilentlyContinue
if ($goPlugin) {
    New-Item -ItemType Directory -Force -Path "go/generated" | Out-Null
    & protoc --proto_path=proto `
        --go_out="go/generated" --go_opt=module=github.com/smshagor-dev/federated-learning-super-system/go/generated `
        @protoFiles

    $goGrpcPlugin = Get-Command protoc-gen-go-grpc -ErrorAction SilentlyContinue
    if ($goGrpcPlugin) {
        & protoc --proto_path=proto `
            --go-grpc_out="go/generated" --go-grpc_opt=module=github.com/smshagor-dev/federated-learning-super-system/go/generated `
            @protoFiles
    } else {
        Write-Warning "protoc-gen-go-grpc not found; skipped Go gRPC service stub generation (message types were still generated)."
    }
} else {
    Write-Warning "protoc-gen-go not found (go install google.golang.org/protobuf/cmd/protoc-gen-go@latest); skipped Go binding generation."
}
