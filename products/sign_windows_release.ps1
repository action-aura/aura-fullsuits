<#
Aura FullSuits -- Windows Authenticode signing script (Wave 1B, Part H).

NOT YET USABLE FOR REAL SIGNING: no trusted code-signing certificate is
available as of this wave (confirmed with the project owner). This script
is prepared so that signing is a single command once a certificate exists,
rather than a from-scratch task at that time.

Usage (once a real .pfx-backed certificate is available):
    $env:AURA_SIGN_PFX_PATH = "C:\path\to\certificate.pfx"      # never commit this file
    $env:AURA_SIGN_PFX_PASSWORD = "..."                          # never commit, never log
    .\products\sign_windows_release.ps1 -Target dist\AuraRetail\AuraRetail.exe
    .\products\sign_windows_release.ps1 -Target dist\installers\AuraRetail-Setup-1.0.0-rc.1.exe

Requires the Windows SDK's signtool.exe on PATH (not installed on this
build machine as of this wave -- see docs/release/windows-code-signing-guide.md
for acquisition steps).
#>
param(
    [Parameter(Mandatory = $true)][string]$Target,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$pfxPath = $env:AURA_SIGN_PFX_PATH
$pfxPassword = $env:AURA_SIGN_PFX_PASSWORD

if (-not $pfxPath -or -not (Test-Path $pfxPath)) {
    Write-Error "AURA_SIGN_PFX_PATH is not set or the file does not exist. See docs/release/windows-code-signing-guide.md."
    exit 1
}
if (-not $pfxPassword) {
    Write-Error "AURA_SIGN_PFX_PASSWORD is not set. Never pass the password as a command-line argument (it would be visible in process listings/history) -- use the environment variable."
    exit 1
}
if (-not (Get-Command signtool.exe -ErrorAction SilentlyContinue)) {
    Write-Error "signtool.exe not found on PATH. Install the Windows SDK (see docs/release/windows-code-signing-guide.md)."
    exit 1
}
if (-not (Test-Path $Target)) {
    Write-Error "Target file not found: $Target"
    exit 1
}

Write-Host "Signing $Target ..."
& signtool.exe sign /f $pfxPath /p $pfxPassword /tr $TimestampUrl /td sha256 /fd sha256 /d "Action Aura" $Target
if ($LASTEXITCODE -ne 0) {
    Write-Error "signtool sign failed (exit $LASTEXITCODE)."
    exit $LASTEXITCODE
}

Write-Host "Verifying signature ..."
& signtool.exe verify /pa /v $Target
if ($LASTEXITCODE -ne 0) {
    Write-Error "signtool verify failed (exit $LASTEXITCODE) -- the signature was written but does not validate."
    exit $LASTEXITCODE
}

Write-Host "OK: $Target signed and verified."
