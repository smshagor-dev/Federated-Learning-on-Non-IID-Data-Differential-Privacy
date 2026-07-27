# Revokes a development-PKI leaf certificate and regenerates the CA's
# CRL (PowerShell) -- mirrors revoke-cert.sh exactly; see that script
# for the full rationale.
#
# Usage: scripts/pki/revoke-cert.ps1 -CertPath <path> -Reason <reason> [-CaDir <path>]
#   Reason: unspecified, keyCompromise, CACompromise, affiliationChanged,
#           superseded, cessationOfOperation, certificateHold
param(
    [Parameter(Mandatory = $true)][string]$CertPath,
    [Parameter(Mandatory = $true)][string]$Reason,
    [string]$CaDir = ""
)

$ErrorActionPreference = "Stop"
$env:OPENSSL_CONF = "NUL"

if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
    $fallback = "C:\Program Files\Git\usr\bin\openssl.exe"
    if (Test-Path $fallback) {
        Set-Alias -Name openssl -Value $fallback -Scope Script
    } else {
        Write-Error "openssl was not found on PATH and the Git-for-Windows fallback ($fallback) does not exist either."
        exit 1
    }
}

$validReasons = @(
    "unspecified", "keyCompromise", "CACompromise", "affiliationChanged",
    "superseded", "cessationOfOperation", "certificateHold"
)
if ($validReasons -notcontains $Reason) {
    Write-Error "unrecognized revocation reason '$Reason'; must be one of: $($validReasons -join ', ')"
    exit 1
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "../..")
if ([string]::IsNullOrEmpty($CaDir)) {
    $CaDir = Join-Path $RepoRoot "certs/dev/ca"
}

if (-not (Test-Path $CertPath)) {
    Write-Error "certificate not found: $CertPath"
    exit 1
}
if (-not (Test-Path (Join-Path $CaDir "ca.key.pem"))) {
    Write-Error "No development CA found at $CaDir -- nothing to revoke against."
    exit 1
}

$subject = (& openssl x509 -in $CertPath -noout -subject)
$serialLine = (& openssl x509 -in $CertPath -noout -serial)
$serial = ($serialLine -split "=")[1]

$caConfigPath = Join-Path $CaDir "openssl-ca.cnf"
& openssl ca -config $caConfigPath -revoke $CertPath -crl_reason $Reason
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$crlPath = Join-Path $CaDir "crl.pem"
& openssl ca -config $caConfigPath -gencrl -out $crlPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Revoked: $subject (serial $serial), reason=$Reason"
Write-Host "CRL regenerated at $crlPath"
