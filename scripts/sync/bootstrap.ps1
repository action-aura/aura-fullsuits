<#
aura-sync bootstrap -- one command, first-time setup on this device.
Checks git identity, runs one real sync pass so you see it work, then installs
the background schedule. Re-runnable safely (each step is idempotent).

Usage: .\bootstrap.ps1 [-IntervalMinutes 5]
#>

param([int]$IntervalMinutes = 5)

$ErrorActionPreference = 'Stop'
$engine = Join-Path $PSScriptRoot 'aura-sync.ps1'

Write-Host 'aura-sync bootstrap' -ForegroundColor Cyan
Write-Host ''

Write-Host '1/3 checking git identity...' -ForegroundColor Cyan
$name = (git config user.name 2>$null)
$email = (git config user.email 2>$null)
if (-not $name -or -not $email) {
    Write-Host '  missing. Set it first, then re-run bootstrap:' -ForegroundColor Yellow
    Write-Host '    git config --global user.name "Your Name"'
    Write-Host '    git config --global user.email "you@example.com"'
    exit 1
}
Write-Host "  ok: $name <$email>" -ForegroundColor Green

Write-Host ''
Write-Host '2/3 running first sync pass...' -ForegroundColor Cyan
& $engine -Once
if ($LASTEXITCODE -eq 3) {
    Write-Host ''
    Write-Host 'First pass hit a conflict or blocking issue (see message above / .autosync\CONFLICT.md).' -ForegroundColor Yellow
    Write-Host 'Resolve it, then re-run bootstrap to finish installing the schedule.' -ForegroundColor Yellow
    exit 1
}
if ($LASTEXITCODE -ge 4) {
    Write-Host ''
    Write-Host 'First pass failed on a config/setup problem (see message above). Fix it, then re-run bootstrap.' -ForegroundColor Red
    exit 1
}

Write-Host ''
Write-Host "3/3 installing background schedule (every $IntervalMinutes min, plus at login)..." -ForegroundColor Cyan
& $engine -Install -IntervalMinutes $IntervalMinutes

Write-Host ''
Write-Host "Done. This device now auto-syncs every $IntervalMinutes min." -ForegroundColor Green
Write-Host 'Check any time with:  .\scripts\sync\aura-sync.ps1 -Status'
Write-Host 'Pause before risky work with:  .\scripts\sync\aura-sync.ps1 -Pause -Minutes 30'
