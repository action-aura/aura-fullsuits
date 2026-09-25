# Aura Lab -- launches an Aura Retail till pointed at the lab Owner.
#
# Two modes:
#   (default)   Launches the real desktop app (products/retail/desktop/
#               launcher_retail.py) -- a native window for a human to use.
#   -Headless   Runs the bare backend (products/retail/backend/app.py)
#               directly, no window, on an explicit -Port/-DataDir -- used
#               by automated proofs.
#
# In both modes: AURA_OWNER_LICENSING_URL points at the lab Owner.
# AURA_SYNC_RELAY_URL and AURA_SITE_RELAY_ENABLED are deliberately NOT set --
# Owner hands the sync relay URL to the till at activation, and site-relay
# hub election is meant to be automatic, not configured here.
#
# Windows PowerShell 5.1 AND pwsh 7 compatible: no '&&', no ternary '?:', no
# null-coalescing '??', ASCII only.

param(
    [switch]$Headless,
    [int]$Port,
    [string]$DataDir,
    # Optional, headless mode only. "auto" (default) leaves
    # AURA_SITE_RELAY_ENABLED unset -- the automatic tri-state decision
    # config.py::site_relay_should_start makes on its own (see that
    # function's docstring). "0"/"1" pin the same env var's own kill-switch/
    # force-on values, exact-string compared there, never cast -- for a lab
    # proof that needs to observe or work around a same-machine two-hub port
    # conflict on 5443 (two headless tills on one box, unlike two real shop
    # machines, share one loopback network stack).
    [ValidateSet("auto", "0", "1")]
    [string]$SiteRelay = "auto",
    # Optional. Where this till activates. Defaults to the lab Owner on
    # loopback; pass the public tunnel address (https://...) to prove the
    # exact path a device at another location takes (2026-09-25).
    [string]$OwnerLicensingUrl = "http://127.0.0.1:5551/api/licensing/v1"
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "lab_common.ps1")

$logDir = Join-Path $LabRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

if ($Headless) {
    if (-not $Port) {
        throw "-Headless requires -Port."
    }
    if ([string]::IsNullOrWhiteSpace($DataDir)) {
        throw "-Headless requires -DataDir."
    }

    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

    $env:AURA_STANDALONE = "1"
    $env:AURA_APP_DATA = $DataDir
    $env:PORT = "$Port"
    $env:AURA_OWNER_LICENSING_URL = $OwnerLicensingUrl
    Remove-Item Env:AURA_SYNC_RELAY_URL -ErrorAction SilentlyContinue
    if ($SiteRelay -eq "0") {
        $env:AURA_SITE_RELAY_ENABLED = "0"
    } elseif ($SiteRelay -eq "1") {
        $env:AURA_SITE_RELAY_ENABLED = "1"
    } else {
        Remove-Item Env:AURA_SITE_RELAY_ENABLED -ErrorAction SilentlyContinue
    }
    Remove-Item Env:AURA_LAN_DEMO_MODE -ErrorAction SilentlyContinue

    $backendDir = Join-Path $RepoRoot "products/retail/backend"
    $stdout = Join-Path $logDir "till-$Port.log"
    $stderr = Join-Path $logDir "till-$Port.err"

    Write-LabLog "Starting headless till on port $Port (data: $DataDir, log: $stdout)..."
    $proc = Start-Process -FilePath $RetailPython -ArgumentList "app.py" `
        -WorkingDirectory $backendDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr

    $baseUrl = "http://127.0.0.1:$Port"
    $health = Wait-Http -Url "$baseUrl/api/health" -TimeoutSec 30
    if (-not $health.Ok) {
        throw "Headless till on port $Port did not answer /api/health with 200 within 30s. Check $stderr and $stdout."
    }
    Write-LabLog "Headless till healthy: $($health.Body)"

    Save-Pid -Name "till-$Port" -ProcessId $proc.Id -Extra $baseUrl | Out-Null
    Write-LabLog "=== Headless till up ==="
    Write-LabLog "URL: $baseUrl"
    Write-LabLog "Data dir: $DataDir"
} else {
    $desktopDataDir = Join-Path $LabRoot "desktop"
    New-Item -ItemType Directory -Force -Path $desktopDataDir | Out-Null

    $env:AURA_APP_DATA = $desktopDataDir
    $env:AURA_OWNER_LICENSING_URL = $OwnerLicensingUrl
    Remove-Item Env:AURA_SYNC_RELAY_URL -ErrorAction SilentlyContinue
    Remove-Item Env:AURA_SITE_RELAY_ENABLED -ErrorAction SilentlyContinue
    Remove-Item Env:AURA_LAN_DEMO_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:AURA_STANDALONE -ErrorAction SilentlyContinue
    Remove-Item Env:PORT -ErrorAction SilentlyContinue

    $launcherPath = Join-Path $RepoRoot "products/retail/desktop/launcher_retail.py"
    $stdout = Join-Path $logDir "desktop-app.log"
    $stderr = Join-Path $logDir "desktop-app.err"

    Write-LabLog "Starting Aura Retail desktop app (data: $desktopDataDir, log: $stdout)..."
    $proc = Start-Process -FilePath $RetailPython -ArgumentList "`"$launcherPath`"" `
        -WorkingDirectory (Join-Path $RepoRoot "products/retail/desktop") -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr

    # The launcher auto-picks the first free port from 5000 and logs it to
    # its own startup.log -- read that back rather than guessing a port.
    $startupLog = Join-Path $desktopDataDir "logs/startup.log"
    $deadline = (Get-Date).AddSeconds(45)
    $foundPort = $null
    while ((Get-Date) -lt $deadline -and -not $foundPort) {
        if (Test-Path $startupLog) {
            $content = Get-Content -Path $startupLog -Raw
            $match = [regex]::Match($content, "Starting server on http://[^:]+:(\d+)")
            if ($match.Success) {
                $foundPort = [int]$match.Groups[1].Value
            }
        }
        if (-not $foundPort) {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $foundPort) {
        throw "Could not determine the desktop app's bound port from $startupLog within 45s. Check $stderr."
    }

    $baseUrl = "http://127.0.0.1:$foundPort"
    $health = Wait-Http -Url "$baseUrl/api/health" -TimeoutSec 20
    if (-not $health.Ok) {
        throw "Desktop app on port $foundPort did not answer /api/health with 200. Check $stderr and $stdout."
    }

    Save-Pid -Name "desktop-app" -ProcessId $proc.Id -Extra $baseUrl | Out-Null
    Write-LabLog "=== Desktop app up ==="
    Write-LabLog "URL: $baseUrl"
    Write-LabLog "Data dir: $desktopDataDir"
}
