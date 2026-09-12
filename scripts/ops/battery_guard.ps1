# Hibernate this laptop when the battery reaches 15% while discharging.
# Owner's instruction 2026-09-05: "work till the battery reaches 15%, pause,
# then hibernate." Briefly 16% on the owner's instruction 2026-09-09, returned
# to 15% on their instruction 2026-09-10 ("hibernate the laptop when the
# battery reaches 15"). The threshold is the owner's call, not a safety
# margin to be padded -- it moves only when they say so.
#
# Runs on its own so it cannot depend on the assistant being between tasks.
# Plugged in (charging) never triggers it -- the level cannot fall while
# charging, and hibernating a plugged-in machine is not the ask.
#
# NOTE for whoever edits this next: PowerShell reads the script once, at
# launch. Editing this file does NOT change the behaviour of a guard that is
# already running; the process has to be restarted, or the old threshold keeps
# being enforced by a process that looks correct in Task Manager.
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
            # Do NOT exit. The process survives hibernation and resumes here
            # when the laptop wakes (measured 2026-09-06: hibernated 06:55,
            # resumed ~12:10, the earlier version had exited and left the
            # next unplug unguarded). Wait out the resume, then keep guarding;
            # if the laptop wakes still discharging at <=15%, the next loop
            # iteration hibernates it again, which is the intended behaviour.
            Start-Sleep -Seconds 120
            "$(Get-Date -Format s) resumed; guarding again" | Add-Content $log
        }
        if ($pct -le 20 -and $discharging) { Write-Output "BATTERY LOW $pct% (discharging) -- hibernate at 15%" }
    }
    Start-Sleep -Seconds 60
}
