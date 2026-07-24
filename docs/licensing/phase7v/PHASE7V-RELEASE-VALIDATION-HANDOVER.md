# Phase 7V — Release Validation Handover

## What Phase 7 vs Phase 7V each mean

- Tag `aura-product-licensing-integration-phase7-complete` (commit `6ef6265`) represents
  **implementation completion**: the full Phase 7 licensing stack, live-tested via source runs.
- Phase 7V is **release-validation completion**: real signed/packaged artifacts, a real rc.1→rc.2
  upgrade, real toolchain closure, real artifact security inspection. It found and fixed 5 real
  defects (see below) that source-level testing alone could not have caught, since none of them
  exist until code is actually frozen/packaged/signed.

The Phase 7 tag is unchanged, not moved, not deleted, per the governing spec's explicit instruction.

## Real defects found and fixed this session

1. **Owner backup/restore tool discovery** — `pg_dump`/`pg_restore` not portably discoverable,
   5 real test failures. Fixed (`owner/app/system/backup.py`).
2. **Windows commercial-build TLS bypass** — a frozen `.exe` honored
   `AURA_OWNER_LICENSING_INSECURE=1` unconditionally, a real security gap. Fixed
   (`products/*/backend/config.py`), verified live against a self-signed-cert HTTPS server.
3. **Stale PyInstaller build cache** — crashed the Windows app at startup
   (`backports.zstd AttributeError`). Fixed via `--clean` rebuild; both frozen exes now start
   correctly.
4. **Missing trust-anchor bundling** — no commercial Windows build could ever verify a real Owner
   response; `trust_anchor.json` was never in either `.spec`'s `datas`. Fixed
   (`products/*/packaging/*.spec`), verified via a real live activation against a real Owner.
5. **Android `lintRelease` blocker** — `setIsStrongBoxBacked` (API 28) guarded only by `try/catch`,
   which lint's `NewApi` check rejects given `minSdk 26`. Fixed
   (`android/aura-*/.../licensing/DeviceIdentity.kt`), both products now build/sign successfully.

## What was proven live this session (not just unit-tested)

- Full activate → restart-persist → check-in → real-Owner-outage → live WARNING → live
  GRACE_PERIOD → deactivate lifecycle, on the **genuinely frozen, rebuilt** `AuraClinic.exe`,
  against a real Owner instance (real Postgres, real Ed25519 signing).
- Same activate → deactivate lifecycle plus the authoritative 100.00/20.00/10%→88.00 sale
  calculation, on the genuinely frozen `AuraRetail.exe`.
- Real rc.1→rc.2 Windows installer upgrade with real pre-existing data (Clinic), full SQLite
  integrity/row-count verification before and after, plus an uninstall/reinstall cycle.
- Real production-signed Android APK/AAB builds for both products, with certificate fingerprints
  matching rc.1 exactly (signing continuity).
- Owner's full suite (186/186) against real PostgreSQL with real backup/restore.
- The existing AUDIT-010 combined test runner (46 files, 585 tests, 0 failures).

## What remains genuinely unverified

- **Physical Android device validation** (Parts J–N of the governing spec): no device was
  connected at any point (`adb devices -l` empty throughout). This is the single gate preventing a
  final unconditional PASS and preventing creation of the closing tag this session.
- Retail's Windows installer-level rc.1→rc.2 upgrade-with-data cycle specifically (mechanism
  identical to Clinic's verified one, but not separately driven this session).
- `RESTRICTED` license state, live (policy-configuration-dependent; fully unit-tested).

## Next step to close Phase 7V

Connect and authorize an Android device (the previously-used Infinix X6528, or any other), confirm
via `adb devices -l`, then complete Parts K–N (physical signed upgrade, physical activation
lifecycle for both products, physical authority-boundary proof) exactly as this document's sibling
NOT-VERIFIED reports describe. Once done, the closing tag
`aura-product-licensing-phase7-validation-complete` can be created per the spec's Part U gate.

## Tag status this session

**No new tag created.** Per the governing spec's explicit Definition of Done and Git Strategy
sections, the closing tag must not be created while physical Android validation is outstanding.
The existing Phase 7 tag remains exactly as it was.

## Stop condition

Per the governing spec: stop completely after Phase 7V. No Phase 8, subscription-expiry
enforcement, payment gateways, e-invoicing, Aura Core integration, or VPS/public deployment work
was begun or implied by anything in this session.
