param(
    [string]$OutDir = "generated"
)

$ErrorActionPreference = "Stop"

$protoc = Get-Command protoc -ErrorAction SilentlyContinue
if (-not $protoc) {
    throw "protoc is required. Supported version: libprotoc 25.x or newer."
}

$protoFiles = Get-ChildItem -Path "proto" -Recurse -Filter "*.proto" |
    Sort-Object FullName |
    ForEach-Object { $_.FullName }

New-Item -ItemType Directory -Force -Path "$OutDir/descriptors" | Out-Null
New-Item -ItemType Directory -Force -Path "$OutDir/cpp" | Out-Null
New-Item -ItemType Directory -Force -Path "$OutDir/python" | Out-Null

& protoc --proto_path=proto `
    --descriptor_set_out="$OutDir/descriptors/fl_contracts.pb" `
    --include_imports `
    @protoFiles

& protoc --proto_path=proto --cpp_out="$OutDir/cpp" @protoFiles
& protoc --proto_path=proto --python_out="$OutDir/python" @protoFiles

$goPlugin = Get-Command protoc-gen-go -ErrorAction SilentlyContinue
if ($goPlugin) {
    New-Item -ItemType Directory -Force -Path "$OutDir/go" | Out-Null
    & protoc --proto_path=proto --go_out="$OutDir/go" @protoFiles
} else {
    Write-Warning "protoc-gen-go not found; skipped Go binding generation."
}
