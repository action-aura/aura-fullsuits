# Wait until the rehearsal phone is attached AND authorised ("device", not
# "offline"/"unauthorized"), then run the unattended phone checks:
#   1. relaunch the Aura app (its embedded backend dies when Android
#      backgrounds it)
#   2. scripts/ops/phone_after_update.py  -- owner login + settings sync
#   3. scripts/ops/phone_offline_and_staff.py -- staff from the phone, offline sale
#   4. screenshot the resulting screen
# Logs to %TEMP%\phone_free_runner.log. Gives up after 8 hours.
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$adb = "C:\Users\MSI\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe"
$log = "$env:TEMP\phone_free_runner.log"
function Log($m) { $line = "$(Get-Date -Format s) $m"; Add-Content -Path $log -Value $line; Write-Output $line }

Log "runner started; waiting for an authorised device"
$deadline = (Get-Date).AddHours(8)
$ready = $false
while ((Get-Date) -lt $deadline) {
    $line = (& $adb devices 2>$null) | Select-String "`tdevice$"
    if ($line) { $ready = $true; break }
    Start-Sleep -Seconds 30
}
if (-not $ready) { Log "no authorised device within 8 hours; giving up"; exit 0 }
Log "device ready"

& $adb reverse tcp:5551 tcp:5551 | Out-Null
$apk = Join-Path $root "android\aura-retail\app\build\outputs\apk\debug\app-debug.apk"
if (Test-Path $apk) {
    # The APK on disk is whatever the last build produced; installing over
    # the running app keeps its data (the watcher does the same).
    $inst = & $adb install -r $apk 2>&1
    Log "install: $($inst -join ' ')"
}
& $adb shell monkey -p com.actionaura.retail.debug -c android.intent.category.LAUNCHER 1 2>$null | Out-Null
Start-Sleep -Seconds 35
Log "app pid: $(& $adb shell pidof com.actionaura.retail.debug)"

foreach ($script in @("phone_after_update.py", "phone_offline_and_staff.py")) {
    Log "=== $script ==="
    $out = & python (Join-Path $PSScriptRoot $script) 2>&1
    foreach ($l in $out) { Log "  $l" }
}
& $adb shell screencap -p /sdcard/runner.png | Out-Null
& $adb pull /sdcard/runner.png "$env:TEMP\phone_free_runner.png" | Out-Null
Log "screenshot at $env:TEMP\phone_free_runner.png; done"
