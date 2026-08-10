# Phase 9.5E Milestone 1 — Duplication Risk Report

Real risks identified by the audit, each with the concrete mitigation Phase 9.5E will apply. Same discipline as Phase 9.5D's own M1 duplication-risk report — named risks with a decided mitigation, not a generic checklist.

## Risk 1 — Re-deriving the Expense/Note/Snapshot schema from scratch

**Risk:** Building Phase 9.5E service modules against a freshly-imagined schema instead of the real 9.5A tables would silently fork the data model (two competing definitions of "an expense"), and any such fork would not be caught by the schema-drift tests until integration.

**Mitigation:** Milestone 1 (this audit) read the actual model files (`app/models/expenses.py`, `app/models/management_notes.py`, `app/models/daily_reports.py`) before any service code is written. Every Phase 9.5E milestone that touches these domains must import and extend these exact models, not redefine them.

## Risk 2 — A second "who can approve this" authority alongside the existing RBAC engine

**Risk:** Expense approval, cash-closing sign-off, and management-note visibility all sound like they could motivate a bespoke permission-check helper, duplicating the RBAC engine every prior phase has used (`app/security/`, `rbac.get_staff_permission_codes`, the `@require_permission`-style route guards).

**Mitigation:** All Phase 9.5E authorization decisions route through the existing RBAC engine and the existing `expenses.*`/`management_notes.*`/`reports.*` permission codes (extended with new codes for cash-closing/payees/attachments/scheduled-reports as needed) — no parallel authorization mechanism.

## Risk 3 — A second commission/financial ledger for expense payments

**Risk:** `Expense.status` includes `PAID`, which could tempt building an expense-payment-ledger parallel to the Commission Ledger (`app/commissions/ledger.py`) or the Commercial Sales payment/allocation machinery.

**Mitigation:** Per the 9.5A model docstring, Expenses are explicitly "never a GL posting" — a flat operational record. Marking an expense `PAID` is a status transition with an audit trail, not a posting into any ledger. No new ledger-shaped abstraction should be introduced for Expenses; if a future phase needs real GL posting, that is explicitly out of Phase 9.5D's forbidden scope and remains out of Phase 9.5E's scope too unless the (still-truncated) governing spec says otherwise.

## Risk 4 — Reporting tables gaining a foreign-key dependency, breaking the 9.5A architectural rule

**Risk:** It would be natural, while building Daily Closing / Executive Reporting, to want `DailyActivitySnapshot` (or a new `ScheduledReportSnapshot`) to be referenced by another table (e.g. a cash-closing record pointing at "the daily snapshot that closed this day"), which would violate the explicit "no FK into any other table — Reporting reads everything, nothing reads it back" rule from `docs/owner/phase9_5a/domain-dependency-rules.md`.

**Mitigation:** Any linkage between Cash Closing and the daily snapshot must be expressed as the closing record storing the `business_date` (already a plain, non-FK value) and re-deriving/re-querying the snapshot at read time — never a hard foreign key into the reporting table. This constraint should be restated explicitly in the Milestone 2 (canonical operational-finance funnel / metric definitions) contract doc so it isn't silently violated three milestones later.

## Risk 5 — Re-granting permissions that already exist but were never wired to a role

**Risk:** Not knowing `management_notes.view/manage` and `reports.regenerate_daily` already exist (just unassigned) could lead to defining *new*, differently-named permission codes for the same capability, leaving the original 9.5A codes as permanent dead weight.

**Mitigation:** Documented explicitly in the reuse matrix. The RBAC milestone must grant the *existing* codes to the appropriate roles, not invent new ones.

## Risk 6 — Building a bespoke scheduler when the codebase has deliberately stayed dependency-light

**Risk:** "Scheduled Report Snapshots" could motivate pulling in Celery/APScheduler/a Windows Task Scheduler entry (mirroring `.autosync`'s own approach) without checking whether that fits this codebase's established pattern.

**Mitigation:** No scheduler dependency exists in `requirements*.txt` today, and every prior phase in this codebase has avoided adding dependencies without a demonstrated need (Phase 9.5D M29 explicitly noted "no new dependencies this phase" as a positive outcome). The scheduling milestone should default to an in-process, CLI-triggerable mechanism (matching `reports.regenerate_daily`'s already-anticipated manual-trigger shape) unless the still-truncated governing spec explicitly calls for OS-level scheduling — a decision to confirm with the user before adding any new dependency or OS-level scheduled task, given `.autosync` has just shown what an OS-level scheduled task's blast radius looks like on this machine.
