# Hibernate this laptop when the battery reaches 15% while discharging.
# Owner's instruction 2026-09-05: "work till the battery reaches 15%, pause,
# then hibernate." Runs on its own so it cannot depend on the assistant being
# between tasks. Plugged in (charging) never triggers it -- the level cannot
# fall while charging, and hibernating a plugged-in machine is not the ask.
$log = "$env:TEMP\battery_guard.log"
"$(Get-Date -Format s) guard started" | Add-Content $log
while ($true) {
    $b = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($b) {
        $pct = [int]$b.EstimatedChargeRemaining
        $discharging = ($b.BatteryStatus -eq 1)   # 1 = discharging, 2 = AC/charging
        "$(Get-Date -Format s) $pct% discharging=$discharging" | Add-Content $log
        if ($discharging -and $pct -le 15) {
            "$(Get-Date -Format s) THRESHOLD -- hibernating" | Add-Content $log
            Write-Output "BATTERY $pct% -- HIBERNATING NOW"
            Start-Sleep -Seconds 5
            shutdown /h
            exit 0
        }
        if ($pct -le 20 -and $discharging) { Write-Output "BATTERY LOW $pct% (discharging) -- hibernate at 15%" }
    }
    Start-Sleep -Seconds 60
}
