# Persisted cross-pass state: .autosync/state.json
# Kept flat and JSON-native (no dynamic hashtable keys) so ConvertTo/From-Json round-trips cleanly.

function Get-SyncStatePath {
    param([Parameter(Mandatory)][string]$RepoRoot)
    return Join-Path (Get-SyncRuntimeDir -RepoRoot $RepoRoot) 'state.json'
}

function Get-SyncState {
    param([Parameter(Mandatory)][string]$RepoRoot)

    $path = Get-SyncStatePath -RepoRoot $RepoRoot
    if (Test-Path -LiteralPath $path) {
        try {
            $obj = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
            if (-not $obj.PSObject.Properties['churnEvents'] -or $null -eq $obj.churnEvents) {
                $obj | Add-Member -NotePropertyName churnEvents -NotePropertyValue @() -Force
            }
            return $obj
        } catch {
            # Corrupt state file: start fresh rather than block the sync loop.
        }
    }

    return [pscustomobject]@{
        lastRunUtc               = $null
        lastResult                = $null
        dirtyFingerprint          = $null
        dirtyFingerprintSinceUtc  = $null
        consecutiveFailures       = 0
        authBackoffUntilUtc       = $null
        lastNotifiedConflictHash  = $null
        churnEvents               = @()   # [{ path, epoch }], pruned to the last hour on save
    }
}

function Save-SyncState {
    param(
        [Parameter(Mandatory)][string]$RepoRoot,
        [Parameter(Mandatory)]$State
    )
    $nowEpoch = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    if ($State.churnEvents) {
        $State.churnEvents = @($State.churnEvents | Where-Object { ($nowEpoch - $_.epoch) -lt 3600 })
    }
    $path = Get-SyncStatePath -RepoRoot $RepoRoot
    ($State | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath $path -Encoding UTF8
}
