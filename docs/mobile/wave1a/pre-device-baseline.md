# Wave 1A — Pre-Device Baseline

Status: **PROVEN**, captured before any Wave 1A code change.

## Git state

- Tag `android-migration-phase4-corrective-complete` verified:
  `git rev-parse android-migration-phase4-corrective-complete` →
  `4ac43e98d41b7e9a5e1d7d6e886d5d1a09783d11`, matches `HEAD`
  (`a7b0c8cef6ba4605c18015593b616db0858f00ce` short `a7b0c8c`).
- Branch: `master`.
- `git status --short`: clean except this wave's own new
  `docs/mobile/wave1a/` directory (untracked, expected).
- No uncommitted changes to `products/`/`commercial_runtime/` exist at
  baseline → the 279-test backend suite is **not required at this
  baseline checkpoint** (no shared backend/runtime code was touched to get
  here). It will be rerun if/when Wave 1A itself modifies backend code
  (Part J's error-payload shape, Part K's role API, if applicable).

## Automated baselines (rerun fresh this wave, not assumed from memory)

```
android/aura-retail: ./gradlew.bat :app:testDebugUnitTest assembleDebug
  -> BUILD SUCCESSFUL, 29/29 tests

android/aura-clinic: ./gradlew.bat :app:testDebugUnitTest assembleDebug
  -> BUILD SUCCESSFUL, 17/17 tests
```

## Fresh debug APKs (this baseline)

| Product | Path | SHA-256 |
|---|---|---|
| Retail | `android/aura-retail/app/build/outputs/apk/debug/app-debug.apk` | `c19acc6b025b27699edd89f362e10ec9cd05da2e4e56ec1f339567bb061d2ff1` |
| Clinic | `android/aura-clinic/app/build/outputs/apk/debug/app-debug.apk` | `ecce2ec42f054deeb2b260124a0d150f413c05785b685dd2858b2d29d23072d7` |

Identical checksums to Phase 4's own build report — confirms no drift
between the corrective-complete commit and this wave's starting point.

## Installed app versions on the target device

None — confirmed via `adb shell pm list packages | grep -i actionaura`
returning empty (see `device-environment-report.md`). This is a clean
-device baseline for both products.
