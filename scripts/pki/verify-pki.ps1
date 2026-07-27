# Automated end-to-end verification of the development PKI scripts
# (PowerShell) -- mirrors verify-pki.sh exactly; see that script's
# comments for the full rationale. Plain-ASCII only -- see
# generate-dev-ca.ps1's note on why.
#
# Creates its own throwaway CA and certificates in a fresh temp
# directory (never touches certs/dev), exercises the full
# issue/inspect/revoke/CRL lifecycle, and deletes every private key it
# created before exiting, regardless of pass or fail.
#
# Usage: scripts/pki/verify-pki.ps1
# Exit code: 0 if every check passes, 1 on the first failure.

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

$WorkDir = Join-Path $env:TEMP ("fl-pki-verify." + [System.Guid]::NewGuid().ToString("N").Substring(0, 12))
$CaDir = Join-Path $WorkDir "ca"
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

$script:Failures = 0

function Invoke-Cleanup {
    # Runs on every exit path -- WorkDir holds real private key
    # material (CA key + four leaf keys) for the lifetime of this
    # script only.
    if (Test-Path $WorkDir) {
        Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
    }
}

function Write-Pass([string]$Message) {
    Write-Host "  PASS: $Message"
}

function Write-Fail([string]$Message) {
    Write-Host "  FAIL: $Message" -ForegroundColor Red
    $script:Failures++
}

function Write-Section([string]$Title) {
    Write-Host ""
    Write-Host "== $Title =="
}

try {
    Write-Section "1. Generating temporary development CA at $WorkDir"
    & "$ScriptDir\generate-dev-ca.ps1" -OutputDir $WorkDir | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "generate-dev-ca.ps1 failed"
        exit 1
    }
    Write-Pass "generate-dev-ca.ps1 succeeded"
    if ((Test-Path (Join-Path $CaDir "ca.cert.pem")) -and (Test-Path (Join-Path $CaDir "ca.key.pem"))) {
        Write-Pass "CA cert and key were written"
    } else {
        Write-Fail "CA cert/key not found after generate-dev-ca.ps1"
        exit 1
    }

    Write-Section "2. Issuing coordinator, go-api, and two worker certificates"
    $coordDir = Join-Path $WorkDir "services\coordinator"
    $goApiDir = Join-Path $WorkDir "services\go-api"
    $worker1Dir = Join-Path $WorkDir "workers\worker-1"
    $worker2Dir = Join-Path $WorkDir "workers\worker-2"

    & "$ScriptDir\issue-service-cert.ps1" -ServiceName "coordinator" -CaDir $CaDir -OutputDir $coordDir | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Fail "issuing coordinator certificate failed"; exit 1 }
    & "$ScriptDir\issue-service-cert.ps1" -ServiceName "go-api" -CaDir $CaDir -OutputDir $goApiDir | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Fail "issuing go-api certificate failed"; exit 1 }
    & "$ScriptDir\issue-worker-cert.ps1" -WorkerId "worker-1" -CaDir $CaDir -OutputDir $worker1Dir | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Fail "issuing worker-1 certificate failed"; exit 1 }
    & "$ScriptDir\issue-worker-cert.ps1" -WorkerId "worker-2" -CaDir $CaDir -OutputDir $worker2Dir | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Fail "issuing worker-2 certificate failed"; exit 1 }

    $coordCert = Join-Path $coordDir "tls.cert.pem"
    $goApiCert = Join-Path $goApiDir "tls.cert.pem"
    $worker1Cert = Join-Path $worker1Dir "tls.cert.pem"
    $worker2Cert = Join-Path $worker2Dir "tls.cert.pem"

    foreach ($pair in @(
        @{ Path = $coordCert; Label = "services/coordinator/tls.cert.pem" },
        @{ Path = $goApiCert; Label = "services/go-api/tls.cert.pem" },
        @{ Path = $worker1Cert; Label = "workers/worker-1/tls.cert.pem" },
        @{ Path = $worker2Cert; Label = "workers/worker-2/tls.cert.pem" }
    )) {
        if (Test-Path $pair.Path) {
            Write-Pass "issued $($pair.Label)"
        } else {
            Write-Fail "expected certificate not found: $($pair.Path)"
        }
    }

    Write-Section "3. Inspecting URI SAN identities"
    $inspectOutput = & python "$ScriptDir\inspect-certificates.py" $coordCert $goApiCert $worker1Cert $worker2Cert
    $inspectOutput | ForEach-Object { Write-Host "  $_" }
    $inspectText = $inspectOutput -join "`n"

    function Test-San([string]$Expected) {
        if ($inspectText -like "*$Expected*") {
            Write-Pass "found expected URI SAN identity: $Expected"
        } else {
            Write-Fail "expected URI SAN identity not found: $Expected"
        }
    }
    Test-San "spiffe://federated-platform/service/coordinator"
    Test-San "spiffe://federated-platform/service/go-api"
    Test-San "spiffe://federated-platform/worker/worker-1"
    Test-San "spiffe://federated-platform/worker/worker-2"

    Write-Section "4. Validating certificate chains against the CA"
    $caCertPath = Join-Path $CaDir "ca.cert.pem"
    foreach ($pair in @(
        @{ Path = $coordCert; Label = "services/coordinator/tls.cert.pem" },
        @{ Path = $goApiCert; Label = "services/go-api/tls.cert.pem" },
        @{ Path = $worker1Cert; Label = "workers/worker-1/tls.cert.pem" },
        @{ Path = $worker2Cert; Label = "workers/worker-2/tls.cert.pem" }
    )) {
        & openssl verify -CAfile $caCertPath $pair.Path | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Pass "chain validates: $($pair.Label)"
        } else {
            Write-Fail "chain does NOT validate: $($pair.Label)"
        }
    }

    Write-Section "5. Revoking worker-2 and regenerating the CRL"
    & "$ScriptDir\revoke-cert.ps1" -CertPath $worker2Cert -Reason keyCompromise -CaDir $CaDir | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "revoke-cert.ps1 succeeded"
    } else {
        Write-Fail "revoke-cert.ps1 failed"
    }
    $crlPath = Join-Path $CaDir "crl.pem"
    if (Test-Path $crlPath) {
        Write-Pass "CRL file was written"
    } else {
        Write-Fail "CRL file not found after revocation"
    }

    Write-Section "6. Verifying revoked status"
    $serialLine = (& openssl x509 -in $worker2Cert -noout -serial)
    $worker2Serial = ($serialLine -split "=")[1]
    $indexContent = Get-Content (Join-Path $CaDir "index.txt") -Raw
    if ($indexContent -match "(?m)^R\S*.*$worker2Serial") {
        Write-Pass "worker-2 (serial $worker2Serial) is marked Revoked in index.txt"
    } else {
        Write-Fail "worker-2 (serial $worker2Serial) is NOT marked Revoked in index.txt"
    }

    & openssl verify -CAfile $caCertPath -crl_check -CRLfile $crlPath $worker2Cert | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Pass "openssl verify -crl_check correctly REJECTS the revoked worker-2 certificate"
    } else {
        Write-Fail "openssl verify -crl_check unexpectedly ACCEPTED the revoked worker-2 certificate"
    }

    & openssl verify -CAfile $caCertPath -crl_check -CRLfile $crlPath $worker1Cert | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Pass "openssl verify -crl_check still ACCEPTS the non-revoked worker-1 certificate"
    } else {
        Write-Fail "worker-1 (never revoked) was unexpectedly rejected by -crl_check"
    }

    Write-Section "7. Confirming no dev-PKI private key material is Git-tracked"
    Push-Location $RepoRoot
    try {
        $trackedKeys = & git ls-files -- 'certs/dev*' '*.key.pem' 2>$null
    } finally {
        Pop-Location
    }
    if ([string]::IsNullOrWhiteSpace(($trackedKeys -join ""))) {
        Write-Pass "no certs/dev* or *.key.pem files are tracked by git"
    } else {
        Write-Fail "the following private-key-shaped paths are tracked by git: $($trackedKeys -join ', ')"
    }

    Write-Section "8. Cleanup"
    Write-Host "  (private key material at $WorkDir will be deleted before exit)"

    Write-Host ""
    if ($script:Failures -eq 0) {
        Write-Host "verify-pki.ps1: all checks passed"
        exit 0
    } else {
        Write-Host "verify-pki.ps1: $($script:Failures) check(s) failed" -ForegroundColor Red
        exit 1
    }
} finally {
    Invoke-Cleanup
}
