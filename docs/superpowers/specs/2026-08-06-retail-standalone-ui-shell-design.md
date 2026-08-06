# Aura Retail — Standalone UI Shell

## Problem

`products/retail` (the standalone Windows product, packaged via
`products/retail/packaging/aura_retail_setup.iss`) has a complete backend
(auth, onboarding, retail API, import, backup, licensing) and a complete
business-logic frontend module (`subsystem-retail.js`, 2053 lines: Dashboard /
POS / Products / Customers / Suppliers / Purchases / Returns / Reports /
Scanner-settings). It has no page to load that module into: `app.py`
registers only `/api/*` routes plus static assets at `/static/*`, and
`products/retail/frontend/` has no `index.html`. Opening the packaged app
hits a 404 on `/`.

The only place a working retail UI exists today is the legacy monolith
(`AuraEnterprise\AuraEnterprise`), which renders one shared shell
(`templates/index.html` + `static/js/subsystem-core.js`) across every
subsystem it bundles (CMS, CRM, Accounting, HR, Retail, Clinic, PM,
Marketing). That shell was never extracted when Retail was split out into
its own product.

## Goal

Make `products/retail` run entirely on its own: real onboarding/login +
sidebar + all 9 existing sections, zero dependency on the legacy monolith or
any other Aura subsystem's code. Out of scope: any other subsystem, the
license-gated multi-module shell idea discussed and dropped earlier, Import
Wizard UI, Backup UI.

## What gets built

- **New route** — `GET /` in `products/retail/backend/app.py`, serving a new
  `frontend/index.html`.
- **New `frontend/index.html`** — slim shell: `<div id="app"></div>` plus
  `<script>`/`<link>` tags for only what retail needs. No CMS / CRM /
  Accounting / HR / PM / Marketing / Clinic script tags (the monolith's
  `index.html` loads all of these; none apply here).
- **New `frontend/app-shell.js`** — ported and trimmed from the monolith's
  `static/js/subsystem-core.js`. Keep: session check, auth-guard (401 →
  re-login modal instead of raw JSON on screen), onboarding/setup modal,
  login modal, sidebar rendering, theme toggle, i18n hookup. Strip: the
  multi-subsystem "EIP menu" chooser page and every other-subsystem branch
  (CRM/Accounting/HR/PM/Marketing/Clinic-specific code, demo multi-system
  bits). Behavior change from the source: `init()` currently renders a
  chooser page of subsystems before launching one; since
  `/api/auth/active-modules` for this product always resolves to a single
  system, skip the chooser and call `launch('retail', 'dashboard')` directly
  once the session is authenticated.
- **New `frontend/icons.js`** — copied as-is from the monolith's
  `static/js/aura-icons.js` (offline SVG icon set, no CDN, no changes
  needed).
- **New `frontend/css/main.css` + `frontend/css/rtl.css`** — ported from the
  monolith's `static/css/`, trimmed of rules that only apply to other
  subsystems or the CMS dashboard-builder.
- **Reused unchanged:** `frontend/i18n.js`, `frontend/locales/en.json`,
  `frontend/locales/ar.json`, `frontend/subsystem-retail.js`,
  `frontend/licensing.html`. `subsystem-retail.js` already expects exactly
  the `#sub-content` container + global `SubsystemApp` object the new shell
  provides — no changes to it are expected, but its calls into
  `AuraI18n`/`_()` need to be confirmed compatible with the existing
  `i18n.js` during implementation (not assumed here).
- **Nav** — the 9 sections `subsystem-retail.js` already renders: Dashboard,
  POS, Products, Customers, Suppliers, Purchases, Returns, Reports, Scanner
  settings. No Import Wizard or Backup entry points this pass — both already
  have backend support (`import_bp`, backup blueprint) and can get UI in a
  follow-up.

## Data flow

1. Browser loads `/` → Flask serves `index.html` → scripts load →
   `SubsystemApp.init()` runs.
2. `init()` calls `GET /api/auth/session`.
   - Not authenticated → `GET /api/onboarding/status`.
     - `needs_setup: true` (no admin yet) → setup modal →
       `POST /api/onboarding/create-admin`.
     - `needs_setup: false` (admin exists, no session) → login modal →
       `POST /api/auth/login`.
   - On success, `init()` re-runs.
3. Authenticated → `GET /api/auth/active-modules` (returns retail-only for
   this product today) → sidebar built for the single retail system →
   `launch('retail', 'dashboard')`.
4. Sidebar nav clicks call `RetailSystem.render(sectionId)` into
   `#sub-content` — unchanged contract, already implemented.
5. Language switch: shell reads `sess.language` / `localStorage`, calls the
   existing `AuraI18n.apply()`; the same locale files drive both the shell
   chrome and `subsystem-retail.js`'s own strings.

## Error handling

- Expired session mid-use (401 from any API call) reuses the existing
  auth-guard pattern: re-shows the login modal rather than leaving a raw
  JSON error on screen. Carried over unchanged from `subsystem-core.js`.
- Onboarding branching (first-run vs returning) already exists in
  `onboarding_routes.py` — the shell only needs to call it in the right
  order (status → setup-or-login).
- No other route changes; Flask's default 404 behavior is untouched for any
  path other than `/`.

## Verification

1. Run from source (`python app.py`): fresh DB → setup modal → create admin
   → login → sidebar renders → each of the 9 sections loads without console
   errors → EN/AR + RTL toggle checked.
2. Rebuild the PyInstaller exe (`pyinstaller
   products/retail/packaging/aura_retail.spec --noconfirm`) and the Inno
   Setup installer (`iscc
   products/retail/packaging/aura_retail_setup.iss`), then repeat the same
   manual pass against the installed, packaged app — this is what closes out
   the original 404 report.
3. No new automated frontend tests planned this pass (there are none today
   for this frontend) — recorded as a residual gap, not a blocker.

## Explicitly out of scope

- The legacy monolith (`AuraEnterprise\AuraEnterprise`) is not modified.
- No license-gated multi-module "buy Clinic later" shell — discussed
  separately, deliberately dropped in favor of this retail-only scope.
- No Import Wizard or Backup/Restore UI.
- No changes to `subsystem-retail.js`'s business logic.
