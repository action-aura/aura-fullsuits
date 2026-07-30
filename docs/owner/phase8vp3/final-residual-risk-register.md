# Phase 8V-P3 — Final Residual Risk Register

| # | Item | Severity | Status |
|---|---|---|---|
| 1 | Physical Android validation, all 7 scenarios | Blocks final tag | NOT VERIFIED -- no device, third consecutive session |
| 2 | Real Android traffic capture / Logcat privacy review | Blocks final tag (via #1) | Not performed -- requires a device |
| 3 | Current rc.3 artifacts have `OWNER_LICENSING_BASE_URL=""` baked in | Blocks physical licensing validation specifically | **New finding, this session.** Expected/by-design for a generic build, not a defect; fixed by rebuilding with `-PownerLicensingBaseUrl=...` once a device is available -- see `artifact-verification.md` for the exact command |
| 4 | `installations.transition` generic route allows an optional, often-blank reason | Low | Carried forward unchanged, still out of scope |
| 5 | Pilot-conversion post-conversion assertion not re-verified over a second real wire round-trip | Low | Carried forward unchanged |
| 6 | No Kotlin/Python conformance fixture files | Low-medium | Carried forward unchanged |
| 7 | Windows installers unsigned | Known, disclosed since Wave 1B | Unchanged |
| 8 | Retail Android licensing screen has no Arabic coverage | Low | Carried forward unchanged |
| 9 | `super_admin_mfa_required` preflight WARNING, now 2 accounts | None (expected) | Both are synthetic local test accounts from prior sessions |

## No new defect found or fixed this session

Item 3 is a real, useful finding (a precondition for the next device session, not previously
documented anywhere) but is not a bug -- it is the deliberate fail-safe default doing exactly what it
was designed to do. No code change was made or needed.

## No P0, no P1 remaining

Unchanged from Phase 8V-P2. The only open items are the physical-device gap (items 1-2, external,
requires hardware) and the pre-existing low-severity carried-forward items (4-8), all explicitly out
of scope for a validation-only phase.
