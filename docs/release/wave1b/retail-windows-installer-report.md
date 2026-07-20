# Wave 1B — Retail Windows Installer Report

## Build
`products/retail/packaging/aura_retail_setup.iss` (Inno Setup 6.7.3). Fixed AppId `{A039EDA8-410E-4400-B3A5-A7CD4AF7F437}` (never to change — this is Inno Setup's upgrade key). Installs to `{autopf}\Action Aura\Aura Retail` (per-user `%LOCALAPPDATA%\Programs\Action Aura\Aura Retail` when run non-admin, as in all testing below); business data lives at `%LOCALAPPDATA%\AuraRetail` (unchanged, pre-existing app-level resolution in `launcher_retail.py` — the installer never touches it). Output: `dist/installers/AuraRetail-Setup-1.0.0-rc.1.exe` (unsigned, no cert this wave).

## Real device — encountered and resolved blocker
Windows SmartScreen ("Windows protected your PC") blocked the first two silent-install attempts outright — an unsigned, freshly-built executable with no reputation, expected. Resolved with a single manual "More info → Run anyway" click by the user; every subsequent run of that same binary proceeded without re-prompting. Documented, not hidden: **this installer cannot currently pass a fully unattended silent install on a machine that has never seen it before** — closing this requires real code signing (Part H), tracked as a residual risk.

## Real safety defect found and fixed during this testing
`/SUPPRESSMSGBOXES` (used by any scripted/managed uninstall) auto-answers `MsgBox` calls with the default button — "Yes" for an unqualified `MB_YESNO`. The original `[Code]` script's opt-in "delete my data" confirmation would have been **auto-confirmed** under a silent uninstall, deleting business data with zero human involvement. Fixed by adding `if UninstallSilent() then Exit;` at the top of `InitializeUninstall` — a silent uninstall now never even shows the prompt, always defaulting to preserve. Re-verified after the fix (see below).

## Test sequence actually executed (real installer, real files, synthetic data only)
1. **Clean install** (`/CURRENTUSER /VERYSILENT`): succeeded. `%LOCALAPPDATA%\Programs\Action Aura\Aura Retail\AuraRetail.exe` created, Start Menu + Desktop shortcuts created, `HKCU\...\Uninstall\{A039...}` registry entry created with correct `DisplayName`/`DisplayVersion`.
2. **First launch**: app auto-started post-install, `/api/health` → `200`, `/api/onboarding/status` → `needs_setup: true` (no demo data pre-seeded, confirmed).
3. **Onboarding + synthetic data**: created admin `win@test.local`, product "Windows Test Product" ($50), and a real sale (`SALE-000001`, $100.00, idempotency key `win-installer-test-1`).
4. **Upgrade**: rebuilt the installer with a bumped `AppVersion` (1.0.0-rc.2, same AppId) and silent-installed over the running-then-closed rc.1 install. Registry `DisplayVersion` correctly updated to `1.0.0-rc.2`; **zero files touched under `%LOCALAPPDATA%\AuraRetail`**. Post-upgrade: same login worked, `SALE-000001` intact with identical totals.
5. **Repair/reinstall**: reinstalled the same version over itself. Data intact, login unchanged.
6. **Uninstall (silent, post-fix)**: `%LOCALAPPDATA%\Programs\Action Aura\Aura Retail` fully removed ("Removed all? Yes"), registry uninstall key removed. `%LOCALAPPDATA%\AuraRetail\database\subsystems\retail.db` **confirmed still present** — preserved by construction (the installer's `[Files]` section only ever wrote into `{app}`; it never had anything to clean up here except via the now-guarded opt-in step).
7. **Reinstall after uninstall**: `needs_setup: false` (onboarding correctly did not rerun), same admin login succeeded — data fully returned.
8. **Explicit delete-data path**: verified by source/logic review, not a live interactive run (see limitation below) — requires two sequential `IDYES` confirmations, only reachable in a genuinely interactive (non-silent) uninstall.

## Not independently tested
- The interactive "type-equivalent" delete-confirmation dialog itself was not click-tested live (would require a fully interactive, non-silent uninstall walkthrough); its logic was verified by reading the compiled `[Code]` section and confirming the `UninstallSilent()` guard makes the silent path provably never reach it.
- Only one Windows environment was available (the primary development machine) — no Windows Sandbox/VM/second user account cross-check this wave, per the spec's own allowed limitation-disclosure clause.
- No mutable database exists inside Program Files at any point (confirmed by inspecting `{app}` post-install — only the frozen PyInstaller `dist/AuraRetail` payload, no `.db` files).

## Result
PASS, with two real findings (SmartScreen blocks true unattended silent install pre-signing; the `/SUPPRESSMSGBOXES` auto-delete defect, found and fixed). Clean install, upgrade, repair, and uninstall/reinstall data preservation all verified against a **real, running installer** on this machine, not simulated.
