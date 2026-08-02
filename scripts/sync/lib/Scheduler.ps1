# Windows Task Scheduler install/uninstall/status. Per-user task, no admin required.

function Import-SchedulerModule {
    # On many machines, ScheduledTasks' underlying CIM/MI native layer throws
    # "The type initializer for ...ApplicationMethods threw an exception" when the
    # cmdlets are invoked from PowerShell 7 (pwsh) directly -- a known Core-edition
    # incompatibility. -UseWindowsPowerShell routes the module through a Windows
    # PowerShell 5.1 compat session instead, which works regardless of which shell
    # aura-sync.ps1 itself is running under.
    if ($PSVersionTable.PSEdition -eq 'Core') {
        Import-Module ScheduledTasks -UseWindowsPowerShell -ErrorAction Stop
    } else {
        Import-Module ScheduledTasks -ErrorAction Stop
    }
}

function Install-SyncTask {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][int]$IntervalMinutes
    )

    Import-SchedulerModule

    # Resolve the real pwsh binary -- the WindowsApps execution alias does not
    # reliably resolve for a non-interactive Scheduled Task principal.
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pwshCmd -and $pwshCmd.Source -notmatch 'WindowsApps') { $pwshCmd.Source } else { (Get-Command powershell).Source }

    $scriptPath = Join-Path $RepoRoot 'scripts\sync\aura-sync.ps1'
    $taskName = 'AuraFullSuits-AutoSync'

    $action = New-ScheduledTaskAction -Execute $exe `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`" -Once" `
        -WorkingDirectory $RepoRoot

    $repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
        -RepetitionDuration ([TimeSpan]::MaxValue)
    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn

    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 0

    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($repeatTrigger, $logonTrigger) `
        -Principal $principal -Settings $settings -Force | Out-Null

    Write-Host "Installed scheduled task '$taskName' -- runs every $IntervalMinutes min, plus at logon." -ForegroundColor Green
    Write-Host "Engine: $exe"
    Write-Host "Check status any time with: .\aura-sync.ps1 -Status"
}

function Uninstall-SyncTask {
    Import-SchedulerModule
    $taskName = 'AuraFullSuits-AutoSync'
    $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed scheduled task '$taskName'." -ForegroundColor Green
    } else {
        Write-Host "No scheduled task named '$taskName' found." -ForegroundColor Yellow
    }
}

function Show-SyncStatus {
    param([Parameter(Mandatory)][string]$RepoRoot)

    Import-SchedulerModule
    $taskName = 'AuraFullSuits-AutoSync'
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        $info = $task | Get-ScheduledTaskInfo
        Write-Host "Scheduled task: state=$($task.State) lastResult=$($info.LastTaskResult) nextRun=$($info.NextRunTime)"
    } else {
        Write-Host "No scheduled task installed. Use -Install to set one up, or -Watch for a foreground loop." -ForegroundColor Yellow
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
