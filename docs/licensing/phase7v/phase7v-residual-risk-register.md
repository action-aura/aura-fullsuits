# Phase 7V — Residual Risk Register

> **Phase 7V-F update:** risk #1 below (no physical Android device) was partially closed —
> a device connected, Clinic's physical signed upgrade was completed, and this exposed a real P0
> in the trusted-time mechanism (now fixed). Retail's physical work and the remainder of Clinic's
> physical lifecycle stayed open due to the device disconnecting mid-session. See
> `docs/licensing/phase7v-final/final-residual-risk-register.md` for the current, complete list.
> This document preserved unmodified below for history.

| # | Risk | Severity | Status |
|---|---|---|---|
| 1 | No physical Android device validated this session — real hardware may surface issues (touch/UI, real network stack, real Keystore hardware behavior, real signed-upgrade install flow) that emulated/source-level testing cannot | **P1** (blocks final tag) | Open — requires a connected, authorized device |
| 2 | Windows installers remain unsigned (no Authenticode certificate) — SmartScreen warns on first run | P2 (known since Wave 1B, disclosed, controlled-pilot acceptable) | Open, unchanged |
| 3 | No production Owner instance exists in this environment — no real `trust_anchor.json` was bundled into any shipped rc.2 artifact; a real release cut requires running `scripts/generate_trust_anchor.py` against the real production Owner immediately before the final build | P1 (blocks real activation until done) | Open — required release step, not an environment defect |
| 4 | `RESTRICTED` license state not exercised live (only reachable under a non-`WARN_ONLY` plan policy; the test license used `WARN_ONLY`) | P3 | Open — fully covered at unit level (20 state-machine tests), low risk |
| 5 | Retail Windows installer-level rc.1→rc.2 upgrade-with-data cycle not performed this session (no pre-existing rc.1 Retail install with data existed) | P2 | Open — mechanism structurally identical to Clinic's verified cycle |
| 6 | Both Android APKs bundle `commercial_runtime/licensing_contracts/tests/*.py` source files (Chaquopy staged Python) — not a secret, but unnecessary size/exposed internal naming | P4 (hygiene only) | Open — not fixed this session, out of P0/P1 minimal-fix scope |
| 7 | Camera barcode scanning, external HID scanner compatibility, receipt-sharing on Android were not re-verified this session (require physical hardware) | P2 | Open — same root cause as #1 |
| 8 | "Older valid assertion attempting to downgrade newer state" replay scenario has no dedicated test (structurally prevented by always-replace-atomically persistence, but not directly tested) | P3 | Open — low risk, reasoning sound but untested |
| 9 | Two intermediate (pre-fix) rc.2 build artifacts were produced and discarded during this session's live debugging (checksums `e499474f...` Clinic / `bec5000b...` Retail installers; an earlier Clinic APK/exe iteration) — none were committed, tagged, or referenced as shippable, but noting for completeness | P4 (informational only) | Closed — final checksums in `rc2-release-candidate-manifest.md` supersede them |

## Fixed this session (not residual — listed for traceability)

- Owner `pg_dump`/`pg_restore` tool discovery (was hard-failing 5 tests).
- Windows commercial-build TLS-verification bypass via `AURA_OWNER_LICENSING_INSECURE` in frozen
  builds.
- Stale PyInstaller build cache causing a startup crash (`backports.zstd` `AttributeError`).
- Missing `trust_anchor.json` bundling in both Windows PyInstaller specs.
- Android `lintRelease` blocker (`setIsStrongBoxBacked` unguarded API-28 call, minSdk 26).

## No P0 or P1 remains open that is *fixable within this environment*

Risk #1 (physical device) and #3 (production Owner instance) are both genuine environmental
prerequisites, not code defects — no source change in this repository can close either.
