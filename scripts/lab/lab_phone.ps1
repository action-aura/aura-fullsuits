# Aura Lab -- one-command phone connector.
#
# What this does, in order: checks the lab Owner is up, waits for exactly
# one Android phone over USB, prints its model/Android version, wires
# 'adb reverse tcp:5551 tcp:5551' so the phone's OWN loopback (which is what
# the lab APK is built with -- see build_lab_apk.ps1's header) reaches the
# lab Owner running on this workstation, verifies that from the phone side
# where possible, installs/updates the lab APK (sha256-checked against
# build-info.json first), launches the app, and prints the owner's next
# on-screen steps.
#
# The cable is needed only for install + activation. After the phone joins
# the shop, it finds the desktop till's LAN hub over Wi-Fi by UDP beacon
# (port 45455, TCP 5443) automatically -- see commercial_runtime/sync/
# site_relay/. 'adb reverse' does NOT survive a replug or a MIUI hibernate,
# which is exactly what -Watch exists for during a long activation session:
# it keeps re-applying the reverse every 10s until the phone is done talking
# to the lab Owner.
#
# The phone may already carry an OLDER Aura Retail debug build signed with a
# DIFFERENT debug key (this machine's ~/.android/debug.keystore was
# regenerated 2026-09-22). Android refuses to update across a signing-key
# change (INSTALL_FAILED_UPDATE_INCOMPATIBLE) and the only fix is to
# uninstall first, which DELETES that app's data on the phone -- so this
# script requires the owner to type UNINSTALL, verbatim, before it will do
# that. It never uninstalls any other package, and never uninstalls without
# that confirmation.
#
# Windows PowerShell 5.1 AND pwsh 7 compatible: no '&&', no ternary '?:', no
# null-coalescing '??', ASCII only.

param(
    [string]$Apk = "C:/Users/MSI/AuraLab/phone/AuraRetail-lab-debug.apk",
    [int]$TimeoutSec = 300,
    [switch]$Watch,
    [switch]$Status,
    [switch]$SkipInstall,
    [string]$Serial
)

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot "lab_common.ps1")

# PowerShell 7+ can turn a native command's non-zero exit into a terminating
# error depending on host configuration; force it off so this script's own
# manual $LASTEXITCODE checks (adb returns non-zero constantly as normal
# signal, e.g. "no devices found") are always what decides control flow.
# This variable does not exist in Windows PowerShell 5.1, so it is only
# touched when present -- same guard as build_lab_apk.ps1.
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
    $global:PSNativeCommandUseErrorActionPreference = $false
}

$OwnerBaseUrl = "http://127.0.0.1:5551"

# The application id is a fact of how the lab APK is built. Prefer reading
# it back from build-info.json next to the requested -Apk (the ground truth
# for THAT specific build) and fall back to the known constant only when
# build-info.json is missing or unreadable -- never invented, never guessed.
$ApplicationId = "com.actionaura.retail.debug"
$buildInfoPathForAppId = Join-Path (Split-Path -Parent $Apk) "build-info.json"
if (Test-Path $buildInfoPathForAppId) {
    try {
        $biForAppId = Get-Content -Path $buildInfoPathForAppId -Raw | ConvertFrom-Json
        if (-not [string]::IsNullOrWhiteSpace($biForAppId.applicationId)) {
            $ApplicationId = $biForAppId.applicationId
        }
    } catch {
        # Convenience read only -- fall back to the known constant silently.
    }
}

# ---------------------------------------------------------------------------
# adb helpers
# ---------------------------------------------------------------------------

function Invoke-Adb {
    <#
        Runs adb with the given arguments, optionally scoped to one device
        via '-s <serial>' inserted right after the executable. Returns
        @{ ExitCode; Output } with stderr merged in (2>&1), matching
        lab_status.ps1's own adb-calling convention. Never throws on a
        non-zero adb exit -- that is normal signal here, not a script error.
    #>
    param(
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [string]$DeviceSerial
    )
    $fullArgs = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($DeviceSerial)) {
        $fullArgs.Add("-s")
        $fullArgs.Add($DeviceSerial)
    }
    foreach ($a in $ArgumentList) { $fullArgs.Add($a) }
    $output = & $AdbPath @fullArgs 2>&1
    $exitCode = $LASTEXITCODE
    return @{ ExitCode = $exitCode; Output = $output }
}

function Get-AdbDeviceRows {
    <#
        Returns an array of @{ Serial; State }, one shot, no waiting -- 'adb
        devices' returns immediately with whatever it currently knows.

        Matches only lines shaped like '<serial><whitespace><state>' against
        a known set of adb device states, so the header line ("List of
        devices attached"), blank lines, and adb server startup chatter
        ("* daemon not running; starting now at tcp:5037") are never
        mistaken for a device row -- that startup line splits into exactly
        two whitespace-separated tokens too, which a naive parser would
        happily read as a device named "*" in state "daemon not running;
        starting now at tcp:5037".
    #>
    $result = Invoke-Adb -ArgumentList @("devices")
    $rows = New-Object System.Collections.Generic.List[object]
    foreach ($line in $result.Output) {
        $text = $line.ToString()
        if ($text -match '^(\S+)\s+(device|unauthorized|offline|authorizing|unknown|no permissions)') {
            $rows.Add(@{ Serial = $Matches[1]; State = $Matches[2] })
        }
    }
    return $rows
}

function Resolve-SingleDevice {
    <#
        Inspects the CURRENT 'adb devices' output (one shot, no waiting) and
        classifies it:
          Ready              -- exactly one usable ("device") row, or the
                                 one matching -DeviceSerial. Serial is set.
          None               -- no rows at all.
          Multiple           -- more than one row and no -DeviceSerial to
                                 disambiguate.
          NotFound           -- -DeviceSerial given but not among the rows.
          <adb state string> -- exactly one row (or the one matching
                                 -DeviceSerial) in a non-"device" state, e.g.
                                 "unauthorized" or "offline".
        Never throws or waits; the caller decides what to do with each
        status.
    #>
    param([string]$DeviceSerial)

    $rows = Get-AdbDeviceRows

    if (-not [string]::IsNullOrWhiteSpace($DeviceSerial)) {
        $matchRows = @($rows | Where-Object { $_.Serial -eq $DeviceSerial })
        if ($matchRows.Count -eq 0) {
            return @{ Status = "NotFound"; Rows = $rows; Serial = $null }
        }
        $row = $matchRows[0]
        if ($row.State -eq "device") {
            return @{ Status = "Ready"; Rows = $rows; Serial = $row.Serial }
        }
        return @{ Status = $row.State; Rows = $rows; Serial = $row.Serial }
    }

    if ($rows.Count -eq 0) {
        return @{ Status = "None"; Rows = $rows; Serial = $null }
    }
    if ($rows.Count -gt 1) {
        return @{ Status = "Multiple"; Rows = $rows; Serial = $null }
    }
    $only = $rows[0]
    if ($only.State -eq "device") {
        return @{ Status = "Ready"; Rows = $rows; Serial = $only.Serial }
    }
    return @{ Status = $only.State; Rows = $rows; Serial = $only.Serial }
}

function Write-DeviceStatusHint {
    <#
        One line of guidance for a non-Ready Resolve-SingleDevice result.
        Shared between the waiting loop and -Status so the two never drift
        into saying different things about the same state.
    #>
    param($Resolved, [string]$RequestedSerial)

    switch ($Resolved.Status) {
        "None" {
            Write-Host "  no device attached -- enable Developer options -> USB debugging on the phone and plug it in."
        }
        "unauthorized" {
            Write-Host ("  UNAUTHORIZED (" + $Resolved.Serial + ") -- accept the 'Allow USB debugging?' prompt on the phone screen.")
        }
        "offline" {
            Write-Host ("  OFFLINE (" + $Resolved.Serial + ") -- try unplugging and replugging the USB cable.")
        }
        "Multiple" {
            Write-Host "  multiple devices attached -- pass -Serial to pick one:"
            foreach ($row in $Resolved.Rows) { Write-Host ("    " + $row.Serial + "  " + $row.State) }
        }
        "NotFound" {
            Write-Host ("  serial '" + $RequestedSerial + "' is not among the attached devices:")
            foreach ($row in $Resolved.Rows) { Write-Host ("    " + $row.Serial + "  " + $row.State) }
        }
        default {
            Write-Host ("  " + $Resolved.Serial + " -- state: " + $Resolved.Status)
        }
    }
}

function Wait-ForSingleDevice {
    <#
        Polls Resolve-SingleDevice once a second until it reports Ready or
        $TimeoutSec elapses. Gives up immediately (does not wait out the
        timeout) on Multiple/NotFound, since more time will not fix a
        disambiguation problem. Prints a hint line only when the status
        changes, so a long wait does not spam identical lines.
    #>
    param([int]$TimeoutSec, [string]$DeviceSerial)

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastStatus = $null
    while ($true) {
        $resolved = Resolve-SingleDevice -DeviceSerial $DeviceSerial
        if ($resolved.Status -ne $lastStatus) {
            Write-DeviceStatusHint -Resolved $resolved -RequestedSerial $DeviceSerial
            $lastStatus = $resolved.Status
        }
        if ($resolved.Status -eq "Ready") {
            return $resolved
        }
        if ($resolved.Status -eq "Multiple" -or $resolved.Status -eq "NotFound") {
            return $resolved
        }
        if ((Get-Date) -ge $deadline) {
            return $resolved
        }
        Start-Sleep -Seconds 1
    }
}

# ---------------------------------------------------------------------------
# sha256
# ---------------------------------------------------------------------------

function Get-Sha256Hex {
    <#
        Deliberately not Get-FileHash -- see build_lab_apk.ps1's comment at
        its own sha256 step: on this machine's Windows PowerShell 5.1,
        Get-FileHash can throw CommandNotFoundException (a module autoload
        failure) even though other Microsoft.PowerShell.Utility cmdlets load
        fine right next to it, so it cannot be trusted to be present here.
        Hashing directly against .NET's own crypto API has no
        module-resolution step to fail.
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $hashBytes = $sha256.ComputeHash($stream)
    } finally {
        $stream.Dispose()
        $sha256.Dispose()
    }
    $parts = New-Object System.Collections.Generic.List[string]
    foreach ($b in $hashBytes) { $parts.Add($b.ToString("x2")) }
    return [string]::Join("", $parts)
}

# ---------------------------------------------------------------------------
# Reverse tunnel verification
# ---------------------------------------------------------------------------

function Test-ReverseFromPhone {
    <#
        Tries to confirm the reverse tunnel actually reaches the lab Owner
        FROM the phone's own shell -- 'adb reverse' reporting success only
        means adbd accepted the mapping, not that anything answers on the
        other end. Tries curl first, falls back to toybox nc (port-open
        check only, no body), and says plainly when it cannot verify either
        way rather than guessing.
    #>
    param([Parameter(Mandatory = $true)][string]$DeviceSerial)

    $curlResult = Invoke-Adb -ArgumentList @("shell", "curl", "-s", "-m", "5", "http://127.0.0.1:5551/healthz") -DeviceSerial $DeviceSerial
    $curlText = ([string]($curlResult.Output -join "`n"))
    if ($curlResult.ExitCode -eq 0 -and $curlText -match '"status"\s*:\s*"ok"') {
        Write-Host ("[lab_phone] Verified from the phone: curl http://127.0.0.1:5551/healthz -> " + $curlText.Trim())
        return $true
    }

    $ncResult = Invoke-Adb -ArgumentList @("shell", "toybox", "nc", "-z", "127.0.0.1", "5551") -DeviceSerial $DeviceSerial
    if ($ncResult.ExitCode -eq 0) {
        Write-Host "[lab_phone] Verified from the phone: toybox nc -z 127.0.0.1 5551 -- port open."
        return $true
    }

    Write-Host "[lab_phone] Could NOT verify the reverse tunnel from the phone side (no usable curl or toybox nc via adb shell)."
    Write-Host "[lab_phone] This does not necessarily mean it failed -- 'adb reverse' itself reported success above."
    return $false
}

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

function Invoke-Install {
    param([Parameter(Mandatory = $true)][string]$DeviceSerial)

    if (-not (Test-Path $Apk)) {
        Write-Host ("[lab_phone] FAILED: APK not found at " + $Apk)
        return 1
    }

    $buildInfoPath = Join-Path (Split-Path -Parent $Apk) "build-info.json"
    if (-not (Test-Path $buildInfoPath)) {
        Write-Host ("[lab_phone] FAILED: build-info.json not found next to the APK at " + $buildInfoPath + " -- refusing to install an APK it cannot verify.")
        return 1
    }
    $buildInfo = $null
    try {
        $buildInfo = Get-Content -Path $buildInfoPath -Raw | ConvertFrom-Json
    } catch {
        Write-Host ("[lab_phone] FAILED: build-info.json at " + $buildInfoPath + " is not valid JSON: " + $_.Exception.Message)
        return 1
    }
    $expectedSha256 = $buildInfo.apkSha256
    if ([string]::IsNullOrWhiteSpace($expectedSha256)) {
        Write-Host ("[lab_phone] FAILED: build-info.json at " + $buildInfoPath + " has no apkSha256 field.")
        return 1
    }

    Write-Host "[lab_phone] Verifying APK sha256 against build-info.json..."
    $actualSha256 = Get-Sha256Hex -Path $Apk
    if ($actualSha256 -ne $expectedSha256) {
        Write-Host "[lab_phone] FAILED: APK sha256 does not match build-info.json -- refusing to install a tampered or stale APK."
        Write-Host ("  expected: " + $expectedSha256)
        Write-Host ("  actual:   " + $actualSha256)
        return 1
    }
    Write-Host ("[lab_phone] sha256 OK: " + $actualSha256)

    Write-Host ("[lab_phone] Installing " + $Apk + " (adb install -r)...")
    $installResult = Invoke-Adb -ArgumentList @("install", "-r", $Apk) -DeviceSerial $DeviceSerial
    $installText = ([string]($installResult.Output -join "`n"))

    if ($installResult.ExitCode -eq 0 -and $installText -match "Success") {
        Write-Host "[lab_phone] Install OK."
        return 0
    }

    if ($installText -match "INSTALL_FAILED_UPDATE_INCOMPATIBLE") {
        Write-Host ""
        Write-Host "[lab_phone] The phone already has an OLDER Aura Retail debug build, signed"
        Write-Host "[lab_phone] with a DIFFERENT debug key than this workstation's current one"
        Write-Host "[lab_phone] (the debug keystore was regenerated 2026-09-22). Android refuses"
        Write-Host "[lab_phone] to update an app across a signing-key change."
        Write-Host "[lab_phone]"
        Write-Host "[lab_phone] The only fix is to UNINSTALL the app first. THIS DELETES ALL OF"
        Write-Host "[lab_phone] ITS DATA ON THE PHONE -- any local sales, cash sessions, or"
        Write-Host "[lab_phone] licence activation on that install are gone for good."
        Write-Host ""
        $confirm = Read-Host "Type UNINSTALL (all caps, exactly) to delete the old app and its data, or anything else to abort"
        if ($confirm -cne "UNINSTALL") {
            Write-Host "[lab_phone] Aborted -- no changes made."
            return 1
        }
        Write-Host ("[lab_phone] Uninstalling " + $ApplicationId + "...")
        $uninstallResult = Invoke-Adb -ArgumentList @("uninstall", $ApplicationId) -DeviceSerial $DeviceSerial
        $uninstallText = ([string]($uninstallResult.Output -join "`n"))
        if ($uninstallResult.ExitCode -ne 0 -or $uninstallText -notmatch "Success") {
            Write-Host "[lab_phone] FAILED: adb uninstall did not report Success:"
            Write-Host ("  " + $uninstallText)
            return 1
        }
        Write-Host "[lab_phone] Uninstall OK. Installing fresh..."
        $freshInstallResult = Invoke-Adb -ArgumentList @("install", $Apk) -DeviceSerial $DeviceSerial
        $freshInstallText = ([string]($freshInstallResult.Output -join "`n"))
        if ($freshInstallResult.ExitCode -ne 0 -or $freshInstallText -notmatch "Success") {
            Write-Host "[lab_phone] FAILED: adb install did not report Success after uninstall:"
            Write-Host ("  " + $freshInstallText)
            return 1
        }
        Write-Host "[lab_phone] Fresh install OK."
        return 0
    }

    Write-Host "[lab_phone] FAILED: adb install did not report Success:"
    Write-Host ("  " + $installText)
    return 1
}

# ---------------------------------------------------------------------------
# -Watch
# ---------------------------------------------------------------------------

function Invoke-WatchLoop {
    param([Parameter(Mandatory = $true)][string]$DeviceSerial)

    Write-Host "[lab_phone] -Watch: re-applying 'adb reverse tcp:5551 tcp:5551' every 10s while the phone is connected. Press Ctrl+C to stop."
    while ($true) {
        Start-Sleep -Seconds 10
        $resolved = Resolve-SingleDevice -DeviceSerial $DeviceSerial
        if ($resolved.Status -eq "Ready") {
            $reverseResult = Invoke-Adb -ArgumentList @("reverse", "tcp:5551", "tcp:5551") -DeviceSerial $DeviceSerial
            if ($reverseResult.ExitCode -eq 0) {
                Write-LabLog ("[lab_phone] -Watch: re-applied adb reverse tcp:5551 tcp:5551 for " + $DeviceSerial + ".")
            } else {
                Write-LabLog ("[lab_phone] -Watch: adb reverse re-apply FAILED for " + $DeviceSerial + ": " + ([string]($reverseResult.Output -join " ")))
            }
        } else {
            Write-LabLog ("[lab_phone] -Watch: device not ready (status=" + $resolved.Status + ") -- will retry in 10s.")
        }
    }
}

# ---------------------------------------------------------------------------
# -Status
# ---------------------------------------------------------------------------

function Get-PhoneLicenceState {
    <#
        Forwards a free local port to the phone app's own backend (device
        port 5000, falling back to 5001 -- the two ports the retail backend
        is known to bind to) and reads GET /api/licensing/status. Always
        removes the forward again before returning, on every path, success
        or failure.
    #>
    param([Parameter(Mandatory = $true)][string]$DeviceSerial)

    foreach ($devicePort in @(5000, 5001)) {
        Invoke-Adb -ArgumentList @("forward", "tcp:18090", ("tcp:" + $devicePort)) -DeviceSerial $DeviceSerial | Out-Null
        try {
            $resp = Get-Http -Url "http://127.0.0.1:18090/api/licensing/status" -TimeoutSec 5
            if ($resp.Ok) {
                try {
                    $parsed = $resp.Body | ConvertFrom-Json
                    if ($parsed.current_state) {
                        return [string]$parsed.current_state
                    }
                    return ("(no current_state field in response on device port " + $devicePort + ")")
                } catch {
                    return ("(could not parse response from device port " + $devicePort + " as JSON)")
                }
            }
        } finally {
            Invoke-Adb -ArgumentList @("forward", "--remove", "tcp:18090") -DeviceSerial $DeviceSerial | Out-Null
        }
    }
    return "(unreachable on device port 5000 or 5001)"
}

function Invoke-Status {
    Write-Host ("=== lab_phone status (" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + ") ===")

    Write-Host ""
    Write-Host ("Lab Owner (" + $OwnerBaseUrl + "/healthz):")
    $healthz = Get-Http -Url ($OwnerBaseUrl + "/healthz") -TimeoutSec 5
    if ($healthz.Ok) {
        Write-Host ("  OK -- " + $healthz.Body)
    } else {
        Write-Host "  unreachable -- run scripts/lab/lab_up.ps1 first if you need it up."
    }

    Write-Host ""
    if (-not $AdbPath) {
        Write-Host "adb: not found (checked winget path, F:/Android/Sdk, C:/Android/Sdk, PATH)"
        return 1
    }
    Write-Host ("adb: " + $AdbPath)

    $resolved = Resolve-SingleDevice -DeviceSerial $Serial
    Write-Host ("Device: " + $resolved.Status)
    if ($resolved.Status -eq "Ready") {
        Write-Host ("  " + $resolved.Serial + "  device")
    } else {
        Write-DeviceStatusHint -Resolved $resolved -RequestedSerial $Serial
    }

    if ($resolved.Status -ne "Ready") {
        return 1
    }
    $deviceSerial = $resolved.Serial

    Write-Host ""
    $pkgResult = Invoke-Adb -ArgumentList @("shell", "dumpsys", "package", $ApplicationId) -DeviceSerial $deviceSerial
    $versionLine = $pkgResult.Output | Select-String "versionName="
    if ($versionLine) {
        Write-Host ("Installed versionName: " + $versionLine[0].ToString().Trim())
    } else {
        Write-Host ("Installed: NOT INSTALLED (no versionName found for " + $ApplicationId + ")")
    }

    Write-Host ""
    Write-Host "adb reverse --list:"
    $reverseListResult = Invoke-Adb -ArgumentList @("reverse", "--list") -DeviceSerial $deviceSerial
    foreach ($line in $reverseListResult.Output) { Write-Host ("  " + $line) }

    Write-Host ""
    Write-Host "Licence state (forward tcp:18090 -> device 5000, fallback 5001, GET /api/licensing/status):"
    $state = Get-PhoneLicenceState -DeviceSerial $deviceSerial
    Write-Host ("  current_state: " + $state)

    return 0
}

# ---------------------------------------------------------------------------
# Setup (default action)
# ---------------------------------------------------------------------------

function Invoke-Setup {
    Write-Host ("[lab_phone] Checking lab Owner at " + $OwnerBaseUrl + "/healthz ...")
    $healthz = Get-Http -Url ($OwnerBaseUrl + "/healthz") -TimeoutSec 5
    if (-not $healthz.Ok) {
        Write-Host ("[lab_phone] FAILED: lab Owner is not reachable at " + $OwnerBaseUrl + "/healthz.")
        Write-Host "[lab_phone] Run scripts/lab/lab_up.ps1 first, then re-run this script."
        return 1
    }
    Write-Host ("[lab_phone] Lab Owner OK: " + $healthz.Body)

    if (-not $AdbPath) {
        Write-Host "[lab_phone] FAILED: adb not found (checked winget path, F:/Android/Sdk, C:/Android/Sdk, PATH)."
        return 1
    }
    Write-Host ("[lab_phone] Using adb: " + $AdbPath)

    Write-Host ("[lab_phone] Waiting up to " + $TimeoutSec + "s for a device...")
    $resolved = Wait-ForSingleDevice -TimeoutSec $TimeoutSec -DeviceSerial $Serial
    if ($resolved.Status -ne "Ready") {
        Write-Host ("[lab_phone] FAILED: no usable device within " + $TimeoutSec + "s (last status: " + $resolved.Status + ").")
        return 1
    }
    $deviceSerial = $resolved.Serial
    Write-Host ("[lab_phone] Device ready: " + $deviceSerial)

    $modelResult = Invoke-Adb -ArgumentList @("shell", "getprop", "ro.product.model") -DeviceSerial $deviceSerial
    $model = ([string]($modelResult.Output -join "")).Trim()
    $releaseResult = Invoke-Adb -ArgumentList @("shell", "getprop", "ro.build.version.release") -DeviceSerial $deviceSerial
    $release = ([string]($releaseResult.Output -join "")).Trim()
    Write-Host ("[lab_phone] Phone: " + $model + " (Android " + $release + ")")

    $reverseResult = Invoke-Adb -ArgumentList @("reverse", "tcp:5551", "tcp:5551") -DeviceSerial $deviceSerial
    if ($reverseResult.ExitCode -ne 0) {
        Write-Host "[lab_phone] FAILED: 'adb reverse tcp:5551 tcp:5551' failed:"
        foreach ($line in $reverseResult.Output) { Write-Host ("  " + $line) }
        return 1
    }
    Write-Host "[lab_phone] adb reverse tcp:5551 tcp:5551 -- OK."
    Test-ReverseFromPhone -DeviceSerial $deviceSerial | Out-Null

    if ($SkipInstall) {
        Write-Host "[lab_phone] -SkipInstall: leaving the installed app as-is."
    } else {
        $installExit = Invoke-Install -DeviceSerial $deviceSerial
        if ($installExit -ne 0) {
            return $installExit
        }
    }

    Write-Host ("[lab_phone] Launching " + $ApplicationId + " ...")
    $launchResult = Invoke-Adb -ArgumentList @("shell", "monkey", "-p", $ApplicationId, "-c", "android.intent.category.LAUNCHER", "1") -DeviceSerial $deviceSerial
    if ($launchResult.ExitCode -ne 0) {
        Write-Host "[lab_phone] WARNING: could not launch the app automatically -- open it by hand on the phone:"
        foreach ($line in $launchResult.Output) { Write-Host ("  " + $line) }
    } else {
        Write-Host "[lab_phone] App launched."
    }

    Write-Host ""
    Write-Host "[lab_phone] ===== Next steps on the phone ====="
    Write-Host "[lab_phone] 1. Enter the licence key when asked."
    Write-Host "[lab_phone] 2. If the desktop till for this shop is already activated,"
    Write-Host "[lab_phone]    choose 'Yes - connect to my shop' instead of activating a new one."
    Write-Host "[lab_phone] 3. Sign in with the desktop admin account."
    Write-Host "[lab_phone] 4. Unplugging the USB cable afterwards is fine -- sync continues"
    Write-Host "[lab_phone]    over Wi-Fi through the desktop's LAN hub, as long as the phone"
    Write-Host "[lab_phone]    and the PC stay on the SAME Wi-Fi network."
    Write-Host "[lab_phone] ====================================="

    if ($Watch) {
        Invoke-WatchLoop -DeviceSerial $deviceSerial
    }

    return 0
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

function Main {
    if ($Status) {
        return Invoke-Status
    }
    return Invoke-Setup
}

$__exitCode = Main
Write-Host ("SCRIPT_DONE exit=" + $__exitCode)
exit $__exitCode
