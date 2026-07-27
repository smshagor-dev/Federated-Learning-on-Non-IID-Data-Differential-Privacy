# Issues a development leaf certificate for a named platform service or
# worker (PowerShell) -- mirrors issue-service-cert.sh exactly; see that
# script for the full rationale. Plain-ASCII only -- see
# generate-dev-ca.ps1's note on why.
#
# Usage: scripts/pki/issue-service-cert.ps1 -ServiceName <name> [-CaDir <path>] [-OutputDir <path>] [-Days <n>]
#   ServiceName: e.g. "coordinator" or "go-api" (also accepts
#                "worker/<worker-id>" -- issue-worker-cert.ps1 wraps this)
param(
    [Parameter(Mandatory = $true)][string]$ServiceName,
    [string]$CaDir = "",
    [string]$OutputDir = "",
    [int]$Days = 90
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

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "../..")
if ([string]::IsNullOrEmpty($CaDir)) {
    $CaDir = Join-Path $RepoRoot "certs/dev/ca"
}

if (-not (Test-Path (Join-Path $CaDir "ca.key.pem"))) {
    Write-Error "No development CA found at $CaDir -- run generate-dev-ca.ps1 first."
    exit 1
}

if ($ServiceName -like "worker/*") {
    $WorkerId = $ServiceName.Substring(7)
    if ([string]::IsNullOrEmpty($WorkerId)) {
        Write-Error "worker identity requires a non-empty worker id (worker/<id>)"
        exit 1
    }
    $UriSan = "spiffe://federated-platform/worker/$WorkerId"
    $DefaultOutputDir = Join-Path $RepoRoot "certs/dev/workers/$WorkerId"
    $Cn = "worker-$WorkerId"
    $ExtKeyUsage = "clientAuth"
} else {
    $UriSan = "spiffe://federated-platform/service/$ServiceName"
    $DefaultOutputDir = Join-Path $RepoRoot "certs/dev/services/$ServiceName"
    $Cn = $ServiceName
    $ExtKeyUsage = "serverAuth,clientAuth"
}

if ([string]::IsNullOrEmpty($OutputDir)) {
    $OutputDir = $DefaultOutputDir
}
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

$KeyPath = Join-Path $OutputDir "tls.key.pem"
$CsrPath = Join-Path $OutputDir "tls.csr.pem"
$CertPath = Join-Path $OutputDir "tls.cert.pem"
$ExtCnfPath = Join-Path $OutputDir "ext.cnf"

if (Test-Path $KeyPath) {
    Write-Error "A certificate already exists at $OutputDir -- refusing to overwrite it. Use revoke-cert.ps1 then re-issue, or pick a different OutputDir, for rotation."
    exit 1
}

& openssl ecparam -name prime256v1 -genkey -noout -out $KeyPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$extConfig = @"
[req]
distinguished_name = req_distinguished_name
prompt = no

[req_distinguished_name]
CN = $Cn

[server_ext]
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = $ExtKeyUsage
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
subjectAltName = @alt_names

[alt_names]
URI.1 = $UriSan
DNS.1 = localhost
DNS.2 = $Cn
IP.1 = 127.0.0.1
"@
Set-Content -Path $ExtCnfPath -Value $extConfig

& openssl req -new -key $KeyPath -out $CsrPath -config $ExtCnfPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$caConfigPath = Join-Path $CaDir "openssl-ca.cnf"
& openssl ca -config $caConfigPath -batch -notext `
    -days $Days -in $CsrPath -out $CertPath `
    -extfile $ExtCnfPath -extensions server_ext
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Remove-Item $CsrPath -Force

Write-Host "Issued certificate for identity $UriSan"
Write-Host "  $KeyPath  (private -- Git-ignored)"
Write-Host "  $CertPath (public)"
& openssl x509 -in $CertPath -noout -subject -dates -serial -fingerprint -sha256
