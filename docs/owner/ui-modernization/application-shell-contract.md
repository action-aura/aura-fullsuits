# Owner App — Application Shell Contract (Stage B)

Real implementation reference for the rebuilt shared shell every one of
Owner's ~102 templates renders through via
`{% extends "layout/base.html" %}`. This document describes what was
actually built, not a plan — cross-check against the real files listed
below.

## Files

- `owner/app/templates/layout/base.html` — shell skeleton: `<head>`
  (theme/sidebar pre-paint script, token/component/shell stylesheets),
  sidebar `<aside>` (staff-only), top bar, `<main id="main-content">`
  wrapping `{% block content %}` (unchanged block name — every child
  template keeps working with zero edits).
- `owner/app/templates/layout/_sidebar.html` — the grouped nav,
  `{% include %}`d from `base.html`, staff-only.
- `owner/app/static/css/tokens.css` — token values only (see
  `design-token-system.md`).
- `owner/app/static/css/components.css` — shared component styles
  (`.card`, `.badge`, tables, forms, buttons, flash messages,
  `.responsive-table`/`.responsive-cards`/`.crm-card`) moved verbatim
  out of the old inline `<style>` block, same class names (zero of the
  102 child templates needed to change), now sourced from `--aura-*`
  tokens instead of the old private `--accent`/`--border`/`--muted`/
  `--danger`/`--bg` constants — those four names are kept as aliases
  pointing at the new tokens, so anything still referencing them
  directly (inline styles in child templates, if any) keeps working
  and now also follows the light/dark theme automatically.
- `owner/app/static/css/shell.css` — sidebar/top-bar layout rules only
  (no token values defined here).
- `owner/app/static/js/theme.js`, `owner/app/static/js/sidebar.js` —
  vanilla JS, external files (CSP-compatible, matching the existing
  `confirm.js`/`geolocation-capture.js` convention — no inline
  `onclick`, no framework).
- `owner/app/__init__.py` — `has_permission(code)` /
  `has_any_permission(*codes)` added to the global Jinja context
  (backed by the existing `get_staff_permission_codes()`),
  presentation-only, every route stays independently protected by its
  own `@require_permission`/`@require_any_permission`/`@require_login`
  decorator regardless of what the nav renders.

## Sidebar

Collapsible (`260px` expanded / `64px` collapsed —
`--aura-sidebar-expanded`/`--aura-sidebar-collapsed`), toggle button
persists state to `localStorage["aura-owner-sidebar"]`
(`"collapsed"`/`"expanded"`), restored before first paint by the inline
head script (no layout jump). Separate mobile off-canvas behavior
(`.aura-sidebar--open`, closes on link click or `Escape`) is
independent of the desktop collapsed/expanded preference. Current-route
highlight compares `request.endpoint` against each link's real Flask
endpoint (`aria-current="page"` on match, not merely a CSS class — real
semantic state for assistive tech).

**Groups and real permission gating** (verified by reading the actual
`@require_permission`/`@require_any_permission` decorator on every
listed view function, cross-checked against
`screen-route-inventory.md`):

| Group | Destinations | Gate |
|---|---|---|
| Overview | Dashboard | always visible to any logged-in staff (`@require_login` only) |
| CRM | Leads, CRM Dashboard, Customers | `leads.view_own`/`leads.view_all` (any), `customers.view` |
| Sales | Quotes, Sales Orders, Invoices, Payments, Refunds, Commissions, Commission Payouts, Commercial Dashboard, Finance Dashboard | per-item, e.g. `quotes.create`/`quotes.approve` (any) for Quotes, `payments.view` for Payments, `commissions.view_all` for the Finance Dashboard |
| Finance / Operations | Expenses, Expense Payees, Cash Closing, Operational Reports, Management Notes, My Expense Dashboard, Operational Management/Finance Dashboards | per-item, e.g. `expenses.view_own`/`expenses.view_all` (any) for Expenses, `dashboard.view_own` vs. `dashboard.view_all` split between the personal and management/finance operational dashboards |
| Licensing | Catalog, Subscriptions, Licenses, Installations, Renewals, Pilots, Emergency Ext., Activation Reviews, Notifications, My Queue, Reconciliation, Activation Service | per-item, e.g. `licenses.view`, `pilots.view`, `system.view` for Reconciliation |
| Management | Staff, Employees, Employee Dashboard | `staff.view`, `employees.view_all` |
| System | Audit Log, Security Events, Backups | `audit.view`, `system.view` |

A group heading renders only if at least one of its children is
visible (`{% set X_visible = ... %}` computed once per group, reused
for both the heading and the item checks — no empty section headers).
Profile/account items (My Profile, Account, Log out) live in the
top-bar profile menu, not the sidebar, matching where the spec's own
suggested IA places "Settings"-adjacent items relative to work-area
navigation.

**This directly fixes `screen-route-inventory.md`'s top usability
finding**: the old flat `nav.tabs` showed all ~40 links to every
logged-in staff member regardless of permission, so a SALES employee
routinely saw controls that 403'd on click. The new sidebar shows only
what a given employee can actually reach — while every route
underneath remains exactly as server-side protected as before (no
enforcement code touched).

## Top bar

- **Brand**: sidebar header shows a small square mark (`"A"`
  monogram, `--aura-color-brand-primary` fill) + "Aura Owner" text,
  linking to the dashboard — a placeholder mark, not the real logo
  image (`logo-brand-audit.md` §7 — the actual asset file is still
  outstanding; swapping in the real `<img>` once provided is a
  small follow-up, not a shell redesign).
- **Page title**: top bar `<h1>` renders `{{ self.title() }}`, reusing
  the existing `{% block title %}` every template already sets — no
  child template needed a new block for this to work.
- **Breadcrumb**: new optional `{% block breadcrumb %}{% endblock %}`,
  empty by default — pages get one only if Stage D work adds it.
- **Search / quick-create**: real, disabled `<button>` placeholders
  (`aria-label`, `disabled` — excluded from tab order, announced as
  disabled to assistive tech), not a fake-interactive control and not
  silently omitted. Chosen over omitting entirely so the shell's final
  layout is stable once the command palette (a separate, later Stage C
  milestone) and quick-create actions (Stage D, per-domain) are wired
  in — no later shell re-layout needed, just enabling the buttons.
- **Theme switch**: three real `<button>`s (`role="group"`,
  `aria-pressed`), Light/Dark/System — see
  `light-dark-theme-contract.md` for the full mechanism.
- **Language switch**: existing `locale.switch` links, restyled only
  — no routing/behavior change.
- **Profile menu**: native `<details>/<summary>` (no JS required for
  open/close — real keyboard/AT-accessible disclosure widget),
  showing display name + a role badge (`Super Admin` for
  `staff.is_super_admin`, else the first assigned role via the
  existing `role_label()` i18n helper — already used elsewhere in the
  app, not a new lookup mechanism), My Profile, Account, and the
  existing logout `<form>` with its CSRF token, byte-identical
  mechanism to before.

## Accessibility

Skip-to-content link (`.aura-sr-only`, targets `#main-content`),
real `<nav aria-label>` landmarks (sidebar "Primary", top-bar language
switch, theme switch as `role="group"`), `aria-expanded`/
`aria-controls` on both sidebar toggles, `aria-current="page"` on the
active nav link, disabled-not-hidden stub buttons. Full axe/keyboard
verification is `accessibility-report.md` (Stage E) — this is the
implementation's accessibility intent, not the verification record.

## What Stage B did not touch

No child template under `owner/app/templates/` (other than
`layout/base.html` and the new `layout/_sidebar.html`) was modified —
every page's actual content, forms, and tables are unchanged pending
Stage D. No Python route, permission check, or business logic changed
beyond the additive, read-only `has_permission`/`has_any_permission`
context-processor helpers.
