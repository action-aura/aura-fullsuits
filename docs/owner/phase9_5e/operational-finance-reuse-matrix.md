# Phase 9.5E Milestone 1 — Reuse Matrix

What Phase 9.5E must reuse as-is, extend, or build fresh. Built from the real audit in [`existing-operational-finance-authority-audit.md`](existing-operational-finance-authority-audit.md), matching the reuse-discipline established every prior phase (e.g. Phase 9.5D reused the entire 9.5A schema foundation, the 9.5C Lead-to-Customer conversion service, the shared idempotency ledger, and the audit-log writer, rather than re-deriving any of them).

| Capability | Status | Action for Phase 9.5E |
|---|---|---|
| `ExpenseCategory` / `Expense` tables | Exists, schema-complete (9.5A) | **Reuse as-is.** No new columns anticipated; build the service layer (lifecycle transitions, approval, payment recording, duplicate/fraud-signal detection) directly on top. |
| `expenses.*` RBAC permissions | Exists, already role-assigned | **Reuse as-is.** SoD shape (SALES creates, FINANCE approves/pays/voids) already matches the maker-checker pattern used throughout Commercial Sales — no redesign needed. |
| `SharedManagementNote` + comment + visibility-grant tables | Exists, schema-complete (9.5A) | **Reuse as-is.** Build the service layer (CRUD, visibility resolution, comment threading, archival) on top. |
| `management_notes.*` RBAC permissions | Exists, **unassigned to any role** | **Fix the gap** (same class as 9.5D M16/M18): decide and grant per role during the RBAC milestone — don't re-define the permissions. |
| `DailyActivitySnapshot` table + metric-shape contract | Exists, schema-complete + structure proven (9.5A M15/M22) | **Reuse the schema and the metric catalog shape as-is.** Build the real `DailySnapshotService.generate()` aggregation this phase — the field names/structure are already specified, don't re-derive them. Must preserve the "no FK into any other table" rule. |
| `reports.*`/`dashboard.*` RBAC permissions | Exists; `view_own`/`view_all` assigned, `regenerate_daily` **unassigned** | **Reuse `view_own`/`view_all` as-is.** Fix the `regenerate_daily` gap when the manual-regenerate action is built. |
| Commercial Sales dashboards (`app/commercial_sales/dashboards.py`) | Exists, commercial-domain-scoped only | **Do not extend in place.** Executive/cross-domain reporting is new scope layered on top of (reading from) Commercial Sales + Expenses + Commissions + CRM data, not a modification of the existing commercial-only dashboard module — matches the reporting "reads everything, nothing reads it back" rule. |
| Idempotency ledger, audit-log writer, RBAC engine, employee-presence, super-admin guard | Exist, domain-agnostic | **Reuse as-is**, exactly as every prior phase has. |
| Payee/vendor entity | Does not exist | **Build fresh.** |
| Secure attachment storage | Does not exist | **Build fresh.** Replaces `Expense.attachment_reference`'s free-text string with a real backing entity/storage path once built — a real, scoped schema change to `Expense`, not new duplicate scope. |
| Cash closing / operational cash control | Does not exist | **Build fresh.** |
| Scheduled report snapshot infrastructure | Does not exist (no scheduler dependency in the project) | **Build fresh** — includes the scheduling mechanism itself (in-process, matching this codebase's existing dependency-light pattern, rather than introducing Celery/APScheduler without cause). |
| Cross-domain executive dashboards | Does not exist | **Build fresh**, reading from (never modifying) Commercial Sales, Expenses, Commissions, CRM, and the daily-snapshot aggregation. |

## Explicit non-duplication commitments

- Expense lifecycle will post through **no new financial-authority chokepoint** beyond what already exists for this domain (there is no `AccountingGateway`-equivalent for Expenses — Expenses are, per the 9.5A docstring, deliberately "never a GL posting," so no gateway needs to be built or reused here).
- Daily/executive reporting will **query existing tables read-only** (Commercial Sales, Commissions, CRM, Expenses) exactly as Phase 9.5D's own dashboards already do for commercial data — never introduce a second source of truth for any figure that already has one.
- Cash closing will **reconcile against existing Payment/Invoice/Refund/Expense records**, not introduce a parallel ledger.
