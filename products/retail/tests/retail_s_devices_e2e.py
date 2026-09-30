"""P2S item 4 -- the "Computers in this shop" card and the takeover dialog, in a
REAL browser against a REAL backend (no route stubs except the one claim-path
case, which names itself).

What it proves, by tag:
  main      on the main computer: badge, meta lines, per-row actions; every action
            is followed to the DATABASE (rename / remove / reinstate / hand over),
            and the cancel path of each confirm changes nothing.
  nonmain   on a computer that is not the main one: no action buttons at all, the
            server refuses a rename with NOT_ADMIN_DEVICE, and the takeover door
            appears only for a computer that could BE the main one (Windows).
  takeover  the dialog: button gating (password + exact TAKE OVER), a recent main
            is refused in words, wrong passwords lock, a lock holds against the
            RIGHT password, and a real takeover moves the flag + epoch.
  errors    every server code has a plain sentence, the lockout wording scales,
            an unknown code / raw server text never leaks, and the gear-menu
            claim button uses the same sentences.
  look      EN + AR, 1366x768 + 1024x768, five themes; nothing sideways, nothing
            covered, Arabic has no English left in the controls.

Run with the plain interpreter (it drives Playwright itself):
    python retail_s_devices_e2e.py [--only main,nonmain,takeover,errors,look]
One e2e script at a time: pick_port() is a racy bind check.
"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import DEFAULT_WAIT_MS, VIEWPORT, Backend  # noqa: E402
from retail_p1b_palette_e2e import check  # noqa: E402
import retail_p1b_palette_e2e as _pe  # noqa: E402
import retail_s_users_roles_e2e as ur  # noqa: E402
from retail_s_users_roles_e2e import (  # noqa: E402
    OWNER_EMAIL, OWNER_PASSWORD, SMALL, THEMES, api, click_center, dialog_fits, go_lang,
    owner_page, pick_port, reg, shot)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

THIS = {"id": None, "company": None}
WIN, DROID, GONE = "dev-win-back-office", "dev-android-phone", "dev-win-old-till"

_MOUNT_JS = """async () => {
  const old = document.getElementById('pd-host'); if (old) old.remove();
  const h = document.createElement('div'); h.id = 'pd-host';
  h.style.cssText = 'position:fixed;inset:0;overflow:auto;z-index:5000;background:var(--surface-app);padding:16px';
  document.body.appendChild(h);
  await window.PlanDevices.render(h);
  return true;
}"""


# --------------------------------------------------------------------- database
def ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def sql(query, args=()):
    conn = reg()
    try:
        cur = conn.execute(query, args)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def dev_row(dev_id):
    rows = sql("SELECT * FROM devices WHERE id=?", (dev_id,))
    return rows[0] if rows else None


def assignment():
    return sql("SELECT * FROM admin_device_assignment WHERE company_id=?", (THIS["company"],))[0]


CTL = {"page": None, "base": None}


def reset(main, main_seen_days=30, this_platform="WINDOWS"):
    """Put the registry in a KNOWN state: this computer + three others, `main`
    ('this' or one of the seeded ids) holding the main-computer role.

    The backend serves GET /api/devices/me from a process-level cached copy of
    this computer's own row (device_context._cached_device), and only the
    device routes drop it. A plain SQL flip is therefore invisible to /me --
    and to the UI, which reads /me to decide whether this is a joined
    computer. So: make this computer main, let ONE real route (a label save,
    main-only) drop the cache, then write the state we actually want. No /me
    read may happen in between; the assertion at the end proves none did."""
    for attempt in range(4):
        _write_state("this", main_seen_days, "WINDOWS")
        page, base = CTL["page"], CTL["base"]
        if page is not None:
            status, _j = api(page, base, "POST", f"/api/devices/{WIN}/label", {"label": "Back office"})
            assert status == 200, f"cache-drop label save failed: {status}"
        _write_state(main, main_seen_days, this_platform)
        if page is None:
            return
        _s, me = api(page, base, "GET", "/api/devices/me")
        want = (main == "this")
        if bool(me["device"]["is_admin_device"]) == want and me["device"]["platform"] == this_platform:
            return
    raise AssertionError(f"/me never caught up with the registry: {me}")


def _write_state(main, main_seen_days, this_platform):
    main_id = THIS["id"] if main == "this" else main
    conn = reg()
    try:
        conn.execute("DELETE FROM devices WHERE id != ?", (THIS["id"],))
        conn.execute("DELETE FROM takeover_lockout")
        seen = ago(days=main_seen_days)
        for did, label, plat, ver, last, status in (
                (WIN, "Back office", "WINDOWS", "2.1.0", seen if main == WIN else ago(days=2), "active"),
                (DROID, "Stock phone", "ANDROID", "2.0.3", None, "active"),
                (GONE, "Old till", "WINDOWS", "1.9.0", ago(days=60), "revoked")):
            conn.execute(
                "INSERT INTO devices (id, company_id, device_label, platform, is_admin_device, status,"
                " first_seen_at, last_seen_at, app_version, registry_schema_version)"
                " VALUES (?,?,?,?,0,?,?,?,?,9)",
                (did, THIS["company"], label, plat, status, ago(days=90), last, ver))
        conn.execute("UPDATE devices SET is_admin_device=0 WHERE company_id=?", (THIS["company"],))
        conn.execute("UPDATE devices SET is_admin_device=1 WHERE id=?", (main_id,))
        conn.execute("UPDATE devices SET platform=? WHERE id=?", (this_platform, THIS["id"]))
        conn.execute(
            "UPDATE admin_device_assignment SET device_id=?, epoch=epoch+1, assigned_at_utc=? WHERE company_id=?",
            (main_id, ago(days=main_seen_days), THIS["company"]))
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------------- page
def mount(page):
    page.evaluate(_MOUNT_JS)
    page.wait_for_selector("#pd-host [data-pd=shop-computers]", timeout=DEFAULT_WAIT_MS)
    page.wait_for_timeout(150)


def n(page, selector):
    return page.locator(selector).count()


def row(dev_id, part=""):
    return f'#pd-host [data-pd-shop-device="{dev_id}"] {part}'.strip()


def texts(page, selector):
    return page.evaluate("(s) => [...document.querySelectorAll(s)].map(e => e.textContent.trim())", selector)


def pd_err(page):
    return page.evaluate("() => { const e = document.querySelector('#pd-host [data-pd=shop-computers] .pd-err');"
                         " return e ? e.textContent.trim() : null; }")


def wait_sql(pred, what, timeout_s=10):
    import time
    end = time.time() + timeout_s
    while time.time() < end:
        if pred():
            return
        time.sleep(0.15)
    raise AssertionError(f"database never showed: {what}")


def live_page(browser, base, **kw):
    ctx, page = owner_page(browser, base, **kw)
    CTL["page"], CTL["base"] = page, base
    lang = kw.get("lang", "en")
    go_lang(page, lang)
    page.wait_for_function("() => window.PlanDevices && window.SubsystemApp", timeout=DEFAULT_WAIT_MS)
    return ctx, page


# ------------------------------------------------------------------------ cases
def case_main(browser, base):
    ctx, page = live_page(browser, base)
    try:
        reset("this")
        mount(page)
        # this computer is shown as the main one, and offers itself no actions
        assert n(page, "#pd-host [data-pd=this-computer] [data-pd=main-badge]") == 1, "this computer should carry the main badge"
        assert n(page, "#pd-host [data-pd=claim-main], #pd-host [data-pd=take-over]") == 0, "the main computer must not offer to claim/take over"
        # order: active rows first, the removed one last
        order = page.evaluate("() => [...document.querySelectorAll('#pd-host [data-pd-shop-device]')].map(r => r.dataset.pdShopDevice)")
        assert order[-1] == GONE and set(order) == {WIN, DROID, GONE}, f"row order {order}"
        # Windows, seen, versioned
        meta = page.inner_text(row(WIN))
        assert "Windows" in meta and "2.1.0" in meta and "Last seen" in meta, f"meta lines: {meta!r}"
        assert n(page, row(WIN, "[data-pd=main-badge]")) == 0
        for part in ("make-main", "remove", "rename"):
            assert n(page, row(WIN, f"[data-pd={part}]")) == 1, f"active Windows row needs {part}"
        assert n(page, row(WIN, "[data-pd=reinstate]")) == 0
        # Android: never seen, cannot be the main computer, can be removed / renamed
        assert "Never seen" in page.inner_text(row(DROID, "[data-pd=last-seen]"))
        assert n(page, row(DROID, "[data-pd=make-main]")) == 0, "an Android phone must not be offered the main role"
        assert n(page, row(DROID, "[data-pd=remove]")) == 1 and n(page, row(DROID, "[data-pd=rename]")) == 1
        # removed: badge + reinstate + rename, no remove / make-main
        assert n(page, row(GONE, "[data-pd=removed-badge]")) == 1
        assert n(page, row(GONE, "[data-pd=reinstate]")) == 1 and n(page, row(GONE, "[data-pd=rename]")) == 1
        assert n(page, row(GONE, "[data-pd=remove]")) == 0 and n(page, row(GONE, "[data-pd=make-main]")) == 0

        # rename: blank refused in words, label untouched; valid saves to the DB
        click_center(page, row(WIN, "[data-pd=rename]"))
        page.fill(row(WIN, "[data-pd=rename-input]"), "   ")
        click_center(page, row(WIN, "[data-pd=rename-save]"))
        page.wait_for_function("() => ((document.querySelector('#pd-host [data-pd=shop-computers] .pd-err')||{}).textContent||'').trim() !== ''", timeout=8000)
        assert pd_err(page) == "Type a name first.", f"blank rename said {pd_err(page)!r}"
        assert dev_row(WIN)["device_label"] == "Back office", "a refused rename must not change the label"
        page.fill(row(WIN, "[data-pd=rename-input]"), "Front counter")
        click_center(page, row(WIN, "[data-pd=rename-save]"))
        wait_sql(lambda: dev_row(WIN)["device_label"] == "Front counter", "label Front counter")
        page.wait_for_selector(row(WIN) + ":has-text('Front counter')", timeout=8000)
        assert n(page, row(WIN, "[data-pd=rename-input]")) == 0, "rename box should close after saving"

        # remove: cancel changes nothing, confirm revokes
        click_center(page, row(DROID, "[data-pd=remove]"))
        page.wait_for_selector("#ret-confirm-cancel", timeout=8000)
        page.click("#ret-confirm-cancel")
        page.wait_for_timeout(400)
        assert dev_row(DROID)["status"] == "active", "cancelled remove must leave the computer in the shop"
        click_center(page, row(DROID, "[data-pd=remove]"))
        page.wait_for_selector("#ret-confirm-ok", timeout=8000)
        page.click("#ret-confirm-ok")
        wait_sql(lambda: dev_row(DROID)["status"] == "revoked", "phone revoked")
        page.wait_for_selector(row(DROID, "[data-pd=removed-badge]"), timeout=8000)

        # reinstate the removed till
        click_center(page, row(GONE, "[data-pd=reinstate]"))
        wait_sql(lambda: dev_row(GONE)["status"] == "active", "old till reinstated")
        page.wait_for_selector(row(GONE, "[data-pd=make-main]"), timeout=8000)

        # hand-over: cancel first, then confirm; the flag AND the epoch move
        epoch0 = assignment()["epoch"]
        click_center(page, row(WIN, "[data-pd=make-main]"))
        page.wait_for_selector("#ret-confirm-cancel", timeout=8000)
        page.click("#ret-confirm-cancel")
        page.wait_for_timeout(400)
        assert dev_row(THIS["id"])["is_admin_device"] == 1 and assignment()["epoch"] == epoch0, "cancelled hand-over must change nothing"
        click_center(page, row(WIN, "[data-pd=make-main]"))
        page.wait_for_selector("#ret-confirm-ok", timeout=8000)
        page.click("#ret-confirm-ok")
        wait_sql(lambda: dev_row(WIN)["is_admin_device"] == 1, "flag moved to the back office")
        assert dev_row(THIS["id"])["is_admin_device"] == 0, "this computer must have let go of the flag"
        a = assignment()
        assert a["device_id"] == WIN and a["epoch"] == epoch0 + 1, f"assignment {dict(a)}"
    finally:
        ctx.close()


def case_nonmain(browser, base):
    ctx, page = live_page(browser, base)
    try:
        reset(WIN)
        mount(page)
        assert n(page, "#pd-host [data-pd=shop-computers] button") == 0, "a computer that is not the main one must show no action buttons"
        assert "Only the main computer can do this" in page.inner_text("#pd-host [data-pd=shop-computers]")
        assert n(page, row(WIN, "[data-pd=main-badge]")) == 1, "the real main computer should be marked"
        assert n(page, "#pd-host [data-pd=take-over]") == 1, "a Windows computer that is not the main one should offer the takeover door"
        # the server says no by itself -- the missing buttons are not the guard
        status, body = api(page, base, "POST", f"/api/devices/{DROID}/label", {"label": "Sneaky"})
        assert status in (403, 409) and body.get("code") == "NOT_ADMIN_DEVICE", f"rename from a non-main: {status} {body}"
        assert dev_row(DROID)["device_label"] == "Stock phone"
        status, body = api(page, base, "POST", f"/api/devices/{DROID}/revoke")
        assert status in (403, 409) and body.get("code") == "NOT_ADMIN_DEVICE", f"revoke from a non-main: {status} {body}"
        # the Windows-only filter: a computer that reports ANDROID gets no takeover door
        reset(WIN, this_platform="ANDROID")
        mount(page)
        assert n(page, "#pd-host [data-pd=take-over]") == 0, "an Android computer must not be offered the main role"
        reset(WIN, this_platform="WINDOWS")
        mount(page)
        assert n(page, "#pd-host [data-pd=take-over]") == 1
    finally:
        ctx.close()


def open_takeover(page):
    click_center(page, "#pd-host [data-pd=take-over]")
    page.wait_for_selector("#pd-takeover-modal #pd-takeover-pw", timeout=8000)


def human_type(page, selector, text):
    """Rule 6b: reach the field the way a person does (topmost at its centre, real mouse click),
    then type with the keyboard. page.fill would work on a field a person cannot reach."""
    click_center(page, selector)
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    if text:
        page.keyboard.type(text)


def try_takeover(page, password, phrase="TAKE OVER"):
    human_type(page, "#pd-takeover-pw", password)
    human_type(page, "#pd-takeover-phrase", phrase)
    assert not page.is_disabled("#pd-takeover-btn"), "button should be ready"
    click_center(page, "#pd-takeover-btn")
    page.wait_for_function(
        "() => { const e = document.getElementById('pd-takeover-err'); return !e || e.textContent.trim() !== ''; }",
        timeout=10000)


def err_now(page):
    return page.evaluate("() => { const e = document.getElementById('pd-takeover-err'); return e ? e.textContent.trim() : null; }")


def case_takeover(browser, base):
    ctx, page = live_page(browser, base)
    try:
        reset(WIN, main_seen_days=30)
        mount(page)
        open_takeover(page)
        btn = "#pd-takeover-btn"
        assert page.is_disabled(btn), "nothing typed: disabled"
        page.fill("#pd-takeover-pw", "x")
        assert page.is_disabled(btn), "password only: disabled"
        for wrong in ("take over", "TAKE", "TAKE OVE", "TAKEOVER"):
            page.fill("#pd-takeover-phrase", wrong)
            assert page.is_disabled(btn), f"phrase {wrong!r} must not enable the button"
        page.fill("#pd-takeover-phrase", "  TAKE OVER  ")
        assert not page.is_disabled(btn), "password + exact phrase (spaces trimmed): enabled"
        page.fill("#pd-takeover-pw", "")
        assert page.is_disabled(btn), "phrase only: disabled again"
        # rule 6b: the Cancel button is reachable by a real click and it closes the box
        click_center(page, "#pd-takeover-modal .ret-modal-footer button.ret-btn-ghost")
        page.wait_for_selector("#pd-takeover-modal", state="detached", timeout=5000)

        # a main seen recently is refused in words, with the RIGHT password, and nothing moves
        reset(WIN, main_seen_days=1)
        mount(page)
        open_takeover(page)
        epoch0 = assignment()["epoch"]
        try_takeover(page, OWNER_PASSWORD)
        assert "online in the last 7 days" in err_now(page), f"recent-main said {err_now(page)!r}"
        assert dev_row(WIN)["is_admin_device"] == 1 and dev_row(THIS["id"])["is_admin_device"] == 0
        assert assignment()["epoch"] == epoch0, "a refused takeover must not move the epoch"
        assert page.evaluate("() => document.getElementById('pd-takeover-pw').value") == "", "password box is cleared after a refusal"

        # wrong passwords: plain sentence first, then the lock, with a real lock row
        said = []
        for _ in range(6):
            try_takeover(page, "definitely-not-it")
            said.append(err_now(page))
            if said[-1].startswith("Too many wrong passwords"):
                break
        assert said[0] == "Your password is not correct.", f"first miss said {said[0]!r}"
        assert said[-1].startswith("Too many wrong passwords. Try again in ") and "minutes" in said[-1], f"lock sentence {said[-1]!r}"
        lock = sql("SELECT * FROM takeover_lockout")
        assert len(lock) == 1 and lock[0]["locked_until_utc"], "a lock row with a deadline must exist"
        # the lock holds against the RIGHT password (a recent main would say 'online in the last 7 days')
        try_takeover(page, OWNER_PASSWORD)
        assert err_now(page).startswith("Too many wrong passwords"), f"locked but said {err_now(page)!r}"
        assert dev_row(THIS["id"])["is_admin_device"] == 0
        page.keyboard.press("Escape")
        page.wait_for_selector("#pd-takeover-modal", state="detached", timeout=5000)

        # once the lock is gone and the main has been silent long enough, it works
        reset(WIN, main_seen_days=30)
        mount(page)
        open_takeover(page)
        epoch0 = assignment()["epoch"]
        try_takeover_ok(page, OWNER_PASSWORD)
        wait_sql(lambda: dev_row(THIS["id"])["is_admin_device"] == 1, "flag moved to this computer")
        assert dev_row(WIN)["is_admin_device"] == 0
        a = assignment()
        assert a["device_id"] == THIS["id"] and a["epoch"] == epoch0 + 1, f"assignment {dict(a)}"
    finally:
        ctx.close()


def try_takeover_ok(page, password):
    human_type(page, "#pd-takeover-pw", password)
    human_type(page, "#pd-takeover-phrase", "TAKE OVER")
    click_center(page, "#pd-takeover-btn")
    page.wait_for_selector("#pd-takeover-modal", state="detached", timeout=15000)


ERROR_TABLE = {
    "NOT_ADMIN_DEVICE": "Only the main computer can do this. Open this screen on the main computer.",
    "ADMIN_ROLE_REQUIRED": "Only the shop owner can do this.",
    "DEVICE_NOT_FOUND": "That computer is not in this shop any more.",
    "DEVICE_REVOKED": "That computer was removed from the shop. Bring it back first.",
    "DEVICE_NOT_REVOKED": "That computer is already part of the shop.",
    "DEVICE_NOT_ELIGIBLE": "That computer cannot be the main computer.",
    "MAIN_MUST_BE_WINDOWS": "Only a Windows computer can be the main computer.",
    "ADMIN_DEVICE_ALREADY_CLAIMED": "Another computer is already the main computer.",
    "CANNOT_REVOKE_MAIN": "The main computer cannot be removed. Make another computer the main one first.",
    "DEVICE_NAME_REQUIRED": "Type a name first.",
    "DEVICE_NAME_TOO_LONG": "The name can be at most 60 characters.",
    "DEVICE_NAME_INVALID": "That name has characters that are not allowed.",
    "TAKEOVER_CONFIRM_REQUIRED": "Type TAKE OVER to confirm.",
    "TAKEOVER_PASSWORD_INCORRECT": "Your password is not correct.",
    "TAKEOVER_NO_MAIN": "This shop has no main computer to replace. Set this computer as the main computer instead.",
    "TAKEOVER_ALREADY_MAIN": "This computer is already the main computer.",
    "TAKEOVER_MAIN_SEEN_RECENTLY": "The main computer was online in the last 7 days. You can only take over when it has been silent for at least 7 days.",
    "TAKEOVER_MAIN_UNKNOWN": "This computer cannot tell when the main computer was last online, so it cannot take over.",
    "TAKEOVER_DEVICE_NOT_ACTIVE": "This computer was removed from the shop and cannot take over.",
    "TAKEOVER_CLOCK_BEHIND": "The clock on this computer is behind the shop records. Correct the date and time, then try again.",
}
LOCK_TABLE = ((1, "Too many wrong passwords. Try again in 1 minute."),
              (60, "Too many wrong passwords. Try again in 1 minute."),
              (61, "Too many wrong passwords. Try again in 2 minutes."),
              (900, "Too many wrong passwords. Try again in 15 minutes."),
              (7140, "Too many wrong passwords. Try again in 119 minutes."),
              (7200, "Too many wrong passwords. Try again in 2 hours."),
              (7201, "Too many wrong passwords. Try again in 3 hours."),
              (169200, "Too many wrong passwords. Try again in 47 hours."),
              (172800, "Too many wrong passwords. Try again in 2 days."),
              (172801, "Too many wrong passwords. Try again in 3 days."))


def case_errors(browser, base):
    ctx, page = live_page(browser, base)
    try:
        et = lambda body: page.evaluate("(b) => window.PlanDevices.errorText(b)", body)  # noqa: E731
        for code, sentence in ERROR_TABLE.items():
            got = et({"code": code, "error": "RAW SERVER ENGLISH"})
            assert got == sentence, f"{code}: {got!r}"
        for secs, sentence in LOCK_TABLE:
            got = et({"code": "TAKEOVER_LOCKED", "retry_after_seconds": secs})
            assert got == sentence, f"lock {secs}s: {got!r}"
        # Arabic has four number forms (1, 2, 3-10, 11+). One glued sentence read
        # wrong for most counts; each count now gets its own whole phrase.
        head = "كلمات مرور خاطئة كثيرة. حاول مجددًا بعد "
        AR_LOCK = ((60, "دقيقة واحدة"),      # 1 minute
                   (120, "دقيقتين"),         # 2 minutes (dual)
                   (180, "3 دقائق"),         # 3 (few)
                   (600, "10 دقائق"),        # 10 (few)
                   (660, "11 دقيقة"),        # 11 (many)
                   (7200, "ساعتين"),         # 2 hours (dual)
                   (7201, "3 ساعات"),        # 3 hours (few)
                   (172800, "يومين"),        # 2 days (dual)
                   (172801, "3 أيام"))       # 3 days (few)
        page.evaluate("() => { window.AuraI18n.current = 'ar'; }")
        try:
            for secs, phrase in AR_LOCK:
                got = et({"code": "TAKEOVER_LOCKED", "retry_after_seconds": secs})
                assert got == head + phrase + ".", f"arabic lock {secs}s: {got!r}"
        finally:
            page.evaluate("() => { window.AuraI18n.current = 'en'; }")
        assert et({"code": "TAKEOVER_LOCKED"}) == "Too many wrong passwords. Try again later."
        assert et({"code": "TAKEOVER_LOCKED", "retry_after_seconds": "soon"}) == "Too many wrong passwords. Try again later."
        generic = "That did not work. Please try again."
        for body in ({"code": "SOMETHING_NEW", "error": "RAW SERVER ENGLISH"}, {"error": "RAW SERVER ENGLISH"}, None, {}, {"code": 7}):
            got = et(body)
            assert got == generic and "RAW" not in got, f"unknown {body}: {got!r}"

        # the gear-menu claim button says the same sentences (its own network call stubbed once)
        def claim_says(status, body):
            def handler(route):
                route.fulfill(status=status, content_type="application/json", body=__import__("json").dumps(body))
            page.route("**/api/devices/me/claim-admin", handler)
            try:
                page.evaluate("() => { window.__claimDone = false; SubsystemApp._claimAdminDeviceFromMenu().then(() => { window.__claimDone = true; }); }")
                page.wait_for_function("() => window.__claimDone === true", timeout=8000)
                return page.evaluate("() => [...document.querySelectorAll('[role=alert],[role=status]')].map(e => e.textContent.trim()).filter(Boolean).pop() || ''")
            finally:
                page.unroute("**/api/devices/me/claim-admin")
        got = claim_says(409, {"code": "JOINED_DEVICE_CANNOT_CLAIM", "error": "RAW SERVER ENGLISH"})
        assert got == "This computer joined an existing shop, so it cannot become the main computer by itself. Ask the main computer to hand the role over.", f"claim joined: {got!r}"
        got = claim_says(409, {"code": "MAIN_MUST_BE_WINDOWS"})
        assert got == "Only a Windows computer can be the main computer.", f"claim android: {got!r}"
        got = claim_says(500, {"error": "RAW SERVER ENGLISH"})
        assert "RAW" not in got and got, f"claim unknown said {got!r}"
    finally:
        ctx.close()


def has_arabic(s):
    return any("؀" <= ch <= "ۿ" for ch in s)


def check_card_look(page, tag, lang):
    assert not page.evaluate("() => document.documentElement.scrollWidth > innerWidth + 1"), f"{tag}: page scrolls sideways"
    # every action button is reachable at its centre (click_center asserts topmost, without clicking through)
    for sel in ("rename", "make-main", "remove", "reinstate"):
        for i in range(n(page, f"#pd-host [data-pd={sel}]")):
            loc = page.locator(f"#pd-host [data-pd={sel}]").nth(i)
            loc.scroll_into_view_if_needed()
            box = loc.bounding_box()
            assert box and box["width"] > 0, f"{tag}: {sel} has no box"
            ok = loc.evaluate("(el) => { const r = el.getBoundingClientRect();"
                              " const e = document.elementFromPoint(r.x + r.width/2, r.y + r.height/2);"
                              " return !!e && (e === el || el.contains(e)) && r.left >= 0 && r.right <= innerWidth; }")
            assert ok, f"{tag}: {sel}[{i}] covered or off-screen"
    if lang == "ar":
        for sel in ("#pd-host [data-pd=shop-computers] h2", "#pd-host [data-pd=shop-computers] button"):
            for txt in texts(page, sel):
                assert has_arabic(txt), f"{tag}: English left in Arabic control {txt!r}"


def case_look(browser, base):
    combos = [(vp_name, vp, lang, None) for vp_name, vp in (("1366", VIEWPORT), ("1024", SMALL)) for lang in ("en", "ar")]
    combos += [("1024", SMALL, lang, theme) for theme in THEMES for lang in ("en", "ar")]
    for vp_name, vp, lang, theme in combos:
        tag = f"{theme or 'default'}-{vp_name}-{lang}"
        kw = {"viewport": vp, "lang": lang}
        if theme:
            kw["theme"] = theme
        ctx, page = live_page(browser, base, **kw)
        try:
            assert page.evaluate("() => document.documentElement.dir") == ("rtl" if lang == "ar" else "ltr")
            reset("this")
            mount(page)
            check_card_look(page, "main " + tag, lang)
            shot(page, f"devices-main-{tag}")
            reset(WIN, main_seen_days=30)
            mount(page)
            open_takeover(page)
            try_err = "#pd-takeover-err"
            human_type(page, "#pd-takeover-pw", "wrong-on-purpose")
            human_type(page, "#pd-takeover-phrase", "TAKE OVER")
            click_center(page, "#pd-takeover-btn")
            page.wait_for_function("() => document.getElementById('pd-takeover-err').textContent.trim() !== ''", timeout=10000)
            dialog_fits(page, "pd-takeover-modal", "takeover " + tag)
            if lang == "ar":
                for sel in ("#pd-takeover-dialog-title", "#pd-takeover-dialog p", "#pd-takeover-dialog label",
                            "#pd-takeover-dialog .ret-modal-footer button", try_err):
                    for txt in texts(page, sel):
                        assert has_arabic(txt), f"takeover {tag}: English left in {sel}: {txt!r}"
            page.keyboard.press("Escape")
            page.wait_for_selector("#pd-takeover-modal", state="detached", timeout=5000)
            shot(page, f"devices-nonmain-{tag}")
            check_card_look(page, "nonmain " + tag, lang)
        finally:
            ctx.close()


# ------------------------------------------------------------------------- main
def main() -> int:
    args = sys.argv[1:]
    if "--only" in args:
        _pe.ONLY = set(args[args.index("--only") + 1].split(","))

    def wanted(tag):
        return not _pe.ONLY or tag in _pe.ONLY

    # This script's own ports (one crew, one range) and its own screenshot folder.
    ur.PORT_RANGE = range(6460, 6480)
    ur.SHOTS = Path(r"C:\Users\MSI\.claude\jobs\c5a4f4c4\tmp\p2s\U3\shots")
    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_s_devices_"))
    ur.DATA["dir"] = data_dir
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
            p0.fill("#su-company", "Devices Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()
            # restart the way a shop does, so the REAL boot announce records this computer
            backend.stop()
            backend.start()
            ctx, page = owner_page(browser, base)
            try:
                status, body = api(page, base, "GET", "/api/devices")
                mine = [d for d in body.get("devices", []) if d.get("is_this_device")]
                assert status == 200 and len(mine) == 1, f"seed: this computer not in the registry: {status} {body}"
                THIS["id"] = mine[0]["id"]
                THIS["company"] = sql("SELECT company_id FROM devices WHERE id=?", (THIS["id"],))[0]["company_id"]
            finally:
                ctx.close()
            print("SEEDING DONE.", flush=True)
            for tag, fn in (("main", case_main), ("nonmain", case_nonmain), ("takeover", case_takeover),
                            ("errors", case_errors), ("look", case_look)):
                if wanted(tag):
                    check(tag, fn.__name__.replace("case_", ""), lambda f=fn: f(browser, base))
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
