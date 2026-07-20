# Windows Code-Signing Guide

## Current status (Wave 1B)
**No trusted Authenticode certificate is available.** Confirmed with the project owner before this wave proceeded. All Windows artifacts produced this wave (`AuraRetail.exe`, `AuraClinic.exe`, both installers) are **UNSIGNED FOR PRODUCTION** and must not be distributed to real customers as-is. This is an explicit release-gate blocker for Wave 1C / first paid pilot, not a task silently deferred.

## What's already prepared
- `products/sign_windows_release.ps1` — a ready-to-run signing script. Takes a target file path, reads the certificate location and password from environment variables (never from the command line, never logged), calls `signtool.exe sign` with SHA-256 file/timestamp digests and an RFC 3161 timestamp server, then immediately runs `signtool.exe verify /pa /v` and fails loudly if verification doesn't pass.
- `keystore.properties.example`-equivalent pattern: the script's `$env:AURA_SIGN_PFX_PATH` / `$env:AURA_SIGN_PFX_PASSWORD` convention mirrors the Android signing setup (Part I) for consistency.

## What's needed before this can run for real
1. **A certificate.** Options, roughly cheapest/simplest to most trusted:
   - A standard OV (Organization Validation) Authenticode code-signing certificate from a public CA (DigiCert, Sectigo, SSL.com, etc.) — typically ~$100-400/year, requires business identity verification (a few business days).
   - An EV (Extended Validation) certificate — higher trust (immediate SmartScreen reputation instead of the "unknown publisher" warning this wave's testing hit), but requires a hardware token (USB HSM) and is more expensive/slower to acquire.
2. **The Windows SDK** (for `signtool.exe`) — not installed on this build machine as of this wave. Available as a standalone download or via Visual Studio Build Tools.
3. **Secure storage for the `.pfx` and its password** — outside Git, ideally in a password manager or a hardware token for EV. Never commit either.

## SmartScreen reputation
Even a valid OV certificate does not grant instant SmartScreen trust — Microsoft builds reputation over time/download volume for a given signed binary. An EV certificate grants immediate reputation. This wave's testing (see `docs/release/wave1b/retail-windows-installer-report.md`) directly hit the unsigned "Windows protected your PC" prompt; an OV-signed but low-download-volume binary may still occasionally show a milder SmartScreen warning until reputation builds, which is expected CA-ecosystem behavior, not a signing failure.

## Once a certificate exists
```powershell
$env:AURA_SIGN_PFX_PATH = "C:\secure\path\aura-codesign.pfx"
$env:AURA_SIGN_PFX_PASSWORD = "<from your password manager>"
.\products\sign_windows_release.ps1 -Target dist\AuraRetail\AuraRetail.exe
.\products\sign_windows_release.ps1 -Target dist\AuraClinic\AuraClinic.exe
# Then rebuild the installers (they embed the exe) and sign those too:
.\products\sign_windows_release.ps1 -Target dist\installers\AuraRetail-Setup-<version>.exe
.\products\sign_windows_release.ps1 -Target dist\installers\AuraClinic-Setup-<version>.exe
```

Record, per signed artifact: certificate subject (not the private key), thumbprint, signing timestamp, and the file's SHA-256 both before and after signing (before/after should differ, since signing modifies the file — that's expected and is what `signtool verify` confirms is still a valid, intact signature).

## Never commit
`.pfx` file, `.pfx` password, any exported private key material, or a `.p12`/`.cer` that includes the private key.
