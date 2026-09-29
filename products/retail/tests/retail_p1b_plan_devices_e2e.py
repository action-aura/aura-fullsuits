"""Aura Retail -- P1b real-browser proof for the "Plan & devices" screen
(frontend/plan-devices.js, Phase 1b item 5, crew B).

============================================================================
STANDALONE SCRIPT. THIS IS **NOT** A PYTEST FILE. DO NOT RUN IT VIA PYTEST.
============================================================================
Playwright lives under the SYSTEM Python, not the repo venv:

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_p1b_plan_devices_e2e.py

Harness shape copied from retail_p1a_display_e2e.py (Backend subprocess with a
temp AURA_APP_DATA, sign up unlicensed FIRST, then write the licence record,
then reload -- seeding before signup makes the app think it JOINED a shop).

WHAT THIS PROVES (every "can a person see / press it" check is rule 6b:
whole box inside the viewport + elementFromPoint hits it + real mouse click)
----------------------------------------------------------------------------
 A. Hub-like APP context (signed-in app page, host div, PlanDevices.render):
    plan name, state badge and "Valid until" come from the persisted licence
    envelope; the seat meter reads "1 of 2 devices" with a real progressbar.
 B. Standalone themed context (/static/whatsapp.html + injected script): the
    same card set paints with the theme's own tokens, sand AND night, EN AND
    AR (dir=rtl), and nothing overflows the viewport sideways.
 C. Request button: ABSENT when window.AuraHelp is missing, ABSENT when
    supportUrl returns null, PRESENT and pointing at the URL when it returns
    one -- and the context string it was given names the add-on and the shop.
 D. A null-price add-on renders "Price on request" -- never "JD null",
    "JD undefined" or a bare "JD".
 E. Remove flow: Cancel sends 0 revoke POSTs; confirming sends exactly 1 to
    /api/site-relay/devices/<id>/revoke and the row disappears on re-render.
    (The relay list and the revoke POST are STUBBED at the network layer --
    a real pairing needs a second machine. The screen's behaviour is what is
    under test, not the relay.)
 F. Non-owner sees only the "Only the shop owner..." line (session stubbed
    to a cashier; the real gate is server-side and tested in pytest).

EXIT CODE: 0 if every scenario passed, non-zero otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PRODUCT_DIR = TESTS_DIR.parent
BACKEND_DIR = PRODUCT_DIR / "backend"
FRONTEND_DIR = PRODUCT_DIR / "frontend"
REPO_ROOT = PRODUCT_DIR.parent.parent

VENV_PYTHON = Path("C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe")

HEALTH_TIMEOUT_SECONDS = 30.0
DEFAULT_WAIT_MS = 30_000
VIEWPORT = {"width": 1366, "height": 768}

TERM_END = "2027-09-01T00:00:00+00:00"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base_url: str, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
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
    """One real `<venv python> app.py`, fresh AURA_APP_DATA, a free port."""

    def __init__(self) -> None:
        self.port = _find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.app_data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_p1b_plan_appdata_"))
        self.log_path = self.app_data_dir / "backend_stdout.log"
        self.proc: subprocess.Popen | None = None
        self._log_file = None

    def start(self) -> None:
        env = os.environ.copy()
        env["AURA_STANDALONE"] = "1"
        env["PORT"] = str(self.port)
        env["AURA_APP_DATA"] = str(self.app_data_dir)
        env.pop("AURA_WHATSAPP_PHONE_NUMBER_ID", None)
        env.pop("AURA_WHATSAPP_ACCESS_TOKEN", None)
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
            raise RuntimeError(f"Backend failed to become healthy.\n--- backend log tail ---\n{tail}")

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

    def cleanup(self) -> None:
        shutil.rmtree(self.app_data_dir, ignore_errors=True)


class Results:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool, str]] = []

    def run(self, name: str, fn) -> None:
        started = time.monotonic()
        try:
            fn()
            self.items.append((name, True, ""))
            print(f"[PASS] {name}  ({time.monotonic() - started:.2f}s)")
        except Exception as e:  # noqa: BLE001 -- one check failing must not kill the run
            import traceback
            where = traceback.extract_tb(e.__traceback__)[-1]
            detail = f"{type(e).__name__}: {e} (line {where.lineno})"
            self.items.append((name, False, detail))
            print(f"[FAIL] {name}  ({time.monotonic() - started:.2f}s)\n       {detail}")

    def all_passed(self) -> bool:
        return all(ok for _, ok, _ in self.items)

    def print_summary(self) -> None:
        print("\n" + "=" * 78)
        passed = sum(1 for _, ok, _ in self.items if ok)
        print(f"RESULT: {passed}/{len(self.items)} checks passed")
        for name, ok, detail in self.items:
            if not ok:
                print(f"  [FAIL] {name} -- {detail}")
        print("=" * 78)


def _dismiss_intro_overlay_if_present(page) -> None:
    try:
        overlay = page.locator("#aura-intro-overlay")
        overlay.wait_for(state="visible", timeout=3000)
        overlay.click()
        overlay.wait_for(state="detached", timeout=3000)
    except Exception:
        pass


def _write_licence(backend: Backend) -> None:
    """Persist an ACTIVE licence WITH a signed-envelope payload (plan, seats,
    term). seed_active_license cannot seed an envelope, so the record is
    written directly -- the same repository the server reads it from."""
    sys.path.insert(0, str(REPO_ROOT))
    from commercial_runtime.licensing_contracts.state_repository import (
        LICENSING_SCHEMA_VERSION, LicenseStateRecord, LicenseStateRepository,
    )
    db_path = backend.app_data_dir / "database" / "subsystems" / "licensing.db"
    LicenseStateRepository(db_path).save(LicenseStateRecord(
        licensing_schema_version=LICENSING_SCHEMA_VERSION,
        product_code="AURA_RETAIL", platform="WINDOWS",
        current_state="ACTIVE_ONLINE", owner_installation_id="test-fixture-installation",
        license_status="ACTIVE", installation_status="ACTIVE", subscription_status="ACTIVE",
        entitlements_json=json.dumps({"digital_receipts_enabled": True}),
        assertion_envelope_json=json.dumps({"payload": {
            "plan_code": "RETAIL_STANDARD", "allowed_device_count": 2,
            "term_start": "2026-09-01T00:00:00+00:00", "term_end": TERM_END,
        }}),
    ))


def _sign_up_and_license(page, backend: Backend, company_name: str) -> None:
    page.goto(backend.base_url, wait_until="domcontentloaded")
    _dismiss_intro_overlay_if_present(page)
    page.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
    page.fill("#su-name", "P1b Plan Admin")
    page.fill("#su-company", company_name)
    page.fill("#su-email", "p1b-plan-admin@example.test")
    page.fill("#su-pass", "P1bPlanTest123!")
    page.fill("#su-pass2", "P1bPlanTest123!")
    page.click("#su-btn")
    try:
        page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
    except Exception:
        tail = ""
        try:
            tail = backend.log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        except OSError:
            pass
        raise RuntimeError(f"sign-up did not reach the nav.\n--- backend log tail ---\n{tail}")
    _write_licence(backend)
    page.reload(wait_until="domcontentloaded")
    page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
    try:
        banner = page.locator("#aura-admin-device-claim")
        banner.wait_for(state="visible", timeout=3000)
        page.get_by_role("button", name="Make this the admin device", exact=True).click()
        banner.wait_for(state="detached", timeout=8000)
    except Exception:  # noqa: BLE001
        pass


_SEEN_JS = (
    "([sel, nth, scroll]) => { const el = document.querySelectorAll(sel)[nth]; "
    "if (!el) return {found: false}; "
    "if (scroll) el.scrollIntoView({block: 'center', inline: 'center'}); "
    "const r = el.getBoundingClientRect(); "
    "const vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight; "
    "const cx = r.left + r.width / 2, cy = r.top + r.height / 2; "
    "const inside = r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= vw && r.bottom <= vh; "
    "const hit = inside ? document.elementFromPoint(cx, cy) : null; "
    "return {found: true, inside: inside, cx: cx, cy: cy, "
    "hits_it: !!hit && (hit === el || el.contains(hit)), "
    "hit: hit ? (hit.tagName + '#' + hit.id + '.' + hit.className) : null, "
    "rect: {left: r.left, top: r.top, right: r.right, bottom: r.bottom, vw: vw, vh: vh}}; }"
)


def _assert_seen(page, selector: str, what: str, nth: int = 0, scroll: bool = True) -> dict:
    """Rule 6b: whole box inside the viewport AND this element at its centre."""
    probe = page.evaluate(_SEEN_JS, [selector, nth, scroll])
    assert probe["found"], f"{what}: no element matches {selector!r} (index {nth})"
    assert probe["inside"], f"{what}: box is not fully inside the viewport (or is zero-size): {probe['rect']}"
    assert probe["hits_it"], f"{what}: something else is painted at its centre -- elementFromPoint is {probe['hit']!r}"
    return probe


def _click_centre(page, selector: str, what: str, nth: int = 0) -> None:
    probe = _assert_seen(page, selector, what, nth=nth)
    page.mouse.click(probe["cx"], probe["cy"])


def _text(page, selector: str) -> str:
    return page.evaluate(
        "(s) => { const e = document.querySelector(s); return e ? e.textContent.trim() : null; }", selector)


def _count(page, selector: str) -> int:
    return page.evaluate("(s) => document.querySelectorAll(s).length", selector)


_MOUNT_JS = """async ([standalone, opts]) => {
  if (standalone) Array.from(document.body.children).forEach(c => { c.style.display = 'none'; });
  const old = document.getElementById('pd-host'); if (old) old.remove();
  const h = document.createElement('div'); h.id = 'pd-host';
  h.style.cssText = standalone ? 'padding:16px'
    : 'position:fixed;inset:0;overflow:auto;z-index:5000;background:var(--surface-app);padding:16px';
  document.body.appendChild(h);
  await window.PlanDevices.render(h, opts || undefined);
  return true;
}"""

_RELOAD_JS = """async (opts) => {
  const h = document.getElementById('pd-host');
  await window.PlanDevices.render(h, opts || undefined);
  return true;
}"""

I18N_B = Path("C:/Users/MSI/.claude/jobs/c5a4f4c4/tmp/p1b/i18n-B.json")


def _prepare(page, lang: str, theme: str) -> None:
    page.evaluate(
        "([l, t]) => { localStorage.setItem('aura_lang', l); localStorage.setItem('aura_theme_v2', t); }",
        [lang, theme])


def _mount(page, backend: Backend, *, standalone: bool, lang: str = "en", theme: str = "sand",
           opts=None, arabic_strings: bool = False) -> None:
    """Load the page, inject plan-devices.js exactly as a hub would, paint."""
    _prepare(page, lang, theme)
    if standalone:
        page.goto(f"{backend.base_url}/static/whatsapp.html", wait_until="load")
    else:
        page.goto(backend.base_url, wait_until="domcontentloaded")
        page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
    if lang == "ar":
        page.wait_for_function("document.documentElement.getAttribute('dir') === 'rtl'", timeout=DEFAULT_WAIT_MS)
    if arabic_strings and I18N_B.exists():
        # Locale files are not merged yet (orchestrator does that), so lay the
        # screen out with the REAL Arabic strings by wrapping window.t.
        table = json.loads(I18N_B.read_text(encoding="utf-8"))
        page.evaluate(
            """(table) => { const prev = window.t;
              window.t = (s) => (Object.prototype.hasOwnProperty.call(table, s) ? table[s]
                                 : (typeof prev === 'function' ? prev(s) : s)); }""", table)
    page.add_script_tag(url=f"{backend.base_url}/static/plan-devices.js")
    page.evaluate(_MOUNT_JS, [standalone, opts])
    page.wait_for_selector("#pd-host [data-pd=addons]", timeout=DEFAULT_WAIT_MS)


def _no_sideways_overflow(page, what: str) -> None:
    got = page.evaluate(
        "() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})")
    assert got["sw"] <= got["cw"] + 1, f"{what}: page scrolls sideways ({got})"


# ── A: hub-like app context, real licence envelope ─────────────────────────

def _scenario_app_context(results: Results, browser, shots: Path) -> None:
    backend = Backend()
    try:
        backend.start()
        context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_WAIT_MS)
        _sign_up_and_license(page, backend, "P1b Plan Co")
        _mount(page, backend, standalone=False)

        def plan_facts_come_from_the_envelope():
            assert _text(page, "[data-pd=plan] .pd-plan") == "Standard", _text(page, "[data-pd=plan]")
            assert _text(page, "[data-pd=state]") == "Active", _text(page, "[data-pd=state]")
            until = _text(page, "[data-pd=until]").replace("\u2066", "").replace("\u2069", "")
            assert until == "Valid until 2027-09-01", until
            body = page.evaluate("document.getElementById('pd-host').innerText")
            for raw in ("RETAIL_STANDARD", "ACTIVE_ONLINE", "AURA_RETAIL", "test-fixture-installation", "null", "undefined", "NaN"):
                assert raw not in body, f"raw value {raw!r} leaked onto the screen: {body!r}"

        results.run("A1 plan name, state and term come from the persisted envelope, no raw codes", plan_facts_come_from_the_envelope)

        def seat_meter_is_honest():
            assert _text(page, "#pd-seat-figure") == "1 of 2 devices", _text(page, "#pd-seat-figure")
            meter = page.evaluate(
                "() => { const m = document.querySelector('#pd-host [role=progressbar]'); "
                "return m ? {now: m.getAttribute('aria-valuenow'), max: m.getAttribute('aria-valuemax'), "
                "w: m.firstElementChild.style.inlineSize} : null; }")
            assert meter == {"now": "1", "max": "2", "w": "50%"}, meter
            assert "manager’s phone and one till" in _text(page, "[data-pd=included]")
            assert "Digital receipts on WhatsApp" in _text(page, "[data-pd=features]")

        results.run("A2 seat meter reads 1 of 2 with a real progressbar; included + feature lines", seat_meter_is_honest)

        def a_person_can_see_every_card():
            for sel, what in (("[data-pd=plan]", "plan card"), ("#pd-seat-figure", "seat figure"),
                              ("[data-pd=features]", "features card")):
                _assert_seen(page, sel, what)
            for i in range(2):
                _assert_seen(page, "[data-pd-addon]", f"add-on tile {i + 1}", nth=i)
            _assert_seen(page, "a.pd-link.quiet", "License details link")

        results.run("A3 (6b) plan, seats, features, both add-on tiles and the licence link are reachable", a_person_can_see_every_card)

        def licence_link_really_navigates():
            _click_centre(page, "a.pd-link.quiet", "License details link")
            page.wait_for_url("**/static/licensing.html", timeout=DEFAULT_WAIT_MS)

        page.screenshot(path=str(shots / "A_app_en_sand.png"))
        results.run("A4 a real click on License details lands on the licence page", licence_link_really_navigates)
    finally:
        backend.stop()
        backend.cleanup()
        backend.cleanup()


def _rgb(s: str) -> tuple[float, float, float, float]:
    inner = s[s.index("(") + 1:s.index(")")].replace("/", ",").replace(" ", ",")
    parts = [p for p in inner.split(",") if p]
    vals = [float(p) for p in parts]
    return (vals[0], vals[1], vals[2], vals[3] if len(vals) > 3 else 1.0)


def _lum(c) -> float:
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def _contrast(a: str, b: str) -> float:
    la, lb = _lum(_rgb(a)), _lum(_rgb(b))
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


_STYLE_JS = (
    "() => { const g = (s, p) => { const e = document.querySelector(s); return e ? getComputedStyle(e)[p] : null; }; "
    "return {card: g('#pd-host .pd-card', 'backgroundColor'), plan: g('.pd-plan', 'color'), "
    "muted: g('.pd-muted', 'color'), body: g('body', 'backgroundColor'), "
    "dir: document.documentElement.getAttribute('dir')}; }"
)


def _centre_x(page, selector: str) -> float:
    return page.evaluate(
        "(s) => { const r = document.querySelector(s).getBoundingClientRect(); return (r.left + r.right) / 2; }",
        selector)


def _scenario_standalone_variants(results: Results, browser, shots: Path) -> None:
    backend = Backend()
    try:
        backend.start()
        context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_WAIT_MS)
        _sign_up_and_license(page, backend, "P1b Plan Co")

        # ── B: themed standalone, EN/AR x sand/night ───────────────────────
        seen_card_bg: dict[str, str] = {}
        for lang in ("en", "ar"):
            for theme in ("sand", "night"):
                def check(lang=lang, theme=theme):
                    _mount(page, backend, standalone=True, lang=lang, theme=theme, arabic_strings=(lang == "ar"))
                    st = page.evaluate(_STYLE_JS)
                    assert page.evaluate("document.documentElement.getAttribute('data-theme')") == theme
                    assert st["dir"] == ("rtl" if lang == "ar" else "ltr") or (lang == "en" and st["dir"] in (None, "ltr")), st
                    assert _rgb(st["card"])[3] > 0.9, f"card background is transparent, tokens not applied: {st}"
                    seen_card_bg[f"{lang}-{theme}"] = st["card"]
                    for fg in ("plan", "muted"):
                        ratio = _contrast(st[fg], st["card"])
                        assert ratio >= 4.5, f"{fg} text on the card has contrast {ratio:.2f} (<4.5) in {lang}/{theme}: {st}"
                    for sel, what in (("[data-pd=plan]", "plan card"), ("#pd-seat-figure", "seat figure"),
                                      ("[data-pd=features]", "features card")):
                        _assert_seen(page, sel, f"{what} ({lang}/{theme})")
                    for i in range(2):
                        _assert_seen(page, "[data-pd-addon]", f"add-on tile {i + 1} ({lang}/{theme})", nth=i)
                    _no_sideways_overflow(page, f"{lang}/{theme}")
                    # The date must READ 2027-09-01 left-to-right even inside an RTL
                    # sentence: measure where the year and the day glyphs really sit.
                    order = page.evaluate(
                        "() => { const p = document.querySelector('[data-pd=until]'); "
                        "const w = document.createTreeWalker(p, NodeFilter.SHOW_TEXT); let n; "
                        "while ((n = w.nextNode())) { const i = n.data.indexOf('2027-09-01'); "
                        "if (i >= 0) { const at = (a, b) => { const r = document.createRange(); r.setStart(n, a); r.setEnd(n, b); "
                        "return r.getBoundingClientRect().left; }; return {year: at(i, i + 4), day: at(i + 8, i + 10)}; } } return null; }")
                    assert order and order["year"] < order["day"], f"date renders reversed in {lang}: {order}"
                    plan_x, badge_x = _centre_x(page, ".pd-plan"), _centre_x(page, "[data-pd=state]")
                    if lang == "ar":
                        assert plan_x > badge_x, f"in RTL the plan name must sit to the right of its badge ({plan_x} vs {badge_x})"
                    else:
                        assert plan_x < badge_x, f"in LTR the plan name must sit left of its badge ({plan_x} vs {badge_x})"
                    page.screenshot(path=str(shots / f"B_standalone_{lang}_{theme}.png"))

                results.run(f"B standalone {lang}/{theme}: tokens applied, contrast >= 4.5, all boxes reachable, "
                            f"no sideways scroll, mirrored order", check)

        def themes_really_differ():
            assert seen_card_bg.get("en-sand") and seen_card_bg.get("en-night"), seen_card_bg
            assert seen_card_bg["en-sand"] != seen_card_bg["en-night"], (
                f"sand and night paint the same card colour -- the theme is not reaching the screen: {seen_card_bg}")

        results.run("B sand and night paint different card colours (the theme is really applied)", themes_really_differ)

        # ── C: Request button, three absent states + one present ───────────
        contexts_seen: list[str] = []
        context.route("https://wa.me/**", lambda route: route.fulfill(status=200, content_type="text/html", body="<p>wa</p>"))

        def with_help(js: str, label: str):
            _mount(page, backend, standalone=True)
            page.evaluate(js)
            page.evaluate(_RELOAD_JS, None)
            page.wait_for_selector("#pd-host [data-pd=addons]", timeout=DEFAULT_WAIT_MS)
            return _count(page, "#pd-host [data-pd=request]")

        def request_hidden_when_help_missing():
            assert with_help("delete window.AuraHelp;", "absent") == 0

        def request_hidden_when_supporturl_null():
            assert with_help("window.AuraHelp = {supportUrl: () => null};", "null") == 0

        def request_hidden_when_supporturl_throws():
            assert with_help("window.AuraHelp = {supportUrl: () => { throw new Error('x'); }};", "throws") == 0

        def request_hidden_when_supporturl_empty_string():
            assert with_help("window.AuraHelp = {supportUrl: () => ''};", "empty") == 0

        results.run("C1 Request is ABSENT when AuraHelp does not exist", request_hidden_when_help_missing)
        results.run("C2 Request is ABSENT when supportUrl returns null", request_hidden_when_supporturl_null)
        results.run("C3 Request is ABSENT when supportUrl throws", request_hidden_when_supporturl_throws)
        results.run("C4 Request is ABSENT when supportUrl returns ''", request_hidden_when_supporturl_empty_string)

        def request_present_and_really_opens():
            page.route("**/api/sub/retail/settings/branding", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"status": "success", "data": {"branding_business_name": "P1b Plan Co"}})))
            n = with_help(
                "window.__ctx = []; window.AuraHelp = {supportUrl: (c) => { window.__ctx.push(c); "
                "return 'https://wa.me/962700000000?text=' + encodeURIComponent(c); }};", "present")
            assert n == 2, f"expected a Request link on each of the 2 tiles, found {n}"
            ctxs = page.evaluate("window.__ctx")
            contexts_seen.extend(ctxs)
            for what in ("Extra device", "Extra branch"):
                mine = [c for c in ctxs if c.startswith(f"Add-on request: {what} | Shop: P1b Plan Co | Installation: ")]
                assert mine and len(mine[0].rsplit("Installation: ", 1)[1]) > 3, (what, ctxs)
            for i in range(2):
                _assert_seen(page, "[data-pd=request]", f"Request link {i + 1}", nth=i)
            attrs = page.evaluate(
                "() => { const a = document.querySelector('[data-pd=request]'); "
                "return {t: a.getAttribute('target'), rel: a.getAttribute('rel'), href: a.getAttribute('href')}; }")
            assert attrs["t"] == "_blank" and "noopener" in attrs["rel"], attrs
            probe = _assert_seen(page, "[data-pd=request]", "Request link 1", nth=0)
            with context.expect_page() as popup_info:
                page.mouse.click(probe["cx"], probe["cy"])
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded")
            assert popup.url.startswith("https://wa.me/962700000000?text="), popup.url
            popup.close()
            page.screenshot(path=str(shots / "C_request_present.png"))

        results.run("C5 Request is PRESENT on both tiles, carries add-on + shop context, and a real click opens it", request_present_and_really_opens)

        def request_without_a_shop_name_still_identifies_the_shop():
            page.unroute("**/api/sub/retail/settings/branding")  # real backend: branding never filled in
            with_help("window.__ctx = []; window.AuraHelp = {supportUrl: (c) => { window.__ctx.push(c); "
                      "return 'https://wa.me/962700000000?text=' + encodeURIComponent(c); }};", "present")
            ctxs = page.evaluate("window.__ctx")
            assert ctxs, "no support links were built"
            for c in ctxs:
                assert "Shop:" not in c and "undefined" not in c and "null" not in c, c
                assert "| Installation: " in c and len(c.rsplit("Installation: ", 1)[1]) > 3, c

        results.run("C6 no branding name: request carries the installation id, never 'Shop: undefined'", request_without_a_shop_name_still_identifies_the_shop)

        # ── D: null price ──────────────────────────────────────────────────
        def null_price_says_price_on_request():
            odd = [
                {"id": "mystery", "english": "Mystery", "name": "Mystery add-on", "desc": "Priced by phone.", "price": None, "unit": ""},
                {"id": "blank", "english": "Blank", "name": "Blank add-on", "desc": "Empty price.", "price": "", "unit": "per x"},
                {"id": "real", "english": "Real", "name": "Real add-on", "desc": "Has a price.", "price": "12", "unit": "each"},
            ]
            _mount(page, backend, standalone=True, opts={"addons": odd})
            prices = page.evaluate(
                "() => Array.from(document.querySelectorAll('[data-pd-addon]')).map(t => "
                "({id: t.getAttribute('data-pd-addon'), price: t.querySelector('[data-pd=price]').textContent.trim(), "
                "text: t.textContent}))")
            by_id = {p["id"]: p for p in prices}
            assert by_id["mystery"]["price"] == "Price on request", by_id["mystery"]
            assert by_id["blank"]["price"] == "Price on request", by_id["blank"]
            assert by_id["real"]["price"] == "JD 12", by_id["real"]
            for p in prices:
                assert "null" not in p["text"] and "undefined" not in p["text"], p
            assert "per x" not in by_id["blank"]["text"], "a unit must not hang under a price that is not shown"
            for i in range(3):
                _assert_seen(page, "[data-pd-addon]", f"add-on tile {i + 1}", nth=i)
            page.screenshot(path=str(shots / "D_price_on_request.png"))

        results.run("D null / empty price -> 'Price on request' (never JD null), priced tile still 'JD 12'", null_price_says_price_on_request)

        # ── F: non-owner ────────────────────────────────────────────────────
        def cashier_sees_only_the_owner_line():
            _prepare(page, "en", "sand")
            page.goto(f"{backend.base_url}/static/whatsapp.html", wait_until="load")
            page.route("**/api/auth/session", lambda r: r.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"authenticated": True, "user": {"role": "cashier"}})))
            hits = []
            page.on("request", lambda r: hits.append(r.url) if "/api/licensing/status" in r.url or "/api/site-relay" in r.url else None)
            page.add_script_tag(url=f"{backend.base_url}/static/plan-devices.js")
            page.evaluate(_MOUNT_JS, [True, None])
            page.wait_for_function("document.getElementById('pd-host').textContent.includes('Only the shop owner')")
            assert _count(page, "#pd-host [data-pd]") == 0, "a cashier must see no plan, seat or add-on card at all"
            assert not hits, f"a cashier's screen must not even ask for licence / relay data: {hits}"
            _assert_seen(page, "#pd-host p", "the owner-only line")
            page.unroute("**/api/auth/session")

        results.run("F non-owner sees only the owner-only line and requests no licence/relay data", cashier_sees_only_the_owner_line)

        # ── G: every REAL licence state, and the seat wording (fix round F7/F11)
        # The licence status is stubbed at the network layer so each of the 15
        # states the licence engine can really emit is walked in a real browser.
        # The expected table below is written out by hand from
        # commercial_runtime/licensing_contracts/state_machine.py's LicenseState,
        # NOT read back from plan-devices.js -- a table copied from the code under
        # test could never catch that code drifting.
        real_states = [
            ("ACTIVE_ONLINE", "Active", "ok"),
            ("ACTIVE_OFFLINE", "Active (offline)", "ok"),
            ("WARNING", "Check-in needed soon", "warn"),
            ("GRACE_PERIOD", "Offline grace period", "warn"),
            ("CLOCK_REVIEW_REQUIRED", "Clock review required", "warn"),
            ("RESTRICTED", "Restricted", "bad"),
            ("SUSPENDED", "Suspended", "bad"),
            ("REVOKED", "Revoked", "bad"),
            ("EXPIRED", "Expired", "bad"),
            ("LOCAL_STATE_CORRUPT", "Local state needs reset", "bad"),
            ("DEVICE_DEACTIVATED", "Computer deactivated", "bad"),
            ("DEVICE_REPLACED", "Computer replaced", "bad"),
            ("NOT_CONFIGURED", "Not configured", "neutral"),
            ("ACTIVATION_REQUIRED", "Activation required", "neutral"),
            ("ACTIVATING", "Activating…", "neutral"),
            # not a state the engine emits: must say so, never pose as one
            ("SOMETHING_NEWER", "Status unavailable", "neutral"),
            ("constructor", "Status unavailable", "neutral"),   # inherited property names are not states
            ("__proto__", "Status unavailable", "neutral"),
        ]
        lic_stub = {"body": {}}

        def licence_status(route):
            route.fulfill(status=200, content_type="application/json", body=json.dumps(lic_stub["body"]))

        page.route("**/api/licensing/status", licence_status)

        def _mount_with_licence(body: dict) -> None:
            lic_stub["body"] = body
            _mount(page, backend, standalone=True)

        for raw_state, label, kind in real_states:
            def one_state(raw_state=raw_state, label=label, kind=kind):
                _mount_with_licence({"current_state": raw_state, "plan_code": "RETAIL_STANDARD", "allowed_device_count": 2,
                                     "term": {"start": None, "end": TERM_END}})
                got = page.evaluate(
                    "() => { const b = document.querySelector('[data-pd=state]'); "
                    "return {t: b.textContent.trim(), c: b.className, s: b.getAttribute('data-pd-state')}; }")
                assert got["t"] == label, f"{raw_state}: badge says {got['t']!r}, expected {label!r}"
                assert got["c"].split() == ["pd-badge", kind], f"{raw_state}: class {got['c']!r}, expected pd-badge {kind}"
                assert got["s"] == raw_state, f"{raw_state}: data-pd-state is {got['s']!r}"
                if label != "Not configured":
                    body = page.evaluate("document.getElementById('pd-host').innerText")
                    assert "Not configured" not in body, f"{raw_state}: a licensed shop is shown 'Not configured'"
                _assert_seen(page, "[data-pd=state]", f"state badge ({raw_state})")

            results.run(f"G1 licence state {raw_state}: badge reads {label!r} ({kind})", one_state)

        def a_missing_state_is_unavailable_not_not_configured():
            _mount_with_licence({"allowed_device_count": 2})
            assert _text(page, "[data-pd=state]") == "Status unavailable", _text(page, "[data-pd=state]")
            _mount_with_licence({"current_state": None, "allowed_device_count": 2})
            assert _text(page, "[data-pd=state]") == "Status unavailable", _text(page, "[data-pd=state]")

        results.run("G2 no current_state at all reads 'Status unavailable', never 'Not configured'", a_missing_state_is_unavailable_not_not_configured)

        def included_line_follows_the_real_count():
            cases = [
                (2, "Your license includes a manager’s phone and one till."),
                (1, "Your license includes 1 device."),
                (5, "Your license includes 5 devices."),
                (3, "Your license includes 3 devices."),
            ]
            for allowed, want in cases:
                _mount_with_licence({"current_state": "ACTIVE_ONLINE", "allowed_device_count": allowed})
                got = _text(page, "[data-pd=included]")
                assert got == want, f"allowed={allowed}: {got!r}, expected {want!r}"
                assert "manager" not in got or allowed == 2, f"the 2-device sentence shown for allowed={allowed}: {got!r}"
            _mount_with_licence({"current_state": "ACTIVE_ONLINE"})
            assert _count(page, "[data-pd=included]") == 0, "an unknown seat count must state no 'includes' sentence at all"
            assert _text(page, "#pd-seat-figure") == "—", _text(page, "#pd-seat-figure")

        results.run("G3 'includes' line: manager's phone only for 2, plain count for others, nothing when unknown", included_line_follows_the_real_count)

        def activation_wording_only_for_states_that_need_activation():
            needs = ("NOT_CONFIGURED", "ACTIVATION_REQUIRED", "ACTIVATING", "DEVICE_DEACTIVATED", "DEVICE_REPLACED")
            for st in needs:
                _mount_with_licence({"current_state": st})
                assert _count(page, "[data-pd=needs-activation]") == 1 and _count(page, "[data-pd=needs-internet]") == 0, st
                assert "Activate it with your license key" in _text(page, "[data-pd=needs-activation]"), st
                assert "internet" not in _text(page, "[data-pd=needs-activation]"), st
            for st in ("ACTIVE_ONLINE", "GRACE_PERIOD", "WARNING", "EXPIRED"):
                _mount_with_licence({"current_state": st})
                assert _count(page, "[data-pd=needs-internet]") == 1 and _count(page, "[data-pd=needs-activation]") == 0, st
                assert "Connect to the internet" in _text(page, "[data-pd=needs-internet]"), st
            _assert_seen(page, "[data-pd=needs-internet]", "the internet sentence")

        results.run("G4 unactivated states get activation wording; other states without a count get the internet wording", activation_wording_only_for_states_that_need_activation)

        def no_seat_count_is_read_from_the_licence():
            # A licence status that carries a stray `devices_used` must not move
            # the meter: the count comes from the relay list only.
            _mount_with_licence({"current_state": "ACTIVE_ONLINE", "allowed_device_count": 4, "devices_used": 4})
            assert _text(page, "#pd-seat-figure") == "1 of 4 devices", _text(page, "#pd-seat-figure")

        results.run("G5 the seat count ignores any devices_used on the licence status (relay list only)", no_seat_count_is_read_from_the_licence)
        page.unroute("**/api/licensing/status")

        # ── E: Remove flow (relay list + revoke are stubbed) ───────────────
        state = {"revoked": False, "posts": []}

        def relay_status(route):
            devices = [{"installation_id": "dev-phone-1", "label": "Sami's phone", "paired_at": "2026-09-20T10:00:00Z",
                        "revoked": state["revoked"]}]
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"enabled": True, "devices": devices}))

        def revoke(route):
            state["posts"].append(route.request.url)
            state["revoked"] = True
            route.fulfill(status=200, content_type="application/json", body="{}")

        page.route("**/api/site-relay/status", relay_status)
        page.route("**/api/site-relay/devices/*/revoke", revoke)

        def remove_cancel_then_confirm_standalone():
            state.update(revoked=False, posts=[])
            _mount(page, backend, standalone=True)
            assert _text(page, "#pd-seat-figure") == "2 of 2 devices", _text(page, "#pd-seat-figure")
            assert _count(page, "[data-pd-device=dev-phone-1]") == 1
            _assert_seen(page, "[data-pd-device=dev-phone-1] .pd-btn", "Remove button")
            page.once("dialog", lambda d: d.dismiss())
            _click_centre(page, "[data-pd-device=dev-phone-1] .pd-btn", "Remove button")
            page.wait_for_timeout(500)
            assert state["posts"] == [], f"Cancel must send no revoke, saw {state['posts']}"
            assert _count(page, "[data-pd-device=dev-phone-1]") == 1
            page.once("dialog", lambda d: d.accept())
            _click_centre(page, "[data-pd-device=dev-phone-1] .pd-btn", "Remove button")
            # Wait for the RE-RENDERED screen (the figure exists again), not merely
            # "no device rows" -- that is also true during the Loading… flash.
            page.wait_for_function(
                "(document.getElementById('pd-seat-figure')||{}).textContent === '1 of 2 devices'", timeout=DEFAULT_WAIT_MS)
            assert _count(page, "[data-pd-device]") == 0
            assert len(state["posts"]) == 1 and state["posts"][0].endswith("/dev-phone-1/revoke"), state["posts"]
            page.screenshot(path=str(shots / "E_after_remove.png"))

        results.run("E1 (standalone, window.confirm) Cancel sends 0 revokes; confirm sends exactly 1 and the row goes", remove_cancel_then_confirm_standalone)

        def remove_failure_is_said_out_loud():
            state.update(revoked=False, posts=[])
            page.unroute("**/api/site-relay/devices/*/revoke")
            page.route("**/api/site-relay/devices/*/revoke", lambda r: r.fulfill(status=500, content_type="application/json", body="{}"))
            _mount(page, backend, standalone=True)
            page.once("dialog", lambda d: d.accept())
            _click_centre(page, "[data-pd-device=dev-phone-1] .pd-btn", "Remove button")
            page.wait_for_function("document.querySelector('#pd-host .pd-err[role=alert]').textContent.length > 0", timeout=DEFAULT_WAIT_MS)
            assert "did not work" in _text(page, "#pd-host .pd-err[role=alert]")
            assert _count(page, "[data-pd-device=dev-phone-1]") == 1, "a failed Remove must leave the device listed"
            assert _text(page, "#pd-seat-figure") == "2 of 2 devices"
            page.unroute("**/api/site-relay/devices/*/revoke")
            page.route("**/api/site-relay/devices/*/revoke", revoke)

        results.run("E2 a failed revoke shows a message and keeps the device (no silent success)", remove_failure_is_said_out_loud)

        def remove_in_the_app_uses_the_real_modal():
            state.update(revoked=False, posts=[])
            _mount(page, backend, standalone=False)
            _assert_seen(page, "[data-pd-device=dev-phone-1] .pd-btn", "Remove button (app)")
            _click_centre(page, "[data-pd-device=dev-phone-1] .pd-btn", "Remove button (app)")
            page.wait_for_selector("#ret-confirm-dialog", timeout=DEFAULT_WAIT_MS)
            assert "Sami's phone" in _text(page, "#ret-confirm-dialog-title")
            _assert_seen(page, "#ret-confirm-cancel", "modal Cancel", scroll=False)
            _click_centre(page, "#ret-confirm-cancel", "modal Cancel")
            page.wait_for_selector("#ret-confirm-dialog", state="detached", timeout=DEFAULT_WAIT_MS)
            assert state["posts"] == [], state["posts"]
            _click_centre(page, "[data-pd-device=dev-phone-1] .pd-btn", "Remove button (app)")
            page.wait_for_selector("#ret-confirm-dialog", timeout=DEFAULT_WAIT_MS)
            _click_centre(page, "#ret-confirm-ok", "modal Remove")
            page.wait_for_function(
                "(document.getElementById('pd-seat-figure')||{}).textContent === '1 of 2 devices'", timeout=DEFAULT_WAIT_MS)
            assert _count(page, "[data-pd-device]") == 0
            assert len(state["posts"]) == 1, state["posts"]
            page.screenshot(path=str(shots / "E_app_modal_done.png"))

        results.run("E3 (app context) the app's own confirm modal: Cancel = 0 revokes, Remove = exactly 1", remove_in_the_app_uses_the_real_modal)
    finally:
        backend.stop()
        backend.cleanup()


def _run_all(results: Results, shots: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for name, fn in (
                ("A app context", lambda: _scenario_app_context(results, browser, shots)),
                ("B-F standalone variants", lambda: _scenario_standalone_variants(results, browser, shots)),
            ):
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    results.items.append((name, False, f"scenario-level failure: {e}"))
                    print(f"[FAIL] {name} (scenario-level) -- {e}")
        finally:
            browser.close()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        import playwright  # noqa: F401
    except ImportError:
        print(f"FATAL: Playwright is not importable under {sys.executable}; use the SYSTEM Python.", file=sys.stderr)
        return 2
    shots = Path(tempfile.gettempdir()) / f"aura_retail_p1b_plan_screenshots_{_now()}"
    shots.mkdir(parents=True, exist_ok=True)
    results = Results()
    try:
        _run_all(results, shots)
    except Exception as e:  # noqa: BLE001
        print(f"HARNESS FAILURE: {e}", file=sys.stderr)
        results.items.append(("harness", False, str(e)))
    results.print_summary()
    print(f"Screenshots: {shots}")
    return 0 if results.all_passed() else 1


if __name__ == "__main__":
    sys.exit(main())
