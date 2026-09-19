"""
THE MODALS, IN A REAL BROWSER.

WHY THIS EXISTS
On 2026-09-19 the 27 remaining hand-rolled modals in subsystem-retail.js were
given dialog semantics, focus management and Escape-to-close, and a modal key
queue was added so that only the front-most dialog answers a key. All of that
is covered by two Node suites -- retail_modal_stack_test.js drives the helpers
through a stubbed DOM, retail_modal_a11y_coverage_test.js pins the structure.

Neither opens a modal. A stub DOM cannot tell you that focus actually moved,
that Escape actually reaches the handler through the real event pipeline, or
that the browser returns focus to the button the operator pressed. And
retail_smoke_e2e.py, the only thing here that drives the real product, walks
the screens without ever opening a dialog.

So the most-changed code in that commit had no runtime verification at all.
This closes that, on the same principle as the smoke suite it borrows its
backend from: read the real thing running, do not reason about it.

WHAT IT PROVES
  1. Opening a modal really produces role="dialog" + aria-modal, and the
     aria-labelledby target really exists and really has text -- a name that
     resolves in the DOM, not just in the source.
  2. Focus really moves INTO the dialog on open.
  3. Escape really closes it, through the real capture-phase listener.
  4. Focus really returns to the control that opened it.
  5. THE QUEUE: with a _confirm raised on top of an open modal, ONE Escape
     closes only the _confirm and leaves the modal standing. This is the
     defect the queue was added to prevent, and it is the one thing a stubbed
     DOM argues about rather than demonstrates -- both listeners are on the
     real document here, in one real dispatch.
  6. The skip link added to the shell is reachable by keyboard from the top
     of the page and actually moves focus to <main>.

NOT NAMED *_test.py ON PURPOSE -- same reason as retail_smoke_e2e.py, which
documents it: products/run_all_tests.py globs *_test.py and runs each under
pytest, while Playwright lives in the SYSTEM interpreter rather than the
pytest venv. Naming it *_test.py makes the canonical runner collect it, find
no pytest tests, and report "no tests ran".

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe products/retail/tests/retail_modal_e2e.py
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from retail_smoke_e2e import (  # noqa: E402  (path must be set first)
    Backend,
    Report,
    DEFAULT_WAIT_MS,
    _now,
    _dismiss_intro_overlay_if_present,
)

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    print("playwright is not installed for this interpreter -- see docs/testing/e2e-smoke-guide.md")
    raise


# Each entry: the screen to navigate to, the control that opens the modal, and
# the dialog id the markup is expected to carry. Chosen to cover three
# different builder shapes: a product form, a category form, and a customer
# form -- all three are SHARED builders reached from an Add wrapper, so they
# are also the ones where wiring the builder had to cover two call paths.
MODALS = [
    ("products", "RetailSystem._openAddProduct()", "ret-product-dialog"),
    ("categories", "RetailSystem._openAddCategory()", "ret-category-dialog"),
    ("customers", "RetailSystem._openAddCustomer()", "ret-customeredit-dialog"),
]


def _signup_and_claim(page, base_url: str) -> None:
    """The minimum real path to a usable shell, copied from the smoke suite."""
    page.goto(base_url, wait_until="domcontentloaded")
    _dismiss_intro_overlay_if_present(page)
    page.locator("#aura-relogin-modal").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
    page.fill("#su-name", "Modal Test Admin")
    page.fill("#su-company", "Modal Test Co")
    page.fill("#su-email", "modal-admin@example.test")
    page.fill("#su-pass", "SmokeTest123!")
    page.fill("#su-pass2", "SmokeTest123!")
    page.click("#su-btn")
    page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

    banner = page.locator("#aura-admin-device-claim")
    if banner.count():
        banner.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.get_by_role("button", name="Make this the admin device", exact=True).click()
        banner.wait_for(state="detached", timeout=DEFAULT_WAIT_MS)


def _open_modal(page, section: str, opener_js: str) -> None:
    page.evaluate(f"SubsystemApp._navigate('{section}')")
    page.wait_for_timeout(400)
    # Focus a real control first, so "focus returned to the trigger" is a
    # claim with something to return TO. document.activeElement is <body>
    # otherwise and the assertion would be vacuous.
    page.evaluate(
        "() => { const b = document.querySelector('.sub-nav-item.active')"
        " || document.querySelector('.sub-nav-item'); if (b) { b.id = b.id || 'modal-e2e-trigger';"
        " b.setAttribute('tabindex','0'); b.focus(); } }"
    )
    page.evaluate(opener_js)
    page.locator(".ret-modal-overlay").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)


def _run(page, backend: Backend, report: Report, shots: Path) -> None:
    base_url = backend.base_url

    def scenario_signup():
        _signup_and_claim(page, base_url)
        assert page.evaluate("typeof window.RetailSystem") == "object", "RetailSystem did not load"
        report.shot(page, shots, "00_shell_ready")

    report.run("00 shell boots, signup completes, device claimed", scenario_signup)

    for section, opener, dialog_id in MODALS:
        def scenario_modal(section=section, opener=opener, dialog_id=dialog_id):
            _open_modal(page, section, opener)

            dialog = page.locator(f"#{dialog_id}")
            assert dialog.count() == 1, (
                f"{opener} opened a modal but #{dialog_id} is not in the DOM -- "
                "the dialog id in the markup does not match the one passed to _wireModalA11y"
            )
            assert dialog.get_attribute("role") == "dialog", f"#{dialog_id} has no role=dialog"
            assert dialog.get_attribute("aria-modal") == "true", f"#{dialog_id} has no aria-modal"

            labelled_by = dialog.get_attribute("aria-labelledby")
            assert labelled_by, f"#{dialog_id} has no aria-labelledby"
            # The NAME MUST RESOLVE IN THE DOM and must not be empty. A
            # dangling or blank name reads as done and announces nothing.
            name = page.locator(f"#{labelled_by}")
            assert name.count() == 1, (
                f"#{dialog_id} points aria-labelledby at #{labelled_by}, which is not in the DOM"
            )
            assert name.inner_text().strip(), f"#{labelled_by} is empty, so the dialog has a blank name"

            # Focus really moved into the dialog.
            # Plain concatenation, not an f-string: only the FIRST literal of
            # an implicitly-joined f-string is an f-string, so the `}}` on a
            # continuation line reached the browser as a literal `}}` and
            # every one of these evaluates died with "Unexpected token '}'".
            inside = page.evaluate(
                "() => { const d = document.getElementById('" + dialog_id + "');"
                " return !!d && d.contains(document.activeElement); }"
            )
            assert inside, f"focus did not move into #{dialog_id} when it opened"

            report.shot(page, shots, f"modal_{dialog_id}_open")

            # Escape really closes it, through the real event pipeline.
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
            assert page.locator(f"#{dialog_id}").count() == 0, (
                f"Escape did not close #{dialog_id} in a real browser"
            )

            # And focus really came back to the control that opened it.
            returned = page.evaluate(
                "() => document.activeElement && document.activeElement.id === 'modal-e2e-trigger'"
            )
            assert returned, (
                f"focus was not returned to the trigger after #{dialog_id} closed -- "
                "it was dropped, which strands a keyboard user at the top of the page"
            )

        report.run(f"modal {dialog_id}: opens named, takes focus, Escape closes, focus returns", scenario_modal)

    # ------------------------------------------------------------------
    # THE ONE A STUB DOM CANNOT SETTLE. Two dialogs, two real capture-phase
    # listeners on the real document, one real Escape.
    # ------------------------------------------------------------------
    def scenario_stacked_escape():
        _open_modal(page, "products", "RetailSystem._openAddProduct()")
        assert page.locator("#ret-product-dialog").count() == 1, "the product modal did not open"

        # A real _confirm, raised on top, exactly as a delete action would.
        #
        # MUST NOT RETURN THE PROMISE. page.evaluate AWAITS a returned Promise,
        # and _confirm's only resolves when the dialog is ANSWERED -- which
        # happens below, after this call returns. Returning it deadlocks the
        # whole run with no output and no error; the first version of this did
        # exactly that and sat for seven minutes. The arrow body ends in a
        # statement, so the expression evaluates to undefined and Playwright
        # returns immediately, leaving the dialog open for the keypress.
        page.evaluate(
            "() => {"
            "  window.__modalE2EAnswer = 'pending';"
            "  RetailSystem._confirm({title:'Stacked', message:'Answer me'})"
            "    .then(v => { window.__modalE2EAnswer = v; });"
            "}"
        )
        page.locator("#ret-confirm-dialog").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        report.shot(page, shots, "stacked_confirm_over_modal")

        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        answered = page.evaluate("window.__modalE2EAnswer")
        assert answered is False, (
            f"the stacked _confirm did not resolve false on Escape (got {answered!r})"
        )
        assert page.locator("#ret-product-dialog").count() == 1, (
            "ONE Escape closed BOTH dialogs in a real browser: the _confirm answered AND the "
            "product form underneath it was destroyed. Both handlers are on the same document "
            "node -- siblings in one dispatch, not a bubble chain -- so stopPropagation on one "
            "does not stop the other. This is exactly what _modalKeyStack exists to prevent."
        )

        # The other direction: the form underneath is now the front of the
        # queue and must answer the NEXT Escape. A queue that swallowed
        # everything would pass the assertion above and be useless.
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        assert page.locator("#ret-product-dialog").count() == 0, (
            "after the _confirm was dismissed, a second Escape did not close the form "
            "underneath it -- the queue is swallowing keys instead of ordering them"
        )

    report.run("stacked dialogs: one Escape closes only the top, the next closes the one beneath", scenario_stacked_escape)

    # ------------------------------------------------------------------
    # The skip link added to the shell on the same day.
    # ------------------------------------------------------------------
    def scenario_nav_is_keyboard_reachable():
        """THE ONE THAT MATTERED. Found by pressing Tab, not by reading source.

        The sidebar destinations are <a> with an onclick and no href. An
        anchor without href is not a link, has no implicit role and never
        enters the tab order -- so the entire navigation was unreachable by
        keyboard and a switch user could not change screens. Measured before
        the fix: the first Tab stop on the dashboard was the AI Assistant
        button in the sidebar FOOTER, with every destination above it absent.
        """
        page.evaluate("SubsystemApp._navigate('dashboard')")
        page.wait_for_timeout(500)

        reachable = page.evaluate(
            "() => [...document.querySelectorAll('.sub-nav-item')]"
            " .filter(e => e.tabIndex >= 0).length"
        )
        total = page.evaluate("() => document.querySelectorAll('.sub-nav-item').length")
        assert total >= 5, f"only {total} nav items rendered; the corpus is too small to prove anything"
        assert reachable == total, (
            f"{total - reachable} of {total} sidebar destinations are not in the tab order. "
            "An <a> with an onclick and no href is not focusable, so a keyboard or switch "
            "user cannot reach them at all."
        )

        # Focusable is half of it -- Enter and Space must also ACTIVATE, which
        # onclick alone does not do for a non-button.
        page.evaluate(
            "() => { const n = [...document.querySelectorAll('.sub-nav-item')]"
            "   .find(e => (e.dataset.section || '') === 'products'); if (n) n.focus(); }"
        )
        page.keyboard.press("Enter")
        page.wait_for_timeout(700)
        header = page.locator("#sub-header-section").inner_text()
        assert "Product" in header, (
            f"Enter on a focused nav item did not navigate (header reads {header!r}). "
            "tabindex without key handling gives a keyboard user a focus ring on a control "
            "they still cannot operate, which looks like it works and does not."
        )
        report.shot(page, shots, "nav_keyboard_reachable")

    report.run("sidebar destinations are keyboard reachable AND operable", scenario_nav_is_keyboard_reachable)

    def scenario_skip_link():
        page.evaluate("SubsystemApp._navigate('products')")
        page.wait_for_timeout(400)

        # DOM ORDER, not "the first Tab stop". Asserting the latter looked
        # right and was wrong: the shell leaves Chromium's sequential focus
        # navigation starting point inside the nav after boot, so the first
        # Tab lands mid-sidebar regardless of what precedes it in the markup.
        # That is a real and separate caveat -- recorded rather than asserted
        # away -- but it is not something the skip link's own CSS can fix, and
        # three different hiding techniques were tried against it before the
        # cause turned out to be elsewhere entirely.
        first_focusable = page.evaluate(
            "() => { const all = [...document.querySelectorAll("
            "   'a[href],button,input,select,textarea,[tabindex]')]"
            "   .filter(e => !e.hasAttribute('disabled'));"
            " return all.length ? (all[0].className || '') : ''; }"
        )
        assert "sub-skip-link" in first_focusable, (
            f"the first focusable element in the document is {first_focusable!r}, not the "
            "skip link. It has to come before the navigation it exists to skip."
        )

        # And it must actually work when reached.
        page.evaluate("() => document.querySelector('.sub-skip-link').focus()")
        focused = page.evaluate(
            "() => (document.activeElement.className || '').includes('sub-skip-link')"
        )
        assert focused, "the skip link cannot take focus at all"
        report.shot(page, shots, "skip_link_focused")

        page.keyboard.press("Enter")
        page.wait_for_timeout(300)
        landed = page.evaluate(
            "() => { const a = document.activeElement; return !!a && a.id === 'sub-content'; }"
        )
        assert landed, (
            "activating the skip link did not move focus to <main id=sub-content>. Without "
            "tabindex=-1 the browser scrolls there and leaves focus on the link, so the next "
            "Tab goes straight back into the nav and the link has done nothing."
        )

    report.run("skip link precedes the nav and moves focus to main", scenario_skip_link)


def main() -> int:
    # Same reason retail_smoke_e2e.py does this: the page carries non-ASCII
    # (this app ships Arabic), and a Windows console's cp1252 default would
    # crash this script's own error reporting mid-run.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    screenshots_dir = Path(tempfile.gettempdir()) / f"aura_retail_modal_screenshots_{_now()}"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    wall_start = time.monotonic()
    backend = Backend()
    report = Report()

    try:
        backend.start()
        print(f"Backend healthy at {backend.base_url}")
    except Exception as e:  # noqa: BLE001
        print(f"FATAL: {e}", file=sys.stderr)
        backend.stop()
        backend.cleanup_app_data()
        return 2

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    reduced_motion="reduce",
                )
                page = context.new_page()
                page.set_default_timeout(DEFAULT_WAIT_MS)
                report.attach(page)
                _run(page, backend, report, screenshots_dir)
            finally:
                browser.close()
    finally:
        backend.stop()
        backend.cleanup_app_data()

    all_passed = report.print_summary(screenshots_dir, time.monotonic() - wall_start)
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
