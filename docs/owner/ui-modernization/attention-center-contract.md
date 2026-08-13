# Owner App — Attention Center Contract (Stage F)

Real implementation reference for the role-aware Attention Center added
this stage: `owner/app/attention/` (service + route), `owner/app/
templates/attention/index.html`, the topbar indicator in `layout/
base.html`, and the sidebar link in `layout/_sidebar.html`.

## Part 1 — the audit (what's real, per the spec's own category list)

The governing spec lists candidate categories as examples ("may
aggregate... such as"), not a mandatory checklist. Each was investigated
against real code before anything was built. Categories with no real,
permission-gated, queryable backing were **not** built — see the bottom
of this section for why, per the spec's explicit instruction not to
fabricate a section for data that doesn't exist.

| # | Category | Real source? | Model / field | Governing permission | Ownership scope | Already surfaced by |
|---|---|---|---|---|---|---|
| 1 | Overdue Follow-ups | Yes | `LeadFollowup` / `CustomerFollowup` — derived: `completed_at IS NULL AND cancelled_at IS NULL AND due_at < now()` (never a stored status column, per `leads/engagement.py::followup_status()`/`is_overdue()`) | `leads.view_own`/`leads.view_all` (Lead side), `customers.view` (Customer side) | Yes — Lead side: `LeadFollowup.employee_profile_id == actor` unless `leads.view_all` held (mirrors `leads/engagement.py::list_own_lead_followups_overdue()`, reused directly, not reimplemented). Customer side: `Customer.assigned_sales_staff_id == staff.id` unless `customers.view_all` held (mirrors `customers/services.py::customer_visible_to_actor()`) | `leads/dashboard.py::employee_crm_dashboard()` (count only, no list); nothing today lists it |
| 2 | Quotes waiting for approval | Yes | `CommercialApproval` (`target_type="QUOTE_LINE"`, `status="PENDING"`) | `quotes.approve` or `pricing.override` (`require_any_permission`, matches `commercial_sales/routes.py::decide_approval_route`) | No — approval permission is a single global grant, not an `_own`/`_all` pair; every approver with the permission sees every pending approval, same as the real decide route | `commercial_sales_web.quote_detail` shows a quote's own pending approvals; nothing lists them company-wide today |
| 3 | Overdue Invoices | Yes | `CommercialInvoice` — derived: `status IN (ISSUED, PARTIALLY_PAID) AND due_date < today` (no stored `OVERDUE` status exists — confirmed against `commercial_sales/errors.py::INVOICE_TRANSITIONS`) | `invoices.create`/`invoices.issue` (`_own`/`_all` pair) | Yes — `apply_ownership_filter(CommercialInvoice, ...)`, the exact real helper `commercial_sales_web.list_invoices` already uses | `commercial_sales_web.list_invoices` (filterable by status, not by overdue) |
| 4 | Unallocated Payments | Yes | `PaymentRecord.status == "CONFIRMED"` with `unallocated_payment_balance(payment) > 0` (the real function `commercial_sales/allocation.py` and `payment_detail` already use) | `payments.view` (single, global — not an `_own`/`_all` pair; matches the real `list_payments`/`payment_detail` routes) | No — `payments.view` is company-wide by design in the real routes | `commercial_sales_web.payment_detail` shows one payment's own unallocated balance; nothing lists all of them today |
| 5 | Pending Expenses | Yes | `ExpenseApproval.status == "PENDING"` | `expenses.approve` | Conflict-excluded, not ownership-scoped: approval visibility is company-wide for an `expenses.approve` holder (matches the real `expense_detail` route's `all_held` set), but items where the actor is the requester or the recorded beneficiary are excluded — self-approval and beneficiary-conflict are always forbidden in `expenses/approvals.py::check_approver_eligibility()`, so an item the actor could never actually decide is never shown, per this task's own "no item should ever link to a 403 [or a guaranteed-reject]" rule | `operations_ui.expense_detail` shows one expense's own pending approval; nothing lists all pending approvals today |
| 6 | Pending Commission approvals | Yes | `CommissionLedgerEntry.status == "PENDING"` | `commissions.approve` | No — same as expenses/payments, a single global approval permission, matches the real `approve_commission_route` gate | `commercial_sales_web.list_commissions` (filterable by `?status=PENDING`, already the real screen) |
| 7 | Expiring Licenses | Yes | `License.status == "ACTIVE"` and `valid_until` within 30 days (mirrors the dashboard's own existing 7/30-day subscription-expiry convention in `dashboard/services.py`) | `licenses.view` | No — `licenses.view` is a single, company-wide permission in the real seed data | `dashboard/index.html` shows subscription (not license) expiry counts; nothing surfaces license-level expiry today |
| 8 | Suspended Licenses | Yes | `License.status == "SUSPENDED"` (real enum value — see `licensing/routes.py::transition()`'s `permission_by_target` map) | `licenses.view` (to see it); the actual reactivate action needs `licenses.reactivate`, which the linked `licensing.detail` page itself already gates inline (`allowed_transitions`) | No | `dashboard/index.html`'s "Licenses by status" table shows the count; nothing lists or links to individual suspended licenses today |
| 9 | Device-limit events | Yes | `InternalNotification` (`notification_type="DEVICE_LIMIT_EXCEEDED"`, `status IN (OPEN, IN_PROGRESS)`) — created by `commercial_ops/device_slot_ops.py` | `subscriptions.view` (matches `commercial_ops_ui.list_notifications`'s real gate) | Yes — filtered to the actor's own real queue role code(s) (`assigned_role_code`), mirroring `commercial_ops/queues.py::_notification_items()`/`ui_routes.py::_staff_role_codes()` exactly; `SUPER_ADMIN` sees all, matching `get_queue_for_role("SUPER_ADMIN")` | `commercial_ops_ui.list_notifications` (full list, all types); `commercial_ops_ui.queue_view` ("My Queue", already role-scoped the same way) |
| 10 | Failed backups | Yes, narrowly | `DatabaseBackupRecord.status == "FAILED"` — **only when it is the single most recent backup record** (mirrors `dashboard/services.py::get_dashboard_summary()`'s own existing `latest_backup` semantics exactly, not a new rule): an older failure superseded by a later success is not shown, since there's nothing to act on | `system.view` | No — company-wide, matches `system.list_backups` | `dashboard/index.html`'s "Latest Owner database backup" card already shows this exact same fact; the Attention Center surfaces it as an actionable item, not just a status line |
| 11 | Degraded readiness checks | **No real source** | `owner/app/health.py`'s `/health/ready` computes checks **live, on each call**, with no persistence anywhere (no table, no row) — see its own docstring: "Never returns a secret, a stack trace, or any customer-domain data." It is also a public, unauthenticated endpoint (no `@require_permission`), not tied to any employee's permission set. There is nothing to query per-employee, per-permission, or after the fact | — | — | `/health/ready` (ops/infra tooling only, not a screen inside Owner) |
| 12 | Security events requiring action | **No real source** | `SecurityEvent` (`app/models/audit.py`) has `event_type`, `staff_user_id`, `ip_address`, `detail`, `created_at` — **no status, severity, or acknowledged/resolved field of any kind**. There is a real, permission-gated security event log (`audit.view` → `audit.security_events`), but nothing in the schema distinguishes "requires action" from "informational" (e.g. a routine `LOGIN_SUCCESS` row and a real anomaly both look identical at the schema level). Inventing a rule here (e.g. "any event_type containing FAILED") would be exactly the fabrication this task explicitly prohibits | — | — | `audit.security_events` (full log, unfiltered) |

**Categories 11 and 12 are deliberately not built.** No fake/empty
section exists for them in the UI or the service — `get_attention_items()`
has no code path for either, at all.

## Part 2 — why no persistent notification table was added

Two real, pre-existing authorities were found and audited before writing
any new code:

1. **`InternalNotification`** (`app/models/commercial_ops.py`) — a real,
   persistent notification record with genuine read-state fields
   (`status` OPEN/IN_PROGRESS/ACKNOWLEDGED/RESOLVED, `acknowledged_at`,
   `resolved_at`, assignment, a real `/commercial-ops/ui/notifications`
   list + assign/acknowledge/resolve routes, and a role-scoped "My
   Queue" at `/commercial-ops/ui/queue`). This **is** a real persistent
   notification authority — but it is scoped to commercial-ops
   scheduler-driven events (subscription/license expiry, device-limit,
   reconciliation drift) by construction (`notification_type` values are
   hardcoded at each creation site). It does not and cannot cover
   Leads, Quotes, Invoices, Payments, Expenses, Commissions, or backups
   — those domains have no notification-authority integration and
   building one for them was out of scope for this stage. The Attention
   Center **reuses** `InternalNotification` for the one category it
   already legitimately covers (device-limit events) rather than
   re-deriving that data a second way, and otherwise leaves the
   Notifications screen and My Queue exactly as they are — the
   Attention Center is a complementary, unified view across every
   *other* real actionable data source, not a replacement for either.
2. Every other category (Leads/Quotes/Invoices/Payments/Expenses/
   Commissions/Licenses) has **no** notification-authority integration
   at all — only a real, live status field on the record itself
   (`Expense.status`, `CommissionLedgerEntry.status`, `License.status`,
   etc.). For these, per the task's explicit instruction, the Attention
   Center **is** the derived, real-time view: computed fresh from the
   real tables on every call, with no persistence of its own.

**No database migration was added.** There is no `owner_attention_*`
table, no "seen"/"dismissed"/"read" column anywhere, and no caching
layer. This mirrors `first-login-welcome-contract.md`'s own precedent
for the product tour's completion state, for the same reason: nothing
here is a durable fact about the employee or the business record, and
persisting a parallel "I've seen this" flag would itself become a stale,
independently-drifting second source of truth the moment the underlying
record changed. **The real record's own lifecycle is the only read/
unread signal**: an item disappears the instant its underlying condition
resolves (follow-up completed, quote decided, invoice paid, payment
allocated, expense/commission approved, license reactivated, backup
succeeds) — there is no separate "mark as read" anywhere in this
module, and none should ever be added without a real product decision to
build actual notification persistence for these domains (a materially
larger, disclosed follow-up, not implied here).

## Part 3 — how permission-gating and ownership work here

`owner/app/attention/service.py::get_attention_items(staff)` resolves
the actor's real permission codes and employee profile **once**, then
calls a category builder **only if the actor holds the permission that
gates it** — unlike `dashboard/services.py::get_dashboard_summary()`
(role-dashboard-contract.md's own disclosed gap), which runs every query
unconditionally and lets the template discard what it can't show. This
module starts from a clean slate, so it does the query-level filtering
from day one:

```python
if "leads.view_own" in codes or "leads.view_all" in codes:
    items += _overdue_lead_followup_items(profile, codes)
...
if "system.view" in codes:
    items += _failed_backup_items()
```

Every ownership-scoped category reuses the **exact real filtering
mechanism** the corresponding real list route already uses — never a
second, independently-derived rule:

- Lead follow-ups: `leads/engagement.py::list_own_lead_followups_overdue()`
  for the `_own` case (called directly, not reimplemented).
- Customer follow-ups: the same rule as
  `customers/services.py::customer_visible_to_actor()`
  (`Customer.assigned_sales_staff_id == staff.id`).
- Invoices: `leads/ownership.py::apply_ownership_filter()` — the same
  shared helper `commercial_sales_web.list_invoices` calls.
- Device-limit notifications: the same `assigned_role_code` role-scoping
  `commercial_ops/queues.py::get_queue_for_role()` already applies.

Categories gated by a single, global, non-`_own`/`_all` permission
(`quotes.approve`, `payments.view`, `expenses.approve`,
`commissions.approve`, `licenses.view`, `system.view`) are intentionally
**not** artificially ownership-filtered — that would contradict the real
routes' own authorization model, which already treats these as
company-wide once the permission is granted.

**Every `action_url` is generated with `url_for()` against a route this
same staff member can already reach** with the permission that surfaced
the item — verified per category in the audit table above (e.g. a
suspended-license item links to `licensing.detail`, gated only by
`licenses.view`, never directly to a reactivate action that needs the
separate `licenses.reactivate` permission the actor may not hold — the
real reactivate form on that page is itself already conditionally
rendered). Pending-expense items where the actor is the requester or
beneficiary are excluded outright (see the audit table, row 5) rather
than linked to a decision the real service layer would reject as
self-approval.

## Part 4 — priority derivation

Priority is one of three values: `urgent` / `normal` / `low`. Every
category derives it from **real elapsed time already on the record**
(how long overdue, how long pending, how soon expiring, how long
suspended) via a shared `_age_priority()` helper with per-category
thresholds chosen from the category's own real-world urgency shape (e.g.
an overdue invoice needs a longer grace window than an overdue
follow-up before it's "urgent") — never an arbitrary/invented score.
Two categories use a different, still-real signal instead of age:

- **Device-limit events** use `InternalNotification.severity`
  (INFO/WARNING/CRITICAL — a real column already set at creation time)
  directly, since it's a stronger real signal than recomputing age.
- **Failed backups** are always `urgent` when shown at all, because the
  category itself is defined narrowly (only the single most recent
  backup, only when it failed — see the audit table, row 10) — by the
  time it's shown, it's already the most current known state.

## Part 5 — localization and text composition

Every `title`/`context` string is built with `flask_babel.gettext()`
(`_()`) and `%(name)s`-style placeholders for the real interpolated data
(amounts, names, durations), exactly like the rest of the server-rendered
UI — **not** left as untranslated plain text. This is a deliberate
departure from `InternalNotification.title`/`.message`'s own precedent
(those are stored, persisted English strings, because they're written
once into the database and never re-rendered per-viewer-locale); every
Attention Center item is instead computed fresh per request, so it can
and does respect the viewer's own locale like any other page.

## Part 6 — the two integration points

1. **`/attention` page** (`owner/app/templates/attention/index.html`) —
   grouped by category, sorted urgent-first then oldest-first, a real
   empty state when nothing needs action (and a second, honest empty
   state note when the reason is "your role holds none of the relevant
   permissions" — same tone as `dashboard/index.html`'s own empty-state
   card). `@require_login` only, not one fixed permission — see
   `attention/routes.py`'s own docstring for why: the screen's real
   content is already independently permission-filtered per category,
   same as `dashboard.index`.
2. **Topbar indicator** (`layout/base.html`) — a real `<a>` link with a
   real, derived count, rendered only when `can_view_attention_center`
   (computed in `app/__init__.py`'s context processor from the same
   `ATTENTION_CATEGORY_PERMISSIONS` set) — distinct from the adjacent
   "Quick create" `<button disabled>` stub, which remains an inert
   placeholder for unrelated, not-yet-built functionality. The Overview
   sidebar group gains a matching `Attention Center` link under the same
   permission check.

## Part 7 — disclosed performance trade-off

The topbar indicator's count is computed by calling the exact same
`get_attention_items()` used by the full `/attention` page and taking
`len()` — **not** a separate, leaner COUNT-only query set. This means a
real per-request cost (proportional to how many Attention Center
permissions the viewer holds) on every page load for any employee who
holds at least one such permission, since `layout/base.html` is
extended by every page. This is an accepted, disclosed trade-off for
this stage — the same one `role-dashboard-contract.md` already accepts
for `get_dashboard_summary()` ("a handful of indexed COUNT queries, not
N+1... not treated as urgent") — chosen deliberately over building a
second, independently-drifting filter implementation for the badge
alone. A future optimization (lean per-category `COUNT(*)` queries, or a
short-TTL cache) is reasonable follow-up work, not done here to keep
this stage's change isolated and correctness-first.
