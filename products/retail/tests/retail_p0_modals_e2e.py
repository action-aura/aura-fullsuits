"""Aura Retail -- real-browser check for cash-drawer.js and import-wizard.js
modals (Phase 0 items #4 and #15(a); reassigned to crew shell-display for
round 2 -- C5 modals had no round-2 crew, see
.claude/artifacts/aura-review/p0/r2_verify_digest.txt "fix:shell-display"
and the [high] #4 defect it responds to).

============================================================================
STANDALONE SCRIPT. THIS IS **NOT** A PYTEST FILE. DO NOT RUN IT VIA PYTEST.
============================================================================
Playwright is installed under the SYSTEM Python
(``C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe``), NOT
under this repo's pytest venv. Run it directly:

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_p0_modals_e2e.py

WHY THIS FILE EXISTS, ON TOP OF retail_p0_modals_test.js
----------------------------------------------------------------------------
retail_modal_a11y_coverage_test.js (widened by item #4) proves the
STRUCTURE is right: an id, role="dialog", aria-modal, an aria-labelledby
that resolves, and no bare .remove() left in the source -- a text scan.
retail_p0_modals_test.js drives the REAL _wireModalA11y / CashDrawer /
ImportWizard functions through a stubbed VM DOM -- real code, fake
elements. Neither runs a real browser, and a reviewer of item #4 found that
gap explicitly: the only real-browser proof of these two files' overlays
lived in job scratch, nothing in the repo would re-prove it. THIS file is
that proof -- an actual Chromium, actual innerHTML parsing, actual inline
onclick attributes, actual keyboard event capture/bubbling, actual CSS
layout, against a real Flask backend booted the same way the desktop app
boots it.

COVERAGE (the reviewer's own list, item #4 re-review)
----------------------------------------------------------------------------
  - All 5 cash-drawer overlays: Open Shift, Cash In/Out, X Report,
    Drawer History, End Shift (Z report).
  - Both Import Wizard shells: the numbered per-entity wizard (_shell,
    reached via ImportWizard.open) and the Smart Import wizard (_smartShell,
    reached via ImportWizard.openSmart).
  - A _confirm stacked on top of a cash-drawer modal.
  - A real wizard STEP TRANSITION (step 1 -> step 2 via an actual file
    upload through /api/import/parse), not a simulated one.
  - Item #15(a): the drawer-close variance preview rounds at the currency's
    own minor unit (3dp for JOD), not a hardcoded 2dp.

ONE HONEST GAP, NAMED RATHER THAN HIDDEN: no button inside cash-drawer.js
itself ever raises RetailSystem._confirm() on top of one of its own
modals (the only in-repo caller of _confirm() from a stacked context is
subsystem-retail.js's own modals, which retail_modal_stack_test.js already
covers). The "confirm stacked on Cash In/Out" check below therefore raises
_confirm() programmatically via page.evaluate() while the real Cash In/Out
overlay is genuinely open in the real DOM -- everything downstream of that
(the real _modalKeyStack, the real document keydown listeners, the real
focus/Escape handling) is exactly as real as every other check in this
file; only the trigger is synthetic, because nothing in the product
provides a real one to click today. retail_p0_modals_test.js's docstring
already proves the same scenario in a VM sandbox; this is its real-DOM
twin.

KEYDOWN-LISTENER INSTRUMENTATION
----------------------------------------------------------------------------
The wizard step-transition check needs to prove that moving from step 1 to
step 2 tears down step 1's document-level keydown listener before step 2's
is registered (see import-wizard.js's own comment on this, right above
_smartShell: a bare overlay replacement used to leak one keydown listener
PER STEP, permanently). A real browser gives no direct way to count
`document`'s registered listeners, so an init script installed BEFORE the
app's own scripts run wraps `Document.prototype.addEventListener` /
`removeEventListener` for the 'keydown' type only, tracking live handlers
in `window.__kdHandlers`. This changes nothing about how the app itself
behaves -- it is a passive counter, not a stub -- so it is not the same
category of risk as stubbing the DOM: real code still runs, real events
still fire, this only makes an otherwise-invisible internal count visible.

BASELINE IS NOT ZERO -- READ BEFORE PUTTING A LITERAL NUMBER BACK INTO ANY
kd_count() ASSERTION (round-2 fix, item #4 [high])
----------------------------------------------------------------------------
The very first version of this file asserted the modal open/stacked/closed
keydown count was exactly 1, 2 and 0. That assumed a document with NOTHING
ELSE listening for keydown. False on this product: RetailSystem._initScanner
and RetailSystem._initShortcuts each register one PERMANENT document
keydown listener the moment the POS section first renders (subsystem-
retail.js:6004/6288, called from render() at :398/:399) and never remove
it -- correctly so, they are meant to live for the section's whole
lifetime, not per-modal. On this product that permanent baseline is 2, so
the old literal assertions failed 8 of 48 checks with the exact signature
"count is 2 higher than expected", even though every one of the checks
underneath that arithmetic was passing correctly. The product was right;
the test's assumed baseline of zero was wrong.

The fix: `kd_base = kd_count(page)` is captured once, right after the POS
section has mounted and CashDrawer.refresh() has run, and every later
keydown-count check compares against `kd_base + N` (one modal's own
handler on top of the permanent baseline), never a literal. A companion
check right after capturing kd_base (CHECK GROUP 0b) also proves kd_base
itself does not drift across a POS remount (navigate away, navigate back)
-- a real leak guard: if _initScanner/_initShortcuts ever stopped being
idempotent and registered a SECOND pair of permanent listeners on remount,
kd_base would climb and this check would catch it, independently of
anything cash-drawer.js or import-wizard.js do.

MUTATION-PROVED (round-2 fix; see AURA_P0_MODALS_E2E_MUTATE_UNWIRE below)
----------------------------------------------------------------------------
subsystem-retail.js's _wireModalA11y() is NOT a file this crew owns for
round 2 (the till crews do, and edit it concurrently in this same worktree
-- contracts.md's "own only your files" / "do not reach" rules forbid
editing it, and hand-editing-then-restoring another crew's live file for a
mutation proof risks clobbering a concurrent edit even done correctly). So
this proof mutates BEHAVIOUR, not source: with
AURA_P0_MODALS_E2E_MUTATE_UNWIRE=1, the keydown-counter init script below
turns close()'s own `document.removeEventListener('keydown', onKeydown,
true)` call into a genuine no-op, but ONLY for listeners whose
registration-time call stack shows they came from _wireModalA11y
specifically (the same registration-stack technique the round-2 verifier
used to tell the two permanent listeners apart from the modal ones -- see
p0/r2_verify_digest.txt line ~174). The real browser listener registry is
left holding the stale handler, exactly as if close()'s
removeEventListener line had been deleted; _confirm()'s own, separate
close path (also capture-phase, but tagged with a different stack) is
untouched. Run once with the flag unset (GREEN), once with it set to '1'
(RED -- every kd_base-relative check after a Cash In/Out close or a wizard
close fails because the count never drops back to kd_base), then unset
again to confirm the restore is clean. Both outputs are quoted in the
Phase 0 shell-display round-2 report.

CRASH CONTAINMENT (round-2 fix, item #4 [high], secondary complaint)
----------------------------------------------------------------------------
Every numbered CHECK GROUP below runs through run_group(), which catches
ANY exception (a Playwright TimeoutError above all -- a selector that never
appears because of a real product regression) and records it as one FAIL
line instead of letting it kill the process. Before this fix, a single
stuck selector in an early group took down every check after it with a raw
traceback and no "TOTAL CHECKS" line at all, so a reader had no idea how
many of the OTHER, unrelated checks in the file were fine. The groups still
share one continuous session rather than being independent scenarios, so a
crash early can still cascade into FAILs in later groups (a session that
never opened will fail every group that needs one open) -- but every one
of those is now a named FAIL line, never a crash.

PORTS
----------------------------------------------------------------------------
Reads AURA_P0_MODALS_E2E_PORT_BASE (default 5750, the historical C5 range)
and tries five ports from there. Round-2 fixer shell-display owns
5840-5849 (contracts.md's port table); set
AURA_P0_MODALS_E2E_PORT_BASE=5840 to run this file without colliding with
another crew's concurrent process in the same worktree.

EXIT CODE
----------------------------------------------------------------------------
0 if every check passed. Non-zero otherwise, including a harness failure
(no browser binary, backend never came up, etc).
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[3]
BACKEND_DIR = REPO / "products" / "retail" / "backend"
for _p in (str(REPO), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_c5_p0_modals_e2e_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

# Seed AFTER signup, not before -- a licence seeded before signup makes
# _isJoinedDevice() true and the app hangs on "Connecting to your shop...".
from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

import app as _app_module  # noqa: E402
flask_app = _app_module.init_app()

import logging  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)

from werkzeug.serving import make_server  # noqa: E402

# Round-2 fix (item #4 [high], secondary complaint): this used to be
# hardcoded to the old C5 range even after C5 stopped having a round-2
# crew. Whoever runs this file next owns a DIFFERENT port block and needs
# to run alongside other crews' processes in the same worktree without
# colliding -- see contracts.md's port table.
PORT_BASE = int(os.environ.get("AURA_P0_MODALS_E2E_PORT_BASE", "5750"))

import socket  # noqa: E402


def port_is_free(port):
    """M8 harness-port fix: werkzeug's make_server sets SO_REUSEADDR, which on
    Windows lets a second listener bind a port another process already holds,
    so a try/except around make_server "succeeds" on an occupied port and the
    browser then talks to a stranger's server. Connect probe (is anything
    listening?) plus a plain bind WITHOUT SO_REUSEADDR (is it held at all?)
    must both say free first."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


server = None
PORT = None
for candidate in range(PORT_BASE, PORT_BASE + 5):
    if not port_is_free(candidate):
        continue
    try:
        server = make_server("127.0.0.1", candidate, flask_app, threaded=True)
        PORT = candidate
        break
    except OSError:
        continue
if server is None:
    print(f"FATAL: no free port {PORT_BASE}-{PORT_BASE + 4}")
    sys.exit(2)

thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
time.sleep(1.5)
BASE = f"http://127.0.0.1:{PORT}"
print(f"Backend up at {BASE}  AURA_APP_DATA={DATA}")

RESULTS = {"checks": [], "pass": True}


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS["checks"].append({"name": name, "ok": ok, "detail": detail})
    RESULTS["pass"] = RESULTS["pass"] and ok
    print(("PASS" if ok else "FAIL") + f": {name}" + (f" -- {detail}" if detail else ""))


def run_group(label, fn):
    # Round-2 fix (item #4 [high]): contain a crash to one FAIL line
    # instead of letting it kill the whole process before "TOTAL CHECKS"
    # ever prints -- see the module docstring's CRASH CONTAINMENT section.
    try:
        fn()
    except Exception as e:  # noqa: BLE001 -- deliberately broad: a harness
        # script must survive EVERY exception shape a stuck selector, a
        # missing element or a bad evaluate() can throw, not just
        # Playwright's own TimeoutError.
        check(f"{label}: ran to completion without raising", False,
              f"{type(e).__name__}: {e}")


def dismiss_intro_overlay(page):
    try:
        overlay = page.locator("#aura-intro-overlay")
        overlay.wait_for(state="visible", timeout=3000)
        overlay.click()
        overlay.wait_for(state="detached", timeout=3000)
    except Exception:
        pass


# Round-2 fix (item #4 [high]): the mutation-proof mode. See the module
# docstring's MUTATION-PROVED section for why this mutates DOM behaviour
# instead of subsystem-retail.js's source. Off (kd_count behaves exactly as
# it did before this fix) unless AURA_P0_MODALS_E2E_MUTATE_UNWIRE=1.
MUTATE_UNWIRE = os.environ.get("AURA_P0_MODALS_E2E_MUTATE_UNWIRE") == "1"

# Passive keydown-listener counter -- see module docstring. Installed via
# add_init_script so it wraps the prototype before ANY app script runs.
# Each live handler is tracked with its OWN registration-time stack trace,
# not just counted, so the mutation flag above can single out listeners
# that came from _wireModalA11y specifically without also touching
# _confirm's separate, otherwise-identical close path.
KEYDOWN_COUNTER_INIT_SCRIPT = (
    """
(function () {
  var MUTATE_UNWIRE = %s;
  var addOrig = Document.prototype.addEventListener;
  var remOrig = Document.prototype.removeEventListener;
  window.__kdHandlers = [];
  Document.prototype.addEventListener = function (type, fn, opts) {
    if (type === 'keydown') {
      window.__kdHandlers.push({ fn: fn, stack: (new Error()).stack || '' });
    }
    return addOrig.call(this, type, fn, opts);
  };
  Document.prototype.removeEventListener = function (type, fn, opts) {
    if (type === 'keydown') {
      var i = -1;
      for (var k = 0; k < window.__kdHandlers.length; k++) {
        if (window.__kdHandlers[k].fn === fn) { i = k; break; }
      }
      if (i !== -1) {
        var fromWireModalA11y = window.__kdHandlers[i].stack.indexOf('_wireModalA11y') !== -1;
        if (MUTATE_UNWIRE && fromWireModalA11y) {
          // Mutation: behave as if close() never called
          // removeEventListener at all -- neither our counter nor the
          // real DOM registry drops this handler. See module docstring,
          // MUTATION-PROVED.
          return;
        }
        window.__kdHandlers.splice(i, 1);
      }
    }
    return remOrig.call(this, type, fn, opts);
  };
})();
"""
    % ("true" if MUTATE_UNWIRE else "false")
)


def kd_count(page):
    return page.evaluate("() => window.__kdHandlers.length")


def dialog_attrs(page, dialog_id):
    return page.evaluate(
        """(id) => {
            const dlg = document.getElementById(id);
            if (!dlg) return null;
            const lbl = dlg.getAttribute('aria-labelledby');
            return {
                role: dlg.getAttribute('role'),
                ariaModal: dlg.getAttribute('aria-modal'),
                labelledby: lbl,
                labelResolves: !!(lbl && document.getElementById(lbl)),
            };
        }""",
        dialog_id,
    )


def check_dialog_semantics(page, label, dialog_id):
    attrs = dialog_attrs(page, dialog_id)
    check(f"{label}: role=dialog", attrs and attrs["role"] == "dialog", json.dumps(attrs))
    check(f"{label}: aria-modal=true", attrs and attrs["ariaModal"] == "true", json.dumps(attrs))
    check(f"{label}: aria-labelledby resolves", attrs and attrs["labelResolves"], json.dumps(attrs))


from playwright.sync_api import sync_playwright  # noqa: E402

with sync_playwright() as p:
    browser = p.chromium.launch()
    browser_ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    browser_ctx.add_init_script(KEYDOWN_COUNTER_INIT_SCRIPT)
    page = browser_ctx.new_page()
    page.goto(BASE)
    dismiss_intro_overlay(page)

    # State shared across the numbered CHECK GROUPS below. A dict (not bare
    # locals) so run_group()'s closures can both read and write it without
    # `nonlocal` declarations for every field.
    state = {"ready": False, "kd_base": None, "mv_trigger": None}

    # ---- harness setup: sign up, seed + activate licence, mount the POS,
    # capture the keydown baseline. Not a numbered CHECK GROUP -- if this
    # fails nothing below it can mean anything -- but still wrapped so the
    # run reports one FAIL and prints TOTAL instead of crashing raw. -------
    def setup():
        page.wait_for_selector("#su-btn", timeout=20000)
        page.fill("#su-name", "C5 Modals E2E")
        page.fill("#su-company", "C5 Modals E2E Co")
        page.fill("#su-email", "c5-modals-e2e@livecheck.local")
        page.fill("#su-pass", "LiveCheck12345!")
        page.fill("#su-pass2", "LiveCheck12345!")
        page.click("#su-btn")
        page.wait_for_selector("#sub-header-section", timeout=30000)
        page.wait_for_timeout(1000)

        seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
        page.reload()
        dismiss_intro_overlay(page)
        page.wait_for_selector("#sub-header-section", timeout=20000)
        page.wait_for_timeout(800)

        dp = page.evaluate("() => RetailSystem._currencyDp()")
        check("base currency is 3dp (JOD) by default", dp == 3, f"_currencyDp() = {dp}")

        page.evaluate("(s) => SubsystemApp._navigate(s)", "pos")
        page.wait_for_timeout(1000)
        page.evaluate("() => { CashDrawer.refresh(); }")
        page.wait_for_timeout(600)

        # Round-2 fix (item #4 [high]): capture the baseline BEFORE any
        # modal opens, right after the POS mount -- see module docstring,
        # BASELINE IS NOT ZERO.
        state["kd_base"] = kd_count(page)
        state["ready"] = True

    run_group("Harness setup (signup, licence activation, POS mount)", setup)

    # =========================================================================
    # CHECK GROUP 0b -- a POS remount does not change the permanent baseline
    # (real leak guard, round-2 fix item #4 [high]: if _initScanner /
    # _initShortcuts ever stopped being idempotent, kd_base would climb
    # here, independently of anything cash-drawer.js or import-wizard.js do)
    # =========================================================================
    def group_remount():
        kd_base = state["kd_base"]
        if kd_base is None:
            check("POS remount baseline check: harness setup ready", False,
                  "kd_base was never captured -- setup failed")
            return
        page.evaluate("(s) => SubsystemApp._navigate(s)", "dashboard")
        page.wait_for_timeout(400)
        page.evaluate("(s) => SubsystemApp._navigate(s)", "pos")
        page.wait_for_timeout(1000)
        page.evaluate("() => { CashDrawer.refresh(); }")
        page.wait_for_timeout(600)
        kd_after_remount = kd_count(page)
        check("a POS remount does not add a duplicate scanner/shortcut keydown listener",
              kd_after_remount == kd_base,
              f"kd_base={kd_base} kd_after_remount={kd_after_remount}")

    if state["ready"]:
        run_group("Check group 0b - POS remount baseline", group_remount)

    # =========================================================================
    # CHECK GROUP 1 -- Open Shift modal (cd-open-*), the ONE cash-drawer
    # overlay that must be driven BEFORE any session exists (its trigger only
    # renders when there is no active session).
    # =========================================================================
    def group1():
        open_trigger = page.get_by_role("button", name="Open Shift", exact=True)
        open_trigger.wait_for(state="visible", timeout=5000)
        open_trigger.click()
        page.wait_for_selector("#cd-open-modal", timeout=5000)
        check_dialog_semantics(page, "cd-open-dialog", "cd-open-dialog")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape removes the real Open Shift overlay from the DOM",
              page.locator("#cd-open-modal").count() == 0, "post-Escape count")
        focus_is_open_trigger = page.evaluate(
            """() => [...document.querySelectorAll('button')]
                .filter((b) => b.textContent.trim() === 'Open Shift')
                .includes(document.activeElement)"""
        )
        check("Escape returned focus to the real 'Open Shift' trigger", focus_is_open_trigger)

        # Actually open the shift through the real form (not the API), so
        # every later check runs against a session this run's own UI
        # created end to end.
        open_trigger.click()
        page.wait_for_selector("#cd-open-modal", timeout=5000)
        page.fill("#cd-opening-float", "50.005")
        page.click("#cd-open-btn")
        page.wait_for_selector("#cd-open-modal", state="detached", timeout=8000)
        page.wait_for_timeout(500)

    if state["ready"]:
        run_group("Check group 1 - Open Shift modal", group1)

    # =========================================================================
    # CHECK GROUP 2 -- Cash In/Out (Movement) modal (cd-mv-*)
    # =========================================================================
    def group2():
        mv_trigger = page.get_by_role("button", name="Cash In/Out", exact=True)
        mv_trigger.wait_for(state="visible", timeout=5000)
        mv_trigger.click()
        page.wait_for_selector("#cd-mv-modal", timeout=5000)
        check_dialog_semantics(page, "cd-mv-dialog", "cd-mv-dialog")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape removes the real Cash In/Out overlay from the DOM",
              page.locator("#cd-mv-modal").count() == 0)
        focus_is_mv_trigger = page.evaluate(
            """() => [...document.querySelectorAll('button')]
                .filter((b) => b.textContent.trim() === 'Cash In/Out')
                .includes(document.activeElement)"""
        )
        check("Escape returned focus to the real 'Cash In/Out' trigger", focus_is_mv_trigger)
        # Handed to CHECK GROUP 3 via `state` -- get_by_role locators
        # re-query the live DOM on every interaction, so reusing this
        # handle across groups is safe even though the button re-renders.
        state["mv_trigger"] = mv_trigger

    if state["ready"]:
        run_group("Check group 2 - Cash In/Out modal", group2)

    # =========================================================================
    # CHECK GROUP 3 -- a _confirm stacked on Cash In/Out (see module docstring
    # for why the trigger is programmatic while everything downstream is real)
    # =========================================================================
    def group3():
        kd_base = state["kd_base"]
        mv_trigger = state["mv_trigger"]
        if kd_base is None or mv_trigger is None:
            check("confirm-stacked-on-Cash-In/Out group: prerequisites ready", False,
                  f"kd_base={kd_base!r} mv_trigger={'set' if mv_trigger is not None else 'missing'}")
            return
        mv_trigger.click()
        page.wait_for_selector("#cd-mv-modal", timeout=5000)
        kd_before_confirm = kd_count(page)
        # Round-2 fix (item #4 [high]): kd_base + 1, not a literal 1 -- see
        # module docstring, BASELINE IS NOT ZERO.
        check("Cash In/Out registered its own real keydown handler",
              kd_before_confirm == kd_base + 1,
              f"kd_base={kd_base} kd count={kd_before_confirm}")

        page.evaluate(
            """() => {
                window.__confirmAnswer = null;
                RetailSystem._confirm({title: 'Discard entry?', message: 'Sure?'})
                    .then((a) => { window.__confirmAnswer = a; });
            }"""
        )
        page.wait_for_timeout(200)
        kd_with_confirm = kd_count(page)
        check("the stacked _confirm registered its OWN real keydown handler on top",
              kd_with_confirm == kd_base + 2,
              f"kd_base={kd_base} kd count={kd_with_confirm}")

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        answer = page.evaluate("() => window.__confirmAnswer")
        check("the stacked _confirm resolved false on the first Escape", answer is False, f"answer={answer!r}")
        check("the FIRST Escape closed only the confirm -- Cash In/Out is still in the DOM",
              page.locator("#cd-mv-modal").count() == 1)

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("the SECOND Escape closed the Cash In/Out modal underneath it",
              page.locator("#cd-mv-modal").count() == 0)
        kd_after_both = kd_count(page)
        check("no keydown handler leaked after both Escapes",
              kd_after_both == kd_base,
              f"kd_base={kd_base} kd count={kd_after_both}")

    if state["ready"]:
        run_group("Check group 3 - confirm stacked on Cash In/Out", group3)

    # =========================================================================
    # CHECK GROUP 4 -- X Report modal (cd-x-*), non-destructive
    # =========================================================================
    def group4():
        x_trigger = page.get_by_role("button", name="X Report", exact=True)
        x_trigger.wait_for(state="visible", timeout=5000)
        x_trigger.click()
        page.wait_for_selector("#cd-x-modal", timeout=5000)
        check_dialog_semantics(page, "cd-x-dialog", "cd-x-dialog")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape removes the real X Report overlay from the DOM",
              page.locator("#cd-x-modal").count() == 0)

    if state["ready"]:
        run_group("Check group 4 - X Report modal", group4)

    # =========================================================================
    # CHECK GROUP 5 -- Drawer History modal (cd-hist-*)
    # =========================================================================
    def group5():
        hist_trigger = page.get_by_role("button", name="Drawer History", exact=True)
        hist_trigger.wait_for(state="visible", timeout=5000)
        hist_trigger.click()
        page.wait_for_selector("#cd-hist-modal", timeout=5000)
        check_dialog_semantics(page, "cd-hist-dialog", "cd-hist-dialog")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape removes the real Drawer History overlay from the DOM",
              page.locator("#cd-hist-modal").count() == 0)

    if state["ready"]:
        run_group("Check group 5 - Drawer History modal", group5)

    # =========================================================================
    # CHECK GROUP 6 -- End Shift / Z report modal (cd-close-*) + item #15(a):
    # the variance preview rounds at the currency's own minor unit (3dp, JOD)
    # =========================================================================
    def group6():
        close_trigger = page.get_by_role("button", name="End Shift (Z)", exact=True)
        close_trigger.wait_for(state="visible", timeout=5000)
        close_trigger.click()
        page.wait_for_selector("#cd-close-modal", timeout=8000)
        page.wait_for_timeout(300)
        check_dialog_semantics(page, "cd-close-dialog", "cd-close-dialog")

        expected_shown = page.evaluate("() => document.getElementById('cd-close-expected')?.textContent || ''")
        check("close-modal shows the expected cash figure", "50.005" in expected_shown, f"shown={expected_shown!r}")

        page.fill("#cd-close-counted", "50.000")
        page.wait_for_timeout(300)
        variance_html = page.evaluate("() => document.getElementById('cd-close-variance-val')?.innerHTML || ''")
        variance_text = page.evaluate(
            "() => (document.getElementById('cd-close-variance-val')?.textContent || '').trim()"
        )
        # Exact text match, not a substring: a sign-flip mutant renders the
        # positive "JD 0.005 over" and a hardcoded-2dp mutant renders "JD 0.00
        # balanced" on a 3dp currency's own scale collapsed to 2dp -- both
        # would slip past a bare "'0.005' in variance_html" substring check.
        # See the unit-level version of this same mutation proof in
        # retail_p0_modals_test.js.
        check("50.000 counted vs 50.005 expected previews EXACTLY '\u2212JD 0.005 short' at 3dp",
              variance_text == "\u2212JD 0.005 short", f"rendered text={variance_text!r} html={variance_html!r}")

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape also closes the real close-shift (Z report) modal",
              page.locator("#cd-close-modal").count() == 0)

    if state["ready"]:
        run_group("Check group 6 - End Shift / Z report modal", group6)

    # =========================================================================
    # CHECK GROUP 7 -- Import Wizard, numbered shell (_shell via .open())
    # =========================================================================
    def group7():
        kd_base = state["kd_base"]
        if kd_base is None:
            check("import wizard (numbered shell) group: prerequisites ready", False, "kd_base was never captured")
            return
        page.evaluate("() => { ImportWizard.open('retail', 'products'); }")
        page.wait_for_selector("#iw-overlay", timeout=5000)
        check_dialog_semantics(page, "iw-dialog (numbered wizard, step 1)", "iw-dialog")
        overlay_class = page.evaluate("() => document.getElementById('iw-overlay').className")
        check("iw-overlay carries ret-modal-overlay (ratchet-visible)",
              "ret-modal-overlay" in overlay_class, overlay_class)
        kd_step1 = kd_count(page)
        check("wizard step 1 registered exactly one real keydown handler",
              kd_step1 == kd_base + 1, f"kd_base={kd_base} kd count={kd_step1}")

        # ---- a REAL step transition: upload an actual CSV, click Next, land
        # on step 2, and confirm step 1's keydown handler was torn down
        # rather than leaked underneath step 2's (see module docstring and
        # import-wizard.js's own comment on the bug this guards against).
        csv_path = DATA / "p0_modals_import.csv"
        csv_path.write_text("Product Name,SKU\nP0 Modals Widget,SKU-P0-MODALS-1\n", encoding="utf-8")
        page.set_input_files("#iw-file", str(csv_path))
        page.wait_for_timeout(200)
        next_btn = page.locator("#iw-next1")
        check("the Next button enabled after a real file was picked", not next_btn.is_disabled())
        next_btn.click()
        page.wait_for_selector("#iw-map-body, .iw-map-row, #iw-overlay", timeout=8000)
        page.wait_for_timeout(400)
        still_one_overlay = page.locator("#iw-overlay").count()
        check("step 2 replaced step 1's overlay -- exactly one #iw-overlay in the DOM",
              still_one_overlay == 1, f"count={still_one_overlay}")
        kd_step2 = kd_count(page)
        check("step 2 left exactly one real keydown handler -- step 1's did not leak",
              kd_step2 == kd_base + 1, f"kd_base={kd_base} kd count={kd_step2}")
        mapped_something = page.locator("select.iw-select").count()
        check("step 2 actually rendered mapping rows (the real transition ran, not a no-op)",
              mapped_something > 0, f"select.iw-select count={mapped_something}")

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape closes the wizard after a real step transition",
              page.locator("#iw-overlay").count() == 0)
        kd_after_wizard_close = kd_count(page)
        check("no keydown handler leaked after the wizard closed post-transition",
              kd_after_wizard_close == kd_base, f"kd_base={kd_base} kd count={kd_after_wizard_close}")

    if state["ready"]:
        run_group("Check group 7 - Import Wizard numbered shell", group7)

    # =========================================================================
    # CHECK GROUP 8 -- Import Wizard, Smart Import shell (_smartShell via
    # .openSmart())
    # =========================================================================
    def group8():
        kd_base = state["kd_base"]
        if kd_base is None:
            check("smart import wizard group: prerequisites ready", False, "kd_base was never captured")
            return
        page.evaluate("() => { ImportWizard.openSmart(['retail']); }")
        page.wait_for_selector("#iw-overlay", timeout=5000)
        check_dialog_semantics(page, "iw-dialog (smart wizard)", "iw-dialog")
        kd_smart = kd_count(page)
        check("the smart wizard registered exactly one real keydown handler",
              kd_smart == kd_base + 1, f"kd_base={kd_base} kd count={kd_smart}")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        check("Escape removes the real Smart Import overlay from the DOM",
              page.locator("#iw-overlay").count() == 0)
        kd_after_smart_close = kd_count(page)
        check("no keydown handler leaked after the smart wizard closed",
              kd_after_smart_close == kd_base, f"kd_base={kd_base} kd count={kd_after_smart_close}")

    if state["ready"]:
        run_group("Check group 8 - Smart Import wizard", group8)

    browser.close()

server.shutdown()
thread.join(timeout=5)

print("\n" + "=" * 70)
print(f"TOTAL CHECKS: {len(RESULTS['checks'])}  ALL PASS: {RESULTS['pass']}")
sys.exit(0 if RESULTS["pass"] else 1)
