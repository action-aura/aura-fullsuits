# Phase 8V-P — Physical Commercial Closure Handover

## What this phase was

A narrow continuation of Phase 8V: physically/operationally validate the 7 Phase 8 commercial
lifecycle scenarios using real Owner processes and real installed products. No new Owner UI, no new
commercial features, no domain redesign, no Phase 9.

## What actually happened

No physical Android device was connected (`adb devices -l` genuinely empty, checked with real
`adb.exe`, not assumed). Per the user's own explicit choice (asked directly, not assumed), this
session did every non-Android-dependent part for real instead of stopping:

1. Bumped all four artifact families `1.0.0-rc.2 -> 1.0.0-rc.3` (real shipped-code change since
   rc.2 justified it per the existing versioning policy).
2. Found and fixed a real environment blocker: the bundled trust anchor referenced a signing key
   that no longer existed in the persistent dev database. Generated a real key, regenerated the
   trust anchor via the project's own canonical script against a real running Owner server.
3. Built real Windows artifacts: `AuraClinic.exe`/`AuraRetail.exe` (PyInstaller) and their Inno
   Setup installers, rc.3, real checksums, alongside (not overwriting) rc.1/rc.2 history.
4. Ran the actual frozen `.exe`s as real Windows processes and drove all 7 commercial scenarios
   against a real running Owner server (real Postgres, real Ed25519 signing, real HTTP) — 6 fully
   real end-to-end, 1 (plan downgrade) conditional on a disclosed feature gap it itself found.
5. Found and fixed a real, production-breaking P0-class defect along the way
   (`DEVICE_ALREADY_REGISTERED` — see `final-residual-risk-register.md`), regression-tested.
6. Found and fixed a second real environment gap (stale permission-seed data blocking a real
   `is_super_admin` staff account from a route it should always have access to).
7. Verified real product data (Clinic patients/appointments, Retail sales/returns/stock) untouched
   throughout, `PRAGMA integrity_check: ok` on both.
8. Ran the full automated regression at final HEAD: 380 owner + 214 commercial_runtime, 0 failures.
9. Wrote the full documentation set, tiered honestly by what's real vs. structural vs. not verified.

## What did not happen, stated plainly

No physical Android device validation. No Android APK/AAB build. No final
`aura-commercial-licensing-operations-phase8-complete` tag — correctly withheld per this phase's own
rules. No Phase 9 work of any kind.

## State at handover

- Working tree: commits below, clean.
- `aura-owner-commercial-ops-phase8-conditional-complete`: unchanged, unmoved.
- 380 owner tests, 214 commercial_runtime tests, both green.
- Two real Windows rc.3 artifacts built and validated end-to-end against a real Owner server.
- One real P0-class defect and two real environment gaps found and fixed.
- One real, disclosed feature gap found and left open (device-limit sync), correctly not "fixed" by
  adding new scope this phase forbids.

## Exact next step

A session with a physical Android device connected. Build rc.3 APK/AAB, install, run the same 7
scenarios (already proven at the Owner/Windows tier this session), capture logcat, write the closing
decision. That is the entire remaining scope before Phase 8 can be called unconditionally complete.
