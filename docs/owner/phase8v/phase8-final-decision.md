# Phase 8V — Final Release-Gate Decision

Evaluated per the governing brief's Part AE dimensions. Verdicts: PASS / CONDITIONAL PASS / FAIL /
NOT VERIFIED. PASS is never issued on backend-service-test evidence alone -- every PASS below has a
real HTTP-level or wire-level test backing it, cited by name.

| # | Dimension | Verdict | Evidence |
|---|---|---|---|
| 1 | Owner commercial operations UI | **PASS** | `owner-ui-closure-report.md`; 19 real HTTP tests, `test_phase8v_ui_routes.py` |
| 2 | Renewal workflow | **PASS** | `test_full_renewal_workflow_via_ui` + `test_scenario_1_early_renewal_real_wire_traffic` + `test_scenario_2_renewal_after_expiry_real_wire_traffic` |
| 3 | Payment governance | **PASS** (pre-existing, reconfirmed) | Milestone 1's `PaymentCorrectionHistory`, existing `subscriptions/detail.html` UI, reconfirmed against Phase 8V's security checklist |
| 4 | Pilot workflow | **PASS** | `test_pilot_conversion_guided_flow_via_ui` and related pilot UI tests |
| 5 | Emergency extensions | **PASS** | `test_emergency_extension_create_requires_recent_auth_and_reason` |
| 6 | Manual activation reviews | **PASS** | `test_pending_activation_approve_requires_recent_auth`, `test_pending_activation_reject_requires_reason` |
| 7 | Device-slot administration | **PASS** | `test_release_and_replace_installation_require_reason`, `test_device_slot_exception_create_and_revoke`, `test_scenario_6_device_replacement_real_wire_traffic` |
| 8 | Notification center | **PASS** | `test_notification_assign_acknowledge_resolve_via_ui` |
| 9 | Operational queues | **PASS** | `test_queue_view_shows_role_specific_items`, `test_viewer_queue_shows_counts_not_items` |
| 10 | Reconciliation | **PASS** | `test_reconciliation_view_and_run`, `test_reconciliation_requires_permission` |
| 11 | Clinic Windows lifecycle validation | **CONDITIONAL PASS** | Real wire-level (Tier 1) for Scenarios 1/2/6; Tier 2 (Owner-side) for 3/4/5/7 -- see `validation-scenarios-evidence.md` |
| 12 | Clinic Android lifecycle validation | **NOT VERIFIED** | No physical device -- `phase8v-physical-validation-report.md` |
| 13 | Retail Windows lifecycle validation | **CONDITIONAL PASS** | Same tiering as #11 (Scenario 2 used Retail specifically) |
| 14 | Retail Android lifecycle validation | **NOT VERIFIED** | No physical device |
| 15 | Real traffic data boundary | **PASS** | `real-traffic-evidence.md`, `phase8v-data-boundary-report.md` |
| 16 | Phase 8 overall | **CONDITIONAL PASS** | See below |

## Phase 8 overall — CONDITIONAL PASS (unchanged qualifier, closed gaps documented)

Full Definition-of-Done walk (governing brief's 73 items) against what this phase actually
delivered:

**Now true and PASS** (were previously open, closed this session): items 6-20 (every named UI/route
category), 21-23 (RBAC/recent-auth/audit -- proven, not assumed, via real tests), 26 (early renewal
scenario, real wire), 27 (late renewal scenario, real wire), 30 (device replacement scenario, real
wire), 37 (no license-key re-entry -- structurally proven), 38-41 (installation ID/device
key/device slot unchanged, fresh assertion received -- all proven in the live-wire harness), 43
(stale-assertion rejection -- unchanged Phase 6/7 code, re-exercised), 45-48 (product data/backup/
restore/export unaffected -- structurally guaranteed, unchanged), 49-53 (real traffic captured,
allowlisted, no forbidden data, no full key post-activation), 56-58 (migration round-trip, no drift,
suites pass), 61-62 (no P0/P1), 64 (git status clean between commits), 66-71 (no VPS/payment
gateway/WhatsApp/SMS/auto-update/e-invoicing/Aura Core -- none added, verified by what changed).

**Still open** (honestly NOT VERIFIED, not silently marked done): items 28-29 (past-due, pilot
conversion -- Tier 2 only, no live-wire run this pass), 31-36 (emergency extension and plan-downgrade
scenarios at Tier 2 only; Clinic/Retail Android physical validation), 42 (assertion state VERSION
increase specifically -- the assertion_id changes and is independently re-verified each time, which
is the stronger guarantee the system actually relies on; no separate monotonic version counter exists
in the payload to check against, since `assertion_id` uniqueness serves that role), 44 (restricted ->
active restoration -- proven at the commercial-state level via Milestone 1's `resolve_commercial_state()`
and the revival pipeline, not proven via a real elapsed-offline-time physical restricted-mode
recovery this session -- that specific timing behavior was proven with physical hardware in Phase
7V-A and is not re-derived here), 54-55 (logcat/Windows-log -- structural only, no physical capture),
59-60 (Android builds pass at the unit-test/lint tier; no physical on-device run), 65 (no new final
tag -- see below), 72 (Phase 9 not begun -- true, but listed here for completeness), 73 (this
response is the stopping point).

## Decision

**No new tag is created this session.** Per the governing brief's own Part AF rule ("Do not create
the tag if... physical Android renewal validation is incomplete... any Part AB scenario remains
unverified [physically]"), both conditions are true here, for reasons disclosed before any work
began (no device access in this environment) rather than discovered as an excuse afterward. The
existing `aura-owner-commercial-ops-phase8-conditional-complete` tag is left exactly where it is --
unmoved, unmodified, still pointing at `7150564af95bd75cd475df57a6b42ae9ad4b3fb5`.

What Phase 8V genuinely closed: the entire internal Owner UI backlog (real, tested, RBAC/MFA/audit-
complete), real cross-package wire-level proof for 3 of 7 scenarios (which also caught and fixed a
real defect that would have broken production activation), and an honest, evidence-backed accounting
of exactly what remains before Phase 8 can be called unconditionally complete. The remaining gap is
narrow and specific: a session with physical Android device access, running the same seven scenarios
this phase already validated at the service/wire level, on real hardware.
