# Windows Task Scheduler install/uninstall/status. Per-user task, no admin required.
#
# ScheduledTasks' underlying CIM/MI native layer is unreliable when driven from
# PowerShell 7 (pwsh) -- both calling the cmdlets directly and importing the module
# via -UseWindowsPowerShell can throw "The type initializer for
# ...ApplicationMethods threw an exception" while constructing New-ScheduledTask*
# objects. The one thing that reliably works is genuine Windows PowerShell 5.1 in
# its own process. So every ScheduledTasks operation here runs in a child
# `powershell.exe` process, regardless of what shell aura-sync.ps1 itself runs
# under, and the result is verified rather than assumed.

function Invoke-WindowsPowerShellScript {
    param([Parameter(Mandatory)][string]$ScriptBlock)

    $psPath = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($ScriptBlock))
    $output = & $psPath -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand $encoded 2>&1
    return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = @($output | ForEach-Object { $_.ToString() }) }
}

function Install-SyncTask {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][int]$IntervalMinutes
    )

    # Resolve the real pwsh binary -- the WindowsApps execution alias does not
    # reliably resolve for a non-interactive Scheduled Task principal.
    $pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
    $exe = if ($pwshCmd -and $pwshCmd.Source -notmatch 'WindowsApps') { $pwshCmd.Source } else { (Get-Command powershell).Source }

    $scriptPath = Join-Path $RepoRoot 'scripts\sync\aura-sync.ps1'
    $taskName = 'AuraFullSuits-AutoSync'
    $userId = "$env:USERDOMAIN\$env:USERNAME"

    $inner = @"
`$action = New-ScheduledTaskAction -Execute '$exe' -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$scriptPath" -Once' -WorkingDirectory '$RepoRoot'
`$repeatTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) -RepetitionDuration (New-TimeSpan -Days 7300)
`$logonTrigger = New-ScheduledTaskTrigger -AtLogOn
`$principal = New-ScheduledTaskPrincipal -UserId '$userId' -LogonType Interactive -RunLevel Limited
`$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 0
Register-ScheduledTask -TaskName '$taskName' -Action `$action -Trigger @(`$repeatTrigger, `$logonTrigger) -Principal `$principal -Settings `$settings -Force -ErrorAction Stop | Out-Null
Write-Output 'AURA_SYNC_REGISTER_OK'
"@

    $result = Invoke-WindowsPowerShellScript -ScriptBlock $inner

    if ($result.ExitCode -eq 0 -and ($result.Output -contains 'AURA_SYNC_REGISTER_OK')) {
        Write-Host "Installed scheduled task '$taskName' -- runs every $IntervalMinutes min, plus at logon." -ForegroundColor Green
        Write-Host "Engine: $exe"
        Write-Host "Check status any time with: .\aura-sync.ps1 -Status"
    } else {
        Write-Host "FAILED to install scheduled task." -ForegroundColor Red
        $result.Output | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        Write-Host "Nothing was registered. Fall back to a foreground loop with: .\aura-sync.ps1 -Watch" -ForegroundColor Yellow
        throw "Install-SyncTask failed (exit $($result.ExitCode))"
    }
}

function Uninstall-SyncTask {
    $taskName = 'AuraFullSuits-AutoSync'
    $inner = @"
`$existing = Get-ScheduledTask -TaskName '$taskName' -ErrorAction SilentlyContinue
if (`$existing) {
    Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false -ErrorAction Stop
    Write-Output 'AURA_SYNC_REMOVED'
} else {
    Write-Output 'AURA_SYNC_NOT_FOUND'
}
"@
    $result = Invoke-WindowsPowerShellScript -ScriptBlock $inner

    if ($result.Output -contains 'AURA_SYNC_REMOVED') {
        Write-Host "Removed scheduled task '$taskName'." -ForegroundColor Green
    } elseif ($result.Output -contains 'AURA_SYNC_NOT_FOUND') {
        Write-Host "No scheduled task named '$taskName' found." -ForegroundColor Yellow
    } else {
        Write-Host "Could not check/remove scheduled task." -ForegroundColor Red
        $result.Output | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    }
}

function Show-SyncStatus {
    param([Parameter(Mandatory)][string]$RepoRoot)

    $taskName = 'AuraFullSuits-AutoSync'
    $inner = @"
`$task = Get-ScheduledTask -TaskName '$taskName' -ErrorAction SilentlyContinue
if (`$task) {
    `$info = `$task | Get-ScheduledTaskInfo
    Write-Output "AURA_SYNC_STATE state=`$(`$task.State) lastResult=`$(`$info.LastTaskResult) nextRun=`$(`$info.NextRunTime)"
} else {
    Write-Output 'AURA_SYNC_NOT_INSTALLED'
}
"@
    $result = Invoke-WindowsPowerShellScript -ScriptBlock $inner
    $stateLine = $result.Output | Where-Object { $_ -like 'AURA_SYNC_STATE *' } | Select-Object -First 1

    if ($stateLine) {
        Write-Host "Scheduled task: $($stateLine -replace '^AURA_SYNC_STATE ', '')"
    } elseif ($result.Output -contains 'AURA_SYNC_NOT_INSTALLED') {
        Write-Host "No scheduled task installed. Use -Install to set one up, or -Watch for a foreground loop." -ForegroundColor Yellow
    } else {
        Write-Host "Could not query scheduled task." -ForegroundColor Red
        $result.Output | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
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
