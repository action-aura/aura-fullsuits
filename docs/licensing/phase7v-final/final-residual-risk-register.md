# Phase 7V-F — Final Residual Risk Register

| # | Risk | Severity | Status |
|---|---|---|---|
| 1 | Retail Android physical upgrade/lifecycle not completed (device disconnected mid-session) | **P1** (blocks final tag) | Open — requires a stable device connection for the remainder of the physical sequence |
| 2 | Clinic Android physical activation-onward lifecycle not completed on-device (upgrade/data proven; activation itself was not) | **P1** (blocks final tag) | Open — same root cause |
| 3 | Both products' physical Kotlin/Python authority boundary and Logcat privacy not verified on real hardware | P2 (source/protocol-level proof exists and is strong) | Open |
| 4 | Android AAB artifacts stale relative to the final rebuilt APK (trusted-time fix landed after the last `bundleRelease`) | P2 | Open — rebuild `bundleRelease` before any real distribution |
| 5 | Device connection instability itself (disconnected 3+ times across the session, root cause not diagnosed — could be USB port/cable/driver related, not a product defect) | P3 (environmental) | Open — recommend a different USB port/cable for the next session |
| 6 | Suspend transition surfaces to the product as a generic check-in failure (`WARNING`/offline fallback), not a distinct local `SUSPENDED` state — observed, not previously documented | P3 (behavior is safe — data preserved, mutations still get blocked once RESTRICTED is reached — but the specific state label a user sees during the interim window may be less informative than intended) | Open — worth a design review in a future phase, not a Phase 7V-F blocker |
| 7 | A narrower trusted-time gap remains for a process that restarts while already offline with no successful sync yet in that process's lifetime (the anchor cache is process-lifetime, not cross-restart) | P3 | Open — the primary, common case (continuously-running process) is fixed; this narrower case would need persisting a monotonic-to-wallclock delta across restarts, out of this session's minimal-fix scope |

## Fixed this session (not residual — listed for traceability)

- **Real P0**: trusted-time anchor re-pinned fresh on every check-in, permanently defeating
  offline/warning/restricted detection during any continuous outage. Fixed in `activation.py`,
  `checkin_scheduler.py`, `trusted_time.py`. Verified live, both products, real elapsed time,
  reaching real `RESTRICTED`.
- **Real P0**: Android `app.py` eagerly imported `WindowsDpapiDeviceIdentityProvider` (needs the
  Windows-only `cryptography` package) at module top-level regardless of platform, crashing the
  entire embedded server on Android with `ModuleNotFoundError`. Fixed via a lazy, platform-gated
  import in both products.
- **Real gap**: `cryptography` was never added to either Android `build.gradle`'s Chaquopy pip
  list, even though Phase 7's `assertion_verifier.py`/`device_identity.py` require it for shared
  Ed25519 verification. Fixed (unpinned `install "cryptography"`, resolves to a real pre-built
  Chaquopy wheel, `42.0.8`).

## No P0 remains open that is fixable within this environment

Risks #1 and #2 (physical Android completion) are genuine environmental/hardware-connectivity
prerequisites, not code defects — no source change in this repository can close either.
