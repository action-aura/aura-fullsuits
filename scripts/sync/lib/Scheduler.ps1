# Windows Task Scheduler install/uninstall/status via schtasks.exe -- the classic
# COM-based Task Scheduler API, not the modern ScheduledTasks PowerShell module.
#
# The module (CIM/WMI-based) was tried first but proved unreliable: its native MI
# layer can throw under PowerShell 7 on some machines, and even from genuine Windows
# PowerShell 5.1, Register-ScheduledTask can hit "Access is denied" for a plain
# per-user task when WMI write access to the TaskScheduler namespace is restricted
# (AV/EDR hooking WMI writes, hardened WMI ACLs, etc.) -- while schtasks.exe, doing
# the identical operation through the older RPC/COM path, works fine. It also avoids
# the module's RepetitionDuration XML-overflow bug: `/sc minute /mo N` just repeats
# forever on its own, no duration value needed.

$script:TaskName = 'AuraFullSuits-AutoSync'

function Install-SyncTask {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][int]$IntervalMinutes,
        [switch]$TrackCurrentBranch
    )

    # Resolve the real pwsh binary -- the WindowsApps execution alias does not
    # reliably resolve for a non-interactive Scheduled Task principal.
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pwshCmd -and $pwshCmd.Source -notmatch 'WindowsApps') { $pwshCmd.Source } else { (Get-Command powershell).Source }

    $scriptPath = Join-Path $RepoRoot 'scripts\sync\aura-sync.ps1'
    $commandLine = "`"$exe`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`" -Once"
    if ($TrackCurrentBranch) { $commandLine += ' -TrackCurrentBranch' }

    # /it = run using the interactive token of the logged-on user (no stored password
    # needed). /rl limited = standard rights, no elevation. /sc minute /mo N = repeats
    # every N minutes indefinitely, starting now, surviving reboots and logins.
    $output = & schtasks.exe /create /tn $script:TaskName /tr $commandLine /sc minute /mo $IntervalMinutes /it /rl limited /f 2>&1
    $exitCode = $LASTEXITCODE

    if ($exitCode -eq 0) {
        Write-Host "Installed scheduled task '$($script:TaskName)' -- runs every $IntervalMinutes min." -ForegroundColor Green
        Write-Host "Engine: $exe"
        Write-Host "Check status any time with: .\aura-sync.ps1 -Status"
    } else {
        Write-Host 'FAILED to install scheduled task.' -ForegroundColor Red
        $output | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        Write-Host 'Nothing was registered. Fall back to a foreground loop with: .\aura-sync.ps1 -Watch' -ForegroundColor Yellow
        throw "Install-SyncTask failed (exit $exitCode)"
    }
}

function Uninstall-SyncTask {
    & schtasks.exe /query /tn $script:TaskName 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "No scheduled task named '$($script:TaskName)' found." -ForegroundColor Yellow
        return
    }

    & schtasks.exe /delete /tn $script:TaskName /f 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Removed scheduled task '$($script:TaskName)'." -ForegroundColor Green
    } else {
        Write-Host "Could not remove scheduled task '$($script:TaskName)'." -ForegroundColor Red
    }
}

function Show-SyncStatus {
    param([Parameter(Mandatory)][string]$RepoRoot)

    $output = & schtasks.exe /query /tn $script:TaskName /fo list /v 2>&1
    if ($LASTEXITCODE -eq 0) {
        $status = ($output | Select-String '^Status:\s*(.+)$' | Select-Object -First 1).Matches.Groups[1].Value
        $nextRun = ($output | Select-String '^Next Run Time:\s*(.+)$' | Select-Object -First 1).Matches.Groups[1].Value
        $lastResult = ($output | Select-String '^Last Result:\s*(.+)$' | Select-Object -First 1).Matches.Groups[1].Value
        Write-Host "Scheduled task: status=$status lastResult=$lastResult nextRun=$nextRun"
    } else {
        Write-Host 'No scheduled task installed. Use -Install to set one up, or -Watch for a foreground loop.' -ForegroundColor Yellow
    }

    $runtimeDir = Join-Path $RepoRoot '.autosync'
    $lastRun = Join-Path $runtimeDir 'last-run.txt'
    Write-Host ''
    if (Test-Path -LiteralPath $lastRun) {
        Get-Content -LiteralPath $lastRun | Write-Host
    } else {
        Write-Host 'No sync pass has run yet.'
    }

    $pausePath = Join-Path $runtimeDir 'PAUSED'
    if (Test-Path -LiteralPath $pausePath) {
        Write-Host ''
        Write-Host 'PAUSED:' -ForegroundColor Yellow
        Get-Content -LiteralPath $pausePath | Write-Host
    }
}
