# build_desktop_installer.ps1
#
# Builds a release-ready Aura Retail Windows installer with the Owner
# licensing URL BAKED IN at build time -- the desktop counterpart of
# android/aura-retail/app/build.gradle's `ownerLicensingBaseUrl` -P property
# (-> BuildConfig.OWNER_LICENSING_BASE_URL). An installed .exe launched from
# the Start menu has no AURA_OWNER_LICENSING_URL environment variable and no
# screen to set one, so without this it can never activate. See
# products/retail/backend/config.py's OWNER_LICENSING_BASE_URL comment block
# (_resolve_owner_licensing_base_url) for the full precedence rule this
# writes into: an operator's own AURA_OWNER_LICENSING_URL still overrides
# whatever this script bakes in.
#
# Usage (from anywhere; PSScriptRoot resolves the repo root):
#   pwsh scripts/release/build_desktop_installer.ps1 `
#       -OwnerLicensingUrl https://owner.actionaura.example/api/licensing/v1
#
#   # Prove the bundle without running Inno Setup (used by the test suite):
#   pwsh scripts/release/build_desktop_installer.ps1 `
#       -OwnerLicensingUrl https://owner.example.invalid/api/licensing/v1 `
#       -OutDir C:/temp/release_proof -AllowDirty -SkipInstaller
#
# Steps: (a) verify a clean git tree and record HEAD, (b) require and parse
# commercial_runtime/licensing_contracts/trust_anchor.json, (c) validate the
# requested URL with the same rule config.py enforces at runtime, (d) write
# build/release/owner_endpoint.json, (e) build with PyInstaller
# (AURA_RELEASE_BUILD=1, which makes the spec fail closed if either input
# file is missing; dist/ and build/ always anchored at the repo root, never
# the caller's working directory) and verify the baked values landed at the
# bundle root EXACTLY, (f) run Inno Setup, (g) copy the installer out and record
# desktop-build-info.json, (h) ALWAYS (success or failure) delete the staged
# owner_endpoint.json and clear AURA_RELEASE_BUILD, so a baked URL can never
# leak into a later ordinary (non-release) build.
#
# This script never writes into products/retail itself and never edits
# tracked source -- the only file it writes outside -OutDir is the
# gitignored build/release/owner_endpoint.json, and that is always deleted
# again before the script exits, success or failure.
#
# Compatible with both Windows PowerShell 5.1 and PowerShell 7 (pwsh):
# no '&&', no ternary '?:', no null-coalescing '??'. ASCII only.

param(
    [Parameter(Mandatory = $true)]
    [string]$OwnerLicensingUrl,
    [string]$OutDir = 'C:/Users/MSI/AuraLab/release',
    [string]$Python = 'C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe',
    [string]$Iscc = 'C:/Users/MSI/AppData/Local/Programs/Inno Setup 6/ISCC.exe',
    [switch]$AllowDirty,
    [switch]$SkipInstaller
)

$ErrorActionPreference = 'Continue'

# See build_lab_apk.ps1's identical guard for why this is needed at all:
# PowerShell 7+ can turn a native command's non-zero exit into a terminating
# error depending on host configuration, and this script's own manual
# $LASTEXITCODE checks must always be the ones that decide pass/fail. The
# variable does not exist in Windows PowerShell 5.1, hence the guard.
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue) {
    $global:PSNativeCommandUseErrorActionPreference = $false
}

# Write-Host, NOT Write-Output -- verifier fix (2026-09-24). These used to be
# Write-Output, which puts every log line on Main's PIPELINE, so
# `$__exitCode = Main` at the bottom captured the whole log plus the return
# code as one array, and `exit <array>` exits 0 -- measured on BOTH Windows
# PowerShell 5.1 and pwsh 7.6: "FAILED: ... mismatch ... 13" printed and the
# process exit code was 0, so every failure was a silent success to anything
# keyed on the exit code. (It also meant nothing was printed until the whole
# multi-minute build had finished.) Write-Host goes to the host, streams live,
# and still reaches a redirected stdout. Nothing inside Main may write to the
# pipeline except its final `return <int>`; the call site at the bottom
# fails closed if anything else ever does.
function Write-Step {
    param([string]$Message)
    Write-Host ('[build_desktop_installer] ' + $Message)
}

function Write-Fail {
    param([string]$Message)
    Write-Host ('[build_desktop_installer] FAILED: ' + $Message)
}

function Test-OwnerLicensingUrlValue {
    # Mirrors config.py's _validate_baked_owner_licensing_url() exactly:
    # https:// with any host, or http:// restricted to a loopback host;
    # never embedded userinfo, a query string, or a fragment. Returns ''
    # when acceptable, or a human-readable problem string.
    param([string]$Url)

    # Whitespace anywhere is rejected on the RAW string, before System.Uri
    # (which would silently trim or percent-escape it) -- same rule and same
    # reason as config.py. Verifier finding: 'https://.../licensing v1' used
    # to pass here, get baked, and be "verified: EXACT match".
    if ($Url -match '\s') {
        return ('owner licensing URL [' + $Url + '] must not contain whitespace.')
    }

    $ParsedUri = $null
    try {
        $ParsedUri = New-Object System.Uri($Url)
    } catch {
        return ('owner licensing URL [' + $Url + '] is not a parsable URL: ' + $_.Exception.Message)
    }

    $Scheme = $ParsedUri.Scheme.ToLowerInvariant()
    $LoopbackHosts = @('127.0.0.1', 'localhost', '::1')

    # The host compared against $LoopbackHosts is taken from the RAW
    # authority text, the way Python's urlsplit().hostname does it (brackets
    # stripped, port stripped, lowercased) -- NOT System.Uri.Host. Verifier
    # finding: .Host is '[::1]' on pwsh and
    # '[0000:0000:0000:0000:0000:0000:0000:0001]' on Windows PowerShell 5.1,
    # so http://[::1]:5551/... was rejected here while config.py accepts it.
    # System.Uri above still does the actual parse validation.
    $Authority = ($Url -replace '^[A-Za-z][A-Za-z0-9+.-]*://', '') -replace '[/?#].*$', ''
    if ($Authority.StartsWith('[')) {
        $UrlHost = $Authority -replace '^\[([^\]]*)\].*$', '$1'
    } else {
        $UrlHost = $Authority -replace ':[^:]*$', ''
    }
    $UrlHost = $UrlHost.ToLowerInvariant()

    if ($Scheme -ne 'http' -and $Scheme -ne 'https') {
        return ('owner licensing URL [' + $Url + '] must use https:// (or http:// for a loopback host); got scheme [' + $Scheme + ']')
    }
    if ([string]::IsNullOrEmpty($UrlHost) -or [string]::IsNullOrEmpty($ParsedUri.DnsSafeHost)) {
        return ('owner licensing URL [' + $Url + '] has no host.')
    }
    if ($Scheme -eq 'http' -and -not ($LoopbackHosts -contains $UrlHost)) {
        return ('owner licensing URL [' + $Url + '] uses cleartext http:// against non-loopback host [' + $UrlHost + ']; use https:// or a loopback host (127.0.0.1/localhost/::1).')
    }
    if (-not [string]::IsNullOrEmpty($ParsedUri.UserInfo)) {
        return ('owner licensing URL [' + $Url + '] must not embed credentials.')
    }
    if (-not [string]::IsNullOrEmpty($ParsedUri.Query)) {
        return ('owner licensing URL [' + $Url + '] must not embed a query string.')
    }
    if (-not [string]::IsNullOrEmpty($ParsedUri.Fragment)) {
        return ('owner licensing URL [' + $Url + '] must not embed a URL fragment.')
    }
    return ''
}

function Main {
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    # ---- Step a: repo root, git HEAD, clean-tree check ----
    $ScriptRoot = $PSScriptRoot
    if ([string]::IsNullOrEmpty($ScriptRoot)) {
        $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    $RepoRoot = (Resolve-Path (Join-Path $ScriptRoot '..\..')).Path
    Write-Step ('Repo root: ' + $RepoRoot)

    # Verifier fix (2026-09-24): a git command that FAILS is a failure, not a
    # clean tree. The unfixed script fell through to "Working tree is clean."
    # whenever `git status` errored (measured with GIT_DIR pointed at nothing:
    # it logged "git HEAD: UNKNOWN", "Working tree is clean.", then built and
    # recorded a release whose tree state was never known). -AllowDirty does
    # not cover this: it is consent to a KNOWN-dirty tree, not to an unknown one.
    $GitHead = ''
    $GitHeadOutput = $null
    try {
        $GitHeadOutput = & git -C $RepoRoot rev-parse HEAD 2>&1
        $GitHeadExit = $LASTEXITCODE
    } catch {
        $GitHeadExit = -1
    }
    if ($GitHeadExit -ne 0) {
        Write-Fail ('git rev-parse HEAD failed in ' + $RepoRoot + ' (exit ' + $GitHeadExit + '): ' + (@($GitHeadOutput) -join ' ') + ' -- a release build must record the commit it was built from.')
        return 1
    }
    $GitHead = (@($GitHeadOutput) | Select-Object -First 1).ToString().Trim()
    Write-Step ('git HEAD: ' + $GitHead)

    $Dirty = $false
    $GitStatusOutput = & git -C $RepoRoot status --porcelain 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Fail ('git status --porcelain failed in ' + $RepoRoot + ' (exit ' + $LASTEXITCODE + '): ' + (@($GitStatusOutput) -join ' ') + ' -- cannot tell whether the tree is clean, refusing.')
        return 1
    }
    if ($null -ne $GitStatusOutput -and @($GitStatusOutput).Count -gt 0) {
        if ($AllowDirty) {
            Write-Step 'WARNING: working tree is DIRTY (uncommitted changes present). -AllowDirty was passed, continuing anyway -- the resulting build/installer does NOT correspond to a clean, reproducible commit.'
            $Dirty = $true
        } else {
            Write-Fail 'Working tree has uncommitted changes. Commit or stash first, or pass -AllowDirty to override deliberately.'
            return 1
        }
    } else {
        Write-Step 'Working tree is clean.'
    }

    # ---- Step b: require and parse trust_anchor.json ----
    $TrustAnchorPath = Join-Path $RepoRoot 'commercial_runtime\licensing_contracts\trust_anchor.json'
    if (-not (Test-Path $TrustAnchorPath)) {
        Write-Fail ('trust_anchor.json not found at ' + $TrustAnchorPath + ' -- generate it first with scripts/generate_trust_anchor.py against the real Owner instance this build targets.')
        return 2
    }
    $TrustAnchorJson = $null
    try {
        $TrustAnchorJson = (Get-Content -Path $TrustAnchorPath -Raw) | ConvertFrom-Json
    } catch {
        Write-Fail ('trust_anchor.json at ' + $TrustAnchorPath + ' could not be parsed as JSON: ' + $_.Exception.Message)
        return 2
    }
    if ($null -eq $TrustAnchorJson.keys -or @($TrustAnchorJson.keys).Count -eq 0) {
        Write-Fail ('trust_anchor.json at ' + $TrustAnchorPath + ' has no keys.')
        return 2
    }
    $TrustAnchorKeyIds = New-Object System.Collections.Generic.List[string]
    foreach ($k in @($TrustAnchorJson.keys)) {
        if ([string]::IsNullOrEmpty($k.key_id)) {
            Write-Fail 'trust_anchor.json has a key entry with no key_id.'
            return 2
        }
        $TrustAnchorKeyIds.Add([string]$k.key_id)
    }
    Write-Step ('trust_anchor.json key_id(s): ' + ($TrustAnchorKeyIds -join ', '))

    # ---- Step c: validate the requested URL ----
    $UrlProblem = Test-OwnerLicensingUrlValue -Url $OwnerLicensingUrl
    if (-not [string]::IsNullOrEmpty($UrlProblem)) {
        Write-Fail $UrlProblem
        return 3
    }
    Write-Step ('OwnerLicensingUrl validated: ' + $OwnerLicensingUrl)

    # ---- Step d: write build/release/owner_endpoint.json ----
    $ReleaseDir = Join-Path $RepoRoot 'build\release'
    if (-not (Test-Path $ReleaseDir)) {
        New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
    }
    $OwnerEndpointPath = Join-Path $ReleaseDir 'owner_endpoint.json'

    # Hand-built JSON text (not ConvertTo-Json) so the exact bytes on disk
    # are unambiguous and match what config.py's json.loads() will read back
    # byte-for-byte -- same escaping discipline as build.gradle's
    # asJavaStringLiteral for build-time secrets: a URL should never contain
    # a double quote or backslash, but this is cheap insurance regardless.
    $EscapedUrl = $OwnerLicensingUrl.Replace('\', '\\').Replace('"', '\"')
    $EscapedCommit = $GitHead.Replace('\', '\\').Replace('"', '\"')
    $OwnerEndpointJsonText = '{"owner_licensing_base_url": "' + $EscapedUrl + '", "written_by": "scripts/release/build_desktop_installer.ps1", "git_commit": "' + $EscapedCommit + '"}'

    if (-not (Test-Path $OutDir)) {
        New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    }
    $OutDirResolved = (Resolve-Path $OutDir -ErrorAction SilentlyContinue).Path
    if ([string]::IsNullOrEmpty($OutDirResolved)) {
        Write-Fail ('OutDir ' + $OutDir + ' could not be created or resolved.')
        return 4
    }

    $ExitCode = 1
    try {
        # The staged file is written INSIDE the try (verifier fix) so that
        # nothing between writing it and entering the try can leave it behind
        # for a later ordinary build to bake in by accident.
        [System.IO.File]::WriteAllText($OwnerEndpointPath, $OwnerEndpointJsonText, $Utf8NoBom)
        Write-Step ('Wrote ' + $OwnerEndpointPath)

        # ---- Step e: PyInstaller, with AURA_RELEASE_BUILD=1 ----
        if (-not (Test-Path $Python)) {
            Write-Fail ('Python not found at ' + $Python)
            return 10
        }
        $SpecPath = Join-Path $RepoRoot 'products\retail\packaging\aura_retail.spec'
        if (-not (Test-Path $SpecPath)) {
            Write-Fail ('Spec file not found at ' + $SpecPath)
            return 10
        }

        $env:AURA_RELEASE_BUILD = '1'
        Write-Step 'AURA_RELEASE_BUILD=1 set for this build (the spec now fails closed if trust_anchor.json or owner_endpoint.json is missing).'

        # Verifier fix (2026-09-24): dist/ and build/ are passed EXPLICITLY,
        # anchored at the repo root. PyInstaller's defaults are ./dist and
        # ./build relative to the CALLER'S working directory, so the unfixed
        # script run from anywhere but the repo root (its own usage line says
        # "from anywhere") built the fresh bundle into that directory and
        # then verified -- and step f would have packaged -- whatever stale
        # bundle already sat in <repo>/dist/AuraRetail. Measured: a stale
        # bundle marked written_by=STALE-FROM-AN-EARLIER-BUILD was reported
        # "verified: EXACT match". For the same reason the old bundle is
        # removed first: nothing left over from an earlier build may ever be
        # what gets verified.
        $DistRoot = Join-Path $RepoRoot 'dist'
        $WorkRoot = Join-Path $RepoRoot 'build'
        $DistDir = Join-Path $DistRoot 'AuraRetail'
        if (Test-Path $DistDir) {
            Remove-Item -Path $DistDir -Recurse -Force -ErrorAction SilentlyContinue
            if (Test-Path $DistDir) {
                Write-Fail ('Could not remove the previous bundle at ' + $DistDir + ' (is Aura Retail running from it?). Refusing to risk verifying a stale bundle.')
                return 11
            }
        }

        $PyInstallerLog = Join-Path $OutDirResolved 'pyinstaller.log'
        Write-Step ('Running: ' + $Python + ' -m PyInstaller ' + $SpecPath + ' --noconfirm --distpath ' + $DistRoot + ' --workpath ' + $WorkRoot)
        Write-Step ('PyInstaller log: ' + $PyInstallerLog)

        $PyInstallerOutputLines = & $Python -m PyInstaller $SpecPath --noconfirm --distpath $DistRoot --workpath $WorkRoot 2>&1
        $PyInstallerExitCode = $LASTEXITCODE
        $PyInstallerTextLines = @($PyInstallerOutputLines | ForEach-Object { $_.ToString() })
        [System.IO.File]::WriteAllLines($PyInstallerLog, $PyInstallerTextLines, $Utf8NoBom)

        if ($PyInstallerExitCode -ne 0) {
            Write-Step '---- last 60 lines of pyinstaller.log ----'
            $TailLines = Get-Content -Path $PyInstallerLog -Tail 60 -ErrorAction SilentlyContinue
            foreach ($line in $TailLines) { Write-Host $line }
            Write-Fail ('PyInstaller exited with code ' + $PyInstallerExitCode)
            return 11
        }
        Write-Step 'PyInstaller build succeeded (exit code 0).'

        # ---- verify the baked bundle (still step e) ----
        if (-not (Test-Path $DistDir)) {
            Write-Fail ('dist bundle not found at ' + $DistDir)
            return 12
        }

        # The bundle ROOT is the directory the frozen app sees as
        # sys._MEIPASS (= config.py's BASE_DIR): <dist>\_internal on
        # PyInstaller 6+, the exe's own directory on older layouts -- the one
        # holding base_library.zip. The baked files are checked THERE, at the
        # exact paths config.py and the licensing package read, not "anywhere
        # under dist" (verifier fix: a recursive search would have accepted a
        # copy bundled into the wrong directory, which the app never reads).
        $BundleRoot = $null
        foreach ($Candidate in @((Join-Path $DistDir '_internal'), $DistDir)) {
            if ($null -eq $BundleRoot -and (Test-Path (Join-Path $Candidate 'base_library.zip'))) {
                $BundleRoot = $Candidate
            }
        }
        if ($null -eq $BundleRoot) {
            Write-Fail ('Could not find the bundle root (the directory holding base_library.zip) under ' + $DistDir)
            return 12
        }
        Write-Step ('Bundle root (sys._MEIPASS): ' + $BundleRoot)

        $BundleOwnerEndpointFile = Join-Path $BundleRoot 'owner_endpoint.json'
        if (-not (Test-Path $BundleOwnerEndpointFile)) {
            Write-Fail ('owner_endpoint.json not found at the bundle root: ' + $BundleOwnerEndpointFile)
            return 12
        }
        Write-Step ('Baked owner_endpoint.json found at: ' + $BundleOwnerEndpointFile)
        $BundleOwnerEndpointJson = [System.IO.File]::ReadAllText($BundleOwnerEndpointFile) | ConvertFrom-Json
        $BundleOwnerUrl = [string]$BundleOwnerEndpointJson.owner_licensing_base_url
        if ($BundleOwnerUrl -cne $OwnerLicensingUrl) {
            Write-Fail ('Baked owner_licensing_base_url mismatch: expected [' + $OwnerLicensingUrl + '] got [' + $BundleOwnerUrl + ']')
            return 13
        }
        Write-Step ('Baked owner_licensing_base_url verified: EXACT match (' + $BundleOwnerUrl + ')')

        $BundleTrustAnchorFile = Join-Path $BundleRoot 'commercial_runtime\licensing_contracts\trust_anchor.json'
        if (-not (Test-Path $BundleTrustAnchorFile)) {
            Write-Fail ('trust_anchor.json not found where the licensing package reads it: ' + $BundleTrustAnchorFile)
            return 14
        }
        Write-Step ('Baked trust_anchor.json found at: ' + $BundleTrustAnchorFile)
        $BundleTrustAnchorJson = [System.IO.File]::ReadAllText($BundleTrustAnchorFile) | ConvertFrom-Json
        $BundleKeyIds = New-Object System.Collections.Generic.List[string]
        foreach ($k in @($BundleTrustAnchorJson.keys)) { $BundleKeyIds.Add([string]$k.key_id) }
        $ExpectedKeyIdsSorted = @($TrustAnchorKeyIds) | Sort-Object
        $BundleKeyIdsSorted = @($BundleKeyIds) | Sort-Object
        $KeyIdsDiffer = $false
        if (@($ExpectedKeyIdsSorted).Count -ne @($BundleKeyIdsSorted).Count) {
            $KeyIdsDiffer = $true
        } else {
            for ($i = 0; $i -lt @($ExpectedKeyIdsSorted).Count; $i++) {
                if (@($ExpectedKeyIdsSorted)[$i] -cne @($BundleKeyIdsSorted)[$i]) { $KeyIdsDiffer = $true }
            }
        }
        if ($KeyIdsDiffer) {
            Write-Fail ('Baked trust_anchor.json key ids differ from source: expected [' + ($ExpectedKeyIdsSorted -join ', ') + '] got [' + ($BundleKeyIdsSorted -join ', ') + ']')
            return 14
        }
        Write-Step ('Baked trust_anchor.json key id(s) verified: EXACT match (' + ($BundleKeyIdsSorted -join ', ') + ')')

        if ($SkipInstaller) {
            Write-Step 'SkipInstaller requested -- stopping after the dist bundle verification (step e). No installer was built.'
            $ExitCode = 0
        } else {
            # ---- Step f: Inno Setup ----
            if (-not (Test-Path $Iscc)) {
                Write-Fail ('ISCC.exe not found at ' + $Iscc)
                return 20
            }
            $IssPath = Join-Path $RepoRoot 'products\retail\packaging\aura_retail_setup.iss'
            if (-not (Test-Path $IssPath)) {
                Write-Fail ('Inno Setup script not found at ' + $IssPath)
                return 20
            }
            $IsccLog = Join-Path $OutDirResolved 'iscc.log'
            Write-Step ('Running: ' + $Iscc + ' ' + $IssPath)
            $IsccOutputLines = & $Iscc $IssPath 2>&1
            $IsccExitCode = $LASTEXITCODE
            $IsccTextLines = @($IsccOutputLines | ForEach-Object { $_.ToString() })
            [System.IO.File]::WriteAllLines($IsccLog, $IsccTextLines, $Utf8NoBom)
            if ($IsccExitCode -ne 0) {
                Write-Step '---- last 60 lines of iscc.log ----'
                $TailLines = Get-Content -Path $IsccLog -Tail 60 -ErrorAction SilentlyContinue
                foreach ($line in $TailLines) { Write-Host $line }
                Write-Fail ('ISCC exited with code ' + $IsccExitCode)
                return 21
            }
            Write-Step 'ISCC build succeeded (exit code 0).'

            # Version comes from the .iss file's own #define AppVersion, read
            # back rather than retyped -- same reasoning
            # retail_site_relay_config_test.py gives for reading app.py's
            # PORT default out of app.py rather than copying it as a literal.
            $IssText = Get-Content -Path $IssPath -Raw
            $VersionMatch = [regex]::Match($IssText, '#define\s+AppVersion\s+"([^"]+)"')
            if (-not $VersionMatch.Success) {
                Write-Fail ('Could not read #define AppVersion out of ' + $IssPath)
                return 22
            }
            $Version = $VersionMatch.Groups[1].Value
            $InstallerPath = Join-Path $RepoRoot ('dist\installers\AuraRetail-Setup-' + $Version + '.exe')
            if (-not (Test-Path $InstallerPath)) {
                Write-Fail ('Installer not found at ' + $InstallerPath)
                return 22
            }
            Write-Step ('Installer built at: ' + $InstallerPath)

            # ---- Step g: copy + sha256 + desktop-build-info.json ----
            $DestInstaller = Join-Path $OutDirResolved (Split-Path -Leaf $InstallerPath)
            Copy-Item -Path $InstallerPath -Destination $DestInstaller -Force

            # Deliberately .NET SHA256, not Get-FileHash -- see
            # build_lab_apk.ps1's identical comment: Get-FileHash has been
            # observed to throw CommandNotFoundException under plain
            # Windows PowerShell 5.1 on this machine even though
            # ConvertFrom-Json/ConvertTo-Json from the same module load
            # fine, so it cannot be trusted to be present.
            $Sha256Provider = [System.Security.Cryptography.SHA256]::Create()
            $InstallerStream = [System.IO.File]::OpenRead($DestInstaller)
            try {
                $HashBytes = $Sha256Provider.ComputeHash($InstallerStream)
            } finally {
                $InstallerStream.Dispose()
                $Sha256Provider.Dispose()
            }
            $HashHexParts = New-Object System.Collections.Generic.List[string]
            foreach ($b in $HashBytes) { $HashHexParts.Add($b.ToString('x2')) }
            $InstallerSha256 = [string]::Join('', $HashHexParts)
            if ($InstallerSha256 -notmatch '^[0-9a-f]{64}$') {
                Write-Fail ('Computed SHA256 does not look like a valid digest: [' + $InstallerSha256 + ']')
                return 23
            }
            Write-Step ('Installer SHA256: ' + $InstallerSha256)

            $BuiltAtUtc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
            # Key names exactly as the design specifies them (snake_case,
            # matching owner_endpoint.json's own keys) -- verifier fix: they
            # had been written camelCase.
            $BuildInfo = [ordered]@{
                version                  = $Version
                git_commit               = $GitHead
                dirty                    = $Dirty
                owner_licensing_base_url = $OwnerLicensingUrl
                trust_anchor_key_ids     = @($BundleKeyIdsSorted)
                installer_file           = (Split-Path -Leaf $DestInstaller)
                installer_sha256         = $InstallerSha256
                built_at                 = $BuiltAtUtc
            }
            $BuildInfoPath = Join-Path $OutDirResolved 'desktop-build-info.json'
            $BuildInfoJsonText = $BuildInfo | ConvertTo-Json -Depth 5
            [System.IO.File]::WriteAllText($BuildInfoPath, $BuildInfoJsonText, $Utf8NoBom)
            Write-Step ('Wrote ' + $BuildInfoPath)

            Write-Step '===== SUMMARY ====='
            Write-Step ('Installer: ' + $DestInstaller)
            Write-Step ('SHA256: ' + $InstallerSha256)
            Write-Step ('Version: ' + $Version)
            Write-Step ('git commit: ' + $GitHead + '  dirty: ' + $Dirty)
            Write-Step ('OwnerLicensingUrl: ' + $OwnerLicensingUrl)
            Write-Step ('trust_anchor key id(s): ' + ($BundleKeyIdsSorted -join ', '))
            Write-Step ('desktop-build-info.json: ' + $BuildInfoPath)

            $ExitCode = 0
        }
    } finally {
        # ---- Step h: ALWAYS clean up -- a stale baked URL must never leak
        # into a later ordinary (non-release) build, regardless of whether
        # this run succeeded, failed, or was interrupted. ----
        if (Test-Path $OwnerEndpointPath) {
            Remove-Item -Path $OwnerEndpointPath -Force -ErrorAction SilentlyContinue
            Write-Step ('Cleaned up ' + $OwnerEndpointPath)
        }
        if (Test-Path Env:AURA_RELEASE_BUILD) {
            Remove-Item Env:AURA_RELEASE_BUILD -ErrorAction SilentlyContinue
            Write-Step 'Cleared AURA_RELEASE_BUILD from this process environment.'
        }
    }

    return $ExitCode
}

# Main's pipeline output must be exactly one [int] (see Write-Step's comment).
# Anything else is echoed so it is not lost, and the run fails closed.
$__mainOutput = @(Main)
$__exitCode = 1
if ($__mainOutput.Count -gt 0 -and $__mainOutput[-1] -is [int]) {
    $__exitCode = $__mainOutput[-1]
    if ($__mainOutput.Count -gt 1) {
        foreach ($__stray in $__mainOutput[0..($__mainOutput.Count - 2)]) { Write-Host ('[build_desktop_installer] (stray output) ' + $__stray) }
    }
} else {
    foreach ($__stray in $__mainOutput) { Write-Host ('[build_desktop_installer] (stray output) ' + $__stray) }
    Write-Host '[build_desktop_installer] FAILED: internal error -- Main did not return an integer exit code.'
}
Write-Host ('SCRIPT_DONE exit=' + $__exitCode)
exit $__exitCode
