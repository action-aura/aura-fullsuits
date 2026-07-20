# Wave 1B — Build Environment Report

Recorded before any Wave 1B code changes, per Part A.

## Repository state
- Branch: `master`
- Commit: `6c44fa8c0e15bfaaf27b72a6038a38f9c1ff5242`
- Tag verified: `android-wave1a-physical-device-validated` → same commit
- `git status`: clean, no untracked files

## Toolchain
- Python: 3.11.9
- PyInstaller: 6.21.0
- Java/JDK: OpenJDK 17.0.19 (Microsoft build)
- Gradle: 8.9 (via wrapper), Kotlin 1.9.23
- Android SDK: platform `android-34`, build-tools `34.0.0`
- `apksigner`: present (`build-tools/34.0.0/apksigner.bat`)
- `keytool`: present
- `bundletool` standalone jar: not found (not required — `bundleRelease` via Gradle already produces AABs directly, confirmed working in Wave 1A)

## Installer / signing tooling — initial state (before Wave 1B setup)
- Inno Setup: **not found** initially. Installed this wave via `winget install JRSoftware.InnoSetup` (v6.7.3), confirmed at `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`.
- WiX Toolset: not installed (Inno Setup chosen per user decision — simplest reliable path, matches spec's stated preference).
- `signtool.exe` / Windows SDK: not found. Per user decision, no real Authenticode certificate is available this wave — signing scripts/config prepared, no signtool invocation performed, all Windows artifacts marked UNSIGNED FOR PRODUCTION.

## Connected Android devices
None connected at the start of Wave 1B (`adb devices -l` returned empty). Device reconnection required before Part J (signed-upgrade test) and Part C's physical Arabic verification.

## User decisions recorded this wave
1. Inno Setup: install via winget now (done).
2. Windows code signing: no real certificate — prepare scripts/docs only, mark UNSIGNED.
3. Hardware: no physical scanner/printer available — build adapters + simulator, label NOT YET VERIFIED.
4. Android keystore location: dedicated folder outside the repo, `C:\Users\Dell\AuraSigningKeys\` (created this wave).
