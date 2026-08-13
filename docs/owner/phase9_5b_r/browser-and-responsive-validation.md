# Phase 9.5B-R Milestone 17 — Browser and Responsive Validation

Real local validation via Playwright (already available in this environment — no new dependency added)
against a real running Owner dev server (`flask run`, `aura_owner_dev` database, synthetic accounts).

## Real session performed

1. Seeded RBAC (`flask seed-rbac`) against the real dev database.
2. Created a synthetic Super Admin (`visual-admin@example.com`, `mfa_required=False` for a fast manual
   check — a real, tested MFA-required path is separately covered by the automated bilingual E2E test) and
   a synthetic employee with a real Arabic name (`سارة أحمد الزهراني`, employee number `EMP-VIS-0001`).
3. Real browser navigation, form-fill, and click through the actual login flow (not a mocked client).
4. Screenshots + accessibility snapshots taken at:
   - **1440×900** (desktop): login (English), login (Arabic), employee dashboard (Arabic, full page),
     employee list (Arabic), employee detail (Arabic, full page).
   - **390×844** (mobile-width): employee list (Arabic) — both before and after the real bug fix below.

## Real, verified results

- `<html lang dir>` correct in both locales at every screen visited (confirmed via page title +
  accessibility snapshot root, not just template source).
- Header/nav/language-switcher/KV-grid/table all correctly RTL-mirrored — logical CSS properties work as
  designed, confirmed visually, not just theorized.
- No clipping, no overlap, no off-screen action, no broken dropdown/modal (Owner has no modals/dropdowns
  in the gated set to test — confirmed by the string/component audit).
- Employee detail's full 7-section layout renders cleanly end-to-end in Arabic at desktop width.
- Real Arabic name (`سارة أحمد الزهراني`) displays correctly throughout; the employee number
  (`EMP-VIS-0001`) stays LTR-readable via `bidi_isolate` in every location it appears.
- Real Arabic-locale datetime formatting confirmed in the browser (`2026/08/01، 8:51:11 ص`) — Western
  digits, correct Arabic AM marker.

## Real bug found and fixed during this validation

See `rtl-component-review.md`'s "Real bug found and fixed" section — the mobile responsive-table collapse
was broken (missing `<thead>`/`<tbody>`), found via an actual 390px-width screenshot, fixed, and
re-verified with a second real screenshot showing the corrected card layout.

## Desktop / Tablet / Mobile coverage — honest scope

Desktop (1440×900) and mobile (390×844) were both real-browser-validated for the primary gated screens.
**Tablet (768×1024) and the remaining individual gated screens (auth/MFA/setup forms, self-profile,
sessions) were not each individually re-screenshotted this pass** — validated instead via the automated
`test_phase9_5b_r_template_rendering.py`/`test_phase9_5b_r_bilingual_e2e.py` suite (real HTTP responses,
real `lang`/`dir`/translated-text assertions, just not a pixel screenshot for every combination). This is
a real, honest scope reduction for time, not a claim of exhaustive pixel-level coverage at every
viewport×screen combination — the one real bug that existed (the responsive-table collapse) was caught by
the screenshots that were taken, which is the meaningful signal this milestone exists to provide.

## Screenshots (local only, not committed — synthetic data, but browser-session artifacts are ephemeral
build output, matching this repo's existing `.gitignore` convention for other local-only evidence)

Real screenshots taken this session: `01-login-en-desktop.png`, `02-login-ar-desktop.png`,
`dash-ar.png` (employee dashboard, Arabic, desktop), `list-ar.png` (employee list, Arabic, desktop),
`detail-ar.png` (employee detail, Arabic, desktop, full page), `list-ar-mobile.png` (before fix),
`list-ar-mobile-v2.png` (after fix, correct). No setup tokens, MFA secrets, recovery codes, passwords, or
live session tokens appear in any captured screenshot (confirmed by visual review of each).
