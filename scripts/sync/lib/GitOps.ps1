# Git wrappers. Invoke-Git is the single point every git call goes through: it hard-disables
# interactive credential prompting (an expired token must fail fast, not pop a GUI and hang
# a scheduled task forever) and enforces a real process-level timeout.

function Invoke-Git {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string[]]$GitArgs,
        [int]$TimeoutSeconds = 60
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'git'
    $psi.WorkingDirectory = $RepoRoot
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true

    $hardening = @('-c', 'credential.interactive=false', '-c', 'core.askPass=', '-c', 'advice.detachedHead=false')
    foreach ($a in ($hardening + $GitArgs)) { $psi.ArgumentList.Add($a) }

    $psi.EnvironmentVariables['GIT_TERMINAL_PROMPT'] = '0'
    $psi.EnvironmentVariables['GCM_INTERACTIVE'] = 'never'
    $psi.EnvironmentVariables['GIT_ASKPASS'] = ''
    $psi.EnvironmentVariables['SSH_ASKPASS'] = ''

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi
    $null = $proc.Start()

    $stdoutTask = $proc.StandardOutput.ReadToEndAsync()
    $stderrTask = $proc.StandardError.ReadToEndAsync()

    $completed = $proc.WaitForExit($TimeoutSeconds * 1000)
    if (-not $completed) {
        try { $proc.Kill($true) } catch { }
        return [pscustomobject]@{ ExitCode = -1; StdOut = ''; StdErr = 'TIMEOUT'; TimedOut = $true }
    }

    $stdout = $stdoutTask.GetAwaiter().GetResult()
    $stderr = $stderrTask.GetAwaiter().GetResult()

    return [pscustomobject]@{ ExitCode = $proc.ExitCode; StdOut = $stdout; StdErr = $stderr; TimedOut = $false }
}

function Test-GitOperationInProgress {
    param([Parameter(Mandatory)][string]$RepoRoot)
    $gd = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('rev-parse', '--git-dir')
    if ($gd.ExitCode -ne 0) { return $true }
    $dir = $gd.StdOut.Trim()
    if (-not [System.IO.Path]::IsPathRooted($dir)) { $dir = Join-Path $RepoRoot $dir }
    foreach ($marker in @('MERGE_HEAD', 'rebase-merge', 'rebase-apply', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'BISECT_LOG')) {
        if (Test-Path -LiteralPath (Join-Path $dir $marker)) { return $true }
    }
    return $false
}

function Get-SyncStatus {
    param([Parameter(Mandatory)][string]$RepoRoot)

    $r = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('status', '--porcelain=v2', '--branch', '--untracked-files=normal')
    if ($r.ExitCode -ne 0) { throw "git status failed: $($r.StdErr)" }

    $ahead = 0; $behind = 0
    $dirty = @(); $untracked = @(); $unmerged = $false

    foreach ($line in ($r.StdOut -split "`n")) {
        if ($line -match '^# branch\.ab \+(\d+) -(\d+)') {
            $ahead = [int]$Matches[1]; $behind = [int]$Matches[2]
        } elseif ($line.StartsWith('1 ') -or $line.StartsWith('2 ')) {
            $parts = $line -split ' '
            $dirty += $parts[-1]
        } elseif ($line.StartsWith('? ')) {
            $untracked += $line.Substring(2)
        } elseif ($line.StartsWith('u ')) {
            $unmerged = $true
        }
    }

    return [pscustomobject]@{
        Ahead = $ahead; Behind = $behind
        DirtyFiles = $dirty; UntrackedFiles = $untracked
        Unmerged = $unmerged
    }
}

function New-SyncSnapshot {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$PassId
    )
    $epoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

    $head = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('rev-parse', 'HEAD')
    $headSha = $null
    if ($head.ExitCode -eq 0) {
        $headSha = $head.StdOut.Trim()
        Invoke-Git -RepoRoot $RepoRoot -GitArgs @('update-ref', "refs/autosync/backup/$epoch", $headSha) | Out-Null
    }

    $stash = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('stash', 'create')
    $wipSha = $stash.StdOut.Trim()
    if ($stash.ExitCode -eq 0 -and $wipSha) {
        Invoke-Git -RepoRoot $RepoRoot -GitArgs @('update-ref', "refs/autosync/wip/$epoch", $wipSha) | Out-Null
    }

    Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'DEBUG' -Event 'snapshot' -Data @{ epoch = $epoch; backup = $headSha; wip = $(if ($wipSha) { $wipSha } else { 'none' }) }
    return [pscustomobject]@{ Epoch = $epoch; BackupSha = $headSha; WipSha = $wipSha }
}

function Remove-OldSyncSnapshots {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][int]$Keep
    )
    $refs = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('for-each-ref', '--sort=-creatordate', '--format=%(refname)', 'refs/autosync/')
    if ($refs.ExitCode -ne 0) { return }
    $all = $refs.StdOut -split "`n" | Where-Object { $_ -ne '' }
    if ($all.Count -le $Keep) { return }
    foreach ($ref in ($all | Select-Object -Skip $Keep)) {
        Invoke-Git -RepoRoot $RepoRoot -GitArgs @('update-ref', '-d', $ref) | Out-Null
    }
}

function Invoke-SyncFetch {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [int]$TimeoutSeconds = 60
    )
    return Invoke-Git -RepoRoot $RepoRoot -GitArgs @('fetch', '--prune', '--no-tags', 'origin') -TimeoutSeconds $TimeoutSeconds
}

function Test-MergeTreeConflict {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$Upstream,
        [string]$Base = 'HEAD'
    )
    $r = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('merge-tree', '--write-tree', '--name-only', $Base, $Upstream)
    if ($r.ExitCode -eq 0) { return [pscustomobject]@{ Conflict = $false; Files = @() } }
    if ($r.ExitCode -eq 1) {
        # Output is a blank-line-separated paragraph pair: paragraph 1 is "<tree
        # oid>\n<conflicted file>\n..." (--name-only), paragraph 2 (if present) is
        # informational "Auto-merging"/"CONFLICT" messages -- not part of the file list.
        $sections = ($r.StdOut -replace "`r`n", "`n") -split "`n`n"
        $fileSectionLines = @($sections[0] -split "`n" | Where-Object { $_.Trim() -ne '' })
        $files = @($fileSectionLines | Select-Object -Skip 1)
        return [pscustomobject]@{ Conflict = $true; Files = $files }
    }
    throw "git merge-tree failed unexpectedly (exit $($r.ExitCode)): $($r.StdErr)"
}

function Get-StagedFileSizeKB {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$Path
    )
    $r = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('cat-file', '-s', ":$Path")
    if ($r.ExitCode -ne 0) { return 0 }
    $bytes = 0L
    if ([long]::TryParse($r.StdOut.Trim(), [ref]$bytes)) { return [math]::Round($bytes / 1KB, 1) }
    return 0
}

function Invoke-AutoCommit {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$PassId,
        [Parameter(Mandatory)][hashtable]$Config,
        [Parameter(Mandatory)]$State
    )

    $result = [pscustomobject]@{ Committed = $false; Files = @(); SkippedPaths = @(); Sha = $null; SecretBlocked = $false; SecretHits = @() }

    $status = Get-SyncStatus -RepoRoot $RepoRoot
    if ($status.DirtyFiles.Count -eq 0 -and $status.UntrackedFiles.Count -eq 0) {
        $State.dirtyFingerprint = $null
        $State.dirtyFingerprintSinceUtc = $null
        return $result
    }

    $porcelain = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('status', '--porcelain=v2', '--untracked-files=normal')).StdOut
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $hashBytes = $sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($porcelain))
    $fingerprint = [System.BitConverter]::ToString($hashBytes).Replace('-', '').Substring(0, 16)
    $nowUtc = (Get-Date).ToUniversalTime()
    $nowEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

    if ($State.dirtyFingerprint -ne $fingerprint) {
        $State.dirtyFingerprint = $fingerprint
        $State.dirtyFingerprintSinceUtc = $nowUtc.ToString('o')
        Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'DEBUG' -Event 'commit-debounce' -Data @{ reason = 'tree-changed-this-pass' }
        return $result
    }
    $stableSince = [datetime]::Parse($State.dirtyFingerprintSinceUtc)
    if (($nowUtc - $stableSince).TotalSeconds -lt $Config['COMMIT_QUIET_SECONDS']) {
        Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'DEBUG' -Event 'commit-debounce' -Data @{ reason = 'quiet-period-not-elapsed' }
        return $result
    }

    Invoke-Git -RepoRoot $RepoRoot -GitArgs @('add', '-A', '--', '.') | Out-Null

    $staged = @((Invoke-Git -RepoRoot $RepoRoot -GitArgs @('diff', '--cached', '--name-only', '-z')).StdOut -split "`0" | Where-Object { $_ -ne '' })

    $denyPatterns = @($Config['PATH_DENYLIST'] -split ',' | Where-Object { $_ -ne '' })
    $maxKB = $Config['MAX_FILE_KB']
    $churnLimit = $Config['CHURN_LIMIT']
    $recentChurn = @($State.churnEvents | Where-Object { ($nowEpoch - $_.epoch) -lt 3600 })

    foreach ($path in $staged) {
        $skip = $false; $reason = $null

        if (Test-PathDenylisted -Path $path -Patterns $denyPatterns) {
            $skip = $true; $reason = 'denylist'
        } else {
            $sizeKB = Get-StagedFileSizeKB -RepoRoot $RepoRoot -Path $path
            if ($sizeKB -gt $maxKB) { $skip = $true; $reason = 'size' }
        }

        if (-not $skip) {
            $churnCount = @($recentChurn | Where-Object { $_.path -eq $path }).Count
            if ($churnCount -gt $churnLimit) { $skip = $true; $reason = 'churn' }
        }

        if ($skip) {
            Invoke-Git -RepoRoot $RepoRoot -GitArgs @('restore', '--staged', '--', $path) | Out-Null
            $result.SkippedPaths += $path
            Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'WARN' -Event 'skip-path' -Data @{ path = $path; reason = $reason }
        }
    }

    $stillStaged = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('diff', '--cached', '--name-only')).StdOut.Trim()
    if (-not $stillStaged) {
        $State.dirtyFingerprint = $null
        return $result
    }

    if ($Config['SECRET_SCAN']) {
        $secretCheck = Invoke-Guard -RepoRoot $RepoRoot -PassId $PassId -Name 'secret-scan' -Check { Test-SecretScan -RepoRoot $RepoRoot }
        if (-not $secretCheck.Pass) {
            Invoke-Git -RepoRoot $RepoRoot -GitArgs @('reset') | Out-Null
            $result.SecretBlocked = $true
            $result.SecretHits = $secretCheck.Data.hits
            return $result
        }
    }

    $files = @((Invoke-Git -RepoRoot $RepoRoot -GitArgs @('diff', '--cached', '--name-only')).StdOut -split "`n" | Where-Object { $_ -ne '' })
    $topDirs = @($files | ForEach-Object { ($_ -split '/')[0] } | Select-Object -Unique | Select-Object -First 3)
    $subject = "$($Config['COMMIT_PREFIX']): $($files.Count) file(s) in $($topDirs -join ', ') [$env:COMPUTERNAME] $(Get-Date -Format 'yyyy-MM-dd HH:mm')"

    $canAmend = $false
    if ($Config['AMEND_AUTOSYNC_COMMITS']) {
        $headSubject = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('log', '-1', '--format=%s')).StdOut.Trim()
        $unpushedCount = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('rev-list', '--count', 'origin/master..HEAD')).StdOut.Trim()
        $isAncestor = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('merge-base', '--is-ancestor', 'HEAD', 'origin/master')).ExitCode -eq 0
        $unpushedInt = 0
        [void][int]::TryParse($unpushedCount, [ref]$unpushedInt)
        if ($headSubject.StartsWith($Config['COMMIT_PREFIX']) -and $unpushedInt -ge 1 -and -not $isAncestor) {
            $committerEpochRaw = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('log', '-1', '--format=%ct')).StdOut.Trim()
            $committerEpoch = 0
            [void][int]::TryParse($committerEpochRaw, [ref]$committerEpoch)
            if (($nowEpoch - $committerEpoch) -lt $Config['AMEND_WINDOW_SECONDS']) { $canAmend = $true }
        }
    }

    if ($canAmend) {
        Invoke-Git -RepoRoot $RepoRoot -GitArgs @('commit', '--amend', '-m', $subject) | Out-Null
    } else {
        Invoke-Git -RepoRoot $RepoRoot -GitArgs @('commit', '-m', $subject) | Out-Null
    }

    $sha = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('rev-parse', 'HEAD')).StdOut.Trim()

    $newChurn = @($files | ForEach-Object { [pscustomobject]@{ path = $_; epoch = $nowEpoch } })
    $State.churnEvents = @(@($State.churnEvents) + $newChurn | Where-Object { ($nowEpoch - $_.epoch) -lt 3600 })
    $State.dirtyFingerprint = $null
    $State.dirtyFingerprintSinceUtc = $null

    $result.Committed = $true
    $result.Files = $files
    $result.Sha = $sha
    Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'INFO' -Event $(if ($canAmend) { 'amend' } else { 'commit' }) -Data @{ files = $files.Count; sha = $sha }
    return $result
}

function Invoke-Integrate {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$PassId,
        [Parameter(Mandatory)][hashtable]$Config,
        [Parameter(Mandatory)][string]$Branch,
        [Parameter(Mandatory)][string]$Upstream
    )

    $result = [pscustomobject]@{ Success = $true; Conflict = $false; ConflictFiles = @(); PulledCommits = @() }

    $preCommits = @((Invoke-Git -RepoRoot $RepoRoot -GitArgs @('log', '--format=%h %s', "HEAD..$Upstream")).StdOut -split "`n" | Where-Object { $_ -ne '' })

    # A debounced (not-yet-committed) local edit is invisible to a plain `HEAD vs
    # upstream` merge-tree check. If we predict "clean" while ignoring it, autostash
    # can silently leave real conflict markers in the working tree even though `git
    # pull` itself reports success -- exactly what this tool exists to prevent. So:
    # fold any dirty tracked changes into the prediction by using a `stash create`
    # commit (non-destructive) as the merge-tree base instead of HEAD.
    $preStatus = Get-SyncStatus -RepoRoot $RepoRoot
    $hasDirty = $preStatus.DirtyFiles.Count -gt 0
    $mergeTreeBase = 'HEAD'
    if ($hasDirty) {
        $wipCheck = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('stash', 'create')
        $wipSha = $wipCheck.StdOut.Trim()
        if ($wipCheck.ExitCode -eq 0 -and $wipSha -match '^[0-9a-f]{7,40}$') {
            $mergeTreeBase = $wipSha
        }
    }

    $check = Test-MergeTreeConflict -RepoRoot $RepoRoot -Upstream $Upstream -Base $mergeTreeBase
    if ($check.Conflict) {
        $result.Success = $false; $result.Conflict = $true; $result.ConflictFiles = $check.Files
        Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'ERROR' -Event 'conflict-predicted' -Data @{ files = ($check.Files -join ','); includesDirty = $hasDirty }
        return $result
    }

    $before = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('rev-parse', 'HEAD')).StdOut.Trim()

    # Manage the stash ourselves (rather than trusting rebase/merge autostash) so a
    # "successful" pull can never quietly leave a conflicted stash-pop behind.
    $weStashed = $false
    if ($hasDirty) {
        $stashPush = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('stash', 'push', '--quiet', '-m', 'aura-sync-temp')
        if ($stashPush.ExitCode -ne 0) {
            $result.Success = $false; $result.Conflict = $true; $result.ConflictFiles = @('could not stash local changes -- see git status')
            Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'ERROR' -Event 'integrate-stash-failed' -Data @{ stderr = ($stashPush.StdErr -replace '\s+', ' ') }
            return $result
        }
        $weStashed = $true
    }

    $r = switch ($Config['PULL_STRATEGY']) {
        'rebase' { Invoke-Git -RepoRoot $RepoRoot -GitArgs @('-c', 'rebase.autoStash=true', 'pull', '--rebase=merges', '--no-tags', 'origin', $Branch) -TimeoutSeconds 90 }
        'merge'  { Invoke-Git -RepoRoot $RepoRoot -GitArgs @('-c', 'merge.autoStash=true', 'pull', '--no-rebase', '--no-edit', '--no-tags', 'origin', $Branch) -TimeoutSeconds 90 }
        default  { Invoke-Git -RepoRoot $RepoRoot -GitArgs @('pull', '--ff-only', '--no-tags', 'origin', $Branch) -TimeoutSeconds 90 }
    }

    if ($r.ExitCode -ne 0) {
        if (Test-GitOperationInProgress -RepoRoot $RepoRoot) {
            if ($Config['PULL_STRATEGY'] -eq 'rebase') {
                Invoke-Git -RepoRoot $RepoRoot -GitArgs @('rebase', '--abort') | Out-Null
            } else {
                Invoke-Git -RepoRoot $RepoRoot -GitArgs @('merge', '--abort') | Out-Null
            }
        }
        if ($weStashed) {
            $restore = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('stash', 'pop', '--quiet')
            if ($restore.ExitCode -ne 0) {
                Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'CRITICAL' -Event 'stash-restore-failed-after-integrate-abort' -Data @{ stderr = ($restore.StdErr -replace '\s+', ' ') }
            }
        }
        $after = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('rev-parse', 'HEAD')).StdOut.Trim()
        if ($after -ne $before) {
            Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'CRITICAL' -Event 'integrate-abort-mismatch' -Data @{ before = $before; after = $after }
        }
        $result.Success = $false; $result.Conflict = $true; $result.ConflictFiles = @('see git status')
        Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'ERROR' -Event 'integrate-failed' -Data @{ stderr = ($r.StdErr -replace '\s+', ' ') }
        return $result
    }

    if ($weStashed) {
        $pop = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('stash', 'pop', '--quiet')
        if ($pop.ExitCode -ne 0) {
            # The merge-tree pre-check said this would be clean; if the pop still
            # conflicts (race with another local change, rename/mode edge case), stop
            # here rather than guess. Working tree now has conflict markers -- treat
            # exactly like a predicted conflict. Stash is deliberately NOT dropped.
            Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'CRITICAL' -Event 'stash-pop-conflict-after-integrate' -Data @{ stderr = ($pop.StdErr -replace '\s+', ' ') }
            $result.Success = $false; $result.Conflict = $true
            $result.ConflictFiles = @('working tree has conflict markers from stash pop -- see git status; your pre-sync stash is preserved (git stash list)')
            return $result
        }
    }

    # Backstop: confirm the tree actually came out clean. Belt-and-suspenders in case
    # any path above (ff-only, merge, a future edge case) leaves unmerged entries that
    # weren't caught by the checks above.
    $postStatus = Get-SyncStatus -RepoRoot $RepoRoot
    if ($postStatus.Unmerged) {
        Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'CRITICAL' -Event 'unmerged-after-integrate' -Data @{}
        $result.Success = $false; $result.Conflict = $true
        $result.ConflictFiles = @('unexpected unmerged paths after integrate -- see git status')
        return $result
    }

    $result.PulledCommits = $preCommits
    Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'INFO' -Event 'pull-ok' -Data @{ commits = $preCommits.Count; strategy = $Config['PULL_STRATEGY']; hadDirty = $hasDirty }
    return $result
}

function Invoke-SyncPush {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)][string]$PassId,
        [Parameter(Mandatory)][hashtable]$Config,
        [Parameter(Mandatory)][string]$Branch
    )

    $result = [pscustomobject]@{ Pushed = $false; Reason = 'not-attempted'; ConflictFiles = @(); AuthFailed = $false }

    $aheadRaw = (Invoke-Git -RepoRoot $RepoRoot -GitArgs @('rev-list', '--count', "origin/$Branch..HEAD")).StdOut.Trim()
    $ahead = 0
    [void][int]::TryParse($aheadRaw, [ref]$ahead)
    if ($ahead -eq 0) { $result.Reason = 'nothing-to-push'; return $result }

    if ($Config['PRE_PUSH_CHECK']) {
        try {
            $p = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c', $Config['PRE_PUSH_CHECK']) -WorkingDirectory $RepoRoot -NoNewWindow -PassThru -Wait
            if ($p.ExitCode -ne 0) {
                $result.Reason = 'held-precheck-failed'
                Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'WARN' -Event 'push-held' -Data @{ reason = 'precheck'; exitCode = $p.ExitCode }
                return $result
            }
        } catch {
            $result.Reason = 'held-precheck-error'
            return $result
        }
    }

    $conflictGate = Invoke-Guard -RepoRoot $RepoRoot -PassId $PassId -Name 'conflict-marker-gate' -Check { Test-ConflictMarkerGuard -RepoRoot $RepoRoot -Upstream "origin/$Branch" }
    if (-not $conflictGate.Pass) {
        $result.Reason = 'held-conflict-markers'
        return $result
    }

    $maxAttempts = 1 + $Config['PUSH_RETRIES']
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        $r = Invoke-Git -RepoRoot $RepoRoot -GitArgs @('push', '--porcelain', 'origin', "HEAD:$Branch") -TimeoutSeconds 60
        $combined = "$($r.StdOut)`n$($r.StdErr)"

        if ($r.ExitCode -eq 0) {
            $result.Pushed = $true; $result.Reason = 'pushed'
            Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'INFO' -Event 'push-ok' -Data @{ attempt = $attempt }
            return $result
        }

        if ($combined -match '\[rejected\]' -and $combined -match '(non-fast-forward|fetch first)') {
            Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'WARN' -Event 'push-rejected' -Data @{ attempt = $attempt }
            if ($attempt -ge $maxAttempts) { break }
            Start-Sleep -Seconds (Get-Random -Minimum 2 -Maximum 5)
            Invoke-Git -RepoRoot $RepoRoot -GitArgs @('fetch', '--prune', '--no-tags', 'origin') | Out-Null
            $reintegrate = Invoke-Integrate -RepoRoot $RepoRoot -PassId $PassId -Config $Config -Branch $Branch -Upstream "origin/$Branch"
            if (-not $reintegrate.Success) {
                $result.Reason = 'conflict-on-retry'; $result.ConflictFiles = $reintegrate.ConflictFiles
                return $result
            }
            Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'INFO' -Event 'push-retry' -Data @{ attempt = $attempt }
            continue
        }

        if ($combined -match '\[remote rejected\]') {
            $result.Reason = 'remote-rejected'
            Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'ERROR' -Event 'push-remote-rejected' -Data @{ stderr = ($r.StdErr -replace '\s+', ' ') }
            return $result
        }

        if ($combined -match 'Authentication failed|could not read Username|terminal prompts disabled|HTTP 403|Permission denied \(publickey\)') {
            $result.Reason = 'auth-failed'; $result.AuthFailed = $true
            Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'ERROR' -Event 'auth-failed' -Data @{}
            return $result
        }

        $result.Reason = 'push-failed'
        Write-SyncLog -RepoRoot $RepoRoot -PassId $PassId -Level 'ERROR' -Event 'push-failed' -Data @{ stderr = ($r.StdErr -replace '\s+', ' ') }
        return $result
    }

    $result.Reason = 'push-retries-exhausted'
    return $result
}
