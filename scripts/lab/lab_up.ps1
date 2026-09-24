# Aura Lab -- starts the Owner Control Center (loopback-only, port 5551) if
# it is not already up, waits for it to become healthy, and optionally also
# starts the Aura Retail desktop app once Owner is healthy (-WithDesktop).
#
# Windows PowerShell 5.1 AND pwsh 7 compatible: no '&&', no ternary '?:', no
# null-coalescing '??', ASCII only.

param(
    [switch]$WithDesktop
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lab_common.ps1")

$OwnerPort = 5551
$OwnerBaseUrl = "http://127.0.0.1:$OwnerPort"

Write-LabLog "=== lab_up: Owner ==="

if (Test-Listening -Port $OwnerPort) {
    Write-LabLog "Port $OwnerPort already listening -- checking health instead of starting a second copy."
} else {
    $LabEnv = Read-LabEnv
    if ($null -eq $LabEnv) {
        throw "lab.env.json not found -- run lab_bootstrap.ps1 first."
    }
    Set-OwnerEnv -LabEnv $LabEnv | Out-Null

    $ownerDir = Join-Path $RepoRoot "owner"
    $logDir = Join-Path $LabRoot "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stdout = Join-Path $logDir "owner.log"
    $stderr = Join-Path $logDir "owner.err"

    Write-LabLog "Starting Owner detached on $OwnerBaseUrl (log: $stdout)..."
    $proc = Start-Process -FilePath $OwnerPython `
        -ArgumentList "-m", "flask", "run", "--host", "127.0.0.1", "--port", "$OwnerPort" `
        -WorkingDirectory $ownerDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr

    Save-Pid -Name "owner" -ProcessId $proc.Id -Extra $OwnerBaseUrl | Out-Null
    Write-LabLog "Owner process started, pid=$($proc.Id)."
}

Write-LabLog "Waiting for GET $OwnerBaseUrl/healthz ..."
$healthz = Wait-Http -Url "$OwnerBaseUrl/healthz" -TimeoutSec 45
if (-not $healthz.Ok) {
    throw "Owner did not answer $OwnerBaseUrl/healthz with 200 within 45s. Check $LabRoot/logs/owner.err and owner.log."
}
Write-LabLog "healthz OK: $($healthz.Body)"

Write-LabLog "Waiting for GET $OwnerBaseUrl/api/licensing/v1/service-info ..."
$serviceInfo = Wait-Http -Url "$OwnerBaseUrl/api/licensing/v1/service-info" -TimeoutSec 20
if (-not $serviceInfo.Ok) {
    throw "Owner's device-facing licensing API did not answer /api/licensing/v1/service-info with 200. Check OWNER_EXTERNAL_API_ENABLED and the signing key."
}
Write-LabLog "service-info OK: $($serviceInfo.Body)"

Write-LabLog "=== Owner is up ==="
Write-LabLog "URL: $OwnerBaseUrl"
Write-LabLog "Credentials: $(Get-LabEnvPath) (admin_email / admin_password fields)"

if ($WithDesktop) {
    Write-LabLog "-WithDesktop requested -- launching the Aura Retail desktop app."
    & (Join-Path $PSScriptRoot "lab_desktop.ps1")
}
