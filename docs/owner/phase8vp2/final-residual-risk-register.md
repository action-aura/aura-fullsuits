# Phase 8V-P2 — Final Residual Risk Register

| # | Item | Severity | Status |
|---|---|---|---|
| 1 | Physical Android validation, all 7 scenarios | Blocks final tag | NOT VERIFIED -- no device this session, disclosed upfront |
| 2 | Real Android-to-Owner traffic capture, Logcat privacy review | Blocks final tag (via #1) | Not performed -- requires a device |
| 3 | `installations.transition` (generic route) allows device deactivation/replacement with an optional, often-blank reason, alongside the mandatory-reason `release_device_slot()`/`replace_device_slot()` | Low (restricting-only) | Carried forward unchanged from Phase 8V/8V-P, still out of scope |
| 4 | Pilot-conversion post-conversion assertion content not re-verified over a second real wire round-trip | Low | Carried forward from Phase 8V-P (scripting oversight in that session, not revisited this session -- no device work was possible to complete it) |
| 5 | No Kotlin/Python conformance fixture files exist | Low-medium | Carried forward; this session's own preflight command is a step in the same direction (catching drift automatically) but does not replace this |
| 6 | Windows installers unsigned | Known, disclosed since Wave 1B | Unchanged |
| 7 | Retail Android licensing screen has no Arabic coverage | Low | Carried forward |
| 8 | `super_admin_mfa_required` preflight WARNING for `phase8vp-admin@example.com` | None (expected) | By design -- synthetic local test account, documented in the Phase 8V-P handover |

## Three real environment/logic defects found and fixed across Phase 8V-P and Phase 8V-P2 (not risks -- closed)

1. `DEVICE_ALREADY_REGISTERED` (Phase 8V-P) -- unhandled 500 on same-device/different-license
   activation. Fixed, regression-tested.
2. Stale trust-anchor / stale local product trust-store cache (Phase 8V-P) -- environment state
   fixes, documented, now covered by an automated preflight check so they will be caught immediately
   next time instead of found by hand.
3. `Subscription.device_allowance` never propagating to `License.device_limit` on a renewal (Phase
   8V-P2, this session) -- real Owner service defect, now fixed with regression tests; see
   `scenario7-resolution-report.md`.
4. `SUPER_ADMIN` role missing 10 `RolePermission` rows despite the underlying `Permission` catalog
   rows existing (Phase 8V-P2, this session, found by the new preflight command) -- real data
   consistency defect with no live-traffic impact (super admins bypass role-permission lookup
   entirely) but now fixed via `flask seed-rbac` and covered by the same preflight check going
   forward.

## No P0, no P1 remaining

All four items above are closed. The only open items are the physical-Android-device gap (items 1-2,
external to this codebase, requires hardware) and pre-existing low-severity carried-forward items
(3-7) that remain explicitly out of this phase's scope.
