# Bring the whole local rehearsal up in one command, idempotently:
#   - Owner Control Center on 127.0.0.1:5551 (HTTP, against aura_owner_rehearsal,
#     advertising itself as the sync relay -- the phone's APK is baked to
#     http://127.0.0.1:5551 and reaches it through `adb reverse`)
#   - the desktop till on :5010 and the third till on :5011, both activated on
#     the rehearsal licence, both pulling the relay
#   - the phone tunnel (`adb reverse tcp:5551`), which does NOT survive a
#     hibernate/USB re-enumeration (see docs/release/sellability-status.md,
#     blocker 4), plus the phone watcher that installs the current APK and
#     sets JOD as soon as a phone is attached
#   - the battery guard (hibernates at 15% on battery, never while charging)
#
# Every piece checks whether it is already running first, so running this
# twice changes nothing. Written 2026-09-06 after every background process
# of the rehearsal was stopped at once and the demo state had to be
# rebuilt by hand from transcript notes -- never again.
#
# Values below are the rehearsal's own dev-only settings (the dev pepper is
# the documented insecure default, not a secret). Nothing here is production.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)   # repo / worktree root
$venvOwner = Join-Path $root ".venv-owner\Scripts\python.exe"
$venvRetail = "C:\Users\MSI\Desktop\aura-fullsuits\.venv\Scripts\python.exe"
$adb = "C:\Users\MSI\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe"
$dataRoot = "C:\Users\MSI\.claude\jobs\215b2785\tmp"   # the tills' data dirs from the 2026-09-05 rehearsal

function Listening($port) { return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }
function Log($m) { Write-Output ("{0} {1}" -f (Get-Date -Format "HH:mm:ss"), $m) }

# ── Owner ──────────────────────────────────────────────────────────────────
if (Listening 5551) { Log "Owner already listening on 5551" }
else {
    $env:OWNER_DATABASE_URL = "postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_rehearsal"
    $env:OWNER_LICENSE_PEPPER = "dev-only-insecure-pepper-do-not-use-in-production"
    $env:OWNER_SECRET_KEY = "rehearsal-local-only-secret-not-for-production"
    $env:OWNER_EXTERNAL_API_ENABLED = "true"
    $env:OWNER_SYNC_RELAY_PUBLIC_URL = "http://127.0.0.1:5551"
    $env:FLASK_APP = "app:create_app"
    Start-Process -FilePath $venvOwner -ArgumentList "-m flask run --host 127.0.0.1 --port 5551" `
        -WorkingDirectory (Join-Path $root "owner") -WindowStyle Hidden `
        -RedirectStandardOutput "$env:TEMP\owner-rehearsal-5551.log" -RedirectStandardError "$env:TEMP\owner-rehearsal-5551.err"
    Log "Owner starting on 5551 (log: $env:TEMP\owner-rehearsal-5551.log)"
}

# ── Tills ──────────────────────────────────────────────────────────────────
function Start-Till($port, $dataDir, $logName) {
    if (Listening $port) { Log "till already listening on $port"; return }
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    $env:AURA_STANDALONE = "1"
    $env:AURA_APP_DATA = $dataDir
    $env:PORT = "$port"
    $env:AURA_OWNER_LICENSING_URL = "http://127.0.0.1:5551/api/licensing/v1"
    $env:AURA_OWNER_LICENSING_INSECURE = "1"
    $env:AURA_SYNC_RELAY_URL = "http://127.0.0.1:5551"
    $env:AURA_SYNC_RELAY_INSECURE = "1"
    Remove-Item Env:AURA_DEV -ErrorAction SilentlyContinue
    Start-Process -FilePath $venvRetail -ArgumentList "app.py" `
        -WorkingDirectory (Join-Path $root "products\retail\backend") -WindowStyle Hidden `
        -RedirectStandardOutput "$env:TEMP\$logName.log" -RedirectStandardError "$env:TEMP\$logName.err"
    Log "till starting on $port (data: $dataDir)"
}
Start-Till 5010 (Join-Path $dataRoot "desktop-till") "desktop-till-5010"
Start-Till 5011 (Join-Path $dataRoot "third-till") "third-till-5011"

# ── Phone tunnel + watcher ─────────────────────────────────────────────────
& $adb reverse tcp:5551 tcp:5551 2>$null | Out-Null
Log ("adb reverse: " + ((& $adb reverse --list 2>$null) -join ' '))
$watcherRunning = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
    Where-Object { $_.CommandLine -like "*phone_watcher.ps1*" }
if ($watcherRunning) { Log "phone watcher already running" }
else {
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\phone_watcher.ps1`"" -WindowStyle Hidden
    Log "phone watcher started (log: $env:TEMP\phone_watcher.log)"
}

# ── Battery guard ──────────────────────────────────────────────────────────
$guardRunning = Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe'" |
    Where-Object { $_.CommandLine -like "*battery_guard.ps1*" }
if ($guardRunning) { Log "battery guard already running" }
else {
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\battery_guard.ps1`"" -WindowStyle Hidden
    Log "battery guard started (log: $env:TEMP\battery_guard.log)"
}

# ── Verdict ────────────────────────────────────────────────────────────────
Start-Sleep -Seconds 20
foreach ($p in 5551, 5010, 5011) { Log ("port {0}: {1}" -f $p, $(if (Listening $p) { "UP" } else { "not yet (check the log)" })) }
