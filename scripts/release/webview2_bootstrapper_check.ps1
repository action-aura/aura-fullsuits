# webview2_bootstrapper_check.ps1 -- dot-sourced by build_desktop_installer.ps1.
#
# Decides whether a file is really Microsoft's WebView2 Evergreen bootstrapper
# before the installer bundles it and RUNS it on a shop PC. "Signed by something
# Microsoft" is not enough (verifier L6: notepad.exe passed the old
# 'O=Microsoft Corporation' test, and so would any other Microsoft-signed exe a
# redirect or a swapped download might deliver). The check is therefore two-part:
#   1. a VALID Authenticode signature whose signer is CN=Microsoft Corporation
#      (the code-signing identity; Windows' own catalog-signed files are
#      CN=Microsoft Windows and are refused), AND
#   2. the file says what the bootstrapper says about itself in its version
#      resource: OriginalFilename MicrosoftEdgeUpdateSetup.exe and a product
#      name containing 'Edge Update' (measured on the real download 2026-10-12).
# If Microsoft ever renames those, the build stops with the reason printed and a
# human updates the expectation here -- failing closed is the point.
#
# Returns $null when the file is acceptable, otherwise a one-line reason.
function Test-WebView2Bootstrapper {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$ExpectedSignerCommonName = 'Microsoft Corporation',
        [string]$ExpectedOriginalFilename = 'MicrosoftEdgeUpdateSetup.exe',
        [string]$ExpectedProductNameFragment = 'Edge Update'
    )
    if (-not (Test-Path -LiteralPath $Path)) { return 'file not found' }
    $sig = Get-AuthenticodeSignature -FilePath $Path
    $subject = ''
    if ($sig.SignerCertificate) { $subject = $sig.SignerCertificate.Subject }
    if ($sig.Status -ne 'Valid') { return ('signature status is ' + $sig.Status + ', not Valid') }
    if ($subject -notmatch ('(^|, )CN=' + [regex]::Escape($ExpectedSignerCommonName) + '(,|$)')) {
        return ('signer is not CN=' + $ExpectedSignerCommonName + ' (subject [' + $subject + '])')
    }
    $info = (Get-Item -LiteralPath $Path).VersionInfo
    if ($info.OriginalFilename -ne $ExpectedOriginalFilename) {
        return ('original filename is [' + $info.OriginalFilename + '], expected [' + $ExpectedOriginalFilename + ']')
    }
    if (-not $info.ProductName -or $info.ProductName -notlike ('*' + $ExpectedProductNameFragment + '*')) {
        return ('product name is [' + $info.ProductName + '], expected it to contain [' + $ExpectedProductNameFragment + ']')
    }
    return $null
}
