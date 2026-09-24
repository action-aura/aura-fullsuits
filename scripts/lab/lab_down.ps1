# Aura Lab -- stops every lab process recorded in $LabRoot/run/*.pid.json
# (Owner, the desktop app, any headless tills). Never touches any other
# process: Stop-LabPid (lab_common.ps1) refuses a pid whose current command
# line no longer matches what was recorded when it was started.
#
# Windows PowerShell 5.1 AND pwsh 7 compatible: no '&&', no ternary '?:', no
# null-coalescing '??', ASCII only.

param(
    [string]$Only  # optional: stop only this one recorded name (e.g. "owner")
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "lab_common.ps1")

$runDir = Get-LabRunDir
$pidFiles = Get-ChildItem -Path $runDir -Filter "*.pid.json" -ErrorAction SilentlyContinue

if (-not $pidFiles -or $pidFiles.Count -eq 0) {
    Write-LabLog "lab_down: no pid files in $runDir -- nothing to stop."
    exit 0
}

$stoppedAny = $false
foreach ($file in $pidFiles) {
    $name = $file.BaseName -replace "\.pid$", ""
    if ($Only -and $name -ne $Only) {
        continue
    }
    Write-LabLog "lab_down: stopping '$name'..."
    $result = Stop-LabPid -Name $name
    if ($result) {
        $stoppedAny = $true
    }
}

if (-not $stoppedAny) {
    Write-LabLog "lab_down: nothing was actually stopped (either nothing matched -Only, or every recorded pid was already gone/mismatched)."
}
Write-LabLog "lab_down: done."
