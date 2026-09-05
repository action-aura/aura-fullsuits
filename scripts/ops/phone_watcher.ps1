# Wait for the rehearsal phone (Mi Note 10) to be plugged in, then do the
# phone steps unattended:
#   1. adb reverse tcp:5551 -- the phone's tunnel to the rehearsal Owner
#      relay; lost whenever USB re-enumerates (hibernate, unplug)
#   2. install the current debug APK, keeping the app's data
#   3. set the shop currency to JOD (phone_set_currency.py)
#   4. relaunch the app and take a screenshot
# Logs every step to %TEMP%\phone_watcher.log; gives up after 8 hours.
# Started by rehearsal_up.ps1; safe to run on its own.
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$adb = "C:\Users\MSI\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe"
$apk = Join-Path $root "android\aura-retail\app\build\outputs\apk\debug\app-debug.apk"
$setcur = Join-Path $PSScriptRoot "phone_set_currency.py"
$log = "$env:TEMP\phone_watcher.log"
function Log($m) { $line = "$(Get-Date -Format s) $m"; Add-Content -Path $log -Value $line; Write-Output $line }

Log "watcher started; waiting for a device"
$deadline = (Get-Date).AddHours(8)
$devs = $null
while ((Get-Date) -lt $deadline) {
    $devs = (& $adb devices 2>$null) | Select-String "`tdevice$"
    if ($devs) { break }
    Start-Sleep -Seconds 30
}
if (-not $devs) { Log "no device within 8 hours; giving up"; exit 0 }
Log "device attached: $($devs.Line.Trim())"

& $adb reverse tcp:5551 tcp:5551 | Out-Null
Log "adb reverse -> $((& $adb reverse --list) -join ' ')"

if (Test-Path $apk) {
    Log "installing $apk (data kept)"
    $inst = & $adb install -r $apk 2>&1
    Log "install: $($inst -join ' ')"
} else { Log "no APK at $apk -- skipped install" }

Log "setting currency to JOD"
$cur = & python $setcur JOD 2>&1
Log ($cur -join ' | ')

Start-Sleep -Seconds 20
Log "app pid after relaunch: $(& $adb shell pidof com.actionaura.retail.debug)"
& $adb shell screencap -p /sdcard/watcher.png | Out-Null
& $adb pull /sdcard/watcher.png "$env:TEMP\phone_watcher.png" | Out-Null
Log "screenshot at $env:TEMP\phone_watcher.png; done"
