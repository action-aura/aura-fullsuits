# Phase 8V-P — Remaining Physical Gate Matrix

One row per Definition-of-Done item that genuinely requires a physical Android device. Everything
NOT listed here was either already closed in Phase 8V or is closed by this session's Windows-only
work (see `final-regression-report.md`, `scenario-*-evidence.md`).

| Gate | Requires | Status this session |
|---|---|---|
| Early renewal, physical Clinic Android | Device | NOT VERIFIED |
| Late renewal, physical Retail Android | Device | NOT VERIFIED |
| Past-due, physical Android leg | Device | NOT VERIFIED (Windows leg done) |
| Pilot conversion, physical Android leg | Device | NOT VERIFIED (Windows leg done) |
| Emergency extension, physical Android leg | Device | NOT VERIFIED (Windows leg done) |
| Device replacement, physical Android leg | Device | NOT VERIFIED (Windows leg done) |
| Plan downgrade, physical Android leg | Device | NOT VERIFIED (Windows leg done) |
| Android APK/AAB release build (rc.3) | Gradle + signing key access — buildable without a device, deferred by user's explicit choice this session | NOT BUILT THIS SESSION |
| Android certificate continuity re-check against a fresh rc.3 build | Device or at minimum a fresh build | NOT VERIFIED |
| Clinic/Retail Android Logcat privacy capture | Device | NOT VERIFIED |
| Android commercial-state/localization on-device rendering (PENDING message) | Device | NOT VERIFIED |
| Final unconditional Phase 8 tag | Every row above | WITHHELD |

Everything in this matrix reduces to one root cause: no physical Android device is connected in this
environment. Nothing here is a code defect, a missing capability, or an untested code path at the
service/wire level — the underlying Android source changes (rc.3 version bump, the PENDING-message UI
fix from Phase 8V) are already committed and were already unit-tested via a real Gradle build in
Phase 8V/M7 (82/82 Clinic, Retail build successful) at rc.2's code state; what's specifically
unverified is the *physical, on-device, rc.3* run.
