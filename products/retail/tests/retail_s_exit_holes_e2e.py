"""
retail_s_exit_holes_e2e.py -- the four doors out of the Settings hub that used to
skip the leave guard, proved in a REAL browser with real mouse clicks.
STANDALONE SCRIPT, NOT A PYTEST FILE.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_s_exit_holes_e2e.py [--only tag,tag]

What was open (P2S "exit holes"): retail_s_leave_exits_e2e.py covers the sidebar
rows, the More sheet, the licence entries, the logo, "make this the main
computer" and the browser's own unload. Four more doors did a bare
`window.location.href = ...` and threw an unsaved hub away without a word:

  palette   Ctrl+K -> an external page (WhatsApp)          palette.js
  wa        the "Manage WhatsApp Reports" button            subsystem-retail.js
  einv      the "Manage E-Invoicing" button                 subsystem-retail.js
  relay     the "Manage Shop Network" button                subsystem-retail.js

Every door has all three cases, because a guard that always says stay (or always
says go) would pass any single one of them:
  * dirty hub, "Keep editing"  -> the prompt shows, the page does NOT move, the
    Unsaved bar is still up and the typed value is still in the field
  * dirty hub, "Discard"       -> the prompt shows, THEN the page moves
  * clean hub                  -> no prompt at all, the page moves

The dirty state is a REAL edit (Money & customers -> loyalty rate, typed with real
keys) so the Unsaved bar is up. Buttons are found by their card, then clicked at
the centre of their box after checking the box is inside the viewport and is what
elementFromPoint returns there (rule 6b) -- `locator.click` is not evidence.
"""
from __future__ import annotations

import socket
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import (  # noqa: E402
    DEFAULT_WAIT_MS, VIEWPORT, Backend, human_click,
)
from retail_p1b_palette_e2e import check  # noqa: E402
from retail_p1b_palette_e2e import open_page as _open_page  # noqa: E402
import retail_p1b_palette_e2e as _pe  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OWNER_EMAIL = "tareq@exitholes.test"
OWNER_PASSWORD = "ExitHolesOwner_123!"
PORT_RANGE = range(6400, 6420)  # crew U2's range (contract: 6400-6419)

# door -> (hub section holding the button, button selector, target page, palette key)
BUTTONS = {
    "wa": ("shop-receipt", "#wa-reports-card button[onclick*=\"_leaveToPage\"]", "/static/whatsapp.html"),
    "einv": ("tax", "#einvoicing-card button[onclick*=\"_leaveToPage\"]", "/static/einvoicing.html"),
    "relay": ("tills", "#shop-network-card button[onclick*=\"_leaveToPage\"]", "/static/site-relay.html"),
}


def pick_port() -> int:
    for port in PORT_RANGE:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            continue
        finally:
            s.close()
        return port
    raise RuntimeError(f"no free port in {PORT_RANGE}")


def open_page(browser, base, viewport=VIEWPORT):
    return _open_page(browser, base, email=OWNER_EMAIL, password=OWNER_PASSWORD, viewport=viewport)


# --------------------------------------------------------------------------- helpers
def _open_hub(page):
    page.evaluate("() => { SubsystemApp._navigate('admin-center'); }")
    page.wait_for_selector("#sh-root .sh-nav-item", timeout=15000)
    page.wait_for_function(
        "() => !!document.getElementById('device-branch-select') && "
        "document.getElementById('device-branch-select').options.length > 0", timeout=15000)
    page.wait_for_timeout(500)


def savebar_up(page):
    return page.evaluate("() => { const b = document.getElementById('sh-savebar'); "
                         "return !!b && !b.hidden && getComputedStyle(b).display !== 'none'; }")


def dirty_hub(page):
    """Open the hub and make a REAL unsaved edit. Returns the typed value."""
    _open_hub(page)
    page.locator('#sh-root .sh-nav-item[data-sh="credit"]').click()
    page.wait_for_selector("#credit-loyalty", state="visible", timeout=DEFAULT_WAIT_MS)
    page.wait_for_timeout(300)
    before = page.input_value("#credit-loyalty")
    typed = "0.35" if before != "0.35" else "0.45"
    loc = page.locator("#credit-loyalty")
    loc.click()
    loc.fill(typed)
    loc.blur()
    page.wait_for_timeout(200)
    assert savebar_up(page), "setup: the Unsaved bar did not come up after a real edit"
    return typed


def clean_hub(page):
    _open_hub(page)
    assert not savebar_up(page), "setup: the Unsaved bar is up on a hub nobody touched"


def dialog_up(page):
    return page.evaluate("() => !!document.getElementById('ret-confirm-dialog')")


def wait_dialog(page):
    page.wait_for_selector("#ret-confirm-dialog", state="visible", timeout=DEFAULT_WAIT_MS)


def keep_editing(page):
    human_click(page, "#ret-confirm-cancel", "Keep editing")
    page.wait_for_selector("#ret-confirm-dialog", state="detached", timeout=DEFAULT_WAIT_MS)


def discard(page):
    human_click(page, "#ret-confirm-ok", "Discard")


def expect_stayed(page, url_before, typed, what):
    page.wait_for_timeout(600)
    assert page.url == url_before, f"{what}: the page LEFT to {page.url} although the person said keep editing"
    assert savebar_up(page), f"{what}: the Unsaved bar is gone after 'keep editing'"
    assert page.input_value("#credit-loyalty") == typed, f"{what}: the typed value did not survive 'keep editing'"


def show_section(page, section):
    page.locator(f'#sh-root .sh-nav-item[data-sh="{section}"]').click()
    page.wait_for_timeout(350)


def fire(page, door):
    """Use the door the way a person does. Returns the page it leads to."""
    if door == "palette":
        page.keyboard.press("Control+k")
        page.locator("#aura-palette-input").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.keyboard.type("whatsapp")
        page.wait_for_selector('.aura-palette-row[data-key="page:whatsapp"]', timeout=DEFAULT_WAIT_MS)
        human_click(page, '.aura-palette-row[data-key="page:whatsapp"]', "palette row WhatsApp")
        return "/static/whatsapp.html"
    section, sel, target = BUTTONS[door]
    show_section(page, section)
    human_click(page, sel, f"hub button {door}", scroll_list=True)
    return target


def door_keep(browser, base, door):
    ctx, page = open_page(browser, base)
    try:
        typed = dirty_hub(page)
        url = page.url
        fire(page, door)
        wait_dialog(page)
        keep_editing(page)
        expect_stayed(page, url, typed, door)
    finally:
        ctx.close()


def door_discard(browser, base, door):
    ctx, page = open_page(browser, base)
    try:
        dirty_hub(page)
        target = fire(page, door)
        wait_dialog(page)
        discard(page)
        page.wait_for_url(f"**{target}*", timeout=DEFAULT_WAIT_MS)
    finally:
        ctx.close()


def door_clean(browser, base, door):
    ctx, page = open_page(browser, base)
    try:
        clean_hub(page)
        target = fire(page, door)
        page.wait_for_url(f"**{target}*", timeout=DEFAULT_WAIT_MS)
        # a prompt would hold the page back, so reaching it with no click on a
        # dialog IS the proof that none was shown
    finally:
        ctx.close()


# --------------------------------------------------------------------------- main
def main() -> int:
    args = sys.argv[1:]
    if "--only" in args:
        _pe.ONLY = set(args[args.index("--only") + 1].split(","))

    def wanted(tag):
        return not _pe.ONLY or tag in _pe.ONLY

    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_s_exit_holes_"))
    backend = Backend(data_dir)
    backend.port = pick_port()
    backend.base_url = f"http://127.0.0.1:{backend.port}"
    base = backend.base_url
    print(f"Backend: {base}", flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            backend.start()
            c0 = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            p0 = c0.new_page()
            p0.goto(base, wait_until="domcontentloaded")
            p0.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            p0.fill("#su-name", "Tareq")
            p0.fill("#su-company", "Exit Holes Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()

            for door in ("palette", "wa", "einv", "relay"):
                check(door, f"{door}: dirty hub -> prompt, 'Keep editing' stays with the edit intact",
                      lambda d=door: door_keep(browser, base, d))
                check(door, f"{door}: dirty hub -> prompt, 'Discard' leaves for the page",
                      lambda d=door: door_discard(browser, base, d))
                check(door, f"{door}: control: a clean hub leaves for the page",
                      lambda d=door: door_clean(browser, base, d))
        finally:
            backend.stop()
            browser.close()

    failed = [r for r in _pe.RESULTS if not r[1]]
    print(f"\nRESULTS: {len(_pe.RESULTS) - len(failed)}/{len(_pe.RESULTS)} passed", flush=True)
    for name, ok, msg in failed:
        print(f"  FAIL {name}: {msg}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
