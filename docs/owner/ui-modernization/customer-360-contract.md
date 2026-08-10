# Owner App -- Customer 360 Profile (Stage D)

Real implementation reference for the redesigned Customer detail screen --
describes what was actually built, cross-check against the real files
listed below, not a plan.

## Files

- `owner/app/templates/customers/detail.html` -- the redesigned template:
  header + real ARIA tabs. Imports `components/table.html` `with context`
  (see `enterprise-table-system.md`) for the seven new bounded list tabs.
- `owner/app/customers/customer_360.py` -- new module: every read-only,
  bounded, permission-gated cross-domain query behind the new tabs, plus
  the Timeline/commercial-summary builders. Full module docstring there
  documents the ownership/permission mapping in detail; this file
  summarizes it.
- `owner/app/customers/routes.py::detail` -- the only route that changed.
  Gains the new read-only `customer_360.*` calls and two small derived
  values (`next_followup`, `open_quote`) for the Overview tab. No other
  route in this file changed (archive/assign/add_contact/add_interaction/
  add_note/add_followup/add_location/verify_location all untouched).
- `owner/app/static/js/tabs.js` -- generic ARIA-tabs progressive
  enhancement, wired into `layout/base.html`'s script list (unconditional,
  same as `table-density.js` -- a no-op on any page without `[data-tabs]`).
- `owner/app/static/css/components.css` -- `/* Customer 360 tabs (Stage D)
  */` section appended at the end of the file (same house style as the
  Enterprise Table System/Command Palette sections above it).
- `owner/tests/test_customer_360.py` -- new tests: tab visibility per real
  permission set (super admin / VIEWER / SALES), real license/subscription
  data rendering, real Timeline event presence.

## Header

All real data, no fabricated fields:

| Header item | Real source |
|---|---|
| Name / status | `customer.legal_name`, `customer_status_label(customer.lifecycle_status)` |
| Assigned employee | `customer.assigned_sales_staff_id` resolved via `db_session.get(StaffUser, ...)` in the route -- `assigned_staff.display_name`, or "Unassigned" if the column is NULL |
| Primary Contact | `next((c for c in customer.contacts if c.is_primary), None)` -- "-" if none is marked primary |
| Account age | "Customer since \<date\>" from `customer.created_at` (real `TimestampMixin` column, `format_owner_date`) -- a duration was considered and rejected: a fixed "since" date is simpler, always correct, and needs no relative-time recomputation on every render |
| Archive state | `customer.archived_at` (real, nullable `DateTime` column set by `customers/services.py::archive_customer` alongside `lifecycle_status = "ARCHIVED"`) -- "Archived on \<datetime\>" or "Not archived"; confirmed by reading `archive_customer` directly, not assumed from the status label alone |
| Commercial summary | `customer_360.build_commercial_summary()` -- reuses the exact rows already fetched for the Quotes/Orders/Invoices/Subscriptions/Licenses/Installations tabs (never a second query): quote/order counts, open-invoices count + total (status `ISSUED`/`PARTIALLY_PAID`), active-subscription/license/installation counts. A key is simply absent from the dict (and the header tile omitted) when the actor lacks permission for that entity or there is nothing to show -- never a fabricated zero standing in for "not permitted to know" |
| Quick actions | Archive form (unchanged), Reassign form (unchanged), plus "New quote" (`commercial_sales_web.new_quote_form`, gated on `quotes.create`), "Record payment" (`commercial_sales_web.new_payment_form`, gated on `payments.create`), "New subscription" (`subscriptions.new_form`, gated on `subscriptions.create`) -- all three are real, currently reachable routes. None of them accept a `customer_id` prefill query parameter (verified by reading each route: `new_quote_form`/`new_payment_form`/`subscriptions.new_form` all just render a plain customer `<select>`), so none is invented here -- they link to the plain form, exactly as they exist today. A direct "New order"/"New invoice" quick action was considered and rejected: neither has a standalone create route at all (orders are only created via `/orders/from-quote/<quote_id>`, invoices only via `/orders/<order_id>/invoice`) -- inventing a plain "new order" link would be a capability that does not exist. |

## Tab list and governing permission

13 tabs, matching the governing spec's list exactly. "Ownership" column
states the real mechanism or explicitly "none" (verified against each
entity's own real list/detail route, not assumed):

| Tab | Visibility gate | Ownership mechanism |
|---|---|---|
| Overview | always (page-level `customers.view` already required) | n/a |
| Contacts | always | n/a (folds in **Location**, see below) |
| Interactions | always (add-form gated `customers.update_own`/`customers.update_all`) | n/a |
| Follow-ups | always (add-form gated `customers.update_own`/`customers.update_all`; Complete button gated `leads.update_own`/`leads.update_all`, the real permission on `crm_shared.complete_followup` -- shared with Lead follow-ups) | n/a |
| Quotes | `quotes.create` or `quotes.approve` (matches `commercial_sales/routes.py::list_quotes`/`quote_detail`) | `apply_ownership_filter(Quote, ...)`, creator-only, bypassed only by `quotes.approve` -- mirrors `list_quotes`'s own `_own_or_all()` rule exactly |
| Orders | `orders.create` or `orders.approve` | `apply_ownership_filter(SalesOrder, ...)`, creator-only, bypassed only by `orders.approve` |
| Invoices | `invoices.create` or `invoices.issue` | `apply_ownership_filter(CommercialInvoice, ...)`, creator-only, bypassed only by `invoices.issue` |
| Payments | `payments.view` | none -- verified: `commercial_sales/routes.py::list_payments` applies no ownership filter, company-wide once the permission is held |
| Subscriptions | `subscriptions.view` | none -- verified: `subscriptions/routes.py::list_subscriptions` applies no ownership filter |
| Licenses | `licenses.view` | none -- verified: `licensing/routes.py` list route applies no ownership filter |
| Installations | `installations.view` | none -- verified: `installations/routes.py::list_installations` applies no ownership filter |
| Notes | always (page-level) -- content itself already visibility-scoped by `list_customer_notes_visible_to()`, reused unchanged; add-form gated `customers.manage_notes` | handled inside `list_customer_notes_visible_to`, not duplicated here |
| Timeline | always (page-level) -- but each individual event only appears if the actor holds that event's own entity permission (see below) | inherited per-event from the source list above |

Every one of the ownership/permission pairs above was verified by reading
the real `@require_permission`/`@require_any_permission` decorator on the
real route in question, the same discipline `command_palette/service.py`
already established for cross-domain search -- never guessed, never a
second independently-written filter (all three ownership-scoped entities
reuse `app.leads.ownership.apply_ownership_filter`, the one shared helper).

All seven new tabs are bounded to the 50 most-recently-created rows
(`customer_360.PER_TAB_LIMIT`) with no further pagination on this page --
a single customer is not expected to accumulate more than a few dozen of
any one document type; if that assumption is ever wrong for a real
account, the fix is real pagination on the affected tab (a real, disclosed
follow-up), not silently raising the bound. Each bounded tab shows a
one-line "showing the most recent 50" note when the cap is hit. The
dedicated `/quotes`, `/orders`, `/invoices`, `/payments`, `/subscriptions`,
`/licenses`, `/installations` list screens remain the place to search/
filter/sort the full company-wide set.

## Location and Assignment history placement

Neither is in the spec's 13-tab list, so neither became a 14th tab or was
dropped:

- **Location** folded into the **Contacts** tab -- it is data about how to
  physically reach this customer, the same category of information as the
  Contacts list/primary-contact fields already on that tab. The capture
  button/manual-address form is now gated on `customers.capture_location`
  (presentation-only fix: it rendered unconditionally before, matching the
  same disclosed class of defect already fixed twice this phase in the
  Attention Center and Command Palette); the per-row "Verify" form is now
  gated on `customers.verify_location`, the real permission
  `crm_shared.verify_location_route` requires (a SALES actor holds
  `customers.capture_location` but not `customers.verify_location` --
  previously saw a Verify button that would 403 on click).
- **Assignment history** folded into the **Overview** tab -- it is the
  detailed record behind the header's "Assigned employee" fact, so it sits
  next to the Overview tab's own restated summary of that same field.

## Timeline construction

`customer_360.build_timeline()` merges these real, typed domain-event rows
-- and only these -- most-recent-first, bounded to 50
(`customer_360.TIMELINE_LIMIT`):

| Event | Real source | Timestamp |
|---|---|---|
| Lead conversion | `customer.converted_from_lead_id` (set once, at conversion time) | `customer.created_at` |
| Contact added | `customer.contacts` (same rows as the Contacts tab) | `contact.created_at` |
| Note added | `notes` (the exact `list_customer_notes_visible_to()` result, already visibility-scoped) | `note.created_at` |
| Quote created | `quotes` (same rows as the Quotes tab) | `quote.created_at` |
| Quote approved | `CommercialApproval` rows, `target_type="QUOTE"`, `status="APPROVED"`, scoped to this actor's already-visible `quotes` -- **not** a Quote-status field: `Quote.status` has no `APPROVED` value (`DRAFT`/`SENT`/`ACCEPTED`/`REJECTED`/`EXPIRED`/`CANCELLED`, confirmed against `QUOTE_STATUSES`); the real, distinct "approved" event in this domain is the pricing/discount-exception `CommercialApproval` record reaching `APPROVED` | `approval.decided_at` |
| Order created | `orders` (same rows as the Orders tab) | `order.created_at` |
| Invoice issued | `invoices` where `issued_at` is set (same rows as the Invoices tab) | `invoice.issued_at` |
| Payment recorded | `payments` (same rows as the Payments tab) | `payment.created_at` |
| Subscription created | `subscriptions` (same rows as the Subscriptions tab) | `subscription.created_at` |
| License issued | `licenses` where `issued_at` is set (same rows as the Licenses tab) | `license.issued_at` |
| Installation activated | `InstallationStatusHistory` rows with `to_status == "ACTIVE"`, scoped to this actor's already-visible `installations` -- the one real place an Installation reaches `ACTIVE` (`installations/services.py::transition_installation`) | `history.created_at` |
| Refund processed | `CommercialRefund` rows joined to this customer's invoices where `paid_at` is set -- gated on `refunds.create`/`refunds.approve` (the real permission `commercial_sales/routes.py::list_refunds` requires, no ownership filter -- verified), even though Refunds has no dedicated tab in the spec's 13-tab list | `refund.paid_at` |

**Non-duplication of the audit log, explicitly**: `customer_360.py` never
imports or queries `AuditLog`/`SecurityEvent`. Every row above is a real,
typed domain table this same actor is *already independently permitted to
see* through its own tab's gate -- each `get_*()` helper in
`customer_360.py` returns an empty list when the actor lacks that entity's
permission, so the Timeline simply receives nothing from that source; no
second permission check is layered on top, and no audit-log row is ever
translated into a "friendly" sentence. This directly satisfies the
governing spec's explicit warning against building a customer-friendly
timeline as an unfiltered/untranslated restatement of the audit log.

## ARIA tabs mechanism and no-JS behavior

Real `role="tablist"`/`role="tab"`/`role="tabpanel"` markup, `aria-selected`,
`aria-controls`/`id` wiring between each tab and its panel, and a real
`aria-label` on the tablist. `static/js/tabs.js` (vanilla JS, external
file, CSP `script-src 'self'`, same convention as `table-density.js`/
`product-tour.js`) adds:

- Real single-panel-visible behavior (`hidden` attribute toggled on every
  panel but the active one).
- Real keyboard navigation: ArrowLeft/ArrowRight (and Up/Down), Home, End
  move focus and activate the target tab, with a roving `tabindex`
  (`0` on the active tab, `-1` on the rest) -- the WAI-ARIA APG "automatic
  activation" tabs pattern.
- Deep-linking: `window.location.hash` is checked on load, so linking
  directly to `#panel-licenses` opens that tab.

**No-JS behavior (deliberate, disclosed)**: every tab in the tablist is a
real `<a href="#panel-id">` anchor, and every panel is server-rendered and
visible (no `[hidden]`, no `display:none` in `components.css` for
`.aura-tabs__panel`) by default -- `tabs.js` is the *only* thing that ever
hides a panel. With JavaScript disabled, clicking a tab still jump-scrolls
to its section via native anchor behavior, and every section is already on
the page to land on -- a real, fully working degraded experience (an
in-page table of contents over one long page), not merely "the content is
technically present but the controls are dead." This was chosen over a
`<button>`-only tablist specifically because a plain anchor keeps working
without any script at all, where a `<button>` with no click handler would
not.

## Permission/ownership discipline verification

- `grep -n '_(".*") %[^(]'` across `customers/detail.html`,
  `customers/customer_360.py`, and `customers/routes.py` returns zero
  matches -- every parameterized translated string uses `_("...", name=value)`.
- Every `_own`/`_all`-shaped check (`_quotes_all_held`/`_orders_all_held`/
  `_invoices_all_held` in `customer_360.py`) is evaluated and passed into
  `apply_ownership_filter()` unconditionally -- there is no "no profile"
  early-return guard ahead of it in this module for these three entities
  (unlike `command_palette/service.py`'s search functions, which do need
  such a guard because they run a *second*, separate query per entity type
  before deciding whether to search at all). Here, `apply_ownership_filter`
  itself is `all_permission_held`-first internally, so passing
  `profile_id=None` when `all_permission_held=False` degrades safely to "no
  rows", never a crash and never an accidental company-wide leak.
- No new permission code was invented -- every code referenced above
  (`quotes.create`, `quotes.approve`, `orders.create`, `orders.approve`,
  `invoices.create`, `invoices.issue`, `payments.view`, `payments.create`,
  `subscriptions.view`, `subscriptions.create`, `licenses.view`,
  `installations.view`, `customers.manage_contacts`,
  `customers.capture_location`, `customers.verify_location`,
  `customers.manage_notes`, `customers.update_own`, `customers.update_all`,
  `leads.update_own`, `leads.update_all`, `refunds.create`,
  `refunds.approve`) already exists in `owner/app/staff/seed_data.py` and
  is already enforced by a real route decorator elsewhere in the app.
- Every `url_for()` call added was verified against the real registered
  endpoint (`commercial_sales_web.quote_detail`/`order_detail`/
  `invoice_detail`/`payment_detail`/`refund_detail`/`new_quote_form`/
  `new_payment_form`, `subscriptions.detail`/`new_form`,
  `licensing.detail`, `installations.detail`).
- No existing route's write behavior changed -- `customers/routes.py::detail`
  is the only route touched, and only with additive read-only queries;
  `archive`/`assign_route`/`add_contact_route`/`add_note_route`/
  `add_interaction_route`/`add_followup_route`/`add_location_route` and
  `crm_shared.complete_followup`/`verify_location_route` are byte-for-byte
  unchanged.

## Verification run

`OWNER_TEST_DATABASE_URL=postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test_uiux`
against the dedicated Stage D/UIUX test database (not the shared default).
Targeted runs before the full suite: `test_customers.py` (16),
`test_phase9_5b_r2_owner_wide_template_rendering.py`,
`test_phase9_5b_r_hardcoded_strings.py`, `test_attention_center.py`,
`test_command_palette.py`, `test_phase9_5c_contacts.py`,
`test_phase9_5c_location.py`, `test_phase9_5c_note_visibility.py`,
`test_phase9_5c_api_idor.py`, `test_phase9_5d_web_commercial_sales.py`, and
the three new tests in `test_customer_360.py` -- all passed. Full-suite
exact pass/fail counts are reported in the phase completion summary.
