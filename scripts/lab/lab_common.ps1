# Aura Lab -- shared helpers, dot-sourced by every other lab_*.ps1 script.
#
# Windows PowerShell 5.1 AND pwsh 7 compatible: no '&&', no ternary '?:', no
# null-coalescing '??', ASCII only.
#
# Dot-source this at the top of every other lab script:
#   . (Join-Path $PSScriptRoot "lab_common.ps1")

# ---------------------------------------------------------------------------
# Roots and fixed tool paths
# ---------------------------------------------------------------------------

$LabRoot = $env:AURA_LAB_ROOT
if ([string]::IsNullOrWhiteSpace($LabRoot)) {
    $LabRoot = "C:/Users/MSI/AuraLab"
}
if (-not (Test-Path $LabRoot)) {
    New-Item -ItemType Directory -Force -Path $LabRoot | Out-Null
}

# W root: this file lives at <W>/scripts/lab/lab_common.ps1
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$OwnerPython = "C:/Users/MSI/Desktop/aura-fullsuits/.venv-owner/Scripts/python.exe"
$OwnerFlaskExe = "C:/Users/MSI/Desktop/aura-fullsuits/.venv-owner/Scripts/flask.exe"
$RetailPython = "C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe"

$AdbCandidates = @(
    "C:/Users/MSI/AppData/Local/Microsoft/WinGet/Packages/Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe/platform-tools/adb.exe",
    "F:/Android/Sdk/platform-tools/adb.exe",
    "C:/Android/Sdk/platform-tools/adb.exe"
)
$AdbPath = $null
foreach ($candidate in $AdbCandidates) {
    if (Test-Path $candidate) {
        $AdbPath = $candidate
        break
    }
}
if (-not $AdbPath) {
    $onPath = Get-Command "adb" -ErrorAction SilentlyContinue
    if ($onPath) {
        $AdbPath = $onPath.Source
    }
}

$PgPsql = "C:/pgportable/pgsql/bin/psql.exe"
$PgCtl = "C:/pgportable/pgsql/bin/pg_ctl.exe"
$PgData = "C:/pgportable/data"

function Invoke-PsqlScalar {
    <#
        Runs a single -tAc (tuples-only, unaligned) query and returns the
        trimmed scalar result as a string -- "" (never $null, never a crash
        on .Trim()) when the query matches zero rows, which is exactly what
        psql prints as no output at all in that case.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Database,
        [Parameter(Mandatory = $true)][string]$Sql,
        [string]$PgHost = "localhost",
        [int]$PgPort = 5432,
        [string]$PgUser = "aura_owner"
    )
    $raw = & $PgPsql -h $PgHost -p $PgPort -U $PgUser -d $Database -tAc $Sql
    if ($LASTEXITCODE -ne 0) {
        throw "psql query failed (exit $LASTEXITCODE) against database '$Database': $Sql"
    }
    if ($null -eq $raw) {
        return ""
    }
    if ($raw -is [array]) {
        return (($raw -join "`n")).Trim()
    }
    return ([string]$raw).Trim()
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

function Write-LabLog {
    param(
        [Parameter(Mandatory = $true)][string]$Message
    )
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Output $line
    try {
        $logDir = Join-Path $LabRoot "logs"
        if (-not (Test-Path $logDir)) {
            New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        }
        Add-Content -Path (Join-Path $logDir "lab.log") -Value $line -Encoding UTF8
    } catch {
        # Logging must never break the caller.
    }
}

# ---------------------------------------------------------------------------
# lab.env.json
# ---------------------------------------------------------------------------

function Get-LabEnvPath {
    return (Join-Path $LabRoot "lab.env.json")
}

function Read-LabEnv {
    $path = Get-LabEnvPath
    if (-not (Test-Path $path)) {
        return $null
    }
    $raw = Get-Content -Path $path -Raw
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return $null
    }
    return $raw | ConvertFrom-Json
}

function Set-OwnerEnv {
    <#
        Sets THIS PROCESS's environment from lab.env.json so a child Owner
        process (flask/alembic/gunicorn) started right after this call
        inherits a correct, consistent environment. Never sets OWNER_ENV
        (development config is the fixed decision) and never sets
        FLASK_DEBUG.
    #>
    param(
        $LabEnv = $null
    )
    if ($null -eq $LabEnv) {
        $LabEnv = Read-LabEnv
    }
    if ($null -eq $LabEnv) {
        throw "lab.env.json not found at $(Get-LabEnvPath) -- run lab_bootstrap.ps1 first."
    }

    $env:FLASK_APP = "app:create_app"
    $env:OWNER_DATABASE_URL = $LabEnv.owner_database_url
    $env:OWNER_SECRET_KEY = $LabEnv.owner_secret_key
    $env:OWNER_LICENSE_PEPPER = $LabEnv.owner_license_pepper
    $env:OWNER_EXTERNAL_API_ENABLED = "true"
    $env:OWNER_SYNC_RELAY_PUBLIC_URL = $LabEnv.owner_sync_relay_public_url
    $env:OWNER_SIGNING_KEY_DIRECTORY = $LabEnv.signing_key_directory

    # Fixed decision: OWNER_ENV unset => DevelopmentConfig. No debugger/reloader.
    Remove-Item Env:OWNER_ENV -ErrorAction SilentlyContinue
    Remove-Item Env:FLASK_DEBUG -ErrorAction SilentlyContinue

    return $LabEnv
}

# ---------------------------------------------------------------------------
# Secrets / passwords
# ---------------------------------------------------------------------------

function New-HexSecret {
    param([int]$ByteLength = 32)
    $bytes = New-Object byte[] $ByteLength
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    } finally {
        $rng.Dispose()
    }
    $sb = New-Object System.Text.StringBuilder
    foreach ($b in $bytes) {
        [void]$sb.Append($b.ToString("x2"))
    }
    return $sb.ToString()
}

function New-StrongPassword {
    <#
        Satisfies owner/app/security/passwords.py's policy: length >= 12,
        at least 3 of {lower, upper, digit, symbol}. Generates all 4 classes
        and a length of 20 so it clears the policy with margin. Avoids
        quote/backslash/backtick/dollar characters so the value is safe to
        carry through JSON, environment variables, and PowerShell without
        any special escaping.
    #>
    param([int]$Length = 20)
    $lower = "abcdefghijkmnopqrstuvwxyz"
    $upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    $digits = "23456789"
    $symbols = "!@#%^&*-_=+"
    $all = $lower + $upper + $digits + $symbols

    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        function Get-RandomIndex([int]$Max) {
            $buf = New-Object byte[] 4
            $rng.GetBytes($buf)
            $val = [System.BitConverter]::ToUInt32($buf, 0)
            return [int]($val % [uint32]$Max)
        }

        $chars = New-Object System.Collections.Generic.List[char]
        $chars.Add($lower[(Get-RandomIndex $lower.Length)])
        $chars.Add($upper[(Get-RandomIndex $upper.Length)])
        $chars.Add($digits[(Get-RandomIndex $digits.Length)])
        $chars.Add($symbols[(Get-RandomIndex $symbols.Length)])
        while ($chars.Count -lt $Length) {
            $chars.Add($all[(Get-RandomIndex $all.Length)])
        }
        for ($i = $chars.Count - 1; $i -gt 0; $i--) {
            $j = Get-RandomIndex ($i + 1)
            $tmp = $chars[$i]
            $chars[$i] = $chars[$j]
            $chars[$j] = $tmp
        }
        $sb = New-Object System.Text.StringBuilder
        foreach ($c in $chars) {
            [void]$sb.Append($c)
        }
        return $sb.ToString()
    } finally {
        $rng.Dispose()
    }
}

# ---------------------------------------------------------------------------
# Network probes
# ---------------------------------------------------------------------------

function Test-Listening {
    param([Parameter(Mandatory = $true)][int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    return [bool]$conns
}

function Get-Http {
    <#
        One-shot GET, no retry. Returns @{ Ok; StatusCode; Body } -- never
        throws on a connection failure, only on a genuinely bad call.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 5
    )
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        return @{ Ok = $true; StatusCode = [int]$resp.StatusCode; Body = $resp.Content }
    } catch [System.Net.WebException] {
        $webResp = $_.Exception.Response
        if ($webResp) {
            $code = [int]$webResp.StatusCode
            $stream = $webResp.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $body = $reader.ReadToEnd()
            return @{ Ok = $true; StatusCode = $code; Body = $body }
        }
        return @{ Ok = $false; StatusCode = $null; Body = $null }
    } catch {
        return @{ Ok = $false; StatusCode = $null; Body = $null }
    }
}

function Wait-Http {
    <#
        Polls $Url until it returns HTTP 200 or $TimeoutSec elapses.
        Returns @{ Ok; StatusCode; Body } -- Ok is true only on a 200.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSec = 30,
        [int]$IntervalMs = 500
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $last = @{ Ok = $false; StatusCode = $null; Body = $null }
    while ((Get-Date) -lt $deadline) {
        $result = Get-Http -Url $Url -TimeoutSec 5
        if ($result.Ok -and $result.StatusCode -eq 200) {
            return $result
        }
        $last = $result
        Start-Sleep -Milliseconds $IntervalMs
    }
    return $last
}

# ---------------------------------------------------------------------------
# PID files -- $LabRoot/run/<name>.pid.json
# ---------------------------------------------------------------------------

function Get-LabRunDir {
    $runDir = Join-Path $LabRoot "run"
    if (-not (Test-Path $runDir)) {
        New-Item -ItemType Directory -Force -Path $runDir | Out-Null
    }
    return $runDir
}

function Get-ProcessCommandLine {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    try {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction Stop
        if ($proc) {
            return $proc.CommandLine
        }
    } catch {
        # process not found / WMI unavailable
    }
    return $null
}

function Save-Pid {
    <#
        Records a started process's pid + its OWN command line (read back
        from the OS right now, not assembled from what we asked for) so a
        later Stop-LabPid can refuse to kill an unrelated process that
        happens to have reused the same pid.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [string]$Extra = ""
    )
    $commandLine = Get-ProcessCommandLine -ProcessId $ProcessId
    $record = [ordered]@{
        name        = $Name
        pid         = $ProcessId
        commandLine = $commandLine
        startedAt   = (Get-Date).ToString("o")
        extra       = $Extra
    }
    $path = Join-Path (Get-LabRunDir) ($Name + ".pid.json")
    ($record | ConvertTo-Json) | Set-Content -Path $path -Encoding UTF8
    Write-LabLog "Saved pid file for '$Name': pid=$ProcessId -> $path"
    return $path
}

function Get-Pid {
    param([Parameter(Mandatory = $true)][string]$Name)
    $path = Join-Path (Get-LabRunDir) ($Name + ".pid.json")
    if (-not (Test-Path $path)) {
        return $null
    }
    try {
        return (Get-Content -Path $path -Raw | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Stop-LabPid {
    <#
        Stops the process recorded under $Name -- ONLY if a process with
        that pid still exists AND its current command line still matches
        exactly what was recorded when it was started. Otherwise refuses
        and explains why, and never touches any other process.

        The recorded pid is the venv LAUNCHER (.venv\Scripts\python.exe);
        the listening server is its child on the base interpreter. Stopping
        the launcher alone is still enough: measured 2026-09-24 on both this
        machine's 3.12 and 3.14 venv launchers, terminating the launcher
        took the listening child down with it (port closed within 3 s).
    #>
    param([Parameter(Mandatory = $true)][string]$Name)

    $record = Get-Pid -Name $Name
    $pidPath = Join-Path (Get-LabRunDir) ($Name + ".pid.json")

    if ($null -eq $record) {
        Write-LabLog "Stop-LabPid('$Name'): no pid file -- nothing to stop."
        return $false
    }

    $currentCommandLine = Get-ProcessCommandLine -ProcessId $record.pid
    if ($null -eq $currentCommandLine) {
        Write-LabLog "Stop-LabPid('$Name'): pid $($record.pid) is not running -- removing stale pid file."
        Remove-Item -Path $pidPath -ErrorAction SilentlyContinue
        return $false
    }

    if ($currentCommandLine -ne $record.commandLine) {
        $refuseMessage = "Stop-LabPid('{0}'): REFUSING to stop pid {1} -- its command line no longer matches what was recorded (the pid was likely reused by an unrelated process). Recorded=[{2}] Now=[{3}]" -f $Name, $record.pid, $record.commandLine, $currentCommandLine
        Write-LabLog $refuseMessage
        return $false
    }

    Stop-Process -Id $record.pid -Force -ErrorAction SilentlyContinue
    Write-LabLog "Stop-LabPid('$Name'): stopped pid $($record.pid)."
    Remove-Item -Path $pidPath -ErrorAction SilentlyContinue
    return $true
}
