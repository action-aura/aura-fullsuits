"""
retail_p1a_shell_e2e.py -- P1a crew S, real-browser proof for the shell
items a JS-sandbox unit test cannot see: rendered GEOMETRY (does an owner's
sidebar group header actually fit inside 1366x768, in EN and AR) and the
real Admin Center screen a cashier gets when they type #admin-center by
hand.

============================================================================
STANDALONE SCRIPT. THIS IS **NOT** A PYTEST FILE. DO NOT RUN IT VIA PYTEST.
============================================================================
Playwright is installed under the SYSTEM Python
(``C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe``), NOT
under this repo's pytest venv. Run it directly with the system interpreter:

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_p1a_shell_e2e.py

WHAT THIS PROVES (contract exit criteria this crew owns)
  1. At 1366x768, every OWNER sidebar group header's bounding box lies
     inside the viewport, in EN and AR (measured -- retail_design_contrast_
     test.js and the JS nav-groups sandbox cannot render real geometry).
  2. A cashier sees no Settings gear, no Categories, no Suppliers, no
     Purchase Orders anywhere in the rendered shell.
  3. #admin-center opened by URL (a direct deep link, not a nav click) as a
     cashier renders NO Save button anywhere on the page. The lock panel
     ITSELF (the actual screen a cashier with zero visible Settings
     sections sees) is crew T's build (subsystem-retail.js's
     _renderAdminCenter first line) -- this file asserts the ABSENCE of a
     working Save, and separately reports (does not fail on, and does not
     edit subsystem-retail.js to add) whether a lock-panel-shaped element is
     present, per the contract's explicit instruction to report rather than
     reach into another crew's file.

HOW THE BACKEND IS BOOTED
Same real subprocess pattern as retail_smoke_e2e.py's own ``Backend``
class -- the project's own venv Python running ``app.py`` unmodified, on a
free localhost port, against a brand-new temporary ``AURA_APP_DATA``.

HOW THE CASHIER ACCOUNT IS CREATED
The real employees API (``POST /api/admin/employees``) issues an INVITE
link for a new cashier rather than a settable password up front -- driving
that whole invite/#setup/<token> UI flow is a different feature this phase
does not touch. So the cashier row is inserted directly into the SAME
running backend's registry.db (the identical real-account/real-capability-
seeding shape retail_p0_owner_guard_test.py's own ``_make_user`` already
uses, and the SAME "flip a column in a real sqlite file this suite reads
back from" idea retail_smoke_e2e.py's own scenario 03b uses for its
joined-computer fixture), and the cashier's SESSION is established with a
REAL ``POST /api/auth/login`` the browser itself performs -- so every
assertion below still exercises the real authentication route and the real
authorization gate on every route the cashier's browser calls; only the
one-time ACCOUNT ROW is seeded directly rather than driven through the
invite-link UI.

Run: C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe products/retail/tests/retail_p1a_shell_e2e.py
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

# This console is cp1252 by default on this machine, and group-header text
# carries a real U+25B8/U+25BE chevron glyph -- reconfigure stdout/stderr to
# UTF-8 so a failure message that quotes rendered text never itself crashes
# the run before the real assertion failure is ever printed.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TESTS_DIR = Path(__file__).resolve().parent
PRODUCT_DIR = TESTS_DIR.parent
BACKEND_DIR = PRODUCT_DIR / "backend"
REPO_ROOT = PRODUCT_DIR.parent.parent
VENV_PYTHON = Path("C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe")

# commercial_runtime.licensing_contracts.test_support (stdlib-only: json/
# sqlite3/pathlib, no `cryptography`) is imported under THIS script's own
# SYSTEM Python, not the venv -- same reasoning retail_smoke_e2e.py's module
# docstring gives for its own identical import.
for _p in (str(REPO_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HEALTH_TIMEOUT_SECONDS = 30.0
DEFAULT_WAIT_MS = 15_000
VIEWPORT = {"width": 1366, "height": 768}  # the owner's own cheap-laptop budget (P1a item 4)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_err = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/api/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            last_err = e
        time.sleep(0.25)
    raise RuntimeError(f"Backend never answered GET {base_url}/api/health within {timeout}s (last error: {last_err!r})")


class Backend:
    def __init__(self, app_data_dir: Path) -> None:
        self.port = _find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.app_data_dir = app_data_dir
        self.log_path = self.app_data_dir / "backend_stdout.log"
        self.proc: subprocess.Popen | None = None
        self._log_file = None

    def start(self) -> None:
        env = os.environ.copy()
        env["AURA_STANDALONE"] = "1"
        env["PORT"] = str(self.port)
        env["AURA_APP_DATA"] = str(self.app_data_dir)
        self._log_file = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [str(VENV_PYTHON), "app.py"], cwd=str(BACKEND_DIR), env=env,
            stdout=self._log_file, stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_health(self.base_url, HEALTH_TIMEOUT_SECONDS)
        except Exception:
            self.stop()
            tail = ""
            try:
                tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            except OSError:
                pass
            raise RuntimeError("Backend failed to become healthy.\n--- backend stdout/stderr tail ---\n" + tail)

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=10)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None


def _owner_company_id(app_data_dir: Path) -> str:
    """GET /api/auth/session deliberately does not expose company_id (see
    onboarding_routes.py's get_session -- 'user' carries only id/employee_id/
    email/role/clinic_role), so this reads it the same direct-registry.db way
    _seed_cashier below does, rather than trying to derive or guess it."""
    os.environ["AURA_APP_DATA"] = str(app_data_dir)
    for _p in (str(REPO_ROOT), str(BACKEND_DIR)):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    from commercial_runtime.identity.registry_db import get_conn

    conn = get_conn()
    try:
        row = conn.execute("SELECT company_id FROM users WHERE role='admin' LIMIT 1").fetchone()
        assert row is not None, "no admin user row found in registry.db after signup"
        return row["company_id"]
    finally:
        conn.close()


def _clear_admin_device(app_data_dir: Path, company_id: str) -> int:
    """Direct UPDATE against the SAME running backend's registry.db, same
    "flip a real sqlite column" technique as _seed_cashier below -- flips
    devices.is_admin_device back to 0 for this company, simulating the real
    state device_registry.py's own schema allows (a wiped/reset admin
    device, or one that never got auto-claimed) rather than one this app can
    reach through its own UI: _autoClaimAdminOnSignup() (app-shell.js) fires
    a silent POST /me/claim-admin the instant an owner account is created,
    so by the time any test's signup flow returns, the signing-up device
    already holds the flag -- can_claim_admin is unreachably false from a
    fresh UI-only signup. Returns the number of rows flipped, so the caller
    can assert this genuinely changed something rather than silently
    matching zero rows.
    """
    os.environ["AURA_APP_DATA"] = str(app_data_dir)
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(BACKEND_DIR))
    from commercial_runtime.identity.registry_db import get_conn

    conn = get_conn()
    try:
        cur = conn.execute(
            "UPDATE devices SET is_admin_device = 0 WHERE company_id = ? AND is_admin_device = 1",
            (company_id,),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def _seed_cashier(app_data_dir: Path, company_id: str, email: str, password: str) -> None:
    """Direct INSERT into the running backend's OWN registry.db -- see this
    file's module docstring for why (the invite-link UI is a different
    feature). Opened and closed immediately, after the backend is confirmed
    idle (no scenario is mid-request at the call site below), matching
    retail_smoke_e2e.py scenario 03b's own "flip a real sqlite column"
    technique."""
    os.environ["AURA_APP_DATA"] = str(app_data_dir)
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(BACKEND_DIR))
    from commercial_runtime.identity import user_accounts
    from commercial_runtime.identity.registry_db import get_conn
    from commercial_runtime.security.passwords import hash_password

    user_id = str(uuid.uuid4())
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
            "require_password_change, branch_scope_uid) VALUES (?,?,?,?,?,?,?,0,?)",
            (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password),
             "cashier", "active", None),
        )
        conn.execute(
            "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), user_id, "retail", "full"),
        )
        user_accounts.seed_capabilities_for_user(conn, user_id, "cashier")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, fn) -> None:
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False, str(e)))
        print(f"[FAIL] {name}\n       {e}")


# ---------------------------------------------------------------------------
# REACHABILITY LIKE A PERSON (contract rule 6b, fix round 3).
#
# Playwright's own locator.click() and is_visible() SUCCEED on an element that
# is entirely off-screen, or under another element, because Playwright scrolls
# and retries -- a person cannot. That is exactly how the settings-gear menu
# shipped opening at y=776..993 on a 768px screen with every check green.
# Every "can the user reach X" question in this file goes through
# human_click(): (1) the element's box is fully INSIDE the viewport, (2)
# document.elementFromPoint() at its centre is the element or inside it (not
# covered, not clipped by a scroll container), (3) the click is a raw
# page.mouse.click() at those coordinates -- so it lands on whatever is really
# there, or the check has already failed.
#
# scroll_list=True is the ONE concession: a person can swipe a scrollable LIST
# (the phone More sheet, whose rows are taller than the sheet by design), so
# the element is first scrolled within its own scroll containers. It is used
# only for the More sheet rows, never for a menu, a header or a tab.
PROBE_JS = """([sel, text, n]) => {
  let els = [...document.querySelectorAll(sel)];
  if (text) els = els.filter(e => (e.textContent || '').includes(text));
  const el = els[n];
  if (!el) return { found: false, count: els.length };
  const r = el.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const hit = document.elementFromPoint(cx, cy);
  return {
    found: true, count: els.length,
    left: r.left, right: r.right, top: r.top, bottom: r.bottom,
    width: r.width, height: r.height, cx, cy, vw, vh,
    // 0.5px of sub-pixel slack (percent-based sheet heights land at e.g.
    // 844.1); the centre-point hit test below is what proves it is clickable.
    inside: r.width > 0 && r.height > 0 && r.left >= -0.5 && r.top >= -0.5 && r.right <= vw + 0.5 && r.bottom <= vh + 0.5,
    hit: !!(hit && (el === hit || el.contains(hit))),
    hitDesc: hit ? (hit.tagName + '#' + hit.id + '.' + String(hit.className)) : null,
  };
}"""


def probe(page, sel: str, text: str | None = None, nth: int = 0) -> dict:
    return page.evaluate(PROBE_JS, [sel, text, nth])


def assert_reachable(page, sel: str, label: str | None = None, text: str | None = None, nth: int = 0) -> dict:
    label = label or sel
    p = probe(page, sel, text, nth)
    assert p["found"], f"[{label}] no element matches {sel!r}" + (f" containing {text!r}" if text else "") + f" (matched {p['count']})"
    assert p["inside"], (
        f"[{label}] its box x={p['left']:.1f}..{p['right']:.1f} y={p['top']:.1f}..{p['bottom']:.1f} is not fully "
        f"inside the {p['vw']}x{p['vh']} viewport -- a person cannot click it"
    )
    assert p["hit"], (
        f"[{label}] is inside the viewport but elementFromPoint at its own centre ({p['cx']:.0f},{p['cy']:.0f}) "
        f"is {p['hitDesc']!r}, not the element -- it is covered or clipped"
    )
    return p


def human_click(page, sel: str, label: str | None = None, text: str | None = None, nth: int = 0,
                scroll_list: bool = False) -> dict:
    if scroll_list:
        page.evaluate(
            """([sel, text, n]) => {
              let els = [...document.querySelectorAll(sel)];
              if (text) els = els.filter(e => (e.textContent || '').includes(text));
              if (els[n]) els[n].scrollIntoView({ block: 'nearest' });
            }""",
            [sel, text, nth],
        )
        page.wait_for_timeout(60)
    p = assert_reachable(page, sel, label, text, nth)
    page.mouse.click(p["cx"], p["cy"])
    return p


OWNER_EMAIL = "p1a-owner@example.test"
OWNER_PASSWORD = "P1aOwner123!"


def _api_login(page, email: str, password: str) -> None:
    res = page.evaluate(
        """(creds) => fetch('/api/auth/login', {
            method: 'POST', credentials: 'include',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(creds),
        }).then(r => r.json())""",
        {"email": email, "password": password},
    )
    assert res.get("success") or res.get("status") == "success" or "user" in res, (
        f"real POST /api/auth/login for {email} did not report success: {res!r}"
    )


@contextlib.contextmanager
def session(browser, base_url: str, viewport: dict, *, lang: str = "en", coarse: bool = False,
            stubs: dict | None = None, email: str = OWNER_EMAIL, password: str = OWNER_PASSWORD):
    """A brand-new browser context signed in as `email`, closed on exit -- so a
    failed Arabic check can never leave the NEXT check in Arabic (the language
    lives in the context's own localStorage). `stubs` maps a URL glob to the
    JSON body its route is fulfilled with (installed BEFORE the first load, so
    the shell's very first poll sees it). `coarse` emulates a touchscreen:
    has_touch=True, and the emulation is asserted to have taken (a test that
    silently measured a fine pointer would prove nothing)."""
    ctx = browser.new_context(viewport=viewport, reduced_motion="reduce", has_touch=coarse)
    try:
        page = ctx.new_page()
        def _fulfiller(body: str):
            # Playwright calls a route handler as handler(route, request), so a
            # default-argument lambda would have its `body` overwritten by the
            # request object -- hence a real closure.
            return lambda route, _request=None: route.fulfill(
                status=200, content_type="application/json", body=body)

        for pattern, payload in (stubs or {}).items():
            page.route(pattern, _fulfiller(json.dumps(payload)))
        page.goto(base_url, wait_until="domcontentloaded")
        _api_login(page, email, password)
        page.goto(base_url, wait_until="domcontentloaded")
        page.locator("#sub-header-section").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        is_coarse = page.evaluate("() => matchMedia('(pointer: coarse)').matches")
        assert is_coarse == coarse, f"pointer emulation did not take: matchMedia coarse={is_coarse}, wanted {coarse}"
        # The language is persisted on the ACCOUNT (POST /api/auth/language), and
        # the shared owner account is left in Arabic by any earlier Arabic
        # session -- so a fresh login can START in Arabic. Never toggle; set the
        # wanted language explicitly (found when an "EN" check measured an RTL
        # layout and an "AR" check waited forever for a toggle that flipped
        # Arabic back to English).
        if page.evaluate("() => AuraI18n.current") != lang:
            page.evaluate("(l) => AuraI18n.setLang(l)", lang)
            page.wait_for_function("(l) => AuraI18n.current === l", arg=lang, timeout=DEFAULT_WAIT_MS)
            page.wait_for_timeout(500)
        direction = page.evaluate("() => getComputedStyle(document.body).direction")
        assert direction == ("rtl" if lang == "ar" else "ltr"), (
            f"wanted {lang!r} but the page direction is {direction!r}"
        )
        yield page
    finally:
        ctx.close()


def main() -> int:
    shots = Path(tempfile.mkdtemp(prefix="aura_retail_p1a_shell_shots_"))
    print(f"Screenshots: {shots}")
    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_p1a_shell_appdata_"))
    backend = Backend(data_dir)
    company_id: str | None = None
    cashier_email = f"p1a-cashier-{uuid.uuid4().hex[:8]}@test.local"
    cashier_password = "P1aCashier123!"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            backend.start()

            # ---- signup as the owner (real first-run form) -----------------
            owner_context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            owner_page = owner_context.new_page()
            owner_page.goto(backend.base_url, wait_until="domcontentloaded")
            owner_page.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            owner_page.fill("#su-name", "P1a Owner")
            owner_page.fill("#su-company", "P1a Shell Co")
            owner_page.fill("#su-email", "p1a-owner@example.test")
            owner_page.fill("#su-pass", "P1aOwner123!")
            owner_page.fill("#su-pass2", "P1aOwner123!")
            owner_page.click("#su-btn")
            owner_page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            owner_page.reload(wait_until="domcontentloaded")
            owner_page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

            company_id = _owner_company_id(data_dir)
            assert company_id, "could not read the owner's company_id from registry.db"

            # ================================================================
            # 1 -- H3 (fix round): every owner group header is genuinely
            #      VISIBLE at 1366x768, for EVERY group open in turn, in EN
            #      and AR -- not merely "inside the page viewport
            #      mathematically" (the earlier version's whole blind spot:
            #      a header can sit at a Y coordinate under 768 while still
            #      being clipped by the SIDEBAR'S OWN scroll container,
            #      #sub-nav, whose own box is shorter than the page). Proved
            #      by elementFromPoint at each header's own centre, which
            #      must resolve to that header (or a descendant of it) --
            #      the one test that cannot be fooled by "the number looks
            #      right but the pixel is actually covered/clipped".
            # ================================================================
            def _check_all_headers_hit_for_every_open_group(lang_tag):
                keys = owner_page.evaluate(
                    "() => [...document.querySelectorAll('button.sub-nav-group-label')].map(b => b.dataset.groupToggle)"
                )
                assert len(keys) >= 5, f"only {len(keys)} group headers found ({lang_tag}) -- expected at least 5"
                vp = owner_page.viewport_size
                for k in keys:
                    human_click(owner_page, f'button[data-group-toggle="{k}"]', f"[{lang_tag}] group header {k}")
                    owner_page.wait_for_timeout(200)
                    result = owner_page.evaluate(
                        """() => {
                          const out = [];
                          for (const b of document.querySelectorAll('button.sub-nav-group-label')) {
                            const r = b.getBoundingClientRect();
                            const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                            const el = document.elementFromPoint(cx, cy);
                            out.push({
                              key: b.dataset.groupToggle,
                              top: r.top, bottom: r.bottom, left: r.left, right: r.right,
                              hit: !!(el && b.contains(el)),
                            });
                          }
                          return out;
                        }"""
                    )
                    for row in result:
                        assert row["top"] >= 0 and row["bottom"] <= vp["height"], (
                            f"[{lang_tag}, '{k}' open] header '{row['key']}' sits at y={row['top']:.0f}.."
                            f"{row['bottom']:.0f}, outside the {vp['height']}px viewport"
                        )
                        assert row["left"] >= 0 and row["right"] <= vp["width"], (
                            f"[{lang_tag}, '{k}' open] header '{row['key']}' sits at x={row['left']:.0f}.."
                            f"{row['right']:.0f}, outside the {vp['width']}px viewport"
                        )
                        assert row["hit"], (
                            f"[{lang_tag}, '{k}' open] header '{row['key']}' is mathematically inside the "
                            f"viewport but elementFromPoint at its own centre does NOT resolve inside it -- "
                            f"it is covered or clipped by something else (most likely #sub-nav's own scroll "
                            f"container, which can be shorter than the page even when the header's Y "
                            f"coordinate looks fine)"
                        )
                owner_page.screenshot(path=str(shots / f"01_owner_sidebar_1366x768_{lang_tag}.png"), full_page=False)

            def owner_headers_hit_test_en():
                owner_page.evaluate("() => SubsystemApp._navigate('dashboard')")
                owner_page.wait_for_timeout(300)
                _check_all_headers_hit_for_every_open_group("en")

            check("1366x768: every owner group header is genuinely visible (elementFromPoint) for every group open (EN)", owner_headers_hit_test_en)

            # ================================================================
            # 1b -- same, in Arabic
            # ================================================================
            def owner_headers_hit_test_ar():
                # try/finally (LOW, fix round 3): a failed Arabic assertion used
                # to leave this shared page in Arabic, so every later check in
                # the run measured Arabic while reporting English.
                owner_page.evaluate("() => AuraI18n.toggle()")
                owner_page.wait_for_timeout(400)
                try:
                    assert owner_page.evaluate("() => AuraI18n.current") == "ar", "the language toggle did not reach Arabic"
                    owner_page.evaluate("() => SubsystemApp._navigate('dashboard')")
                    owner_page.wait_for_timeout(300)
                    _check_all_headers_hit_for_every_open_group("ar")
                finally:
                    if owner_page.evaluate("() => AuraI18n.current") == "ar":
                        owner_page.evaluate("() => AuraI18n.toggle()")  # back to EN for later checks
                        owner_page.wait_for_timeout(400)

            check("1366x768: every owner group header is genuinely visible (elementFromPoint) for every group open (AR)", owner_headers_hit_test_ar)

            # ================================================================
            # 1c-bis -- the accordion is genuinely one-open-at-a-time: opening
            #      group B must hide group A's items, on the REAL rendered
            #      sidebar (mut.py's `one_open` mutation survived the old
            #      5-check suite here at 5/5 -- this is the check that closes
            #      that gap).
            # ================================================================
            def one_group_open_at_a_time():
                owner_page.evaluate("() => SubsystemApp._navigate('dashboard')")
                owner_page.wait_for_timeout(200)
                keys = owner_page.evaluate(
                    "() => [...document.querySelectorAll('button.sub-nav-group-label')].map(b => b.dataset.groupToggle)"
                )
                assert len(keys) >= 2, f"only {len(keys)} group headers found -- need at least 2 to prove exclusivity"
                key_a, key_b = keys[0], keys[1]

                human_click(owner_page, f'button[data-group-toggle="{key_a}"]', f"group header {key_a}")
                owner_page.wait_for_timeout(200)
                a_open_after_a = owner_page.evaluate(
                    "(k) => { const el = document.querySelector(`.sub-nav-group-items[data-group-items=\"${k}\"]`); "
                    "return el ? !el.hasAttribute('hidden') : null; }", key_a
                )
                assert a_open_after_a, f"group '{key_a}' did not open on its own click"

                human_click(owner_page, f'button[data-group-toggle="{key_b}"]', f"group header {key_b}")
                owner_page.wait_for_timeout(200)
                a_open_after_b, b_open_after_b = owner_page.evaluate(
                    "(ks) => { const [ka, kb] = ks; "
                    "const ea = document.querySelector(`.sub-nav-group-items[data-group-items=\"${ka}\"]`); "
                    "const eb = document.querySelector(`.sub-nav-group-items[data-group-items=\"${kb}\"]`); "
                    "return [ea ? !ea.hasAttribute('hidden') : null, eb ? !eb.hasAttribute('hidden') : null]; }",
                    [key_a, key_b],
                )
                assert b_open_after_b, f"group '{key_b}' did not open on its own click"
                assert not a_open_after_b, (
                    f"group '{key_a}' is STILL open after '{key_b}' was clicked -- the sidebar allows more "
                    f"than one group open at a time, which is exactly how Money's header sat past y=768 "
                    f"in the pre-fix measurement (every group's items stacked and pushed later ones down)"
                )

            check("only one sidebar group is open at a time", one_group_open_at_a_time)

            # ================================================================
            # M2 (fix round) -- keyboard menu nav on the REAL dropdown
            #      controller (_onDropdownKeydown): Escape closes and returns
            #      focus to the toggle button, ArrowDown/ArrowUp move between
            #      enabled items, and Tab off either end closes the menu.
            #      Exercised on both menus this controller drives (identity
            #      chip + settings gear) since both share the one handler.
            # ================================================================
            def _keyboard_nav_on_menu(btn_id, menu_id, label):
                owner_page.evaluate("() => SubsystemApp._navigate('dashboard')")
                owner_page.wait_for_timeout(150)
                btn = owner_page.locator(f"#{btn_id}")
                assert btn.count() == 1, f"[{label}] toggle button #{btn_id} not found"
                human_click(owner_page, f"#{btn_id}", f"{label} toggle")
                owner_page.wait_for_timeout(150)
                menu = owner_page.locator(f"#{menu_id}")
                assert menu.count() == 1 and menu.is_visible(), f"[{label}] menu #{menu_id} did not open on click"

                item_count = owner_page.evaluate(
                    "(id) => document.getElementById(id).querySelectorAll('.sub-identity-menu-item:not([disabled])').length",
                    menu_id,
                )
                assert item_count >= 2, f"[{label}] only {item_count} enabled item(s) -- need >=2 to prove ArrowDown/Up movement"

                # ArrowDown from nothing focused inside the menu -> first item;
                # a second ArrowDown moves to the SECOND item (proves movement,
                # not just "some item is focused").
                owner_page.keyboard.press("ArrowDown")
                owner_page.wait_for_timeout(80)
                first_focused = owner_page.evaluate(
                    "(id) => { const items = [...document.getElementById(id).querySelectorAll('.sub-identity-menu-item:not([disabled])')]; "
                    "return items.indexOf(document.activeElement); }",
                    menu_id,
                )
                owner_page.keyboard.press("ArrowDown")
                owner_page.wait_for_timeout(80)
                second_focused = owner_page.evaluate(
                    "(id) => { const items = [...document.getElementById(id).querySelectorAll('.sub-identity-menu-item:not([disabled])')]; "
                    "return items.indexOf(document.activeElement); }",
                    menu_id,
                )
                assert first_focused >= 0, f"[{label}] ArrowDown did not focus any menu item"
                assert second_focused == (first_focused + 1) % item_count, (
                    f"[{label}] a second ArrowDown moved focus from item {first_focused} to {second_focused}, "
                    f"expected {(first_focused + 1) % item_count} (the next item, wrapping)"
                )

                owner_page.keyboard.press("ArrowUp")
                owner_page.wait_for_timeout(80)
                back_to_first = owner_page.evaluate(
                    "(id) => { const items = [...document.getElementById(id).querySelectorAll('.sub-identity-menu-item:not([disabled])')]; "
                    "return items.indexOf(document.activeElement); }",
                    menu_id,
                )
                assert back_to_first == first_focused, f"[{label}] ArrowUp did not move focus back to item {first_focused}"

                # Escape closes the menu and returns focus to the toggle button.
                owner_page.keyboard.press("Escape")
                owner_page.wait_for_timeout(150)
                assert menu.is_hidden(), f"[{label}] Escape did not close the menu"
                focused_is_toggle = owner_page.evaluate("(id) => document.activeElement && document.activeElement.id === id", btn_id)
                assert focused_is_toggle, f"[{label}] Escape closed the menu but did not return focus to #{btn_id}"

                # Tab off the last item closes the menu too.
                human_click(owner_page, f"#{btn_id}", f"{label} toggle (reopen)")
                owner_page.wait_for_timeout(150)
                assert menu.is_visible(), f"[{label}] menu did not reopen for the Tab-out check"
                for _ in range(item_count):
                    owner_page.keyboard.press("ArrowDown")
                    owner_page.wait_for_timeout(50)
                owner_page.keyboard.press("Tab")
                owner_page.wait_for_timeout(150)
                assert menu.is_hidden(), f"[{label}] Tab off the last item did not close the menu"

            check("keyboard nav (Escape/ArrowUp/ArrowDown/Tab-out) on the identity chip menu",
                  lambda: _keyboard_nav_on_menu("sub-identity-chip-btn", "sub-identity-menu", "identity"))
            check("keyboard nav (Escape/ArrowUp/ArrowDown/Tab-out) on the settings gear menu",
                  lambda: _keyboard_nav_on_menu("sub-settings-gear-btn", "sub-settings-menu", "settings gear"))

            # ================================================================
            # M3 (fix round) -- till rail items carry a non-empty aria-label
            #      AND title matching their translated nav label, proved on
            #      the real rendered DOM rather than trusting the source.
            # ================================================================
            def rail_items_carry_aria_label_and_title():
                owner_page.evaluate("() => SubsystemApp._navigate('dashboard')")
                owner_page.wait_for_timeout(200)
                rows = owner_page.evaluate(
                    """() => [...document.querySelectorAll('.sub-nav-item')].map(el => ({
                        section: el.dataset.section || null,
                        text: (el.textContent || '').trim(),
                        ariaLabel: el.getAttribute('aria-label'),
                        title: el.getAttribute('title'),
                    }))"""
                )
                assert len(rows) >= 3, f"only {len(rows)} .sub-nav-item rows found -- expected the full rail"
                bad = [
                    r for r in rows
                    if not r["ariaLabel"] or not r["ariaLabel"].strip() or not r["title"] or not r["title"].strip()
                ]
                assert not bad, (
                    f"{len(bad)} nav rail item(s) are missing a non-empty aria-label/title: "
                    f"{[r['section'] or r['text'] for r in bad]}"
                )
                mismatched = [r for r in rows if r["ariaLabel"] != r["title"]]
                assert not mismatched, (
                    f"{len(mismatched)} nav rail item(s) have aria-label != title (should name the same "
                    f"thing): {mismatched}"
                )

            check("every till rail item has a non-empty, matching aria-label and title", rail_items_carry_aria_label_and_title)

            # ================================================================
            # M10 (fix round) -- genuinely reproduce the no-admin-device-
            #      claimed state and prove the gear-menu claim entry actually
            #      works, rather than reporting "could not reproduce" the way
            #      the verifier's own probe did. _autoClaimAdminOnSignup()
            #      (app-shell.js) fires a silent POST /me/claim-admin the
            #      instant this test's own owner account was created, so by
            #      this point in the run the signing-up device already holds
            #      is_admin_device -- exactly why the verifier's UI-only
            #      repro also reported canClaim: false. The state IS real
            #      (device_registry.py's schema has no invariant preventing
            #      it -- a wiped/reset admin device is exactly this row),
            #      it just is not reachable through this app's own UI, so it
            #      is constructed directly in registry.db, the same
            #      established technique _seed_cashier below already uses.
            # ================================================================
            def claim_admin_device_entry_appears_and_works_when_unclaimed():
                flipped = _clear_admin_device(data_dir, company_id)
                assert flipped >= 1, (
                    "the direct UPDATE against devices.is_admin_device matched 0 rows -- either this "
                    "company never had an admin device (the auto-claim-on-signup regressed) or the "
                    "repro setup itself is wrong; either way, this check cannot claim to have "
                    "reproduced anything without first confirming it broke the invariant it means to test"
                )

                # device_context.py's own resolve_local_device() docstring: the
                # resolved row is cached in a MODULE-LEVEL variable for the
                # life of the backend PROCESS, keyed only by company_id -- so
                # a raw DB flip (the only way to reach this state at all,
                # since _autoClaimAdminOnSignup fires before this test ever
                # gets a turn) is invisible to a running process until that
                # cache is gone. This is almost certainly why the verifier's
                # own probe also reported canClaim: false rather than proving
                # a false negative in the probe itself. Restarting the SAME
                # backend against the SAME AURA_APP_DATA (same secret key --
                # config.py's get_or_create_secret_key persists it to disk,
                # so the owner's session cookie is still valid after this)
                # gives it a fresh process and therefore a fresh cache,
                # without needing to sign in again.
                backend.stop()
                backend.start()

                owner_page.reload(wait_until="domcontentloaded")
                owner_page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                owner_page.evaluate("() => SubsystemApp._navigate('dashboard')")
                owner_page.wait_for_timeout(400)
                can_claim_now = owner_page.evaluate("() => SubsystemApp.canClaimAdminDevice")
                assert can_claim_now is True, (
                    f"after clearing is_admin_device for this company, a re-check reported "
                    f"canClaimAdminDevice={can_claim_now!r}, expected true -- the reproduction did not "
                    f"take, or GET /api/devices/me is not re-deriving can_claim_admin from the cleared row"
                )

                human_click(owner_page, "#sub-settings-gear-btn", "settings gear")
                owner_page.wait_for_timeout(200)
                claim_row = owner_page.locator("#sub-settings-menu .sub-identity-menu-item", has_text="Make this the main computer")
                assert claim_row.count() == 1, (
                    "canClaimAdminDevice is true but the 'Make this the main computer' row is not in the "
                    "settings gear menu"
                )

                human_click(owner_page, "#sub-settings-menu .sub-identity-menu-item", "claim row",
                            text="Make this the main computer")
                owner_page.wait_for_timeout(500)
                is_admin_now = owner_page.evaluate("() => SubsystemApp.isAdminDevice")
                can_claim_after = owner_page.evaluate("() => SubsystemApp.canClaimAdminDevice")
                assert is_admin_now is True, (
                    f"clicking 'Make this the main computer' did not result in isAdminDevice=true "
                    f"(got {is_admin_now!r}) -- the real POST /me/claim-admin did not succeed"
                )
                assert can_claim_after is False, (
                    "after a successful claim, canClaimAdminDevice is still true -- the claim row would "
                    "stay offered forever"
                )
                owner_page.screenshot(path=str(shots / "05_owner_claims_admin_device.png"), full_page=False)

            check("no-admin-device state reproduced directly, and 'Make this the main computer' genuinely claims it",
                  claim_admin_device_entry_appears_and_works_when_unclaimed)

            # ---- seed the cashier now the backend is idle between scenarios ----
            _seed_cashier(data_dir, company_id, cashier_email, cashier_password)

            cashier_context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            cashier_page = cashier_context.new_page()
            cashier_page.goto(backend.base_url, wait_until="domcontentloaded")
            login_ok = cashier_page.evaluate(
                """(creds) => fetch('/api/auth/login', {
                    method: 'POST', credentials: 'include',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(creds),
                }).then(r => r.json())""",
                {"email": cashier_email, "password": cashier_password},
            )
            assert login_ok.get("success") or login_ok.get("status") == "success" or "user" in login_ok, (
                f"real POST /api/auth/login for the seeded cashier did not report success: {login_ok!r}"
            )
            cashier_page.goto(backend.base_url, wait_until="domcontentloaded")
            cashier_page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

            # ================================================================
            # 1c -- home routing by capability: the cashier lands on the till,
            #       the owner lands on the dashboard, on SHELL START (DOM id
            #       asserted per role, not read from source)
            # ================================================================
            def home_routing_by_capability():
                cashier_section = cashier_page.evaluate("() => SubsystemApp.currentSection")
                assert cashier_section == "pos", (
                    f"the sell-only cashier landed on {cashier_section!r} at shell start, expected "
                    f"'pos' (the till) -- P1a item 2's home routing"
                )
                assert cashier_page.locator("#pos-cart, .pos-grid, #ret-pos-root").count() >= 1 or \
                    cashier_page.locator("[id^='pos-'], [class^='pos-']").count() >= 1, (
                    "SubsystemApp.currentSection reports 'pos' but no recognisable till DOM landed"
                )
                owner_section = owner_page.evaluate("() => SubsystemApp.currentSection")
                assert owner_section == "dashboard", (
                    f"Sanity, other direction: the owner's own shell-start section is {owner_section!r}, "
                    f"expected 'dashboard'"
                )
                cashier_page.screenshot(path=str(shots / "00_cashier_lands_on_till.png"), full_page=False)

            check("home routing: cashier lands on the till, owner on the dashboard (both directions)", home_routing_by_capability)

            # ================================================================
            # 1d -- M1 (fix round): the two bugs the verifier's mut.py caught
            #      survived-mutation, proved on the REAL owner page against
            #      the REAL 4 status endpoints (not a JS-sandbox stand-in):
            #        (a) 3 re-logins (SubsystemApp.init() called again in the
            #            SAME document -- exactly what the relogin modal
            #            does, per _ensureConnectedServicesFocusListener's own
            #            comment) then ONE focus event must fire exactly 4
            #            requests (one per endpoint, one listener), not 16
            #            (4 endpoints x 4 stacked listeners).
            #        (b) after logout() (_connectedPollActive flips false),
            #            a focus event must fire ZERO status requests.
            # ================================================================
            STATUS_ENDPOINTS = (
                "/api/notifications/whatsapp/status",
                "/api/notifications/status",
                "/api/einvoicing/status",
                "/api/site-relay/status",
            )
            status_requests: list[str] = []

            def _track_status_request(request):
                url = request.url
                if any(ep in url for ep in STATUS_ENDPOINTS):
                    status_requests.append(url)

            owner_page.on("request", _track_status_request)

            def no_poll_stacking_across_relogins_and_none_after_logout():
                # (a) three re-logins in the same document (this is what
                # SubsystemApp.init() being called again IS -- see the
                # relogin-modal comment above _ensureConnectedServicesFocusListener).
                for _ in range(3):
                    owner_page.evaluate("() => SubsystemApp.init()")
                    owner_page.wait_for_timeout(300)  # let that call's own immediate poll land and drain
                status_requests.clear()
                owner_page.evaluate("() => window.dispatchEvent(new Event('focus'))")
                owner_page.wait_for_timeout(500)
                got = len(status_requests)
                assert got == 4, (
                    f"one focus event after 3 re-logins fired {got} status request(s), expected exactly 4 "
                    f"(one per endpoint, one listener) -- {status_requests!r} -- a count of 16 would mean the "
                    f"focus listener re-stacked on every SubsystemApp.init() call"
                )

                # (b) after logout, a focus event polls nothing at all.
                owner_page.evaluate("() => SubsystemApp.logout()")
                owner_page.wait_for_timeout(300)
                status_requests.clear()
                owner_page.evaluate("() => window.dispatchEvent(new Event('focus'))")
                owner_page.wait_for_timeout(500)
                got_after_logout = len(status_requests)
                assert got_after_logout == 0, (
                    f"a focus event after logout() fired {got_after_logout} status request(s), expected 0 "
                    f"-- {status_requests!r} -- logout() must flip _connectedPollActive false"
                )

            check("no poll-listener stacking: 3 relogins + 1 focus = exactly 4 requests; 1 focus after logout = 0", no_poll_stacking_across_relogins_and_none_after_logout)
            owner_page.remove_listener("request", _track_status_request)

            # ================================================================
            # FIX ROUND 3 -- everything below is measured like a person: every
            # "can they reach it" question goes through human_click /
            # assert_reachable (box inside the viewport, elementFromPoint hits
            # it, raw mouse click), each check in a brand-new signed-in context
            # (session()) so a failure cannot leak a language or a stub into
            # the next check.
            # ================================================================
            BASE = backend.base_url
            DESKTOP_SIZES = [(1366, 768), (1280, 720), (1024, 600)]

            # ---- CRITICAL 1: the settings gear menu opens fully on screen ----
            def gear_menu_is_fully_on_screen(width, height, lang, coarse=False):
                with session(browser, BASE, {"width": width, "height": height}, lang=lang, coarse=coarse) as pg:
                    tag = f"{width}x{height} {lang}{' coarse' if coarse else ''}"
                    gear = assert_reachable(pg, "#sub-settings-gear-btn", f"[{tag}] settings gear")
                    human_click(pg, "#sub-settings-gear-btn", f"[{tag}] settings gear")
                    pg.wait_for_timeout(200)
                    assert pg.locator("#sub-settings-menu").is_visible(), f"[{tag}] the gear menu did not open"
                    menu = assert_reachable(pg, "#sub-settings-menu", f"[{tag}] settings menu (whole box)")
                    assert menu["bottom"] <= gear["top"] + 1, (
                        f"[{tag}] the menu (y {menu['top']:.0f}..{menu['bottom']:.0f}) is not ABOVE the gear "
                        f"(top y={gear['top']:.0f}) -- it must open upward from the sidebar footer"
                    )
                    if lang == "ar":
                        assert abs(menu["right"] - gear["right"]) <= 3, (
                            f"[{tag}] in Arabic the menu must hang from the gear's right (inline-start) edge: "
                            f"menu right={menu['right']:.0f}, gear right={gear['right']:.0f}"
                        )
                    else:
                        assert abs(menu["left"] - gear["left"]) <= 3, (
                            f"[{tag}] in English the menu must hang from the gear's left (inline-start) edge: "
                            f"menu left={menu['left']:.0f}, gear left={gear['left']:.0f}"
                        )
                    rows = pg.evaluate("() => document.querySelectorAll('#sub-settings-menu .sub-identity-menu-item').length")
                    assert rows >= 3, f"[{tag}] only {rows} row(s) in the gear menu"
                    for i in range(rows):
                        assert_reachable(pg, "#sub-settings-menu .sub-identity-menu-item", f"[{tag}] gear menu row {i}", nth=i)
                    # A real click on a real row lands on its screen.
                    human_click(pg, '#sub-settings-menu .sub-identity-menu-item[onclick*="backup-export"]',
                                f"[{tag}] Backup & Export row")
                    pg.wait_for_timeout(300)
                    section = pg.evaluate("() => SubsystemApp.currentSection")
                    assert section == "backup-export", f"[{tag}] clicking the Backup & Export row landed on {section!r}"
                    assert pg.locator("#sub-settings-menu").is_hidden(), f"[{tag}] the menu stayed open after a row was chosen"
                    if (width, height) == (1366, 768):
                        pg.screenshot(path=str(shots / f"10_gear_menu_open_{lang}{'_coarse' if coarse else ''}.png"), full_page=False)

            for (w, h) in DESKTOP_SIZES:
                for lg in ("en", "ar"):
                    check(f"gear menu opens fully on screen, upward, anchored to the inline-start edge ({w}x{h} {lg})",
                          lambda w=w, h=h, lg=lg: gear_menu_is_fully_on_screen(w, h, lg))

            # ---- LOW: only one dropdown open at a time (both directions) ----
            def one_dropdown_at_a_time():
                with session(browser, BASE, VIEWPORT) as pg:
                    def open_state():
                        return pg.evaluate(
                            "() => [!document.getElementById('sub-identity-menu').hidden, "
                            "!document.getElementById('sub-settings-menu').hidden]"
                        )
                    human_click(pg, "#sub-identity-chip-btn", "identity chip")
                    pg.wait_for_timeout(150)
                    assert open_state() == [True, False], f"identity chip click: identity/settings open = {open_state()}"
                    human_click(pg, "#sub-settings-gear-btn", "settings gear")
                    pg.wait_for_timeout(150)
                    assert open_state() == [False, True], (
                        f"opening the gear menu must close the identity menu: identity/settings open = {open_state()}"
                    )
                    human_click(pg, "#sub-identity-chip-btn", "identity chip (second)")
                    pg.wait_for_timeout(150)
                    assert open_state() == [True, False], (
                        f"opening the identity menu must close the gear menu: identity/settings open = {open_state()}"
                    )

            check("only one header/footer dropdown is open at a time (both directions, real clicks)", one_dropdown_at_a_time)

            # ---- HIGH 2: the brand logo and the phone Home tab, per role ----
            def logo_routes_by_role():
                cashier_page.evaluate("() => SubsystemApp._navigate('products')")
                cashier_page.wait_for_timeout(250)
                assert cashier_page.evaluate("() => SubsystemApp.currentSection") == "products"
                human_click(cashier_page, ".sub-sidebar-brand", "brand logo (cashier)")
                cashier_page.wait_for_timeout(400)
                got = cashier_page.evaluate("() => SubsystemApp.currentSection")
                assert got == "pos", (
                    f"a sell-only cashier clicking the brand logo landed on {got!r}, not the till ('pos') -- "
                    f"the dashboard would answer them with a refusal sentence"
                )
                with session(browser, BASE, VIEWPORT) as pg:
                    pg.evaluate("() => SubsystemApp._navigate('products')")
                    pg.wait_for_timeout(250)
                    human_click(pg, ".sub-sidebar-brand", "brand logo (owner)")
                    pg.wait_for_timeout(400)
                    got_owner = pg.evaluate("() => SubsystemApp.currentSection")
                    assert got_owner == "dashboard", f"the owner's brand logo landed on {got_owner!r}, expected 'dashboard'"

            check("brand logo: cashier -> till, owner -> dashboard (real clicks, both directions)", logo_routes_by_role)

            PHONE = {"width": 390, "height": 844}

            def phone_tabs_and_more_sheet_by_role():
                with session(browser, BASE, PHONE, coarse=True, email=cashier_email, password=cashier_password) as pg:
                    tabs = pg.evaluate("() => [...document.querySelectorAll('.sub-tab[data-tab]')].map(e => e.dataset.tab)")
                    assert "dashboard" not in tabs, f"a cashier's phone tab bar still has a Home/dashboard tab: {tabs}"
                    assert tabs.count("pos") == 1, f"a cashier's phone tab bar must have the Till tab exactly once: {tabs}"
                    human_click(pg, '.sub-tab[data-tab="products"]', "Stock tab (cashier)")
                    pg.wait_for_timeout(300)
                    human_click(pg, '.sub-tab[data-tab="pos"]', "Till tab (cashier)")
                    pg.wait_for_timeout(300)
                    assert pg.evaluate("() => SubsystemApp.currentSection") == "pos", "the cashier's Till tab did not open the till"
                    human_click(pg, "#sub-tab-more", "More tab (cashier)")
                    pg.locator(".sub-more-sheet").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                    labels = pg.evaluate("() => [...document.querySelectorAll('.sub-more-row')].map(e => e.textContent.trim())")
                    assert labels, "the cashier's More sheet has no rows at all"
                    assert not [x for x in labels if "licen" in x.lower()], (
                        f"a cashier's More sheet offers a License entry (the desktop gear does not): {labels}"
                    )
                    assert any("Log Out" in x for x in labels), f"the cashier's More sheet lost Log Out: {labels}"
                with session(browser, BASE, PHONE, coarse=True) as pg:
                    tabs = pg.evaluate("() => [...document.querySelectorAll('.sub-tab[data-tab]')].map(e => e.dataset.tab)")
                    assert tabs.count("dashboard") == 1, f"the owner's phone bar must keep Home: {tabs}"
                    human_click(pg, '.sub-tab[data-tab="products"]', "Stock tab (owner)")
                    pg.wait_for_timeout(300)
                    human_click(pg, '.sub-tab[data-tab="dashboard"]', "Home tab (owner)")
                    pg.wait_for_timeout(300)
                    assert pg.evaluate("() => SubsystemApp.currentSection") == "dashboard", "the owner's Home tab did not open the dashboard"
                    human_click(pg, "#sub-tab-more", "More tab (owner)")
                    pg.locator(".sub-more-sheet").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                    labels = pg.evaluate("() => [...document.querySelectorAll('.sub-more-row')].map(e => e.textContent.trim())")
                    assert [x for x in labels if "licen" in x.lower()], f"the owner's More sheet lost its License entry: {labels}"

            check("phone (390x844, touch): cashier has Till not Home and no License; owner keeps Home and License", phone_tabs_and_more_sheet_by_role)

            # ---- HIGH 3: a collapsed Connected services header still paints the status ----
            FAILING_STUB = {
                "**/api/notifications/whatsapp/status": {
                    "success": True,
                    "data": {"enabled": True, "whatsapp_configured": True, "counts_by_state": {"FAILED_PERMANENT": 2}},
                }
            }

            def collapsed_header_paints_failing(lang):
                with session(browser, BASE, VIEWPORT, lang=lang, stubs=FAILING_STUB) as pg:
                    pg.wait_for_function("() => SubsystemApp._connectedPolled === true", timeout=DEFAULT_WAIT_MS)
                    pg.wait_for_timeout(300)
                    hdr = '[data-group-toggle="connected"]'
                    expanded = pg.evaluate("(s) => document.querySelector(s).getAttribute('aria-expanded')", hdr)
                    assert expanded == "false", f"[{lang}] precondition: the Connected services group must be COLLAPSED, aria-expanded={expanded!r}"
                    word = probe(pg, hdr + " .sub-nav-status-word")
                    assert word["found"], f"[{lang}] the collapsed header has no status word element at all"
                    assert word["width"] > 0, f"[{lang}] the collapsed header's status word is {word['width']}px wide -- nothing is painted"
                    assert_reachable(pg, hdr + " .sub-nav-status-word", f"[{lang}] collapsed header status word")
                    assert_reachable(pg, hdr + " .status-dot", f"[{lang}] collapsed header status dot")
                    wanted = pg.evaluate("() => window.t('Failing')")
                    painted = pg.evaluate("(s) => document.querySelector(s + ' .sub-nav-status-word').textContent.trim()", hdr)
                    assert painted == wanted, f"[{lang}] the collapsed header reads {painted!r}, expected {wanted!r} (Failing)"
                    dot_cls = pg.evaluate("(s) => document.querySelector(s + ' .status-dot').className", hdr)
                    assert "is-failing" in dot_cls, f"[{lang}] the header dot class is {dot_cls!r}, expected is-failing"
                    chev = probe(pg, hdr + " .sub-nav-group-chevron")
                    hb = probe(pg, hdr)
                    assert chev["left"] >= hb["left"] - 0.5 and chev["right"] <= hb["right"] + 0.5, (
                        f"[{lang}] the chevron (x {chev['left']:.0f}..{chev['right']:.0f}) sits outside its header "
                        f"(x {hb['left']:.0f}..{hb['right']:.0f})"
                    )
                    # After a poll, no row and no header may be left with an empty word
                    # (the _repaintConnectedServiceDots empty-word mutation).
                    words = pg.evaluate("() => [...document.querySelectorAll('.sub-nav-status-word')].map(e => e.textContent.trim())")
                    assert len(words) >= 5 and all(words), f"[{lang}] after a poll some status words are empty: {words}"
                    pg.screenshot(path=str(shots / f"11_collapsed_connected_header_{lang}.png"), full_page=False)

            for lg in ("en", "ar"):
                check(f"collapsed Connected services header paints Failing (word width > 0, reachable) ({lg})",
                      lambda lg=lg: collapsed_header_paints_failing(lg))

            # ---- HIGH 4: navigation never polls ----
            def navigation_never_polls():
                with session(browser, BASE, VIEWPORT) as pg:
                    pg.wait_for_function("() => SubsystemApp._connectedPolled === true", timeout=DEFAULT_WAIT_MS)
                    pg.wait_for_timeout(500)
                    seen: list[str] = []
                    pg.on("request", lambda r: seen.append(r.url) if any(ep in r.url for ep in STATUS_ENDPOINTS) else None)
                    sections = pg.evaluate(
                        """() => {
                          const ext = new Set(['whatsapp', 'einvoicing', 'email-notifications', 'site-relay']);
                          const out = [];
                          for (const el of document.querySelectorAll('.sub-nav-item[data-section]')) {
                            const s = el.dataset.section;
                            if (!ext.has(s) && s !== 'admin-center' && !out.includes(s)) out.push(s);
                          }
                          return out;
                        }"""
                    )[:7]
                    assert len(sections) == 7, f"needed 7 in-shell sections to navigate, found {sections}"
                    for s in sections:
                        pg.evaluate("(id) => SubsystemApp._navigate(id)", s)
                        pg.wait_for_timeout(250)
                    assert seen == [], f"7 navigations fired {len(seen)} status request(s): {seen}"
                    # Other direction: the poll still works when it is meant to (window focus).
                    pg.evaluate("() => window.dispatchEvent(new Event('focus'))")
                    pg.wait_for_timeout(500)
                    assert len(seen) == 4, f"a focus event must poll exactly the 4 endpoints, saw {len(seen)}: {seen}"

            check("navigating 7 sections fires zero status requests; a focus event fires exactly 4", navigation_never_polls)

            # ---- HIGH 4 / M5: coarse pointer -- 44px targets, every header reachable, gear menu on screen ----
            def coarse_targets_and_reach(lang):
                with session(browser, BASE, VIEWPORT, lang=lang, coarse=True) as pg:
                    keys = pg.evaluate("() => [...document.querySelectorAll('button.sub-nav-group-label')].map(b => b.dataset.groupToggle)")
                    assert len(keys) >= 5, f"[coarse {lang}] only {len(keys)} group headers"
                    for k in keys:
                        human_click(pg, f'button[data-group-toggle="{k}"]', f"[coarse {lang}] header {k}")
                        pg.wait_for_timeout(200)
                        for j in keys:
                            assert_reachable(pg, f'button[data-group-toggle="{j}"]', f"[coarse {lang}, '{k}' open] header {j}")
                        heights = pg.evaluate(
                            "(k) => [...document.querySelectorAll(`.sub-nav-group-items[data-group-items=\"${k}\"] .sub-nav-item`)]"
                            ".map(e => e.getBoundingClientRect().height)", k)
                        assert heights and min(heights) >= 43.5, (
                            f"[coarse {lang}, '{k}' open] a nav row is {min(heights) if heights else None}px tall, "
                            f"a touch target must be >= 44px: {heights}"
                        )
                        hb = pg.evaluate(
                            "(k) => { const b = document.querySelector(`button[data-group-toggle=\"${k}\"]`).getBoundingClientRect(); return b.height; }", k)
                        slack = pg.evaluate(
                            "() => { const n = document.getElementById('sub-nav'); const cs = getComputedStyle(n);"
                            " const used = [...n.children].reduce((a, c) => { const m = getComputedStyle(c);"
                            " return a + c.getBoundingClientRect().height + parseFloat(m.marginTop) + parseFloat(m.marginBottom); }, 0)"
                            " + parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom) + parseFloat(cs.rowGap || 0) * (n.children.length - 1);"
                            " return Math.round(n.clientHeight - used); }")
                        assert hb >= 31.5, (
                            f"[coarse {lang}, '{k}' open] group header {k} is {hb:.1f}px tall; under a coarse pointer it "
                            f"must be >= 32px (the 44px row floor does not fit 7 headers in the 768px rail: slack was 102px)"
                        )
                        assert slack >= 0, f"[coarse {lang}, '{k}' open] the rail overflows by {-slack}px, so a header can be clipped"
                        print(f"       (report only) [coarse {lang}, '{k}' open] header {k} is {hb:.1f}px tall, nav rows min {min(heights):.1f}px, rail slack {slack}px")
                    assert_reachable(pg, "#sub-settings-gear-btn", f"[coarse {lang}] settings gear")
                    if pg.locator("#sub-ai-sidebar-btn").count():
                        assert_reachable(pg, "#sub-ai-sidebar-btn", f"[coarse {lang}] AI button")
                    pg.screenshot(path=str(shots / f"12_coarse_sidebar_{lang}.png"), full_page=False)

            for lg in ("en", "ar"):
                check(f"touch screen 1366x768: nav rows >= 44px, every group header reachable with each group open ({lg})",
                      lambda lg=lg: coarse_targets_and_reach(lg))
                check(f"touch screen 1366x768: gear menu opens fully on screen ({lg})",
                      lambda lg=lg: gear_menu_is_fully_on_screen(1366, 768, lg, coarse=True))

            # ---- M5: with a MOUSE the rows are the 38px the height budget assumes (not 40) ----
            def mouse_rows_are_38px(lang):
                with session(browser, BASE, VIEWPORT, lang=lang) as pg:
                    keys = pg.evaluate("() => [...document.querySelectorAll('button.sub-nav-group-label')].map(b => b.dataset.groupToggle)")
                    for k in keys:
                        pg.evaluate("(k) => SubsystemApp._toggleSidebarGroup(k)", k)
                        pg.wait_for_timeout(120)
                        heights = pg.evaluate(
                            "(k) => [...document.querySelectorAll(`.sub-nav-group-items[data-group-items=\"${k}\"] .sub-nav-item`)]"
                            ".map(e => e.getBoundingClientRect().height)", k)
                        assert heights, f"[{lang}] group {k!r} has no rows"
                        assert all(37.5 <= x <= 38.5 for x in heights), (
                            f"[{lang}, '{k}' open] mouse rows must be 38px, measured {sorted(set(round(x, 1) for x in heights))}"
                        )

            for lg in ("en", "ar"):
                check(f"mouse 1366x768: every sidebar row is 38px tall in every group ({lg})",
                      lambda lg=lg: mouse_rows_are_38px(lg))

            # ---- HIGH 4: the rail scrolls the active item into view (it STARTS out of view) ----
            def rail_scrolls_active_item_into_view():
                with session(browser, BASE, {"width": 1024, "height": 420}) as pg:
                    target = pg.evaluate(
                        """() => {
                          let best = null;
                          for (const g of document.querySelectorAll('.sub-nav-group-items')) {
                            const items = [...g.querySelectorAll('.sub-nav-item')];
                            if (!best || items.length > best.items.length) best = { key: g.dataset.groupItems, items };
                          }
                          if (!best) return null;
                          return { key: best.key, section: best.items[best.items.length - 1].dataset.section };
                        }"""
                    )
                    assert target, "no group with items"
                    pg.evaluate("(k) => SubsystemApp._toggleSidebarGroup(k)", target["key"])
                    pg.evaluate("() => { const r = document.getElementById('sub-nav'); r.scrollTop = 0; }")
                    pg.wait_for_timeout(200)
                    sel = f'.sub-nav-item[data-section="{target["section"]}"]'
                    measure = """(sel) => {
                      const rail = document.getElementById('sub-nav').getBoundingClientRect();
                      const el = document.querySelector(sel).getBoundingClientRect();
                      const nav = document.getElementById('sub-nav');
                      return { railTop: rail.top, railBottom: rail.bottom, top: el.top, bottom: el.bottom,
                               scrollTop: nav.scrollTop, overflow: nav.scrollHeight - nav.clientHeight };
                    }"""
                    before = pg.evaluate(measure, sel)
                    assert before["overflow"] > 0, f"precondition: the rail does not overflow at 1024x420 ({before})"
                    assert before["bottom"] > before["railBottom"] + 1, (
                        f"precondition: the target item must START out of view (item bottom {before['bottom']:.0f}, "
                        f"rail bottom {before['railBottom']:.0f}) or this proves nothing"
                    )
                    pg.evaluate("(id) => SubsystemApp._navigate(id)", target["section"])
                    pg.wait_for_timeout(400)
                    after = pg.evaluate(measure, sel)
                    assert after["top"] >= after["railTop"] - 1 and after["bottom"] <= after["railBottom"] + 1, (
                        f"after navigating, the active item (y {after['top']:.0f}..{after['bottom']:.0f}) is still outside "
                        f"the rail (y {after['railTop']:.0f}..{after['railBottom']:.0f}) -- the rail did not scroll to it"
                    )
                    assert after["scrollTop"] > before["scrollTop"], "the rail did not scroll at all"
                    assert_reachable(pg, sel, "active rail item after navigation")

            check("short viewport (1024x420): an active item that starts out of view is scrolled into view", rail_scrolls_active_item_into_view)

            # ================================================================
            # 2 -- a cashier sees no gear, no Categories, no Suppliers, no
            #      Purchase Orders anywhere in the rendered shell
            # ================================================================
            def cashier_sees_no_admin_or_stock_write_screens():
                html = cashier_page.content()
                assert 'sub-settings-gear' not in html, "the Settings gear is present for a cashier"
                assert 'data-section="admin-center"' not in html, "an admin-center nav anchor is present for a cashier"
                for section_id, label in [("categories", "Categories"), ("suppliers", "Suppliers"), ("purchases", "Purchase Orders")]:
                    loc = cashier_page.locator(f'.sub-nav-item[data-section="{section_id}"]')
                    assert loc.count() == 0, f"'{label}' ({section_id}) is reachable from the sidebar for a cashier"
                # Sanity, both directions: Products (ungated) must still be there.
                assert cashier_page.locator('.sub-nav-item[data-section="products"]').count() == 1, (
                    "Sanity: 'products' must still be reachable for a cashier, or this test would "
                    "pass on a nav that renders nothing"
                )
                cashier_page.screenshot(path=str(shots / "03_cashier_sidebar_no_gear.png"), full_page=False)

            check("cashier sees no Settings gear, Categories, Suppliers or Purchase Orders", cashier_sees_no_admin_or_stock_write_screens)

            # ================================================================
            # 3 -- #admin-center by URL as a cashier renders no Save button
            # ================================================================
            def admin_center_deep_link_has_no_save():
                cashier_page.evaluate("() => SubsystemApp._navigate('admin-center')")
                cashier_page.wait_for_timeout(600)  # let the section render
                save_buttons = cashier_page.locator("button:has-text('Save')")
                assert save_buttons.count() == 0, (
                    f"{save_buttons.count()} button(s) reading 'Save' rendered on #admin-center for a "
                    f"cashier -- a Save the server will 403 anyway must not be offered"
                )
                # Reported, not asserted on: whether a lock-panel-shaped element
                # is present is crew T's build (_renderAdminCenter's first
                # line). Per the contract, this file reports the fact rather
                # than failing the run or editing subsystem-retail.js.
                lock_like = (
                    cashier_page.locator("[id*='lock' i]").count()
                    + cashier_page.locator("[class*='lock' i]").count()
                    + cashier_page.get_by_text(re.compile("owner only|store owner", re.I)).count()
                )
                print(f"       (report only, not asserted: {lock_like} lock-panel-shaped element(s) "
                      f"found on #admin-center for a cashier -- crew T's build)")
                cashier_page.screenshot(path=str(shots / "04_cashier_admin_center_no_save.png"), full_page=False)

            check("#admin-center by URL as a cashier renders no Save button", admin_center_deep_link_has_no_save)

            # ================================================================
            # H2 (fix round) -- on a REAL phone viewport (390x844), tapping
            #      WhatsApp / E-Invoicing / Shop network in the phone's More
            #      sheet must land on the real standalone page (the SAME
            #      externalPage the desktop sidebar already browser-navigates
            #      to), not "Coming soon." -- the exact regression H2's own
            #      comment in app-shell.js describes (_openMoreSheet used to
            #      call _navigate(item.id) unconditionally, which fell
            #      through to the default "Coming soon." case for exactly
            #      these three ownerOnly externalPage entries).
            # ================================================================
            PHONE_VIEWPORT = {"width": 390, "height": 844}
            MORE_SHEET_EXTERNAL_PAGES = [
                ("WhatsApp", "whatsapp.html", "WhatsApp"),
                ("E-Invoicing", "einvoicing.html", "E-Invoicing"),
                ("Shop network", "site-relay.html", "Shop Network"),
            ]

            def more_sheet_external_pages_open_the_real_page():
                for label, url_suffix, title_substr in MORE_SHEET_EXTERNAL_PAGES:
                    phone_context = browser.new_context(viewport=PHONE_VIEWPORT, reduced_motion="reduce")
                    phone_page = phone_context.new_page()
                    try:
                        phone_page.goto(backend.base_url, wait_until="domcontentloaded")
                        login_ok = phone_page.evaluate(
                            """(creds) => fetch('/api/auth/login', {
                                method: 'POST', credentials: 'include',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify(creds),
                            }).then(r => r.json())""",
                            {"email": "p1a-owner@example.test", "password": "P1aOwner123!"},
                        )
                        assert login_ok.get("success") or login_ok.get("status") == "success" or "user" in login_ok, (
                            f"[{label}] real POST /api/auth/login for the owner did not report success on the "
                            f"phone viewport: {login_ok!r}"
                        )
                        phone_page.goto(backend.base_url, wait_until="domcontentloaded")
                        phone_page.locator("#sub-tab-more").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                        human_click(phone_page, "#sub-tab-more", "More tab")
                        phone_page.locator(".sub-more-sheet").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                        # "visible" is true from the sheet's FIRST frame, when its slide-in
                        # transform (translateY(100%)) still parks it below the viewport.
                        # Under load a probe run in that window saw a row at y=1509 on an
                        # 844px screen (one flaky FAIL in 3 runs). Wait for the sheet to
                        # have actually arrived (bottom edge inside the viewport) before
                        # measuring -- a person taps after it has slid up, not during.
                        phone_page.wait_for_function(
                            "() => { const s = document.querySelector('.sub-more-sheet'); "
                            "return !!s && s.getBoundingClientRect().bottom <= window.innerHeight + 1; }",
                            timeout=DEFAULT_WAIT_MS,
                        )

                        row = phone_page.locator(".sub-more-row", has_text=label)
                        assert row.count() >= 1, f"[{label}] no More-sheet row with that label was found for the owner"
                        human_click(phone_page, ".sub-more-row", f"More row {label}", text=label, scroll_list=True)
                        phone_page.wait_for_url(f"**/*{url_suffix}*", timeout=DEFAULT_WAIT_MS)

                        assert url_suffix in phone_page.url, (
                            f"[{label}] tapping the More-sheet row navigated to {phone_page.url!r}, "
                            f"expected it to contain {url_suffix!r}"
                        )
                        title = phone_page.title()
                        assert title_substr in title, (
                            f"[{label}] landed on {phone_page.url!r} but its <title> is {title!r}, "
                            f"expected it to contain {title_substr!r}"
                        )
                        body_text = phone_page.evaluate("() => document.body.innerText || ''")
                        assert "Coming soon" not in body_text, (
                            f"[{label}] landed on a page whose body still reads 'Coming soon.' -- H2's own "
                            f"regression, reintroduced"
                        )
                    finally:
                        phone_context.close()

            check("phone More sheet (390x844): WhatsApp/E-Invoicing/Shop network open the real standalone page", more_sheet_external_pages_open_the_real_page)

            # ================================================================
            # M9 (fix round) -- at a SHORT viewport (1024x600, shorter than
            #      the 1366x768 budget H3 proves), navigating to the LAST
            #      item of the group with the most members must still leave
            #      the active item genuinely visible inside #sub-nav's own
            #      scroll container -- proving the pre-existing rail-scroll
            #      mechanism (app-shell.js's own scrollIntoView block, "a
            #      short viewport clips the lower destinations") still does
            #      its job now that the sidebar is an accordion. Same
            #      elementFromPoint technique as H3, because "the number
            #      looks right" is not proof the pixel is not clipped.
            # ================================================================
            SHORT_VIEWPORT = {"width": 1024, "height": 600}

            def active_item_scrolls_into_view_at_a_short_viewport():
                short_context = browser.new_context(viewport=SHORT_VIEWPORT, reduced_motion="reduce")
                short_page = short_context.new_page()
                try:
                    short_page.goto(backend.base_url, wait_until="domcontentloaded")
                    login_ok = short_page.evaluate(
                        """(creds) => fetch('/api/auth/login', {
                            method: 'POST', credentials: 'include',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(creds),
                        }).then(r => r.json())""",
                        {"email": "p1a-owner@example.test", "password": "P1aOwner123!"},
                    )
                    assert login_ok.get("success") or login_ok.get("status") == "success" or "user" in login_ok, (
                        f"real POST /api/auth/login for the owner did not report success at the short viewport: {login_ok!r}"
                    )
                    short_page.goto(backend.base_url, wait_until="domcontentloaded")
                    short_page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

                    # Find the group with the most members, open it, and navigate
                    # to its LAST item -- the one most likely to sit past a short
                    # rail's own visible area.
                    target = short_page.evaluate(
                        """() => {
                          const groups = [...document.querySelectorAll('.sub-nav-group-items')];
                          let best = null;
                          for (const g of groups) {
                            const items = [...g.querySelectorAll('.sub-nav-item')];
                            if (!best || items.length > best.items.length) best = { key: g.dataset.groupItems, items };
                          }
                          if (!best || !best.items.length) return null;
                          const last = best.items[best.items.length - 1];
                          return { groupKey: best.key, section: last.dataset.section };
                        }"""
                    )
                    assert target, "no sidebar group with any items was found at the short viewport"
                    human_click(short_page, f'button[data-group-toggle="{target["groupKey"]}"]', "largest group header")
                    short_page.wait_for_timeout(200)
                    short_page.evaluate("(id) => SubsystemApp._navigate(id)", target["section"])
                    short_page.wait_for_timeout(300)

                    result = short_page.evaluate(
                        """(sec) => {
                          const el = document.querySelector(`.sub-nav-item[data-section="${sec}"]`);
                          const rail = document.getElementById('sub-nav');
                          if (!el || !rail) return null;
                          const r = el.getBoundingClientRect();
                          const rr = rail.getBoundingClientRect();
                          const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                          const hitEl = document.elementFromPoint(cx, cy);
                          return {
                            top: r.top, bottom: r.bottom, railTop: rr.top, railBottom: rr.bottom,
                            hit: !!(hitEl && el.contains(hitEl)),
                          };
                        }""",
                        target["section"],
                    )
                    assert result, f"active item for section {target['section']!r} or #sub-nav not found in the DOM"
                    assert result["top"] >= result["railTop"] - 1 and result["bottom"] <= result["railBottom"] + 1, (
                        f"active item '{target['section']}' sits at y={result['top']:.0f}..{result['bottom']:.0f}, "
                        f"outside #sub-nav's own visible area y={result['railTop']:.0f}..{result['railBottom']:.0f} "
                        f"at a {SHORT_VIEWPORT['height']}px-tall viewport -- the rail did not scroll it into view"
                    )
                    assert result["hit"], (
                        f"active item '{target['section']}' is mathematically inside #sub-nav's bounds but "
                        f"elementFromPoint at its own centre does not resolve inside it -- covered or clipped "
                        f"by something else"
                    )
                finally:
                    short_context.close()

            # Renamed (fix round 3): mutation-proved to NOT guard the scroll --
            # disabling the rail scrollIntoView left it green, because at
            # 1024x600 the item is already inside the rail before navigating.
            # It is a reachability check only. The scroll guard is the
            # 1024x420 check above, which asserts the item STARTS out of view.
            check("1024x600: the last item of the largest group is inside #sub-nav and clickable after navigating (reachability only)",
                  active_item_scrolls_into_view_at_a_short_viewport)

        finally:
            browser.close()
            backend.stop()
            shutil.rmtree(data_dir, ignore_errors=True)

    print("\n" + "=" * 78)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail else ""))
    print(f"\n{passed}/{len(RESULTS)} checks passed.")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
