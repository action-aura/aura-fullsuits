# Two logs, written together on every event:
#   .autosync/logs/sync-YYYY-MM-DD.log   human-readable, greppable key=value
#   .autosync/logs/audit.jsonl           structured, one JSON object per line -- the
#                                        durable record of every guard decision and
#                                        git action this dev's machine has taken.
# Plus .autosync/last-run.txt: a one-screen confirmation of what the most recent
# pass actually did, regenerated at the end of every pass.

function Get-SyncRuntimeDir {
    param([Parameter(Mandatory)][string]$RepoRoot)
    $dir = Join-Path $RepoRoot '.autosync'
    $logsDir = Join-Path $dir 'logs'
    New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
    return $dir
}

function New-SyncPassId {
    return ('{0:yyyyMMddTHHmmss}-{1}' -f (Get-Date), (Get-Random -Maximum 9999))
}

function Write-SyncLog {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$PassId,
        [Parameter(Mandatory)][ValidateSet('DEBUG','INFO','WARN','ERROR','CRITICAL')][string]$Level,
        [Parameter(Mandatory)][string]$Event,
        [hashtable]$Data = @{}
    )

    $runtimeDir = Get-SyncRuntimeDir -RepoRoot $RepoRoot
    $now = Get-Date

    # Human log line: key=value pairs, one per event.
    $kv = ($Data.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join ' '
    $humanLine = "{0:yyyy-MM-ddTHH:mm:sszzz} [{1}] pass={2} event={3} {4}" -f $now, $Level, $PassId, $Event, $kv
    $humanPath = Join-Path $runtimeDir ("logs\sync-{0:yyyy-MM-dd}.log" -f $now)
    Add-Content -LiteralPath $humanPath -Value $humanLine -Encoding UTF8

    # Structured audit line: durable, machine-parseable, one JSON object per line.
    $record = [ordered]@{
        timestamp = $now.ToString('o')
        pass      = $PassId
        level     = $Level
        event     = $Event
        host      = $env:COMPUTERNAME
        user      = $env:USERNAME
        pid       = $PID
        data      = $Data
    }
    $auditPath = Join-Path $runtimeDir 'logs\audit.jsonl'
    Add-Content -LiteralPath $auditPath -Value ($record | ConvertTo-Json -Compress -Depth 6) -Encoding UTF8

    if ($Level -in @('WARN', 'ERROR', 'CRITICAL')) {
        Write-Host $humanLine -ForegroundColor $(if ($Level -eq 'WARN') { 'Yellow' } else { 'Red' })
    } elseif ($env:AURA_SYNC_VERBOSE -eq '1') {
        Write-Host $humanLine
    }
}

function Write-SyncSummary {
    <#
    End-of-pass confirmation. Prints a short human summary to the console and writes
    it to .autosync/last-run.txt so either dev can glance at "what did the last sync
    actually do" without reading the full log. Also emits a pass-summary audit event
    and (if enabled) a desktop notification.
    #>
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$PassId,
        [Parameter(Mandatory)][hashtable]$Config,
        [Parameter(Mandatory)][string]$Result,      # ok | skipped | conflict | error
        [Parameter(Mandatory)][string]$Reason,
        [string[]]$PulledCommits = @(),
        [string[]]$CommittedFiles = @(),
        [string]$PushResult = 'none',                # none | pushed | held | failed
        [string[]]$SkippedPaths = @(),
        [string]$Branch = ''
    )

    $runtimeDir = Get-SyncRuntimeDir -RepoRoot $RepoRoot
    $now = Get-Date

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("aura-sync  $($now.ToString('yyyy-MM-dd HH:mm:ss zzz'))  pass=$PassId  result=$Result")
    if ($Branch) {
        $mode = if ($Config['SYNC_TRACK_CURRENT']) { 'current' } else { 'fixed' }
        $lines.Add("branch: $Branch (mode=$mode)")
    }
    $lines.Add("reason: $Reason")

    if ($PulledCommits.Count -gt 0) {
        $lines.Add("pulled ($($PulledCommits.Count)):")
        $PulledCommits | ForEach-Object { $lines.Add("  $_") }
    } else {
        $lines.Add("pulled: none")
    }

    if ($CommittedFiles.Count -gt 0) {
        $lines.Add("committed ($($CommittedFiles.Count)): $($CommittedFiles -join ', ')")
    } else {
        $lines.Add("committed: none")
    }

    $lines.Add("push: $PushResult")

    if ($SkippedPaths.Count -gt 0) {
        $lines.Add("skipped (guard blocked): $($SkippedPaths -join ', ')")
    }

    $summaryText = $lines -join [Environment]::NewLine
    Set-Content -LiteralPath (Join-Path $runtimeDir 'last-run.txt') -Value $summaryText -Encoding UTF8

    Write-Host ''
    Write-Host $summaryText -ForegroundColor $(
        switch ($Result) { 'ok' { 'Green' } 'conflict' { 'Red' } 'error' { 'Red' } default { 'Gray' } }
    )
    Write-Host ''

    Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'INFO' -Event 'pass-summary' -Data @{
        result   = $Result
        pulled   = $PulledCommits.Count
        committed = $CommittedFiles.Count
        push     = $PushResult
        skipped  = $SkippedPaths.Count
    }

    if ($Config['NOTIFY'] -and $Result -ne 'ok') {
        Send-SyncNotification -Title "aura-sync: $Result" -Message $Reason -Level $(if ($Result -eq 'error' -or $Result -eq 'conflict') { 'Error' } else { 'Info' })
    } elseif ($Config['NOTIFY'] -and ($PulledCommits.Count -gt 0 -or $CommittedFiles.Count -gt 0)) {
        Send-SyncNotification -Title 'aura-sync: synced' -Message "pulled $($PulledCommits.Count), committed $($CommittedFiles.Count), push=$PushResult" -Level 'Info'
    }
}

function Clear-OldSyncLogs {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][int]$RetentionDays
    )
    $logsDir = Join-Path (Get-SyncRuntimeDir -RepoRoot $RepoRoot) 'logs'
    $cutoff = (Get-Date).AddDays(-$RetentionDays)
    Get-ChildItem -LiteralPath $logsDir -Filter 'sync-*.log' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $cutoff } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}
