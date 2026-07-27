# Development-only root CA generator (PowerShell) -- mirrors
# generate-dev-ca.sh exactly; see that script's comments for the full
# rationale (Git-Bash-specific workarounds do not apply here, since this
# is native PowerShell, not MSYS).
#
# Deliberately plain-ASCII only in this file: Windows PowerShell 5.1
# reads .ps1 scripts using the system codepage unless the file carries a
# UTF-8 BOM, and non-ASCII characters (em-dashes, curly quotes) get
# silently mangled into multi-byte garbage that breaks string/quote
# parsing -- discovered the hard way while writing this script.
#
# Usage: scripts/pki/generate-dev-ca.ps1 [-OutputDir <path>] [-Days <n>]
param(
    [string]$OutputDir = "",
    [int]$Days = 3650
)

$ErrorActionPreference = "Stop"
$env:OPENSSL_CONF = "NUL"

# Windows does not ship OpenSSL, and even when it is present (e.g.
# bundled with Git for Windows at the path below) it is commonly not on
# PowerShell's default PATH -- only on Git Bash's. Falling back to the
# well-known Git-for-Windows location keeps this script usable out of
# the box on a bare Windows machine that already has Git installed,
# without requiring a manual PATH edit.
if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
    $fallback = "C:\Program Files\Git\usr\bin\openssl.exe"
    if (Test-Path $fallback) {
        Set-Alias -Name openssl -Value $fallback -Scope Script
    } else {
        Write-Error "openssl was not found on PATH and the Git-for-Windows fallback ($fallback) does not exist either. Install OpenSSL (or Git for Windows) and ensure it is reachable."
        exit 1
    }
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "../..")
if ([string]::IsNullOrEmpty($OutputDir)) {
    $OutputDir = Join-Path $RepoRoot "certs/dev"
}
$CaDir = Join-Path $OutputDir "ca"

if (Test-Path (Join-Path $CaDir "ca.key.pem")) {
    Write-Error "A development CA already exists at $CaDir -- refusing to overwrite it. Delete $CaDir first if you intend to regenerate the whole PKI (this invalidates every certificate issued from it)."
    exit 1
}

New-Item -ItemType Directory -Force -Path $CaDir | Out-Null
Set-Content -Path (Join-Path $CaDir "index.txt") -Value "" -NoNewline
Set-Content -Path (Join-Path $CaDir "serial.txt") -Value "1000"
Set-Content -Path (Join-Path $CaDir "crlnumber.txt") -Value "1000"

$CaDirNative = (Resolve-Path $CaDir).Path -replace '\\', '/'

$caConfig = @"
[ca]
default_ca = dev_ca

[dev_ca]
dir              = $CaDirNative
database         = `$dir/index.txt
serial           = `$dir/serial.txt
new_certs_dir    = `$dir
certificate      = `$dir/ca.cert.pem
private_key      = `$dir/ca.key.pem
default_md       = sha256
default_days     = 30
policy           = policy_loose
email_in_dn      = no
copy_extensions  = copy
crlnumber        = `$dir/crlnumber.txt
default_crl_days = 30

[policy_loose]
countryName            = optional
stateOrProvinceName    = optional
organizationName       = optional
organizationalUnitName = optional
commonName             = supplied
emailAddress           = optional

[req]
distinguished_name = req_distinguished_name
x509_extensions    = v3_ca
prompt             = no

[req_distinguished_name]
O  = Federated Learning Platform (development only)
CN = federated-platform-dev-root-ca

[v3_ca]
basicConstraints       = critical, CA:TRUE, pathlen:0
keyUsage                = critical, keyCertSign, cRLSign
subjectKeyIdentifier    = hash
authorityKeyIdentifier  = keyid:always,issuer:always
"@
Set-Content -Path (Join-Path $CaDir "openssl-ca.cnf") -Value $caConfig

$caKeyPath = Join-Path $CaDir "ca.key.pem"
$caCertPath = Join-Path $CaDir "ca.cert.pem"
$caConfigPath = Join-Path $CaDir "openssl-ca.cnf"

# EC P-256, not the workers' separate Ed25519 signing identity -- see
# generate-dev-ca.sh's identical note on why these are different key
# material for a different purpose.
& openssl ecparam -name prime256v1 -genkey -noout -out $caKeyPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& openssl req -config $caConfigPath -x509 -new -nodes `
    -key $caKeyPath -sha256 -days $Days -out $caCertPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Development root CA created at $CaDir"
Write-Host "  ca.key.pem  (private -- NEVER commit; already covered by .gitignore's certs/dev entry)"
Write-Host "  ca.cert.pem (public -- safe to distribute to services/workers as the trust anchor)"
& openssl x509 -in $caCertPath -noout -subject -dates -fingerprint -sha256
