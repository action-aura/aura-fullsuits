# Owner App — Current Frontend Architecture Audit (Stage A)

Read-only audit of the existing Aura Owner frontend (Flask + Jinja2,
server-rendered, no JS framework), produced ahead of the UI/UX
modernization phase. Every claim cites a real `path:line`. This is a
factual snapshot of what exists today, not a critique of decisions —
several patterns below (CSP `script-src 'self'`, no client tracking,
i18n-first markup) are deliberate, documented engineering choices and are
noted as such.

## 1. Templating base/layout

There is exactly **one** base template:
`owner/app/templates/layout/base.html` (150 lines). Every one of the 102
`.html` files under `owner/app/templates/` extends it — confirmed via
`grep -rl "{% extends" owner/app/templates | wc -l` → 101 (all templates
except `base.html` itself).

`base.html` defines:
- `<head>` with a single inline `<style>` block (the entire app's CSS —
  see §2) and a `<title>` block (`base.html:5`).
- A `<header class="topbar">` with the brand link, language switcher, and
  logout form (`base.html:73-90`).
- A single flat `<nav class="tabs">` (`base.html:92-134`) listing **every**
  screen in the app — Overview, CRM, Sales, Finance, Licensing,
  Management, System, Auth — as one unbroken list of ~40 links, rendered
  identically for every logged-in staff member regardless of role. There
  is no permission-based filtering in this markup, only the single
  `{% if staff %}` gate that hides the whole nav pre-login
  (`base.html:91,135`). This means every role sees links to screens it
  cannot use (confirmed against the RBAC/permission model in
  `screen-route-inventory.md`).
- A `<main>` region with a flashed-messages block (`base.html:137-145`)
  and the single `{% block content %}{% endblock %}` every child template
  fills in (`base.html:146`).
- One `<script src="{{ url_for('static', filename='js/confirm.js') }}">`
  tag (`base.html:148`) — the only script loaded on any page.

There are **no Jinja macros and no `{% include %}`/`{% import %}`
statements anywhere in the template tree** —
`grep -rn "{% import\|{% include\|{% macro" owner/app/templates` returns
zero matches. Every one of the 101 child templates is a fully
self-contained file that duplicates its own header markup, filter-bar
markup, table markup, and form markup from scratch. There are no partial
templates (`_something.html`) and no template inheritance beyond the
single `base.html` → child level (no intermediate "list-page base" or
"detail-page base" layer).

### Reusable building blocks that do exist (CSS-class-only, not markup-level)

Because there are no macros, the only "reuse" mechanism is CSS utility
classes defined once in `base.html`'s `<style>` block and referenced by
class name across templates:
- `.card` (`base.html:21`) — bordered white panel, used everywhere.
- `.badge` + status modifiers `.active/.confirmed/.success/.ok`,
  `.warn/.pending/.draft`, `.danger/.revoked/.failed/.disabled`
  (`base.html:25-28`) — color-coded status pills, but note: these are
  **color-differentiated, not shape/icon-differentiated** — see the
  accessibility section below.
- `.btn` / `button` / `.btn.secondary` / `.btn.danger` (`base.html:32-34`).
- `.filters` (`base.html:39`) — flex row for filter-bar forms, duplicated
  markup-wise on ~15+ list screens (each screen writes its own
  `<form class="filters" method="get">...</form>` from scratch; only the
  class name is shared).
- `.kv` (`base.html:40-41`) — a fixed `200px 1fr` two-column
  definition-list grid for detail pages. **Not** covered by the 720px
  media query, so long values in the fixed 200px label column can crowd
  on narrow viewports (confirmed on `profile/index.html`).
- `.responsive-table` (`base.html:47-60`, added "Phase 9.5B") — an
  opt-in table pattern that hides `<thead>` and stacks rows as
  labeled blocks under a 720px breakpoint via
  `td[data-label]::before { content: attr(data-label) }`. **58 template
  files** apply the `.responsive-table` class
  (`grep -rl "responsive-table" owner/app/templates | wc -l`) but only
  **18 files** actually set the `data-label` attribute needed to make the
  collapse readable (`grep -rl "data-label" owner/app/templates | wc -l`)
  — for the other ~40, the CSS rule fires but has nothing to render,
  producing blank/unlabeled stacked rows on mobile. This is the single
  most-repeated defect in the responsive-issues columns of
  `screen-route-inventory.md` (all of the Licensing/Installations/
  Commercial-Ops domain group's ~25 tables, plus Subscriptions).
- `.responsive-cards`/`.crm-card` (`base.html:65-68`, "Phase 9.5C") — a
  CSS-grid card layout, used only by the Leads list screen
  (`owner/app/templates/leads/list.html`) — not adopted anywhere else.
- `.pagination` (`base.html:69`) — a right-aligned text label wrapper.
  Used in exactly **one** template in the entire app
  (`owner/app/templates/leads/list.html:34`), and even there it renders
  only `"Page X / Y · N total"` as plain text with no prev/next controls
  — there is no real pagination UI component anywhere in Owner today.
  Every other list screen (Customers, Quotes, Orders, Invoices, Payments,
  Refunds, Commissions, Licenses, Installations, Renewals, Pilots, etc.)
  has either a hard server-side `.limit(200)`/`.limit(60)`/`.limit(500)`
  with no page control at all, or no limit whatsoever (Customers,
  Subscriptions) — see `screen-route-inventory.md` for per-screen detail.

### Duplicated-per-page pattern (evidence)

`owner/app/templates/leads/list.html:1-13` and
`owner/app/templates/customers/list.html:1-13` are structurally identical
boilerplate (extends → title block → `.card` → flex header row → "New X"
button → `.filters` form → status `<select>`) copy-pasted independently
rather than sharing a macro:

```
{% extends "layout/base.html" %}
{% block title %}{{ _("Leads") }}{% endblock %}
{% block content %}
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;">
    <h2>{{ _("Leads") }}</h2>
    <a class="btn" href="{{ url_for('leads.new_form') }}">{{ _("New lead") }}</a>
  </div>
  <form class="filters" method="get">
```
— `owner/app/templates/leads/list.html:1-9`, versus the near-identical
`owner/app/templates/customers/list.html:1-9`. This exact shape (header
row + filter form) is repeated independently across essentially every
list screen in the app (confirmed: 27 matches for
`class="filters"`/`method="get"` list-filter forms via
`grep -rn "<form.*method=\"get\"\|class=\"filters\"" owner/app/templates`).
A future component/macro layer (or a proper templating component system)
would collapse this into one reusable list-page shell.

## 2. CSS

**There are zero `.css` files anywhere in the repository**
(`find owner -iname "*.css"` → no results). All styling lives in the
single inline `<style>` block in `owner/app/templates/layout/base.html:7-70`
— roughly **5,667 characters** (~64 lines) of CSS total for the entire
application. This is a deliberate architectural consequence of the CSP
policy set in `owner/app/security/headers.py:8-9`:
```
"script-src 'self'; "
"style-src 'self' 'unsafe-inline'; "
```
`style-src` explicitly allows `'unsafe-inline'` (why inline `<style>` and
`style="..."` attributes work) but there is no external stylesheet
loading, no CDN, and — confirmed by grep — **no Bootstrap, Tailwind,
jQuery, or any other CSS/JS framework anywhere in `owner/app`**
(`grep -rn "bootstrap\|cdn\.\|jquery\|tailwind" owner/app/templates
owner/app/static` → 0 matches).

### Token/variable usage

`base.html:8` defines exactly **five** CSS custom properties:
```
:root { --border:#d8dee4; --muted:#6b7785; --accent:#2452b8; --danger:#b3261e; --bg:#f6f8fa; }
```
These are the only design tokens in the app. There is no spacing scale,
type scale, radius scale, shadow scale, or z-index scale defined
anywhere — sizes/spacing are hardcoded per-rule throughout `base.html`'s
style block (e.g. `padding: 0.4rem 0.5rem`, `border-radius: 8px`,
`border-radius: 999px` all appear as one-off literals rather than shared
tokens).

### Inline styles and hardcoded colors outside the token system

`style="..."` attributes appear **134 times** across the template tree
(`grep -rn "style=\"" owner/app/templates | wc -l`), most commonly to set
an ad hoc `max-width`/`margin` on auth-form cards, e.g.
`owner/app/templates/auth/login.html:4`:
```html
<div class="card" style="max-width:380px;margin:2rem auto;">
```
— this exact pattern (`max-width:NNNpx;margin:2rem auto;`) is repeated
independently with slightly different pixel values across
`auth/login.html:4`, `auth/change_password.html:4` (which, notably,
*omits* the `max-width` entirely — a real inconsistency,
`auth/change_password.html:4` vs. `auth/login.html:4`),
`auth/mfa_enroll.html:4`, `auth/mfa_recovery_codes.html:4`,
`auth/mfa_verify.html:4`, `auth/reauth.html:4`, and
`auth/accept_invitation.html:4`.

Seven template files hardcode raw hex colors outside `base.html`
(`grep -rln "#[0-9a-fA-F]\{3,6\}" owner/app/templates | grep -v
layout/base.html`): `commercial_ops/emergency_extensions_new.html`,
`customers/detail.html`, `licensing/detail.html`,
`licensing_admin/entitlement_preview.html`,
`licensing_admin/offline_policies.html`,
`licensing_admin/signing_keys.html`, `system/backups.html`. Two
representative excerpts show the pattern's real cost — color values are
**re-typed as literals instead of referencing the existing
`var(--muted)`/`var(--danger)` tokens**, so the two copies can silently
drift:
```html
<!-- owner/app/templates/customers/detail.html:99 -->
<p style="border-bottom:1px solid #eee;padding-bottom:0.4rem;">{{ n.body }}<br>
  <small style="color:#6b7785;">{{ note_visibility_label(n.visibility) }} ...</small></p>
```
`#6b7785` here is a hand-retyped duplicate of `--muted` (defined as
`#6b7785` in `base.html:8`) — the template does not use
`var(--muted)`. Similarly:
```html
<!-- owner/app/templates/licensing/detail.html:5 -->
<div class="card" style="border:2px solid #b3261e;">
```
`#b3261e` is a hand-retyped duplicate of `--danger` (also `#b3261e` in
`base.html:8`). No template anywhere in the app references
`var(--accent)`, `var(--border)`, `var(--muted)`, `--bg`, or `--danger`
via the `var()` function — the tokens exist only inside `base.html`'s own
rules; every other file that needs one of these colors re-types the hex
literal instead of consuming the variable.

## 3. JavaScript

There are exactly **two** JavaScript files in the entire application,
both under `owner/app/static/js/`, totaling **2,596 bytes**:
- `confirm.js` (958 bytes, 20 lines) — the sole confirmation mechanism
  app-wide. Listens for any form submit and, if the form has a
  `data-confirm` attribute, calls `window.confirm()` with that
  (server-localized) string before allowing the submit
  (`confirm.js:13-20`). Its own header comment explains *why* it's an
  external file rather than an inline `onsubmit="confirm(...)"`: Jinja's
  `_()` translation output is HTML-attribute-escaped but would be unsafe
  to interpolate directly into a JS string literal, and this pattern
  keeps translated text server-side-only (`confirm.js:1-11`).
- `geolocation-capture.js` (1,638 bytes, 37 lines) — a single one-shot
  `navigator.geolocation.getCurrentPosition()` call wired to a
  `#capture-location-btn` button, used only by the Leads/Customers
  location-capture forms. Explicitly documented as deliberately
  **not** using `watchPosition` or any timer/background tracking
  (`geolocation-capture.js:1-15`, "Non-Negotiable Domain Rule 8: no
  continuous/background tracking").

Both files are **vanilla JavaScript with no library or framework** — no
jQuery, no bundler, no build step, no `type="module"`. This is a
deliberate consequence of the CSP `script-src 'self'` policy
(`owner/app/security/headers.py:8`), which blocks any CDN-hosted
library and any inline `<script>` block — `geolocation-capture.js:3-6`'s
own comment states this was "a real defect found via Milestone 23
real-browser validation," confirming the external-file-only pattern was
a corrective fix, not the original design.

**There is no client-side theme/dark-mode logic anywhere** — confirmed no
`prefers-color-scheme`, no theme toggle, no `localStorage` usage in
either JS file or anywhere in the templates. **There is no client-side
table sorting or client-side search anywhere** — every filter (status
dropdowns on ~20+ list screens) is a `<select onchange="this.form.submit()">`
that triggers a full server round-trip; there is no JS-driven
instant-filter/search-as-you-type pattern in the app at all.

## 4. Accessibility patterns

**ARIA usage is minimal and confined almost entirely to `base.html`**:
`grep -rn "aria-" owner/app/templates` returns only **3 matches**, all in
the language switcher (`base.html:16,76,79` — `aria-label` and
`aria-current` on the locale links). No child template anywhere adds its
own `aria-*` attribute. `role="..."` appears exactly **once** in the
whole app: `role="status"` on the flash-message paragraph
(`base.html:141`).

**Semantic landmark elements (`<nav>`, `<header>`, `<main>`, `<section>`,
`<article>`, `<footer>`) are used only in `base.html`**
(`grep -rl "<nav\|<header\|<main\|<section\|<article\|<footer"
owner/app/templates` → exactly one file). Every content template renders
its content as `<div>`/`<table>`/`<form>` inside the single shared
`<main>` from `base.html` — no page introduces its own `<section>` to
group related content, and heading hierarchy is flat and repetitive:
e.g. `owner/app/templates/dashboard/index.html` has one `<h2>Overview</h2>`
(`dashboard/index.html:5`) followed by six sibling `<h3>` cards with no
`<section>` wrapper distinguishing them
(`dashboard/index.html:17,29,40,51,62,69`). No template in the app uses
an `<h1>` at all — `base.html` itself has none, and every content
template's first heading is an `<h2>`.

**Form label association is inconsistent and, by volume, more often
wrong than right.** The individual `screen-route-inventory.md` audit
found this defect repeated on the large majority of create/edit forms
across every domain (Leads, Customers, Sales documents, Expenses, Cash
Closing, Management Notes, Renewals, Pilots, Emergency Extensions): a
`<label>` sits visually adjacent to its `<input>`/`<select>`/`<textarea>`
but is not linked via `for`/`id`, so assistive technology cannot
programmatically associate them. A representative case with a confirmed
**broken** association (not merely absent) is
`owner/app/templates/auth/accept_invitation.html:10-11`:
```html
<label>{{ _('Email') }}</label>
<input value="{{ email }}" dir="ltr" disabled>
```
— neither element carries `for`/`id`, and there is no implicit wrapping
either. By contrast, a minority of forms get this right — e.g.
`owner/app/templates/customers/new.html:15-30`,
`owner/app/templates/catalog/plan_new.html:8-23`, and
`owner/app/templates/subscriptions/new.html:8-19` all use correct
`<label for="...">`/`id="..."` pairs — proving the pattern is known and
achievable in this codebase, just not applied consistently.

**Status is communicated via the `.badge` class family
(`base.html:25-28`), which pairs a background color with the status text
inside the same element** — e.g. `<span class="badge active">Active</span>`.
This is **not a pure color-only violation** (the text label is always
present), but several screens render the *bare* `.badge` class with no
color modifier at all for a status that IS colored on a sibling screen
(e.g. Quote/Order/Invoice/Plan **detail** headers render an uncolored
`.badge` for a status that the corresponding **list** screen colors
correctly — confirmed at `commercial_sales/quote_detail.html:6` vs.
`commercial_sales/quotes_list.html:26`), which is a real visual
consistency defect worth fixing even though it isn't a WCAG color-only
violation per se.

**Focus handling**: no custom focus-trap, skip-link, or focus-management
JS exists anywhere (consistent with there being only 2 small JS files
total, neither of which touches focus). Native browser focus order/tab
order applies throughout, which is neutral-to-positive given the
server-rendered, non-SPA architecture, but means there is no "skip to
main content" link for keyboard users navigating past the ~40-link global
nav on every single page load.

## 5. Responsive patterns

There is exactly **one** media-query breakpoint in the entire app:
`@media (max-width: 720px)` (`base.html:52-60`). It does four things:
hides `<thead>` and stacks `table.responsive-table` rows as labeled
blocks (see §1/§2 above on the `data-label` gap), makes `nav.tabs`
horizontally scrollable instead of wrapping
(`base.html:58: nav.tabs { overflow-x: auto; flex-wrap: nowrap; }`), and
reduces `<main>`'s inline padding (`base.html:59`). This single
breakpoint is the **entire** responsive strategy for the app — there is
no tablet-specific breakpoint, no wide-desktop max-width/centering
strategy for `<main>` beyond the fixed `max-width: 1100px` set
unconditionally at all viewport sizes (`base.html:20`), and no
container-query usage anywhere.

Because the 720px breakpoint only targets `table.responsive-table`, any
of the ~40 tables across the app that don't opt into that class (or that
opt in but omit `data-label` — see §1) will either overflow horizontally
on narrow viewports with no scroll affordance, or (for the `data-label`
gap case) collapse to blank unlabeled rows. The global `nav.tabs`
horizontal-scroll behavior (`base.html:58`) applies uniformly on every
single page, meaning the ~40-link primary navigation is a horizontally
scrolling strip on any viewport narrower than 720px, on every screen in
the app, for every role.

## 6. Typography

The only font declaration in the app is on `<body>`
(`base.html:10`):
```css
body { font-family: system-ui, -apple-system, "Segoe UI", "Noto Sans Arabic", sans-serif; margin: 0; ... }
```
This is a system-font stack with **"Noto Sans Arabic" as an explicit
fallback for Arabic text** — no font file is bundled or loaded by the
app itself (no `@font-face`, no Google Fonts link, no CDN font); it
relies entirely on the font being available on the end-user's OS. There
is no type scale (no `h1`–`h6` font-size system defined beyond the
browser's UA defaults — `base.html` sets no explicit heading font sizes
at all) and no defined line-height system.

### Arabic / RTL support (real, already implemented)

RTL is a first-class, already-working feature, not a gap: the `<html>`
tag sets `dir` dynamically from the current locale
(`base.html:2: <html lang="{{ current_locale }}" dir="{{
current_direction }}">`), and `owner/translations/ar/LC_MESSAGES/` +
`owner/translations/en/LC_MESSAGES/` contain compiled `.mo`/`.po`
translation catalogs (`owner/app/i18n.py`, `i18n_format.py`,
`i18n_labels.py` implement the locale machinery). All of `base.html`'s
CSS uses **logical properties** (`margin-inline-end`, `padding-inline`,
`text-align: start`) rather than physical `left`/`right` properties
(`base.html:12,14,20,23,59`), so the layout mirrors correctly under RTL
without a separate stylesheet. Bidirectional identifiers (emails, UUIDs,
correlation IDs, hashes) are consistently wrapped in
`<bdi dir="ltr">...</bdi>` throughout the app to keep them left-to-right
even inside an RTL page — confirmed in `audit/list.html:21,23`,
`audit/security_events.html:10`, `catalog/index.html:10,25,40,64,76,77`,
and dozens of other templates. This is a deliberate, documented, and
consistently-applied pattern (`base.html:43-46` even has a comment block
explaining the `<bdi>` choice) — one of the strongest architectural
through-lines in the codebase.

## 7. Color usage summary

| Source | Values | Notes |
|---|---|---|
| `base.html:8` `:root` | `--border:#d8dee4`, `--muted:#6b7785`, `--accent:#2452b8`, `--danger:#b3261e`, `--bg:#f6f8fa` | The entire token system — 5 colors total. |
| `.badge` status modifiers | `#dcf3e2`/`#1c6b34` (success), `#fbeecb`/`#8a5c00` (warn), `#fbdcda`/`#8a2119` (danger) | Defined once in `base.html:26-28`, referenced only by class name elsewhere — no drift risk here since these are never re-typed in child templates. |
| Re-typed literals in child templates | `#6b7785` (`customers/detail.html:99`), `#b3261e` (`licensing/detail.html:5`), `#eee` (`customers/detail.html:99`) | Duplicate the token values by hand instead of using `var()` — confirmed drift-risk pattern (see §2). |
| 7 template files total | see §2 list | Every hardcoded-hex template found via `grep -rln "#[0-9a-fA-F]\{3,6\}" owner/app/templates \| grep -v layout/base.html`. |

No template anywhere consumes a CSS custom property via `var(...)` —
the token system that exists is real but is currently a write-only
convenience for `base.html`'s own rules, not an app-wide reuse
mechanism.

## Summary judgment for the modernization phase

The current frontend is a deliberately minimal, CSP-hardened,
server-rendered, i18n/RTL-first system with **one base template, one
inline stylesheet (~5.7KB), and two tiny JS files (~2.6KB total)** — no
framework, no build step, no bundler. Its real strengths to preserve are
the CSP discipline (`script-src 'self'`), the logical-properties-based
RTL support, and the consistent `<bdi>` bidirectional-identifier pattern.
Its real gaps for the modernization phase to close are the complete
absence of a component/macro reuse layer (100+ templates hand-duplicate
the same header/filter/table boilerplate), the `data-label`
implementation gap on ~40 of the 58 `.responsive-table` instances, the
majority-broken form-label association, the single 720px breakpoint as
the entire responsive strategy, and the total absence of a design-token
system beyond 5 root CSS variables that aren't even consumed outside
`base.html` itself.
