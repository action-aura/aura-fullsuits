"""
retail_s_numbering_unavailable_e2e.py -- the Receipt numbers card when readable
numbers are UNAVAILABLE, in a REAL browser. STANDALONE SCRIPT, NOT A PYTEST FILE.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_s_numbering_unavailable_e2e.py [--only tag,tag]

Why this exists: the server's kill switch (READABLE_NUMBERS_AVAILABLE) is up, so
`available:false` never comes from a real server today. The two client-side
behaviours that depend on it were therefore never exercised by anything:

  wording   the sentence under the disabled switch follows the SERVER field
            `readable_switched_off_by_update`: true -> "it has no till number
            from the main computer now", false and ABSENT -> the "given out by
            the main computer" sentence, and a `unavailable_reason` the server
            sends is shown as sent. Separate directions, because a guard that
            ignores the field passes some of them.
  example   while unavailable the example beside the fields shows the LONG
            number this computer really prints (SALE-000001-<8 hex>-<8 hex>),
            never the short <code><till>-000001 shape, and it does not change
            when a branch code is typed. The control is the real server, which
            says available:true: typing ABD there must show ABD1-000001.

The route GET /api/sub/retail/numbering is rewritten in the browser (Playwright
route.fetch + fulfill); the page itself, its JS and the server are real.
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import DEFAULT_WAIT_MS, VIEWPORT, Backend, human_click  # noqa: E402
from retail_p1b_palette_e2e import check  # noqa: E402
from retail_p1b_palette_e2e import open_page as _open_page  # noqa: E402
import retail_p1b_palette_e2e as _pe  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OWNER_EMAIL = "tareq@numunavail.test"
OWNER_PASSWORD = "NumUnavailOwner_123!"
PORT_RANGE = range(6260, 6280)

# v42 wording (the old "arrive with multi-computer support" promise is gone): the real
# reason is "the main computer has not given this computer a till number yet".
SWITCHED_OFF = ("This computer was using readable numbers, but it has no till number from the "
                "main computer now, so it uses the long numbers again.")
PLAIN = ("Readable numbers are given out by the main computer, and this computer has no till "
         "number from the main computer yet. Until it has one it uses the long numbers, so two "
         "computers can never print the same receipt number.")
# A reason the SERVER sends must be shown as sent (the page prefers it to its own sentence).
SERVER_REASON = "Server-sent reason for this test only: ask the main computer for a till number."
LONG_SALE = "SALE-000001-a1b2c3d4-e5f6a7b8"
LONG_RET = "RET-000001-a1b2c3d4-e5f6a7b8"
LONG_NOTE = "This computer uses the long numbers. The two short codes at the end tell tills apart."


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


def numbering_page(browser, base, mutate):
    """Open the Tills section with GET numbering rewritten by `mutate(dict)`
    (None = leave the real answer alone). Returns (ctx, page, seen) where `seen`
    counts the rewritten GETs -- a case that never rewrote anything proved nothing."""
    ctx, page = _open_page(browser, base, email=OWNER_EMAIL, password=OWNER_PASSWORD, viewport=VIEWPORT)
    seen = {"gets": 0}

    def handler(route, request):
        if request.method != "GET" or mutate is None:
            route.continue_()
            return
        resp = route.fetch()
        try:
            body = resp.json()
        except Exception:
            route.fulfill(response=resp)
            return
        seen["gets"] += 1
        # the route wraps its answer as {status:'success', data:{...}}; the page
        # reads body.data, so that is the dict to rewrite
        mutate(body["data"] if isinstance(body.get("data"), dict) else body)
        route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")

    page.route("**/api/sub/retail/numbering*", handler)
    page.evaluate("() => { SubsystemApp._navigate('admin-center'); }")
    page.wait_for_selector("#sh-root .sh-nav-item", timeout=15000)
    page.wait_for_function("() => !!document.getElementById('device-branch-select') && "
                           "document.getElementById('device-branch-select').options.length > 0", timeout=15000)
    human_click(page, '#sh-root .sh-nav-item[data-sh="tills"]', "nav tills")
    page.wait_for_selector("#num-branch-code", timeout=15000)
    page.wait_for_function("() => document.getElementById('num-preview') && "
                           "document.getElementById('num-preview').textContent.trim().length > 0", timeout=15000)
    return ctx, page, seen


def preview(page):
    return page.inner_text("#num-preview").replace("\n", " | ")


def note(page):
    return page.inner_text("#num-preview-note").strip()


def reason(page):
    return page.evaluate("() => { const e = document.getElementById('num-unavailable'); return e ? e.textContent.trim() : null; }")


def unavailable(off_by_update, server_reason=None):
    def m(body):
        body["available"] = False
        body["readable_enabled"] = False
        if server_reason is not None:
            body["unavailable_reason"] = server_reason
        if off_by_update is None:
            body.pop("readable_switched_off_by_update", None)
        else:
            body["readable_switched_off_by_update"] = off_by_update
    return m


def wording_case(browser, base, off_by_update, expected, server_reason=None):
    ctx, page, seen = numbering_page(browser, base, unavailable(off_by_update, server_reason))
    try:
        assert seen["gets"] >= 1, "the numbering GET was never rewritten -- this case proved nothing"
        got = reason(page)
        assert got == expected, f"switched_off_by_update={off_by_update!r}: the card said {got!r}, wanted {expected!r}"
        assert page.is_disabled("#num-readable"), "the switch is not disabled while unavailable"
    finally:
        ctx.close()


def no_old_promise_case(browser, base):
    """Every unavailable shape, and the whole Tills card, must not promise a feature
    that already shipped."""
    for off in (True, False, None):
        ctx, page, seen = numbering_page(browser, base, unavailable(off))
        try:
            assert seen["gets"] >= 1, "the numbering GET was never rewritten -- this case proved nothing"
            body = page.inner_text("#sh-root")
            assert "multi-computer support" not in body, \
                f"switched_off_by_update={off!r}: the old promise is still on the page"
        finally:
            ctx.close()


def example_case(browser, base):
    ctx, page, seen = numbering_page(browser, base, unavailable(False))
    try:
        assert seen["gets"] >= 1, "the numbering GET was never rewritten -- this case proved nothing"
        shown = preview(page)
        assert LONG_SALE in shown and LONG_RET in shown, f"unavailable: the example is not the long numbers: {shown!r}"
        assert note(page) == LONG_NOTE, f"unavailable: the example note is {note(page)!r}"
        # typing a branch code must not turn the example into a short number this
        # computer cannot print
        page.fill("#num-branch-code", "ABD")
        page.wait_for_timeout(250)
        shown = preview(page)
        assert LONG_SALE in shown and "ABD" not in shown, f"a typed code changed the unavailable example: {shown!r}"
        # the branch code is still saveable/editable (the code is on paper as a prefix later)
        assert not page.is_disabled("#num-branch-code"), "the branch code field must stay editable"
    finally:
        ctx.close()


def control_case(browser, base):
    """The real server: available. Typing ABD must show the SHORT example, and the
    'unavailable' sentence must not exist."""
    ctx, page, _seen = numbering_page(browser, base, None)
    try:
        assert reason(page) is None, f"available server still shows the unavailable sentence: {reason(page)!r}"
        page.fill("#num-branch-code", "ABD")
        page.wait_for_timeout(250)
        shown = preview(page)
        assert "ABD" in shown and "-000001" in shown, f"available: typing ABD did not show a short example: {shown!r}"
        assert "SALE-000001-" not in shown, f"available: the LONG example leaked into the available card: {shown!r}"
    finally:
        ctx.close()


def main() -> int:
    args = sys.argv[1:]
    if "--only" in args:
        _pe.ONLY = set(args[args.index("--only") + 1].split(","))

    def wanted(tag):
        return not _pe.ONLY or tag in _pe.ONLY

    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_s_numunavail_"))
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
            p0.fill("#su-company", "Num Unavailable Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()

            if wanted("wording"):
                check("wording", "switched_off_by_update TRUE -> 'it has no till number from the main computer now'",
                      lambda: wording_case(browser, base, True, SWITCHED_OFF))
                check("wording", "switched_off_by_update FALSE, no server reason -> the main-computer sentence",
                      lambda: wording_case(browser, base, False, PLAIN))
                check("wording", "switched_off_by_update ABSENT, no server reason -> the main-computer sentence",
                      lambda: wording_case(browser, base, None, PLAIN))
                check("wording", "switched_off_by_update FALSE + a reason from the server -> the server's reason, as sent",
                      lambda: wording_case(browser, base, False, SERVER_REASON, server_reason=SERVER_REASON))
                check("wording", "the old 'multi-computer support' promise is nowhere on the card",
                      lambda: no_old_promise_case(browser, base))
            if wanted("example"):
                check("example", "unavailable: the example is the LONG number and ignores a typed code",
                      lambda: example_case(browser, base))
                check("example", "control: available server -> typed ABD shows the SHORT example",
                      lambda: control_case(browser, base))
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
