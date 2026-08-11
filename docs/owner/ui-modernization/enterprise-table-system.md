# Owner App — Enterprise Table System (Stage D)

Real implementation reference for the reusable list/table component
layer and its first real application (CRM: Customers, Leads). Describes
what was actually built, cross-check against the real files listed below
— not a plan.

## Files

- `owner/app/templates/components/table.html` — the macro library
  (`toolbar`, `filter_bar`, `table`, `pagination_nav`, `density_toggle`,
  `empty_state`). Every list screen that adopts it imports it
  `with context` (several macros read `request`/`has_permission` from the
  calling template's render context, which a context-less `{% import %}`
  would not provide).
- `owner/app/static/css/components.css` — the `/* Enterprise Table
  System */` section appended at the end of the file (same house style as
  the tour/attention/command-palette sections above it): toolbar, filter
  bar, `.aura-table`/`.aura-table-scroll`, sticky header, sort-link,
  density attribute rules, pagination.
- `owner/app/static/js/table-density.js` — vanilla JS, external file
  (CSP `script-src 'self'`, same convention as `sidebar.js`/`theme.js`),
  wired into `layout/base.html`'s script list.
- `owner/app/templates/customers/list.html`, `owner/app/templates/leads/list.html`
  — the two screens migrated onto the system this pass.
- `owner/app/customers/services.py` (`list_customers`), `owner/app/customers/routes.py`
  (`list_customers` route) — new service function + thinned route.
- `owner/app/leads/services.py` (`list_own_leads`/`list_all_leads`),
  `owner/app/leads/routes.py` (`list_leads` route) — extended with real
  status/search/sort params; the old in-memory status-filter bug is
  removed.
- `owner/tests/test_phase9_5b_r_hardcoded_strings.py`,
  `owner/tests/test_phase9_5b_r2_owner_wide_template_rendering.py` —
  `GATED_DIRS`/`REAL_TEMPLATE_DIRS` extended with `"components"` (both
  tests assert the directory set exactly matches every directory that
  contains a template; the new `components/` dir needed a matching entry
  in both).

## What's real vs. explicitly deferred

### Real (built and wired to real server-side behavior)

- **Sticky header** — CSS only (`table.aura-table thead th { position:
  sticky; top: 0; }`). The `.aura-table-scroll` wrapper deliberately has
  no fixed `block-size`, so its own vertical overflow never activates
  independently and the whole page scrolls together — that keeps the
  sticky reference frame equivalent to the viewport, not a clipped inner
  box.
- **Responsive horizontal handling** — see the dedicated "Responsive-mode
  decision" section below.
- **Server-side pagination** — real, focusable `<a>` prev/next + a
  bounded page-number window (macro `pagination_nav`), backed by the
  existing `app.services.pagination.paginate()` helper (already used by
  `leads`/`employees`). Customers previously had **no pagination at all**
  (`screen-route-inventory.md`: "Unbounded query, no LIMIT/OFFSET... will
  not scale") — this pass adds real `?page=` support to `/customers` via
  `customers.services.list_customers()`. `pagination_nav` preserves every
  other active query param (`status`/`q`/`sort`/`dir`) on every
  prev/next/page link — the pre-existing `employees/list.html` prev/next
  links **drop** the current filters on click; this is not repeated here.
- **Search** — real `?q=` → `ILIKE` on real columns, added to both
  screens' service functions: Customers searches `legal_name`/
  `trade_name`; Leads searches `organization_or_prospect_name`/
  `primary_contact_name`/`phone`/`email`. Neither existed before this
  pass (both were status-filter-only).
- **Filters** — the existing status-dropdown pattern (auto-submit on
  change, same `.filters` GET-form convention) is now rendered by
  `filter_bar()` instead of being hand-duplicated per template.
- **Sorting** — real, verified server-side `ORDER BY` only:
  - Customers (table mode): clickable `<th>` links for `legal_name`,
    `country`, `lifecycle_status` (via `CUSTOMER_SORT_COLUMNS` in
    `customers/services.py`), toggling `asc`/`desc`, with `aria-sort` and
    a real up/down/unsorted glyph.
  - Leads (card-grid mode, no table headers to attach a sort link to): a
    `<select name="sort">` + `<select name="dir">` dropdown pair in the
    filter bar, backed by `LEAD_SORT_COLUMNS` in `leads/services.py`
    (`organization_or_prospect_name`, `status`, `priority`,
    `next_follow_up_at`, `created_at`).
  - No column/field is offered as sortable unless its service function
    has a real `ORDER BY` entry for it — there is no client-side
    re-ordering of the current page's rows anywhere in this system.
- **Density toggle (compact/comfortable)** — real, using the
  `--aura-table-row-compact`/`--aura-table-row-comfortable`/
  `--aura-table-cell-pad-*` tokens already defined in `tokens.css`
  (previously unused app-wide). `[data-table-density="compact"]` on
  `<html>`, toggled by `table-density.js`, persisted to
  `localStorage["aura-owner-table-density"]` — presentation-only, same
  status as the sidebar's collapsed/expanded preference
  (`application-shell-contract.md`), not persisted server-side.
- **Row quick actions, status cells** — reuse the existing `.badge`
  classes exactly as before (the per-domain color-mapping logic — which
  status is "active" vs. "draft" vs. "danger" — stays in the calling
  template, not the macro, since that mapping is domain-specific business
  meaning, not presentation).
- **Empty states** — `table()`'s own `empty_message` param for table
  mode; `empty_state()` macro for the Leads card grid.
- **Loading/error states** — not applicable: every control in this
  system (search, filter, sort, page) is a real GET form submission or
  `<a href>` link, never a `fetch()` call, so there is no client-side
  loading state to build (matches the governing spec's own "no skeletons
  for instantly server-rendered content" rule).
- **Keyboard/screen-reader support** — real `<table>` markup with
  `scope="col"` and a `<caption class="aura-sr-only">` per instance, real
  `aria-sort` on sortable headers, real focusable `<a>` pagination links
  (not JS-only handlers), `aria-current="page"` on the current page
  number, `aria-disabled` on the inert prev/next state at the first/last
  page.
- **RTL-correct pagination glyphs** — found during verification (not in
  the original pass): `&larr;`/`&rarr;` are directional — "previous"
  must visually point toward the reading-backward direction, which flips
  screen side under `dir="rtl"`. Both prev/next elements (enabled `<a>`
  and disabled `<span>`) carry `data-aura-pagination-dir="prev"|"next"`;
  `:root[dir="rtl"] [data-aura-pagination-dir] { transform: scaleX(-1); }`
  mirrors them — the same technique `shell.css` already uses for the
  sidebar's collapse-toggle icon, not a new mechanism.
- **Permission-gated "New X" action** — `toolbar(new_permission=...)`
  fixes a real, disclosed defect (`screen-route-inventory.md`): the old
  per-template markup rendered "New customer"/"New lead" unconditionally,
  so a viewer lacking `customers.create`/`leads.create` saw a live button
  that 403'd on click. The button is now hidden via `has_permission()`
  (presentation-only — the routes stay independently
  `@require_permission`-protected regardless, same discipline as the
  sidebar and command palette).
- **Real bug fix (Leads status filter)** — the old `/leads` route applied
  its status filter in Python, *after* pagination, over only the current
  page's already-limited rows, while `result.total`/`total_pages` still
  reflected the *unfiltered* set (`screen-route-inventory.md`: "List
  pagination inconsistency"). `list_own_leads`/`list_all_leads` now apply
  `status`/`search` inside the `WHERE` clause, before `LIMIT`/`OFFSET`,
  so pagination metadata is always correct for what's actually filtered.

### Explicitly out of scope (with the real reason)

- **Column visibility** — no existing per-user/per-screen column
  preference persistence exists anywhere in the app (confirmed:
  `localStorage` is used only for the sidebar/theme/density UI
  preferences, all presentation-only and screen-independent; there is no
  server-side "saved view" storage table). Building column visibility
  with no persistence would mean it resets on every navigation, which is
  not a usable feature — deferred until a real persistence mechanism
  exists, same reasoning `role-dashboard-contract.md`/
  `attention-center-contract.md` used for equivalent gaps in Stage C.
- **Saved views** — same reason: no persistence authority exists.
- **CSV export** — audited (`grep -rn "csv\|export" owner/app --include=*.py`):
  the only real export mechanism in the app is `/audit/export`
  (`owner/app/audit/routes.py`), gated on the dedicated `audit.export`
  permission and scoped specifically to audit records. No equivalent
  export authority (permission code, route, or service function) exists
  for Customers or Leads — inventing one would be a new, undisclosed
  business capability, not a UI layer. Deferred until a real, secure
  export authority exists for these domains specifically.
- **Row selection + bulk actions** — audited (`grep -rn "bulk"
  owner/app --include=*.py`): the only "bulk" code in the app is
  `employees.presence.bulk_presence_states()`, a bulk *read* of presence
  status, not a bulk *mutation* of records. No customer/lead route
  supports a bulk transactional operation (bulk archive, bulk reassign,
  etc.) with its own authorization. Per the governing spec's explicit
  warning ("do not create bulk actions without transactional and
  authorization support"), this is deferred until such a route exists.
- **`leads/dashboard.html`** — checked, not migrated. Its "Leads by
  status" table is a 2-column aggregate count breakdown
  (`{status, count}` pairs from `summary.leads_by_status`), not a
  paginated/filterable/sortable record list — there is nothing for
  search, sort, or pagination to apply to, and no per-row link/action.
  Left as a plain `.responsive-table` (unchanged).

## Responsive-mode decision: contained horizontal scroll, not card-flip stacking

`current-ui-audit.md` documents the existing `.responsive-table`
mechanism: hides `<thead>` under 720px and stacks each row as a labeled
block via `td[data-label]::before`. This pass deliberately does **not**
reuse that mechanism for tables built through `table()` — it uses a new,
separate mechanism instead: `.aura-table-scroll { overflow-x: auto; }`
wrapping a normal `<table class="aura-table">` at every viewport size.

**Reasoning**: `.responsive-table`'s stacking works by hiding the
`<thead>` entirely under 720px. That is fundamentally incompatible with
two of this system's real, required capabilities — a **sticky header**
(nothing to stick once `<thead>` is `display: none`) and **real
sortable column-header links** (no `<th>` to click once it's hidden).
Rather than let both mechanisms silently coexist with no rule for which
applies when (the exact anti-pattern the governing spec warned against),
the rule is explicit and single-purpose:

- Any table rendered through `components/table.html`'s `table()` macro
  uses `.aura-table` + `.aura-table-scroll` (contained horizontal
  scroll), **never** `.responsive-table`, at every viewport size.
- The pre-existing `.responsive-table` card-flip stacking is **unchanged**
  and continues to serve the ~58 legacy templates outside this pass's
  scope (Licensing, Installations, Subscriptions, Employees, etc.).
  Migrating those to the new table system is future Stage D work,
  screen by screen — this pass does not retrofit them unilaterally.

Customers' list (the one table-mode screen migrated this pass) previously
used `.responsive-table` with **no** `data-label` attributes set (a
`.responsive-table` gap from the original template) — moving it to
`.aura-table-scroll` closes that gap by removing the broken mechanism
entirely for this screen, rather than adding the missing `data-label`
attributes to a mechanism now being phased out for new work.

Leads' card grid (`.responsive-cards`/`.crm-card`) already reflows
naturally via CSS grid at any viewport with no overflow risk
(`current-ui-audit.md` confirms this) — it needed no responsive-mode
change at all, only the shared filter/pagination/empty-state pieces.

## Table mode vs. card mode: why Leads stayed cards

`table()` was designed to be usable for either domain but Leads was
deliberately **not** converted to a `<table>`. A lead card already
surfaces six fields at a glance (organization name, status, phone/email,
priority, source, next follow-up) in a compact, grid-reflowing block;
forcing that into table columns would mean either truncating several of
those fields or producing a wide table that immediately needs the same
horizontal-scroll treatment the card grid never needed in the first
place. Leads gets real sorting via a `<select>` dropdown pair instead of
column-header links (see "Sorting" above) — functionally equivalent
server-side `ORDER BY` support, different UI surface appropriate to a
card layout with no header row. Customers, by contrast, is genuinely
tabular (three flat scalar fields + an action), so it adopted `table()`
directly.

## Real additive backend changes (exact diffs)

### `owner/app/customers/services.py`

Added `list_customers(*, page, page_size, status, search, sort,
direction, actor_employee_profile_id, all_permission_held)`, factored out
of the query-building logic that used to live inline in
`customers/routes.py::list_customers`. With no query params (the old
route's only call shape), it reproduces the exact prior
`select(Customer).order_by(Customer.created_at.desc())` behavior,
`apply_ownership_filter` call included — search/sort/pagination are
strictly additive, opt-in via new query params. Previously the route had
**no** `LIMIT`/`OFFSET` at all (a disclosed performance risk in
`screen-route-inventory.md`); it is now always paginated (`page_size=25`
default, matching `leads`/`employees`).

### `owner/app/customers/routes.py`

`list_customers` route: reads `page`, `status`, `q` (search), `sort`,
`dir` from `request.args`, calls the new service function, passes
`result` (the `{rows, page, page_size, total, total_pages, has_prev,
has_next}` dict shape already used by `leads`/`employees`) to the
template instead of a bare `customers` list.

### `owner/app/leads/services.py`

`list_own_leads`/`list_all_leads` gained `status`, `search`, `sort`,
`direction` keyword-only params (all default to the exact prior
behavior: no filter, `created_at desc`) — existing call sites
(`api_operations/crm.py`, the test suite) that only pass `page`/
`page_size` are unaffected. Filtering moved from the caller's Python
list-comprehension (the disclosed bug) into the query's `WHERE` clause.

### `owner/app/leads/routes.py`

`list_leads` route: reads `q`, `sort`, `dir` from `request.args` in
addition to the existing `page`/`status`, passes them through to the
service calls, and no longer post-filters `result["rows"]` in Python.

No route path, URL prefix, permission code, or response shape (JSON API
contract) changed — only new, optional query parameters on two existing
GET routes, exactly as constraint 1 in the governing task spec allows.

## Ground-rules verification

- `grep -n '_(".*") %[^(]'` across every new/changed file (`components/table.html`,
  `customers/list.html`, `leads/list.html`) returns zero matches — every
  translated string with placeholders uses `_("...", name=value)`, never
  raw `%`.
- No new permission code was invented — `toolbar()`'s permission checks
  use `customers.create`/`leads.create`, both pre-existing (confirmed
  against `owner/app/staff/seed_data.py`) and already enforced by the
  underlying `@require_permission` decorators on the `new_form`/`create`
  routes.
- Every `url_for()` call added (`customers.new_form`, `customers.detail`,
  `leads.new_form`, `leads.detail`, plus the dynamic `request.endpoint`
  used by the pagination/sort-link helper macros) was verified against
  the real route files.
- No new hardcoded colors/spacing — every new CSS rule in
  `components.css`'s Enterprise Table System section uses `var(--aura-*)`
  tokens exclusively.
- No new JS framework/CDN/dependency — `table-density.js` is vanilla JS,
  same external-file/no-inline-script pattern as every existing script in
  the app (required by the CSP `script-src 'self'` policy).
