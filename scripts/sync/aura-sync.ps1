<#
aura-sync -- two-way git auto-sync for a shared clone.

Usage:
  .\aura-sync.ps1                       one sync pass (default)
  .\aura-sync.ps1 -Once                 same as above, explicit
  .\aura-sync.ps1 -Watch                foreground loop, Ctrl+C to stop
  .\aura-sync.ps1 -Install [-IntervalMinutes 5]   register Windows Scheduled Task
  .\aura-sync.ps1 -Uninstall            remove the scheduled task
  .\aura-sync.ps1 -Status               show task + last pass + pause state
  .\aura-sync.ps1 -Pause [-Minutes 30] [-Reason "risky rebase"]
  .\aura-sync.ps1 -Resume

See docs\ops\auto-sync.md for the full guide.
#>

param(
    [switch]$Once,
    [switch]$Watch,
    [switch]$Pause,
    [int]$Minutes = 0,
    [string]$Reason = 'manual',
    [switch]$Resume,
    [switch]$Status,
    [switch]$Install,
    [int]$IntervalMinutes = 5,
    [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'

foreach ($lib in @('Config', 'Audit', 'Notify', 'State', 'Guards', 'GitOps', 'Scheduler')) {
    . (Join-Path $PSScriptRoot "lib\$lib.ps1")
}

$gitRootRaw = (& git -C $PSScriptRoot rev-parse --show-toplevel 2>$null)
if (-not $gitRootRaw) {
    Write-Error "aura-sync: '$PSScriptRoot' is not inside a git repository."
    exit 4
}
$RepoRoot = (Resolve-Path $gitRootRaw).Path

function Write-ConflictReport {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$Branch,
        [Parameter(Mandatory)][string]$Upstream,
        [Parameter(Mandatory)][string[]]$ConflictFiles
    )
    $runtimeDir = Get-SyncRuntimeDir -RepoRoot $RepoRoot
    $headInfo = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('log', '-1', '--format=%H %s')).StdOut.Trim()
    $upstreamInfo = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('log', '-1', '--format=%H %s', $Upstream)).StdOut.Trim()
    $fileList = ($ConflictFiles | ForEach-Object { "- $_" }) -join "`n"

    $content = @"
# aura-sync conflict report

Detected: $(Get-Date -Format o)
Branch: $Branch
Local HEAD:            $headInfo
Remote ($Upstream): $upstreamInfo

## Conflicting files

$fileList

## What happened

aura-sync predicted a merge conflict BEFORE touching your working tree
(via ``git merge-tree``). Nothing was merged or rebased -- your files were
not modified. Auto-sync is now paused so it doesn't make this worse.

## How to resolve

1. Pull manually and resolve the conflict yourself:

   git pull --no-rebase origin $Branch
   # fix conflicts in the files listed above
   git add <resolved files>
   git commit
   git push origin $Branch

2. Resume auto-sync:

   .\scripts\sync\aura-sync.ps1 -Resume

## Recovery

Your pre-sync state was snapshotted and is recoverable if you need it:

   git for-each-ref refs/autosync/
"@
    Set-Content -LiteralPath (Join-Path $runtimeDir 'CONFLICT.md') -Value $content -Encoding UTF8
}

function Set-SyncPauseFlag {
    param([Parameter(Mandatory)][string]$RepoRoot, [Parameter(Mandatory)][string]$FlagReason)
    $runtimeDir = Get-SyncRuntimeDir -RepoRoot $RepoRoot
    $content = "reason=$FlagReason`nsetAtUtc=$((Get-Date).ToUniversalTime().ToString('o'))"
    Set-Content -LiteralPath (Join-Path $runtimeDir 'PAUSED') -Value $content -Encoding UTF8
}

function Invoke-SyncPass {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][hashtable]$Config
    )

    $passId = New-SyncPassId
    Write-SyncLog -RepoRoot $RepoRoot -PassId $passId -Level 'INFO' -Event 'start' -Data @{}

    $lockGuard = Invoke-Guard -RepoRoot $RepoRoot -PassId $passId -Name 'lock' -Check {
        Enter-SyncLock -RepoRoot $RepoRoot -StaleSeconds $Config['LOCK_STALE_SECONDS']
    }
    if (-not $lockGuard.Pass) {
        Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'skipped' -Reason $lockGuard.Reason
        return 2
    }

    try {
        $state = Get-SyncState -RepoRoot $RepoRoot

        $pauseGuard = Invoke-Guard -RepoRoot $RepoRoot -PassId $passId -Name 'pause' -Check { Test-PauseGuard -RepoRoot $RepoRoot }
        if (-not $pauseGuard.Pass) {
            Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'skipped' -Reason $pauseGuard.Reason
            return 2
        }

        $opGuard = Invoke-Guard -RepoRoot $RepoRoot -PassId $passId -Name 'in-progress' -Check { Test-InProgressGuard -RepoRoot $RepoRoot }
        if (-not $opGuard.Pass) {
            Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'skipped' -Reason $opGuard.Reason
            return 2
        }

        $allowedBranches = @($Config['SYNC_BRANCHES'] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
        $branchGuard = Invoke-Guard -RepoRoot $RepoRoot -PassId $passId -Name 'branch' -Check {
            Test-BranchGuard -RepoRoot $RepoRoot -AllowedBranches $allowedBranches
        }
        if (-not $branchGuard.Pass) {
            Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'skipped' -Reason $branchGuard.Reason
            return 2
        }
        $branch = $branchGuard.Data.branch

        $upstreamGuard = Invoke-Guard -RepoRoot $RepoRoot -PassId $passId -Name 'upstream' -Check { Test-UpstreamGuard -RepoRoot $RepoRoot }
        if (-not $upstreamGuard.Pass) {
            Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'error' -Reason $upstreamGuard.Reason
            return 4
        }
        $upstream = $upstreamGuard.Data.upstream

        $identityGuard = Invoke-Guard -RepoRoot $RepoRoot -PassId $passId -Name 'identity' -Check { Test-IdentityGuard -RepoRoot $RepoRoot }
        if (-not $identityGuard.Pass) {
            Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'error' -Reason $identityGuard.Reason
            return 4
        }

        $backoffGuard = Invoke-Guard -RepoRoot $RepoRoot -PassId $passId -Name 'backoff' -Check { Test-BackoffGuard -State $state }
        if (-not $backoffGuard.Pass) {
            Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'skipped' -Reason $backoffGuard.Reason
            return 2
        }

        $jitter = [math]::Max(1, $Config['JITTER_SECONDS'])
        Start-Sleep -Seconds (Get-Random -Minimum 0 -Maximum $jitter)

        New-SyncSnapshot -RepoRoot $RepoRoot -PassId $passId | Out-Null

        $fetch = Invoke-SyncFetch -RepoRoot $RepoRoot -TimeoutSeconds 60
        if ($fetch.ExitCode -ne 0) {
            $combined = "$($fetch.StdOut)`n$($fetch.StdErr)"
            $state.consecutiveFailures = [int]$state.consecutiveFailures + 1
            if ($combined -match 'Authentication failed|could not read Username|terminal prompts disabled|HTTP 403|Permission denied \(publickey\)') {
                $state.authBackoffUntilUtc = (Get-Date).ToUniversalTime().AddMinutes($Config['AUTH_BACKOFF_MINUTES']).ToString('o')
                Save-SyncState -RepoRoot $RepoRoot -State $state
                Write-SyncLog -RepoRoot $RepoRoot -PassId $passId -Level 'ERROR' -Event 'auth-failed' -Data @{ phase = 'fetch' }
                Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'error' -Reason 'auth failed on fetch -- check `gh auth status` / credential manager'
                return 3
            }
            Save-SyncState -RepoRoot $RepoRoot -State $state
            Write-SyncLog -RepoRoot $RepoRoot -PassId $passId -Level 'WARN' -Event 'fetch-failed' -Data @{ reason = 'network' }
            Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'skipped' -Reason 'offline or fetch failed (network)'
            return 0
        }
        Write-SyncLog -RepoRoot $RepoRoot -PassId $passId -Level 'DEBUG' -Event 'fetch-ok' -Data @{}

        $commitResult = [pscustomobject]@{ Committed = $false; Files = @(); SkippedPaths = @(); Sha = $null; SecretBlocked = $false; SecretHits = @() }
        if ($Config['AUTO_COMMIT']) {
            $commitResult = Invoke-AutoCommit -RepoRoot $RepoRoot -PassId $passId -Config $Config -State $state
            if ($commitResult.SecretBlocked) {
                Set-SyncPauseFlag -RepoRoot $RepoRoot -FlagReason 'secret'
                $hitSummary = ($commitResult.SecretHits | ForEach-Object { "$($_.Path):$($_.Line)[$($_.Rule)]" }) -join '; '
                Save-SyncState -RepoRoot $RepoRoot -State $state
                Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'error' -Reason "possible secret staged, auto-sync PAUSED: $hitSummary"
                return 3
            }
        }

        $status = Get-SyncStatus -RepoRoot $RepoRoot
        $integrateResult = [pscustomobject]@{ Success = $true; Conflict = $false; ConflictFiles = @(); PulledCommits = @() }
        if ($status.Behind -gt 0) {
            $integrateResult = Invoke-Integrate -RepoRoot $RepoRoot -PassId $passId -Config $Config -Branch $branch -Upstream $upstream
            if (-not $integrateResult.Success) {
                Write-ConflictReport -RepoRoot $RepoRoot -Branch $branch -Upstream $upstream -ConflictFiles $integrateResult.ConflictFiles
                Set-SyncPauseFlag -RepoRoot $RepoRoot -FlagReason 'conflict'
                $state.consecutiveFailures = [int]$state.consecutiveFailures + 1
                Save-SyncState -RepoRoot $RepoRoot -State $state
                Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result 'conflict' `
                    -Reason "conflict predicted in: $($integrateResult.ConflictFiles -join ', ') -- see .autosync\CONFLICT.md" `
                    -CommittedFiles $commitResult.Files -SkippedPaths $commitResult.SkippedPaths
                return 3
            }
        }

        $pushResult = [pscustomobject]@{ Pushed = $false; Reason = 'auto-push-disabled'; ConflictFiles = @(); AuthFailed = $false }
        if ($Config['AUTO_PUSH']) {
            $pushResult = Invoke-SyncPush -RepoRoot $RepoRoot -PassId $passId -Config $Config -Branch $branch
            if ($pushResult.AuthFailed) {
                $state.authBackoffUntilUtc = (Get-Date).ToUniversalTime().AddMinutes($Config['AUTH_BACKOFF_MINUTES']).ToString('o')
            }
            if ($pushResult.Reason -eq 'conflict-on-retry') {
                Write-ConflictReport -RepoRoot $RepoRoot -Branch $branch -Upstream $upstream -ConflictFiles $pushResult.ConflictFiles
                Set-SyncPauseFlag -RepoRoot $RepoRoot -FlagReason 'conflict'
            }
        }

        $failed = $pushResult.Reason -in @('push-failed', 'remote-rejected', 'conflict-on-retry', 'auth-failed')
        $state.lastRunUtc = (Get-Date).ToUniversalTime().ToString('o')
        $state.lastResult = if ($failed) { 'error' } else { 'ok' }
        $state.consecutiveFailures = if ($failed) { [int]$state.consecutiveFailures + 1 } else { 0 }
        if ([int]$state.consecutiveFailures -ge $Config['MAX_CONSECUTIVE_FAILURES']) {
            Set-SyncPauseFlag -RepoRoot $RepoRoot -FlagReason 'too-many-failures'
            Write-SyncLog -RepoRoot $RepoRoot -PassId $passId -Level 'CRITICAL' -Event 'auto-paused' -Data @{ consecutiveFailures = $state.consecutiveFailures }
        }
        Save-SyncState -RepoRoot $RepoRoot -State $state

        Remove-OldSyncSnapshots -RepoRoot $RepoRoot -Keep $Config['SNAPSHOT_KEEP']
        Clear-OldSyncLogs -RepoRoot $RepoRoot -RetentionDays $Config['LOG_RETENTION_DAYS']

        $overallResult = if ($integrateResult.Conflict -or $pushResult.Reason -eq 'conflict-on-retry') { 'conflict' } elseif ($failed) { 'error' } else { 'ok' }
        $pushLabel = if ($pushResult.Pushed) { 'pushed' }
            elseif ($pushResult.Reason -eq 'nothing-to-push') { 'none' }
            elseif ($pushResult.Reason -eq 'auto-push-disabled') { 'disabled' }
            else { $pushResult.Reason }

        Write-SyncSummary -RepoRoot $RepoRoot -PassId $passId -Config $Config -Result $overallResult -Reason 'pass complete' `
            -PulledCommits $integrateResult.PulledCommits -CommittedFiles $commitResult.Files -PushResult $pushLabel -SkippedPaths $commitResult.SkippedPaths

        Write-SyncLog -RepoRoot $RepoRoot -PassId $passId -Level 'INFO' -Event 'finish' -Data @{ result = $overallResult }
        return $(if ($overallResult -eq 'ok') { 0 } else { 3 })
    } finally {
        Exit-SyncLock -RepoRoot $RepoRoot
    }
}

# --- CLI dispatch ---

if ($Install) {
    Install-SyncTask -RepoRoot $RepoRoot -IntervalMinutes $IntervalMinutes
    exit 0
}

if ($Uninstall) {
    Uninstall-SyncTask
    exit 0
}

if ($Status) {
    Show-SyncStatus -RepoRoot $RepoRoot
    exit 0
}

if ($Pause) {
    $runtimeDir = Get-SyncRuntimeDir -RepoRoot $RepoRoot
    $content = "reason=$Reason`nsetAtUtc=$((Get-Date).ToUniversalTime().ToString('o'))"
    if ($Minutes -gt 0) {
        $content += "`nexpiresAtUtc=$((Get-Date).ToUniversalTime().AddMinutes($Minutes).ToString('o'))"
    }
    Set-Content -LiteralPath (Join-Path $runtimeDir 'PAUSED') -Value $content -Encoding UTF8
    $suffix = if ($Minutes -gt 0) { "resumes automatically in $Minutes min" } else { 'resume manually with -Resume' }
    Write-Host "Auto-sync paused ($Reason) -- $suffix." -ForegroundColor Yellow
    exit 0
}

if ($Resume) {
    $pausePath = Join-Path (Get-SyncRuntimeDir -RepoRoot $RepoRoot) 'PAUSED'
    Remove-Item -LiteralPath $pausePath -Force -ErrorAction SilentlyContinue
    Write-Host 'Auto-sync resumed.' -ForegroundColor Green
    exit 0
}

$config = Import-SyncConfig -RepoRoot $RepoRoot

if ($Watch) {
    Write-Host "aura-sync watching -- interval $($config['INTERVAL_SECONDS'])s, branch(es): $($config['SYNC_BRANCHES']). Ctrl+C to stop." -ForegroundColor Cyan
    try {
        while ($true) {
            Invoke-SyncPass -RepoRoot $RepoRoot -Config $config | Out-Null
            Start-Sleep -Seconds $config['INTERVAL_SECONDS']
        }
    } finally {
        Exit-SyncLock -RepoRoot $RepoRoot
    }
    exit 0
}

$exitCode = Invoke-SyncPass -RepoRoot $RepoRoot -Config $config
exit $exitCode
