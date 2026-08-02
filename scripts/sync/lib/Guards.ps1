# Event guards: every risky step in aura-sync runs behind a named guard. Invoke-Guard
# is the single choke point that both performs the check AND writes an audit record of
# the decision (pass/fail + reason) -- so every commit, pull, and push this machine ever
# does has a traceable "why was this allowed" entry in .autosync/logs/audit.jsonl.

function Invoke-Guard {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$PassId,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][scriptblock]$Check
    )

    $result = & $Check
    $level = if ($result.Pass) { 'DEBUG' } else { 'WARN' }
    $logData = @{ guard = $Name; pass = $result.Pass; reason = $result.Reason }
    Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level $level -Event 'guard' -Data $logData
    return $result
}

function Enter-SyncLock {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][int]$StaleSeconds
    )

    $lockDir = Join-Path (Get-SyncRuntimeDir -RepoRoot $RepoRoot) 'sync.lock'
    $ownerFile = Join-Path $lockDir 'owner.txt'

    try {
        New-Item -ItemType Directory -Path $lockDir -ErrorAction Stop | Out-Null
        Set-Content -LiteralPath $ownerFile -Value "$PID@$env:COMPUTERNAME $(Get-Date -Format o)" -Encoding UTF8
        return [pscustomobject]@{ Pass = $true; Reason = 'acquired' }
    } catch {
        $dirInfo = Get-Item -LiteralPath $lockDir -ErrorAction SilentlyContinue
        $ageSeconds = if ($dirInfo) { ((Get-Date) - $dirInfo.CreationTime).TotalSeconds } else { [double]::MaxValue }

        if ($ageSeconds -gt $StaleSeconds) {
            $ownerPid = $null
            if (Test-Path -LiteralPath $ownerFile) {
                $content = Get-Content -LiteralPath $ownerFile -Raw -ErrorAction SilentlyContinue
                if ($content -match '^(\d+)@') { $ownerPid = [int]$Matches[1] }
            }
            $alive = $false
            if ($ownerPid) { $alive = [bool](Get-Process -Id $ownerPid -ErrorAction SilentlyContinue) }

            if (-not $alive) {
                Remove-Item -LiteralPath $lockDir -Recurse -Force -ErrorAction SilentlyContinue
                try {
                    New-Item -ItemType Directory -Path $lockDir -ErrorAction Stop | Out-Null
                    Set-Content -LiteralPath $ownerFile -Value "$PID@$env:COMPUTERNAME $(Get-Date -Format o)" -Encoding UTF8
                    return [pscustomobject]@{ Pass = $true; Reason = 'reclaimed-stale-lock' }
                } catch {
                    return [pscustomobject]@{ Pass = $false; Reason = 'lock-held' }
                }
            }
        }
        return [pscustomobject]@{ Pass = $false; Reason = 'lock-held' }
    }
}

function Exit-SyncLock {
    param([Parameter(Mandatory)][string]$RepoRoot)
    $lockDir = Join-Path (Get-SyncRuntimeDir -RepoRoot $RepoRoot) 'sync.lock'
    Remove-Item -LiteralPath $lockDir -Recurse -Force -ErrorAction SilentlyContinue
}

function Test-PauseGuard {
    param([Parameter(Mandatory)][string]$RepoRoot)

    $pausePath = Join-Path (Get-SyncRuntimeDir -RepoRoot $RepoRoot) 'PAUSED'
    if (Test-Path -LiteralPath $pausePath) {
        $content = Get-Content -LiteralPath $pausePath -Raw -ErrorAction SilentlyContinue
        $reason = 'manual'
        $expires = $null
        if ($content -match 'reason=(\S+)') { $reason = $Matches[1] }
        if ($content -match 'expiresAtUtc=(\S+)') {
            try { $expires = [datetime]::Parse($Matches[1], $null, [System.Globalization.DateTimeStyles]::AdjustToUniversal) } catch { }
        }
        if ($expires -and $expires -lt (Get-Date).ToUniversalTime()) {
            Remove-Item -LiteralPath $pausePath -Force -ErrorAction SilentlyContinue
        } else {
            return [pscustomobject]@{ Pass = $false; Reason = "paused:$reason" }
        }
    }

    $cfgPause = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('config', '--local', '--get', 'aura.sync.paused')
    if ($cfgPause.ExitCode -eq 0 -and $cfgPause.StdOut.Trim() -eq 'true') {
        return [pscustomobject]@{ Pass = $false; Reason = 'paused:git-config' }
    }

    return [pscustomobject]@{ Pass = $true; Reason = 'not-paused' }
}

function Test-InProgressGuard {
    param([Parameter(Mandatory)][string]$RepoRoot)
    if (Test-GitOperationInProgress -RepoRoot $RepoRoot) {
        return [pscustomobject]@{ Pass = $false; Reason = 'operation-in-progress' }
    }
    return [pscustomobject]@{ Pass = $true; Reason = 'clean' }
}

function Test-BranchGuard {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string[]]$AllowedBranches
    )
    $r = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('symbolic-ref', '--quiet', '--short', 'HEAD')
    $branch = $r.StdOut.Trim()
    if ($r.ExitCode -ne 0 -or -not $branch) {
        return [pscustomobject]@{ Pass = $false; Reason = 'detached-head' }
    }
    if ($AllowedBranches -notcontains $branch) {
        return [pscustomobject]@{ Pass = $false; Reason = "branch-not-allowed:$branch" }
    }
    return [pscustomobject]@{ Pass = $true; Reason = $branch; Data = @{ branch = $branch } }
}

function Test-UpstreamGuard {
    param([Parameter(Mandatory)][string]$RepoRoot)
    $r = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
    $upstream = $r.StdOut.Trim()
    if ($r.ExitCode -ne 0 -or -not $upstream) {
        return [pscustomobject]@{ Pass = $false; Reason = 'no-upstream' }
    }
    return [pscustomobject]@{ Pass = $true; Reason = $upstream; Data = @{ upstream = $upstream } }
}

function Test-IdentityGuard {
    param([Parameter(Mandatory)][string]$RepoRoot)
    $name = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('config', 'user.name')).StdOut.Trim()
    $email = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('config', 'user.email')).StdOut.Trim()
    if (-not $name -or -not $email) {
        return [pscustomobject]@{ Pass = $false; Reason = 'missing-identity' }
    }
    return [pscustomobject]@{ Pass = $true; Reason = 'ok' }
}

function Test-BackoffGuard {
    param([Parameter(Mandatory)]$State)
    if ($State.authBackoffUntilUtc) {
        $until = [datetime]::Parse($State.authBackoffUntilUtc)
        if ($until -gt (Get-Date).ToUniversalTime()) {
            return [pscustomobject]@{ Pass = $false; Reason = "auth-backoff-until:$($State.authBackoffUntilUtc)" }
        }
    }
    return [pscustomobject]@{ Pass = $true; Reason = 'ok' }
}

$script:SecretPatterns = @(
    @{ Name = 'aws-access-key';    Regex = 'AKIA[0-9A-Z]{16}' },
    @{ Name = 'github-pat-classic'; Regex = 'ghp_[A-Za-z0-9]{36}' },
    @{ Name = 'github-pat-fine';   Regex = 'github_pat_[A-Za-z0-9_]{22,}' },
    @{ Name = 'private-key-block'; Regex = '-----BEGIN [A-Z ]*PRIVATE KEY-----' },
    @{ Name = 'slack-token';       Regex = 'xox[baprs]-[A-Za-z0-9-]{10,}' },
    @{ Name = 'generic-sk-key';    Regex = 'sk-[A-Za-z0-9]{20,}' },
    @{ Name = 'google-api-key';    Regex = 'AIza[0-9A-Za-z_\-]{35}' }
)

function Test-SecretScan {
    param([Parameter(Mandatory)][string]$RepoRoot)

    $diff = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('diff', '--cached', '-U0')
    if ($diff.ExitCode -ne 0) { return [pscustomobject]@{ Pass = $true; Reason = 'diff-unavailable' } }

    $currentFile = $null
    $lineNo = 0
    $hits = @()

    foreach ($line in ($diff.StdOut -split "`n")) {
        if ($line -match '^\+\+\+ b/(.+)$') { $currentFile = $Matches[1]; continue }
        if ($line -match '^@@ -\d+(?:,\d+)? \+(\d+)') { $lineNo = [int]$Matches[1]; continue }

        if ($line.StartsWith('+') -and -not $line.StartsWith('+++')) {
            foreach ($p in $script:SecretPatterns) {
                if ($line -match $p.Regex) {
                    $hits += [pscustomobject]@{ Path = $currentFile; Line = $lineNo; Rule = $p.Name }
                }
            }
            $lineNo++
        } elseif (-not $line.StartsWith('-')) {
            $lineNo++
        }
    }

    if ($hits.Count -gt 0) {
        return [pscustomobject]@{ Pass = $false; Reason = 'secret-detected'; Data = @{ hits = $hits } }
    }
    return [pscustomobject]@{ Pass = $true; Reason = 'clean' }
}

function Test-ConflictMarkerGuard {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$Upstream
    )
    $diff = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('diff', "$Upstream...HEAD", '-U0')
    if ($diff.ExitCode -ne 0) { return [pscustomobject]@{ Pass = $true; Reason = 'diff-unavailable' } }

    $hasOpen = $diff.StdOut -match '(?m)^\+<<<<<<< '
    $hasMid = $diff.StdOut -match '(?m)^\+=======$'
    $hasClose = $diff.StdOut -match '(?m)^\+>>>>>>> '

    if ($hasOpen -and $hasMid -and $hasClose) {
        return [pscustomobject]@{ Pass = $false; Reason = 'conflict-markers-in-diff' }
    }
    return [pscustomobject]@{ Pass = $true; Reason = 'clean' }
}

function Test-PathDenylisted {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string[]]$Patterns
    )
    $leaf = Split-Path -Leaf $Path
    foreach ($pat in $Patterns) {
        if ($leaf -like $pat -or $Path -like $pat) { return $true }
    }
    return $false
}
