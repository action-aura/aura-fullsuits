# Owner App — Commercial Sales Flow UI (Stage D)

Real implementation reference for the redesigned commercial-sales screens
(Quote → Approval → Order → Invoice → Payment → Allocation → Commission →
Fulfillment → Refund → Commission Reversal) — describes what was actually
built, cross-check against the real files listed below, not a plan.

## Files

- `owner/app/templates/commercial_sales/quotes_list.html`, `orders_list.html`,
  `invoices_list.html`, `payments_list.html`, `refunds_list.html`,
  `commissions_list.html`, `payout_batches_list.html` — migrated onto the
  `enterprise-table-system.md` macro library (`toolbar`/`filter_bar`/
  `table`/`pagination_nav`).
- `owner/app/commercial_sales/list_queries.py` — new module: the seven
  list-query service functions (pagination/status filter/search/sort),
  factored out of `commercial_sales/routes.py`'s previously inline
  `.limit(200)` queries, following `customers/services.py::list_customers`'s
  exact pattern.
- `owner/app/commercial_sales/status_presentation.py` — new module: shared
  badge-color mapping (`quote_badge_class`, etc.) and the real per-entity
  status-step-timeline builders (`quote_timeline`, `order_timeline`,
  `invoice_timeline`, `refund_timeline`, `payment_timeline`), registered as
  Jinja globals in `owner/app/i18n.py` (same "expose once, reuse everywhere"
  convention every `*_status_label()` helper already follows).
- `owner/app/templates/components/commercial_record.html` — new macro file:
  `steps(timeline)` (the status/step visualization) and
  `related_records(items)` (upstream/downstream document links), imported
  `with context` by the five detail templates.
- `owner/app/templates/components/table.html` — extended with an optional
  `{"align": "end"}` column key (adds the new `.num` utility class to a
  `<th>`; `components.css`'s `table.aura-table th.num, td.num` rule does the
  actual right-align + tabular-nums).
- `owner/app/templates/commercial_sales/quote_detail.html`, `order_detail.html`,
  `invoice_detail.html`, `payment_detail.html`, `refund_detail.html` —
  redesigned headers (status badge + step timeline + related records +
  actor/audit context), action forms unchanged in *which* transition they
  offer, only made visually consistent (`.btn` primary / `.secondary` /
  `.btn.danger`, already-established classes).
- `owner/app/commercial_sales/routes.py` — the seven list routes call
  `list_queries.*` instead of building queries inline; the five detail
  routes gained additive, read-only related-record lookups (real FKs only)
  and actor-name resolution (`_employee_profile_name`/`_staff_display_name`,
  mirroring `customers/routes.py::detail`'s exact `db_session.get(StaffUser,
  ...)` pattern for "Assigned employee").
- `owner/app/static/css/components.css` — `.num` utility (financial-column
  right-align + tabular-nums) and the `/* Commercial-record status timeline
  */` section (`.aura-steps*`, `.aura-related-records*`), both token-only.

## The real state machines (not an idealized pipeline)

Every graph below is the literal `*_TRANSITIONS` dict in
`app/commercial_sales/errors.py` (Quote/Order/Invoice/Refund) or, for
Payment (which predates that convention), the literal status checks inside
`app/commercial_sales/payments.py`'s own functions. The UI never
re-implements or second-guesses these — `status_presentation.py`'s own
module docstring states this explicitly, and every `*_timeline()` function
only paints a state the backend already reached, using real transition
timestamp columns where they exist.

### Quote (`app/models/commercial_sales.py::QUOTE_STATUSES`)

```
DRAFT --submit_quote--> SENT --record_customer_decision(accepted)--> ACCEPTED
DRAFT --cancel_quote--> CANCELLED
SENT  --cancel_quote--> CANCELLED
SENT  --record_customer_decision(rejected)--> REJECTED
SENT  --expire_stale_quotes (scheduled)--> EXPIRED
```

Happy-path steps shown: **Draft → Sent → Accepted**. ACCEPTED/REJECTED/
EXPIRED/CANCELLED are all real terminal states (`QUOTE_TRANSITIONS[status]
== set()`) — REJECTED/CANCELLED/EXPIRED render as a distinct off-path chip
appended after whichever happy-path steps their own real timestamp columns
(`sent_at`/`rejected_at`/`cancelled_at`) prove were actually reached (EXPIRED
has no dedicated timestamp column — `quote.updated_at` is used, since
`expire_stale_quotes()` never has anything transition out of EXPIRED
afterward to overwrite it).

### Sales Order (`SALES_ORDER_STATUSES`)

```
DRAFT --confirm_order--> CONFIRMED --fulfill_order--> FULFILLED
DRAFT      --cancel_order--> CANCELLED
CONFIRMED  --cancel_order--> CANCELLED
```

Happy-path: **Draft → Confirmed → Fulfilled**. CANCELLED is the one real
off-path terminal, using `order.confirmed_at` to tell whether it was
cancelled from DRAFT or CONFIRMED.

### Commercial Invoice (`INVOICE_STATUSES`)

```
DRAFT   --issue_invoice--> ISSUED --void_invoice--> VOID
DRAFT   --void_invoice--> VOID
ISSUED  --allocate_payment (partial)--> PARTIALLY_PAID
ISSUED  --allocate_payment (full)--> PAID
PARTIALLY_PAID --allocate_payment (reaches full)--> PAID
PARTIALLY_PAID --confirm_refund (partial)--> PARTIALLY_REFUNDED
PAID    --confirm_refund (partial)--> PARTIALLY_REFUNDED
PAID    --confirm_refund (full)--> REFUNDED
```

Happy-path: **Draft → Issued → Partially paid → Paid**. Neither
PARTIALLY_PAID nor PAID has a dedicated "reached at" timestamp column (both
are set by `app/commercial_sales/allocation.py::allocate_payment`, which
only bumps `status`/`version`) — the step macro shows their done/current/
upcoming state from the current status's rank only, never a fabricated
timestamp. VOID is only reachable from DRAFT/ISSUED (never once money has
been allocated — `void_invoice()` itself rejects otherwise).
PARTIALLY_REFUNDED/REFUNDED are real terminals reachable only from PAID or
PARTIALLY_PAID (`confirm_refund`'s own logic) — proven, not guessed, from
the transition graph, so the step list correctly shows Issued (and,
implicitly, at least Partially paid) as done before the refunded chip.

### Commercial Refund (`REFUND_STATUSES`)

```
DRAFT --approve_refund--> APPROVED --confirm_refund--> PAID
DRAFT     --void_refund--> VOID
APPROVED  --void_refund--> VOID
```

Happy-path: **Draft → Approved → Paid**. VOID is the one off-path terminal
(real `voided_at` column).

### Payment (`PaymentRecord`, `app/subscriptions/services.py::PAYMENT_STATUSES`)

No `*_TRANSITIONS` dict exists for this entity (it predates that
convention) — the real transitions are the literal checks in
`app/commercial_sales/payments.py`:

```
PENDING --confirm_payment--> CONFIRMED
PENDING --reject_payment--> FAILED
```

Happy-path: **Pending → Confirmed** (no dedicated "confirmed at" column
either — only `status` + `verified_by_staff_user_id` + a correction-history
row, so no timestamp is shown for that step). FAILED is the one off-path
terminal reachable via any current web route. `REFUNDED`/`VOIDED` are real
enum values in `PAYMENT_STATUSES` but — verified by reading every caller —
no `commercial_sales_web` route currently sets either; `payment_timeline()`
handles them as an honest "unmapped, nothing assumed reached" case rather
than guessing, should a future code path ever reach them.

## The shared status-timeline component

`components/commercial_record.html`'s `steps(timeline)` macro renders the
`{"steps": [...], "offpath": {...}|None}` dict a `*_timeline()` Jinja
global returns for the entity in question (`{{ record.steps(quote_timeline(quote)) }}`,
etc.). Each happy-path step shows done (✓, green)/current (blue,
bold)/upcoming (gray) plus its real timestamp when one exists; a real
off-path terminal renders as a separate badge-colored chip after the steps,
never smashed into the linear list as if it were just another normal step.
This satisfies the governing spec's own instruction almost verbatim: *"a
Cancelled/Rejected quote... is a real terminal state outside the happy
path, not a bug to hide."*

`related_records(items)` renders upstream/downstream document links —
every relationship is a real, verified FK, checked one at a time below.

### Related-record links added (real FK, verified before adding)

| From | To | Real FK verified |
|---|---|---|
| Quote | Customer | `Quote.customer_id` (nullable — lead-based quotes show "not yet a customer" instead of a broken link) |
| Quote | Sales order | `SalesOrder.quote_id` (queried non-CANCELLED) — **new**, not shown before this pass |
| Order | Customer | `SalesOrder.customer_id` |
| Order | Originating quote | `SalesOrder.quote_id` — **new** |
| Order | Invoice | `CommercialInvoice.sales_order_id` (already shown before this pass, kept) |
| Invoice | Customer | `CommercialInvoice.customer_id` — **new** |
| Invoice | Sales order | `CommercialInvoice.sales_order_id` — **new** |
| Invoice | Payments (via allocations) | `PaymentAllocation.commercial_invoice_id` (already shown before this pass, kept) |
| Payment | Customer | `PaymentRecord.customer_id` — **new** |
| Payment | Invoices funded | `PaymentAllocation.payment_record_id` — **new**, a real downstream relationship that had no UI surface anywhere before this pass |
| Refund | Customer | via `CommercialInvoice.customer_id` (refund has no direct customer FK) — **new** |
| Refund | Invoice | `CommercialRefund.commercial_invoice_id` — **new**, refund_detail.html had **zero** related-record links before this pass |
| Refund | Source payment | `CommercialRefund.payment_record_id` (nullable) — **new** |

### Actor/audit context added

| Entity | Column | Resolved via |
|---|---|---|
| Quote/Order/Invoice/Refund | `created_by_employee_profile_id` | `EmployeeProfile.full_name` (real, `nullable=False` column — no second join to `StaffUser` needed) |
| Refund | `approved_by_staff_user_id` | `StaffUser.display_name` (same `db_session.get(StaffUser, ...)` pattern `customers/routes.py::detail` already used for "Assigned employee") |
| Payment | `recorded_by_staff_user_id`, `verified_by_staff_user_id` | `StaffUser.display_name` |

None of these columns are new — every one already existed and was simply
never displayed.

## Financial-screen requirements

- **Right-aligned, tabular-numeral columns**: `table()`'s new optional
  `{"align": "end"}` column key + the new `table.aura-table th.num, td.num
  { text-align: end; font-variant-numeric: tabular-nums; }` rule
  (`components.css`) — applied to every real money/quantity column on the
  seven migrated list screens (Quote/Order/Invoice `Total`, Payment
  `Amount`, Refund `Amount`, Commission `Amount`). `text-align: end` (a
  logical property, not `right`) so the column still right-aligns correctly
  under RTL. No prior table-cell numeral utility existed — the only
  pre-existing `tabular-nums` rule was `.aura-summary-tile__value`, a KPI
  tile, not a table cell (confirmed by reading `typography-contract.md`
  and `components.css` directly).
- **Paid/unpaid/partial status**: unchanged, still badge + the existing
  `*_status_label()` text — never color alone.
- **No ambiguous sign / no color-only state**: unchanged from before this
  pass — `format_owner_number()` (Babel `format_decimal`) already renders a
  real, locale-correct minus sign for negative amounts (e.g. a reversed
  commission ledger entry), and every badge already pairs its color with
  the real translated status text via `*_status_label()`. Verified none of
  that text was stripped during migration.
- **Badge-color consistency (real bug found and fixed)**: `quote_detail.html`,
  `order_detail.html`, and `invoice_detail.html` rendered their header
  status with a bare, uncolored `<span class="badge">` — losing the exact
  color information the corresponding list screen already showed one click
  away. `payment_detail.html`'s own inline ternary was missing the
  `danger` (FAILED/VOIDED) branch its list screen already had. Fixed by
  centralizing every badge-color mapping in `status_presentation.py`,
  reproducing each list screen's existing ternary byte-for-byte — no color
  assignment changed, only shared instead of duplicated (and, for the
  three pages that had none, applied for the first time).

## List pagination / search / sort — what's real per screen

All seven previously had **no pagination at all** — an unconditional
`.limit(200)`, the same disclosed performance risk
`enterprise-table-system.md` found and fixed for Customers. Every list
below now always paginates (`page_size=25`, matching Customers/Leads/
Employees) via `app.services.pagination.paginate()`.

| Screen | Search field | Real, verified `ORDER BY` columns | Ownership |
|---|---|---|---|
| Quotes | `quote_number` (ILIKE) | number, status, total, created_at | `apply_ownership_filter(Quote, ...)`, creator-only, bypass = `quotes.approve` — mirrors `list_quotes`'s own `_own_or_all()` rule exactly, called directly (never a hand-rolled check) |
| Sales orders | `order_number` | number, status, total, created_at | `apply_ownership_filter(SalesOrder, ...)`, creator-only, bypass = `orders.approve` |
| Invoices | `invoice_number` | number, status, total, due_date, created_at | `apply_ownership_filter(CommercialInvoice, ...)`, creator-only, bypass = `invoices.issue` |
| Payments | `reference` (only other real free-text column) | amount, status, payment_date, created_at | none — verified: `list_payments` applies no ownership filter, company-wide once `payments.view` is held (unchanged) |
| Refunds | `reason` (CommercialRefund has no document-number column) | amount, status, created_at | none — verified: `list_refunds` applies no ownership filter |
| Commissions | none (no real free-text field on `CommissionLedgerEntry`) | amount, status, earned_at, created_at | `commissions.view_own`/`commissions.view_all` — a genuinely different, permission-based shape (not creator/assignee), `apply_ownership_filter` deliberately has no rule for this model; reproduces the route's pre-existing filter exactly, `view_all` checked *before* the "no profile" branch |
| Commission payout batches | `batch_reference` | reference, status, period_start, created_at | none (page itself gated `commissions.pay`) |

Every sortable column has a stable secondary `ORDER BY <pk>` (matching
`enterprise-table-system.md`'s Customers/Leads precedent) so two rows
sharing an identical sort value never have an unstable relative page
position.

### "New X" toolbar buttons — permission-gated (real bug fixed)

`quotes_list.html`'s "New quote" and `payments_list.html`'s "Record
payment" both rendered unconditionally before this pass — a viewer holding
only `quotes.approve` (not `quotes.create`) or only `payments.view` (not
`payments.create`) saw a live button that 403'd on click, the exact same
disclosed defect `enterprise-table-system.md` already fixed for Customers/
Leads. Now gated via `toolbar(new_permission=...)`. Orders/Invoices/Refunds/
Commissions correctly have **no** "New X" button (verified: no standalone
create route exists for any of them — orders only via
`/orders/from-quote/<quote_id>`, invoices only via `/orders/<id>/invoice`,
refunds only via `/invoices/<id>/refunds/new`, commission entries are
system-generated) — unchanged from before, not newly hidden.

## Real bugs found and fixed (disclosed, additive)

1. **No pagination on any of the 7 list routes** (see table above) —
   fixed the same way `enterprise-table-system.md` fixed Customers.
2. **Unconditional "New quote"/"Record payment" buttons** bypassing the
   real create-route's permission — fixed via `toolbar(new_permission=...)`.
3. **Detail-page status badges missing color** (Quote/Order/Invoice) or an
   incomplete color branch (Payment) — fixed via the shared
   `status_presentation.py` badge functions.
4. **`quote_detail.html`'s "Create sales order" button stayed visible after
   an order already existed** for an ACCEPTED quote — clicking it would hit
   `create_order_from_quote`'s real "one active order per accepted quote"
   rule (`IDEMPOTENCY_CONFLICT`) and fail. The route now fetches the real
   order (if any, non-CANCELLED) and the template only shows the button
   when `not order` — the same "don't suggest an invalid transition" class
   of fix as bug #2, just triggered by a business rule instead of a
   permission gate.
5. **`payout_batches_list.html`'s "Approved commission entries awaiting
   payout" sub-table looped over the *same* (now paginated) batches list**
   to find approve-batch targets for its "Pay via" buttons — a batch beyond
   page 1 would silently lose its payout button. Fixed with a separate,
   dedicated `approved_batches` query (bounded 200, unconditional — the
   entries-awaiting-payout list is itself already bounded/unpaginated, same
   reasoning below), independent of the primary paginated table.

## Explicitly out of scope (with the real reason)

- **Pagination on the `approved_entries`/`approved_batches` embedded lists
  inside `payout_batches_list.html`** — these back a same-page "pay via
  batch" action form, not the primary browsable list; a second,
  separately-namespaced `?page=` control on one page was judged more
  confusing than useful at current real data volumes, same reasoning
  `customer_360.py`'s bounded-not-paginated tabs already used.
- **Line-item/allocation/approval/refund sub-tables inside the five detail
  pages** (Quote Lines, Order Lines, Invoice Lines, Payment allocations,
  Pricing approvals) — left on the pre-existing `.responsive-table`
  mechanism, unmigrated. These are not the "list screens" item 1 of the
  governing task named, they have no pagination/search/sort dimension of
  their own (a single document's own line items, always small and
  bounded), and mixing `.responsive-table` mobile-stacking with
  `.aura-table`'s sticky-header/sort-link mechanism on the same page was
  already ruled out by `enterprise-table-system.md`'s own "Responsive-mode
  decision" section.
- **Right-aligned/tabular-numeral styling on those same sub-tables** — same
  reason: they stay on `.responsive-table`, whose mobile stacked-block
  layout doesn't have a "column" for `text-align: end` to apply to
  meaningfully.
- **Commission payout batches / commission entries getting their own
  shared status-timeline header** — the governing task's 5 named detail
  screens are Quote/Order/Invoice/Payment/Refund; Commissions and Payout
  Batches are list screens only in this pass (no single-record detail page
  exists for either today).

## Ground-rules verification

- `grep -n '_(".*") %[^(]'` across every new/changed file in this pass
  (`status_presentation.py`, `list_queries.py`, `routes.py`, all 12 changed
  templates, `commercial_record.html`) returns zero matches.
- Every `_own`/`_all`-shaped check calls `apply_ownership_filter()` directly
  (Quotes/Orders/Invoices' list functions) — no hand-rolled "no profile"
  guard precedes the `_all` bypass anywhere in `list_queries.py`;
  `list_commissions`'s own different (permission-based) shape checks
  `view_all` before its "no profile → empty result" branch, matching the
  same ordering discipline.
- No new permission code invented — every code referenced (`quotes.create`,
  `quotes.approve`, `orders.create`, `orders.approve`, `invoices.create`,
  `invoices.issue`, `payments.view`, `payments.create`, `payments.confirm`,
  `refunds.create`, `refunds.approve`, `commissions.view_own`,
  `commissions.view_all`, `commissions.approve`, `commissions.pay`) already
  exists in `owner/app/staff/seed_data.py` and is already enforced by a
  real route decorator, unchanged.
- Every `url_for()` call added was cross-checked against the real
  registered endpoint names in `commercial_sales/routes.py` (see the
  endpoint-inventory diff performed during this pass — zero references to a
  non-existent endpoint) plus `customers.detail`.
- No new hardcoded colors/spacing — every new rule in `components.css`
  uses `var(--aura-*)` tokens exclusively.
- No new JS/CDN/framework dependency — the entire status-step component is
  pure server-rendered HTML/CSS, no interactivity needed.
- No write route's behavior, transition rule, or permission requirement
  changed — every route touched only gained additive, read-only lookups
  (related records, actor names) or had its list query factored into a
  service function with identical default behavior; verified against the
  real diff before considering this done.

## Verification run

Targeted, before the full suite (all passed):
`test_phase9_5d_web_commercial_sales.py` (5),
`test_phase9_5d_commercial_sales_rbac.py`,
`test_phase9_5b_r_hardcoded_strings.py`,
`test_phase9_5b_r2_owner_wide_template_rendering.py`,
`test_phase9_5d_commission_ledger.py`,
`test_phase9_5d_commission_management.py`,
`test_customer_360.py`, `test_phase9_5a_commission_preview.py`,
`test_phase9_5d_api_commercial_sales.py` — 43 + 12 = 55 tests, all passed.

A throwaway smoke test (not committed) additionally exercised: the full
Quote→Order→Invoice→Payment→Allocation→Refund happy path rendering every
detail page end to end (including the new related-record links and the
"Invoices funded by this payment" section); an off-path CANCELLED quote
(from DRAFT, never sent) and an off-path CANCELLED order (from DRAFT);
and a PARTIALLY_REFUNDED invoice (real off-path terminal, confirmed
rendering correctly with the refunded chip appended after Issued). All
real GET screens returned 200 with the expected off-path/related-record
content present. Deleted after use, per this phase's standing "don't leave
extra files" discipline — the full pytest suite's exact pass/fail counts
are reported in the phase completion summary against
`OWNER_TEST_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test_uiux`
(the dedicated Stage D/UIUX test database, not the shared default).
