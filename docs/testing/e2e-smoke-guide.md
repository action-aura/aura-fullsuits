# Aura Retail real-browser E2E smoke suite

`products/retail/tests/retail_smoke_e2e_test.py` drives the REAL Aura Retail
product (real Flask backend, real Chromium browser, real clicks) end to end.
It exists because this repo has a written history of green test suites over
a broken product -- screens that rendered wrong, money shown over a 403, dead
buttons, a feature reachable only from a settings screen -- all while every
suite passed. Most "UI" suites here assert against SOURCE TEXT, which
structurally cannot catch a screen that renders wrong. Before this suite,
there was no test anywhere that drove the real UI in a real browser. This is
that test.

## Why it is not a pytest file

Playwright is installed under the SYSTEM Python
(`C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe`), not
under this repo's pytest venv (`.venv`). A pytest run uses the venv, which
does not have Playwright, so this file is a standalone script instead of
something only `pytest` can collect.

## How to run it

```
C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe products/retail/tests/retail_smoke_e2e_test.py
```

Run from anywhere -- all paths are resolved from the script's own location,
not the current working directory. No arguments are required.

Optional flags:

- `--headed` -- watch it run in a visible Chromium window (debugging aid).
- `--keep-app-data` -- do not delete the temporary `AURA_APP_DATA` directory
  afterwards (its backend log is at `<dir>/backend_stdout.log`).

Exit code `0` means every scenario passed. Non-zero means at least one
scenario failed (see the printed report), or the harness itself could not
run (no browser binary, backend never became healthy).

**Measured runtime: ~40-45 seconds** end to end (backend boot + 11 browser
scenarios + teardown), across repeated runs during development. Fast enough
to run before every push, not just in CI.

## What it does

Boots the project's own venv Python running
`products/retail/backend/app.py` completely unmodified -- the same way the
desktop launcher does -- as a subprocess, with `AURA_STANDALONE=1`, a free
localhost port picked at runtime (never 5000/5010/5011, which are already in
use on dev machines in this project), and a brand-new temporary
`AURA_APP_DATA` directory. This is a genuinely fresh install every run: no
licence seeded, no admin account, no data. It waits for `GET /api/health` to
return 200 before touching the browser, and the backend is always killed in
a `finally` block, even if a scenario crashes the script outright.

It then drives that backend with a real headless Chromium browser via
Playwright: clicking the actual sidebar nav (never setting `location.hash`
by hand where a user would click instead), filling actual forms, reading
actual rendered DOM state (element existence, table contents, computed
attributes like `data-theme` and `dir`) -- never grepping source files.
Every scenario asserts something that would fail if the page were blank; a
self-check scenario proves this directly (a selector that cannot exist
reads 0, a selector that must exist reads >=1).

Console errors, uncaught page errors, and every HTTP response are captured
for the whole run and printed in full at the end, regardless of which
scenario was running or whether it passed -- so a "benign"-looking error is
never silently dropped.

### The 11 scenarios

1. **App shell boots** -- `window.SubsystemApp` and `window.AuraI18n` are
   defined, zero uncaught console errors during load.
2. **Fresh-install first-run signup** -- the create-admin form renders with
   the fields it should have (and, correctly, no forced licence-key field on
   an install with no Owner configured), then actually completing it, which
   is what first proves the sidebar nav genuinely renders (see note below).
3. **Claim admin device** -- a brand-new admin account does NOT automatically
   become the admin device (confirmed by reading `app-shell.js`'s own
   `GET /api/devices/me` flow); this drives the real "Make this the admin
   device" banner, which is what unlocks the Settings nav entry.
4. **Navigate every major screen** -- Dashboard, Products, Customers,
   Reports, Settings, each via a real sidebar click, each asserted against a
   concrete rendered element (a table, a KPI figure, a known heading) with
   zero new console errors per navigation.
5. **Self-check + a real finding** -- proves the harness can tell "absent"
   from "present", using the Stock Transfers nav-unreachable bug (below) as
   the real negative case.
6. **Empty-state tables** -- Products and Customers render "No products
   found." / "No customers found.", not a blank or broken table.
7. **Read-only/licence-restricted honesty** -- attempts the real "+ Add
   Product" form (the actual first mutation a sale requires, since a fresh
   install cannot ring a sale at all) and asserts the user is told something
   truthful, not left at a dead button or a silent failure.
8. **License banner overlap (REAL BUG, pinned)** -- see below.
9. **Theme switching (REAL BUG, pinned)** -- see below.
10. **Dashboard stale-render race (REAL BUG, intermittent, pinned)** -- see
    below.
11. **Arabic/RTL toggle** -- `dir`/`lang` flip, the nav stays populated (not
    a blank RTL shell), and it flips back cleanly.

Screenshots are taken at every major step into a directory under the OS temp
folder (see "Where screenshots go" below), 15 PNGs on a typical run.

### Why "nav renders" is checked in scenario 2, not scenario 1

On a genuinely fresh install this app renders **no nav at all** before an
admin account exists -- confirmed by reading `app-shell.js`'s `init()`:
it returns immediately after `_openFirstRun()` when
`GET /api/onboarding/status` says `needs_setup`. There is nothing to assert
about "the nav" until signup actually completes, so that assertion lives at
the point it can honestly be made, rather than being faked by skipping
onboarding.

## Real bugs found while building this suite

These were found by literally watching what the real browser does, not by
reading source and guessing. None were fixed here -- per this task's scope,
other agents were working in `app-shell.js`/`subsystem-retail.js` in
parallel, and fixing them was explicitly out of scope.

### 1. `brand/intro.html` 404s on every single run (scenario 1's one failure)

The first-run brand intro overlay's iframe is built with
`frame.src = 'brand/intro.html'` (`app-shell.js`) -- a path RELATIVE to the
current page. The page is served at `/`, and only `/`, `/api/*` and
`/static/*` are registered routes (`app.py`) -- the real file lives at
`frontend/brand/intro.html`, reachable only at `/static/brand/intro.html`.
So the relative path resolves to `GET /brand/intro.html`, which 404s, every
time, on every fresh install. The overlay itself is harmless (dismissible by
click/keypress/timeout regardless of whether the iframe loaded), but the
brand intro video has never actually played on this build. This is why
scenario 1 legitimately, correctly fails on every run of this suite -- that
is the suite doing its job, not a flaw in it.

### 2. A permanent "not licensed" banner overlaps and blocks the header (scenario 8)

`app-shell.js`'s `_renderLicenseBanner()` appends `#aura-license-banner` as
`position:fixed;top:0;inset-inline:0;z-index:99998` whenever the licence
state is in `LICENSE_BANNER_BLOCKED_STATES` (which includes
`NOT_CONFIGURED` -- i.e. every unlicensed install, not a rare state).
Nothing reserves layout space for it (grepped `app-shell.js` and
`css/main.css` for a compensating `padding-top`/`margin-top`: none exists),
so it renders directly on top of `.sub-header`, covering the theme and
language toggle buttons underneath. Measured with real
`getBoundingClientRect()` values (not inferred): banner occupies
`y: 0-64`, the theme button sits at `y: 20-56` -- fully inside it. A real
(unforced) Playwright click on the theme button is confirmed intercepted by
the banner every time. **On any unlicensed install, a user cannot reach the
theme switcher or the language toggle with a mouse click at all**, only
through some other path (keyboard focus + Enter still works, since focus
order is unaffected by the visual overlap).

### 3. Switching the theme discards whatever screen you were on (scenario 9)

`ThemeEngine.apply()`'s own comment says it "re-renders the active section"
after a theme change (to rebuild Chart.js canvases), via
`SubsystemApp._navigate(SubsystemApp.active)`. But `.active` is the ACTIVE
SUBSYSTEM id (`'retail'`), not the current SECTION id
(`SubsystemApp.currentSection`, e.g. `'products'`) -- confirmed by reading
`app-shell.js`'s own `launch()` (`this.active = systemId`).
`subsystem-retail.js`'s `render(sectionId)` switch has no `'retail'` case,
so it falls through to the generic default branch:
`<h2>retail</h2><p>Coming soon.</p>` -- discarding the real screen entirely.
**Every theme switch, on every install, replaces the current screen with a
broken placeholder.** Screenshot `08b_theme_switch_discarded_current_screen`
shows this happening live while the Products screen was open.

### 4. An intermittent stale-render race can crash a Dashboard revisit (scenario 10)

Sometimes reproduces:

```
TypeError: Cannot set properties of null (setting 'textContent')
    at Object._renderDashboard (subsystem-retail.js:1729)
```

`_renderDashboard` sets `#sub-content`'s innerHTML (including `#r-k-cust`)
synchronously, then `await`s `GET /api/sub/retail/dashboard/stats`, and only
writes the response into `#r-k-cust` etc. once that resolves -- with no
check that this render is still the current one. A stats fetch from an
earlier Dashboard visit that is still in flight when the user has since
navigated elsewhere resolves late and writes into elements that no longer
exist. `app-shell.js`'s own `_navigate` catches the throw and shows a
generic "Failed to load" screen rather than crashing the tab, so this is
never a totally broken page -- just a Dashboard that silently fails to load
on that particular revisit.

**Measured honestly as intermittent, not asserted as guaranteed**: during
development of this suite it reproduced with an identical message and stack
trace on 2 of 3 independent full runs; other runs loaded cleanly. That is
exactly the shape of an unguarded timing race. Scenario 10 therefore accepts
either real, rendered outcome (the crash screen or a normal dashboard) and
prints loudly which one happened, rather than asserting the crash is
guaranteed -- which would make this suite flaky over the product's own race
instead of reliably reporting it either way.

## A real finding that is not a bug in the code that ships, but a UI gap

**"Stock Transfers" is unreachable from the real UI.** It has a complete nav
entry in `app-shell.js` (icon, label, `retail.stock.adjust` capability gate)
and a complete render function/table in `subsystem-retail.js`
(`_renderTransfers`, with a working "+ New Transfer" button) -- but the nav
id `'transfers'` appears in neither `navGroups` (sidebar) nor
`_TAB_BAR_SECTIONS` (phone tab bar), and grepping the whole file confirms
zero other reference to it anywhere. Backend routes for create/send/receive/
cancel are complete per `CLAUDE.md`. There is simply no click path to the
screen. Scenario 5 pins this via a real DOM assertion (the nav item's
selector count is 0) rather than describing it in prose only.

## What this suite deliberately does NOT cover

- **Ringing an actual sale.** A fresh install with no licence is read-only
  by design (`CLAUDE.md`, measured): every mutation route (`POST /sales`,
  `POST /cash-sessions/open`, `POST /products`, ...) answers
  `403 LICENSE_INACTIVE`. Scenario 7 proves and pins this restriction itself
  rather than working around it -- there is no code path to a genuine sale
  without an activated licence, and seeding one would defeat the point of
  testing what a real fresh install actually does.
- **Printing / cash-drawer hardware.** No physical or virtual printer is
  configured in this harness.
- **Multi-device sync.** Single browser, single backend instance.
- **Anything requiring an activated licence** -- Employees, Branches,
  Backup & Export's live-license-gated panels, e-invoicing submission, and
  any `retail.reports`/`retail.employees`-gated flows that additionally
  require an ACTIVE (not just NOT_CONFIGURED) licence state to fully
  exercise.
- **Cross-browser or mobile-viewport testing.** Chromium only, one desktop
  viewport (1440x900, wide enough for the sidebar nav rather than the
  <=640px phone tab bar).
- **Visual regression** (pixel-diffing). Screenshots are captured for a
  human to look at, not compared against a baseline.

## Where screenshots go

Under the OS temp directory, in a fresh timestamped folder per run:
`%TEMP%\aura_retail_smoke_screenshots_<UTC timestamp>\`. This is
deliberately OUTSIDE the repo entirely (not merely gitignored) -- nothing
under the system temp directory is ever a candidate for a commit, so there
is no risk of screenshots landing in git regardless of `.gitignore` state.
The suite prints the exact path at the end of every run.

## Known noise in the printed report

Chromium itself logs a `console.error`-type message for ANY non-2xx
fetch/XHR response (`Failed to load resource: the server responded with a
status of ... `), regardless of how gracefully the page's own JS handles it.
Scenario 7 deliberately provokes a 403 to test the read-only-honesty
behaviour, so it is the one scenario run with `strict_console=False` --
its OWN assertions (a truthful toast appears, the Save button re-enables)
are its real pass bar, not "zero console errors". The 403 (and the
`brand/intro.html` 404 from scenario 1) are still printed in full in the
final report either way -- nothing is hidden, only excluded from that one
scenario's own auto-fail check.
