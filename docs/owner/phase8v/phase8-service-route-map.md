# Phase 8V — Service / Route / Permission / Audit / Scenario Map

Every row below is real: service function verified to exist by reading the module, permission code
verified against `app/staff/seed_data.py`, audit action code assigned following the existing
`<ENTITY>_<VERB>` convention already used by every prior milestone. Routes/templates marked "NEW"
are what this phase adds; nothing here re-implements lifecycle logic -- every route below is a thin
wrapper that resolves a public UUID, checks RBAC, calls the existing service function, and redirects
(PRG) or renders.

## Renewals

| Workflow | Service (`commercial_ops/renewal_requests.py`) | Permission | Recent-auth/MFA | Route | Audit action (existing) |
|---|---|---|---|---|---|
| Create | `create_renewal_request()` | `subscriptions.renew` | no | `POST /commercial-ops/ui/renewals` (NEW) | `RENEWAL_REQUEST_CREATED` |
| Detail | n/a (read) | `subscriptions.view` | no | `GET /commercial-ops/ui/renewals/<id>` (NEW) | n/a |
| Transition (QUOTED/AWAITING_*/CANCELLED/VOIDED/REJECTED) | `transition_renewal_request()` | `subscriptions.renew` | no | `POST .../transition` (NEW) | `RENEWAL_REQUEST_STATUS_CHANGED` |
| Approve | `approve_renewal_request()` | `subscriptions.renew` | **yes** | `POST .../approve` (NEW) | `RENEWAL_REQUEST_APPROVED` |
| Apply | `apply_renewal_request()` | `subscriptions.renew` | **yes** | `POST .../apply` (NEW) | `RENEWAL_APPLIED` |
| List | n/a (read) | `subscriptions.view` | no | `GET /commercial-ops/ui/renewals` (NEW) | n/a |

Separation-of-duties (creator != approver) and idempotent apply (row-locked, concurrency-safe) are
already enforced inside the service layer (Milestone 2) -- the route never duplicates that check.

## Payments and corrections

Already has a working UI: `subscriptions/routes.py::create_payment`/`correct_payment_route` +
`subscriptions/detail.html`. `correct_payment()` (Milestone 1) already writes a
`PaymentCorrectionHistory` row instead of mutating in place. **No new route needed** -- this phase
verifies the existing one still meets the Phase 8V bar (server-side RBAC: `payments.create`/
`payments.correct`; history preserved; no card/bank fields anywhere in the form) rather than
duplicating it.

## Pilots

| Workflow | Service (`commercial_ops/pilot_lifecycle.py`) | Permission | Recent-auth | Route | Audit action |
|---|---|---|---|---|---|
| Create | `create_pilot_record()` | `pilots.manage` | no | `POST /commercial-ops/ui/pilots` (NEW) | `PILOT_CREATED` |
| Approve | `approve_pilot()` | `pilots.manage` | no | `POST .../approve` (NEW) | `PILOT_STATUS_CHANGED` |
| Activate | `activate_pilot()` | `pilots.manage` | no | `POST .../activate` (NEW) | `PILOT_STATUS_CHANGED` |
| Extend | `extend_pilot()` | `pilots.manage` | no | `POST .../extend` (NEW) | `PILOT_EXTENDED` |
| Convert | `mark_pilot_converted()` (called after a real applied `RenewalRequest`) | `pilots.manage` + `subscriptions.renew` | **yes** (via renewal apply) | `POST .../convert` (NEW) -- creates+approves+applies the renewal then marks converted, in one guided flow | `RENEWAL_APPLIED` + `PILOT_CONVERTED` |
| Complete (no conversion) | `complete_pilot()` | `pilots.manage` | no | `POST .../complete` (NEW) | `PILOT_STATUS_CHANGED` |
| Cancel | `cancel_pilot()` | `pilots.manage` | no | `POST .../cancel` (NEW) | `PILOT_STATUS_CHANGED` |
| Detail/list | n/a | `pilots.view` | no | `GET .../pilots[/<id>]` (NEW) | n/a |

Extension already enforces max-extension cap + mandatory reason server-side (Milestone 4) -- the
route only surfaces the current count/cap so staff aren't guessing.

## Emergency extensions

| Workflow | Service (`commercial_ops/emergency_extensions.py`) | Permission | Recent-auth | Route | Audit action |
|---|---|---|---|---|---|
| Create | `create_emergency_extension()` | `emergency_extensions.create` | **yes** | `POST /commercial-ops/ui/emergency-extensions` (NEW) | `EMERGENCY_EXTENSION_CREATED` |
| Revoke | `revoke_emergency_extension()` | `emergency_extensions.revoke` | **yes** | `POST .../revoke` (NEW) | `EMERGENCY_EXTENSION_REVOKED` |
| List/detail | n/a | `emergency_extensions.view` | no | `GET .../emergency-extensions[/<id>]` (NEW) | n/a |

Both permissions remain SUPER_ADMIN-wildcard-only per Milestone 4's own design decision -- Phase 8V
does not widen that.

## Manual activation review

| Workflow | Service (`commercial_ops/activation_policy.py`) | Permission | Recent-auth | Route | Audit action |
|---|---|---|---|---|---|
| List pending | n/a | `pending_activations.view` | no | `GET /commercial-ops/ui/pending-activations` (NEW) | n/a |
| Approve | `approve_pending_activation()` | `pending_activations.decide` | **yes** | `POST .../approve` (NEW) | `PENDING_ACTIVATION_APPROVED` |
| Reject | `reject_pending_activation()` | `pending_activations.decide` | **yes** | `POST .../reject` (NEW) | `PENDING_ACTIVATION_REJECTED` |
| Manage policy | `create_activation_policy()` | `activation_policy.manage` | **yes** | `POST .../activation-policy` (NEW) | `ACTIVATION_POLICY_CREATED` |

## Device-slot operations

| Workflow | Service (`commercial_ops/device_slot_ops.py`) | Permission | Recent-auth | Route | Audit action |
|---|---|---|---|---|---|
| Release slot | `release_device_slot()` | `installations.replace_device` | no | `POST /commercial-ops/ui/installations/<id>/release` (NEW) | `INSTALLATION_STATUS_CHANGED` |
| Replace slot | `replace_device_slot()` | `installations.replace_device` | no | `POST .../replace` (NEW) | `INSTALLATION_STATUS_CHANGED` |
| Create exception | `create_device_slot_exception()` | `device_slot_exceptions.manage` | no | `POST /commercial-ops/ui/licenses/<id>/slot-exceptions` (NEW) | `DEVICE_SLOT_EXCEPTION_CREATED` |
| Revoke exception | `revoke_device_slot_exception()` | `device_slot_exceptions.manage` | no | `POST .../revoke` (NEW) | `DEVICE_SLOT_EXCEPTION_REVOKED` |
| View | `resolve_effective_device_limit()` | `device_slot_exceptions.view`/`installations.view` | no | folded into license detail (NEW section) | n/a |
| Over-limit scan | `scan_over_limit_licenses()` | (CLI/notification-driven, no dedicated permission) | no | surfaced via notifications, not a standalone page | n/a |

## Notifications

| Workflow | Service (`commercial_ops/commercial_policy.py`) | Permission | Route | Audit action |
|---|---|---|---|---|
| List/filter | n/a | any authenticated staff (own-role queue) | `GET /commercial-ops/ui/notifications` (NEW) | n/a |
| Assign | `assign_notification()` | `subscriptions.view` (read) + role match | `POST .../assign` (NEW) | `NOTIFICATION_ASSIGNED` |
| Acknowledge | `acknowledge_notification()` | same | `POST .../acknowledge` (NEW) | `NOTIFICATION_ACKNOWLEDGED` |
| Resolve | `resolve_notification()` | same | `POST .../resolve` (NEW) | `NOTIFICATION_RESOLVED` |

No dedicated "notifications.*" permission exists in `seed_data.py`. Reuses existing view permissions
per notification `assigned_role_code`, gated in-route rather than inventing a new permission set
Milestone 3 never defined (kept minimal, matches how `queues.py` itself already reads
`assigned_role_code` without a bespoke permission).

## Reconciliation

| Workflow | Service (`commercial_ops/reconciliation.py`) | Permission | Route | Audit action |
|---|---|---|---|---|
| Dry-run scan | `run_reconciliation(dry_run=True)` | `system.view` (super-admin-only tooling, matches CLI's own unguarded-but-operator-only precedent) | `GET /commercial-ops/ui/reconciliation` (NEW) | n/a (read) |
| Apply (write notifications) | `run_reconciliation(dry_run=False)` | `system.manage_settings` | `POST .../run` (NEW) | `RECONCILE_STATE_INVALID`/etc. via `create_notification()` |

Reconciliation never mutates a commercial record itself (Milestone 6 design) -- "apply" only means
"write the notifications," never "auto-repair." No separate repair-approval workflow exists in the
service layer to wire up (there is no `approve_repair()`/`apply_repair()` function anywhere in this
codebase) -- the governing brief's Part K describes a repair-approval capability that was never built
in Milestones 1-8 and is out of this closure phase's scope to invent from nothing (see
`phase8v-residual-risk-register.md`).

## Operational queues

| Role | Service | Route |
|---|---|---|
| SALES/FINANCE/SUPPORT/SUPER_ADMIN/VIEWER | `queues.get_queue_for_role()` | `GET /commercial-ops/ui/queue` (NEW, resolves role from the logged-in staff's own role assignment) |

## Commercial timeline

No dedicated timeline-assembly service exists in Milestones 1-8. Built new for Phase 8V as a
read-only aggregation (`commercial_ops/timeline.py`, NEW) over existing history tables
(`SubscriptionStatusHistory`, `LicenseStatusHistory`, `InstallationStatusHistory`,
`RenewalRequestStatusHistory`, `PilotStatusHistory`, `InternalNotification`) joined by explicit
`subscription_id`/`license_id`/`installation_id` foreign keys -- never by timestamp inference.

## Dashboard

Already extended in Milestone 6 (`dashboard/services.py`). This phase adds working links from each
count to its filtered queue/list route (the counts existed; the links did not, since the destination
routes didn't exist until this phase).
