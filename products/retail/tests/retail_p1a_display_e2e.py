"""Aura Retail -- P1a real-browser proof for crew D's display/standalone-page
work (Phase 1a items 5 and 7, contracts.md).

============================================================================
STANDALONE SCRIPT. THIS IS **NOT** A PYTEST FILE. DO NOT RUN IT VIA PYTEST.
============================================================================
Playwright is installed under the SYSTEM Python
(``C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe``), NOT
under this repo's pytest venv. Run it directly:

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_p1a_display_e2e.py

Harness copied from retail_smoke_e2e.py / retail_p0_modals_e2e.py (Backend
subprocess class, health-wait, the "sign up unlicensed -> seed_active_license
-> reload" order those files' own comments insist on -- seeding before
signup makes the app think it JOINED a shop and it hangs on "Connecting to
your shop...").

WHAT THIS PROVES
----------------------------------------------------------------------------
 1. The idle customer screen (customer-display.html), with a shop WhatsApp
    number and an active promotion configured: shop name and the promotion
    text are rendered with non-zero size inside the viewport, and the
    WhatsApp QR is rendered SERVER-SIDE and is byte-identical to
    commercial_runtime.einvoicing.qr.render_qr_svg_data_uri computed
    independently, in this process, against the same wa.me payload -- never
    a re-implementation of the encoder, an exact-output comparison against
    it. (segno IS importable under this system Python -- checked directly --
    so this uses the exact-data-URI-equality proof the contract offers as
    the alternative to a pixel QR decoder, which this system Python does not
    have installed; the equality proof is the stronger claim of the two,
    since it pins the exact bytes the server sent, not just that SOMETHING
    decodable was painted.)
 2. With NO shop WhatsApp number configured: no QR element renders at all
    (no placeholder box either) -- a SEPARATE, dedicated install proves this,
    since #1's install has a number by design.
 3. Standalone pages (whatsapp.html) load the shop's chosen theme: computed
    background colour of <body> is measured on whatsapp.html and compared,
    byte-for-byte, against the SAME computed property read from the app
    shell itself with the SAME theme active -- for both 'sand' and 'night'.
 4. dir="rtl" is set on <html> when Arabic is the active language, on a
    standalone page that never loads app-shell.js.
 5. The no-transport sentence (owner decision, contracts.md 2026-09-28) is
    shown with NO env-var name anywhere in the page's rendered text, and
    never promises "your business number".

EXIT CODE
----------------------------------------------------------------------------
0 if every scenario passed. Non-zero otherwise (including a harness-level
failure such as no Chromium binary).
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PRODUCT_DIR = TESTS_DIR.parent
BACKEND_DIR = PRODUCT_DIR / "backend"
REPO_ROOT = PRODUCT_DIR.parent.parent

VENV_PYTHON = Path("C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe")

HEALTH_TIMEOUT_SECONDS = 30.0
# Generous on purpose: this script runs concurrently with other crews' own
# backends/browsers on the same machine during Phase 1a (contracts.md), so a
# cold Chromium/Flask boot under real contention is routinely slower than an
# isolated run.
DEFAULT_WAIT_MS = 30_000
VIEWPORT = {"width": 1366, "height": 768}

SHOP_WHATSAPP_E164 = "+9627 9123 4567".replace(" ", "")  # normalised shape T's Settings field is contracted to store


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
    """One real `<venv python> app.py` subprocess, fresh AURA_APP_DATA, a
    free port -- copied from retail_smoke_e2e.py's own Backend class (same
    rationale, same shape)."""

    def __init__(self) -> None:
        self.port = _find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.app_data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_p1a_display_appdata_"))
        self.log_path = self.app_data_dir / "backend_stdout.log"
        self.proc: subprocess.Popen | None = None
        self._log_file = None

    def start(self) -> None:
        env = os.environ.copy()
        env["AURA_STANDALONE"] = "1"
        env["PORT"] = str(self.port)
        env["AURA_APP_DATA"] = str(self.app_data_dir)
        # D5 needs a genuinely UNCONFIGURED WhatsApp transport (the
        # "Automatic WhatsApp messages aren't switched on yet" sentence only
        # renders when whatsapp_client.is_configured() is False) -- this
        # machine's own ambient shell environment carries real
        # AURA_WHATSAPP_PHONE_NUMBER_ID/AURA_WHATSAPP_ACCESS_TOKEN values
        # (confirmed by reading `env` directly), which os.environ.copy()
        # would otherwise silently inherit into every backend this script
        # boots, making every install here look "configured" regardless of
        # what this test actually wants to prove. Stripped unconditionally,
        # not just for D5, so every scenario in this file starts from the
        # same clean baseline retail_smoke_e2e.py's own Backend deliberately
        # keeps ("An install with no licensing configured is READ-ONLY, not
        # unlocked" -- the same discipline applied here to WhatsApp).
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
            raise RuntimeError(f"Backend failed to become healthy.\n--- backend stdout/stderr tail ---\n{tail}")

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

    def retail_db_path(self) -> Path:
        return self.app_data_dir / "database" / "subsystems" / "retail.db"


def _dismiss_intro_overlay_if_present(page) -> None:
    try:
        overlay = page.locator("#aura-intro-overlay")
        overlay.wait_for(state="visible", timeout=3000)
        overlay.click()
        overlay.wait_for(state="detached", timeout=3000)
    except Exception:
        pass


class Results:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool, str]] = []

    def run(self, name: str, fn) -> None:
        started = time.monotonic()
        try:
            fn()
            self.items.append((name, True, ""))
            print(f"[PASS] {name}  ({time.monotonic() - started:.2f}s)")
        except Exception as e:  # noqa: BLE001 -- one scenario's failure must not kill the run
            self.items.append((name, False, str(e)))
            print(f"[FAIL] {name}  ({time.monotonic() - started:.2f}s)\n       {e}")

    def all_passed(self) -> bool:
        return all(ok for _, ok, _ in self.items)

    def print_summary(self) -> None:
        print("\n" + "=" * 78)
        passed = sum(1 for _, ok, _ in self.items if ok)
        print(f"RESULT: {passed}/{len(self.items)} scenarios passed")
        for name, ok, detail in self.items:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))
        print("=" * 78)


def _sign_up_and_license(page, backend: Backend, company_name: str) -> None:
    """Sign up FIRST while unlicensed, THEN seed_active_license, THEN
    reload -- see contracts.md's harness note. Reused verbatim by every
    scenario below that needs a signed-in owner."""
    sys.path.insert(0, str(REPO_ROOT))
    from commercial_runtime.licensing_contracts.test_support import seed_active_license

    page.goto(backend.base_url, wait_until="domcontentloaded")
    _dismiss_intro_overlay_if_present(page)
    page.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
    page.fill("#su-name", "P1a Display Admin")
    page.fill("#su-company", company_name)
    page.fill("#su-email", "p1a-display-admin@example.test")
    page.fill("#su-pass", "P1aDisplayTest123!")
    page.fill("#su-pass2", "P1aDisplayTest123!")
    page.click("#su-btn")
    try:
        page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
    except Exception:
        tail = ""
        try:
            tail = backend.log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
        except OSError:
            pass
        raise RuntimeError(f"sign-up did not reach the nav after clicking #su-btn.\n--- backend log tail ---\n{tail}")

    seed_active_license(str(backend.app_data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
    page.reload(wait_until="domcontentloaded")
    page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

    # Clear the admin-device-claim banner defensively if it appears -- same
    # shape retail_smoke_e2e.py's own scenarios use; #20's auto-claim means
    # it normally should not, but a stray banner must never block a later
    # click in this script.
    try:
        banner = page.locator("#aura-admin-device-claim")
        banner.wait_for(state="visible", timeout=3000)
        page.get_by_role("button", name="Make this the admin device", exact=True).click()
        banner.wait_for(state="detached", timeout=8000)
    except Exception:  # noqa: BLE001
        pass


def _api(page, method: str, path: str, body=None):
    return page.evaluate(
        """({method, path, body}) => fetch(path, {
            method, credentials: 'include',
            headers: body !== null ? {'Content-Type': 'application/json'} : {},
            body: body !== null ? JSON.stringify(body) : undefined,
        }).then(r => r.json())""",
        {"method": method, "path": path, "body": body},
    )


def _ensure_retail_settings_table(page) -> None:
    """retail_settings is created LAZILY at runtime (CLAUDE.md: "Lazy columns
    + migration order" -- NOT by schema.py) -- the first authenticated read
    that touches it (branding, tax settings) creates it."""
    resp = _api(page, "GET", "/api/sub/retail/settings/branding")
    assert (resp or {}).get("status") == "success", f"branding GET failed, retail_settings may not exist yet: {resp}"


def _set_shop_whatsapp_number_via_settings_post(page, raw_number: str) -> dict:
    """Fix round F1 (verifier finding): this used to write directly into
    retail_settings with a second sqlite3 connection, bypassing the real
    Settings write path entirely -- crew T's Settings field posts to
    POST /api/sub/retail/settings/branding, and that route (retail_api.py's
    branding_settings_set) used to silently DROP `shop_whatsapp_number`
    because it was never in `_BRANDING_TEXT_KEYS`. The direct-DB-write
    version of this helper could never have caught that: it wrote past the
    broken route entirely and only ever proved the READ side
    (branding_settings_get -> the QR) worked, which is exactly why D1/D1c
    passed while a real shop's Settings save silently did nothing. Deleted
    that path; every scenario in this file now goes through the SAME
    POST a real owner's browser would use, returns the JSON body so a
    scenario can assert on what the server actually echoed back."""
    return _api(page, "POST", "/api/sub/retail/settings/branding", {"shop_whatsapp_number": raw_number})


def _rect(page, selector: str) -> dict:
    return page.evaluate(
        "(sel) => { const el = document.querySelector(sel); if (!el) return null; "
        "const r = el.getBoundingClientRect(); "
        "return {width: r.width, height: r.height, hidden: !!el.hidden, text: el.textContent}; }",
        selector,
    )


def _run_all(results: Results, screenshots_dir: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            scenarios = [
                ("D1 idle screen with a shop number + promotion",
                 lambda: _scenario_idle_screen_with_number_and_promotion(results, browser, screenshots_dir)),
                ("D2 idle screen with no shop number",
                 lambda: _scenario_idle_screen_with_no_number(results, browser, screenshots_dir)),
                ("D3 standalone page theme",
                 lambda: _scenario_standalone_page_theme_matches_shell(results, browser, screenshots_dir)),
                ("D4 standalone page RTL",
                 lambda: _scenario_standalone_page_rtl_in_arabic(results, browser)),
                ("D5 no-transport copy",
                 lambda: _scenario_no_transport_copy_has_no_env_var_names(results, browser)),
                ("F2 real scroll (wheel + End key)",
                 lambda: _scenario_standalone_pages_are_really_scrollable(results, browser, screenshots_dir)),
                ("F3 exactly 4 report cards, EN and AR",
                 lambda: _scenario_exactly_four_report_cards_en_and_ar(results, browser)),
                ("Item 2: a 20-second promotion hides itself with no sale",
                 lambda: _scenario_promotion_hides_itself_after_its_own_deadline(results, browser, screenshots_dir)),
            ]
            # Each top-level scenario owns/tears down its OWN Backend, so one
            # scenario's setup failure (e.g. a slow sign-up under machine
            # contention from other crews' concurrent backends) must not
            # abort the ones after it -- each is wrapped here individually,
            # in addition to results.run()'s own per-CHECK wrapping inside a
            # scenario, so a harness-level exception (raised before a
            # scenario reaches its first results.run() call) is still
            # recorded and every remaining scenario still gets to run.
            for name, fn in scenarios:
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    results.items.append((name, False, f"scenario-level failure: {e}"))
                    print(f"[FAIL] {name}  (scenario-level) -- {e}")
        finally:
            browser.close()


# ── Scenario 1: idle screen, shop number + promotion set ───────────────────

def _scenario_idle_screen_with_number_and_promotion(results: Results, browser, shots: Path) -> None:
    backend = Backend()
    try:
        backend.start()
        context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_WAIT_MS)

        _sign_up_and_license(page, backend, "P1a QR Test Co")
        _ensure_retail_settings_table(page)

        # Fix round F1: through the REAL Settings POST, not a direct DB
        # write -- see _set_shop_whatsapp_number_via_settings_post's own
        # comment for why the direct write hid this exact bug.
        wa_post = _set_shop_whatsapp_number_via_settings_post(page, SHOP_WHATSAPP_E164)
        assert (wa_post or {}).get("status") == "success", f"real Settings POST failed: {wa_post}"
        assert (wa_post.get("data") or {}).get("shop_whatsapp_number") == SHOP_WHATSAPP_E164, (
            f"the real Settings POST must echo back the saved number -- got {wa_post}"
        )

        cat_resp = _api(page, "POST", "/api/sub/retail/categories", {"name": "Idle Screen Promo Category"})
        cat_id = ((cat_resp or {}).get("data") or {}).get("id")
        assert cat_id, f"category not created: {cat_resp}"
        promo_resp = _api(page, "POST", "/api/sub/retail/promotions", {
            "name": "Idle screen promo", "category_id": cat_id, "discount_pct": 15,
        })
        assert (promo_resp or {}).get("status") == "success", f"promotion not created: {promo_resp}"

        display = context.new_page()
        display.set_default_timeout(DEFAULT_WAIT_MS)
        display.goto(f"{backend.base_url}/static/customer-display.html", wait_until="domcontentloaded")
        display.wait_for_selector("#cd-idle", state="visible", timeout=DEFAULT_WAIT_MS)

        def check_shop_name_rendered():
            # NOT a bare "non-empty text" wait: init() paints the neutral
            # bilingual fallback ('Welcome / ...') SYNCHRONOUSLY, before the
            # async branding fetch ever resolves (customer-display.js's
            # _applyShopName(null), see that method's own comment) -- that
            # text is already non-empty, so a length>0 wait is satisfied
            # immediately and proves nothing about the real name arriving.
            # This waits for the REAL name specifically.
            display.wait_for_function(
                "document.getElementById('cd-shop-name').textContent.includes('P1a QR Test Co')",
                timeout=DEFAULT_WAIT_MS,
            )
            r = _rect(display, "#cd-shop-name")
            assert r and r["width"] > 0 and r["height"] > 0, f"#cd-shop-name has zero size: {r}"
            assert "P1a QR Test Co" in r["text"], f"shop name not rendered, got {r['text']!r}"

        results.run("D1a idle screen shows the shop's own name, non-zero size", check_shop_name_rendered)

        def check_category_scoped_promotion_shows_with_its_scope():
            # F7 follow-up (coordinator instruction): hiding every scoped
            # promotion was honest but defeated the feature -- this promotion
            # (category-scoped, created through the REAL API, the exact
            # shape the verifier's own probe used) must now render WITH its
            # scope named, never as a bare shop-wide-reading claim.
            # retail_api.py's list_active_promotions enriches the row with
            # `category_name` (a company-scoped lookup, same file); the
            # rendered text must contain that category's own name.
            display.wait_for_function(
                "!document.getElementById('cd-promo').hidden && "
                "document.getElementById('cd-promo').textContent.trim().length > 0",
                timeout=DEFAULT_WAIT_MS,
            )
            r = _rect(display, "#cd-promo")
            assert r and r["width"] > 0 and r["height"] > 0, f"#cd-promo has zero size: {r}"
            assert "Idle Screen Promo Category" in r["text"], (
                f"promotion text must name its own category, never a bare shop-wide-reading claim: {r['text']!r}"
            )
            assert "15" in r["text"], f"promotion text missing the discount percentage: {r['text']!r}"

        results.run(
            "D1b idle screen shows a category-scoped promotion WITH its category name (F7 follow-up)",
            check_category_scoped_promotion_shows_with_its_scope,
        )

        def check_qr_matches_server_encoder():
            display.wait_for_function(
                "!document.getElementById('cd-whatsapp').hidden", timeout=DEFAULT_WAIT_MS,
            )
            src = display.locator("#cd-whatsapp-qr").get_attribute("src")
            assert src and src.startswith("data:image/svg+xml;base64,"), f"QR img has no data URI src: {src!r}"

            sys.path.insert(0, str(REPO_ROOT))
            from commercial_runtime.einvoicing.qr import render_qr_svg_data_uri
            digits = SHOP_WHATSAPP_E164.lstrip("+")
            expected = render_qr_svg_data_uri(f"https://wa.me/{digits}")
            # EXACT equality against the canonical encoder, computed independently
            # in THIS process from the SAME payload the server is contracted to
            # build (contracts.md: "Encode https://wa.me/<digits without +>") --
            # not a decode of the pixels, a byte-for-byte proof the server sent
            # exactly what the encoder produces for that payload. See this
            # script's module docstring for why this, not a pixel decoder
            # (segno IS available under this system Python, checked directly;
            # this proof is the stronger of the two the contract offers).
            assert src == expected, (
                "server-rendered QR data URI does not match render_qr_svg_data_uri("
                f"'https://wa.me/{digits}') computed independently -- wrong payload or a different encoder"
            )

        results.run("D1c idle screen WhatsApp QR is byte-identical to render_qr_svg_data_uri(wa.me/<number>)",
                    check_qr_matches_server_encoder)

        try:
            display.screenshot(path=str(shots / "d1_idle_with_number_and_promo.png"))
        except Exception:
            pass
    finally:
        backend.stop()
        backend.cleanup()


# ── Scenario 2: idle screen, no shop number configured -> no QR ────────────

def _scenario_idle_screen_with_no_number(results: Results, browser, shots: Path) -> None:
    backend = Backend()
    try:
        backend.start()
        context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_WAIT_MS)
        _sign_up_and_license(page, backend, "P1a No-Number Co")
        _ensure_retail_settings_table(page)  # deliberately never calls _set_shop_whatsapp_number

        display = context.new_page()
        display.set_default_timeout(DEFAULT_WAIT_MS)
        display.goto(f"{backend.base_url}/static/customer-display.html", wait_until="domcontentloaded")
        display.wait_for_selector("#cd-idle", state="visible", timeout=DEFAULT_WAIT_MS)
        # Waits for the REAL name specifically, not just non-empty text --
        # see D1a's own comment: init() paints a non-empty fallback
        # synchronously, before the branding fetch (which also carries the
        # QR field, had one been set) has even started.
        display.wait_for_function(
            "document.getElementById('cd-shop-name').textContent.includes('P1a No-Number Co')",
            timeout=DEFAULT_WAIT_MS,
        )

        def check_no_qr():
            r = _rect(display, "#cd-whatsapp")
            assert r is not None, "#cd-whatsapp element missing from the DOM entirely"
            assert r["hidden"] is True, f"#cd-whatsapp must stay hidden with no shop number configured, got {r}"
            visible_box = display.evaluate(
                "() => { const el = document.getElementById('cd-whatsapp'); "
                "const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }"
            )
            assert visible_box is False, "no shop number -> no QR, but #cd-whatsapp still occupies visible space"

        results.run("D2 no shop WhatsApp number -> no QR element rendered, no empty box", check_no_qr)

        try:
            display.screenshot(path=str(shots / "d2_idle_no_number.png"))
        except Exception:
            pass
    finally:
        backend.stop()
        backend.cleanup()


# ── Scenario 3: standalone page theme matches the shell's, sand + night ────

def _scenario_standalone_page_theme_matches_shell(results: Results, browser, shots: Path) -> None:
    backend = Backend()
    try:
        backend.start()
        context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_WAIT_MS)
        _sign_up_and_license(page, backend, "P1a Theme Test Co")

        # Fix round D4/theming (coordinator instruction): einvoicing.html and
        # site-relay.html get the SAME theme-parity proof whatsapp.html
        # already had -- all three pages carry the identical page-scoped
        # theme boot + css/main.css load.
        for theme in ("sand", "night"):
            page.evaluate("(t) => localStorage.setItem('aura_theme_v2', t)", theme)
            page.reload(wait_until="domcontentloaded")
            page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            shell_bg = page.evaluate("getComputedStyle(document.body).backgroundColor")

            for pg, wait_sel in (("whatsapp", "#status-content"), ("einvoicing", "#status-content"), ("site-relay", ".back-link")):
                def check(theme=theme, pg=pg, wait_sel=wait_sel, shell_bg=shell_bg):
                    sp = context.new_page()
                    sp.set_default_timeout(DEFAULT_WAIT_MS)
                    sp.goto(f"{backend.base_url}/static/{pg}.html", wait_until="domcontentloaded")
                    sp.wait_for_selector(wait_sel, timeout=DEFAULT_WAIT_MS)
                    assert sp.evaluate("document.documentElement.getAttribute('data-theme')") == theme, (
                        f"{pg}.html did not boot into data-theme={theme!r} from aura_theme_v2"
                    )
                    sp_bg = sp.evaluate("getComputedStyle(document.body).backgroundColor")
                    sp.close()
                    assert sp_bg == shell_bg, (
                        f"{pg}.html body background ({sp_bg!r}) != shell body background ({shell_bg!r}) "
                        f"for theme={theme!r}"
                    )

                results.run(f"D3 {pg}.html body background matches the shell's, theme={theme}", check)

        try:
            page.screenshot(path=str(shots / "d3_theme_shell.png"))
        except Exception:
            pass
    finally:
        backend.stop()
        backend.cleanup()


# ── Scenario 4: dir=rtl on a standalone page in Arabic ──────────────────────

def _scenario_standalone_page_rtl_in_arabic(results: Results, browser) -> None:
    backend = Backend()
    try:
        backend.start()
        context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_WAIT_MS)
        _sign_up_and_license(page, backend, "P1a RTL Test Co")

        def check():
            # Fix round D4 (coordinator finding): a bare dir="rtl"/lang="ar"
            # check is VACUOUS here -- i18n.js sets those two attributes
            # unconditionally from localStorage on EVERY page that merely
            # loads it, whether or not this crew's own fix (loading
            # css/rtl.css, and F3's fix to keep the report cards alive in
            # Arabic) is present or even correct. Replaced with two
            # assertions that only pass BECAUSE of this crew's own changes:
            # (1) the back-link sits on the visual RIGHT in RTL -- this is
            # css/rtl.css's own logical-property mirroring actually taking
            # effect on a page that did not load it before this crew's
            # fix -- and (2) the report cards this crew built still render
            # in Arabic (F3's fix: the container itself must carry no
            # data-i18n, or Arabic wipes them).
            page.evaluate("() => localStorage.setItem('aura_lang', 'ar')")
            wa_page = context.new_page()
            wa_page.set_default_timeout(DEFAULT_WAIT_MS)
            wa_page.goto(f"{backend.base_url}/static/whatsapp.html", wait_until="domcontentloaded")
            wa_page.wait_for_function(
                "document.documentElement.getAttribute('dir') === 'rtl'", timeout=DEFAULT_WAIT_MS,
            )
            dir_attr = wa_page.evaluate("document.documentElement.getAttribute('dir')")
            lang_attr = wa_page.evaluate("document.documentElement.getAttribute('lang')")
            assert dir_attr == "rtl", f"whatsapp.html <html dir> was {dir_attr!r}, expected 'rtl'"
            assert lang_attr == "ar", f"whatsapp.html <html lang> was {lang_attr!r}, expected 'ar'"

            wa_page.wait_for_timeout(1500)  # settle renderReportCards() + the second, racing apply()
            back_rect = wa_page.evaluate(
                "() => { const r = document.querySelector('.back-link').getBoundingClientRect(); "
                "return {left: r.left, right: r.right, vw: innerWidth}; }"
            )
            # "On the right" in RTL: this page's CSS never positions
            # .back-link explicitly (no left/right/inset rule at all -- it
            # is a plain inline element at the top of <body>), so what
            # actually moves it is css/rtl.css's own [dir=rtl] rules
            # mirroring the DOCUMENT FLOW (text-align/direction), which
            # right-aligns inline content. Proven as "closer to the right
            # edge than the left edge", the same shape of check
            # verify_pages.py's own probe already used.
            mid = (back_rect["left"] + back_rect["right"]) / 2
            assert mid > back_rect["vw"] / 2, (
                f"in RTL, the back link must sit on the visual RIGHT (css/rtl.css mirroring) -- got {back_rect}"
            )

            card_count = wa_page.evaluate(
                "[...document.querySelectorAll('#reports-content .report-card')]"
                ".filter(c => c.getBoundingClientRect().height > 0).length"
            )
            assert card_count == 4, f"the 4 report cards must still render in Arabic (F3) -- found {card_count}"

            wa_page.close()

        results.run("D4 in Arabic: dir=rtl AND the back link mirrors right AND the report cards still render", check)
    finally:
        backend.stop()
        backend.cleanup()


# ── Scenario 5: no-transport copy names no env vars ─────────────────────────

def _scenario_no_transport_copy_has_no_env_var_names(results: Results, browser) -> None:
    backend = Backend()
    try:
        backend.start()
        context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_WAIT_MS)
        _sign_up_and_license(page, backend, "P1a Copy Test Co")

        def check():
            wa_page = context.new_page()
            wa_page.set_default_timeout(DEFAULT_WAIT_MS)
            wa_page.goto(f"{backend.base_url}/static/whatsapp.html", wait_until="domcontentloaded")
            # NOT a bare "non-empty text" wait: the markup's own placeholder
            # ("Loading…") is already non-empty before refreshStatus()'s
            # async fetch resolves -- see the same class of bug fixed in
            # D1a/D2 above. Waits for the placeholder to actually be gone.
            wa_page.wait_for_function(
                "document.getElementById('status-content').textContent.trim() !== 'Loading…' && "
                "document.getElementById('status-content').textContent.trim().length > 0",
                timeout=DEFAULT_WAIT_MS,
            )
            body_text = wa_page.evaluate("document.body.textContent")
            wa_page.close()
            # Owner decision (contracts.md, 2026-09-28): never an env-var name,
            # never "your business number" (there is ONE WhatsApp number --
            # Action Aura's own -- not a per-shop one).
            assert "AURA_WHATSAPP" not in body_text, "an env-var name leaked into the rendered page text"
            assert "your business number" not in body_text.lower(), (
                "the old 'Action Aura can set up your business number' promise is still on the page"
            )
            assert "Ask Action Aura to turn them on" in body_text, (
                "the owner-decided no-transport sentence is not on the page"
            )

        results.run("D5 no-transport copy names no env vars and makes no per-shop-number promise", check)
    finally:
        backend.stop()
        backend.cleanup()


# ── Scenario 6 (F2): a REAL mouse wheel + the End key can actually scroll ──

def _scenario_standalone_pages_are_really_scrollable(results: Results, browser, shots: Path) -> None:
    """Fix round F2 (verifier finding): css/main.css's shell-scoped
    `body{overflow:hidden}` made these one-long-page documents unscrollable.
    A scripted `window.scrollTo()` would pass even with the bug present (it
    forces scrollY directly, bypassing whatever the CSS says) -- so this
    drives a REAL mouse wheel (`page.mouse.wheel`) and the real End key
    (`page.keyboard.press`), matching the verifier's own verify_wheel.py."""
    backend = Backend()
    try:
        backend.start()
        context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_WAIT_MS)
        _sign_up_and_license(page, backend, "P1a Scroll Test Co")

        # Item 3(c) (verifier finding): the two input methods must be proven
        # INDEPENDENTLY, each starting from scrollY=0 on a fresh page load --
        # the original version did wheel-then-End, so the End assertion's
        # baseline was already partway down the page and never actually
        # proved End ALONE could reach the bottom from the top.
        for pg in ("whatsapp", "einvoicing"):
            def check_wheel_only(pg=pg):
                wp = context.new_page()
                wp.set_default_timeout(DEFAULT_WAIT_MS)
                wp.goto(f"{backend.base_url}/static/{pg}.html", wait_until="domcontentloaded")
                wp.wait_for_timeout(1500)  # let the async cards/status render so the page reaches its real full height

                scroll_height = wp.evaluate("document.scrollingElement.scrollHeight")
                assert scroll_height > VIEWPORT["height"], (
                    f"{pg}.html is only {scroll_height}px tall at {VIEWPORT['height']}px viewport -- "
                    f"this scenario needs a page that genuinely overflows to prove scrolling at all"
                )
                assert wp.evaluate("window.scrollY") == 0, "must start at the top, unscrolled"

                wp.mouse.move(VIEWPORT["width"] // 2, VIEWPORT["height"] // 2)
                wp.mouse.wheel(0, 3000)
                wp.wait_for_timeout(400)
                y_after_wheel = wp.evaluate("window.scrollY")
                assert y_after_wheel > 0, (
                    f"a real mouse wheel over {pg}.html scrolled nothing (scrollY stayed {y_after_wheel}) -- "
                    f"the body{{overflow:hidden}} bug is back"
                )
                wp.screenshot(path=str(shots / f"f2_{pg}_wheel_only.png"))
                wp.close()

            results.run(f"F2 a real mouse wheel alone scrolls {pg}.html (independent check)", check_wheel_only)

            def check_end_key_only(pg=pg):
                wp = context.new_page()
                wp.set_default_timeout(DEFAULT_WAIT_MS)
                wp.goto(f"{backend.base_url}/static/{pg}.html", wait_until="domcontentloaded")
                wp.wait_for_timeout(1500)
                assert wp.evaluate("window.scrollY") == 0, "must start at the top, unscrolled -- no wheel in this check at all"

                # Focus the page (not a specific control) before End -- the
                # browser's own default scroll-the-document behaviour for a
                # body-focused page, exactly what a real shopkeeper pressing
                # End with nothing selected gets.
                wp.locator("body").click(position={"x": 5, "y": 5})
                wp.keyboard.press("End")
                wp.wait_for_timeout(400)
                y_after_end = wp.evaluate("window.scrollY")
                assert y_after_end > 0, (
                    f"the End key ALONE (no wheel) scrolled nothing on {pg}.html (scrollY stayed {y_after_end})"
                )
                last_card_rect = wp.evaluate(
                    "() => { const cs = [...document.querySelectorAll('.card')]; "
                    "const c = cs[cs.length - 1]; const r = c.getBoundingClientRect(); "
                    "return {top: r.top, bottom: r.bottom, visible: r.top < innerHeight && r.bottom > 0}; }"
                )
                assert last_card_rect["visible"] is True, (
                    f"the LAST card must be reachable by the End key ALONE, from the top: {last_card_rect}"
                )
                wp.screenshot(path=str(shots / f"f2_{pg}_end_key_only.png"))
                wp.close()

            results.run(f"F2 the End key alone reaches the last card on {pg}.html (independent check)", check_end_key_only)
    finally:
        backend.stop()
        backend.cleanup()


# ── Scenario 7 (F3): exactly 4 visible report cards, EN and AR ─────────────

def _scenario_exactly_four_report_cards_en_and_ar(results: Results, browser) -> None:
    backend = Backend()
    try:
        backend.start()
        context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_WAIT_MS)
        _sign_up_and_license(page, backend, "P1a Cards Test Co")

        for lang in ("en", "ar"):
            def check(lang=lang):
                page.evaluate("(l) => localStorage.setItem('aura_lang', l)", lang)
                wp = context.new_page()
                wp.set_default_timeout(DEFAULT_WAIT_MS)
                wp.goto(f"{backend.base_url}/static/whatsapp.html", wait_until="domcontentloaded")
                # Settles BOTH the boot()-driven apply() and i18n.js's own
                # independent, racing DOMContentLoaded-triggered apply() --
                # F3's whole bug was the SECOND one winning the race and
                # wiping the cards; waiting only for the first render would
                # not catch a regression of this exact fix.
                wp.wait_for_timeout(2500)
                count = wp.evaluate(
                    "[...document.querySelectorAll('#reports-content .report-card')]"
                    ".filter(c => c.getBoundingClientRect().height > 0).length"
                )
                wp.close()
                assert count == 4, f"expected exactly 4 visible report cards in lang={lang!r}, found {count}"

            results.run(f"F3 exactly 4 visible report cards render, lang={lang}", check)
    finally:
        backend.stop()
        backend.cleanup()


# ── Scenario 9 (Item 2): a 20-second promotion hides itself with no sale ───

def _scenario_promotion_hides_itself_after_its_own_deadline(results: Results, browser, shots: Path) -> None:
    """Item 2 (verifier fix round): a REAL wall-clock wait, not a mocked
    timer -- customer-display.js's deadline timer must fire on its own,
    with no sale, no cart event, nothing else in this scenario ever
    touching the page after the initial load. 20 seconds (not 2-3) so the
    real gap between "the timer was armed" and "it actually fired" cannot
    be explained by test-harness slack (page load, the ~600ms poll, JS
    event-loop jitter) -- if this passed at 2 seconds it would not
    distinguish a real deadline timer from an accidental coincidence."""
    backend = Backend()
    try:
        backend.start()
        context = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
        page = context.new_page()
        page.set_default_timeout(DEFAULT_WAIT_MS)
        _sign_up_and_license(page, backend, "P1a Timed Promo Co")

        cat_resp = _api(page, "POST", "/api/sub/retail/categories", {"name": "Twenty Second Category"})
        cat_id = ((cat_resp or {}).get("data") or {}).get("id")
        assert cat_id, f"category not created: {cat_resp}"
        # SAME local wall-clock shape/clock the backend's own now_local uses
        # (retail_api.py's list_active_promotions: `datetime.now().strftime(...)`)
        # -- this test process and the backend run on the SAME machine, so
        # one clock read here is exactly as authoritative as the server's own.
        ends_at = (datetime.now() + timedelta(seconds=20)).strftime("%Y-%m-%d %H:%M:%S")
        promo_resp = _api(page, "POST", "/api/sub/retail/promotions", {
            "name": "Twenty Second Sale", "category_id": cat_id, "discount_pct": 30, "ends_at": ends_at,
        })
        assert (promo_resp or {}).get("status") == "success", f"promotion not created: {promo_resp}"

        display = context.new_page()
        display.set_default_timeout(DEFAULT_WAIT_MS)
        # REAL bug this exact scenario caught: the promotion stayed visible
        # forever after its deadline. The deadline timer DID fire (measured),
        # but the refetch returned the same row (the backend keeps a promotion
        # through its final second) and the old 'is it current' check compared
        # ends_at at SECOND precision while the timer-arming code used
        # MILLISECOND precision: the check said 'still current' and the arming
        # code refused to set a new timer, so nothing could ever hide it.
        # Fixed in customer-display.js (_promotionIsCurrent, one definition).
        # An earlier theory here, Chromium background-tab throttling, was
        # WRONG (a raw setTimeout on a second tab fires on time). The
        # bring_to_front() below is kept only because a kiosk's display is the
        # focused surface of its own monitor; it is not what fixed anything.
        display.bring_to_front()
        display.goto(f"{backend.base_url}/static/customer-display.html", wait_until="domcontentloaded")
        display.wait_for_function(
            "!document.getElementById('cd-promo').hidden && "
            "document.getElementById('cd-promo').textContent.includes('Twenty Second Category')",
            timeout=DEFAULT_WAIT_MS,
        )
        display.screenshot(path=str(shots / "item2_promo_visible_before_deadline.png"))

        def check():
            # No interaction at all for the whole window -- exactly the
            # "quiet idle screen" the finding named. 30s real wait: 20s for
            # the promotion's own deadline, +10s slack for the 24h-clamped
            # setTimeout, the JS event loop and this browser's own timer
            # coalescing to actually fire and for _loadPromotion()'s
            # re-fetch to resolve.
            display.wait_for_timeout(30_000)
            hidden = display.evaluate("document.getElementById('cd-promo').hidden")
            assert hidden is True, (
                "a promotion must hide ITSELF once its own ends_at passes, with no sale and no cart event "
                "-- the quiet-idle-screen bug this scenario exists to catch"
            )
            display.screenshot(path=str(shots / "item2_promo_hidden_after_deadline.png"))

        results.run("Item 2: a 20-second promotion hides itself with no sale (real wall-clock wait)", check)
    finally:
        backend.stop()
        backend.cleanup()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep-screens", action="store_true")
    args = parser.parse_args()

    try:
        import playwright  # noqa: F401
    except ImportError:
        print(
            "FATAL: Playwright is not importable under this interpreter "
            f"({sys.executable}). Run with the SYSTEM Python -- see the module docstring.",
            file=sys.stderr,
        )
        return 2

    screenshots_dir = Path(tempfile.gettempdir()) / f"aura_retail_p1a_display_screenshots_{_now()}"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    results = Results()
    try:
        _run_all(results, screenshots_dir)
    except Exception as e:  # noqa: BLE001 -- a harness-level failure must still print what ran so far
        print(f"HARNESS FAILURE: {e}", file=sys.stderr)
        results.items.append(("harness", False, str(e)))

    results.print_summary()
    print(f"Screenshots: {screenshots_dir}")
    return 0 if results.all_passed() else 1


if __name__ == "__main__":
    sys.exit(main())
