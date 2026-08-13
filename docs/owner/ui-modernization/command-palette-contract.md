# Owner App — Command Palette Contract (Stage C)

Real implementation reference for the Ctrl+K / Cmd+K command palette.
Two independent halves — read `owner/app/command_palette/service.py`
alongside this document.

## 1. Entity search — real support, per type

| Entity | Real source? | Governing permission | Ownership scope | Match field(s) |
|---|---|---|---|---|
| Customers | Yes | `customers.view` | Yes — `apply_ownership_filter` (`customers.view_all` bypass), same helper `customers.list_customers` uses | `Customer.legal_name` |
| Leads | Yes | `leads.view_own`/`leads.view_all` | Yes — same helper, `leads.view_all` bypass | `Lead.organization_or_prospect_name` |
| Quotes | Yes | `quotes.create`/`quotes.approve` | Yes — `quotes.approve` is the real ownership bypass (mirrors `commercial_sales/routes.py::_own_or_all`), `create` alone is not | `Quote.quote_number` |
| Sales Orders | Yes | `orders.create`/`orders.approve` | Yes — `orders.approve` bypass | `SalesOrder.order_number` |
| Invoices | Yes | `invoices.create`/`invoices.issue` | Yes — `invoices.issue` bypass | `CommercialInvoice.invoice_number` |
| Licenses | Yes | `licenses.view` | No — company-wide once held, matches the real `licensing.list_licenses` route (verified: no ownership filter exists there either) | `License.key_prefix`, joined `Customer.legal_name` |
| Installations | Yes | `installations.view` | No — company-wide, matches `installations.list_installations` | `Installation.installation_label`/`device_label`, joined `Customer.legal_name` |

Every ownership-scoped entity reuses `app.leads.ownership.apply_ownership_filter`
directly — the exact same helper the corresponding real list route calls
— never a second, independently-derived filter. Every permission code
above was read from the real `@require_permission`/
`@require_any_permission` decorator on that entity's actual route, not
guessed.

## 2. Bounds

- `PER_TYPE_LIMIT = 6` results per entity type.
- `TOTAL_LIMIT = 36` results across the whole response (7 types × 6 max
  ≈ never exceeds this, but the cap is enforced explicitly regardless).
- `MIN_QUERY_LENGTH = 2` — anything shorter returns zero entity results
  (avoids an unfiltered/near-unfiltered table scan on every keystroke).
  Static navigation matches are unaffected by this floor — a 1-character
  or empty query still filters the small static command list client-side.
- Every query is a bound-parameterized `ILIKE`, with the user's own `%`/`_`
  literally escaped first (`_escaped_term()`) so a typed wildcard
  character is matched literally rather than expanded — defense-in-depth,
  not itself an injection risk since the value is parameter-bound either
  way.
- `Cache-Control: no-store` on every search response — results are
  permission- and ownership-scoped per actor and per request; a
  shared/proxy cache keyed only on the URL could otherwise leak one
  employee's results to another hitting the same query string.

## 3. Navigation and quick-create

`get_static_commands(codes)` returns a small, fixed-size (~40 entries)
list built by hand-mirroring `layout/_sidebar.html`'s own real
destinations and permission checks, endpoint-for-endpoint and
permission-code-for-permission-code — plus five real quick-create
entries (`leads.new_form`, `customers.new_form`,
`commercial_sales_web.new_quote_form`, `licensing.new_form`,
`installations.new_form`), each gated on that route's own real create
permission (`leads.create`, `customers.create`, `quotes.create`,
`licenses.create`, `installations.register`), all five endpoint names
verified to exist against the real route files before use.

**Disclosed trade-off**: this is a second, hand-kept-in-sync rendering
of the same permission source as `_sidebar.html`, not derived from it —
Jinja templates cannot be introspected from Python, so there is no
single source of truth to generate both from without a larger
refactor. If a nav destination is added to the sidebar in a future
stage, it should also be added here; a drift between the two would
only ever under- or over-list a *navigation shortcut* (a UX gap, not a
security issue — every result's own link is still independently
protected server-side by its real route's own decorator regardless of
whether the palette lists it).

## 4. Frontend behavior

- Real ARIA combobox pattern: `role="combobox"` input with
  `aria-expanded`/`aria-controls`/`aria-activedescendant`, `role="listbox"`
  results with `role="option"`/`aria-selected`, an `aria-live="polite"`
  status region for "Searching..." / empty-state announcements.
- Keyboard-first: Ctrl+K/Cmd+K opens from anywhere; Up/Down moves
  selection; Enter activates; Escape closes; Tab is trapped inside the
  single focusable control while open.
- Debounced 220ms after the last keystroke, `AbortController`-cancelled
  on every new keystroke, plus a stale-response guard (checks the
  input's current value still matches the request's term before
  rendering) as defense against a response that resolves just after a
  newer request already started.
- Static navigation/quick-create matches render immediately (client-side
  substring match against the small permission-filtered list already in
  the page) while the debounced entity search is still in flight, so the
  palette never feels empty during the wait.
- **Recent commands**: built. `localStorage["aura-owner-command-palette-recent"]`,
  capped at 5, storing only `{label, url}` pairs for destinations the
  employee actually navigated to — never a raw search query string
  (which could contain something typed that shouldn't sit in browser
  storage) and never any entity's real field values beyond the label
  already rendered on screen. Same presentation-only-preference status
  as `sidebar.js`'s collapsed/expanded state.
- No `console.log`/`console.error` of search terms, results, or fetch
  errors anywhere in the module — a failed or aborted request falls back
  to a real empty/loading state silently.

## 5. What was not built

No client-side full-dataset search of any kind — every entity result
comes from the real, bounded, permission-scoped backend endpoint. No
persistent search-history table (recent commands are the client-only
mechanism described above). No fuzzy/typo-tolerant matching beyond a
plain substring `ILIKE` — acceptable for this stage; real full-text
search infrastructure would be a disclosed, larger follow-up if ever
needed.

## 6. Two real bugs this module was deliberately built to avoid repeating

Both were found and fixed in the Attention Center (the previous Stage C
feature) via the full test suite, not by targeted tests or browser
spot-checks alone:

1. Every parameterized translated string here uses `gettext()`'s own
   kwarg form (`_("...%(name)s...", name=value)`) — never `_("...") % {...}`
   after the fact, which collides with markupsafe's own `%` handling
   inside Jinja rendering.
2. Every `_all`-permission bypass check (`quotes.approve`,
   `orders.approve`, `invoices.issue`, `leads.view_all`,
   `customers.view_all`) is checked **before** any "no `EmployeeProfile`"
   guard, so an actor holding the bypass permission but no profile of
   their own (e.g. a VIEWER-role account) still gets the company-wide
   result. Verified directly for Leads and Customers by
   `test_lead_search_view_all_bypass_works_without_own_employee_profile`
   / `test_customer_search_view_all_bypass_works_without_own_employee_profile`
   in `owner/tests/test_command_palette.py`.
