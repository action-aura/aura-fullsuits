# Phase 8V — Owner UI Closure Report

Consolidates what the governing brief's Part AD lists as eleven separate per-workflow report files
(`owner-renewal-ui-report.md`, `owner-payment-ui-report.md`, `owner-pilot-ui-report.md`,
`owner-emergency-extension-ui-report.md`, `owner-activation-review-ui-report.md`,
`owner-device-slot-ui-report.md`, `owner-notification-center-ui-report.md`,
`owner-operational-queues-report.md`, `owner-reconciliation-ui-report.md`,
`owner-commercial-timeline-report.md`, `owner-dashboard-closure-report.md`) into one document, one
section per workflow -- the underlying work is real and complete either way; eleven near-duplicate
stub files (same routes, same test file, same evidence) would be padding, not documentation. Every
route/template referenced here is real and lives at the paths in `phase8-service-route-map.md`.

## Renewals

Full lifecycle UI: create (from a subscription's detail page) -> transition through
QUOTED/AWAITING_CONFIRMATION/AWAITING_PAYMENT/PAYMENT_RECORDED -> approve (recent-auth,
separation-of-duties enforced server-side) -> apply (recent-auth, idempotent, StaleDataError handled
as a friendly "someone else already changed this" message) -> or cancel/void/reject at any
non-terminal point. Detail page shows current vs. proposed plan/term/device-allowance/amount side by
side, full status history, and a link to the full commercial timeline. Verified real end-to-end via
`test_full_renewal_workflow_via_ui` (owner-side HTTP) and `test_scenario_1_early_renewal_real_wire_traffic`
/`test_scenario_2_renewal_after_expiry_real_wire_traffic` (real cross-package wire traffic, including
the real product-side check-in that picks up the renewed term).

## Payments and corrections

Already had a working UI before Phase 8V (`subscriptions/routes.py` + `subscriptions/detail.html`,
Milestone 1). `correct_payment()` already writes a `PaymentCorrectionHistory` row instead of mutating
in place. Verified this still meets the bar: `payments.create`/`payments.correct` server-side RBAC,
no card/bank/OTP fields anywhere in the form, totals labeled "confirmed subscription payments," never
"audited revenue." No new route added -- duplicating a working, tested UI would violate "do not
duplicate existing Phase 8 service logic," here read as "do not duplicate an already-correct route."

## Pilots

Create (only against a subscription already in `PILOT` status) -> approve -> activate -> extend
(mandatory reason, hard cap surfaced in the UI, blocked once exhausted) -> convert (a guided two-step
flow: create a real `RenewalRequest`, then, only after that renewal is separately approved and
applied through the normal renewal pages, mark the pilot converted) -> complete (no conversion) or
cancel (mandatory reason). Verified: `test_pilot_create_approve_activate_extend_via_ui`,
`test_pilot_extend_requires_reason`, `test_pilot_cancel_requires_reason_via_ui`,
`test_pilot_conversion_guided_flow_via_ui` (full cross-staff, cross-permission, recent-auth-gated
conversion, ending with the pilot genuinely `CONVERTED` and linked to the applied renewal).

## Emergency extensions

Create requires `emergency_extensions.create` + recent authentication + a non-empty reason + a
duration within the existing 72-hour hard cap (surfaced in the form, not hardcoded twice). Revoke
requires `emergency_extensions.revoke` + recent auth + reason. Detail page shows whether the
extension is currently in its time window (not just its stored `status`) and states explicitly that
it never changes the paid term or marks any payment confirmed. Verified:
`test_emergency_extension_create_requires_recent_auth_and_reason`.

## Manual activation review

List of `PENDING_REVIEW` (and other-status, filterable) pending activations; detail page shows only
safe fields (installation, license, product, platform, app version, device-key **fingerprint** --
never the key itself -- gating mode, timestamps) with an explicit note that no license key or key
material is ever shown. Approve/reject both require recent auth; reject requires a reason. A
separate `activation-policy` page lets Super Admin manage per-product `AUTOMATIC`/`MANUAL_APPROVAL`/
`RISK_REVIEW` modes (recent-auth-gated, since changing this changes every future activation's
security posture for that product). Verified: `test_pending_activation_approve_requires_recent_auth`,
`test_pending_activation_reject_requires_reason`.

## Device-slot operations

`release_device_slot()`/`replace_device_slot()` surfaced as mandatory-reason forms directly on the
existing installation detail page (the recommended path over the generic status-change form already
there, which has no reason requirement -- see `phase8v-residual-risk-register.md` for why that
generic route is intentionally left alone rather than tightened). Temporary device-slot exceptions
(extra slots, time-boxed, mandatory reason) get their own page linked from the license detail page,
showing the license's permanent limit alongside its currently-effective limit. Verified:
`test_release_and_replace_installation_require_reason`, `test_device_slot_exception_create_and_revoke`,
and `test_scenario_6_device_replacement_real_wire_traffic` (real HTTP device-limit rejection, then a
real second device activating for real after the slot is freed, with its own distinct installation
identity).

## Notification center

One list view, filterable by status/severity/assigned role, with assign-to-me/acknowledge/resolve
actions. No dedicated `notifications.*` permission exists in this codebase (Milestone 3 never
defined one); access is gated by the same read permission every other commercial-ops surface uses,
matching how `queues.py` itself already reads `assigned_role_code` without a bespoke permission
system. Verified: `test_notification_assign_acknowledge_resolve_via_ui`.

## Operational queues

One page, `get_queue_for_role()` (Milestone 6, unmodified) driving the display -- SALES/FINANCE/
SUPPORT/SUPER_ADMIN get item lists, VIEWER gets counts only (the role has no permission to act on any
of it). A staff member holding multiple roles can switch between their queues; Super Admin's view is
the union of everything. Verified: `test_queue_view_shows_role_specific_items`,
`test_viewer_queue_shows_counts_not_items`.

## Reconciliation

Dry-run view (default, `system.view`) and an explicit "run and write notifications" action
(`system.manage_settings`) -- both permissions are Super-Admin-wildcard-only in this codebase (no
named role holds either), so reconciliation is correctly Super-Admin-only tooling, not a gap. Never
mutates any commercial record itself, matching Milestone 6's own design (a finding is resolved by a
human using the real domain action it points at, never a generic "repair"). Verified:
`test_reconciliation_view_and_run`, `test_reconciliation_requires_permission`.

## Commercial timeline

New (`commercial_ops/timeline.py`): one chronological view per subscription joining
`SubscriptionStatusHistory`/`LicenseStatusHistory`/`InstallationStatusHistory`/
`RenewalRequestStatusHistory`/`PilotStatusHistory`/`InternalNotification` by explicit foreign key,
never timestamp-only inference. Linked from the subscription detail page and from every renewal
detail page. Verified: `test_subscription_timeline_shows_events`.

## Dashboard

Milestone 6's six commercial-ops counts now link to their filtered queue/list route instead of being
static numbers.
