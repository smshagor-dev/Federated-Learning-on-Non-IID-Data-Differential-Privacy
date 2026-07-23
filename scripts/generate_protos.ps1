param(
    [string]$OutDir = "generated"
)

Write-Host "Milestone 1 placeholder for protobuf generation."
Write-Host "Expected future usage: protoc --proto_path=proto ..."
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
