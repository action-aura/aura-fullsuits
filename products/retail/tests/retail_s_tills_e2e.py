"""
retail_s_tills_e2e.py -- the Tills section of the Settings hub, proved in a REAL
browser against a REAL backend process, with real mouse clicks and real keys.
STANDALONE SCRIPT, NOT A PYTEST FILE.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_s_tills_e2e.py [--only tag,tag]

What P2S built: only the MAIN computer hands out till numbers (schema v42
`till_allocations`), and the hub now has a Tills section to do it from. This file
proves the section both ways for everything that both allows and denies:

  list        the main computer lists every computer with its number or the next
              free number to offer (allow half: inputs and buttons exist)
  problems    a number that cannot be right is refused on the screen, in words,
              WITHOUT a request leaving the browser
  assign      type a number, the ONE Save bar comes up, Save -> the server holds it
  release     Release number, Save -> the server holds nothing
  discard     Discard puts every box back, sends nothing
  refusal     a number taken behind the screen's back is refused BY THE SERVER,
              shown as a sentence (never a code), and the screen re-reads the truth
              (deny half of assign)
  sale        a sale before any number prints the plain number; after Number-all
              the next sale prints <CODE><n>-000001 with THIS till's own number
  number_all  asks first: Cancel changes nothing; Confirm numbers every free
              computer; the button is disabled while there are unsaved edits
  atomic      changing a number is ONE request ({replace:true}); a refused change leaves
              the computer holding its OLD number (never stranded with none); a free
              number is exactly one POST
  pending     a number released from a computer that is switched off: the server says
              reason:'release_pending', the screen names the computer, lists the number
              under "reserved", and never offers it as the next free number
  non_main    a computer that is not the main one sees the pointer to the main
              computer and nothing it could change a number with; the server
              refuses it too
  reach       1366x768 and 1024x768, English and Arabic: every control is inside
              the viewport, is what elementFromPoint finds at its centre (rule 6b),
              nothing spills sideways
  themes      light, sand, dark, night, dusk: the theme really took, every text of
              the section clears 4.5:1 on what it sits on, screenshot per theme

Rule 7 (count the check, not the outcome): the refusal check counts the POSTs that
left the browser; the problems check counts them too (must be zero).
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import (  # noqa: E402
    BACKEND_DIR, DEFAULT_WAIT_MS, REPO_ROOT, VIEWPORT, Backend, _owner_company_id,
    assert_reachable, human_click,
)
from retail_p1b_palette_e2e import check as _check  # noqa: E402
from retail_p1b_palette_e2e import open_page as _open_page  # noqa: E402
import retail_p1b_palette_e2e as _pe  # noqa: E402
import traceback  # noqa: E402


def check(tag, name, fn):
    """The shared runner, except a bare `assert x` (no message) still says WHERE it
    failed -- an empty FAIL line is undiagnosable."""
    def wrapped():
        try:
            fn()
        except AssertionError as e:
            if str(e):
                raise
            tb = traceback.extract_tb(e.__traceback__)[-1]
            raise AssertionError(f"assertion failed at line {tb.lineno}: {tb.line}") from None
    _check(tag, name, wrapped)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OWNER_EMAIL = "tareq@tills.test"
OWNER_PASSWORD = "TillsOwner_123!"
PORT_RANGE = range(6480, 6500)  # crew V's range (was U2's 6400-6419)
THEMES = ("light", "sand", "dark", "night", "dusk")
VIEWPORTS = ({"width": 1366, "height": 768}, {"width": 1024, "height": 768})
MIN_CONTRAST = 4.5
BACK_ID = "till-e2e-back-" + uuid.uuid4().hex[:8]
KITCHEN_ID = "till-e2e-kitchen-" + uuid.uuid4().hex[:8]
BASE = ""


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


def open_page(browser, base, *, lang="en", theme=None, viewport=VIEWPORT):
    return _open_page(browser, base, email=OWNER_EMAIL, password=OWNER_PASSWORD,
                      lang=lang, theme=theme, viewport=viewport)


# --------------------------------------------------------------------------- registry
def _registry():
    os.environ["AURA_APP_DATA"] = str(DATA_DIR)
    for p in (str(REPO_ROOT), str(BACKEND_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)
    from commercial_runtime.identity.registry_db import get_conn
    return get_conn()


def add_fake_device(company_id: str, dev_id: str, label: str) -> None:
    conn = _registry()
    try:
        conn.execute(
            "INSERT INTO devices (id, company_id, device_label, platform, is_admin_device, status, first_seen_at)"
            " VALUES (?,?,?,?,0,'active',?)",
            (dev_id, company_id, label, "WINDOWS", datetime.utcnow().isoformat()))
        conn.commit()
    finally:
        conn.close()


def move_main_flag(company_id: str, to_id: str) -> None:
    conn = _registry()
    try:
        conn.execute("UPDATE devices SET is_admin_device=0 WHERE company_id=?", (company_id,))
        conn.execute("UPDATE devices SET is_admin_device=1 WHERE id=? AND company_id=?", (to_id, company_id))
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- api / page helpers
def api(page, method, path, body=None):
    fn = getattr(page.request, method.lower())
    resp = fn(BASE + path, data=body) if body is not None else fn(BASE + path)
    try:
        j = resp.json()
    except Exception:  # noqa: BLE001
        j = {}
    return resp.status, j


def tills(page) -> dict:
    st, b = api(page, "GET", "/api/sub/retail/tills")
    assert st == 200 and b.get("status") == "success", (st, b)
    return b["data"]


def dev(data: dict, uid: str) -> dict:
    for d in data["devices"]:
        if d["terminal_uid"] == uid:
            return d
    raise AssertionError(f"device {uid} not in {[x['terminal_uid'] for x in data['devices']]}")


def open_tills(page):
    page.evaluate("() => { SubsystemApp._navigate('admin-center'); }")
    page.wait_for_selector("#sh-root .sh-nav-item", timeout=15000)
    page.wait_for_function(
        "() => !!document.getElementById('device-branch-select') && "
        "document.getElementById('device-branch-select').options.length > 0", timeout=15000)
    page.wait_for_timeout(400)
    human_click(page, '#sh-root .sh-nav-item[data-sh="tills"]', "nav tills")
    page.wait_for_selector("#tills-body", state="visible", timeout=DEFAULT_WAIT_MS)
    page.wait_for_timeout(500)


def savebar_up(page) -> bool:
    return page.evaluate("() => { const b = document.getElementById('sh-savebar'); "
                         "return !!b && !b.hidden && getComputedStyle(b).display !== 'none'; }")


def row_sel(uid: str, part: str = "") -> str:
    return f'#tills-list li[data-till-row="{uid}"]' + (f" {part}" if part else "")


def input_sel(uid: str) -> str:
    return f'#tills-list input[data-till-input="{uid}"]'


def type_number(page, uid: str, text: str):
    """Click the box like a person, replace what is in it, type with real keys."""
    # A refusal toast sits at the bottom-end corner for 3 seconds and can cover a
    # box near the bottom of the list. A person waits for it to go; so do we (this
    # is the toast only -- the inline #tills-error is not a body child).
    page.wait_for_function(
        "() => !document.querySelector('body > div[role=alert], body > div[role=status]')",
        timeout=8000)
    human_click(page, input_sel(uid), f"till box {uid}", scroll_list=True)
    page.keyboard.press("Control+a")
    if text == "":
        page.keyboard.press("Delete")
    else:
        page.keyboard.type(text)
    page.wait_for_timeout(150)


def hint_text(page, uid: str) -> str:
    return page.locator(row_sel(uid, "[data-till-hint]")).inner_text().strip()


def error_text(page) -> str:
    return page.evaluate("() => { const e = document.getElementById('tills-error'); "
                         "return e && !e.hidden ? e.textContent.trim() : ''; }")


def wait_toast(page, needle: str):
    page.wait_for_function(
        "(n) => [...document.querySelectorAll('[role=status],[role=alert]')].some(e => e.innerText.includes(n))",
        arg=needle, timeout=DEFAULT_WAIT_MS)


def click_save(page):
    human_click(page, "#sh-save", "Save bar: Save")
    page.wait_for_function("() => { const b = document.getElementById('sh-savebar'); "
                           "return !b || b.hidden || getComputedStyle(b).display === 'none'; }",
                           timeout=DEFAULT_WAIT_MS)


def spy_allocations(page):
    """Every request that leaves the browser for the till-allocation route."""
    sent = []

    def handler(route, request):
        if request.method in ("POST", "DELETE"):
            sent.append((request.method, request.url))
        route.continue_()
    page.route("**/api/sub/retail/tills/allocations*", handler)
    return sent


def release_all_via_api(page):
    data = tills(page)
    for d in data["devices"]:
        if d["till_number"] is not None:
            st, b = api(page, "DELETE", f"/api/sub/retail/tills/allocations?terminal_uid={d['terminal_uid']}")
            assert st == 200, (st, b)


# --------------------------------------------------------------------------- checks
def check_list(page, me):
    open_tills(page)
    data = tills(page)
    assert data["is_main"] is True and data["branch_code"], data
    rows = page.locator("#tills-list li[data-till-row]").count()
    assert rows == 3 == len(data["devices"]), f"expected 3 computers listed, the screen shows {rows}"
    assert page.locator("#tills-list input[data-till-input]").count() == 3
    assert page.locator("#till-plan").count() == 1
    badge = page.locator(row_sel(me, ".sh-till-badge")).all_inner_texts()
    assert badge == ["This computer", "Main computer"], badge
    for d in data["devices"]:
        assert d["till_number"] is None and d["suggested_till_number"], d
        want = f"No number yet. Next free number: {d['suggested_till_number']}."
        assert hint_text(page, d["terminal_uid"]) == want, (hint_text(page, d["terminal_uid"]), want)
        assert page.locator(input_sel(d["terminal_uid"])).input_value() == ""
    sug = sorted(d["suggested_till_number"] for d in data["devices"])
    assert sug == [1, 2, 3], f"three computers must be offered three different numbers: {sug}"
    assert not savebar_up(page), "a hub nobody touched shows the Unsaved bar"
    assert page.locator("#till-number-all").is_enabled()


def check_problems(page):
    open_tills(page)
    sent = spy_allocations(page)
    # out of range. The box takes 2 characters at most, so "too big" cannot be typed;
    # zero is the reachable out-of-range value.
    type_number(page, BACK_ID, "0")
    assert page.locator(row_sel(BACK_ID)).get_attribute("data-problem") == "1"
    assert hint_text(page, BACK_ID) == "A till number is a whole number from 1 to 99.", hint_text(page, BACK_ID)
    assert savebar_up(page), "a typed number did not raise the Save bar"
    human_click(page, "#sh-save", "Save bar: Save")
    page.wait_for_timeout(400)
    assert error_text(page) == "A till number is a whole number from 1 to 99.", error_text(page)
    assert sent == [], f"the browser sent {sent} although the number is impossible (must send nothing)"
    # the same number twice
    type_number(page, BACK_ID, "4")
    type_number(page, KITCHEN_ID, "4")
    want = "Till number 4 is used twice in this list. Every computer needs its own number."
    assert hint_text(page, BACK_ID) == want and hint_text(page, KITCHEN_ID) == want, hint_text(page, BACK_ID)
    human_click(page, "#sh-save", "Save bar: Save")
    page.wait_for_timeout(400)
    assert error_text(page) == want, error_text(page)
    assert sent == [], f"the browser sent {sent} for a number used twice"
    # fixing it clears the complaint (allow half)
    type_number(page, KITCHEN_ID, "5")
    assert page.locator(row_sel(BACK_ID)).get_attribute("data-problem") == "0"
    assert page.locator(row_sel(KITCHEN_ID)).get_attribute("data-problem") == "0"
    assert hint_text(page, KITCHEN_ID) == "Will get till number 5 when you save.", hint_text(page, KITCHEN_ID)
    human_click(page, "#sh-discard", "Save bar: Discard")
    page.wait_for_timeout(300)
    assert not savebar_up(page)
    assert tills(page)["allocations"] == [], "problems check must leave the server untouched"


def check_assign_release_discard(page):
    open_tills(page)
    sent = spy_allocations(page)
    type_number(page, BACK_ID, "5")
    assert hint_text(page, BACK_ID) == "Will get till number 5 when you save.", hint_text(page, BACK_ID)
    assert savebar_up(page)
    plan = page.input_value("#till-plan")
    assert plan and json.loads(plan) == {BACK_ID: 5}, plan
    assert tills(page)["allocations"] == [], "typing alone must not change the server"
    assert sent == []
    # discard first (deny half of save): the box, the plan and the bar all go back
    human_click(page, "#sh-discard", "Save bar: Discard")
    page.wait_for_timeout(300)
    assert page.locator(input_sel(BACK_ID)).input_value() == ""
    assert page.input_value("#till-plan") == "" and not savebar_up(page)
    assert sent == [] and tills(page)["allocations"] == []
    # now for real
    type_number(page, BACK_ID, "5")
    click_save(page)
    wait_toast(page, "Till numbers saved.")
    assert [m for m, _ in sent] == ["POST"], sent
    d = dev(tills(page), BACK_ID)
    assert d["till_number"] == 5, d
    assert page.locator(input_sel(BACK_ID)).input_value() == "5"
    assert hint_text(page, BACK_ID).startswith("Receipts start with"), hint_text(page, BACK_ID)
    assert "MAI5-" in hint_text(page, BACK_ID), hint_text(page, BACK_ID)
    assert not savebar_up(page) and page.input_value("#till-plan") == ""
    # release with the button
    human_click(page, f'button[data-till-act="release"][data-till-uid="{BACK_ID}"]', "Release number", scroll_list=True)
    page.wait_for_timeout(250)
    assert hint_text(page, BACK_ID) == "Will lose its number 5 when you save.", hint_text(page, BACK_ID)
    assert savebar_up(page)
    assert dev(tills(page), BACK_ID)["till_number"] == 5, "Release must wait for Save"
    click_save(page)
    wait_toast(page, "Till numbers saved.")
    assert dev(tills(page), BACK_ID)["till_number"] is None
    assert page.locator(input_sel(BACK_ID)).input_value() == ""
    assert [m for m, _ in sent] == ["POST", "DELETE"], sent
    # change = ONE atomic POST {replace:true} in one Save (the old release-then-assign
    # pair could strand the computer with no number; see the `atomic` tag below)
    type_number(page, BACK_ID, "6")
    click_save(page)
    wait_toast(page, "Till numbers saved.")
    type_number(page, BACK_ID, "8")
    assert hint_text(page, BACK_ID) == "Will change from 6 to 8 when you save.", hint_text(page, BACK_ID)
    click_save(page)
    page.wait_for_function(
        "(uid) => document.querySelector(`input[data-till-input=\"${uid}\"]`).value === '8'", arg=BACK_ID,
        timeout=DEFAULT_WAIT_MS)
    assert dev(tills(page), BACK_ID)["till_number"] == 8
    release_all_via_api(page)


def check_refusal(browser):
    ctx, page = open_page(browser, BASE)
    try:
        open_tills(page)
        sent = spy_allocations(page)
        data = tills(page)
        # behind the screen's back: Back till takes 9
        st, b = api(page, "POST", "/api/sub/retail/tills/allocations",
                    {"terminal_uid": BACK_ID, "till_number": 9, "branch_uid": data["branch_uid"]})
        assert st == 200, (st, b)
        try:
            # the screen still believes 9 is free
            assert page.locator(input_sel(BACK_ID)).input_value() == ""
            type_number(page, KITCHEN_ID, "9")
            assert page.locator(row_sel(KITCHEN_ID)).get_attribute("data-problem") == "0", \
                "the client must not know about the clash (that is what makes this the SERVER's refusal)"
            human_click(page, "#sh-save", "Save bar: Save")
            page.wait_for_function("() => { const e = document.getElementById('tills-error'); "
                                   "return e && !e.hidden && e.textContent.trim().length > 0; }",
                                   timeout=DEFAULT_WAIT_MS)
            msg = error_text(page)
            ok = {"Till number 9 is already used by another computer. Choose a different number.",
                  "Till number 9 was just given to another computer. Pick a different number."}
            assert msg in ok, f"refusal shown as {msg!r}"
            assert "TILL_" not in msg and "409" not in msg, msg
            assert [m for m, _ in sent] == ["POST"], f"the request must have reached the server: {sent}"
            wait_toast(page, "Pick a different number." if "just given" in msg else "Choose a different number.")
            # the screen re-read the truth
            page.wait_for_function(
                "(uid) => document.querySelector(`input[data-till-input=\"${uid}\"]`).value === '9'",
                arg=BACK_ID, timeout=DEFAULT_WAIT_MS)
            assert dev(tills(page), KITCHEN_ID)["till_number"] is None, "the refused number must not be held"
        finally:
            release_all_via_api(page)
    finally:
        ctx.close()


def spy_bodies(page):
    """Like spy_allocations, but also keeps the JSON body of every POST/DELETE."""
    sent = []

    def handler(route, request):
        if request.method in ("POST", "DELETE"):
            try:
                body = request.post_data_json
            except Exception:  # noqa: BLE001
                body = None
            sent.append((request.method, body, request.url))
        route.continue_()
    page.route("**/api/sub/retail/tills/allocations*", handler)
    return sent


ALLOC = "/api/sub/retail/tills/allocations"


def check_atomic(browser):
    """Item 4: changing a number is ONE request the server applies all-or-nothing.
    Deny half: the new number is taken behind the screen's back -> the computer STILL
    holds its old one (release-then-assign used to leave it with none).
    Allow half: a free number goes through in a single POST (replace:true), no DELETE."""
    ctx, page = open_page(browser, BASE)
    try:
        release_all_via_api(page)
        bu = tills(page)["branch_uid"]
        # Numbers 40/41/42 on purpose: the earlier sections of this file released 6 and 8
        # from a computer that is switched off, so the server (rightly) still RESERVES
        # those (release_pending). Fresh numbers keep this check about atomicity only.
        st, b = api(page, "POST", ALLOC, {"terminal_uid": BACK_ID, "till_number": 40, "branch_uid": bu})
        assert st == 200, (st, b)
        open_tills(page)
        sent = spy_bodies(page)
        assert page.locator(input_sel(BACK_ID)).input_value() == "40"
        # another computer takes 41 while the screen still believes it is free
        st, b = api(page, "POST", ALLOC, {"terminal_uid": KITCHEN_ID, "till_number": 41, "branch_uid": bu})
        assert st == 200, (st, b)
        type_number(page, BACK_ID, "41")
        assert page.locator(row_sel(BACK_ID)).get_attribute("data-problem") == "0", \
            "the client must not know about the clash (this is the SERVER's refusal)"
        human_click(page, "#sh-save", "Save bar: Save")
        page.wait_for_function("() => { const e = document.getElementById('tills-error'); "
                               "return e && !e.hidden && e.textContent.trim().length > 0; }",
                               timeout=DEFAULT_WAIT_MS)
        msg = error_text(page)
        ok = {"Till number 41 is already used by another computer. Choose a different number.",
              "Till number 41 was just given to another computer. Pick a different number."}
        assert msg in ok, f"refusal shown as {msg!r}"
        assert [(m, (bd or {}).get("replace"), (bd or {}).get("till_number")) for m, bd, _ in sent] \
            == [("POST", True, 41)], f"a change must be ONE POST with replace:true, no DELETE: {sent}"
        held = dev(tills(page), BACK_ID)["till_number"]
        assert held == 40, f"the refused change took the old number away: the computer holds {held}"
        assert dev(tills(page), KITCHEN_ID)["till_number"] == 41
        # the screen re-read the truth: the edit is kept, and Discard shows the old number
        human_click(page, "#sh-discard", "Save bar: Discard")
        page.wait_for_timeout(300)
        assert page.locator(input_sel(BACK_ID)).input_value() == "40"
        assert page.locator(input_sel(KITCHEN_ID)).input_value() == "41"
        # allow half: a free number, one POST, the old one is gone in the same step
        del sent[:]
        type_number(page, BACK_ID, "42")
        click_save(page)
        wait_toast(page, "Till numbers saved.")
        assert [(m, (bd or {}).get("replace")) for m, bd, _ in sent] == [("POST", True)], sent
        nums = {a["till_number"] for a in tills(page)["allocations"] if a["terminal_uid"] == BACK_ID}
        assert nums == {42}, f"after a change the computer must hold exactly the new number: {nums}"
    finally:
        release_all_via_api(page)
        ctx.close()


def check_pending(browser):
    """Item 5: a number released from a computer that is switched off stays reserved.
    The server says so with reason:'release_pending'; the screen names the computer,
    lists the number as reserved and never offers it."""
    ctx, page = open_page(browser, BASE)
    try:
        release_all_via_api(page)
        bu = tills(page)["branch_uid"]
        open_tills(page)          # the card is drawn while 11 is free and nobody is waiting
        sent = spy_bodies(page)
        st, b = api(page, "POST", ALLOC, {"terminal_uid": BACK_ID, "till_number": 11, "branch_uid": bu})
        assert st == 200, (st, b)
        # released while the (fake, never-answering) computer is off: no confirmation ever comes
        st, b = api(page, "DELETE", f"{ALLOC}?terminal_uid={BACK_ID}")
        assert st == 200, (st, b)
        st, b = api(page, "POST", ALLOC, {"terminal_uid": KITCHEN_ID, "till_number": 11, "branch_uid": bu})
        assert st == 409 and b.get("reason") == "release_pending", (st, b)
        assert b.get("held_by_terminal_uid") == BACK_ID and b.get("till_number") == 11, b
        want = ("Till number 11 is still reserved for Back till until that computer is switched on "
                "and confirms it gave it up. Pick another number.")
        # through the screen (stale card: it does not know yet)
        type_number(page, KITCHEN_ID, "11")
        assert page.locator(row_sel(KITCHEN_ID)).get_attribute("data-problem") == "0"
        human_click(page, "#sh-save", "Save bar: Save")
        page.wait_for_function("() => { const e = document.getElementById('tills-error'); "
                               "return e && !e.hidden && e.textContent.trim().length > 0; }",
                               timeout=DEFAULT_WAIT_MS)
        assert error_text(page) == want, error_text(page)
        assert [m for m, _, _ in sent] == ["POST"], f"the request must have reached the server: {sent}"
        assert dev(tills(page), KITCHEN_ID)["till_number"] is None
        # the card now draws the reserved number, with the computer's name
        res = page.locator('#tills-reserved li[data-till-reserved-n="11"]')
        assert res.count() == 1, "the reserved number is not drawn"
        assert res.inner_text().strip().replace("\n", " ").startswith("Till number 11"), res.inner_text()
        assert "Reserved for Back till until that computer is switched on and confirms it gave the number up." \
            in res.inner_text()
        # typing it again is refused on the screen, sending nothing
        assert hint_text(page, KITCHEN_ID) == want, hint_text(page, KITCHEN_ID)
        human_click(page, "#sh-save", "Save bar: Save")
        page.wait_for_timeout(400)
        assert len(sent) == 1, f"a reserved number was sent again: {sent}"
        human_click(page, "#sh-discard", "Save bar: Discard")
        page.wait_for_timeout(300)
        # never suggested: make the server (wrongly) offer 11 and see the screen refuse to show it
        stubbed = []

        def stub(route, request):
            r = route.fetch()
            j = r.json()
            for d in j["data"]["devices"]:
                if d["till_number"] is None:
                    d["suggested_till_number"] = 11
            stubbed.append(1)
            route.fulfill(response=r, json=j)
        page.route("**/api/sub/retail/tills*", stub)
        page.evaluate("() => { SubsystemApp._navigate('dashboard'); }")   # leave, so the card is drawn afresh
        page.wait_for_timeout(500)
        open_tills(page)
        assert stubbed, "the stub never ran, so this proves nothing"
        for d in tills(page)["devices"]:
            if d["terminal_uid"] == BACK_ID:
                continue    # it is the computer that gave 11 up: getting its own number back is allowed
            h = hint_text(page, d["terminal_uid"])
            assert "Next free number: 11" not in h, f"{d['label']} is offered a reserved number: {h!r}"
        assert page.locator(f'button[data-till-act="suggest"][data-till-uid="{KITCHEN_ID}"]:has-text("Use 11")').count() == 0
        page.unroute("**/api/sub/retail/tills*")
    finally:
        release_all_via_api(page)
        ctx.close()


def open_shift_and_sell(page, pid) -> str:
    st, b = api(page, "GET", "/api/sub/retail/branches")
    rows = b.get("data")
    if isinstance(rows, dict):
        rows = rows.get("branches") or rows.get("items") or []
    branch_id = rows[0]["id"]
    _, cur = api(page, "GET", f"/api/sub/retail/cash-sessions/current?branch_id={branch_id}")
    row = (cur or {}).get("data")
    if row:
        exp = row.get("expected_cash") if isinstance(row, dict) else None
        st, b = api(page, "POST", f"/api/sub/retail/cash-sessions/{row['id']}/close",
                    {"closing_float_counted": exp if exp is not None else 0})
        assert st == 200, f"could not close the open shift: {b}"
    st, b = api(page, "POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 0, "branch_id": branch_id})
    assert st == 200, f"could not open a shift: {b}"
    st, sale = api(page, "POST", "/api/sub/retail/sales", {
        "items": [{"product_id": pid, "quantity": 1}], "amount_paid": 100, "payment_method": "cash",
        "idempotency_key": str(uuid.uuid4())})
    assert st == 200, (st, sale)
    return sale["data"]["sale_number"]


def make_product(page) -> int:
    _, b = api(page, "POST", "/api/sub/retail/categories", {"name": "Tills Cat"})
    cat = ((b or {}).get("data") or {}).get("id")
    assert cat, b
    _, b = api(page, "POST", "/api/sub/retail/products", {
        "name": "Tills Widget", "sku": "TILL-" + uuid.uuid4().hex[:8], "sell_price": 2, "cost_price": 1,
        "category_id": cat, "unit": "pc", "initial_stock": 50})
    pid = ((b or {}).get("data") or {}).get("id")
    assert pid, b
    return pid


READABLE = re.compile(r"^[A-Z]{2,4}\d{1,2}-\d{6}$")


def check_sale_before(page, pid):
    sale_no = open_shift_and_sell(page, pid)
    assert not READABLE.match(sale_no), f"no till number was given, yet the sale printed {sale_no}"


def check_number_all_and_sale(browser, me, pid):
    ctx, page = open_page(browser, BASE)
    try:
        open_tills(page)
        # disabled while the plan is dirty (deny half)
        type_number(page, BACK_ID, "7")
        assert page.locator("#till-number-all").is_disabled(), "Number all must wait for Save or Discard"
        assert page.locator("#till-all-note").inner_text().strip() == "Save or discard your changes first."
        human_click(page, "#sh-discard", "Save bar: Discard")
        page.wait_for_timeout(300)
        assert page.locator("#till-number-all").is_enabled()
        # Cancel changes nothing
        sent = spy_allocations(page)
        human_click(page, "#till-number-all", "Number all tills", scroll_list=True)
        page.wait_for_selector("#ret-confirm-dialog", state="visible", timeout=DEFAULT_WAIT_MS)
        assert "Number all tills?" in page.locator("#ret-confirm-dialog").inner_text()
        human_click(page, "#ret-confirm-cancel", "Cancel")
        page.wait_for_selector("#ret-confirm-dialog", state="detached", timeout=DEFAULT_WAIT_MS)
        page.wait_for_timeout(400)
        assert sent == [], f"Cancel still sent {sent}"
        assert tills(page)["allocations"] == []
        # Confirm numbers every free computer
        human_click(page, "#till-number-all", "Number all tills", scroll_list=True)
        page.wait_for_selector("#ret-confirm-dialog", state="visible", timeout=DEFAULT_WAIT_MS)
        human_click(page, "#ret-confirm-ok", "Number all tills (confirm)")
        wait_toast(page, "All tills are numbered.")
        assert [m for m, _ in sent] == ["POST"], sent
        data = tills(page)
        nums = sorted(d["till_number"] for d in data["devices"])
        assert nums == [1, 2, 3], f"every computer must hold its own number: {nums}"
        for d in data["devices"]:
            assert page.locator(input_sel(d["terminal_uid"])).input_value() == str(d["till_number"])
        assert page.locator("#till-number-all").is_disabled()
        assert page.locator("#till-all-note").inner_text().strip() == "Every computer already has a number."
        n = dev(data, me)["till_number"]
        # the next shift books this till; the next sale carries its number
        sale_no = open_shift_and_sell(page, pid)
        assert sale_no == f"MAI{n}-000001", f"expected MAI{n}-000001, the sale printed {sale_no}"
    finally:
        ctx.close()


def check_non_main(browser, backend, company_id, me):
    move_main_flag(company_id, BACK_ID)
    backend.stop()
    backend.start()
    try:
        ctx, page = open_page(browser, BASE)
        try:
            open_tills(page)
            want = "Till numbers are given out on the main computer (Back till)."
            got = page.locator("#tills-main-pointer").inner_text().strip()
            assert got == want, got
            for sel in ("#tills-list", "[data-till-input]", "[data-till-act]", "#till-number-all", "#till-plan"):
                assert page.locator(sel).count() == 0, f"a computer that is not main offers {sel}"
            assert page.locator("#tills-readonly-list li[data-till-row-ro]").count() == 3
            mains = page.locator("#tills-readonly-list .sh-till-badge", has_text="Main computer").count()
            assert mains == 1, f"exactly one row must say Main computer, found {mains}"
            assert page.locator("#tills-readonly-list li", has_text="Back till").locator(
                ".sh-till-badge", has_text="Main computer").count() == 1
            assert not savebar_up(page)
            data = tills(page)
            assert data["is_main"] is False
            st, b = api(page, "POST", "/api/sub/retail/tills/allocations",
                        {"terminal_uid": KITCHEN_ID, "till_number": 3, "branch_uid": data["branch_uid"]})
            assert st in (403, 409) and b.get("code") == "TILL_NEEDS_MAIN_COMPUTER", (st, b)
        finally:
            ctx.close()
    finally:
        move_main_flag(company_id, me)
        backend.stop()
        backend.start()
    ctx, page = open_page(browser, BASE)
    try:
        open_tills(page)
        assert page.locator("#tills-list").count() == 1 and page.locator("#tills-main-pointer").count() == 0, \
            "back on the main computer the section must be editable again"
    finally:
        ctx.close()


# --------------------------------------------------------------------------- reach + themes
def check_reach(browser, viewport, lang):
    ctx, page = open_page(browser, BASE, lang=lang, viewport=viewport)
    try:
        open_tills(page)
        assert page.evaluate("() => document.documentElement.dir") == ("rtl" if lang == "ar" else "ltr")
        uids = [d["terminal_uid"] for d in tills(page)["devices"]]
        for uid in uids:
            page.evaluate("(s) => document.querySelector(s).scrollIntoView({block:'center'})", input_sel(uid))
            assert_reachable(page, input_sel(uid), f"till box {uid}")
        page.evaluate("() => document.querySelector('[data-till-act]').scrollIntoView({block:'center'})")
        assert_reachable(page, "[data-till-act]", "row button")
        page.evaluate("() => document.getElementById('till-number-all').scrollIntoView({block:'center'})")
        assert_reachable(page, "#till-number-all", "Number all tills")
        # with the Save bar up (it is sticky and could cover the last controls)
        type_number(page, uids[0], "7")
        assert savebar_up(page)
        assert_reachable(page, "#sh-save", "Save")
        assert_reachable(page, "#sh-discard", "Discard")
        page.evaluate("() => document.getElementById('till-number-all').scrollIntoView({block:'nearest'})")
        assert_reachable(page, "#till-number-all", "Number all tills (Save bar up)")
        page.evaluate("(s) => document.querySelector(s).scrollIntoView({block:'nearest'})", input_sel(uids[-1]))
        assert_reachable(page, input_sel(uids[-1]), "last till box (Save bar up)")
        over = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert over <= 1, f"the page spills {over}px sideways"
        human_click(page, "#sh-discard", "Discard")
    finally:
        ctx.close()


CONTRAST_JS = """
() => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  function parse(c) {
    const one = (sentinel) => { cx.clearRect(0, 0, 1, 1); cx.fillStyle = sentinel; cx.fillStyle = c;
      cx.fillRect(0, 0, 1, 1); return [...cx.getImageData(0, 0, 1, 1).data]; };
    const a = one('#010203'), b = one('#fdfeff');
    if (a.join() !== b.join()) throw new Error('unparseable colour ' + c);
    return [a[0], a[1], a[2], a[3] / 255];
  }
  function bgOf(el) {
    for (let e = el; e; e = e.parentElement) {
      const c = parse(getComputedStyle(e).backgroundColor);
      if (c[3] > 0.5) return c;
    }
    return [255, 255, 255, 1];
  }
  const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const ratio = (f, b) => { const x = lum(f), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const pick = (sel) => document.querySelector(sel);
  const probes = {
    'row name': '#tills-list .sh-till-name',
    'badge': '#tills-list .sh-till-badge',
    'hint (plain)': '#tills-list .sh-till-hint[data-tone=""]',
    'hint (will change)': '#tills-list .sh-till-hint[data-tone="change"]',
    'hint (problem)': '#tills-list .sh-till-hint[data-tone="problem"]',
    'number box': '#tills-list .sh-till-input',
    'row button': '#tills-list .sh-till-act',
    'Number all button': '#till-number-all',
    'note': '#till-all-note',
  };
  const out = {};
  for (const [name, sel] of Object.entries(probes)) {
    const el = pick(sel);
    if (!el) { out[name] = null; continue; }
    const fg = parse(getComputedStyle(el).color);
    out[name] = Math.round(ratio(fg, bgOf(el)) * 100) / 100;
  }
  return out;
}
"""


def check_theme(browser, theme, shots: Path):
    ctx, page = open_page(browser, BASE, theme=theme)
    try:
        assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == theme, \
            f"the theme {theme} did not take"
        open_tills(page)
        uids = [d["terminal_uid"] for d in tills(page)["devices"]]
        type_number(page, uids[1], "0")   # a problem
        type_number(page, uids[2], "7")     # a change
        page.wait_for_timeout(200)
        page.evaluate("() => document.getElementById('tills-card').scrollIntoView({block:'start'})")
        page.wait_for_timeout(200)
        page.screenshot(path=str(shots / f"tills-{theme}.png"))
        res = page.evaluate(CONTRAST_JS)
        missing = [k for k, v in res.items() if v is None]
        assert not missing, f"nothing to measure for {missing}"
        low = {k: v for k, v in res.items() if v < MIN_CONTRAST}
        assert not low, f"{theme}: below {MIN_CONTRAST}:1 -> {low} (all: {res})"
        print(f"       {theme}: min contrast {min(res.values())}", flush=True)
    finally:
        ctx.close()


# --------------------------------------------------------------------------- main
DATA_DIR = Path(".")


def main() -> int:
    global BASE, DATA_DIR
    args = sys.argv[1:]
    if "--only" in args:
        _pe.ONLY = set(args[args.index("--only") + 1].split(","))
    DATA_DIR = Path(tempfile.mkdtemp(prefix="aura_retail_s_tills_"))
    shots = Path(tempfile.mkdtemp(prefix="aura_retail_s_tills_shots_"))
    backend = Backend(DATA_DIR)
    backend.port = pick_port()
    backend.base_url = f"http://127.0.0.1:{backend.port}"
    BASE = backend.base_url
    print(f"Backend: {BASE}\nScreenshots: {shots}", flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            backend.start()
            c0 = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            p0 = c0.new_page()
            p0.goto(BASE, wait_until="domcontentloaded")
            p0.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            p0.fill("#su-name", "Tareq")
            p0.fill("#su-company", "Tills Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(DATA_DIR), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()

            company_id = _owner_company_id(DATA_DIR)
            add_fake_device(company_id, BACK_ID, "Back till")
            add_fake_device(company_id, KITCHEN_ID, "Kitchen till")

            ctx, page = open_page(browser, BASE)
            me = next(d["terminal_uid"] for d in tills(page)["devices"] if d["is_this_computer"])
            assert dev(tills(page), me)["is_main"], "setup: this computer must be the main one"
            pid = make_product(page)

            check("list", "main computer: every computer listed with the next free number, inputs and buttons exist",
                  lambda: check_list(page, me))
            check("problems", "an impossible or doubled number is refused in words and sends nothing",
                  lambda: check_problems(page))
            check("assign", "assign, discard, release and change go through the one Save bar and hold on the server",
                  lambda: check_assign_release_discard(page))
            check("refusal", "a number taken behind the screen's back is refused by the server as a plain sentence",
                  lambda: check_refusal(browser))
            check("sale", "before any number is given a sale prints the plain number",
                  lambda: check_sale_before(page, pid))
            check("number_all", "Number all asks first (Cancel changes nothing), then numbers every computer; "
                  "the next sale prints <CODE><n>-000001",
                  lambda: check_number_all_and_sale(browser, me, pid))
            release_all_via_api(page)
            for vp in VIEWPORTS:
                for lang in ("en", "ar"):
                    check("reach", f"human reach {vp['width']}x{vp['height']} {lang}",
                          lambda vp=vp, lang=lang: check_reach(browser, vp, lang))
            for theme in THEMES:
                check("themes", f"theme {theme}: took, every text clears {MIN_CONTRAST}:1, screenshot",
                      lambda th=theme: check_theme(browser, th, shots))
            ctx.close()
            check("atomic", "a refused change of number leaves the computer holding its old number; a free one is one POST",
                  lambda: check_atomic(browser))
            check("pending", "a number released from a switched-off computer: named, listed as reserved, never offered",
                  lambda: check_pending(browser))
            check("non_main", "a computer that is not main sees the pointer and nothing it could change; the server refuses too",
                  lambda: check_non_main(browser, backend, company_id, me))
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
