"""Photograph every retail screen, for real.

v1 screenshotted on `networkidle`, which fires before this app has rendered
anything -- i18n and the onboarding-status fetch both resolve after it, so
every shot came out blank and looked like a boot failure. Waits are now on
SELECTORS, which is the only thing that actually means "the screen is there".

v2 (item #0, Phase 0) fixed a harness that could not get past first run and
could not fail:
  - `seed_active_license()` used to run BEFORE signup. That stamps an
    `owner_installation_id` into a licensing.db with no admin user yet,
    which makes app-shell.js's `_isJoinedDevice(lic)` true, so a genuinely
    fresh install took the `_waitForShopAccount()` branch instead of the
    real signup form -- see evidence/capture.py's own discovery of the
    same trap. The seed now runs AFTER signup, like capture.py does.
  - Paths resolved from `Path(".")`, the process cwd, not this file --
    worked only when invoked from the repo root. Now resolved from
    `Path(__file__)`.
  - The brand intro overlay (`#aura-intro-overlay`, plays once ever) was
    never dismissed, so early shots on a fresh browser profile could show
    the intro instead of the screen they claimed to capture.
  - The section list was missing quotations, transfers, cheques,
    receivables, payables and the four GL-derived statements -- see
    app-shell.js's own `nav` array, the single source of truth used here.
  - Every shot is now checked, not just captured: `#sub-content` must have
    rendered a real box, and no shot may be sitting on the licensing
    "Connecting to your shop…" wait screen. A failure is recorded and the
    process now exits 1 -- previously `problems` was printed but nothing
    ever called `sys.exit(1)`, so a broken run still reported success to
    whatever CI or crew consumed the exit code.
  - Product seeding now posts `category_id` (create_product,
    retail_api.py:2046, ignores a `category` name key -- evidence/
    capture.py's own trap, reproduced deliberately here and then checked:
    every seeded category must come back with `product_count > 0`).
  - Added the 1366x768 viewport (item #7's cart/tender bugs were measured
    at this exact width), a cashier pass (capability-gated nav, seeded
    through the product's own invite/self-setup routes) and a
    joined-computer pass (the legitimate flip side of the seed-order bug:
    a device that really did join a shop legitimately sees "Connecting to
    your shop…" on first boot -- captured deliberately, once, on its own
    throwaway AURA_APP_DATA and its own subprocess).
  - Added a theme x language matrix over a representative slice of
    screens. Full SECTIONS x WIDTHS x 5 THEMES x 2 LANGS would be several
    thousand screenshots and turn this into an hours-long run; three
    visually-dense screens (dashboard, pos, trial-balance) at one width
    across every theme/language combination is what actually proves theme
    tokens and RTL mirroring apply, at a runtime this can still be re-run
    routinely. If a future phase needs the full cross product, widen
    THEME_MATRIX_SECTIONS rather than reaching for is_visible()-style
    shortcuts to make it faster.

v3 (item #0, reviewer round on the v2 diff) closed two ways the harness
could still pass while broken:
  - The per-shot "rendered" guard was vacuous for a wrong or removed
    section: `shot()` only checked that #sub-content had a non-zero
    bounding box, which is equally true of the app's own unknown-section
    fallback (subsystem-retail.js's `default:` case, literally
    `<h2>${sectionId}</h2><p>Coming soon.</p>`) as it is of a real screen --
    "the pass condition IS the bug signature" (ENGINEERING.md failure shape
    #3). Proven live: a bogus id appended to SECTIONS screenshotted a
    perfectly real-looking "Coming soon." card and the run still exited 0.
    `walk_sections()` now passes the section id through to `shot()`, which
    calls the new `section_render_mismatch()` -- see that function's own
    docstring for the two independent checks it runs.
  - `page.on('pageerror')` was wired only in the owner width loop. A JS
    exception in the cashier pass or in the 5-theme x EN/AR pass was
    dropped on the floor -- an uncaught throw injected into the theme pass
    still exited 0 ("no page errors"), while the identical throw injected
    into the owner pass correctly exited 1. Both passes, plus the
    joined-computer subprocess's own page, now attach the same listener.

v4 (item #0, round 2 of the Retail UX programme) widened the theme x
language matrix past "owner only", the scope gap the round-1 report itself
flagged as a trade-off rather than an oversight:
  - The 5-theme x EN/AR pass used to authenticate as the OWNER only. A
    theme or RTL regression reachable exclusively through capability-gated
    cashier rendering -- a permission-message branch has its own markup --
    was never exercised in any theme but the default light/en one. The
    cashier pass now runs the same THEME_MATRIX_SECTIONS x THEMES x LANGS
    sweep the owner pass does, through a new shared `run_theme_matrix()`
    (factored out of the owner pass's own loop, byte-for-byte the same
    checks, so the two cannot drift apart the same reason `walk_sections()`
    is already shared between the owner and cashier NAV passes).
  - The joined-computer pass captured its "Connecting to your shop…" screen
    once, at the default theme/language only. That screen calls into
    `ThemeEngine.apply()`/`AuraI18n.setLang()` the same as any other --
    the language toggle is reachable from it in the real product -- but
    nothing had ever driven either function from this PRE-AUTH state, where
    `SubsystemApp` is not yet initialised. Both are already internally
    guarded for that (`ThemeEngine.apply`: `if (... && SubsystemApp.active
    ...)`; `AuraI18n.setLang`: a bare `try { SubsystemApp.init() } catch`)
    -- confirmed by reading both before relying on it, not assumed -- so
    this proves that guard holds against a REAL uncaught-exception listener,
    not just that the source looks guarded. No SECTIONS walk applies here
    (nothing has joined yet, so there is exactly one screen, not a nav to
    drive), so this loops THEMES x LANGS only and re-checks the same
    "still says Connecting, title not empty" property `_run_joined_
    computer_pass`'s own single capture already checks once.
  - The one-time setup page (`setup_page` -- onboarding, licence reload,
    data/cashier seeding) had NO `pageerror` listener and none of it was
    wrapped, so any exception there crashed the whole process with a raw
    Python traceback instead of this file's own "record to `problems`,
    exit 1" convention. Hit LIVE during this round's own end-to-end run,
    from a genuinely transient cause (a concurrent crew's mid-edit syntax
    error in app-shell.js, self-resolved within ~2 minutes) rather than a
    defect in this file -- but the harness's own reaction to it, an
    uncaught traceback, was exactly the "cannot fail cleanly" shape item #0
    exists to close. Mutation-proved by reproducing the classic seed-
    before-onboard bug one more time: it now reports `PROBLEMS (1): -
    one-time setup: Page.wait_for_selector: Timeout 20000ms exceeded.` and
    exits 1, not a stack trace. A pageerror that does NOT raise (onboarding
    still completes) is recorded and the sweep continues -- proven
    separately by injecting an async throw that does not block onboard():
    the run still took its screenshots and still exited 1 at the end.

v5 (round 2, final pass) closed a gap an adversarial verifier found in v3's
own render guard: `shot()` proves a box rendered and the right section is
behind it, but nothing it checks can tell "the section loaded its data"
from "the section's data fetch failed and the app quietly rendered its own
empty state" -- the app catches a failed load in a `console.error`, which
never raises `pageerror`, so a real backend failure on a section's own API
route was completely invisible to every check this file had. Proven live:
routing `/api/sub/retail/products` to a real HTTP 500 for every owner width
screenshotted the correct-looking empty state ("No products yet -- Add
your first product", stock value JD 0.000) against a seeded catalogue of
six real rows, and none of the resulting problem lines said so --
`section_render_mismatch()`'s own checks (no "Coming soon.", right header)
are exactly as satisfied by a genuinely empty screen as by one that failed
to load. This is the "pass condition IS the bug signature" shape
(ENGINEERING.md failure shape #3) one level up from the one v3 already
closed.

`_attach_response_watcher()` now listens on `page.on('response')` for
every page this file drives -- the one-time setup page, every owner width,
the cashier pass, the owner theme pass (the cashier theme pass already
shares `cashier_page`, so it inherits the same listener with no separate
wiring) and the joined-computer subprocess's own page -- and records any
`/api/` response whose status is >= 500, or a 4xx other than 401/403, as
`"<pass> HTTP <status> <method> <path>"`. 401/403 are the one deliberate
allowlist entry, and only for the cashier and joined-computer passes: a
cashier is legitimately denied by capability-gated routes (that denial is
the whole point of that pass), and a joined, not-yet-approved device is
legitimately denied by anything that needs a real session. Every other
pass authenticates as the owner, who this sweep never expects to be denied
anything it exercises -- a 401/403 there is exactly the kind of capability
regression this guard exists to catch, not noise to filter. One route is
exempted from the 401 check on every pass regardless: `GET /api/auth/
session` is documented and pinned (`test_employee_admin_routes.py`) to
answer an anonymous caller with a plain 401, and `init()` calls it
unconditionally before every login this file ever does -- caught live the
first time this guard ran with no mutation at all and still reported a
"problem" on a genuinely clean run, which is its own kind of vacuous guard
in reverse.

Set `UI_SHOT_MUTATION_INJECT_500` to a Playwright URL glob (e.g.
`**/api/sub/retail/products*`) to reproduce the verifier's own mutant on
demand -- every matching request on every page this file drives then fails
with a real HTTP 500 instead of reaching the backend. Never set it outside
a mutation proof; a real sweep leaves it unset and every request reaches
the real backend.

Completes first-run onboarding through the real form (that is what a
customer does), seeds demo data so screens are not empty, then walks every
sidebar destination at four widths, as a cashier, and as a joined device.

Set UI_SHOT_QUICK=1 for a fast smoke pass (one width, three sections, no
cashier/theme passes) -- enough to mutation-prove the licence-seed-order
fix without paying for the full sweep's runtime every time.
"""
import io
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Resolve every path from THIS FILE, never the process cwd (item #0): the
# old `Path(".").resolve()` only worked when invoked from the repo root,
# and silently pointed at the wrong `products/retail/backend` (or nothing
# at all) from any other cwd -- e.g. a crew running it from its own
# scratch dir, or a future CI step that does not `cd` first.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
BACKEND_DIR = (REPO_ROOT / "products" / "retail" / "backend").resolve()

QUICK = os.environ.get("UI_SHOT_QUICK") == "1"

# THEME_NAMES, app-shell.js:38 -- the frozen allowlist ThemeEngine._sanitize
# enforces. Kept as a literal here (not imported: this is Python driving a
# browser, not sharing a module with the frontend) but named at its source
# so a future 6th theme is a one-line diff, not a rediscovery.
#
# v4 (round 2): moved ABOVE the `--joined-computer-pass` dispatch below.
# That subprocess re-execs this same file from the top and calls sys.exit()
# from inside `_run_joined_computer_pass()` before ever reaching what used
# to be this block's old position (after DATA/SECTIONS/WIDTHS, further down)
# -- so a joined-computer theme x language pass could not have referenced
# THEMES/LANGS from there. Moving the constants, not duplicating them,
# keeps this the only place either list is spelled out.
THEMES = ["light", "sand", "dark", "night", "dusk"]
LANGS = ["en", "ar"]
# Full SECTIONS x WIDTHS x THEMES x LANGS would be ~3000 screenshots -- see
# this file's own docstring for why that is not what runs by default.
THEME_MATRIX_SECTIONS = ["dashboard", "pos", "trial-balance"]

# v5 (round 2, final pass) -- mutation-proof knob for `_attach_response_
# watcher()` below. Set to a Playwright URL glob (e.g.
# '**/api/sub/retail/products*') to make every matching request, on every
# page this file drives, fail with a real HTTP 500 instead of reaching the
# backend -- reproduces the adversarial verifier's own mutant on demand,
# without hand-editing this file for every proof. Never set outside a
# mutation proof: a real sweep leaves this unset and every request reaches
# the real backend.
INJECT_500_GLOB = os.environ.get("UI_SHOT_MUTATION_INJECT_500")


def _maybe_inject_500(page):
    if INJECT_500_GLOB:
        page.route(INJECT_500_GLOB, lambda route: route.fulfill(status=500, body="{}"))


def _attach_response_watcher(page, prefix, sink, allow_401_403=False):
    """v5 (round 2, final pass): the render guards `shot()` /
    `section_render_mismatch()` run prove a box rendered and the right
    section is behind it -- neither can tell a section that loaded its own
    data from one whose data fetch failed and got quietly swallowed. The
    app catches a failed load in its own `console.error`, which never
    raises `pageerror`, so a real backend 500 on a section's own API route
    was invisible to every check this file had (proven live: HTTP 500 on
    `/api/sub/retail/products` screenshotted a correct-looking empty state
    against a seeded 6-row catalogue, and nothing in `problems` said so --
    see this file's own docstring).

    Records any `/api/` response whose status is >= 500, or a 4xx other
    than 401/403, as `"<prefix> HTTP <status> <method> <path>"` into
    `sink` (`problems.append` for every page in the main process; a local
    list's `.append` for the joined-computer subprocess, which reports
    back to the parent only through stdout/exit code, never the shared
    `problems` list -- see `_run_joined_computer_pass`'s own docstring).

    `allow_401_403`: True only for the cashier pass and the joined-computer
    pass. A cashier is DELIBERATELY denied by capability-gated routes --
    that denial is the very thing this sweep's cashier pass exists to
    prove -- and a joined, not-yet-approved device is legitimately denied
    by anything that needs a real session. Every other pass authenticates
    as the owner, who this sweep never expects to be denied anything it
    exercises, so a 401/403 there is exactly the kind of capability
    regression this guard exists to catch, not noise to filter.

    One route is exempted from the 401 check regardless of `allow_401_403`:
    `GET /api/auth/session` is, BY ITS OWN DOCSTRING
    (commercial_runtime/identity/onboarding_routes.py `get_session`), "the
    mechanism a fresh client uses to LEARN whether it is authenticated at
    all" -- app-shell.js's `init()` calls it unconditionally, before
    knowing whether a session exists, on every `page.goto(BASE)` this file
    does. An anonymous caller getting a plain 401 there is the route's
    OWN documented, tested contract (`test_employee_admin_routes.py`
    asserts `== 401` for a logged-out caller), not a failure -- caught live
    the first time this guard ran clean with no mutation at all and still
    reported `desktop HTTP 401 GET /api/auth/session`, which would have
    made a truly clean run report a problem on every single run, exactly
    the kind of guard that trains a crew to stop reading `problems`. This
    is the only exemption this function grants beyond `allow_401_403` --
    every other route, on every pass, keeps the check."""
    def _on_response(resp):
        try:
            url = resp.url
            if "/api/" not in url:
                return
            status = resp.status
            if status < 400:
                return
            path = "/api/" + url.split("/api/", 1)[-1]
            if status == 401 and path.split("?", 1)[0] == "/api/auth/session":
                return
            if allow_401_403 and status in (401, 403):
                return
            sink("%s HTTP %s %s %s" % (prefix, status, resp.request.method, path))
        except Exception:
            pass  # a response-inspection error must never crash the sweep itself
    page.on("response", _on_response)


def _run_joined_computer_pass():
    """A device that has ALREADY joined a shop -- an Owner-issued
    `installation_id` seeded BEFORE the device has ever onboarded --
    legitimately lands on `_waitForShopAccount()`'s "Connecting to your
    shop…" screen the first time it boots (app-shell.js:2005-2009,
    :2238-2266). That is the exact state item #0's own trap analysis
    names as the bug in reverse: seeding the licence before signup breaks
    a FRESH, never-joined install, but it is the CORRECT, intentional
    state for a device that really did join one. Captured once, here.

    Runs as a SEPARATE PROCESS, not a function called from the main sweep,
    because `products/retail/backend/config.py` reads `AURA_APP_DATA` into
    a MODULE-LEVEL global (`DATABASE_DIR`) the instant it is first
    imported. Re-importing an already-imported module is a no-op in
    CPython, so a second `AURA_STANDALONE` backend with a different data
    dir cannot be booted inside the same interpreter no matter what the
    environment variable is changed to afterwards -- a subprocess is the
    only way to get a second, independently configured backend at all.
    """
    data = Path(tempfile.mkdtemp(prefix="aura_ui_shots2_joined_"))
    (data / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
    os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                       AURA_APP_DATA=str(data))
    os.environ.pop("AURA_DEV", None)
    for _p in (str(REPO_ROOT), str(BACKEND_DIR)):
        if _p not in sys.path:
            sys.path.insert(0, _p)

    from commercial_runtime.licensing_contracts.test_support import seed_active_license
    # INTENTIONALLY before any onboarding -- this IS the joined-device
    # state, reproduced on purpose instead of by accident.
    seed_active_license(str(data), product_code="AURA_RETAIL", platform="WINDOWS")

    import app as _app_module
    flask_app = _app_module.init_app()
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    port = int(os.environ.get("UI_SHOT_JOINED_PORT", "5701"))
    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", port, flask_app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(1.5)
    base = "http://127.0.0.1:%d" % port

    out = Path(os.environ.get("UI_SHOT_DIR", "ui-shots")).resolve()
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright
    ok, msg = True, ""
    # v3 reviewer finding: the owner/cashier/theme passes are not the only
    # ones with a page -- this subprocess drives one too, and it had no
    # pageerror listener either. Collected separately (this process reports
    # back to the parent only via stdout/exit code, not the parent's shared
    # `problems` list) and folded into `ok`/`msg` below.
    joined_pageerrors = []
    # v5 (round 2, final pass): see _attach_response_watcher's own docstring.
    # allow_401_403=True -- this pre-auth pass is legitimately denied by
    # anything that needs a real session, the same allowance the cashier
    # pass gets for the same reason (a capability-gated denial here, not a
    # regression).
    joined_http_problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("pageerror", lambda e: joined_pageerrors.append(str(e)))
        _attach_response_watcher(page, "joined-computer", joined_http_problems.append,
                                  allow_401_403=True)
        _maybe_inject_500(page)
        page.goto(base)
        try:
            page.wait_for_selector("#aura-relogin-modal .auth-title", timeout=20000)
            title = page.locator("#aura-relogin-modal .auth-title").inner_text()
            if "Connecting to your shop" not in title:
                ok, msg = False, "unexpected title %r" % title
            # .auth-card plays a 0.45s entrance animation (authCardIn,
            # main.css:3913) from opacity:0 -- the selector above resolves
            # the instant the element ATTACHES, which is mid-animation, so
            # a screenshot taken immediately caught a near-blank frame with
            # no readable text. Measured live: 700ms was STILL not enough
            # (still blank); 2000ms, matching this file's own onboard()/
            # login() post-transition wait elsewhere, reliably shows the
            # full card.
            page.wait_for_timeout(2000)
            page.screenshot(path=str(out / "joined-computer-01-connecting.png"))
        except Exception as exc:
            ok, msg = False, str(exc).splitlines()[0][:160]
            try:
                page.screenshot(path=str(out / "joined-computer-FAILED.png"))
            except Exception:
                pass

        # v4 (round 2, item #0 continued): 'run ... the joined-computer pass
        # across THEMES x LANGS' -- proves ThemeEngine.apply()/AuraI18n.
        # setLang() do not crash and the modal keeps rendering when driven
        # from this PRE-AUTH screen, a state the main sweep's owner/cashier
        # theme passes never reach (both only ever run after login). Both
        # functions are already internally guarded for a missing
        # `SubsystemApp.active` -- confirmed by reading them, see this
        # file's own docstring -- so this is proving that guard holds under
        # a real `pageerror` listener, not re-deriving it from source. There
        # is no `SECTIONS` walk here: nothing has joined yet, so there is
        # exactly one screen, not a nav to drive -- THEMES x LANGS only.
        theme_problems = []
        if ok:
            for theme in THEMES:
                try:
                    page.evaluate("(t) => ThemeEngine.apply(t)", theme)
                    page.wait_for_timeout(400)
                except Exception as exc:
                    theme_problems.append("theme %s: apply failed: %s" %
                                           (theme, str(exc).splitlines()[0][:120]))
                    continue
                for lang in LANGS:
                    try:
                        page.evaluate("(l) => AuraI18n.setLang(l)", lang)
                        page.wait_for_timeout(700)
                        title_el = page.locator("#aura-relogin-modal .auth-title")
                        title_el.wait_for(state="visible", timeout=5000)
                        text = title_el.inner_text()
                        if not text.strip():
                            theme_problems.append(
                                "theme %s/%s: connecting title went empty" % (theme, lang))
                        page.screenshot(path=str(
                            out / ("joined-computer-theme-%s-%s.png" % (theme, lang))))
                    except Exception as exc:
                        theme_problems.append("theme %s/%s: %s" %
                                               (theme, lang, str(exc).splitlines()[0][:140]))
            # Leave the page in its default state, matching run_theme_matrix's
            # own convention in the main process for anyone reading the plain
            # 'joined-computer-01-connecting.png' shot against it later.
            try:
                page.evaluate("(l) => AuraI18n.setLang(l)", "en")
                page.evaluate("(t) => ThemeEngine.apply(t)", "light")
            except Exception:
                pass
        ctx.close()
        browser.close()
    server.shutdown()
    if joined_pageerrors and ok:
        ok, msg = False, "PAGEERROR %s" % joined_pageerrors[0][:160]
    if joined_http_problems and ok:
        ok, msg = False, "; ".join(joined_http_problems)[:200]
    if theme_problems and ok:
        ok, msg = False, "; ".join(theme_problems)[:200]
    print("JOINED-COMPUTER-OK" if ok else ("JOINED-COMPUTER-FAIL: " + msg))
    sys.exit(0 if ok else 1)


if "--joined-computer-pass" in sys.argv:
    _run_joined_computer_pass()  # calls sys.exit() itself

for _p in (str(REPO_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_ui_shots2_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

# The licence is seeded AFTER onboarding now (see this file's own docstring
# and _run_joined_computer_pass's docstring for the trap this avoids) -- the
# import is kept here, only the call moves, matching evidence/capture.py's
# own fix for the identical mistake.
from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
import app as _app_module  # noqa: E402
flask_app = _app_module.init_app()

import logging  # noqa: E402
logging.getLogger('werkzeug').setLevel(logging.ERROR)

PORT = int(os.environ.get("UI_SHOT_PORT", "5700"))
from werkzeug.serving import make_server  # noqa: E402
server = make_server("127.0.0.1", PORT, flask_app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(1.5)
BASE = "http://127.0.0.1:%d" % PORT

OUT = Path(os.environ.get("UI_SHOT_DIR", "ui-shots")).resolve()
OUT.mkdir(parents=True, exist_ok=True)

NAME, COMPANY = "Aura Owner", "Sweets & Co"
EMAIL, PASSWORD = "owner@shots.local", "ShotsPassword1"
CASHIER_EMAIL, CASHIER_PASSWORD = "cashier@shots.local", "ShotsPassword1"

# Section ids, in app-shell.js's own `systems.retail.nav` order (the single
# source of truth for what SubsystemApp._navigate() accepts) -- item #0:
# quotations, transfers, cheques, receivables, payables and the four
# GL-derived statements were entirely missing from the old list. "The four
# statements" are trial-balance / profit-and-loss / balance-sheet /
# cash-flow BY NAV ID -- 'income-statement' is not a route or section id
# anywhere in this codebase; 'profit-and-loss' is the real one
# (financial_statements.py::income_statement, wired to that id).
SECTIONS = [
    "dashboard", "pos", "products", "categories", "customers", "quotations",
    "promotions", "suppliers", "purchases", "transfers", "returns",
    "reports", "scanner",
    "employees", "branches", "admin-center", "email-notifications", "backup-export",
    "audit-log", "stock-accuracy", "exceptions",
    "cheques", "receivables", "payables",
    "trial-balance", "profit-and-loss", "balance-sheet", "cash-flow",
]

# 1366x768 added (item #0): the exact width item #7's cart/tender-layout
# bugs were measured at, and the one desktop-class width the old list
# skipped entirely (1440, then straight down to the 768-WIDE tablet).
WIDTHS = [
    (1440, 900, "desktop"),
    (1366, 768, "laptop"),
    (768, 1024, "tablet"),
    (390, 844, "phone"),
]
if QUICK:
    WIDTHS = WIDTHS[:1]
    SECTIONS = SECTIONS[:3]

# THEMES / LANGS / THEME_MATRIX_SECTIONS are now defined near the top of this
# file, above the `--joined-computer-pass` dispatch -- see that block's own
# comment for why.

CATEGORIES = ["Grocery", "Coffee & Tea", "Bakery"]
PRODUCTS = [
    # name, sku, sell_price, cost_price, category, unit
    ("Arabic Coffee 250g", "COF-250", 4.500, 2.900, "Coffee & Tea", "pc"),
    ("Green Tea 100 Bags", "TEA-100", 2.250, 1.400, "Coffee & Tea", "pc"),
    ("Basmati Rice 5kg", "GRO-RICE5", 6.900, 5.200, "Grocery", "pc"),
    ("Olive Oil 1L", "GRO-OIL1", 7.750, 5.600, "Grocery", "pc"),
    ("Arabic Bread 5pc", "BAK-BRD5", 0.750, 0.400, "Bakery", "pc"),
    ("Maamoul Box 12pc", "BAK-MML12", 5.500, 3.600, "Bakery", "pc"),
]

from playwright.sync_api import sync_playwright  # noqa: E402

problems = []


def dismiss_intro_overlay(page):
    """The brand intro (brand/intro.html) plays AT MOST ONCE EVER, gated on
    a localStorage key (app-shell.js `_INTRO_PLAYED_KEY`) -- so it WILL be
    showing, full-screen and z-index 100000, the instant a fresh browser
    profile reaches first run. The old onboard() never dismissed it: fill()
    on the signup fields would eventually get through once the overlay's own
    ~6.5s timer tore it down, but any screenshot taken before that timer
    fired showed the intro card instead of the screen it claimed to
    capture. Same approach as evidence/capture.py's own
    dismiss_intro_overlay, and retail_smoke_e2e.py's equivalent helper."""
    try:
        overlay = page.locator("#aura-intro-overlay")
        overlay.wait_for(state="visible", timeout=4000)
        overlay.click()
        overlay.wait_for(state="detached", timeout=3000)
    except Exception:
        pass  # never played this context, or already gone -- fine either way


def onboard(page):
    """Do what a customer does on a fresh install."""
    dismiss_intro_overlay(page)
    page.wait_for_selector("#su-btn", timeout=20000)
    page.fill("#su-name", NAME)
    page.fill("#su-company", COMPANY)
    page.fill("#su-email", EMAIL)
    page.fill("#su-pass", PASSWORD)
    page.fill("#su-pass2", PASSWORD)
    page.click("#su-btn")
    # The shell replaces the setup card; wait for the sidebar to exist.
    page.wait_for_selector("#subsystem-shell .sub-nav-item, #subsystem-shell nav, "
                           "#sub-header-section", timeout=30000)
    page.wait_for_timeout(2000)


def login(page, email, password):
    page.wait_for_selector("input[type=password]", timeout=20000)
    inputs = page.locator("input[type=email], input[type=text]")
    inputs.first.fill(email)
    page.locator("input[type=password]").first.fill(password)
    page.keyboard.press("Enter")
    page.wait_for_selector("#sub-header-section", timeout=30000)
    page.wait_for_timeout(2000)


def seed_shop_data(page):
    """Item #0: 'Seed products with category_id.' The earlier audit's
    'every category has 0 products' finding traced back to evidence/
    capture.py posting a `category` NAME key -- create_product
    (retail_api.py:2046) reads `data.get('category_id')`, not `category`,
    and silently ignores a field it does not recognise, so every seeded
    product landed with `category_id` NULL and every category showed 0
    products. The fixture manufactured the defect it went on to report
    (ENGINEERING.md failure shape 2: "the fixture manufactures the exact
    state that hides the bug"), not the product.

    Posts the real category id here, then reads categories back and checks
    `product_count` itself -- a live, in-script guard against the exact
    same mistake recurring, not just a fixed literal that happens to work
    today."""
    # Status IS checked (reviewer finding, P0 #0 crew report): this is the
    # real `/settings/credit` route (retail_api.py:12379, `credit_settings_
    # set`) which deliberately doubles as the general small-kv "shop money
    # settings" write path -- its own comment there says so explicitly,
    # "carries base_currency alongside the credit fields its name suggests"
    # -- rather than an unrelated/misnamed endpoint. Confirmed against the
    # route body: it iterates a fixed key allowlist that includes
    # 'base_currency' and upserts each key present in the POST body. The
    # response was silently ignored here even though `credit_settings_set`
    # requires CAP_EMPLOYEES, so a scoping/capability regression on this one
    # write would leave every seeded product's price display defaulting to
    # whatever currency the deployment falls back to, with nothing in this
    # script's own `problems` list to say why -- the exact silent-failure
    # shape category seeding below (lines checking `resp.ok`) already
    # guards against.
    resp = page.request.post(BASE + "/api/sub/retail/settings/credit", data={"base_currency": "JOD"})
    if not resp.ok:
        problems.append("seed settings: credit failed HTTP %s" % resp.status)

    cat_ids = {}
    for cat_name in CATEGORIES:
        resp = page.request.post(BASE + "/api/sub/retail/categories", data={"name": cat_name})
        if resp.ok:
            cat_ids[cat_name] = ((resp.json() or {}).get("data") or {}).get("id")
        else:
            problems.append("seed category %r failed: HTTP %s" % (cat_name, resp.status))

    for name, sku, price, cost, cat_name, unit in PRODUCTS:
        category_id = cat_ids.get(cat_name)
        if not category_id:
            problems.append("seed product %r: no category id for %r" % (name, cat_name))
            continue
        resp = page.request.post(BASE + "/api/sub/retail/products", data={
            "name": name, "sku": sku, "sell_price": price, "cost_price": cost,
            "category_id": category_id, "unit": unit,
        })
        if not resp.ok:
            problems.append("seed product %r failed: HTTP %s" % (name, resp.status))

    # The actual regression guard: every category we just filled must come
    # back with product_count > 0. Reverting `category_id=` above to the
    # old `category=` name key reproduces the audit finding exactly --
    # every count here goes back to 0 -- which is how this was mutation-
    # proved (see the crew report for the before/after run).
    resp = page.request.get(BASE + "/api/sub/retail/categories")
    rows = ((resp.json() or {}).get("data") or []) if resp.ok else []
    counts = {r.get("name"): r.get("product_count") for r in rows}
    for cat_name in CATEGORIES:
        if not counts.get(cat_name):
            problems.append("category %r has 0 products after seeding "
                             "(category_id trap regressed)" % cat_name)


def seed_cashier(page):
    """Creates a real, active cashier account through the same two-step
    invite/self-setup flow the product actually uses -- POST
    /api/admin/employees (onboarding_routes.py:992) returns a one-time
    setup link, POST /api/auth/employee/setup (:1997) consumes it and sets
    the password -- rather than writing a `users` row directly. The
    cashier PASS exists to screenshot the product's own cashier
    experience, and that experience is reached through this exact flow on
    every real till, never a hand-inserted row."""
    resp = page.request.post(BASE + "/api/admin/employees",
                              data={"email": CASHIER_EMAIL, "role": "cashier"})
    body = resp.json() if resp.ok else {}
    setup_url = (body or {}).get("setup_link", "")
    token = setup_url.rsplit("/#setup/", 1)[-1] if "/#setup/" in setup_url else ""
    if not token:
        problems.append("seed cashier: no setup token in response %r" % body)
        return False
    resp2 = page.request.post(BASE + "/api/auth/employee/setup",
                               data={"token": token, "password": CASHIER_PASSWORD})
    if not resp2.ok:
        problems.append("seed cashier: employee/setup failed HTTP %s" % resp2.status)
        return False
    return True


def sub_content_box(page):
    """Rendered geometry, not `is_visible()` -- ENGINEERING.md's own rule:
    is_visible() was measured VACUOUS on the customer display (it returned
    true while a specificity bug kept the element painted at display:none
    behind an id rule). A bounding box is the same category of proof this
    repo already trusts for a screen having actually rendered."""
    try:
        return page.locator("#sub-content").bounding_box()
    except Exception:
        return None


def stuck_on_connecting(page):
    try:
        return page.get_by_text("Connecting to your shop").count() > 0
    except Exception:
        return False


def section_render_mismatch(page, section):
    """v3 reviewer finding (medium): the old 'rendered' guard proved only
    that SOMETHING with a bounding box exists, which a mistyped or deleted
    section id satisfies just as well as a real screen -- subsystem-
    retail.js's render switch has a `default:` case that writes
    `<h2>${sectionId}</h2><p>Coming soon.</p>` into #sub-content, a
    perfectly real, non-zero box. Mutant M3 (SECTIONS + one bogus id)
    screenshotted that card and the run still exited 0.

    Two independent checks, read LIVE from the page rather than a second
    hardcoded section list here that could itself drift from app-shell.js's
    real one (the same trap item #0's own SECTIONS list was built to
    avoid):
      1. #sub-content must not contain the literal 'Coming soon.' fallback
         text -- directly catches "no matching case in the render switch".
      2. `_navigate()` unconditionally writes the matching nav entry's
         translated label into #sub-header-section BEFORE dispatching to a
         renderer (app-shell.js:3384-3387), falling back to the RAW,
         untranslated section id when `sys.nav.find(...)` finds no entry --
         so an id absent from `systems.retail.nav` shows up as its own name
         in the header instead of a real label. Recomputing the expected
         header with the page's own `window.t` (rather than guessing a
         translation from Python) means this check works unchanged across
         every theme/language combination the theme pass exercises.
    Returns None when the section rendered correctly, else a message
    naming which check failed and why."""
    try:
        info = page.evaluate(
            """(sectionId) => {
                const sys = (window.SubsystemApp && SubsystemApp.systems)
                    ? SubsystemApp.systems['retail'] : null;
                const navItem = sys ? sys.nav.find(n => n.id === sectionId) : null;
                const label = navItem ? navItem.label : sectionId;
                const expectedHeader = window.t ? window.t(label) : label;
                const headerEl = document.getElementById('sub-header-section');
                const contentEl = document.getElementById('sub-content');
                return {
                    known: !!navItem,
                    header: headerEl ? headerEl.innerText : null,
                    expectedHeader: expectedHeader,
                    comingSoon: !!(contentEl && contentEl.innerText.indexOf('Coming soon.') !== -1),
                };
            }""",
            section,
        )
    except Exception as exc:
        return "section-render check failed: %s" % str(exc).splitlines()[0][:140]
    if not info:
        return None  # evaluate gave nothing usable -- do not fabricate a failure from it
    if info.get("comingSoon"):
        return ("rendered the 'Coming soon.' fallback for section %r "
                 "(no matching case in subsystem-retail.js's render switch)" % section)
    if not info.get("known"):
        return "%r has no entry in systems.retail.nav" % section
    if info.get("header") != info.get("expectedHeader"):
        return ("#sub-header-section is %r, expected %r for section %r" %
                 (info.get("header"), info.get("expectedHeader"), section))
    return None


def shot(page, path, label, allow_connecting=False, section=None):
    """Item #0: 'On every shot, assert that #sub-content rendered and that
    no "Connecting to your shop" text is present.' A screenshot alone
    cannot tell "the screen is there" from "still on the licensing wait
    screen" -- both produce a PNG. Failures are appended to `problems`
    (feeding the non-zero exit) rather than raised, matching this file's
    existing "record and continue" convention for navigation failures --
    one bad section must not abort the whole sweep.

    `section`, when given, additionally proves the RIGHT screen rendered
    (v3 reviewer finding) via `section_render_mismatch()` -- see that
    function's own docstring. Left None for shots with no single section
    behind them (the post-login landing shot, the joined-computer
    "Connecting…" capture) where there is nothing meaningful to compare
    against."""
    ok = True
    box = sub_content_box(page)
    if not box or box["width"] <= 0 or box["height"] <= 0:
        problems.append("%s: #sub-content did not render (box=%r)" % (label, box))
        ok = False
    if not allow_connecting and stuck_on_connecting(page):
        problems.append("%s: stuck on 'Connecting to your shop' text" % label)
        ok = False
    if section is not None:
        mismatch = section_render_mismatch(page, section)
        if mismatch:
            problems.append("%s: %s" % (label, mismatch))
            ok = False
    try:
        page.screenshot(path=str(path))
    except Exception as exc:
        problems.append("%s: screenshot failed: %s" % (label, str(exc).splitlines()[0][:160]))
        ok = False
    return ok


def walk_sections(page, sections, prefix, start=1):
    """Navigate + screenshot every section id for the already-authenticated
    `page`. Shared by the owner and cashier passes so the two cannot drift
    apart on how a shot is taken or checked. Passes `section=section`
    through to `shot()` (v3 reviewer finding) so a mistyped or removed
    section id is caught here instead of silently screenshotting the app's
    own 'Coming soon.' fallback."""
    for i, section in enumerate(sections, start=start):
        try:
            page.evaluate("(s) => SubsystemApp._navigate(s)", section)
            page.wait_for_timeout(1600)
            shot(page, OUT / ("%s-%02d-%s.png" % (prefix, i, section)),
                 "%s %s" % (prefix, section), section=section)
        except Exception as exc:
            problems.append("%s %s: %s" % (prefix, section, str(exc).splitlines()[0][:140]))


def run_theme_matrix(page, sections, prefix):
    """Apply every THEMES x LANGS combination and screenshot `sections` for
    the already-authenticated `page`, checked the same way `walk_sections()`
    checks the owner/cashier nav passes (same `shot(..., section=section)`
    call, so a themed shot gets the identical pageerror-independent
    render-guard as an un-themed one).

    v4 (round 2, item #0 continued -- 'run the cashier pass and the
    joined-computer pass across THEMES x LANGS ... not only the owner'):
    factored out of what used to be the owner theme pass's own inline loop
    so the owner and cashier theme passes cannot drift apart on how a
    themed shot is taken or checked, the identical reason `walk_sections()`
    is already shared between the owner and cashier NAV passes. `prefix`
    (e.g. 'owner', 'cashier') becomes part of every filename and problem
    message so a failure is never ambiguous about which pass produced it."""
    for theme in THEMES:
        page.evaluate("(t) => ThemeEngine.apply(t)", theme)
        page.wait_for_timeout(400)
        for lang in LANGS:
            page.evaluate("(l) => AuraI18n.setLang(l)", lang)
            page.wait_for_timeout(700)
            for section in sections:
                try:
                    page.evaluate("(s) => SubsystemApp._navigate(s)", section)
                    page.wait_for_timeout(1200)
                    shot(page, OUT / ("theme-%s-%s-%s-%s.png" % (prefix, theme, lang, section)),
                         "theme %s %s/%s %s" % (prefix, theme, lang, section), section=section)
                except Exception as exc:
                    problems.append("theme %s %s/%s %s: %s" %
                                     (prefix, theme, lang, section, str(exc).splitlines()[0][:140]))
    # Leave the shared session back in its default state for anyone reading
    # this run's other screenshots against it.
    page.evaluate("(l) => AuraI18n.setLang(l)", "en")
    page.evaluate("(t) => ThemeEngine.apply(t)", "light")


with sync_playwright() as p:
    browser = p.chromium.launch()

    # ── One-time setup: sign up, activate the licence, claim the admin ──────
    # device, seed data, seed a cashier. All of this is BACKEND state (the
    # SQLite file under DATA), not per-browser-context state, so it needs
    # doing exactly once regardless of how many viewport contexts follow.
    setup_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    setup_page = setup_ctx.new_page()
    # v4 (round 2): this page had NO pageerror listener and none of this
    # one-time setup was wrapped -- an exception anywhere in it (onboarding,
    # licence reload, seeding) crashed the whole process with a raw Python
    # traceback instead of this file's own "record to `problems`, exit 1"
    # convention every later pass already follows. Hit live during this
    # round's own H2 end-to-end run, from a genuinely transient cause (a
    # concurrent crew's mid-edit syntax error in app-shell.js, self-resolved
    # within ~2 minutes -- see the crew report) rather than a defect here,
    # but the harness's OWN response to it -- an uncaught traceback -- was
    # exactly the "cannot fail cleanly" shape item #0 exists to close.
    # Seeding is a hard prerequisite for every pass that follows, so on
    # failure this exits early rather than attempting a degraded sweep.
    setup_page.on("pageerror", lambda e: problems.append("setup PAGEERROR %s" % e))
    # v5 (round 2, final pass): see _attach_response_watcher's own docstring.
    _attach_response_watcher(setup_page, "setup", problems.append)
    _maybe_inject_500(setup_page)
    try:
        setup_page.goto(BASE)
        onboard(setup_page)
        # No separate "claim admin device" step needed: _setupSubmit() calls
        # _autoClaimAdminOnSignup() (app-shell.js:1954, P0 #20) unconditionally
        # right after create-admin succeeds, so THIS device is already the
        # admin device by the time onboard() returns. evidence/capture.py's own
        # `_claim_admin()` step predates that fix and is dead code against the
        # current app-shell.js -- confirmed live: the claim banner never
        # appears here because there is nothing left for it to offer.

        # Seeded AFTER signup, never before -- see this file's own docstring
        # and evidence/capture.py's identical fix for the identical trap.
        seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
        setup_page.reload()
        setup_page.wait_for_selector("#sub-header-section", timeout=20000)
        setup_page.wait_for_timeout(1000)

        seed_shop_data(setup_page)
        seed_cashier(setup_page)
    except Exception as exc:
        problems.append("one-time setup: %s" % str(exc).splitlines()[0][:200])
        try:
            setup_page.screenshot(path=str(OUT / "setup-FAILED.png"))
        except Exception:
            pass
        browser.close()
        server.shutdown()
        print("\nPROBLEMS (%d):" % len(problems))
        for p_ in problems:
            print("  -", p_[:200])
        sys.exit(1)  # seeding is a hard prerequisite -- no later pass can run without it
    # A `setup PAGEERROR` recorded by the listener above WITHOUT raising
    # (onboarding still completed) is left in `problems` and NOT treated as
    # fatal here -- it falls through to the rest of the sweep and the same
    # final `if problems: ... sys.exit(1)` every other pass's failures
    # already go through, matching `shot()`'s own "record and continue"
    # convention: one bad signal during setup must not throw away every
    # later pass's screenshots, which are still useful evidence.
    setup_ctx.close()

    # ── Joined-computer pass: a separate subprocess, a separate throwaway
    # AURA_APP_DATA (see _run_joined_computer_pass's own docstring). -------
    if not QUICK:
        try:
            proc = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--joined-computer-pass"],
                env={**os.environ, "UI_SHOT_DIR": str(OUT)},
                capture_output=True, text=True, timeout=90,
            )
            print(proc.stdout.strip())
            if proc.returncode != 0:
                problems.append("joined-computer pass: %s" %
                                 (proc.stderr or proc.stdout).strip().splitlines()[-1][:200])
        except Exception as exc:
            problems.append("joined-computer pass crashed: %s" % str(exc).splitlines()[0][:160])

    # ── Main owner sweep, across every width ────────────────────────────────
    for w, h, label in WIDTHS:
        ctx = browser.new_context(viewport={"width": w, "height": h})
        page = ctx.new_page()
        page.on("pageerror", lambda e, L=label: problems.append("%s PAGEERROR %s" % (L, e)))
        # v5 (round 2, final pass): see _attach_response_watcher's own docstring.
        _attach_response_watcher(page, label, problems.append)
        _maybe_inject_500(page)

        page.goto(BASE)
        try:
            login(page, EMAIL, PASSWORD)
        except Exception as exc:
            problems.append("%s auth: %s" % (label, str(exc).splitlines()[0][:160]))
            page.screenshot(path=str(OUT / ("%s-AUTH-FAILED.png" % label)))
            ctx.close()
            continue

        shot(page, OUT / ("%s-01-landing.png" % label), "%s landing" % label)
        walk_sections(page, SECTIONS, label, start=2)
        ctx.close()

    # ── Cashier pass: capability-gated nav, one representative width. ──────
    # A cashier lacks retail.reports/retail.employees/etc, so several of
    # these sections will legitimately render a permission message instead
    # of the owner's screen -- that is the point of this pass, not a bug in
    # it, and #sub-content still renders a real box either way.
    if not QUICK:
        cashier_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        cashier_page = cashier_ctx.new_page()
        # v3 reviewer finding: this listener used to exist ONLY on the owner
        # width-loop pages, so a JS exception thrown only under the
        # capability-gated cashier nav was dropped on the floor.
        cashier_page.on("pageerror", lambda e: problems.append("cashier PAGEERROR %s" % e))
        # v5 (round 2, final pass): see _attach_response_watcher's own
        # docstring -- allow_401_403=True here, this pass is DELIBERATELY
        # denied by capability-gated routes.
        _attach_response_watcher(cashier_page, "cashier", problems.append, allow_401_403=True)
        _maybe_inject_500(cashier_page)
        cashier_page.goto(BASE)
        try:
            login(cashier_page, CASHIER_EMAIL, CASHIER_PASSWORD)
            shot(cashier_page, OUT / "cashier-01-landing.png", "cashier landing")
            walk_sections(cashier_page, SECTIONS, "cashier", start=2)
            # v4 (round 2, item #0 continued): 'run the cashier pass ... across
            # THEMES x LANGS ... not only the owner' -- the theme x language
            # matrix used to authenticate as the OWNER only, so a theme/RTL
            # regression reachable exclusively through capability-gated
            # cashier rendering (a permission-message branch has its own
            # markup) was never exercised in any theme but light/en.
            run_theme_matrix(cashier_page, THEME_MATRIX_SECTIONS, "cashier")
        except Exception as exc:
            problems.append("cashier auth: %s" % str(exc).splitlines()[0][:160])
            cashier_page.screenshot(path=str(OUT / "cashier-AUTH-FAILED.png"))
        cashier_ctx.close()

    # ── Theme x language matrix, owner. Representative screens, one width. ──
    if not QUICK:
        theme_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        theme_page = theme_ctx.new_page()
        # v3 reviewer finding: this pass ran with NO pageerror listener at
        # all -- an uncaught throw injected here (mutant M4) still exited 0
        # ("no page errors"), while the identical throw in the owner pass
        # (which did have the listener, control M4c) correctly exited 1.
        theme_page.on("pageerror", lambda e: problems.append("theme PAGEERROR %s" % e))
        # v5 (round 2, final pass): see _attach_response_watcher's own docstring.
        _attach_response_watcher(theme_page, "theme", problems.append)
        _maybe_inject_500(theme_page)
        theme_page.goto(BASE)
        try:
            login(theme_page, EMAIL, PASSWORD)
            # v4: this used to be an inline loop here -- now shared with the
            # cashier theme pass above via run_theme_matrix() (see its own
            # docstring) so the two cannot drift apart.
            run_theme_matrix(theme_page, THEME_MATRIX_SECTIONS, "owner")
        except Exception as exc:
            problems.append("theme matrix auth: %s" % str(exc).splitlines()[0][:160])
        theme_ctx.close()

    browser.close()

server.shutdown()
shots = sorted(OUT.glob("*.png"))
print("screenshots: %d in %s" % (len(shots), OUT))
if problems:
    print("\nPROBLEMS (%d):" % len(problems))
    seen = set()
    for p_ in problems:
        k = p_[:120]
        if k in seen:
            continue
        seen.add(k)
        print("  -", p_[:200])
    sys.exit(1)  # item #0: a run with problems must not report success.
else:
    print("no page errors, no navigation failures, no rendering failures")
    sys.exit(0)
