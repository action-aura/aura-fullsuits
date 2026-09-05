# One-command phone activation round-trip against the local rehearsal Owner.
#
# Why this exists: on 2026-09-04 a real Mi Note 10 was found unlicensable and
# the cause (a trust store seeded once, shadowing every later anchor) was
# cleared on the device -- but the round-trip itself was never re-run, because
# the phone was unplugged before Owner could be started. This closes that gap
# in one go, and it is what the go-live runbook's Part 2 step 4 needs anyway.
#
# The phone's installation is ALREADY ACTIVE on the rehearsal Owner
# (owner_installations b857c7d5..., ANDROID, device key c80edc2a... ACTIVE), so
# nothing is re-issued here. The app retries its held activation on launch;
# Owner recognises the same device key and returns the signed assertion; the
# phone -- which now trusts Owner's key -- verifies and persists it.
#
# Facts this script depends on, all verified 2026-09-04:
#   * the APK is baked to http://127.0.0.1:5551/api/licensing/v1 -- the DEVICE's
#     loopback, plain HTTP -- so Owner must listen on 5551 WITHOUT TLS and the
#     device reaches it through `adb reverse`;
#   * the rehearsal DB is aura_owner_rehearsal and its signing key's private PEM
#     is owner/var/signing-keys/owner-ed25519-20260903T160641Z-cdcaa65b.pem,
#     which is why Owner must be started from owner/ (default key directory);
#   * activation validates the licence key against OWNER_LICENSE_PEPPER, so the
#     pepper must be the one the rehearsal was run with. Pass it with -Pepper;
#     verify a candidate first with scripts/ops/verify_pepper.py.
#
# Usage (phone plugged in, USB debugging on). Two modes:
#
#   .\scripts\ops\phone_roundtrip.ps1
#       No pepper known (the normal case -- it was never recorded). Re-issues a
#       fresh licence via rehearsal_reissue.py under the dev-default pepper,
#       releases the phone's stale registration, CLEARS THE APP'S DATA on the
#       phone so it enrols with a fresh device key and re-seeds trust from the
#       bundled anchor, and prints the ONE licence key to type on the phone.
#       Clearing app data wipes that phone's local shop data (it is the test
#       handset with the "Xyz" demo company) -- say so before running it on a
#       phone that matters.
#
#   .\scripts\ops\phone_roundtrip.ps1 -Pepper "<value>"
#       The original pepper IS known (prove it first with verify_pepper.py).
#       Nothing is re-issued and nothing on the phone is cleared; the app
#       retries its held activation and Owner recognises the same device key.
param(
    [string]$Pepper = "",
    [int]$WaitSeconds = 90,
    [string]$Tag = (Get-Date -Format "yyyyMMdd")
)
$ErrorActionPreference = "Stop"
$repo   = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$owner  = Join-Path $repo "owner"
$py     = Join-Path $repo ".venv-owner\Scripts\python.exe"
$adb    = "C:\Users\MSI\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe"
$pkg    = "com.actionaura.retail.debug"
$dbFile = "files/data/database/subsystems/licensing.db"
$dbUrl  = "postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_rehearsal"

if (-not ((& $adb devices) -match "\sdevice$")) { throw "No phone attached (adb devices shows none). Plug it in with USB debugging on." }
Write-Host "Phone attached."

$reissue = [string]::IsNullOrWhiteSpace($Pepper)
if ($reissue) {
    $Pepper = "dev-only-insecure-pepper-do-not-use-in-production"
    Write-Host "No pepper given -- re-issuing a fresh licence under the dev-default pepper."
    $env:OWNER_DATABASE_URL = $dbUrl; $env:OWNER_LICENSE_PEPPER = $Pepper
    $env:OWNER_SECRET_KEY = "rehearsal-local-only-secret-not-for-production"; $env:OWNER_EXTERNAL_API_ENABLED = "true"
    $env:FLASK_APP = "app:create_app"
    Push-Location $owner
    try {
        $out = & $py (Join-Path $repo "scripts\ops\rehearsal_reissue.py") --pepper $Pepper --tag $Tag 2>&1
        if ($out -match "NO SUPERADMIN") {
            Write-Host "No superadmin in the rehearsal DB -- creating one (rehearsal@aura.local)."
            $env:OWNER_BOOTSTRAP_PASSWORD = "Rehearsal2026!Pass"
            & $py -m flask create-superadmin --email rehearsal@aura.local --display-name "Rehearsal Admin" --non-interactive | Out-Null
            $out = & $py (Join-Path $repo "scripts\ops\rehearsal_reissue.py") --pepper $Pepper --tag $Tag 2>&1
        }
    } finally { Pop-Location }
    $out | ForEach-Object { Write-Host "  $_" }
    $keyLine = ($out | Select-String "^LICENSE_KEY: (AURA-[A-Z0-9-]+)").Matches | Select-Object -First 1
    if (-not $keyLine) { throw "rehearsal_reissue.py did not print a fresh key (replayed tag? pass -Tag <new>)" }
    $licenceKey = $keyLine.Groups[1].Value

    # Fresh device key + fresh trust store, both from a clean slate.
    & $adb shell "pm clear $pkg" | Out-Null
    Write-Host "App data cleared on the phone."
    Write-Host ""
    Write-Host "TYPE THIS KEY on the phone's activation screen when it appears:" -ForegroundColor Green
    Write-Host "    $licenceKey" -ForegroundColor Green
    Write-Host ""
}

# 1. Device loopback :5551 -> this machine :5551.
& $adb reverse tcp:5551 tcp:5551 | Out-Null
Write-Host "adb reverse tcp:5551 in place."

# 2. Owner on 127.0.0.1:5551, HTTP, rehearsal DB, from owner/ (signing-key dir).
$env:OWNER_DATABASE_URL         = "postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_rehearsal"
$env:OWNER_LICENSE_PEPPER       = $Pepper
$env:OWNER_SECRET_KEY           = "rehearsal-local-only-secret-not-for-production"
$env:OWNER_EXTERNAL_API_ENABLED = "true"
$env:FLASK_APP                  = "app:create_app"
$log = Join-Path $env:TEMP "owner-rehearsal-5551.log"
$proc = Start-Process -FilePath $py -ArgumentList "-m","flask","run","--host","127.0.0.1","--port","5551" `
    -WorkingDirectory $owner -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru
Write-Host "Owner starting (pid $($proc.Id)), log: $log"
$ready = $false
for ($i = 0; $i -lt 30 -and -not $ready; $i++) {
    Start-Sleep -Seconds 1
    try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:5551/api/licensing/v1/signing-keys" -TimeoutSec 3 -UseBasicParsing; $ready = ($r.StatusCode -eq 200) } catch {}
}
if (-not $ready) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue; throw "Owner did not come up on 5551 -- see $log and $log.err" }
Write-Host "Owner is up; signing-keys manifest served."

# 3. Baseline event count, then launch the app and let it retry activation.
function Pull-Db {
    # The app opens licensing.db in WAL mode, so the newest rows sit in
    # licensing.db-wal until a checkpoint. Pulling the main file alone reads
    # STALE data with no error -- it reported "no state" on an activated phone
    # twice on 2026-09-05. Pull all three side by side under the same base
    # name; SQLite merges the WAL on open.
    foreach ($suffix in @("", "-wal", "-shm")) {
        $local = Join-Path $env:TEMP "phone-licensing.db$suffix"
        & $adb exec-out "run-as $pkg cat $dbFile$suffix" 2>$null > $local
        if ((Get-Item $local -ErrorAction SilentlyContinue).Length -eq 0) { Remove-Item $local -ErrorAction SilentlyContinue }
    }
}
function Count-Events {
    # After `pm clear` (re-issue mode) the app has no licensing.db until it
    # boots and touches licensing, so an absent/empty file simply means "no
    # events yet" -- it must read as 0, not abort the wait.
    Pull-Db
    $f = Join-Path $env:TEMP "phone-licensing.db"
    if (-not (Test-Path $f) -or (Get-Item $f).Length -eq 0) { return 0 }
    $n = & $py -c "import sqlite3;c=sqlite3.connect(r'$f');print(c.execute(""select count(*) from licensing_events"").fetchone()[0])" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $n) { return 0 }
    return [int]$n
}
$before = [int](Count-Events)
& $adb shell "monkey -p $pkg -c android.intent.category.LAUNCHER 1" | Out-Null
Write-Host "App launched; waiting up to $WaitSeconds s for a new licensing event ..."
$deadline = (Get-Date).AddSeconds($WaitSeconds)
do { Start-Sleep -Seconds 5; $now = [int](Count-Events) } while ($now -le $before -and (Get-Date) -lt $deadline)

# 4. Verdict, read off the device's own log -- never inferred from the UI.
Pull-Db
$dbLocal = Join-Path $env:TEMP "phone-licensing.db"
if (-not (Test-Path $dbLocal) -or (Get-Item $dbLocal).Length -eq 0) {
    Write-Host "VERDICT: NOT YET -- the app has not written licensing.db. In re-issue mode that means the key has not been typed on the phone yet; type it, then re-run with the same -Tag to poll again (nothing is re-issued on a replayed tag)." -ForegroundColor Yellow
    Write-Host "Owner is still running (pid $($proc.Id)). Stop it with:  Stop-Process -Id $($proc.Id)"
    exit 3
}
& $py -c @"
import sqlite3
c = sqlite3.connect(r'$($env:TEMP)\phone-licensing.db')
print('--- last 6 licensing_events ---')
for r in c.execute('select occurred_at, event_type, details_json from licensing_events order by id desc limit 6'):
    print('  ', r)
st = c.execute('select current_state, owner_installation_id from licensing_state').fetchall()
print('--- licensing_state ---', st if st else 'EMPTY')
ok = any(r[1] == 'ACTIVATION_SUCCEEDED' for r in c.execute('select occurred_at, event_type from licensing_events order by id desc limit 6'))
print('VERDICT:', 'ACTIVATED' if ok else 'NOT YET -- see events above')
"@

Write-Host ""
Write-Host "Owner is still running (pid $($proc.Id)) so you can keep using the phone against it."
Write-Host "Stop it with:  Stop-Process -Id $($proc.Id)"
