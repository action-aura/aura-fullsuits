"""
retail_s_users_roles_e2e.py -- Users & roles (registry v9 presets) in a REAL
browser. STANDALONE SCRIPT, NOT A PYTEST FILE.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_s_users_roles_e2e.py [--only tag,tag]

Tags: table rows custom role delegated name gate look

What each tag pins (and what the SERVER, not the page, is asked afterwards):

  table      the page's display copy of the four presets (RetailEmployees.
             _PRESET_CAPS / _PRESET_BASE_ROLE) equals the python constants the
             server enforces (user_accounts.ROLE_PRESETS / PRESET_BASE_ROLE),
             and the twelve rows of the matrix are drawn from it.
  rows       inviting each preset through the dialog leaves EXACTLY twelve
             retail.* permission rows in registry.db and the granted set is
             the preset's set -- read back from the database, not the page.
  custom     the "Custom" badge appears only where the stored rows disagree
             with the preset, and never on an agreeing row.
  role       a role edit on a preset user goes through PUT /preset, never
             PUT /role, and an accountant edited and re-saved stays
             role_preset='accountant' with unchanged rows.
  delegated  a branch manager (delegated) sees no preset controls at all, and
             the invite body keeps its exact old keys.
  name       Name field: capped at 60, sent only when typed, editable, and a
             blank value clears it.
  gate       while a device still reports registry_schema_version < 9 the
             preset controls are OFF and the reason is on screen; no /preset
             call is made.
  gaterace   the device answer arrives LATE (held with page.route). Before it,
             no preset card exists to click and the dialog says it is checking;
             an old-device answer then shows the explanation, an allowed answer
             shows the cards on the already-open dialog, and an unreadable
             answer fails CLOSED (plain Cashier/Manager picker + a sentence).
  look       Arabic/RTL, all five themes, and rule 6b (1366x768 and 1024x768,
             EN and AR): dialogs inside the window, footer buttons reachable,
             no sideways page scroll. Screenshots go to SHOTS.
"""
from __future__ import annotations

import json
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import DEFAULT_WAIT_MS, VIEWPORT, Backend  # noqa: E402
from retail_p1b_palette_e2e import check  # noqa: E402
from retail_p1b_palette_e2e import open_page as _open_page  # noqa: E402
import retail_p1b_palette_e2e as _pe  # noqa: E402
from commercial_runtime.identity import user_accounts as ua  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OWNER_EMAIL = "tareq@usersroles.test"
OWNER_PASSWORD = "UsersRolesOwner_123!"
PORT_RANGE = range(6580, 6600)
SHOTS = Path(r"C:\Users\MSI\.claude\jobs\c5a4f4c4\tmp\p2s\V2\shots")
THEMES = ["light", "sand", "dark", "night", "dusk"]
SMALL = {"width": 1024, "height": 768}
NEW_CODES = [c for c in ua.CAPABILITY_CODES]  # the twelve
DATA = {"dir": None}


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


# --------------------------------------------------------------------------- db
def reg():
    conn = sqlite3.connect(str(DATA["dir"] / "database" / "registry.db"), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def user_id(email):
    conn = reg()
    try:
        return conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    finally:
        conn.close()


def activate(email, password):
    conn = reg()
    try:
        conn.execute("UPDATE users SET password_hash=?, status='active' WHERE email=?",
                     (hash_password(password), email))
        conn.commit()
    finally:
        conn.close()


def granted(email):
    """(total retail.* rows, set of codes held at full access) straight from the DB."""
    conn = reg()
    try:
        uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
        rows = conn.execute("SELECT subsystem, access_level FROM user_permissions "
                            "WHERE user_id=? AND subsystem LIKE 'retail.%'", (uid,)).fetchall()
        return len(rows), {r["subsystem"] for r in rows if r["access_level"] == ua.ACCESS_FULL}
    finally:
        conn.close()


def set_row(email, code, level):
    conn = reg()
    try:
        uid = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
        conn.execute("UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                     (level, uid, code))
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------------- page
def api(page, base, method, path, body=None):
    fn = getattr(page.request, method.lower())
    resp = fn(base + path, data=body) if body is not None else fn(base + path)
    try:
        j = resp.json()
    except Exception:  # noqa: BLE001
        j = {}
    return resp.status, j


def go_lang(page, lang):
    if page.evaluate("() => AuraI18n.current") != lang:
        page.evaluate("(l) => AuraI18n.setLang(l)", lang)
        page.wait_for_function("(l) => AuraI18n.current === l", arg=lang, timeout=DEFAULT_WAIT_MS)
        page.wait_for_timeout(500)


def employees_screen(page, ready_action="pin"):
    page.evaluate("() => SubsystemApp._navigate('employees')")
    page.wait_for_selector(f'#emp-table button[data-action="{ready_action}"]', timeout=15000)
    page.wait_for_timeout(300)


def reload_list(page):
    page.evaluate("() => RetailEmployees._load()")
    page.wait_for_timeout(700)


def row_of(page, email):
    return page.locator("#emp-table tbody tr", has_text=email)


def click_center(page, selector):
    """Click where a person would: the element's centre, after proving it is the
    topmost thing there (a covered button is a failure, not a pass)."""
    loc = page.locator(selector).first
    loc.wait_for(state="visible", timeout=8000)
    loc.scroll_into_view_if_needed()
    box = loc.bounding_box()
    assert box, f"{selector}: no box"
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    top = loc.evaluate("(el, p) => { const e = document.elementFromPoint(p[0], p[1]); "
                       "return !!e && (e === el || el.contains(e)); }", [cx, cy])
    assert top, f"{selector}: covered at its centre"
    page.mouse.click(cx, cy)


def pick_card(page, preset):
    click_center(page, f'.emp-pcard[data-preset="{preset}"]')
    page.wait_for_timeout(120)


def watch(page):
    seen = []
    page.on("request", lambda r: seen.append((r.method, r.url.split("?")[0], r.post_data)) if "/api/" in r.url else None)
    return seen


def owner_page(browser, base, **kw):
    ctx, page = _open_page(browser, base, email=OWNER_EMAIL, password=OWNER_PASSWORD, viewport=kw.pop("viewport", VIEWPORT), **kw)
    return ctx, page


# ----------------------------------------------------------------------- seeding
PASSWORDS = {}


def invite_via_api(owner, base, email, preset, name=None, branch_uid=None):
    body = {"email": email, "role": ua.PRESET_BASE_ROLE[preset], "preset": preset}
    if name:
        body["name"] = name
    if branch_uid:
        body["branch_scope_uid"] = branch_uid
    status, j = api(owner, base, "POST", "/api/admin/employees", body)
    assert status in (200, 201) and j.get("success"), f"seed invite {email}/{preset}: {status} {j}"
    PASSWORDS[email] = "EmpPw_" + preset + "_1!"
    activate(email, PASSWORDS[email])


def first_branch_uid(owner, base):
    status, j = api(owner, base, "GET", "/api/sub/retail/branches")
    if status == 200 and not j.get("data"):
        # a fresh install has no branch row until something makes one
        cs, cj = api(owner, base, "POST", "/api/sub/retail/branches", {"name": "Main branch"})
        assert cs == 200, f"create branch: {cs} {cj}"
        status, j = api(owner, base, "GET", "/api/sub/retail/branches")
    assert status == 200 and j.get("data"), f"branches: {status} {j}"
    return j["data"][0]["uid"]


# ------------------------------------------------------------------------ cases
def case_table(browser, base):
    ctx, page = owner_page(browser, base)
    try:
        js = page.evaluate("() => ({caps: RetailEmployees._PRESET_CAPS, base: RetailEmployees._PRESET_BASE_ROLE, "
                           "order: RetailEmployees._PRESET_ORDER, rows: RetailEmployees._capRows().map(r => r[0])})")
        assert set(js["caps"].keys()) == set(ua.ROLE_PRESETS.keys()), \
            f"preset names differ: js {sorted(js['caps'])} vs server {sorted(ua.ROLE_PRESETS)}"
        for name, codes in ua.ROLE_PRESETS.items():
            assert set(js["caps"][name]) == set(codes), \
                f"{name}: page says {sorted(js['caps'][name])}, server enforces {sorted(codes)}"
            assert len(js["caps"][name]) == len(set(js["caps"][name])), f"{name}: duplicate code in the page table"
        assert js["base"] == dict(ua.PRESET_BASE_ROLE), f"base roles differ: {js['base']} vs {dict(ua.PRESET_BASE_ROLE)}"
        assert set(js["order"]) == set(ua.ROLE_PRESETS), f"card order names {js['order']}"
        assert len(js["rows"]) == 12 and set(js["rows"]) == set(ua.CAPABILITY_CODES), \
            f"the matrix lists {js['rows']}, the server has {list(ua.CAPABILITY_CODES)}"
        # and the drawn matrix agrees with the table, cell by cell
        employees_screen(page, "pin")
        page.evaluate("() => RetailEmployees._openInvite()")
        page.wait_for_selector("#emp-matrix", timeout=8000)
        cells = page.evaluate("() => [...document.querySelectorAll('#emp-matrix td[data-cap]')].map(c => "
                              "[c.dataset.preset, c.dataset.cap, c.dataset.has])")
        assert len(cells) == 48, f"matrix has {len(cells)} cells, wanted 12 x 4"
        for preset, cap, has in cells:
            want = "1" if cap in ua.ROLE_PRESETS[preset] else "0"
            assert has == want, f"matrix cell {preset}/{cap} drawn {has}, server says {want}"
    finally:
        ctx.close()


def case_rows(browser, base):
    ctx, page = owner_page(browser, base)
    try:
        employees_screen(page, "pin")
        branch_uid = first_branch_uid(page, base)
        for preset in ("cashier", "manager", "branch_manager", "accountant"):
            # not ui-<preset>: those four are seeded by invite_via_api already
            email = f"rows-{preset}@usersroles.test"
            page.evaluate("() => RetailEmployees._openInvite()")
            page.wait_for_selector(".emp-pcard", timeout=8000)
            assert page.locator(".emp-pcard").count() == 4, "the invite dialog does not show four preset cards"
            page.fill("#emp-inv-name", f"UI {preset}")
            page.fill("#emp-inv-email", email)
            pick_card(page, preset)
            hidden = page.evaluate("() => document.getElementById('emp-inv-branch-wrap').hidden")
            assert hidden == (preset != "branch_manager"), f"{preset}: branch select hidden={hidden}"
            if preset == "branch_manager":
                page.select_option("#emp-inv-branch", branch_uid)
            replies = []
            page.on("response", lambda r: replies.append((r.request.method, r.status, r.request.post_data, r.text()[:300]))
                    if "/api/admin/employees" in r.url and r.request.method == "POST" else None)
            click_center(page, "#emp-inv-btn")
            try:
                page.wait_for_selector("#emp-link-modal", timeout=10000)
            except Exception:  # noqa: BLE001
                shot(page, f"rows-fail-{preset}")
                said = page.evaluate("() => [...document.querySelectorAll('[role=alert],[role=status]')].map(e => e.textContent.trim())")
                raise AssertionError(f"{preset}: no invite link appeared; the page said {said}; POST replies {replies}")
            page.evaluate("() => RetailEmployees._closeModal('emp-link-modal')")
            total, held = granted(email)
            assert total == 12, f"{preset}: {total} retail.* rows saved, wanted exactly 12"
            assert held == set(ua.ROLE_PRESETS[preset]), \
                f"{preset}: full-access rows {sorted(held)} != preset {sorted(ua.ROLE_PRESETS[preset])}"
            status, j = api(page, base, "GET", "/api/admin/employees")
            mine = [e for e in j["employees"] if e["email"] == email][0]
            assert mine["role_preset"] == preset and mine["preset_matches"] is True, f"{preset}: server row {mine}"
            assert mine["display_name"] == f"UI {preset}", f"{preset}: name {mine.get('display_name')!r}"
    finally:
        ctx.close()


def case_custom(browser, base):
    ctx, page = owner_page(browser, base)
    email = "ui-accountant@usersroles.test"
    try:
        employees_screen(page, "pin")
        assert page.locator("[data-emp-custom]").count() == 0, "a Custom badge shows while every row agrees with its preset"
        set_row(email, ua.CAP_SELL, ua.ACCESS_FULL)  # an accountant who can also sell: disagrees
        reload_list(page)
        assert page.locator("[data-emp-custom]").count() == 1, \
            f"expected exactly one Custom badge, saw {page.locator('[data-emp-custom]').count()}"
        assert row_of(page, email).locator("[data-emp-custom]").count() == 1, "the Custom badge is on the wrong row"
        assert row_of(page, "ui-cashier@usersroles.test").locator("[data-emp-custom]").count() == 0
        # the dialog says so
        click_center(page, f'#emp-table tr:has-text("{email}") button[data-action="role"]')
        page.wait_for_selector("#emp-role-modal .emp-pcard", timeout=8000)
        assert "custom permissions" in page.inner_text("#emp-role-modal"), "the role dialog does not warn about custom permissions"
        page.evaluate("() => RetailEmployees._closeModal('emp-role-modal')")
        set_row(email, ua.CAP_SELL, ua.ACCESS_NONE)  # back to agreeing
        reload_list(page)
        assert page.locator("[data-emp-custom]").count() == 0, "the Custom badge stayed after the rows agreed again"
    finally:
        ctx.close()


def case_role(browser, base):
    ctx, page = owner_page(browser, base)
    email = "ui-accountant@usersroles.test"
    try:
        seen = watch(page)
        employees_screen(page, "pin")
        # 1. re-save the accountant unchanged
        click_center(page, f'#emp-table tr:has-text("{email}") button[data-action="role"]')
        page.wait_for_selector("#emp-role-modal .emp-pcard", timeout=8000)
        assert page.locator('#emp-role-modal input[name="emp-preset"]:checked').get_attribute("value") == "accountant"
        click_center(page, "#emp-role-btn")
        page.wait_for_function("() => !document.getElementById('emp-role-modal')", timeout=8000)
        puts = [(m, u) for m, u, _ in seen if m == "PUT"]
        assert any(u.endswith("/preset") for _, u in puts), f"no PUT /preset went out: {puts}"
        assert not any(u.endswith("/role") for _, u in puts), f"a preset user was edited through PUT /role: {puts}"
        status, j = api(page, base, "GET", "/api/admin/employees")
        mine = [e for e in j["employees"] if e["email"] == email][0]
        assert mine["role_preset"] == "accountant", f"role_preset became {mine['role_preset']!r}"
        total, held = granted(email)
        assert total == 12 and held == set(ua.ROLE_PRESETS["accountant"]), f"rows drifted: {total} {sorted(held)}"
        # 2. away and back
        for target in ("manager", "accountant"):
            click_center(page, f'#emp-table tr:has-text("{email}") button[data-action="role"]')
            page.wait_for_selector("#emp-role-modal .emp-pcard", timeout=8000)
            pick_card(page, target)
            click_center(page, "#emp-role-btn")
            page.wait_for_function("() => !document.getElementById('emp-role-modal')", timeout=8000)
            page.wait_for_timeout(500)
            status, j = api(page, base, "GET", "/api/admin/employees")
            mine = [e for e in j["employees"] if e["email"] == email][0]
            assert mine["role_preset"] == target, f"after picking {target}: role_preset={mine['role_preset']!r}"
            total, held = granted(email)
            assert total == 12 and held == set(ua.ROLE_PRESETS[target]), f"{target}: rows {total} {sorted(held)}"
        assert not any(m == "PUT" and u.endswith("/role") for m, u, _ in seen), "PUT /role went out for a preset user"
        # 3. a branch manager cannot be picked for someone with no branch
        click_center(page, f'#emp-table tr:has-text("ui-cashier@usersroles.test") button[data-action="role"]')
        page.wait_for_selector("#emp-role-modal .emp-pcard", timeout=8000)
        off = page.evaluate("() => document.querySelector('#emp-role-modal input[value=\"branch_manager\"]').disabled")
        assert off, "branch_manager is pickable for a person with no branch"
        page.evaluate("() => RetailEmployees._closeModal('emp-role-modal')")
    finally:
        ctx.close()


def case_delegated(browser, base):
    bm_email = "bm@usersroles.test"
    ctx, page = _open_page(browser, base, email=bm_email, password=PASSWORDS[bm_email], viewport=VIEWPORT)
    try:
        seen = watch(page)
        employees_screen(page, "pin")
        for act in ("role", "name", "branch", "delegation"):
            assert page.locator(f'#emp-table button[data-action="{act}"]').count() == 0, \
                f"a delegated branch manager sees the '{act}' control"
        page.evaluate("() => RetailEmployees._openInvite(true)")
        page.wait_for_selector("#emp-invite-modal", timeout=8000)
        assert page.locator('input[name="emp-preset"]').count() == 0 and page.locator("#emp-matrix").count() == 0 \
            and page.locator("#emp-inv-role").count() == 0, "the delegated invite dialog shows role controls"
        page.fill("#emp-inv-email", "cashier-by-bm@usersroles.test")
        click_center(page, "#emp-inv-btn")
        page.wait_for_selector("#emp-link-modal", timeout=10000)
        posts = [json.loads(d) for m, u, d in seen if m == "POST" and u.endswith("/api/admin/employees")]
        assert posts and sorted(posts[-1].keys()) == ["email", "role"], f"delegated POST body keys: {posts}"
        assert posts[-1]["role"] == "cashier"
        # the server-side lock behind the hidden controls
        emp_id = user_id("ui-cashier@usersroles.test")
        status, j = api(page, base, "PUT", f"/api/admin/employees/{emp_id}/preset", {"preset": "manager"})
        assert status in (401, 403), f"a delegated manager could call PUT /preset: {status} {j}"
    finally:
        ctx.close()


def case_name(browser, base):
    ctx, page = owner_page(browser, base)
    email = "ui-manager@usersroles.test"
    try:
        seen = watch(page)
        employees_screen(page, "pin")
        page.evaluate("() => RetailEmployees._openInvite()")
        page.wait_for_selector("#emp-inv-name", timeout=8000)
        assert page.get_attribute("#emp-inv-name", "maxlength") == "60"
        # no name typed -> no `name` key on the wire
        page.fill("#emp-inv-email", "noname@usersroles.test")
        click_center(page, "#emp-inv-btn")
        page.wait_for_selector("#emp-link-modal", timeout=10000)
        page.evaluate("() => RetailEmployees._closeModal('emp-link-modal')")
        posts = [json.loads(d) for m, u, d in seen if m == "POST" and u.endswith("/api/admin/employees")]
        assert "name" not in posts[-1], f"an untyped name was sent: {posts[-1]}"
        # 61 characters -> refused before any request
        n_before = len([1 for m, u, _ in seen if m == "POST" and u.endswith("/api/admin/employees")])
        page.evaluate("() => RetailEmployees._openInvite()")
        page.wait_for_selector("#emp-inv-name", timeout=8000)
        # typing cannot exceed the cap (the browser truncates) ...
        page.fill("#emp-inv-name", "x" * 61)
        assert page.input_value("#emp-inv-name") == "x" * 60, "maxlength=60 did not cap typing"
        # ... so reach the JS guard the way pasted/scripted text would: set the value directly
        page.evaluate("() => { document.getElementById('emp-inv-name').value = 'x'.repeat(61); }")
        page.fill("#emp-inv-email", "toolong@usersroles.test")
        click_center(page, "#emp-inv-btn")
        page.wait_for_timeout(500)
        n_after = len([1 for m, u, _ in seen if m == "POST" and u.endswith("/api/admin/employees")])
        assert n_after == n_before, "a 61-character name was sent to the server"
        assert page.locator("#emp-invite-modal").count() == 1, "the dialog closed on a refused name"
        page.evaluate("() => RetailEmployees._closeModal('emp-invite-modal')")
        # edit, then clear
        click_center(page, f'#emp-table tr:has-text("{email}") button[data-action="name"]')
        page.wait_for_selector("#emp-name-input", timeout=8000)
        page.fill("#emp-name-input", "Maher Manager")
        click_center(page, "#emp-name-btn")
        page.wait_for_function("() => !document.getElementById('emp-name-modal')", timeout=8000)
        status, j = api(page, base, "GET", "/api/admin/employees")
        assert [e for e in j["employees"] if e["email"] == email][0]["display_name"] == "Maher Manager"
        # the table repaints after the modal closes; wait for the repaint rather than count at once
        try:
            row_of(page, email).locator("text=Maher Manager").first.wait_for(timeout=6000)
        except Exception:  # noqa: BLE001
            pass
        assert row_of(page, email).locator("text=Maher Manager").count() >= 1, "the new name is not on the row"
        click_center(page, f'#emp-table tr:has-text("{email}") button[data-action="name"]')
        page.wait_for_selector("#emp-name-input", timeout=8000)
        page.fill("#emp-name-input", "")
        click_center(page, "#emp-name-btn")
        page.wait_for_function("() => !document.getElementById('emp-name-modal')", timeout=8000)
        status, j = api(page, base, "GET", "/api/admin/employees")
        cleared = [e for e in j["employees"] if e["email"] == email][0]["display_name"]
        assert not cleared or cleared == email, f"a blank name did not clear it: {cleared!r}"
    finally:
        ctx.close()


def gate_page(browser, base, blocked):
    ctx, page = owner_page(browser, base)
    hits = {"n": 0}

    def handler(route, request):
        if request.method != "GET" or not blocked:
            route.continue_()
            return
        try:
            resp = route.fetch()
        except Exception as exc:  # noqa: BLE001
            # The page's own poll can still be in flight when ctx.close() runs;
            # that races the browser going away and is not a product fault.
            # Only that one error is swallowed -- anything else must fail loudly.
            if "closed" in str(exc).lower():
                return
            raise
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            route.fulfill(response=resp)
            return
        hits["n"] += 1
        d = body["data"] if isinstance(body.get("data"), dict) else body
        d["role_editing"] = {"allowed": False, "min_registry_schema_version": 9,
                             "blocking_devices": [{"id": "old-1", "device_label": "Old phone", "platform": "ANDROID",
                                                   "registry_schema_version": 8}]}
        route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")

    page.route("**/api/devices", handler)
    return ctx, page, hits


def case_gate(browser, base):
    email = "ui-accountant@usersroles.test"
    ctx, page, hits = gate_page(browser, base, True)
    try:
        seen = watch(page)
        employees_screen(page, "pin")
        assert hits["n"] >= 1, "GET /api/devices was never rewritten -- this case proved nothing"
        btn = row_of(page, email).locator('button[data-action="role"]')
        assert btn.is_disabled(), "Change Role is live while a device is on the old registry"
        assert "Old phone" in page.inner_text("#emp-gate-note"), f"the gate note does not name the device: {page.inner_text('#emp-gate-note')!r}"
        assert "locked" in page.inner_text("#emp-gate-note").lower()
        page.evaluate("() => RetailEmployees._openInvite()")
        page.wait_for_selector("#emp-invite-modal", timeout=8000)
        assert page.locator(".emp-pcard").count() == 0 and page.locator("#emp-inv-role").count() == 1, \
            "the invite dialog still offers presets while the gate is shut"
        assert "locked" in page.inner_text("#emp-inv-gate").lower(), "the invite dialog does not say why"
        page.fill("#emp-inv-email", "gated@usersroles.test")
        click_center(page, "#emp-inv-btn")
        page.wait_for_selector("#emp-link-modal", timeout=10000)
        posts = [json.loads(d) for m, u, d in seen if m == "POST" and u.endswith("/api/admin/employees")]
        assert sorted(posts[-1].keys()) == ["email", "role"], f"a gated invite carried preset keys: {posts[-1]}"
        assert not any(m == "PUT" and u.endswith("/preset") for m, u, _ in seen), "a /preset call went out while gated"
    finally:
        ctx.close()
    # control: the real answer (no old device) -> presets on
    ctx, page, _hits = gate_page(browser, base, False)
    try:
        employees_screen(page, "pin")
        assert not row_of(page, email).locator('button[data-action="role"]').is_disabled(), "control: Change Role disabled with no old device"
        page.evaluate("() => RetailEmployees._openInvite()")
        page.wait_for_selector(".emp-pcard", timeout=8000)
        assert page.locator(".emp-pcard").count() == 4
    finally:
        ctx.close()


def held_devices_page(browser, base):
    """Owner page whose GET /api/devices is HELD until the test releases it.
    The route callback only parks the route; page.wait_for_timeout keeps the
    browser pumping while it waits, so the screen really is mid-question."""
    ctx, page = owner_page(browser, base)
    held = []

    def handler(route, request):
        if request.method != "GET":
            route.continue_()
            return
        held.append(route)

    page.route("**/api/devices", handler)
    return ctx, page, held


def _release(page, held, mode):
    """Answer the parked /api/devices request: 'old', 'allowed' or 'fail'."""
    for _ in range(60):
        if held:
            break
        page.wait_for_timeout(100)
    assert held, "GET /api/devices never reached the browser -- this case proved nothing"
    route = held.pop(0)
    if mode == "fail":
        route.abort()
        return
    try:
        resp = route.fetch()
    except Exception as exc:  # noqa: BLE001
        if "closed" in str(exc).lower():
            return
        raise
    body = resp.json()
    d = body["data"] if isinstance(body.get("data"), dict) else body
    if mode == "old":
        d["role_editing"] = {"allowed": False, "min_registry_schema_version": 9,
                             "blocking_devices": [{"id": "old-1", "device_label": "Old phone", "platform": "ANDROID",
                                                   "registry_schema_version": 8}]}
    else:
        d["role_editing"] = {"allowed": True, "min_registry_schema_version": 9, "blocking_devices": []}
    route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")


def _reachable_cards(page):
    """Preset cards a person could actually hit: inside the window, and the
    topmost thing at their centre (rule 6b -- not is_visible, not locator.click)."""
    return page.evaluate("""() => [...document.querySelectorAll('.emp-pcard')].filter(c => {
        const q = c.getBoundingClientRect(); if (!q.width || !q.height) return false;
        const cx = q.x + q.width / 2, cy = q.y + q.height / 2;
        if (cx < 0 || cy < 0 || cx > innerWidth || cy > innerHeight) return false;
        const top = document.elementFromPoint(cx, cy);
        return !!top && (top === c || c.contains(top));
    }).length""")


def _open_invite_while_held(page, held):
    """Start on the employees screen with the answer held, open the invite dialog."""
    page.evaluate("() => SubsystemApp._navigate('employees')")
    for _ in range(60):
        if held:
            break
        page.wait_for_timeout(100)
    assert held, "the employees screen never asked for /api/devices"
    page.evaluate("() => RetailEmployees._openInvite()")
    page.wait_for_selector("#emp-invite-modal", timeout=8000)


def case_gate_race(browser, base):
    # 1. Answer held, then "an old phone" arrives: never a card, then the explanation.
    ctx, page, held = held_devices_page(browser, base)
    try:
        _open_invite_while_held(page, held)
        assert page.locator(".emp-pcard").count() == 0, "preset cards drawn before the device answer"
        assert _reachable_cards(page) == 0
        assert page.locator("#emp-inv-role").count() == 1, "the plain Role picker is missing while checking"
        assert "Checking" in page.inner_text("#emp-inv-gate"), page.inner_text("#emp-inv-gate")
        _release(page, held, "old")
        page.wait_for_function("() => /Old phone/.test((document.getElementById('emp-inv-gate')||{}).textContent || '')", timeout=8000)
        assert page.locator(".emp-pcard").count() == 0 and _reachable_cards(page) == 0, "cards appeared for an old-device answer"
        assert "locked" in page.inner_text("#emp-inv-gate").lower()
    finally:
        ctx.close()
    # 2. Answer held, then "allowed": the OPEN dialog grows its cards, and a mouse click picks one.
    ctx, page, held = held_devices_page(browser, base)
    try:
        _open_invite_while_held(page, held)
        assert _reachable_cards(page) == 0
        _release(page, held, "allowed")
        page.wait_for_function("() => document.querySelectorAll('.emp-pcard').length === 4", timeout=8000)
        assert _reachable_cards(page) == 4, "the four cards are not all reachable once the answer allowed them"
        click_center(page, '.emp-pcard:has(input[value="accountant"])')
        assert page.evaluate("() => document.querySelector('input[name=\"emp-preset\"]:checked').value") == "accountant"
        page.screenshot(path=str(SHOTS / "gaterace_allowed.png"))
    finally:
        ctx.close()
    # 3. Answer never readable: fail CLOSED -- plain picker, one honest sentence, no cards.
    ctx, page, held = held_devices_page(browser, base)
    try:
        _open_invite_while_held(page, held)
        _release(page, held, "fail")
        page.wait_for_function("() => /Could not check/.test((document.getElementById('emp-inv-gate')||{}).textContent || '')", timeout=8000)
        assert page.locator(".emp-pcard").count() == 0 and _reachable_cards(page) == 0, "cards drawn after a failed check"
        assert page.locator("#emp-inv-role").count() == 1
        page.fill("#emp-inv-email", "failclosed@usersroles.test")
        click_center(page, "#emp-inv-btn")
        page.wait_for_selector("#emp-link-modal", timeout=10000)
    finally:
        ctx.close()


def dialog_fits(page, overlay_id, tag):
    info = page.evaluate("""(id) => {
        const o = document.getElementById(id); const d = o && o.querySelector('.ret-modal');
        if (!d) return null; const r = d.getBoundingClientRect();
        const btns = [...d.querySelectorAll('.ret-modal-footer button')].map(b => {
          const q = b.getBoundingClientRect(); const cx = q.x + q.width / 2, cy = q.y + q.height / 2;
          const top = document.elementFromPoint(cx, cy);
          return {inside: q.left >= 0 && q.right <= innerWidth && q.top >= 0 && q.bottom <= innerHeight,
                  hit: !!top && (top === b || b.contains(top))};
        });
        return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: innerWidth, h: innerHeight, btns,
                sx: document.documentElement.scrollWidth > innerWidth + 1};
    }""", overlay_id)
    assert info, f"{tag}: dialog {overlay_id} not found"
    shot(page, "fit-" + tag.replace(" ", "-").replace("/", "-"))
    assert info["l"] >= -1 and info["r"] <= info["w"] + 1, f"{tag}: dialog sideways out of the window {info}"
    assert info["t"] >= -1 and info["b"] <= info["h"] + 1, f"{tag}: dialog taller than the window {info}"
    assert info["btns"] and all(b["inside"] and b["hit"] for b in info["btns"]), f"{tag}: footer buttons unreachable {info['btns']}"
    assert not info["sx"], f"{tag}: the page scrolls sideways"


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def case_look(browser, base):
    email = "ui-accountant@usersroles.test"
    for vp_name, vp in (("1366", VIEWPORT), ("1024", SMALL)):
        for lang in ("en", "ar"):
            ctx, page = owner_page(browser, base, viewport=vp, lang=lang)
            try:
                go_lang(page, lang)
                employees_screen(page, "pin")
                rtl = page.evaluate("() => document.documentElement.dir")
                assert rtl == ("rtl" if lang == "ar" else "ltr"), f"{lang}: dir={rtl!r}"
                assert not page.evaluate("() => document.documentElement.scrollWidth > innerWidth + 1"), f"{vp_name}/{lang}: list scrolls sideways"
                shot(page, f"list-{vp_name}-{lang}")
                page.evaluate("() => RetailEmployees._openInvite()")
                page.wait_for_selector(".emp-pcard", timeout=8000)
                dialog_fits(page, "emp-invite-modal", f"invite {vp_name}/{lang}")
                shot(page, f"invite-{vp_name}-{lang}")
                page.evaluate("() => RetailEmployees._closeModal('emp-invite-modal')")
                click_center(page, f'#emp-table tr:has-text("{email}") button[data-action="role"]')
                page.wait_for_selector("#emp-role-modal .emp-pcard", timeout=8000)
                dialog_fits(page, "emp-role-modal", f"role {vp_name}/{lang}")
                shot(page, f"role-{vp_name}-{lang}")
                page.evaluate("() => RetailEmployees._closeModal('emp-role-modal')")
                if lang == "ar":
                    # every visible word inside the dialog is Arabic-capable: no English
                    # preset name left in the cards once the Arabic is merged
                    page.evaluate("() => RetailEmployees._openInvite()")
                    page.wait_for_selector(".emp-pcard", timeout=8000)
                    names = page.evaluate("() => [...document.querySelectorAll('.emp-pname')].map(e => e.textContent.trim())")
                    assert all(any("\u0600" <= ch <= "\u06ff" for ch in n) for n in names), f"English preset names in Arabic: {names}"
                    cap = page.inner_text("#emp-matrix caption")
                    assert any("\u0600" <= ch <= "\u06ff" for ch in cap), f"English matrix caption in Arabic: {cap!r}"
                    page.evaluate("() => RetailEmployees._closeModal('emp-invite-modal')")
            finally:
                ctx.close()
    # five themes, both languages, at the small window (the harder one)
    for theme in THEMES:
        for lang in ("en", "ar"):
            ctx, page = owner_page(browser, base, viewport=SMALL, lang=lang, theme=theme)
            try:
                go_lang(page, lang)
                employees_screen(page, "pin")
                page.evaluate("() => RetailEmployees._openInvite()")
                page.wait_for_selector(".emp-pcard", timeout=8000)
                dialog_fits(page, "emp-invite-modal", f"invite {theme}/{lang}")
                shot(page, f"theme-{theme}-{lang}")
                # the selected card must be told apart from an unselected one by more than nothing
                contrast = page.evaluate("""() => { const on = document.querySelector('.emp-pcard.emp-on');
                    const off = document.querySelector('.emp-pcard:not(.emp-on)');
                    const a = getComputedStyle(on), b = getComputedStyle(off);
                    return [a.borderColor, a.backgroundColor, b.borderColor, b.backgroundColor]; }""")
                assert contrast[0] != contrast[2] and contrast[1] != contrast[3], f"{theme}: selected card looks like an unselected one {contrast}"
            finally:
                ctx.close()


# -------------------------------------------------------------------------- main
def main() -> int:
    args = sys.argv[1:]
    if "--only" in args:
        _pe.ONLY = set(args[args.index("--only") + 1].split(","))

    def wanted(tag):
        return not _pe.ONLY or tag in _pe.ONLY

    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_s_users_"))
    DATA["dir"] = data_dir
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
            p0.fill("#su-company", "Users Roles Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()

            # seed: one of each the later cases need, made through the real API
            ctx, owner = owner_page(browser, base)
            try:
                branch_uid = first_branch_uid(owner, base)
                invite_via_api(owner, base, "ui-cashier@usersroles.test", "cashier", name="Cara Cashier")
                invite_via_api(owner, base, "ui-manager@usersroles.test", "manager", name="Mo Manager")
                invite_via_api(owner, base, "ui-accountant@usersroles.test", "accountant", name="Amal Accountant")
                invite_via_api(owner, base, "bm@usersroles.test", "branch_manager", name="Bassam BM", branch_uid=branch_uid)
            finally:
                ctx.close()
            # A fresh install has no company at boot, so the boot announcement
            # (app.py _announce_device) skips; until the next start this computer
            # reads as "old" and the roles gate is (correctly) shut. Restart the
            # way a shop does, so the REAL announcement runs -- no SQL stand-in.
            backend.stop()
            backend.start()
            print("SEEDING DONE.", flush=True)
            # the case_rows dialog invites use their own addresses; the accountant used
            # by custom/role/gate is the API-seeded one, so make the UI ones share it
            for tag, fn in (("table", case_table), ("rows", case_rows), ("custom", case_custom),
                            ("role", case_role), ("delegated", case_delegated), ("name", case_name),
                            ("gate", case_gate), ("gaterace", case_gate_race), ("look", case_look)):
                if wanted(tag):
                    check(tag, fn.__name__.replace("case_", "").replace("_", " "), lambda f=fn: f(browser, base))
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
