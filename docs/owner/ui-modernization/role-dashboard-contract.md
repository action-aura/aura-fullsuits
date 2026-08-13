# Owner App — Role-Aware Dashboard Contract (Stage C)

## Real gap found and fixed

`app/dashboard/services.py::get_dashboard_summary()` computes one
unfiltered aggregation — customer/subscription/license/installation
counts, commercial-ops activity (notifications, renewals, pilots,
pending activations, emergency extensions, device-slot exceptions),
latest backup status, recent audit log — and, before this stage,
`dashboard/index.html` rendered **all of it to every logged-in staff
member regardless of permission**. A SALES-only account (no
`system.view`, no `audit.view`) could see company-wide database backup
status and the full audit trail summary on its own landing page —
confirmed by direct browser inspection before this fix (see the
Stage B application-shell verification screenshots, taken before this
fix landed).

This is exactly the failure mode the governing spec's §14 describes:
*"A user may hold a role but have adjusted permissions... Dashboard
composition must therefore use: role as a presentation hint, actual
permissions as authority."* The dashboard used neither — it used
nothing at all.

## The fix: permission-gated template sections, not a service rewrite

`dashboard/services.py`'s query set and return shape are **unchanged**
— real existing tests (`test_dashboard_commercial_ops_summary.py`,
`test_phase8_security_fraud_controls.py`) exercise
`get_dashboard_summary()`'s exact current contract, and changing its
signature to accept a permission set was unnecessary risk for a fix
that's correctly scoped at the presentation layer: `dashboard/index.html`
now wraps every card in a `has_permission()`/`has_any_permission()`
check using the same real permission codes `layout/_sidebar.html`
already uses for the equivalent nav destination —

| Dashboard section | Real permission |
|---|---|
| Overview — customer counts | `customers.view` |
| Overview — subscription counts | `subscriptions.view` |
| Commercial operations — notifications, renewals | `subscriptions.view` |
| Commercial operations — pilots | `pilots.view` |
| Commercial operations — pending activations | `pending_activations.view` |
| Commercial operations — emergency extensions | `emergency_extensions.view` |
| Commercial operations — device-slot exceptions | `device_slot_exceptions.view` |
| Subscriptions by product | `subscriptions.view` |
| Licenses by status | `licenses.view` |
| Active installations by product | `installations.view` |
| Latest Owner database backup | `system.view` |
| Recent staff actions (audit) | `audit.view` |

An employee who holds none of the above sees a real, honest empty
state ("your role doesn't include a company-wide overview permission
— use the sidebar") rather than a blank or broken page. Verified
against all five real seeded roles (`staff/seed_data.py`): every one
of SUPER_ADMIN, SALES, SUPPORT, FINANCE, and VIEWER holds at least
`customers.view` or `subscriptions.view`, so the empty state is a real
defensive fallback, not something any current role actually hits.

**What this does not do**: skip the now-unauthorized-to-view queries
inside `get_dashboard_summary()` itself — they still run and their
results are simply discarded by the template for a viewer without the
matching permission. This is correct from a data-exposure standpoint
(nothing unauthorized reaches the browser) but leaves a real, disclosed
efficiency gap: a SALES-only dashboard load still executes the
audit-log and backup-status queries it will never render. Closing that
would mean threading a permission set into the service function and
updating its two existing test files' call sites — reasonable future
work, not done in this pass to keep the fix isolated and low-risk.
Real cost is small (a handful of indexed COUNT queries, not N+1) —
noted for `ui-performance-before-after.md` (Stage E), not treated as
urgent.

## What this does not (yet) build

The governing spec's §14 also describes richer, domain-specific
per-role dashboards — a SALES dashboard showing "assigned Leads,
overdue Follow-ups, open Quotes, active Orders, personal Commission
earnings," a FINANCE dashboard showing "open Invoices, overdue
Invoices, pending Expenses," and so on. **None of that data is
currently computed anywhere** — `get_dashboard_summary()` has no query
against `Lead`, `Quote`, `Order`, `Invoice`, `Commission`, or `Expense`
models at all. Building those widgets means real new aggregation
queries per domain, not a template change, and is real, substantial,
disclosed follow-up work — explicitly not fabricated here. Per the
spec's own "no fake KPI exists" rule, the honest choice this stage is
to correctly scope-and-permission the dashboard data that already
exists rather than invent numbers for data that doesn't.
