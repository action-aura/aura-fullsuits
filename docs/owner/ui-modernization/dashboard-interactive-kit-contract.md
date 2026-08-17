# Dashboard Interactive Kit -- design contract

Status: approved by user 2026-08-17, kicking off implementation.
Builds on the shipped `feat/owner-ui-ux-modernization` branch (application
shell, design tokens, permission-aware sidebar, command palette all already
in place -- see `application-shell-contract.md`, `design-token-system.md`,
`command-palette-contract.md`). This is the next increment on top of that
work, not a redo of it.

## Scope

Presentation-layer only. **No schema change, no migration, no new/changed
service function.** `dashboard/services.py`'s `get_dashboard_summary()`
return shape is consumed as-is. Every new component is gated by the exact
same `has_permission()` / `has_any_permission()` Jinja globals the sidebar
and old dashboard already use -- no new permission codes.

First delivery = Dashboard page + the reusable component kit it's built
from. Other department list/detail pages (CRM, Sales, Finance, Licensing)
reuse the same kit in a later phase -- explicitly deferred, not gapped.

## 1. Chart components (new)

New `owner/app/static/css/charts.css` + Jinja macros in new
`owner/app/templates/layout/_charts.html`:

- `stat_tile(label, value, href=None, tone='default')` -- KPI card: big
  number, label, optional click-through `<a>` to the exact filtered list
  route the old `<dl>` row already linked to (e.g. "Open notifications" ->
  `commercial_ops_ui.list_notifications`). `tone` maps to existing status
  tokens (`default`/`success`/`warning`/`danger`) -- no new colors.
- `bar_chart(data, href_for=None)` -- horizontal SVG bars for
  label/count dict data (`subs_by_product`, `active_installations_by_product`).
  Each bar is a real `<a>` when `href_for(label)` resolves a route, same
  drill-down precedent as `.crm-card`.
- `donut_chart(data, tone_for=None)` -- SVG donut for status-breakdown data
  (`licenses_by_status`). Segment colors come from `tone_for(status)` ->
  existing `--aura-color-{success,warning,danger,info}` tokens (reuses the
  same status->tone mapping the `.badge` classes already encode).

All hand-rolled inline SVG. Zero new JS/CSS dependency. Colors exclusively
via `var(--aura-*)` custom properties so dark mode / RTL / high-contrast
need no separate code path (matches every existing component in
`components.css`/`shell.css`). Charts degrade to a visible `<table>` inside
a `<noscript>`-safe structure -- SVG `<title>`/`<desc>` per segment for
screen readers (no JS required to read the data, consistent with the
existing a11y bar set in `accessibility-report.md`).

## 2. Quick-action cards

A row of up to 6 shortcut cards at the top of the Dashboard, each a direct
link into the single highest-frequency action for that permission, chosen
by business weight (commercial pipeline + daily-ops cadence, per
[[enterprise-roadmap-strategy]] and the 9.5D/9.5E phase records):

| Card | Route | Gate |
|---|---|---|
| New Quote | `commercial_sales_web.new_quote` | `quotes.create` |
| Record Payment | `commercial_sales_web.new_payment` | `payments.view` (create-capable subset) |
| New Lead | `leads.new_lead` | `leads.view_own` or `leads.view_all` |
| New Expense | `operations_ui.new_expense` | `expenses.create` |
| Prepare Cash Closing | `operations_ui.new_closing` or today's draft | `cash_closing.prepare` |
| Attention Center | `attention.index` | `can_view_attention_center` |

Exact route names verified against `routes.py` before wiring (some of the
above are best-guess from list-route naming and must be confirmed, not
assumed, during implementation -- if a "new X" route doesn't exist, the
card is dropped rather than invented). Each card is a plain `.aura-*`
styled link, permission-gated the same way every sidebar link already is;
zero cards render for a role with none of the above permissions (same
graceful-empty precedent as the current `{% if not (...) %}` block).

## 3. Hotkeys

New `owner/app/static/js/shortcuts.js`, loaded next to the existing
`command-palette.js`:

- `g` then `d/c/l/s/e` -- jump to Dashboard/Customers/Leads/Sales(Quotes)/
  Expenses. Destination list matches the sidebar's actual visible links for
  the current user (read from the rendered nav, not a hardcoded route list)
  so a hotkey never points somewhere the user lacks permission to see.
- `?` -- shortcuts cheatsheet, reuses `.aura-profile-menu__panel` visual
  pattern for the popover.
- Guard: ignored while focus is inside `input`/`textarea`/`select`/
  `[contenteditable]`, and ignored while the command palette or any modal
  is open. Implementation will inspect `command-palette.js`'s existing
  focus/keydown guard first and reuse the same pattern rather than
  reinventing it.

## 4. Dashboard reorganization

`dashboard/index.html` rewritten section-by-section to mirror the sidebar's
department groups (Overview / CRM / Sales / Finance-Operations / Licensing
/ Management / System), same `has_permission()` conditionals as today,
same "nothing to show" empty state when a role has zero visible sections.
Per section: quick-action row (Overview only) -> stat-tile row -> one chart
where the data shape supports it -> table fallback for anything that
doesn't (recent staff actions, backup status stay tables).

## 5. Testing

- `test_phase9_5e_dashboards.py` (and any other test asserting old
  `<dl class="kv">` markup) updated for new markup/CSS classes -- same
  permission-gating assertions, different rendered structure.
- New: a hotkey guard test (input-focus suppression) and an SVG a11y check
  (each chart segment has a `<title>`) at whatever level the existing
  `accessibility-report.md` tooling already checks at.
- Full Owner regression must stay green before this is considered done,
  per this repo's own established discipline (see
  [[aura-owner-phase9-5a-commercial-ops]] -- multiple prior phases only
  caught real bugs at full-suite-regression time, not per-file).

## Explicitly out of scope (phase 2+)

Reusing this kit on CRM/Sales/Finance/Licensing list and detail pages,
real historical trend sparklines (would read the existing
`report_snapshots` table via the existing operational-reports service --
zero new backend code, but deferred to keep this phase's surface area
tight per the user's own "first things first, no schema/function changes"
instruction).
