# Aura Lab -- read-only status report. Never starts, stops, or changes
# anything.
#
# Windows PowerShell 5.1 AND pwsh 7 compatible: no '&&', no ternary '?:', no
# null-coalescing '??', ASCII only.

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lab_common.ps1")

function Section {
    param([string]$Title)
    Write-Output ""
    Write-Output "== $Title =="
}

Write-Output "=== Aura Lab status ($(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) ==="

# ---------------------------------------------------------------------------
Section "Postgres"
try {
    if (Test-Listening -Port 5432) {
        Write-Output "Postgres: UP (listening on 5432)"
    } else {
        Write-Output "Postgres: DOWN (not listening on 5432)"
    }
} catch {
    Write-Output "Postgres: UNKNOWN ($($_.Exception.Message))"
}

# ---------------------------------------------------------------------------
Section "Owner"
$ownerBaseUrl = "http://127.0.0.1:5551"
try {
    $healthz = Get-Http -Url "$ownerBaseUrl/healthz" -TimeoutSec 5
    if ($healthz.Ok) {
        Write-Output "Owner /healthz: HTTP $($healthz.StatusCode) -- $($healthz.Body)"
    } else {
        Write-Output "Owner /healthz: unreachable"
    }
} catch {
    Write-Output "Owner /healthz: ERROR ($($_.Exception.Message))"
}

try {
    $env:PGPASSWORD = "aura_owner_dev"
    $rev = Invoke-PsqlScalar -Database "aura_owner_lab" -Sql "SELECT version_num FROM alembic_version;"
    if ($rev) {
        Write-Output "aura_owner_lab alembic revision: $rev"
    } else {
        Write-Output "aura_owner_lab alembic revision: UNKNOWN (database missing, table missing, or not yet migrated)"
    }
} catch {
    Write-Output "aura_owner_lab alembic revision: ERROR ($($_.Exception.Message))"
}

# ---------------------------------------------------------------------------
Section "Desktop / headless tills"
try {
    $runDir = Get-LabRunDir
    $pidFiles = Get-ChildItem -Path $runDir -Filter "*.pid.json" -ErrorAction SilentlyContinue |
        Where-Object { $_.BaseName -match "^(desktop-app|till-\d+)\.pid$" }
    if (-not $pidFiles -or $pidFiles.Count -eq 0) {
        Write-Output "No desktop app or headless till pid files recorded."
    }
    foreach ($file in $pidFiles) {
        $name = $file.BaseName -replace "\.pid$", ""
        $record = Get-Content -Path $file.FullName -Raw | ConvertFrom-Json
        $alive = $null -ne (Get-ProcessCommandLine -ProcessId $record.pid)
        $aliveText = "not running"
        if ($alive) { $aliveText = "running" }
        Write-Output "$name : pid=$($record.pid) ($aliveText), url=$($record.extra)"
        if ($alive -and $record.extra) {
            $statusResp = Get-Http -Url "$($record.extra)/api/licensing/status" -TimeoutSec 5
            if ($statusResp.Ok) {
                $state = $null
                try {
                    $parsed = $statusResp.Body | ConvertFrom-Json
                    $state = $parsed.current_state
                } catch {}
                if ($null -eq $state) { $state = "(could not parse current_state)" }
                Write-Output "  GET /api/licensing/status -> current_state=$state"
            } else {
                Write-Output "  GET /api/licensing/status -> unreachable"
            }
        }
    }
} catch {
    Write-Output "Desktop/till check: ERROR ($($_.Exception.Message))"
}

# ---------------------------------------------------------------------------
Section "Site relay hub (TCP 5443)"
try {
    $conns = Get-NetTCPConnection -LocalPort 5443 -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Output "5443: not listening"
    } else {
        $ownerPid = ($conns | Select-Object -First 1).OwningProcess
        $procPath = $null
        try {
            $proc = Get-Process -Id $ownerPid -ErrorAction Stop
            $procPath = $proc.Path
        } catch {}
        Write-Output "5443: listening (pid=$ownerPid, path=$procPath)"

        if ($procPath) {
            $category = $null
            try {
                $profile = Get-NetConnectionProfile -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($profile) { $category = $profile.NetworkCategory.ToString() }
            } catch {}
            $fwCategory = $category
            if ($fwCategory -eq "DomainAuthenticated") { $fwCategory = "Domain" }
            Write-Output "  Current network category: $category"

            try {
                $filters = Get-NetFirewallApplicationFilter -Program $procPath -ErrorAction SilentlyContinue
                $matchingRule = $null
                foreach ($filter in $filters) {
                    $rules = Get-NetFirewallRule -AssociatedNetFirewallApplicationFilter $filter -ErrorAction SilentlyContinue |
                        Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Allow" -and $_.Enabled -eq "True" }
                    foreach ($rule in $rules) {
                        $profileStr = $rule.Profile.ToString()
                        if ($profileStr -eq "Any" -or ($fwCategory -and $profileStr -match $fwCategory)) {
                            $matchingRule = $rule
                            break
                        }
                    }
                    if ($matchingRule) { break }
                }
                if ($matchingRule) {
                    Write-Output "  Inbound Allow firewall rule for current category: YES ($($matchingRule.DisplayName), profile=$($matchingRule.Profile))"
                } else {
                    Write-Output "  Inbound Allow firewall rule for current category: NO"
                }
            } catch {
                Write-Output "  Firewall rule check: ERROR ($($_.Exception.Message))"
            }
        }
    }
} catch {
    Write-Output "Site relay hub check: ERROR ($($_.Exception.Message))"
}

# ---------------------------------------------------------------------------
Section "LAN IPv4 (default-route interface)"
try {
    $defaultRoute = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue |
        Sort-Object -Property RouteMetric | Select-Object -First 1
    if ($defaultRoute) {
        $ip = Get-NetIPAddress -InterfaceIndex $defaultRoute.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.PrefixOrigin -ne "WellKnown" } | Select-Object -First 1
        if ($ip) {
            Write-Output "LAN IPv4: $($ip.IPAddress) (interface index $($defaultRoute.InterfaceIndex))"
        } else {
            Write-Output "LAN IPv4: could not resolve an address on the default-route interface (index $($defaultRoute.InterfaceIndex))"
        }
    } else {
        Write-Output "LAN IPv4: no default route found"
    }
} catch {
    Write-Output "LAN IPv4: ERROR ($($_.Exception.Message))"
}

# ---------------------------------------------------------------------------
Section "Android (adb)"
if (-not $AdbPath) {
    Write-Output "adb: not found (checked winget path, F:/Android/Sdk, C:/Android/Sdk, PATH)"
} else {
    Write-Output "adb: $AdbPath"
    try {
        $devicesOutput = & $AdbPath devices 2>&1
        Write-Output "adb devices:"
        $devicesOutput | ForEach-Object { Write-Output "  $_" }
    } catch {
        Write-Output "adb devices: ERROR ($($_.Exception.Message))"
    }
    try {
        $reverseOutput = & $AdbPath reverse --list 2>&1
        Write-Output "adb reverse --list:"
        $reverseOutput | ForEach-Object { Write-Output "  $_" }
    } catch {
        Write-Output "adb reverse --list: ERROR ($($_.Exception.Message))"
    }
    try {
        $deviceLines = $devicesOutput | Select-String "\tdevice$"
        if ($deviceLines -and $deviceLines.Count -ge 1) {
            $pkgOutput = & $AdbPath shell dumpsys package com.actionaura.retail.debug 2>&1
            $versionLine = $pkgOutput | Select-String "versionName="
            if ($versionLine) {
                Write-Output "com.actionaura.retail.debug: INSTALLED ($($versionLine[0].ToString().Trim()))"
            } else {
                Write-Output "com.actionaura.retail.debug: NOT INSTALLED (no versionName found)"
            }
        } else {
            Write-Output "com.actionaura.retail.debug: no attached device to check"
        }
    } catch {
        Write-Output "com.actionaura.retail.debug check: ERROR ($($_.Exception.Message))"
    }
}

Write-Output ""
Write-Output "=== end of status ==="
