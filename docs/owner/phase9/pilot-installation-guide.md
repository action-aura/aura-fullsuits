# Phase 9 — Pilot Installation Guide (template, real content, no real artifact to install yet)

## Windows

1. Download `AuraRetail-Setup-1.0.0-rc.6.exe` (or Clinic equivalent) from the link the pilot owner
   provided. Verify the SHA-256 checksum shown on the download page matches the file you received
   before running it (`certutil -hashfile <file> SHA256` on Windows, or ask support for help).
2. Running it will likely show a Windows SmartScreen "Windows protected your PC" prompt — this is
   expected for a standard (non-EV) code-signing certificate, not a sign of a compromised installer.
   Click "More info" -> "Run anyway". If the publisher name shown does not say "Action Aura", **stop
   and contact support** — that would indicate a wrong/tampered file, not a normal SmartScreen prompt.
3. Follow the installer. On first launch, activate using the license key your support contact issued
   you (see `controlled-pilot-operating-model.md`'s manual license-issuance workflow).

## Android

1. Download the APK from the link provided (never from any other source).
2. Android will show "Install blocked" unless you allow installs from this specific source — go to
   Settings and allow it for this download only, not "allow from all sources" generally.
3. Install, open, activate with your issued license key.

## First check-in

Both platforms check in with Owner automatically after activation. If activation reports an error,
contact support with the exact error message shown — do not retry more than 2-3 times (repeated
failed attempts are visible to support as a real signal something is wrong, not noise to filter out).

## What pilot users should never be asked to do

Disable antivirus, disable Windows Defender SmartScreen system-wide, install from an untrusted
third-party mirror, or share their license key with anyone else. If asked to do any of these by
someone claiming to be support, treat it as suspicious and verify through the documented support
channel (`support-escalation-matrix.md`).
