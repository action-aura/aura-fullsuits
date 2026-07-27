# Phase 8V-P — Final Residual Risk Register

| # | Item | Severity | Status |
|---|---|---|---|
| 1 | Physical Android validation, all 7 scenarios | Blocks final tag | NOT VERIFIED — no device this session (user's explicit choice, disclosed upfront) |
| 2 | Android APK/AAB rc.3 build | Blocks final tag (indirectly, via #1) | Not built — deferred to device-access session |
| 3 | Renewal `device_allowance_after` never propagates to `License.device_limit` | **Medium — genuine feature gap, found this session** | Disclosed, not fixed (would require a new commercial-operations sync capability, explicitly out of this phase's scope). Real remediation mechanics (over-limit scan, no-silent-deactivation, blocked new activations, temporary exceptions) all work correctly once `device_limit` is updated by whatever means — only the auto-trigger from a renewal is missing. |
| 4 | `installations.transition` (generic route) allows device deactivation/replacement with an optional, often-blank reason, alongside the mandatory-reason `release_device_slot()`/`replace_device_slot()` | Low (restricting-only) | Carried forward from Phase 8V, still not fixed (still out of scope) |
| 5 | Pilot-conversion post-conversion assertion content not re-verified over a second real wire round-trip (Scenario 4) | Low | Device key wasn't persisted this session (scripting oversight); underlying function already unit-tested against real Postgres |
| 6 | Backend direct-enforcement (Part S) not independently re-run this session | Low | Relies on pre-existing, unchanged, already-real capability-guard test coverage; no product-backend code changed |
| 7 | No Kotlin/Python conformance fixture files exist | Low-medium | Carried forward from Phase 8V; this session's `DEVICE_ALREADY_REGISTERED`/`ALLOWED_PAYLOAD_FIELDS`-class defects are exactly what such fixtures would catch automatically instead of requiring a live-wire session to find by hand |
| 8 | Windows installers unsigned | Known, disclosed since Wave 1B | Unchanged |
| 9 | Retail Android licensing screen has no Arabic coverage | Low | Carried forward from Phase 8V |

## Two real defects found and fixed this session (not risks — closed)

- `owner/app/licensing_service/activation.py`: same-device-different-license activation crashed
  Owner with an unhandled 500 (`owner_device_public_keys.fingerprint` UNIQUE violation). Fixed:
  clean `DEVICE_ALREADY_REGISTERED` rejection. Regression-tested
  (`test_same_device_activating_a_different_license_rejected_cleanly`), full 380-test owner suite
  green.
- Stale local environment state (an old signing key with no corresponding database row, referenced
  by the bundled `trust_anchor.json`; a stale, un-resettable local product trust-store cache pointing
  at a different old key) blocked all real product validation until corrected — both environment
  fixes, not code changes, documented in `environment-readiness-report.md` and
  `scenario-2-late-renewal-evidence.md`.

## No P0, no P1 remaining

Item 3 (device-limit sync gap) is assessed Medium, not P0/P1 — its own remediation mechanics (the
part that actually protects against silent overage) work correctly; only the convenience of
auto-triggering them from a renewal is missing, and staff can update `License.device_limit` manually
today to achieve the same enforcement outcome.
