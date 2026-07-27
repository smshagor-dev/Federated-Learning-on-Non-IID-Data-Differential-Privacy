# Thin ergonomic wrapper around issue-service-cert.ps1 for worker
# identities -- see that script for the actual issuance logic.
#
# Usage: scripts/pki/issue-worker-cert.ps1 -WorkerId <id> [-CaDir <path>] [-OutputDir <path>] [-Days <n>]
param(
    [Parameter(Mandatory = $true)][string]$WorkerId,
    [string]$CaDir = "",
    [string]$OutputDir = "",
    [int]$Days = 90
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

& (Join-Path $ScriptDir "issue-service-cert.ps1") `
    -ServiceName "worker/$WorkerId" -CaDir $CaDir -OutputDir $OutputDir -Days $Days
exit $LASTEXITCODE
