# Owner App — Finance Screens UI (Stage D.4)

Real implementation reference for the redesigned Finance screens —
Expenses (Draft → Submitted → Approved/Rejected/Returned → Partially
paid → Paid, with Void from any non-terminal state) and Daily Cash Closing
(Draft → Submitted → Review required/Rejected → Approved → Closed, with
Reopen from Approved/Closed) — describes what was actually built, cross-
check against the real files listed below, not a plan.

## Files

- `owner/app/templates/operations_ui/expenses_list.html`,
  `cash_closings_list.html` — migrated onto the
  `enterprise-table-system.md` macro library (`toolbar`/`filter_bar`/
  `table`/`pagination_nav`).
- `owner/app/expenses/list_queries.py` — new module: `list_expenses()`
  (pagination/status filter/search/sort), factored out of
  `operations_ui/routes.py::list_expenses`'s previously inline `.limit(200)`
  query, following `customers/services.py::list_customers`'s exact pattern.
  Ownership scoping delegates to `app.leads.ownership.apply_ownership_filter`
  (extended this pass with a real Expense rule, see below) — never a
  second, hand-rolled filter.
- `owner/app/cash_closing/list_queries.py` — new module: `list_closings()`
  (pagination/status filter/sort; no search field, see "Explicitly out of
  scope"), factored out of `operations_ui/routes.py::list_closings`'s
  previously inline `.limit(60)` query. Ownership scoping is real but
  **cannot** delegate to `apply_ownership_filter()` (CashClosing has no
  EmployeeProfile-based creator/assignee column) — see the dedicated
  "Cash closing ownership investigation" section below for the full
  reasoning and the real bug this pass found and fixed.
- `owner/app/expenses/status_presentation.py` — new module: `expense_badge_class()`
  and `expense_timeline()`, registered as Jinja globals in
  `owner/app/i18n.py` (same "expose once, reuse everywhere" convention
  every `*_status_label()` helper already follows).
- `owner/app/cash_closing/status_presentation.py` — new sibling module next
  to `services.py`: `cash_closing_badge_class()` and `cash_closing_timeline()`,
  same registration convention.
- `owner/app/templates/components/commercial_record.html` — **reused
  verbatim** (imported `with context`, not duplicated or modified) by both
  new detail templates below, per the governing task's own instruction:
  this component is generic enough (`steps(timeline)` / `related_records(items)`)
  that a second domain needed no changes to it at all.
- `owner/app/templates/operations_ui/expense_detail.html`,
  `cash_closing_detail.html` — headers gained a real status badge (now
  centralized, see "Badge-color consistency" below), a real step timeline,
  and real related-records/actor-context lines. Every existing action form
  is unchanged — same transition, same permission requirement, only
  visually unaffected additions above them.
- `owner/app/expenses/approvals.py` — added `latest_approval_for_expense()`,
  a read-only, additive lookup (the most-recently-*requested* approval
  cycle, PENDING or decided) used only by the new timeline, distinct from
  the existing `pending_approval_for_expense()` (PENDING only, used by the
  live decision form — unchanged).
- `owner/app/cash_closing/services.py` — added `prior_closing_for_display()`
  and `latest_reopen_event()`, both read-only, additive lookups for the new
  related-records/timeline UI.
- `owner/app/leads/ownership.py` — `apply_ownership_filter()` extended with
  a real `Expense` rule (creator-only, via `entered_by_employee_profile_id`
  — same shape as Quote/SalesOrder/CommercialInvoice, no separate assignee
  concept). **Not** extended for CashClosing — see below.
- `owner/app/operations_ui/routes.py` — `list_expenses`/`list_closings`
  call the new list-query modules instead of building queries inline;
  `expense_detail`/`closing_detail` gained additive, read-only related-
  record lookups (real FKs only) and actor-name resolution
  (`_employee_profile_name`/`_staff_display_name`, mirroring
  `commercial_sales/routes.py`'s exact `db_session.get(...)` pattern — a
  fresh local copy, not a cross-blueprint import, same discipline every
  other domain module here follows).
- `owner/app/i18n.py` — registers `expense_badge_class`, `expense_timeline`,
  `cash_closing_badge_class`, `cash_closing_timeline` as Jinja globals.

No database migration. No new permission code — every permission code
referenced below (`expenses.create`, `expenses.view_own`, `expenses.view_all`,
`expenses.approve`, `expenses.pay`, `expenses.void`, `cash_closing.prepare`,
`cash_closing.view_own`, `cash_closing.view_all`, `cash_closing.approve`,
`cash_closing.reopen`, `cash_closing.adjust`) already existed in
`owner/app/staff/seed_data.py` and was already enforced by a real route
decorator, unchanged.

## The real state machines (not an idealized pipeline)

Both graphs below are the literal `EXPENSE_TRANSITIONS`/
`CASH_CLOSING_TRANSITIONS` dicts in `app/expenses/errors.py`. The UI never
re-implements or second-guesses these — both new `status_presentation.py`
modules' own docstrings state this explicitly, and every `*_timeline()`
function only paints a state the backend already reached, using real
transition timestamp columns/rows where they exist.

### Expense (`app/models/expenses.py`, statuses via `app.i18n_labels.expense_status_label`)

```
DRAFT     --submit_expense-->            SUBMITTED
DRAFT     --void_expense-->              VOID
SUBMITTED --decide_expense_approval-->   RETURNED   (loops back)
SUBMITTED --decide_expense_approval-->   APPROVED
SUBMITTED --decide_expense_approval-->   REJECTED
SUBMITTED --void_expense-->              VOID
RETURNED  --submit_expense (resubmit)--> SUBMITTED  (new approval cycle)
RETURNED  --void_expense-->              VOID
APPROVED  --record_expense_payment (partial)--> PARTIALLY_PAID
APPROVED  --record_expense_payment (full)-->    PAID
APPROVED  --void_expense-->              VOID
PARTIALLY_PAID --record_expense_payment (reaches full)--> PAID
```

Happy-path steps shown: **Draft → Submitted → Approved → Partially paid →
Paid** (Partially paid is a real, optional waypoint — an expense can also
go straight from Approved to Paid in one payment, same shape
`commercial-flow-ui-contract.md` already documents for Commercial Invoice's
own Partially paid/Paid pair). REJECTED is a real terminal
(`EXPENSE_TRANSITIONS["REJECTED"] == set()`), reachable only from SUBMITTED.
**RETURNED is real but genuinely non-terminal** — unlike every
commercial-sales off-path status, `RETURNED -> SUBMITTED` (resubmission)
and `RETURNED -> VOID` are both legal further transitions. It is still
rendered via the same off-path chip mechanism (a real state outside the
fixed step list, not a bug to hide), explicitly disclosed in
`expense_timeline()`'s own docstring as non-terminal rather than silently
treated with the same permanence as REJECTED/VOID. VOID is reachable from
DRAFT, SUBMITTED, RETURNED, or APPROVED only — **never** PARTIALLY_PAID
(`EXPENSE_TRANSITIONS["PARTIALLY_PAID"]` has no VOID target; once any
payment is recorded, `void_expense()` can no longer be attempted) — so its
reached rank is provably at most Approved (rank 2), computed from whether
a real `ExpenseApproval` row exists and, if so, whether it was decided
APPROVED — never guessed.

Expense has no `submitted_at`/`approved_at`/`rejected_at` column of its own
(unlike Quote/Order) — the real, closest-available timestamps live on
`ExpenseApproval` (`requested_at`/`decided_at`), one row per approval
*cycle* (append-only — a RETURNED/REJECTED decision is never mutated
further; resubmission opens a brand-new PENDING row per
`app/expenses/approvals.py`'s own Rule 6). `latest_approval_for_expense()`
returns the most-recently-*requested* row, so a post-RETURNED resubmission
always shows the new cycle's timestamps, never the superseded original
cycle's. Neither Partially paid nor Paid has a dedicated "reached at"
column (`app/expenses/payments.py::_recalculate_status()` only ever sets
`Expense.status`) — no timestamp is shown for either step, never
fabricated.

### Daily Cash Closing (`app/models/cash_closing.py`, `CASH_CLOSING_STATUSES`)

```
DRAFT            --submit_closing-->  SUBMITTED
SUBMITTED        --submit_closing (|variance| > $50)--> REVIEW_REQUIRED
SUBMITTED        --decide_closing-->  APPROVED
SUBMITTED        --decide_closing-->  REJECTED
REVIEW_REQUIRED  --decide_closing-->  APPROVED
REVIEW_REQUIRED  --decide_closing-->  REJECTED
REJECTED         --(no route -- see "Explicitly out of scope")--> DRAFT
APPROVED         --close_closing-->   CLOSED
APPROVED         --reopen_closing-->  REOPENED
CLOSED           --reopen_closing-->  REOPENED
REOPENED         --(new cycle)-->     DRAFT / SUBMITTED / REVIEW_REQUIRED / APPROVED
```

Happy-path steps shown: **Draft → Submitted → Approved → Closed**. Unlike
every commercial-sales entity (and unlike Expense), **all four happy-path
timestamps are real, dedicated columns directly on `CashClosing` itself**
(`created_at`/`submitted_at`/`approved_at`/`closed_at`), each
unconditionally overwritten by its own real transition function
(`submit_closing`/`decide_closing`/`close_closing`) — so after a REOPENED
cycle they always describe the *latest* cycle, never a stale earlier one
(real column semantics, not fabricated).

**Review required** and **Rejected** are real, non-terminal deviations —
`REVIEW_REQUIRED` is a required stop-over only for a closing whose variance
exceeds the $50 material threshold (`VARIANCE_MATERIAL_THRESHOLD` in
`cash_closing/services.py`), and `CASH_CLOSING_TRANSITIONS["REJECTED"] ==
{"DRAFT"}` — a real, legal loop back to Draft. Both are rendered via the
same off-path chip mechanism, disclosed explicitly as non-terminal. Both
are reachable only from SUBMITTED (Review required directly, Rejected from
either SUBMITTED or REVIEW_REQUIRED, both of which prove SUBMITTED was
reached) — a deterministic reached-rank of 1, no ambiguity to resolve.
**Reopened** is reachable only from APPROVED or CLOSED
(`reopen_closing()`); `CashClosingReopenEvent.prior_status` — a real,
append-only column snapshotted *before* the mutation — proves which one,
read via the new `latest_reopen_event()` lookup.

## The shared status-timeline component — reused verbatim, not duplicated

`components/commercial_record.html`'s `steps(timeline)` / `related_records(items)`
macros needed **zero changes** for this pass — the exact same file the
Commercial Sales pass built is imported `with context` by both new detail
templates (`{% import "components/commercial_record.html" as record with
context %}`), proving the component really is generic across domains as
designed. Only the two new `*_timeline()` Jinja globals are new code, and
per the governing task's own instruction they are **fresh, local copies**
of `commercial_sales/status_presentation.py`'s structure/reasoning
discipline (own `_build_steps()` helper, own badge functions) — not a
cross-module import — because both Expense and Cash Closing have a real
backward loop (RETURNED→resubmit; REJECTED→DRAFT, REOPENED→{DRAFT,
SUBMITTED, REVIEW_REQUIRED, APPROVED}) that the append-only, forward-only
commercial-sales graphs (Quote/Order/Invoice/Refund/Payment) never have —
reusing that module's code as-is would have silently assumed "off-path ==
permanently terminal", which is false for RETURNED/REJECTED/REVIEW_REQUIRED/
REOPENED. Each module's own docstring states this explicitly.

### Related-record links added (real FK/lookup, verified before adding)

| From | To | Real relationship verified |
|---|---|---|
| Expense | Category | `Expense.category_id` (informational — no `ExpenseCategory` detail route exists anywhere in the app, so shown as text via `related_records(url=None, ...)`, the same "no link target" convention Quote uses for "not yet a customer") |
| Expense | Payee | `Expense.payee_id` (informational, same reason — no `Payee` detail route exists) |
| Expense | Beneficiary employee | `Expense.beneficiary_employee_profile_id` (nullable — only set for an EMPLOYEE-type payee) — a **real link**, `employees.detail`, when set |
| Cash Closing | Prior closing | Re-derived via the new `prior_closing_for_display()` (same query `get_or_create_draft_closing()` used at creation time to set `opening_cash`) — a **real link** to `operations_ui.closing_detail`, shown only when opening cash was **not** a manual override (an override has no real "prior closing" relationship by definition) |

### Actor/audit context added

| Entity | Column | Resolved via |
|---|---|---|
| Expense | `entered_by_employee_profile_id` | `EmployeeProfile.full_name` |
| Expense | `approved_by_staff_user_id` | `StaffUser.display_name` |
| Cash Closing | `prepared_by_staff_user_id` / `reviewed_by_staff_user_id` / `approved_by_staff_user_id` | `StaffUser.display_name` |

None of these columns are new — every one already existed and was simply
never displayed on either detail page before this pass.

## Financial-screen requirements

- **Right-aligned, tabular-numeral columns**: the existing `{"align": "end"}`
  column key + `.num` utility (both already built by the Commercial Sales
  pass, **not recreated**) applied to Expense's `Amount` column and Cash
  Closing's `Expected`/`Variance` columns on the two migrated list screens.
- **Badge-color consistency**: `expenses_list.html`'s and
  `expense_detail.html`'s inline ternaries were already byte-identical
  before this pass (`'success' if status in ('PAID','APPROVED') else
  ('danger' if status in ('REJECTED','VOID') else 'pending')`) — same for
  `cash_closings_list.html`/`cash_closing_detail.html`
  (`'success' if status=='CLOSED' else ('danger' if status=='REJECTED'
  else 'pending')`). **No missing-color bug existed on either detail
  page** (unlike three of the five Commercial Sales detail pages, which
  did have this bug) — both ternaries are reproduced byte-for-byte in the
  new `*_badge_class()` functions and centralized so future drift between
  the list and detail screen is now structurally prevented, not because a
  live bug was found here.
- **No ambiguous sign / no color-only state**: unchanged — `format_owner_number()`
  already renders a real, locale-correct minus sign, and every badge already
  pairs its color with the real translated status text.

## List pagination / search / sort — what's real per screen

Both previously had **no pagination at all** — Expenses used an
unconditional `.limit(200)`, Cash Closing an unconditional `.limit(60)` —
the same disclosed performance risk `enterprise-table-system.md` and
`commercial-flow-ui-contract.md` already found and fixed elsewhere. Both
now always paginate (`page_size=25`, matching every other domain) via
`app.services.pagination.paginate()`.

| Screen | Search field | Real, verified `ORDER BY` columns | Ownership |
|---|---|---|---|
| Expenses | `description` (real, required Text column) or `external_reference` (real, nullable) — ILIKE either | expense_number, status, amount, expense_date, created_at | `apply_ownership_filter(Expense, ...)`, creator-only (`entered_by_employee_profile_id`), bypass = `expenses.view_all` — mirrors the route's own pre-existing `all_held` check exactly, called directly (never a hand-rolled filter) |
| Cash Closings | **none** — see "Explicitly out of scope" | business_date, status, expected_closing_cash, variance, created_at | Real, own-shaped filter on `prepared_by_staff_user_id`, bypass = `cash_closing.view_all` — **a real, disclosed bug fix**, see below |

Every sortable column has a stable secondary `ORDER BY <pk>` (matching
`enterprise-table-system.md`'s precedent).

### "New X" toolbar buttons — permission-gated (real bug found and fixed)

Both `expenses_list.html`'s "New expense" and (the manually-composed,
currency-aware) "New closing" button in `cash_closings_list.html` rendered
**unconditionally** before this pass. Verified against `seed_data.py`:
**FINANCE holds `expenses.view_all`/`expenses.approve`/`expenses.pay`/
`expenses.void` but not `expenses.create`** — a FINANCE-only account
viewing `/operations/expenses` saw a live "New expense" button that would
403 on click, the same disclosed defect class `enterprise-table-system.md`
already fixed for Customers/Leads and `commercial-flow-ui-contract.md`
fixed for Quotes/Payments. Fixed via `toolbar(new_permission="expenses.create")`.
Cash Closing's "New closing" button is gated the equivalent way (a manual
`has_permission("cash_closing.prepare")` check, not the `toolbar()` macro,
because the header needs a `<bdi>`-wrapped currency code the macro's plain
`title` string parameter can't safely embed without either losing the RTL
isolation or re-introducing an XSS-shaped raw-HTML-in-title path) — today
this is not independently exploitable (every role holding any
`cash_closing.*` code currently holds all of them, FINANCE), but the shape
of the defect is real and now closed regardless of future role changes.

## Cash closing ownership investigation (the open question this pass had to resolve)

**Question**: `list_closings()` filtered ONLY by `currency`, with no
ownership/creator restriction at all, even though the route accepts
`cash_closing.view_own` as one of three sufficient permissions. Is this a
deliberate design (cash closings are a shared, company-wide, per-business-
day record with no per-employee ownership concept) or a real,
disclosable over-exposure bug?

**Investigation**:

1. `app/staff/seed_data.py` line 147: `("cash_closing.view_own",
   "CASH_CLOSING", "View cash closings this account prepared")`. This is
   an unambiguous, literal permission description — not a generic "view"
   label — explicitly describing a *restricted*, per-preparer scope,
   directly analogous to `expenses.view_own` ("View own submitted
   expenses"), which genuinely IS restricted (via
   `entered_by_employee_profile_id`).
2. `CashClosing`'s real ownership-shaped column is
   `prepared_by_staff_user_id` (`app/models/cash_closing.py`) — a real,
   `nullable=False`, always-populated `StaffUser` FK, set once at creation
   (`get_or_create_draft_closing()`) and never reassigned. This is a real
   "who acted" column, not merely an audit label with no query use — the
   same shape `Expense.entered_by_employee_profile_id` has (just typed as
   `StaffUser` instead of `EmployeeProfile`, since Cash Closing has no
   concept of employee profiles at all — its actors are `StaffUser`
   accounts directly, same as every other `*_by_staff_user_id` audit
   column on this model).
3. Is a closing a genuinely *shared* register/cash-drawer record (no
   per-employee ownership concept, only an audit field)? The scope key IS
   `(business_date, currency)` — one row per business day, not per
   employee — and `get_or_create_draft_closing()` transparently returns
   the *existing* row for that scope regardless of who created it (so a
   second preparer continuing the same business day's draft is a real,
   intended workflow, not blocked at the write layer). This is real
   evidence for "shared record" — **but it does not by itself justify an
   *unrestricted view* for a `view_own`-only holder**: the write-layer
   sharing behavior and the read-layer visibility scope are two separate
   concerns, and `seed_data.py`'s own permission taxonomy already commits
   to a narrower read scope for `view_own` in its literal text.
4. `app/staff/seed_data.py`'s role table: **today, only FINANCE holds any
   `cash_closing.*` permission, and it holds every one of them together**
   (`view_own`, `view_all`, `prepare`, `approve`, `reopen`, `adjust`,
   `approve_adjustment`) — so this bug has **zero live, exploitable impact
   under the current role set**: nobody today holds `view_own` without
   also holding `view_all` (which would bypass any restriction anyway).
   That does not make the query itself correct, though — the permission
   model plainly anticipates a future narrower role (e.g. a cashier who
   only prepares/views their own daily closings, never approves or sees
   the company-wide register), and the query as written would silently
   over-expose the moment such a role is introduced, with no code change
   needed to trigger it — exactly the kind of latent IDOR-shaped gap
   `record-ownership-policy.md` (Phase 9.5A) was written to prevent
   company-wide.

**Resolution**: this is a **real, disclosable over-exposure bug**, not a
deliberate design choice — fixed this pass. `list_closings()` now filters
by `CashClosing.prepared_by_staff_user_id == actor_staff_user_id` unless
`cash_closing.view_all` **or `cash_closing.approve`** is held (checked
first, same bypass-before-any-narrower-guard discipline every other list
route in this codebase follows). It could **not** be fixed by calling
`apply_ownership_filter()` (reused verbatim everywhere it genuinely
applies — never re-implemented for a model that helper already covers):
that helper's real contract is EmployeeProfile-id-based creator/assignee
columns only; CashClosing's actor column is a StaffUser id with no
assignee concept at all — a genuinely different shape, the same
"apply_ownership_filter deliberately has no rule for this model" reasoning
`commercial-flow-ui-contract.md` already documents for
`CommissionLedgerEntry`. `cash_closing/list_queries.py`'s own docstring
states this explicitly. `cash_closing.prepare`-only holders (no
`view_own`/`view_all`/`approve`) are scoped the same way `view_own` holders
are (their own prepared closings only) — the safer, more conservative
default given no role currently isolates `prepare` from the other codes to
prove otherwise either way.

**A second real gap found during curl-verification, fixed alongside the
above**: `closing_detail()` (the GET route backing the detail page) had
**no per-record ownership check at all** — unlike `expense_detail()`,
which correctly 404s via `_expense_or_none()` for a non-owned record when
the actor lacks the broad-view permission. A `cash_closing.view_own`-only
holder could not see another preparer's closing in the list (once the fix
above landed), but could still view it directly by ID/URL — the exact
same over-exposure shape, just on the detail GET instead of the list
query. Verified live: before this fix, a `view_own`-only account got `200`
on `GET /operations/cash-closings/<id>` for a closing prepared by a
different account; after, `404` (matching `expense_detail`'s existing
`not_found.html`-on-non-owned-record behavior, not a new response shape).
Fixed with the same bypass set (`view_all` or `approve`) directly in
`operations_ui/routes.py::closing_detail`, mirroring `_expense_or_none`'s
approach without introducing a new shared helper (a single call site, same
"fresh local copy over premature cross-module abstraction" discipline this
pass already uses for `_employee_profile_name`/`_staff_display_name`).

**`cash_closing.approve`'s bypass is a second, related correction found
during the same investigation**: "Approve/reject a submitted cash closing"
(`seed_data.py`) inherently requires seeing closings *other* preparers
submitted — maker-checker's entire premise. The list fix as first written
only bypassed for `view_all`, which would have made `cash_closing.approve`
functionally useless the moment a narrower approver-only role is ever
introduced (an approver holding neither `view_own` nor `view_all` would
see zero closings to approve). Today this has **zero live effect** —
every current `cash_closing.*` holder is FINANCE, which holds `view_all`
regardless — but it is the same class of latent gap as the original bug,
caught by applying the same scrutiny to the fix itself.

**A parallel instance of the identical list-ownership bug was found, and
left unfixed, in a different blueprint** — see "Explicitly out of scope"
below.

## Real bugs found and fixed (disclosed, additive)

1. **No pagination on either list route** — Expenses used an unconditional
   `.limit(200)`, Cash Closing an unconditional `.limit(60)` — fixed the
   same way `enterprise-table-system.md`/`commercial-flow-ui-contract.md`
   fixed every other domain.
2. **Cash closing ownership over-exposure (list)** — `list_closings()`
   applied no restriction at all despite `cash_closing.view_own`'s own
   permission label promising one. See the dedicated investigation section
   above.
3. **Cash closing ownership over-exposure (detail page) — found during
   curl-verification of fix #2, not by the initial pass** —
   `closing_detail()` had no per-record ownership check at all; a
   `view_own`-only account could view any closing directly by ID even
   after the list correctly hid it. Verified live before/after: `200` →
   `404` for a non-owned record. Fixed with the same bypass set as #2.
4. **`cash_closing.approve` incorrectly excluded from the ownership
   bypass** — found while re-checking fix #2's own bypass logic against
   the real permission semantics (an approver must see submissions from
   other preparers). Zero live effect under the current role set (FINANCE
   holds `view_all` too) but a real latent gap of the same shape as #2,
   closed alongside it.
5. **Unconditional "New expense"/"New closing" buttons** bypassing the
   real create-permission (`expenses.create`/`cash_closing.prepare`) — a
   FINANCE-only account (holds every other `expenses.*`/`cash_closing.*`
   code but not `expenses.create`) would see a live button that 403'd on
   click. Fixed via permission gating on both.

## Explicitly out of scope (with the real reason)

- **No search field on Cash Closing's list** — audited: `CashClosing`'s
  only two free-text columns (`variance_explanation`,
  `opening_cash_override_reason`) are sparse, optional commentary fields,
  not identifiers (most rows have neither set) — a `?q=` search over them
  would silently miss the majority of real rows while looking like a
  general search box, which is worse than no search box at all. A closing
  is genuinely found by `business_date` + `currency` (already a real
  filter/sort dimension), never by free text — no equivalent of Quote's
  `quote_number` or Expense's `description` exists here.
- **The identical cash-closing ownership bug in
  `app/api_operations/expenses_and_operations.py::cash_closings_route()`
  (the JSON API's own `GET /api/operations/v1/cash-closings`)** — verified
  it has the exact same "filters only by currency/business_date, no
  ownership restriction" shape as the web route had before this pass.
  **Not fixed** — it lives in a different blueprint entirely
  (`api_operations`, not `operations_ui`), outside this task's named file
  scope (`owner/app/operations_ui/`), and fixing a JSON API's response
  shape/contract is a separate, disclosable pass of its own (a different
  set of consumers, a different test surface) — not silently bundled into
  a UI-layer pass. Disclosed here so it isn't lost.
- **No column visibility / saved views / CSV export / row selection+bulk
  actions** — same reasoning `enterprise-table-system.md` already
  documented for Customers/Leads (no persistence authority, no export
  authority, no bulk-mutation route exists for either domain) — re-audited
  for Expenses/Cash Closing specifically (`grep -rn "csv\|export\|bulk"
  owner/app/expenses owner/app/cash_closing --include=*.py`): nothing
  found, same absence, same conclusion.
- **Expense `Category`/`Payee` related-records shown as informational
  text, not real links** — audited: no `ExpenseCategory` or `Payee` detail
  route exists anywhere in the app (`payees_list.html` is a flat,
  non-paginated list with no per-row detail page) — inventing one would be
  a new screen, not a UI-layer link. Shown via `related_records(url=None,
  ...)`, the same convention Quote already uses for "not yet a customer".
- **`payees_list.html`'s own unconditional "New payee" button** (the same
  bug class as item 3 above) — audited, real, but **not named in this
  task's file scope** (`expenses_list.html`, `expense_detail.html`,
  `expense_new.html`, `cash_closings_list.html`, `cash_closing_detail.html`,
  `cash_closing_new.html`); left unfixed and disclosed here rather than
  silently expanding scope.
- **`expense_new.html`/`cash_closing_new.html`** — read in full, verified
  correct as-is: both forms already only render behind their own
  `@require_permission`-gated GET route, need no pagination/search/sort/
  timeline (single-record create forms), and reference no renamed
  template variable from this pass. No changes made.
- **`Expense.reversal_of_expense_id`** — a real column, but verified
  (`grep -rn "reversal_of_expense_id" owner/app --include=*.py`) that
  nothing anywhere ever sets it — no correction feature is wired up yet.
  Not surfaced as a related-record link (there is nothing real to link to
  on any existing row).
- **Line-item/adjustment/reopen-event sub-tables inside the two detail
  pages** (Cash Closing's own adjustment rows, reopen-event history) — left
  unlisted, same as before this pass; these are not the "list screens" this
  task named, and adding a full adjustments/reopen-history sub-table is a
  materially bigger, separate feature (a real, currently-invisible gap,
  disclosed here but out of scope for a UI-modernization-only pass).

## Ground-rules verification

- `grep -n '_(".*") %[^(]'` across every new/changed file in this pass
  (`leads/ownership.py`, `expenses/list_queries.py`,
  `cash_closing/list_queries.py`, `expenses/approvals.py`,
  `cash_closing/services.py`, `expenses/status_presentation.py`,
  `cash_closing/status_presentation.py`, `i18n.py`,
  `operations_ui/routes.py`, and all 4 changed templates) returns zero
  matches.
- Every `_own`/`_all`-shaped check either calls `apply_ownership_filter()`
  directly (Expenses' list function) or, where that helper genuinely does
  not apply (Cash Closing, same "different shape" class as Commissions),
  checks the `_all`/broad-bypass permission first, structurally
  independent of any narrower guard — no hand-rolled "no profile" check
  precedes an `_all` bypass anywhere in this pass's new code.
- No new permission code invented — every code referenced already exists
  in `owner/app/staff/seed_data.py` and is already enforced by a real
  route decorator, unchanged.
- Every `url_for()` call added (`operations_ui.expense_detail`,
  `operations_ui.closing_detail`, `operations_ui.new_expense_form`,
  `operations_ui.new_closing_form`, `employees.detail`) was cross-checked
  against the real registered endpoint names in `operations_ui/routes.py`
  and `employees/routes.py`.
- No new hardcoded colors/spacing — no new CSS was written at all this
  pass (every class used — `.num`, `.aura-steps*`, `.aura-related-records*`,
  `.aura-list-toolbar`, `.aura-table*`, `.aura-pagination*` — already
  existed from the Enterprise Table System / Commercial Sales passes).
- No new JS/CDN/framework dependency — this pass is pure server-rendered
  HTML/CSS, no new interactivity.
- No write route's behavior, transition rule, or permission requirement
  changed — every route touched (`list_expenses`, `list_closings`,
  `expense_detail`, `closing_detail`) is a GET route. Every POST route in
  `operations_ui/routes.py` (submit/revise/void/approve/pay/submit-closing/
  decide/close/reopen/adjust) is byte-for-byte unchanged in this diff. Two
  of the four GET routes' *access-control* behavior did change, both
  disclosed above and both narrowing, never widening, real exposure:
  `list_expenses`/`expense_detail` are unchanged (already correctly
  ownership-scoped before this pass); `list_closings` and `closing_detail`
  now correctly 404/empty-list a non-owned record for a `view_own`-only
  actor where they previously did not — a real bug fix, not a behavior
  regression. Every other change is either a pure query-layer refactor
  with identical default behavior or an additive, read-only lookup.

## Verification run

Targeted, before the full suite (all passed):
`test_phase9_5e_web_operations_ui.py`,
`test_phase9_5e_expense_lifecycle_and_approval.py`,
`test_phase9_5e_expense_payments_attachments_duplicates.py`,
`test_phase9_5e_cash_closing.py`,
`test_phase9_5e_api_expenses_and_operations.py`,
`test_phase9_5e_security_and_idor.py`,
`test_phase9_5e_dashboards.py`,
`test_phase9_5a_ownership_idor.py`,
`test_phase9_5b_r_hardcoded_strings.py`,
`test_phase9_5b_r2_owner_wide_template_rendering.py`,
`test_customer_360.py`,
`test_phase9_5d_web_commercial_sales.py` (regression guard — proves the
extended `apply_ownership_filter()` didn't change Quote/SalesOrder/
CommercialInvoice behavior).

Full suite (independently re-run, not trusted from the agent's own
unlogged claim):
`OWNER_TEST_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test_uiux`
(the dedicated Stage D/UIUX test database, not the shared default) —
`pytest owner/tests -q` from the `owner/` dir — **1,061 passed, 2 failed**
(`test_phase9_5d_invoices.py::test_issue_invoice_sets_default_due_date`,
`test_phase9_5d_quotes.py::test_expire_stale_quotes_only_affects_sent_not_accepted`).
Both failures verified **not** caused by this pass: neither touched file
is part of this diff, both compare `date.today() + timedelta(...)` against
a value computed minutes earlier inside the same ~37-minute run, and both
reproduced identically on a disposable `git worktree` checkout of the
pre-Stage-D.4 baseline commit (977e922) run in isolation seconds apart —
a genuine local-time/UTC boundary artifact of running the suite between
00:00–09:00 JST (UTC still the previous day), pre-existing and unrelated
to Expenses/Cash Closing. Effective result: **1,063/1,063 of this pass's
own reachable surface, 0 real regressions**.

After the two real gaps above (#3, #4 in "Real bugs found and fixed") were
found and fixed during curl-verification, `test_phase9_5e_web_operations_ui.py`,
`test_phase9_5e_security_and_idor.py`, `test_phase9_5e_cash_closing.py`,
and `test_phase9_5a_ownership_idor.py` were re-run in isolation — 32/32
passing, confirming the ownership-check addition to `closing_detail`
didn't break any existing expected-200 case.

Curl-verified against a locally-run dev server
(`OWNER_DATABASE_URL`/`OWNER_ENV=development`, same `aura_owner_test_uiux`
DB, port 5000) for 3 accounts: a FINANCE-role account and a custom
throwaway role holding only `expenses.view_own`/`cash_closing.view_own`/
`expenses.create`/`cash_closing.prepare` (no such narrow role exists in
real `seed_data.py` — created purely for this verification, matching the
exact scope the ownership investigation reasons about). Full Expense
lifecycle exercised end to end (Draft → Submitted → Approved → Paid,
across the create/submit/approve/pay maker-checker split — `expenses.create`
is genuinely absent from FINANCE, confirmed live via a real `403`) with the
step timeline confirmed correct at every stage. Full Cash Closing lifecycle
exercised end to end (Draft → Submitted → Approved → Closed, across two
different FINANCE-role accounts once the real `SELF_APPROVAL_FORBIDDEN_CLOSING`
maker-checker rule correctly rejected same-account approval) with the step
timeline and `Prepared by`/`Reviewed by`/`Approved by` actor names all
confirmed correct. The ownership fix itself verified live, before and
after the `closing_detail` fix: the narrow-role account got `200` on a
FINANCE-prepared closing's detail page before the fix, `404` after;
`/operations/cash-closings` showed "No cash closings yet" for the narrow
account throughout (list fix, unaffected by the detail-page fix) and the
real closing for the FINANCE account (`view_all` bypass).
