# Config loading: flat KEY=value files, layered.
# Precedence (highest wins): CLI overrides (applied by caller) > AURA_SYNC_* env vars >
# .autosync/sync.config.local (per-dev, gitignored) > scripts/sync/sync.config (team defaults).

function Read-KeyValueFile {
    param([Parameter(Mandatory)][string]$Path)

    $result = @{}
    if (-not (Test-Path -LiteralPath $Path)) { return $result }

    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        $idx = $trimmed.IndexOf('=')
        if ($idx -lt 1) { continue }
        $key = $trimmed.Substring(0, $idx).Trim()
        $val = $trimmed.Substring($idx + 1).Trim()
        $result[$key] = $val
    }
    return $result
}

function Import-SyncConfig {
    param(
        [Parameter(Mandatory)][string]$RepoRoot
    )

    $defaultsPath = Join-Path $RepoRoot 'scripts\sync\sync.config'
    $localPath = Join-Path $RepoRoot '.autosync\sync.config.local'

    $config = Read-KeyValueFile -Path $defaultsPath
    $local = Read-KeyValueFile -Path $localPath
    foreach ($k in $local.Keys) { $config[$k] = $local[$k] }

    foreach ($envVar in Get-ChildItem env:AURA_SYNC_* -ErrorAction SilentlyContinue) {
        $key = $envVar.Name.Substring('AURA_SYNC_'.Length)
        $config[$key] = $envVar.Value
    }

    # Type coercion for known keys the engine consumes as non-strings.
    $intKeys = @('INTERVAL_SECONDS','JITTER_SECONDS','COMMIT_QUIET_SECONDS','AMEND_WINDOW_SECONDS',
                 'MAX_FILE_KB','CHURN_LIMIT','PUSH_RETRIES','LOG_RETENTION_DAYS','LOCK_STALE_SECONDS',
                 'SNAPSHOT_KEEP','AUTH_BACKOFF_MINUTES','MAX_CONSECUTIVE_FAILURES')
    $boolKeys = @('AUTO_COMMIT','AUTO_PUSH','AMEND_AUTOSYNC_COMMITS','SECRET_SCAN','NOTIFY')

    $typed = @{}
    foreach ($k in $config.Keys) {
        $v = $config[$k]
        if ($intKeys -contains $k) {
            $parsed = 0
            if ([int]::TryParse($v, [ref]$parsed)) { $typed[$k] = $parsed } else { $typed[$k] = $v }
        } elseif ($boolKeys -contains $k) {
            $typed[$k] = ($v -eq 'true' -or $v -eq '1')
        } else {
            $typed[$k] = $v
        }
    }

    if ($typed['PULL_STRATEGY'] -notin @('rebase', 'merge', 'ff-only')) {
        throw "sync.config: PULL_STRATEGY must be rebase|merge|ff-only, got '$($typed['PULL_STRATEGY'])'"
    }

    return $typed
}
