# Android Release Checklist

Run through this per product (`aura-retail`, `aura-clinic`) before any real
distribution. Nothing on this list was executed as an actual production release in
Phase 4 — this phase produced debug/unsigned builds only (see the build reports).

- [ ] Real production keystore generated and stored securely (not in this repo) —
      see `signing-and-release-guide.md`.
- [ ] `keystore.properties` present locally with real values, verified `git status`
      shows it as untracked/ignored.
- [ ] `versionCode` incremented and `versionName` updated in `app/build.gradle`
      (currently `1` / `"1.0.0"` for both products — placeholders, not yet a real
      release number).
- [ ] `./gradlew clean testDebugUnitTest lintDebug assembleRelease` all pass —
      see the build report for this phase's baseline (0 tests exist in source; lint
      results recorded there).
- [ ] APK/AAB installed on at least one real device and the full smoke test (18
      steps retail / 19 steps clinic, see `device-testing-guide.md`) run end to end —
      **not done in this phase**, no device was available.
- [ ] Camera/barcode scanning verified on a real device (retail) — **not done in
      this phase**.
- [ ] Data persistence verified across an app restart and a full uninstall/reinstall
      (confirms `allowBackup="false"` doesn't unexpectedly restore stale data) —
      **not done in this phase**.
- [ ] Arabic/RTL verified visually on-device, not just in source — **not done in
      this phase**.
- [ ] `applicationId` (`com.actionaura.retail` / `com.actionaura.clinic`) confirmed
      not already taken on the target distribution channel.
- [ ] Release notes drafted; `versionCode` bump matches what was actually changed.
- [ ] No secrets (keystore, passwords, real tenant/license data) present anywhere in
      the built APK's assets (spot-check `assets/bundle/**` after
      `stageAuraAssets` — should only ever contain the product's `frontend/`
      i18n/locale files).
- [ ] Confirmed the release build was NOT produced with the debug signing key
      (`apksigner verify --print-certs` on the final APK, compare the fingerprint
      against the real production key, not `androiddebugkey`).

## Explicitly deferred to later phases (do not attempt from this checklist alone)

Owner Control Center enrollment, license issuance/activation, subscription
expiration enforcement, customer-specific package generation, payment integration,
production VPS/store deployment, automatic remote update execution, Aura Core
integration. `identity/CommercialIdentity.kt` provides the placeholder hooks these
future phases will use; it does not implement any of them.
