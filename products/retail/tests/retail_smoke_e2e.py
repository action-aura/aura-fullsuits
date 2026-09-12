"""Aura Retail -- real-browser end-to-end smoke suite.

============================================================================
STANDALONE SCRIPT. THIS IS **NOT** A PYTEST FILE. DO NOT RUN IT VIA PYTEST.
============================================================================
Playwright is installed under the SYSTEM Python
(``C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe``), NOT
under this repo's pytest venv (``.venv``). Collecting this file with pytest
(which runs under the venv) will fail with
``ModuleNotFoundError: No module named 'playwright'``. Run it directly with
the system interpreter instead:

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_smoke_e2e.py

(paths are resolved from this file's own location, so the current working
directory does not matter).

WHAT THIS DOES
---------------
Boots a REAL Aura Retail Flask backend (the project's own venv Python
running ``products/retail/backend/app.py`` unmodified, exactly the way the
desktop launcher does) as a subprocess, on a free localhost port, against a
brand-new temporary ``AURA_APP_DATA`` directory -- a genuinely fresh
install, every run. It then drives that backend with a REAL Chromium
browser via Playwright: clicking the actual nav, filling actual forms,
reading actual rendered DOM state. It asserts on what the browser painted,
never on source text -- see docs/testing/e2e-smoke-guide.md for the reason
this suite exists and what class of bug it is built to catch that a
source-text assertion structurally cannot.

The backend is always torn down in a ``finally`` block, whether the run
passes, fails, or crashes outright -- a leaked server holds its port and
poisons the next run.

WHAT THIS DOES NOT COVER
-------------------------
See docs/testing/e2e-smoke-guide.md's "What this does not cover" section.
In short: ringing an actual sale, printing, multi-device sync, anything
that requires an activated licence -- all deliberately out of reach on a
fresh install, and this suite proves that restriction rather than working
around it.

EXIT CODE
---------
0 if every scenario passed. Non-zero if any scenario failed, or if the
harness itself could not run at all (no browser binary, backend never
became healthy, etc).
"""

from __future__ import annotations

import argparse
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

# ---------------------------------------------------------------------------
# Paths & fixed configuration
# ---------------------------------------------------------------------------
TESTS_DIR = Path(__file__).resolve().parent
PRODUCT_DIR = TESTS_DIR.parent
BACKEND_DIR = PRODUCT_DIR / "backend"

# Given verbatim in the task brief -- the project's own venv, which has
# Flask/the backend's real dependencies installed (NOT the system Python
# this script itself must run under -- see the module docstring).
VENV_PYTHON = Path("C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe")

HEALTH_TIMEOUT_SECONDS = 30.0
DEFAULT_WAIT_MS = 15_000
VIEWPORT = {"width": 1440, "height": 900}  # wide enough for the sidebar nav,
# not the <=640px phone tab bar -- this suite drives the sidebar the way a
# desktop/POS-till user actually would.


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _find_free_port() -> int:
    """Bind to port 0 to ask the OS for a free ephemeral port, then release
    it immediately so the backend can bind it. Small TOCTOU race (something
    else could grab it in between) -- acceptable for a smoke suite, and the
    task brief explicitly asks for a port picked this way rather than one of
    the hardcoded ports (5000/5010/5011) already in use on this machine."""
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
    raise RuntimeError(
        f"Backend never answered GET {base_url}/api/health within {timeout}s "
        f"(last error: {last_err!r})"
    )


class Backend:
    """Owns exactly one real `<venv python> app.py` subprocess for the run's
    lifetime -- a genuinely fresh install (brand-new AURA_APP_DATA temp dir,
    no licence seeded) on a free port, torn down unconditionally via
    ``stop()``, which the caller MUST invoke from a ``finally`` block."""

    def __init__(self) -> None:
        self.port = _find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self.app_data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_smoke_appdata_"))
        self.log_path = self.app_data_dir / "backend_stdout.log"
        self.proc: subprocess.Popen | None = None
        self._log_file = None

    def start(self) -> None:
        env = os.environ.copy()
        env["AURA_STANDALONE"] = "1"
        env["PORT"] = str(self.port)
        env["AURA_APP_DATA"] = str(self.app_data_dir)
        # Deliberately NOT setting AURA_OWNER_LICENSING_URL or seeding a
        # licence anywhere -- the whole point of this run is a genuinely
        # fresh, unlicensed install (see CLAUDE.md's "An install with no
        # licensing configured is READ-ONLY, not unlocked").
        self._log_file = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [str(VENV_PYTHON), "app.py"],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
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
            raise RuntimeError(
                "Backend failed to become healthy.\n--- backend stdout/stderr tail ---\n"
                + tail
            )

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

    def cleanup_app_data(self) -> None:
        shutil.rmtree(self.app_data_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Console / network observation + scenario bookkeeping
# ---------------------------------------------------------------------------
class Report:
    """Accumulates scenario pass/fail results plus every console error,
    uncaught page error, failed request and non-2xx response observed
    across the WHOLE run -- reported in full at the end regardless of
    whether any given scenario passed, per the task brief's "report every
    console error and every failed network request ... even if you decided
    it was benign"."""

    def __init__(self) -> None:
        self.scenarios: list[tuple[str, bool, str]] = []
        self.console_errors: list[tuple[str, str]] = []   # (scenario, text)
        self.page_errors: list[tuple[str, str]] = []       # (scenario, text)
        self.bad_responses: list[tuple[str, int, str, str]] = []  # (scenario, status, method, url)
        self.request_failures: list[tuple[str, str, str]] = []    # (scenario, url, failure_text)
        self.current_scenario = "startup"
        self.screenshots: list[Path] = []

    # -- wiring -------------------------------------------------------------
    def attach(self, page) -> None:
        def on_console(msg):
            if msg.type == "error":
                self.console_errors.append((self.current_scenario, msg.text))

        def on_pageerror(exc):
            self.page_errors.append((self.current_scenario, str(exc)))

        def on_response(response):
            try:
                status = response.status
            except Exception:
                return
            if status >= 400:
                try:
                    method = response.request.method
                except Exception:
                    method = "?"
                self.bad_responses.append((self.current_scenario, status, method, response.url))

        def on_requestfailed(request):
            failure = request.failure or "unknown failure"
            self.request_failures.append((self.current_scenario, request.url, failure))

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("response", on_response)
        page.on("requestfailed", on_requestfailed)
        page.on("dialog", lambda d: d.dismiss())  # never let a stray confirm()/alert() hang the run

    # -- scenario recording ---------------------------------------------------
    def run(self, name: str, fn, *, strict_console: bool = True) -> None:
        """``strict_console=False`` is for the one scenario that DELIBERATELY
        provokes a blocked-mutation 403 (read-only-honesty): Chromium itself
        logs a "Failed to load resource: ... 403" console error for ANY
        non-2xx fetch/XHR response, regardless of how gracefully the page's
        own JS handles it -- that is a browser-level side effect of
        intentionally triggering the 403, not evidence of an app bug, and
        asserting zero console errors there would be a vacuous, wrong
        assertion. It is still recorded in the full report below either way."""
        self.current_scenario = name
        console_checkpoint = len(self.console_errors)
        pageerror_checkpoint = len(self.page_errors)
        started = time.monotonic()
        try:
            fn()
            new_pageerrors = self.page_errors[pageerror_checkpoint:]
            new_console = self.console_errors[console_checkpoint:]
            if new_pageerrors:
                raise AssertionError(
                    f"{len(new_pageerrors)} uncaught page error(s) during this scenario: "
                    f"{[t for _, t in new_pageerrors]}"
                )
            if strict_console and new_console:
                raise AssertionError(
                    f"{len(new_console)} console error(s) during this scenario: "
                    f"{[t for _, t in new_console]}"
                )
            elapsed = time.monotonic() - started
            self.scenarios.append((name, True, ""))
            print(f"[PASS] {name}  ({elapsed:.2f}s)")
        except Exception as e:  # noqa: BLE001 -- deliberately broad: one scenario's exception must not kill the run
            elapsed = time.monotonic() - started
            detail = str(e)
            self.scenarios.append((name, False, detail))
            print(f"[FAIL] {name}  ({elapsed:.2f}s)\n       {detail}")

    def shot(self, page, screenshots_dir: Path, name: str) -> None:
        path = screenshots_dir / f"{name}.png"
        try:
            page.screenshot(path=str(path), full_page=True)
            self.screenshots.append(path)
        except Exception as e:  # noqa: BLE001 -- a failed screenshot must not fail the scenario it documents
            print(f"       (screenshot '{name}' failed: {e})")

    # -- final report ---------------------------------------------------------
    def print_summary(self, screenshots_dir: Path, wall_seconds: float) -> bool:
        print("\n" + "=" * 78)
        print("SCENARIO RESULTS")
        print("=" * 78)
        passed = sum(1 for _, ok, _ in self.scenarios if ok)
        for name, ok, detail in self.scenarios:
            status = "PASS" if ok else "FAIL"
            line = f"  [{status}] {name}"
            if detail:
                line += f"\n         {detail}"
            print(line)
        print(f"\n{passed}/{len(self.scenarios)} scenarios passed.")
        print(f"Total wall time: {wall_seconds:.2f}s")

        print("\n" + "=" * 78)
        print(f"CONSOLE ERRORS OBSERVED: {len(self.console_errors)}")
        print("=" * 78)
        if not self.console_errors:
            print("  (none)")
        for scenario, text in self.console_errors:
            print(f"  [{scenario}] {text}")

        print("\n" + "=" * 78)
        print(f"UNCAUGHT PAGE ERRORS OBSERVED: {len(self.page_errors)}")
        print("=" * 78)
        if not self.page_errors:
            print("  (none)")
        for scenario, text in self.page_errors:
            print(f"  [{scenario}] {text}")

        print("\n" + "=" * 78)
        print(f"NON-2xx/3xx HTTP RESPONSES OBSERVED: {len(self.bad_responses)}")
        print("=" * 78)
        if not self.bad_responses:
            print("  (none)")
        for scenario, status, method, url in self.bad_responses:
            print(f"  [{scenario}] {status} {method} {url}")

        print("\n" + "=" * 78)
        print(f"FAILED NETWORK REQUESTS OBSERVED: {len(self.request_failures)}")
        print("=" * 78)
        if not self.request_failures:
            print("  (none)")
        for scenario, url, failure in self.request_failures:
            print(f"  [{scenario}] {url} -- {failure}")

        print("\n" + "=" * 78)
        print(f"SCREENSHOTS ({len(self.screenshots)}) -- {screenshots_dir}")
        print("=" * 78)
        for p in self.screenshots:
            print(f"  {p}")

        return passed == len(self.scenarios) and len(self.scenarios) > 0


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------
def _dismiss_intro_overlay_if_present(page) -> None:
    """The first-run brand intro (brand/intro.html) plays AT MOST ONCE EVER,
    gated on a localStorage key -- so on a brand-new browser profile (every
    run of this suite) it WILL play, as a full-screen overlay
    (#aura-intro-overlay, z-index 100000) sitting on top of the setup screen
    underneath. app-shell.js's own contract is "dismissed by a click/tap
    anywhere on it, any keypress, or a ~6.5s timeout" -- clicking it is
    exactly what a real user impatient to get started would do, so that is
    what this does, rather than reaching into localStorage to skip it."""
    try:
        overlay = page.locator("#aura-intro-overlay")
        overlay.wait_for(state="visible", timeout=3000)
        overlay.click()
        overlay.wait_for(state="detached", timeout=3000)
    except Exception:
        pass  # never played, or already gone -- both fine


def main() -> int:
    # Playwright error messages and page content can carry Arabic text (this
    # suite deliberately switches the app to Arabic/RTL) and other non-ASCII
    # characters. A Windows console's default codepage (cp1252) cannot encode
    # them and would crash this script's own error reporting mid-run --
    # exactly the kind of self-inflicted failure this suite must not have.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--headed", action="store_true",
        help="Run Chromium headed instead of headless (debugging aid).",
    )
    parser.add_argument(
        "--keep-app-data", action="store_true",
        help="Do not delete the temporary AURA_APP_DATA directory after the run.",
    )
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "FATAL: Playwright is not importable under this interpreter "
            f"({sys.executable}). This script must be run with the SYSTEM "
            "Python that has Playwright installed -- see the module "
            "docstring at the top of this file for the exact command.",
            file=sys.stderr,
        )
        return 2

    screenshots_dir = Path(tempfile.gettempdir()) / f"aura_retail_smoke_screenshots_{_now()}"
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    wall_start = time.monotonic()
    backend = Backend()
    report = Report()

    try:
        print(f"Booting real backend: {VENV_PYTHON} app.py  (cwd={BACKEND_DIR})")
        print(f"  PORT={backend.port}  AURA_APP_DATA={backend.app_data_dir}")
        backend.start()
        print(f"Backend healthy at {backend.base_url}")
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        backend.stop()
        if not args.keep_app_data:
            backend.cleanup_app_data()
        return 2

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=not args.headed)
            except Exception as e:
                print(
                    "FATAL: could not launch a Chromium browser binary. If "
                    "`playwright install` has never downloaded one, this "
                    "suite cannot run without a (potentially large) "
                    f"download -- not attempted here. Underlying error: {e}",
                    file=sys.stderr,
                )
                return 2

            try:
                context = browser.new_context(
                    viewport=VIEWPORT,
                    reduced_motion="reduce",  # shortens the intro overlay's own fallback timer; we still click to dismiss it immediately
                )
                page = context.new_page()
                page.set_default_timeout(DEFAULT_WAIT_MS)
                report.attach(page)

                _run_all_scenarios(page, backend, report, screenshots_dir)
            finally:
                browser.close()
    finally:
        backend.stop()
        if not args.keep_app_data:
            backend.cleanup_app_data()
        else:
            print(f"AURA_APP_DATA kept at: {backend.app_data_dir}")

    wall_seconds = time.monotonic() - wall_start
    all_passed = report.print_summary(screenshots_dir, wall_seconds)
    return 0 if all_passed else 1


def _run_all_scenarios(page, backend: Backend, report: Report, shots: Path) -> None:
    base_url = backend.base_url

    # ------------------------------------------------------------------
    # Scenario 1 -- the shell boots with no uncaught errors.
    # ------------------------------------------------------------------
    def scenario_shell_boots():
        page.goto(base_url, wait_until="domcontentloaded")
        _dismiss_intro_overlay_if_present(page)
        # Real assertion, not vacuous: a JS syntax error anywhere in the
        # boot chain (index.html's own history section documents exactly
        # this failure mode -- a redeclared global killing app-shell.js
        # before its first statement) leaves window.SubsystemApp undefined.
        defined = page.evaluate("typeof window.SubsystemApp")
        assert defined == "object", f"window.SubsystemApp is {defined!r}, expected 'object'"
        assert page.evaluate("typeof window.AuraI18n") == "object", "window.AuraI18n did not load"
        report.shot(page, shots, "01_shell_boot")

    report.run("01 app shell boots with no uncaught JS errors", scenario_shell_boots)

    # ------------------------------------------------------------------
    # Scenario 2 -- fresh-DB first-run signup surface appears, and works.
    #
    # NOTE on "the nav renders" (part of the task's scenario 1 wording):
    # on a genuinely fresh install this app renders NO nav at all before
    # authentication -- init() returns straight after _openFirstRun() when
    # /api/onboarding/status says needs_setup (confirmed by reading
    # app-shell.js's init(), not assumed). There is no nav to assert on
    # until an admin account exists. That assertion is therefore made here,
    # once signup actually completes, rather than faked by skipping
    # onboarding -- this scenario proves BOTH halves: the first-run surface
    # is what a fresh DB actually shows, AND the nav genuinely renders once
    # you get past it.
    # ------------------------------------------------------------------
    def scenario_first_run_and_signup():
        modal = page.locator("#aura-relogin-modal")
        modal.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        title = page.locator("#su-title").inner_text()
        assert "Welcome" in title, f"unexpected first-run title: {title!r}"
        for field_id in ("su-name", "su-email", "su-pass", "su-pass2", "su-btn"):
            assert page.locator(f"#{field_id}").count() == 1, f"missing first-run field #{field_id}"
        # A licence-key field must NOT be forced on an install with no Owner
        # configured (CLAUDE.md: "invisible unless opted in") -- pin that too.
        assert page.locator("#su-key").count() == 0, "unexpected license-key field on an unconfigured install"
        report.shot(page, shots, "02_first_run_signup_form")

        page.fill("#su-name", "Smoke Test Admin")
        page.fill("#su-company", "Smoke Test Co")
        page.fill("#su-email", "smoke-admin@example.test")
        page.fill("#su-pass", "SmokeTest123!")
        page.fill("#su-pass2", "SmokeTest123!")
        page.click("#su-btn")

        # Real nav assertion: fails if the shell stayed blank or stuck.
        page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        nav_count = page.locator(".sub-nav-item").count()
        assert nav_count >= 1, "sidebar nav rendered with zero items after signup"
        header = page.locator("#sub-header-section").inner_text()
        assert header == "Dashboard", f"expected to land on Dashboard, header shows {header!r}"
        report.shot(page, shots, "03_dashboard_after_signup")

    report.run("02 fresh-install first-run signup surface, and completing it", scenario_first_run_and_signup)

    # ------------------------------------------------------------------
    # Scenario 3 -- claim this device as the admin device (real UI banner),
    # which is what actually unlocks the Settings nav entry. Discovered by
    # reading app-shell.js's own devices/me flow: a brand-new admin account
    # does NOT automatically become the admin device -- GET /api/devices/me
    # answers is_admin_device:false until this banner's action is taken.
    # ------------------------------------------------------------------
    def scenario_claim_admin_device():
        assert page.locator('.sub-nav-item[data-section="admin-center"]').count() == 0, (
            "Settings nav entry was visible BEFORE claiming this device as admin -- "
            "expected it hidden until the claim banner's action is taken"
        )
        banner = page.locator("#aura-admin-device-claim")
        banner.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        assert "not yet your store" in banner.inner_text()
        report.shot(page, shots, "04_claim_admin_device_banner")

        page.get_by_role("button", name="Make this the admin device", exact=True).click()
        banner.wait_for(state="detached", timeout=DEFAULT_WAIT_MS)

        settings_entry = page.locator('.sub-nav-item[data-section="admin-center"]')
        settings_entry.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        assert settings_entry.count() == 1, "Settings nav entry did not appear after claiming admin device"

    report.run("03 claim admin device via the real banner (unlocks Settings)", scenario_claim_admin_device)

    # ------------------------------------------------------------------
    # Scenario 4 -- navigate every major screen through the real sidebar,
    # asserting a concrete rendered element per screen, capturing a
    # screenshot, and requiring zero new console errors per navigation.
    # ------------------------------------------------------------------
    SCREENS = [
        ("dashboard", ".rdash-answer-value", None),
        ("products", "#prod-table", "Products & Inventory"),
        ("customers", "#cust-table", "Customers"),
        ("reports", "#rep-rev", "Analytics & Reports"),
        ("admin-center", "#branding-card", "Settings"),
    ]

    def scenario_navigate_every_screen():
        for section_id, must_exist_selector, title_text in SCREENS:
            nav_item = page.locator(f'.sub-nav-item[data-section="{section_id}"]')
            assert nav_item.count() == 1, f"nav item for '{section_id}' not found in the sidebar"
            nav_item.click()
            page.locator(must_exist_selector).first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            assert page.locator(must_exist_selector).count() >= 1, (
                f"'{section_id}' screen did not render its expected element {must_exist_selector!r}"
            )
            if section_id == "dashboard":
                # SUITE ROBUSTNESS, not a product fix: `#r-k-rev` (class
                # `.rdash-answer-value`, this SCREENS entry's own
                # must_exist_selector) exists with a '-' placeholder the
                # instant _renderDashboard sets innerHTML SYNCHRONOUSLY --
                # its `GET /dashboard/stats` settles separately, afterwards,
                # and writes the real figures into it and #r-k-cust etc.
                # Moving on to the next screen as soon as the placeholder is
                # merely VISIBLE (the generic wait above) can click away
                # while that fetch is still in flight; when it then resolves
                # against a DOM that has moved on, _renderDashboard's
                # un-generation-guarded `document.getElementById('r-k-cust')
                # .textContent = ...` throws on a null element -- observed
                # here directly, same stack frame subsystem-retail.js:1729,
                # as the stale-render race scenario 10 exists to investigate.
                # This scenario's own job is proving plain navigation is
                # clean, not re-probing that race, so it waits for the real
                # figure to land before moving on -- a real wait-for-condition,
                # not a sleep, and not a weakened assertion: the race itself
                # stays fully assertable on its own terms in scenario 10.
                page.wait_for_function(
                    "document.getElementById('r-k-rev') && "
                    "document.getElementById('r-k-rev').textContent.trim() !== '—'",
                    timeout=DEFAULT_WAIT_MS,
                )
            if title_text is not None:
                actual = page.locator(".ret-title").first.inner_text()
                assert actual == title_text, f"'{section_id}' screen title was {actual!r}, expected {title_text!r}"
            report.shot(page, shots, f"05_screen_{section_id}")

    report.run("04 navigate every major screen via the real sidebar", scenario_navigate_every_screen)

    # ------------------------------------------------------------------
    # Scenario 5 -- anti-vacuity self-check, AND a regression guard for a
    # real bug that has since been fixed: "Stock Transfers" used to have a
    # full nav entry defined in app-shell.js (icon, label, capability gate)
    # and a complete render function/table in subsystem-retail.js, but was a
    # member of NEITHER `navGroups` (sidebar) NOR `_TAB_BAR_SECTIONS` (phone
    # tab bar), making it unreachable from the real UI by any click a user
    # could make. `transfers` is now listed in the Stock group of
    # `systems.retail.navGroups` (app-shell.js), so the sidebar renders it
    # and it must stay reachable and renderable going forward. The self-check
    # half (bogus selector must read 0, a real one must read >=1) is
    # preserved unchanged -- it is what proves the harness can tell a broken
    # page from a working one, independent of this specific finding.
    # ------------------------------------------------------------------
    def scenario_self_check_and_transfers_reachable():
        bogus = page.locator("#definitely-does-not-exist-e2e-selftest")
        assert bogus.count() == 0, "self-check FAILED: a selector that cannot exist was found -- the harness cannot be trusted"
        known_good = page.locator("#sub-content")
        assert known_good.count() >= 1, "self-check FAILED: a selector that must exist was not found"

        transfers_nav = page.locator('.sub-nav-item[data-section="transfers"]')
        assert transfers_nav.count() == 1, (
            "'Stock Transfers' nav item is NOT reachable from the sidebar -- "
            "it must be listed in the Stock group of systems.retail.navGroups "
            "(app-shell.js) for the sidebar to render it; this used to be a real bug, "
            "so if this regresses, the fix has been reverted"
        )
        transfers_nav.click()
        page.locator("#transfer-table").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        transfers_title = page.locator(".ret-title").first.inner_text()
        assert transfers_title == "Stock Transfers", (
            f"transfers screen title was {transfers_title!r}, expected 'Stock Transfers' -- "
            "the nav entry exists but does not actually navigate/render"
        )
        report.shot(page, shots, "05_screen_transfers")

        products_nav_still_there = page.locator('.sub-nav-item[data-section="products"]')
        assert products_nav_still_there.count() == 1, "sanity check: a real, reachable nav item vanished too"

    report.run(
        "05 self-check (bogus vs real selector) + Stock Transfers is reachable and renders",
        scenario_self_check_and_transfers_reachable,
    )

    # ------------------------------------------------------------------
    # Scenario 6 -- empty-state tables render honestly (not a broken/blank
    # table) on a fresh install that has zero products and zero customers.
    # ------------------------------------------------------------------
    def scenario_empty_states():
        page.locator('.sub-nav-item[data-section="products"]').click()
        page.locator("#prod-table tbody").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.wait_for_function(
            "document.querySelector('#prod-table tbody') && "
            "!document.querySelector('#prod-table tbody').textContent.includes('Loading')",
            timeout=DEFAULT_WAIT_MS,
        )
        prod_body = page.locator("#prod-table tbody").inner_text()
        assert "No products found." in prod_body, f"products empty state not shown, tbody was: {prod_body!r}"

        page.locator('.sub-nav-item[data-section="customers"]').click()
        page.locator("#cust-table tbody").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.wait_for_function(
            "document.querySelector('#cust-table tbody') && "
            "!document.querySelector('#cust-table tbody').textContent.includes('Loading')",
            timeout=DEFAULT_WAIT_MS,
        )
        cust_body = page.locator("#cust-table tbody").inner_text()
        assert "No customers found." in cust_body, f"customers empty state not shown, tbody was: {cust_body!r}"
        report.shot(page, shots, "06_empty_states")

    report.run("06 empty-state tables render honestly (zero products, zero customers)", scenario_empty_states)

    # ------------------------------------------------------------------
    # Scenario 7 -- the read-only/licence-restricted state is surfaced
    # honestly. A fresh install with no licence is READ-ONLY (CLAUDE.md,
    # measured): every mutation route answers 403 LICENSE_INACTIVE. There
    # is no product to add to a cart (creating one is itself a blocked
    # mutation), so "toward a sale" here means the real first mutation a
    # cashier journey requires -- adding a product via the real "+ Add
    # Product" form -- and asserting the user is told something truthful,
    # not left at a dead button or a silent failure. Whatever actually
    # happens is pinned as an assertion, not assumed from reading the code.
    # ------------------------------------------------------------------
    def scenario_readonly_honesty():
        page.locator('.sub-nav-item[data-section="products"]').click()
        page.locator("#prod-table").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.click('button[onclick="RetailSystem._openAddProduct()"]')
        page.locator("#pm-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.fill("#pm-name", "Smoke Test Product")
        page.fill("#pm-sku", "SMOKE-001")
        save_btn = page.locator("#pm-save-btn")
        original_label = save_btn.inner_text()
        save_btn.click()

        # A truthful toast (SubsystemApp.showToast -- see app-shell.js) is a
        # plain <div> with no id/class carrying `d.message` as its text.
        # `require_license_capability`'s denial response (commercial_runtime/
        # licensing_contracts/flask_guard.py) always carries this EXACT
        # hardcoded message regardless of the specific denial_reason -- read
        # from that source file, not guessed -- so matching it verbatim is a
        # real assertion on rendered text, not a source-text cheat: it fails
        # if the toast never appears, is empty, or says anything else.
        toast = page.get_by_text("This action is not available in the current licensing state.")
        toast.wait_for(state="visible", timeout=4000)
        toast_text = toast.inner_text().strip()
        assert toast_text, "a toast element appeared but carried no visible text -- a silent failure by another name"
        print(f"       >>> observed read-only-state message shown to the user: {toast_text!r}")

        # The button must not be left permanently stuck disabled/"Saving...":
        # that IS the dead-button failure mode this scenario exists to catch.
        page.wait_for_function(
            "document.getElementById('pm-save-btn') && !document.getElementById('pm-save-btn').disabled",
            timeout=4000,
        )
        final_label = save_btn.inner_text()
        print(f"       >>> Add Product button label before={original_label!r} after-refusal={final_label!r}")

        page.locator("#ret-prod-modal").evaluate("(el) => el.remove()")

    report.run(
        "07 read-only/licence-restricted state is surfaced honestly (not silent, not a dead button)",
        scenario_readonly_honesty,
        strict_console=False,  # this scenario deliberately provokes a 403 -- see Report.run's own docstring
    )

    # ------------------------------------------------------------------
    # FIXED, verified by driving the real browser (not by reading source):
    # app-shell.js's `_renderLicenseBanner` still appends
    # `#aura-license-banner` on every licence-blocked state (NOT_CONFIGURED
    # included -- our fresh install is in that state the whole run), but
    # `.page` (css/main.css) now sits at `top: var(--top-banner-inset, 0px)`
    # instead of a hardcoded `top: 0`, and `_reflowTopBanners()`
    # (app-shell.js) publishes the live banner height into that custom
    # property whenever the banner is shown/hidden/resized. Layout space is
    # therefore reserved for the banner instead of the banner floating on
    # top of `.sub-header` -- the header's theme and language buttons must
    # be clear of it and reachable by a real, unforced click. Measured below
    # with getBoundingClientRect, not inferred from a click succeeding alone.
    # ------------------------------------------------------------------
    def _rects_overlap(a, b) -> bool:
        return not (a["right"] <= b["left"] or b["right"] <= a["left"]
                    or a["bottom"] <= b["top"] or b["bottom"] <= a["top"])

    def scenario_license_banner_clear_of_header():
        banner = page.locator("#aura-license-banner")
        banner.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        assert "not licensed to ring new sales" in banner.inner_text()

        theme_btn = page.locator('button[onclick="ThemeEngine.openPicker()"]')
        banner_rect = banner.evaluate("(el) => el.getBoundingClientRect().toJSON()")
        theme_rect = theme_btn.evaluate("(el) => el.getBoundingClientRect().toJSON()")
        overlap = _rects_overlap(banner_rect, theme_rect)
        print(f"       >>> #aura-license-banner rect: {banner_rect}")
        print(f"       >>> theme button rect:         {theme_rect}")
        print(f"       >>> geometrically overlapping: {overlap}")
        assert not overlap, (
            "expected the license banner to no longer overlap the header now that "
            "'.page' reserves space via --top-banner-inset (_reflowTopBanners()) -- "
            "if this overlaps again, that fix has regressed"
        )

        # Confirm with a REAL, UNFORCED click exactly the way a real user's
        # mouse click would be evaluated -- must succeed now that nothing
        # covers the header buttons, pinning the fix as an observed
        # behaviour rather than an assumption from the geometry alone.
        theme_btn.click(timeout=4000)
        page.locator("#theme-picker-panel").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        print("       >>> CONFIRMED: a real (unforced) click on the theme button opens the picker -- no longer intercepted by #aura-license-banner")
        report.shot(page, shots, "07_license_banner_clear_of_header")

        # Leave the picker closed again so later scenarios start clean.
        page.click("#theme-picker-backdrop")
        page.locator("#theme-picker-panel").wait_for(state="detached", timeout=DEFAULT_WAIT_MS)

    report.run(
        "08 license banner no longer overlaps or blocks header buttons",
        scenario_license_banner_clear_of_header,
    )

    # ------------------------------------------------------------------
    # Scenario 9 -- theme switching. The picker is opened with a real,
    # unforced click on the header button, exactly like scenario 08 just
    # confirmed a real user could now do -- no more direct JS invocation or
    # `force=True` workaround needed, now that the license banner no longer
    # covers the header. Once open, the panel (z-index 9999, anchored 72px
    # from the top -- below the 64px-tall banner, per css/main.css) and its
    # swatches are unambiguously real, unforced clicks too.
    #
    # FIXED, verified by driving the real browser: switching the theme used
    # to discard whatever screen was open. `ThemeEngine.apply()` used to
    # re-render via `SubsystemApp._navigate(SubsystemApp.active)` -- but
    # `.active` is the ACTIVE SUBSYSTEM id ('retail'), not the current
    # SECTION id -- so subsystem-retail.js's `render(sectionId)` switch had
    # no 'retail' case and fell to the generic default branch: `<h2>retail
    # </h2><p>Coming soon.</p>`, discarding the actual screen. It now calls
    # `_navigate(SubsystemApp.currentSection || 'dashboard')` instead, so the
    # screen the user was on must still be showing after a theme switch.
    # This must hold on EVERY theme switch, on every install, not only an
    # unlicensed one -- unrelated to the license-banner fix above.
    # ------------------------------------------------------------------
    def scenario_theme_switch_preserves_current_screen():
        page.locator('.sub-nav-item[data-section="products"]').click()
        page.locator("#prod-table").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

        page.click('button[onclick="ThemeEngine.openPicker()"]')
        page.locator("#theme-picker-panel").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.click('.theme-swatch[data-theme="dark"]')
        page.wait_for_function("document.documentElement.getAttribute('data-theme') === 'dark'", timeout=DEFAULT_WAIT_MS)
        report.shot(page, shots, "08_theme_dark")

        # Real assertion, not vacuous: this fails immediately if the screen
        # ever regresses back to the generic "Coming soon." placeholder.
        page.locator("#prod-table").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        content_text = page.locator("#sub-content").inner_text()
        print(f"       >>> #sub-content after switching theme while on Products: {content_text[:120]!r}")
        assert "Coming soon." not in content_text, (
            "theme switch discarded the current screen ('Coming soon.') -- the "
            "ThemeEngine.apply() -> _navigate(currentSection || 'dashboard') fix has regressed"
        )
        assert page.locator("#prod-table").count() >= 1, (
            "Products screen did not survive the theme switch -- expected #prod-table to still be rendered"
        )
        report.shot(page, shots, "08b_theme_switch_preserved_current_screen")

        for theme in ("sand", "light"):
            page.click(f'.theme-swatch[data-theme="{theme}"]')
            page.wait_for_function(
                f"document.documentElement.getAttribute('data-theme') === '{theme}'",
                timeout=DEFAULT_WAIT_MS,
            )
        page.click("#theme-picker-backdrop")
        page.locator("#theme-picker-panel").wait_for(state="detached", timeout=DEFAULT_WAIT_MS)

    report.run(
        "09 theme switching preserves the current screen (no longer discards it)",
        scenario_theme_switch_preserves_current_screen,
    )

    # ------------------------------------------------------------------
    # THIRD REAL BUG, found here: revisiting Dashboard after scenario 09's
    # theme-switch chain can throw inside `_renderDashboard`
    # (subsystem-retail.js) --
    #   TypeError: Cannot set properties of null (setting 'textContent')
    #   at Object._renderDashboard (subsystem-retail.js:1729)
    # -- on `document.getElementById('r-k-cust').textContent = ...`. Reading
    # that function: it sets `#sub-content`'s innerHTML (including
    # `#r-k-cust`) SYNCHRONOUSLY, then `await`s
    # `GET /api/sub/retail/dashboard/stats`, and writes the response into
    # `#r-k-cust` etc. only once that resolves -- with no check that this
    # render is still the current one. A stats fetch from an EARLIER
    # Dashboard visit (this suite visits Dashboard at least twice before
    # this point) that is still in flight when the user has since navigated
    # elsewhere several times resolves late and writes into elements that
    # belonged to whichever screen was current when IT was issued, not the
    # screen current now -- exactly the missing-render-generation-guard
    # shape. app-shell.js's own `_navigate` catches the throw and swaps in
    # its generic "Failed to load" screen (console.error, not an uncaught
    # page error) rather than crashing the tab -- so the user is never left
    # on a totally broken page, but Dashboard silently fails to load on
    # this revisit when it happens.
    #
    # HONESTLY NON-DETERMINISTIC, measured, not assumed: while building this
    # suite it reproduced with an identical message and stack trace on 2 of
    # 3 independent full runs; the 3rd run's Dashboard revisit loaded
    # cleanly. That is exactly what an unguarded stale-fetch race looks
    # like -- timing-dependent, not "sometimes the bug exists". This
    # scenario therefore does NOT assert the crash is guaranteed (that would
    # make THIS suite flaky over the product's own race, which is a
    # different problem from the race itself) -- it accepts either real,
    # rendered outcome and reports loudly which one happened, so the
    # crash's real, intermittent existence is never lost even on a run
    # where it doesn't fire. Root-caused only as far as pinning the
    # reproduction and the exact throw site; the render-generation guard
    # itself is a fix for whoever owns subsystem-retail.js, not this suite.
    #
    # ADDENDUM, found while re-verifying the fixes above: this suite's OWN
    # earlier scenario 4 was itself one source of the staleness -- it used
    # to click from Dashboard to Products the instant `.rdash-answer-value`'s
    # synchronous placeholder appeared, without waiting for THIS visit's own
    # stats fetch to settle, which is exactly the shape that produces a late
    # write into a torn-down element. Scenario 4 (and this scenario's own
    # tail, below) now wait for that fetch to settle before moving on, purely
    # to stop THIS HARNESS from manufacturing the race through its own click
    # speed. Measured across 5 consecutive runs after that hardening, the
    # crash did not reproduce once here -- so the "2 of 3" rate above is no
    # longer current. That is NOT evidence the underlying bug is fixed
    # (there is still no render-generation guard in `_renderDashboard`,
    # unchanged) -- it means this suite got quieter about a bug it was
    # partly causing itself. This scenario still exercises the real
    # navigation path and still honestly reports whichever outcome occurs;
    # it is left in place, unweakened, in case a slower machine, a slower
    # network, or a future change reopens the timing window.
    # ------------------------------------------------------------------
    def scenario_dashboard_stale_render_race():
        page.locator('.sub-nav-item[data-section="dashboard"]').click()
        # The generic per-section error boundary (app-shell.js's `_navigate`
        # catch block) renders a "Failed to load" heading on the crash path;
        # a clean load shows the ordinary `.rdash-answer-value` KPI figure.
        # Either is a real, non-blank rendered outcome.
        page.wait_for_function(
            "document.querySelector('.rdash-answer-value') || "
            "(document.getElementById('sub-content') && "
            " document.getElementById('sub-content').textContent.includes('Failed to load'))",
            timeout=DEFAULT_WAIT_MS,
        )
        content_text = page.locator("#sub-content").inner_text()
        crashed = "Failed to load" in content_text
        if crashed:
            print("       >>> REPRODUCED this run: Dashboard crashed on revisit (stale-render race) -- see docs/testing/e2e-smoke-guide.md")
            report.shot(page, shots, "10_dashboard_stale_render_race_reproduced")
        else:
            assert page.locator(".rdash-answer-value").count() >= 1, "neither the crash nor a healthy dashboard rendered"
            # Wait for THIS visit's own stats fetch to actually settle (not
            # just the synchronous '-' placeholder) before the scenario ends
            # -- scenario 11 clicks straight to Customers next, and without
            # this the same race documented above could bleed forward and
            # surface as a console error under scenario 11 instead of here.
            # See scenario 4's identical wait for the full mechanism.
            page.wait_for_function(
                "document.getElementById('r-k-rev') && "
                "document.getElementById('r-k-rev').textContent.trim() !== '—'",
                timeout=DEFAULT_WAIT_MS,
            )
            print("       >>> did not reproduce this run (known intermittent race, see docs/testing/e2e-smoke-guide.md) -- Dashboard loaded normally")
            report.shot(page, shots, "10_dashboard_after_theme_switch_ok_this_run")

    report.run(
        "10 REAL BUG (intermittent): revisiting Dashboard after a theme switch can throw (stale-render race)",
        scenario_dashboard_stale_render_race,
    )

    # ------------------------------------------------------------------
    # Scenario 11 -- Arabic/RTL toggle engine itself. `#aura-lang-toggle`
    # sits in the same banner-covered header strip as the theme button (same
    # rect-overlap proven in scenario 08), so this uses the same direct-JS-
    # invocation approach (`AuraI18n.toggle()`), for the same reason.
    # Navigates to Customers first, deliberately NOT Dashboard: scenario 10
    # just pinned a real, unrelated Dashboard-specific bug, and this
    # scenario's own job is RTL, not a second collision with that one.
    # ------------------------------------------------------------------
    def scenario_rtl_toggle():
        page.locator('.sub-nav-item[data-section="customers"]').click()
        page.locator("#cust-table").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

        assert page.evaluate("document.documentElement.getAttribute('dir')") == "ltr"
        page.evaluate("() => AuraI18n.toggle()")
        page.wait_for_function("document.documentElement.getAttribute('dir') === 'rtl'", timeout=DEFAULT_WAIT_MS)
        assert page.evaluate("document.documentElement.getAttribute('lang')") == "ar"
        # Still a real, populated UI, not a blank RTL shell:
        assert page.locator(".sub-nav-item").count() >= 1, "nav disappeared after switching to Arabic/RTL"
        report.shot(page, shots, "11_rtl_arabic")

        page.evaluate("() => AuraI18n.toggle()")
        page.wait_for_function("document.documentElement.getAttribute('dir') === 'ltr'", timeout=DEFAULT_WAIT_MS)
        assert page.evaluate("document.documentElement.getAttribute('lang')") == "en"

    report.run("11 Arabic/RTL toggle flips direction and keeps rendering", scenario_rtl_toggle)


if __name__ == "__main__":
    sys.exit(main())
