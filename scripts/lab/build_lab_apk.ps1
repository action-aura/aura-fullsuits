# build_lab_apk.ps1
#
# Builds the Aura Retail Android debug APK for the hardware lab, pointed at
# the lab Owner over the phone's adb-reversed loopback, and verifies what is
# actually inside the resulting APK before handing it back.
#
# Owner/sync URLs are BUILD-TIME only (see android/aura-retail/app/build.gradle,
# ownerLicensingBaseUrl / ownerSyncBaseUrl -> BuildConfig.OWNER_LICENSING_BASE_URL
# / OWNER_SYNC_BASE_URL). Android's network_security_config only allows
# cleartext to 127.0.0.1/localhost, so the lab APK must be built to talk to the
# phone's own loopback, reached from a workstation via
# "adb reverse tcp:5551 tcp:5551".
#
# This script never writes android/aura-retail/local.properties or any other
# tracked file -- URLs are passed only as -P gradle properties. It never edits
# build.gradle or any source file.
#
# Compatible with both Windows PowerShell 5.1 and PowerShell 7 (pwsh):
# no '&&', no ternary '?:', no null-coalescing '??'. ASCII only.

param(
    [string]$OwnerLicensingUrl = 'http://127.0.0.1:5551/api/licensing/v1',
    [string]$SyncUrl = 'http://127.0.0.1:5551',
    [string]$OutDir = 'C:/Users/MSI/AuraLab/phone'
)

$ErrorActionPreference = 'Continue'

# PowerShell 7+ can turn a native command's non-zero exit into a terminating
# error depending on host configuration; force it off so this script's own
# manual $LASTEXITCODE checks are always the ones that decide pass/fail. This
# variable does not exist in Windows PowerShell 5.1, so it is only touched
# when present.
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
    $global:PSNativeCommandUseErrorActionPreference = $false
}

# Write-Host, NOT Write-Output -- same defect and same fix as
# scripts/release/build_desktop_installer.ps1's Write-Step (see its comment):
# Write-Output put every log line on Main's pipeline, `exit <array>` exits 0,
# so a FAILED run was a silent success to anything keyed on the exit code.
# Nothing inside Main may write to the pipeline except its final `return <int>`.
function Write-Step {
    param([string]$Message)
    Write-Host ('[build_lab_apk] ' + $Message)
}

function Write-Fail {
    param([string]$Message)
    Write-Host ('[build_lab_apk] FAILED: ' + $Message)
}

function Main {
    $ScriptRoot = $PSScriptRoot
    if ([string]::IsNullOrEmpty($ScriptRoot)) {
        $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    $RepoRoot = (Resolve-Path (Join-Path $ScriptRoot '..\..')).Path
    $AndroidDir = Join-Path $RepoRoot 'android\aura-retail'
    $GradlewPath = Join-Path $AndroidDir 'gradlew.bat'
    $TrustAnchorSrc = Join-Path $RepoRoot 'commercial_runtime\licensing_contracts\trust_anchor.json'

    Write-Step ('Repo root: ' + $RepoRoot)
    Write-Step ('Android dir: ' + $AndroidDir)
    Write-Step ('OwnerLicensingUrl: ' + $OwnerLicensingUrl)
    Write-Step ('SyncUrl: ' + $SyncUrl)

    if (-not (Test-Path $GradlewPath)) {
        Write-Fail ('gradlew.bat not found at ' + $GradlewPath)
        return 10
    }
    if (-not (Test-Path $TrustAnchorSrc)) {
        Write-Fail ('Trust anchor source not found at ' + $TrustAnchorSrc)
        return 10
    }

    if (-not (Test-Path $OutDir)) {
        New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    }
    $OutDir = (Resolve-Path $OutDir).Path

    # ---- Step 1: resolve JAVA_HOME and ANDROID_HOME for the child process ----
    $JavaHomeCandidate = 'C:\Program Files\Eclipse Adoptium\jdk-17.0.18.8-hotspot'
    $JavaHome = $null
    if (Test-Path $JavaHomeCandidate) {
        $JavaHome = $JavaHomeCandidate
    } else {
        $AdoptiumRoot = 'C:\Program Files\Eclipse Adoptium'
        $Found = $null
        if (Test-Path $AdoptiumRoot) {
            $Found = Get-ChildItem -Path $AdoptiumRoot -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like 'jdk-17*' } |
                Select-Object -First 1
        }
        if ($null -eq $Found) {
            Write-Fail ('No jdk-17* directory found under ' + $AdoptiumRoot)
            return 11
        }
        $JavaHome = $Found.FullName
    }
    Write-Step ('JAVA_HOME resolved to: ' + $JavaHome)

    $AndroidHomeCandidateF = 'F:\Android\Sdk'
    $AndroidHomeCandidateC = 'C:\Android\Sdk'
    $AndroidHome = $null
    if (Test-Path $AndroidHomeCandidateF) {
        $AndroidHome = $AndroidHomeCandidateF
    } elseif (Test-Path $AndroidHomeCandidateC) {
        $AndroidHome = $AndroidHomeCandidateC
    } else {
        Write-Fail ('Neither ' + $AndroidHomeCandidateF + ' nor ' + $AndroidHomeCandidateC + ' exists')
        return 12
    }
    Write-Step ('ANDROID_HOME resolved to: ' + $AndroidHome)

    $env:JAVA_HOME = $JavaHome
    $env:ANDROID_HOME = $AndroidHome
    $env:Path = (Join-Path $JavaHome 'bin') + ';' + $env:Path

    # Informational only -- never fails the build.
    try {
        $DriveC = Get-PSDrive -Name C -ErrorAction Stop
        Write-Step ('Free space on C: ' + [math]::Round($DriveC.Free / 1GB, 2) + ' GB')
    } catch {
        Write-Step 'Could not read free space on C:'
    }
    $AndroidHomeDriveLetter = $AndroidHome.Substring(0, 1)
    try {
        $DriveA = Get-PSDrive -Name $AndroidHomeDriveLetter -ErrorAction Stop
        Write-Step ('Free space on ' + $AndroidHomeDriveLetter + ': ' + [math]::Round($DriveA.Free / 1GB, 2) + ' GB')
    } catch {
        Write-Step ('Could not read free space on ' + $AndroidHomeDriveLetter + ':')
    }

    # ---- Step 2: run the Gradle build ----
    $BuildLog = Join-Path $OutDir 'build.log'
    $GradleArgs = @(
        'assembleDebug',
        '--no-daemon',
        ('-PownerLicensingBaseUrl=' + $OwnerLicensingUrl),
        ('-PownerSyncBaseUrl=' + $SyncUrl)
    )
    Write-Step ('Running: gradlew.bat ' + ($GradleArgs -join ' '))
    Write-Step ('Build log: ' + $BuildLog)

    $GradleExitCode = 1
    Push-Location $AndroidDir
    try {
        $GradleOutputLines = & $GradlewPath @GradleArgs 2>&1
        $GradleExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    # Same BOM pitfall as build-info.json below (Out-File -Encoding utf8 would
    # add one under Windows PowerShell 5.1) -- write plain lines without one.
    $Utf8NoBomLog = New-Object System.Text.UTF8Encoding($false)
    $GradleOutputTextLines = @($GradleOutputLines | ForEach-Object { $_.ToString() })
    [System.IO.File]::WriteAllLines($BuildLog, $GradleOutputTextLines, $Utf8NoBomLog)

    if ($GradleExitCode -ne 0) {
        Write-Step '---- last 60 lines of build.log ----'
        $TailLines = Get-Content -Path $BuildLog -Tail 60 -ErrorAction SilentlyContinue
        foreach ($line in $TailLines) { Write-Host $line }
        Write-Fail ('gradlew.bat assembleDebug exited with code ' + $GradleExitCode)
        return 20
    }
    Write-Step 'Gradle build succeeded (exit code 0).'

    # ---- Step 3: verify BuildConfig.java contains EXACTLY the two URLs ----
    $BuildConfigDir = Join-Path $AndroidDir 'app\build\generated\source\buildConfig\debug'
    $BuildConfigCandidates = Get-ChildItem -Path $BuildConfigDir -Recurse -Filter 'BuildConfig.java' -ErrorAction SilentlyContinue
    if ($null -eq $BuildConfigCandidates -or @($BuildConfigCandidates).Count -eq 0) {
        Write-Fail ('BuildConfig.java not found under ' + $BuildConfigDir)
        return 30
    }
    $BuildConfigFile = @($BuildConfigCandidates)[0].FullName
    Write-Step ('Checking BuildConfig at: ' + $BuildConfigFile)
    $BuildConfigText = Get-Content -Path $BuildConfigFile -Raw

    $LicensingMatch = [regex]::Match($BuildConfigText, 'OWNER_LICENSING_BASE_URL\s*=\s*"([^"]*)"')
    $SyncMatch = [regex]::Match($BuildConfigText, 'OWNER_SYNC_BASE_URL\s*=\s*"([^"]*)"')

    if (-not $LicensingMatch.Success) {
        Write-Fail 'OWNER_LICENSING_BASE_URL field not found in BuildConfig.java'
        return 31
    }
    if (-not $SyncMatch.Success) {
        Write-Fail 'OWNER_SYNC_BASE_URL field not found in BuildConfig.java'
        return 31
    }

    $ActualLicensingUrl = $LicensingMatch.Groups[1].Value
    $ActualSyncUrl = $SyncMatch.Groups[1].Value

    Write-Step ('BuildConfig.OWNER_LICENSING_BASE_URL = ' + $ActualLicensingUrl)
    Write-Step ('BuildConfig.OWNER_SYNC_BASE_URL = ' + $ActualSyncUrl)

    if ($ActualLicensingUrl -ne $OwnerLicensingUrl) {
        Write-Fail ('OWNER_LICENSING_BASE_URL mismatch: expected [' + $OwnerLicensingUrl + '] got [' + $ActualLicensingUrl + ']')
        return 32
    }
    if ($ActualSyncUrl -ne $SyncUrl) {
        Write-Fail ('OWNER_SYNC_BASE_URL mismatch: expected [' + $SyncUrl + '] got [' + $ActualSyncUrl + ']')
        return 32
    }
    Write-Step 'BuildConfig URLs verified: EXACT match.'

    # ---- Step 4: verify the trust anchor bundled inside the APK ----
    $ApkPath = Join-Path $AndroidDir 'app\build\outputs\apk\debug\app-debug.apk'
    $MetadataPath = Join-Path $AndroidDir 'app\build\outputs\apk\debug\output-metadata.json'
    if (-not (Test-Path $ApkPath)) {
        Write-Fail ('APK not found at ' + $ApkPath)
        return 40
    }
    if (-not (Test-Path $MetadataPath)) {
        Write-Fail ('output-metadata.json not found at ' + $MetadataPath)
        return 40
    }

    $TrustAnchorJson = Get-Content -Path $TrustAnchorSrc -Raw | ConvertFrom-Json
    $SrcKeyId = $TrustAnchorJson.keys[0].key_id
    $SrcPublicKey = $TrustAnchorJson.keys[0].public_key
    Write-Step ('Source trust anchor key_id: ' + $SrcKeyId)

    $VenvPython = 'C:\Users\MSI\Desktop\aura-fullsuits\.venv\Scripts\python.exe'
    if (-not (Test-Path $VenvPython)) {
        Write-Fail ('Verification python not found at ' + $VenvPython)
        return 41
    }

    $VerifyScriptPath = Join-Path $OutDir '_verify_trust_anchor.py'
    $VerifyScriptLines = @(
        'import sys, zipfile, io, json',
        'apk_path = sys.argv[1]',
        'expected_key_id = sys.argv[2]',
        'expected_public_key = sys.argv[3]',
        'z = zipfile.ZipFile(apk_path)',
        'try:',
        '    imy_data = z.read("assets/chaquopy/app.imy")',
        'except KeyError:',
        '    print("TRUST_ANCHOR_MISSING=app.imy")',
        '    sys.exit(1)',
        'inner = zipfile.ZipFile(io.BytesIO(imy_data))',
        'ta_path = "commercial_runtime/licensing_contracts/trust_anchor.json"',
        'try:',
        '    raw = inner.read(ta_path)',
        'except KeyError:',
        '    print("TRUST_ANCHOR_MISSING=" + ta_path)',
        '    sys.exit(1)',
        'data = json.loads(raw)',
        'keys = data.get("keys", [])',
        'if not keys:',
        '    print("TRUST_ANCHOR_EMPTY=TRUE")',
        '    sys.exit(1)',
        'apk_key_id = keys[0].get("key_id", "")',
        'apk_public_key = keys[0].get("public_key", "")',
        'print("APK_KEY_ID=" + apk_key_id)',
        'print("APK_PUBLIC_KEY=" + apk_public_key)',
        'if apk_key_id == expected_key_id and apk_public_key == expected_public_key:',
        '    print("TRUST_ANCHOR_MATCH=TRUE")',
        '    sys.exit(0)',
        'else:',
        '    print("TRUST_ANCHOR_MATCH=FALSE")',
        '    sys.exit(1)'
    )
    Set-Content -Path $VerifyScriptPath -Value $VerifyScriptLines -Encoding ascii

    $VerifyOutput = & $VenvPython $VerifyScriptPath $ApkPath $SrcKeyId $SrcPublicKey 2>&1
    $VerifyExitCode = $LASTEXITCODE
    foreach ($line in $VerifyOutput) { Write-Step ('trust-anchor-check: ' + $line) }

    if ($VerifyExitCode -ne 0) {
        Write-Fail 'Trust anchor inside the APK does not match, or is missing.'
        return 42
    }
    Write-Step 'Trust anchor inside the APK verified: EXACT match.'

    # ---- Step 5: copy the APK, write sha256 and build-info.json ----
    $Metadata = Get-Content -Path $MetadataPath -Raw | ConvertFrom-Json
    $Element = @($Metadata.elements)[0]
    $VersionName = $Element.versionName
    $VersionCode = $Element.versionCode
    $ApplicationId = $Metadata.applicationId

    $DestApk = Join-Path $OutDir 'AuraRetail-lab-debug.apk'
    Copy-Item -Path $ApkPath -Destination $DestApk -Force

    $ApkSizeBytes = (Get-Item $DestApk).Length

    # Deliberately NOT Get-FileHash: measured on this machine, the plain
    # 'powershell.exe' that Start-Process resolves is Windows PowerShell 5.1,
    # and in that specific process Get-FileHash throws CommandNotFoundException
    # (module autoload failure) even though ConvertFrom-Json/ConvertTo-Json from
    # the SAME Microsoft.PowerShell.Utility module load fine right next to it --
    # so the cmdlet cannot be trusted to be present here. That failure is
    # silent under $ErrorActionPreference = 'Continue': $Sha256 previously came
    # out $null and both build-info.json and the .sha256 file shipped with no
    # real hash in them at all, with the script still reporting SCRIPT_DONE
    # exit=0. Computing the digest directly against .NET's own crypto API has
    # no module-resolution step to fail, so it cannot hit this failure mode.
    $Sha256Provider = [System.Security.Cryptography.SHA256]::Create()
    $ApkStream = [System.IO.File]::OpenRead($DestApk)
    try {
        $HashBytes = $Sha256Provider.ComputeHash($ApkStream)
    } finally {
        $ApkStream.Dispose()
        $Sha256Provider.Dispose()
    }
    $HashHexParts = New-Object System.Collections.Generic.List[string]
    foreach ($b in $HashBytes) { $HashHexParts.Add($b.ToString('x2')) }
    $Sha256 = [string]::Join('', $HashHexParts)

    # Never trust the digest silently: a 64-hex-char SHA-256 is the one
    # invariant that lets a bad computation be caught here instead of shipping
    # as a plausible-looking but wrong value in build-info.json.
    if ($Sha256 -notmatch '^[0-9a-f]{64}$') {
        Write-Fail ('Computed SHA256 does not look like a valid digest: [' + $Sha256 + ']')
        return 43
    }

    $Sha256FilePath = $DestApk + '.sha256'
    Set-Content -Path $Sha256FilePath -Value ($Sha256 + '  AuraRetail-lab-debug.apk') -Encoding ascii

    $GitCommit = 'UNKNOWN'
    try {
        $GitOutput = & git -C $RepoRoot rev-parse HEAD 2>&1
        if ($LASTEXITCODE -eq 0) {
            $GitCommit = ($GitOutput | Select-Object -First 1).ToString().Trim()
        }
    } catch {
        $GitCommit = 'UNKNOWN'
    }

    $BuildTimeUtc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

    $BuildInfo = [ordered]@{
        gitCommit             = $GitCommit
        buildTimeUtc          = $BuildTimeUtc
        ownerLicensingBaseUrl = $OwnerLicensingUrl
        ownerSyncBaseUrl      = $SyncUrl
        trustAnchorKeyId      = $SrcKeyId
        versionName           = $VersionName
        versionCode           = $VersionCode
        applicationId         = $ApplicationId
        apkSizeBytes          = $ApkSizeBytes
        apkSha256             = $Sha256
    }
    $BuildInfoPath = Join-Path $OutDir 'build-info.json'
    $BuildInfoJsonText = $BuildInfo | ConvertTo-Json -Depth 5
    # Deliberately not Set-Content -Encoding utf8: measured on this machine,
    # Windows PowerShell 5.1 (what the detached child actually is here) writes
    # a UTF-8 BOM for that encoding name, while PowerShell 7 does not for the
    # same flag -- the BOM then makes a strict JSON reader fail with
    # "Expecting value: line 1 column 1" (reproduced against this exact file
    # with Python's json.load). Writing via .NET's UTF8Encoding($false)
    # sidesteps the edition-dependent default instead of guessing at it.
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($BuildInfoPath, $BuildInfoJsonText, $Utf8NoBom)

    # ---- Step 6: summary ----
    Write-Step '===== SUMMARY ====='
    Write-Step ('APK: ' + $DestApk)
    Write-Step ('Size: ' + $ApkSizeBytes + ' bytes')
    Write-Step ('SHA256: ' + $Sha256)
    Write-Step ('versionName: ' + $VersionName + '  versionCode: ' + $VersionCode)
    Write-Step ('applicationId: ' + $ApplicationId)
    Write-Step ('OWNER_LICENSING_BASE_URL: ' + $ActualLicensingUrl)
    Write-Step ('OWNER_SYNC_BASE_URL: ' + $ActualSyncUrl)
    Write-Step ('Trust anchor key_id: ' + $SrcKeyId + ' (matched inside APK)')
    Write-Step ('git commit: ' + $GitCommit)
    Write-Step ('build-info.json: ' + $BuildInfoPath)

    return 0
}

# Main's pipeline output must be exactly one [int] (see Write-Step's comment).
# Anything else is echoed so it is not lost, and the run fails closed.
$__mainOutput = @(Main)
$__exitCode = 1
if ($__mainOutput.Count -gt 0 -and $__mainOutput[-1] -is [int]) {
    $__exitCode = $__mainOutput[-1]
    if ($__mainOutput.Count -gt 1) {
        foreach ($__stray in $__mainOutput[0..($__mainOutput.Count - 2)]) { Write-Host ('[build_lab_apk] (stray output) ' + $__stray) }
    }
} else {
    foreach ($__stray in $__mainOutput) { Write-Host ('[build_lab_apk] (stray output) ' + $__stray) }
    Write-Host '[build_lab_apk] FAILED: internal error -- Main did not return an integer exit code.'
}
Write-Host ('SCRIPT_DONE exit=' + $__exitCode)
exit $__exitCode
