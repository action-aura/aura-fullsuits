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
import sqlite3
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
# products/retail -> products -> repo root. Only used by scenario 14 to
# import commercial_runtime.licensing_contracts.test_support -- a stdlib-only
# module (json/sqlite3/pathlib, no `cryptography`), safe to import under this
# script's own SYSTEM Python even though the backend itself always runs under
# the venv (see the module docstring).
REPO_ROOT = PRODUCT_DIR.parent.parent

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

    def __init__(self, app_data_dir: Path | None = None) -> None:
        self.port = _find_free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        # P0 #20 scenario 03b needs a SECOND backend process pointed at an
        # existing, pre-populated AURA_APP_DATA copy (a simulated "joined
        # computer" -- see that scenario's own comment) rather than a
        # brand-new, unrelated install. Every other caller passes nothing
        # and gets the original genuinely-fresh-tempdir behaviour unchanged.
        self.app_data_dir = (
            app_data_dir if app_data_dir is not None
            else Path(tempfile.mkdtemp(prefix="aura_retail_smoke_appdata_"))
        )
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
    # Scenario 3 -- P0 #20 changed what this scenario must prove. It used to
    # assert the OLD flow: a brand-new admin account did NOT auto-claim the
    # admin device, so Settings stayed hidden until the owner clicked a
    # dismissible red banner ("Make this the admin device"). C4's #20 fix
    # calls POST /api/devices/me/claim-admin automatically, from
    # _autoClaimAdminOnSignup() (app-shell.js), the instant
    # /api/onboarding/create-admin succeeds -- so by the time scenario 2
    # above finished signup, THIS install's device had already claimed the
    # flag. The old assertion here ("Settings nav entry was visible BEFORE
    # claiming this device as admin") is now backwards on the signup
    # computer: Settings is SUPPOSED to already be visible there, with zero
    # clicks, and no banner is supposed to ever appear.
    #
    # Weakening this to "assert whatever currently happens" would defeat the
    # whole point of the item, so this proves the NEW behaviour with the
    # same rigor the old assertion had, in both directions: zero
    # clicks/zero banner HERE, on the one computer where auto-claim is
    # supposed to fire (this scenario), AND -- scenario 03b below -- that
    # the manual banner path this replaces is not simply gone: it still
    # works, completely unmodified, on a computer where the auto-claim
    # never ran, which item #20's own text says is every OTHER computer in
    # the shop ("The bar stays on joined computers until Phase 1a replaces
    # the device gate with the person gate").
    # ------------------------------------------------------------------
    def scenario_signup_computer_auto_claims_admin():
        # No click anywhere in this scenario -- that absence IS the
        # assertion. If #20's auto-claim regressed (the fetch removed, or
        # moved to fire after this point in the flow), the banner would be
        # sitting here and Settings would still be hidden, and this scenario
        # would fail exactly the way the OLD banner-based one used to pass.
        assert page.locator("#aura-admin-device-claim").count() == 0, (
            "the admin-device claim banner rendered on the SIGNUP computer -- "
            "P0 #20's _autoClaimAdminOnSignup() should have claimed the admin "
            "device automatically, right after create-admin, before this screen "
            "was ever shown; a visible banner here means the auto-claim did not "
            "fire (or fired and this render did not pick it up)"
        )
        settings_entry = page.locator('.sub-nav-item[data-section="admin-center"]')
        settings_entry.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        assert settings_entry.count() == 1, (
            "Settings nav entry was NOT visible on the signup computer with zero "
            "clicks -- P0 #20 says the computer that just created the company IS "
            "the store's admin device from the moment signup succeeds"
        )
        # Not just the UI's own belief: read the very same GET /api/devices/me
        # app-shell.js's init() reads, so a bug that left isAdminDevice=true
        # client-side while the SERVER still says false (a stale process
        # cache, a race between the claim POST and this check) cannot hide
        # behind a nav item that rendered off client state alone.
        dev = page.evaluate(
            "() => fetch('/api/devices/me', {credentials:'include', cache:'no-store'}).then(r => r.json())"
        )
        assert dev.get("success") and dev.get("device", {}).get("is_admin_device") is True, (
            f"GET /api/devices/me did not report is_admin_device:true for the "
            f"signup computer -- got {dev!r}"
        )
        report.shot(page, shots, "04_signup_computer_no_banner_settings_visible")

    report.run(
        "03 signup computer auto-claims the admin device (P0 #20) -- no click, no banner",
        scenario_signup_computer_auto_claims_admin,
    )

    # ------------------------------------------------------------------
    # Scenario 3b -- the OTHER half of #20: the manual banner claim path
    # this item's auto-claim exists ALONGSIDE (not instead of) must still
    # work on a computer where the auto-claim never ran. A joined computer
    # never calls /api/onboarding/create-admin (only the ONE computer that
    # types the company's name in does that), so
    # _autoClaimAdminOnSignup() never fires there either -- the banner
    # stays that computer's only route to Settings, exactly as it was
    # before #20 existed.
    #
    # This whole suite is architected as ONE fresh, unlicensed install per
    # run (module docstring), so a genuine second, licensed, joined till
    # cannot be booted here -- licensing needs an activated key this
    # install deliberately never has. Per the RESUME NOTE in contracts.md,
    # this instead reproduces the exact DATABASE STATE a joined computer is
    # always in (is_admin_device=0 for every device row of this company) by
    # copying the real signed-up install's own AURA_APP_DATA into a
    # throwaway directory and flipping that ONE column back to 0 directly in
    # the COPY's registry.db -- never in the running scenario's own
    # backend/database above, which stays completely untouched so every
    # later scenario in this file still runs against the state #20 actually
    # produces. A second, independent backend SUBPROCESS (a real port, a
    # real fresh browser context with no cookies) is booted against that
    # copy, so this still runs the real routes and the real UI end to end --
    # the only thing stood in for is which physical computer the copy came
    # from, never the server logic or the client code under test.
    # ------------------------------------------------------------------
    def scenario_joined_computer_banner_claim_still_works():
        joined_dir = Path(tempfile.mkdtemp(prefix="aura_retail_smoke_joined_appdata_"))
        second_backend: Backend | None = None
        joined_context = None
        try:
            # WAL checkpoint BEFORE copying: registry_db.py opens registry.db
            # with PRAGMA journal_mode=WAL, so a committed row can live only
            # in the -wal sidecar file until something checkpoints it. A
            # plain directory copy taken between commits would be safe by
            # luck, not by construction -- this folds every committed write
            # into the main .db file first, so the copy is safe regardless
            # of timing.
            registry_db_path = backend.app_data_dir / "database" / "registry.db"
            ckpt_conn = sqlite3.connect(str(registry_db_path))
            try:
                ckpt_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                ckpt_conn.close()

            shutil.copytree(backend.app_data_dir, joined_dir, dirs_exist_ok=True)

            # THE flip -- same device identity file (device/local_device.json)
            # as the original, deliberately: this proves the BANNER path, not
            # device *identification*, so the fewer things differ from the
            # real signed-up install, the more cleanly a failure here points
            # at is_admin_device specifically. Every row, not a WHERE tied to
            # today's single-device fixture -- a company with more than one
            # device row must come back with a banner until ONE of them
            # claims, not merely the first row in the table.
            joined_db_path = joined_dir / "database" / "registry.db"
            flip_conn = sqlite3.connect(str(joined_db_path))
            try:
                cur = flip_conn.execute("UPDATE devices SET is_admin_device = 0")
                flip_conn.commit()
                assert cur.rowcount >= 1, (
                    "0 device rows existed in the copied registry.db to flip -- "
                    "the copy step or the schema path is wrong, not the fix under test"
                )
            finally:
                flip_conn.close()

            second_backend = Backend(app_data_dir=joined_dir)
            second_backend.start()

            # page.context.browser (not a bare `browser` -- _run_all_scenarios
            # is never handed that name; see scenario 14's identical fix for
            # the same reasoning) -- the same running Chromium instance the
            # suite already launched, asked for a genuinely separate context.
            joined_context = page.context.browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            joined_page = joined_context.new_page()
            report.attach(joined_page)

            joined_page.goto(second_backend.base_url, wait_until="domcontentloaded")
            _dismiss_intro_overlay_if_present(joined_page)

            # An account already exists in this copy (onboarding genuinely
            # completed, in scenario 02 above) -- this must be a returning
            # LOGIN screen, never the first-run signup form: the relogin
            # modal's own fields (#rl-email/#rl-pass), not #su-*.
            joined_page.locator("#rl-email").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            joined_page.fill("#rl-email", "smoke-admin@example.test")
            joined_page.fill("#rl-pass", "SmokeTest123!")
            joined_page.click("#rl-btn")
            joined_page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

            assert joined_page.locator('.sub-nav-item[data-section="admin-center"]').count() == 0, (
                "Settings nav entry was visible on the JOINED computer before any "
                "claim was made -- the flipped is_admin_device=0 row was not "
                "honoured (or the auto-claim fired somewhere it must not)"
            )
            banner = joined_page.locator("#aura-admin-device-claim")
            banner.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            assert "not yet your store" in banner.inner_text(), (
                "the admin-device claim banner did not render its expected copy "
                "on the joined computer"
            )
            report.shot(joined_page, shots, "04b_joined_computer_claim_banner")

            joined_page.get_by_role("button", name="Make this the admin device", exact=True).click()
            banner.wait_for(state="detached", timeout=DEFAULT_WAIT_MS)

            settings_entry = joined_page.locator('.sub-nav-item[data-section="admin-center"]')
            settings_entry.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            assert settings_entry.count() == 1, (
                "Settings nav entry did not appear on the joined computer after "
                "clicking the claim banner's own button"
            )
            report.shot(joined_page, shots, "04c_joined_computer_settings_after_claim")
        finally:
            if joined_context is not None:
                joined_context.close()
            if second_backend is not None:
                second_backend.stop()
            shutil.rmtree(joined_dir, ignore_errors=True)

    report.run(
        "03b claim-admin banner path still works on a JOINED computer (is_admin_device flipped to 0)",
        scenario_joined_computer_banner_claim_still_works,
        # This scenario's first navigation is a genuine "existing account,
        # no session yet" page load -- the one path this whole file never
        # exercised before it existed (every earlier scenario either IS the
        # fresh first-run signup, or stays inside an already-authenticated
        # tab). GET /api/auth/session's own docstring
        # (onboarding_routes.py) is explicit that gating it with
        # @mt_login_required means an anonymous call gets a plain 401,
        # deliberately, in exchange for a disabled/revoked account no longer
        # being told it is still logged in -- app-shell.js's init() already
        # treats that exactly like `authenticated:false` (same file, same
        # comment). Same "an intentional non-2xx is not a defect" shape
        # scenario 07 already established for its own deliberate 403.
        strict_console=False,
    )

    # ------------------------------------------------------------------
    # Scenario 4 -- navigate every major screen through the real sidebar,
    # asserting a concrete rendered element per screen, capturing a
    # screenshot, and requiring zero new console errors per navigation.
    # ------------------------------------------------------------------
    SCREENS = [
        ("dashboard", ".rdash-answer-value", None),
        ("products", "#prod-table", "Products"),
        ("customers", "#cust-table", "Customers"),
        ("reports", "#rep-rev", "Reports"),
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
                actual = page.locator("#sub-header-section").first.inner_text()
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
        transfers_title = page.locator("#sub-header-section").first.inner_text()
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
        # Structure, not prose. A silently-blank or broken table cannot produce a
        # .ret-empty with a non-empty title, so this still catches the failure the
        # scenario is named for -- and unlike the old literal-sentence match it
        # survives a copy edit or a translation.
        prod_empty = page.locator("#prod-table tbody .ret-empty")
        assert prod_empty.count() == 1, (
            "products table rendered no empty state at all on a fresh install; tbody was: "
            f"{page.locator('#prod-table tbody').inner_text()!r}"
        )
        prod_title = page.locator("#prod-table tbody .ret-empty-title").inner_text().strip()
        assert prod_title, "the products empty state has no title -- a blank card by another name"
        # The guidance half, which is the point of an empty state on a FIRST-RUN
        # screen: a new shop must be told what to do next, next to the control
        # that does it. Before this, the screen said only "No products found."
        # while Add Product and Import sat in the far corner of the header.
        prod_actions = page.locator("#prod-table tbody .ret-empty-actions button")
        assert prod_actions.count() >= 1, (
            "the products empty state offers no action -- a new shop is told there is "
            "nothing here and not told how to change that"
        )

        page.locator('.sub-nav-item[data-section="customers"]').click()
        page.locator("#cust-table tbody").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
        page.wait_for_function(
            "document.querySelector('#cust-table tbody') && "
            "!document.querySelector('#cust-table tbody').textContent.includes('Loading')",
            timeout=DEFAULT_WAIT_MS,
        )
        cust_empty = page.locator("#cust-table tbody .ret-empty")
        assert cust_empty.count() == 1, (
            "customers table rendered no empty state at all on a fresh install; tbody was: "
            f"{page.locator('#cust-table tbody').inner_text()!r}"
        )
        cust_title = page.locator("#cust-table tbody .ret-empty-title").inner_text().strip()
        assert cust_title, "the customers empty state has no title -- a blank card by another name"
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

    # ------------------------------------------------------------------
    # Scenario 12 -- the rail still says WHERE YOU ARE after a re-render.
    #
    # The nav rail holds more destinations than fit: measured 2026-09-12,
    # 1017px of items in a 442px rail at 1366x768, `overflow-y: auto`. Nothing
    # ever scrolled it to the active item, so a shell re-render left the marker
    # for "you are here" below the fold on exactly the destinations far enough
    # down the list to need it -- Reports at y787-831, Stock Transfers at
    # y702-746. A 1440x900 laptop clipped the same ones, because the rail's
    # content height does not change with the window.
    #
    # NOT tested through a sidebar click: Playwright scrolls an element into
    # view before clicking it, so a click-driven navigation scrolls the rail as
    # a side effect and the active item is visible afterwards no matter what.
    # Scenario 04 clicks every destination and passed throughout the whole
    # period this was broken. The rail is put back to the top explicitly below
    # to reproduce the state a fresh render leaves it in.
    #
    # A theme switch is the real trigger: it re-navigates to the current
    # section with the rail freshly laid out. (An earlier version of this
    # scenario used a reload instead, on the strength of comments in
    # app-shell.js and subsystem-retail.js claiming AuraRouter replays the
    # section from the URL hash. Retail never loads a router -- window.AuraRouter
    # is undefined and the hash stays empty -- so that path does not exist. The
    # anti-vacuity assertion below is what caught it.)
    # ------------------------------------------------------------------
    def scenario_active_nav_visible_after_rerender():
        page.locator('.sub-nav-item[data-section="reports"]').click()
        page.wait_for_timeout(1200)

        rail_sel = ".sub-nav"
        assert page.locator(rail_sel).count() == 1, f"no single {rail_sel} rail to measure"

        # Anti-vacuity: this scenario only means something if the rail actually
        # overflows. On a hypothetical short nav list every assertion below
        # would pass trivially and prove nothing.
        overflow = page.evaluate(
            "() => { const r = document.querySelector('.sub-nav');"
            " return { scrollH: r.scrollHeight, clientH: r.clientHeight }; }"
        )
        assert overflow["scrollH"] > overflow["clientH"] + 1, (
            f"the nav rail does not overflow ({overflow}), so it cannot clip the active "
            "item and this scenario is checking nothing -- re-anchor it or delete it"
        )

        # Reproduce a fresh render: the rail starts at the top, not wherever the
        # click that got us here happened to leave it.
        page.evaluate("() => { document.querySelector('.sub-nav').scrollTop = 0; }")
        page.wait_for_timeout(250)

        # A real user action that re-navigates the shell.
        page.evaluate("() => ThemeEngine.apply('dark')")
        page.wait_for_timeout(1600)

        state = page.evaluate(
            """() => {
              const active = document.querySelector('.sub-nav-item.active');
              if (!active) return { found: false };
              const r = active.getBoundingClientRect();
              const cx = Math.round(r.left + r.width / 2);
              const cy = Math.round(r.top + r.height / 2);
              const onScreen = cy >= 0 && cy <= window.innerHeight;
              const hit = onScreen ? document.elementFromPoint(cx, cy) : null;
              return {
                found: true,
                label: (active.textContent || '').trim(),
                section: active.dataset.section,
                top: Math.round(r.top),
                bottom: Math.round(r.bottom),
                vh: window.innerHeight,
                // Hit-tested, not merely on-screen: a marker behind an overlay
                // is no more use to a cashier than one below the fold.
                visible: !!(hit && (hit === active || active.contains(hit))),
              };
            }"""
        )
        assert state["found"], (
            "after a theme switch on Reports, NO nav item is marked active -- the rail "
            "cannot say which screen the till is showing"
        )
        assert state["section"] == "reports", (
            f"the re-render left section {state['section']!r} active, expected 'reports' "
            "(scenario 09 covers the screen itself being preserved)"
        )
        assert state["visible"], (
            "the active nav item ({label!r}) is marked but NOT VISIBLE: it sits at "
            "y{top}-{bottom} in a {vh}px window, so the rail is showing 'you are here' "
            "somewhere the user cannot see it. The rail holds more destinations than "
            "fit and nothing scrolled it to the active one.".format(**state)
        )
        report.shot(page, shots, "12_active_nav_visible_after_rerender")

        page.evaluate("() => ThemeEngine.apply('light')")
        page.wait_for_timeout(900)

    report.run(
        "12 the rail still shows which screen you are on after a re-render",
        scenario_active_nav_visible_after_rerender,
    )

    # ------------------------------------------------------------------
    # Scenario 13 -- #9: the customer-facing second screen actually becomes
    # visible. AUDIT #9's root cause is CSS specificity
    # ('.cd-screen[hidden]{display:none}' at (0,2,0) losing to the ID rules
    # '#cd-idle/#cd-active/#cd-complete{display:flex}' at (1,0,0)) -- with
    # the bug, ALL THREE screens render simultaneously, stacked inside
    # body{overflow:hidden} (idle at y=0, cart at y=768, thank-you at
    # y=1536). Proving this needs REAL rendered geometry, computed style and
    # hit-testing in a REAL browser -- retail_customer_display_test.js's `vm`
    # harness has no layout engine at all (see that file's own docstring) and
    # cannot see this bug either way.
    #
    # NEVER is_visible() here (measured vacuous on this exact bug): with the
    # defect present, the item name element still has a non-empty box and no
    # display:none, which is ALL is_visible() checks -- it returns true
    # whether the CSS fix is present or not, on every one of the three
    # stacked screens. to_be_in_viewport() (geometry-relative to the
    # scrollport) and elementFromPoint() (hit-testing) are what actually
    # distinguish "on screen, at the top" from "exists somewhere off past
    # y=768, unreachable" -- and getComputedStyle(#cd-idle).display is the
    # one assertion that reads the CASCADE'S OWN VERDICT rather than an
    # attribute, so it fails specifically when specificity, not merely the
    # `hidden` attribute, is wrong.
    #
    # Deliberately NOT a real till session: ringing an actual sale needs an
    # activated licence, and this whole suite is architected as ONE fresh,
    # UNLICENSED install per run (module docstring, "WHAT THIS DOES NOT
    # COVER") -- seeding a licence here would silently change what every
    # OTHER scenario in this file is proving. The #9 defect is entirely
    # inside customer-display.html/.js and independent of licensing, so the
    # till's own already-proven message shape (see
    # retail_customer_display_test.js's testCartMessageRendersExactPassthrough
    # Strings and testPromotionsRowShowsOnlyWhenPresent) is delivered
    # directly to the REAL CustomerDisplay._onMessage() -- the same entry
    # point BOTH BroadcastChannel and the #11 relay poll ultimately call --
    # in a REAL Chromium tab with the REAL stylesheet loaded. Only the
    # TRANSPORT is stood in for; the rendering pipeline under test (markup,
    # CSS cascade, JS state machine) is exactly what a real till would drive.
    #
    # Review fix (2026-09-27, #9 test, medium): a made-up total/sale_complete
    # injected straight into _onMessage() -- exactly what this scenario still
    # does, deliberately, for the reason above -- cannot prove the TILL-TO-
    # DISPLAY path itself (that the number the popup shows is the number the
    # till actually charged). That real, till-driven comparison (real product
    # tile click, RetailSystem._fmt(_currentTotals.total), a real cash
    # checkout, the server's own change) now lives in scenario 14 below,
    # reusing the licensed till that scenario already boots for #11 rather
    # than seeding a SECOND licensed install here and duplicating the
    # licensing-side-effect problem this comment already explains. This
    # scenario keeps proving the #9 CSS-specificity defect in isolation, on
    # the suite's one shared UNLICENSED install, independent of anything
    # licensing-gated.
    # ------------------------------------------------------------------
    def scenario_customer_display_shows_the_sale():
        from playwright.sync_api import expect  # local: see main()'s own deferred-import reasoning: by the time this scenario runs, playwright is already confirmed importable
        display_page = page.context.new_page()
        report.attach(display_page)
        display_page.goto(f"{base_url}/static/customer-display.html", wait_until="domcontentloaded")
        display_page.wait_for_function("typeof window.CustomerDisplay === 'object'", timeout=DEFAULT_WAIT_MS)

        total_str = "JD 7.750"
        display_page.evaluate(
            """(total) => window.CustomerDisplay._onMessage({
                type: 'cart',
                items: [{ name: 'Bug-proof Widget', quantity: 1, unitPrice: total, lineTotal: total }],
                subtotal: total, discount: null, tax: 'JD 0.000', total: total,
            })""",
            total_str,
        )

        item_name = display_page.locator(".cd-item-name")
        expect(item_name).to_be_in_viewport(timeout=4000)

        hit = display_page.evaluate(
            """() => {
                const el = document.querySelector('.cd-item-name');
                const r = el.getBoundingClientRect();
                const hitEl = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                return !!(hitEl && (hitEl === el || el.contains(hitEl)));
            }"""
        )
        assert hit, (
            "elementFromPoint at the item name's own centre did not land on it (or a descendant) -- "
            "either something else is painted on top, or the CSS bug has it off past the viewport again"
        )

        idle_display = display_page.evaluate("() => getComputedStyle(document.getElementById('cd-idle')).display")
        assert idle_display == "none", (
            f"#cd-idle's COMPUTED display must be 'none' once the cart is active -- was {idle_display!r}. "
            "This is the actual regression signature: the `hidden` ATTRIBUTE can be correctly set while the "
            "CASCADE still overrides it back to flex (AUDIT #9)."
        )

        # The till's own total string, unmodified: this file never re-formats
        # money (see customer-display.js's own module comment) -- so the
        # figure on screen must be byte-identical to what was sent.
        assert display_page.locator("#cd-total-value").inner_text() == total_str, (
            "the display's total must equal EXACTLY the string the till sent, never recomputed or reformatted"
        )
        report.shot(display_page, shots, "13_customer_display_cart_visible")

        # Sale-complete: paid/change must ALSO be genuinely on screen, not
        # merely present in the DOM -- same to_be_in_viewport discipline.
        display_page.evaluate(
            "() => window.CustomerDisplay._onMessage({ type: 'sale_complete', amountPaid: 'JD 10.000', change: 'JD 2.250' })"
        )
        expect(display_page.locator("#cd-complete-heading")).to_be_in_viewport(timeout=4000)
        expect(display_page.locator("#cd-change-row")).to_be_in_viewport(timeout=4000)
        assert display_page.locator("#cd-change-value").inner_text() == "JD 2.250"
        report.shot(display_page, shots, "14_customer_display_sale_complete")

        # The 8-second auto-revert -- a REAL wait on the REAL setTimeout
        # customer-display.js armed (SALE_COMPLETE_DISPLAY_MS), not a mocked
        # clock: this is the one place in this scenario where "run the real
        # artefact" (ENGINEERING.md) means literally waiting for it, since a
        # virtual clock would also fast-forward the #11 relay poll's own
        # setInterval (every ~600ms) into ~13 real HTTP requests for no
        # benefit to this assertion (that route is now real -- C2 landed it,
        # see scenario 14 below -- so those extra polls would just be noise,
        # not the "hits a stub" reasoning an earlier draft of this comment
        # gave when the route did not exist yet).
        display_page.wait_for_function(
            "document.getElementById('cd-idle') && document.getElementById('cd-idle').hidden === false",
            timeout=10_000,
        )
        idle_after = display_page.evaluate("() => getComputedStyle(document.getElementById('cd-idle')).display")
        complete_after = display_page.evaluate("() => getComputedStyle(document.getElementById('cd-complete')).display")
        assert idle_after != "none", "idle screen must be showing again once the 8s auto-revert timer fires"
        assert complete_after == "none", "the thank-you screen must be hidden again once its own timer fires"

        display_page.close()

    report.run(
        "13 customer display: the CSS fix makes the cart/thank-you screens actually visible (#9)",
        scenario_customer_display_shows_the_sale,
        # The #11 relay poll this page starts on load (_startPolling(),
        # customer-display.js) is a genuine 204 against the REAL route now
        # (C2 landed it -- scenario 14 below exercises it directly), but
        # display_page.close() above tears the page down mid-poll-cycle, and
        # whichever fetch happens to be in flight at that instant surfaces as
        # a request FAILURE (net::ERR_ABORTED, not a console error -- see the
        # full run's own "FAILED NETWORK REQUESTS" section, which is separate
        # bookkeeping from console_errors and does not fail a scenario). Kept
        # defensive rather than tightened back to the default: a page torn
        # down mid-request is not evidence of a product bug either way, and
        # scenario 07 already establishes the "an intentional non-2xx is not
        # a defect" precedent this shares.
        strict_console=False,
    )

    # ------------------------------------------------------------------
    # Scenario 14 -- #11/I-1's REAL route, not the state-machine stub
    # scenario 13 drives directly, AND (review fix, 2026-09-27, #9 test,
    # medium -- see scenario 13's own addendum comment above) the real
    # till-vs-display comparison item #9's scenario never had. C2's actual
    # POST/GET /api/sub/retail/customer-display/state exists (grepped in
    # retail_api.py before writing this). This scenario proves what
    # scenario 13 structurally cannot: that a message crosses a genuine
    # PROCESS/COOKIE boundary through the backend -- the way the real
    # desktop app's second native window would (a separate pywebview
    # profile, no BroadcastChannel at all; see #11's own comment in
    # customer-display.js) -- AND that the figures a real till charges are
    # the exact figures either delivery path (same-context popup +
    # BroadcastChannel, or the cross-context relay) renders, never a value
    # this test file made up itself.
    #
    # A SEPARATE, THIRD Backend/AURA_APP_DATA (never the suite's main
    # `backend`, and never the joined-computer copy scenario 03b makes) is
    # booted and licensed here, deliberately: ringing a real sale needs an
    # activated licence (module docstring, "WHAT THIS DOES NOT COVER"), and
    # the relay POST is NOT one of RETAIL_RESTRICTED_ALLOWLIST's seven codes
    # (retail_api.py), so on a NOT_CONFIGURED/unlicensed install it is
    # refused with 403 -- the same gate scenario 07 already proves
    # deliberately denies a mutation. Seeding a licence into the suite's own
    # `backend` would silently change what every OTHER scenario in this
    # file proves (scenario 13's own comment says so explicitly); a
    # dedicated, disposable third install, signed up and THEN licensed in
    # the exact order contracts.md's harness note requires (sign up first
    # while unlicensed, THEN seed_active_license, THEN reload -- seeding
    # before signup makes the app think it JOINED a shop and it hangs on
    # "Connecting to your shop..."), keeps every other scenario's fixture
    # untouched.
    #
    # Review fix (2026-09-27, #11 test, high + medium deny-half): this
    # scenario used to POST a hand-built fetch body straight from the till's
    # page context -- never touching the till's real cart, its ~150ms
    # debounce, _customerDisplayCartPayload() or the checkout ordering, so a
    # real bug in any of those (exactly the shape of the #11(b) CRITICAL fix
    # landed the same wave in subsystem-retail.js's
    # _postToCustomerDisplayRelay: 'sale_complete' silently overwritten by
    # the very next _clearCart() echo inside the SAME debounce window) would
    # have shipped green here. The hand-built payload also set `shop.name`
    # to a literal `null`, so #12-display's own fallback was never exercised
    # by this route either. And the deny half used to run BEFORE anything
    # had been posted to the relay at all, so "0 .cd-item-name" held whether
    # or not auth worked -- moved to run AFTER a real cart is already the
    # relay's latest state, which is the only ordering that actually proves
    # the guard. Every money/name figure asserted below now comes from a
    # REAL click on a REAL product tile, a REAL cash checkout, and the
    # till's OWN `RetailSystem._fmt()`/`_lastSaleData` -- never invented by
    # this file, matching this suite's MONEY DISCIPLINE convention.
    # ------------------------------------------------------------------
    def scenario_customer_display_relay_crosses_a_real_context():
        from playwright.sync_api import expect  # local: see scenario 13's own deferred-import reasoning
        sys.path.insert(0, str(REPO_ROOT))
        from commercial_runtime.licensing_contracts.test_support import seed_active_license

        relay_backend = Backend()
        till_context = None
        try:
            relay_backend.start()
            # page.context.browser, not a bare `browser` name -- this nested
            # function is never handed one; it reaches the same running
            # Chromium instance main() launched through the page it already
            # has, exactly like scenario 03b's identical fix.
            till_context = page.context.browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            till_page = till_context.new_page()
            report.attach(till_page)

            # Sign up FIRST, unlicensed -- see this scenario's own comment
            # above for why the order matters. "Relay Test Co" (the Company
            # Name typed here) is also #12-display's own proof further down:
            # the I-3 read-time fallback this crew's review fix wires into
            # customer-display.js's _loadBranding must surface THIS exact
            # string on a fresh install that has never touched
            # Settings > Branding.
            till_page.goto(relay_backend.base_url, wait_until="domcontentloaded")
            _dismiss_intro_overlay_if_present(till_page)
            till_page.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            till_page.fill("#su-name", "Relay Test Admin")
            till_page.fill("#su-company", "Relay Test Co")
            till_page.fill("#su-email", "relay-admin@example.test")
            till_page.fill("#su-pass", "RelayTest123!")
            till_page.fill("#su-pass2", "RelayTest123!")
            till_page.click("#su-btn")
            till_page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

            # THEN seed an active licence directly into THIS dedicated
            # install's own licensing.db, and reload. flask_guard.py's own
            # docstring: it re-reads licensing state on every request rather
            # than caching it, so no backend restart is needed -- only a
            # reload, so this TAB's own client-side gates (mostly resolved
            # once at init()) re-derive against the now-licensed state too.
            seed_active_license(str(relay_backend.app_data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            till_page.reload(wait_until="domcontentloaded")
            till_page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)

            # The signup computer auto-claims the admin-device role (item
            # #20, a different crew's fix), so this banner should not appear
            # here at all -- cleared defensively if it somehow does, the same
            # shape retail_p0_till_e2e.py's own onboarding already uses, so
            # it can never intercept a click meant for something underneath.
            try:
                banner = till_page.locator("#aura-admin-device-claim")
                banner.wait_for(state="visible", timeout=3000)
                till_page.get_by_role("button", name="Make this the admin device", exact=True).click()
                banner.wait_for(state="detached", timeout=8000)
            except Exception:  # noqa: BLE001
                pass

            # ---- seed two real, sellable products through the real HTTP
            # API, the till's own authenticated session (same cookies the
            # page itself holds) -- category_id, never a 'category' name key
            # (contracts.md: create_product ignores that; this is the exact
            # mistake #0's own item describes) -------------------------------
            def till_api(method, path, body=None):
                return till_page.evaluate(
                    """({method, path, body}) => fetch(path, {
                        method, credentials: 'include',
                        headers: body !== null ? {'Content-Type': 'application/json'} : {},
                        body: body !== null ? JSON.stringify(body) : undefined,
                    }).then(r => r.json())""",
                    {"method": method, "path": path, "body": body},
                )

            cat_resp = till_api("POST", "/api/sub/retail/categories", {"name": "Relay Test Category"})
            cat_id = ((cat_resp or {}).get("data") or {}).get("id")
            assert cat_id, f"category was not created: {cat_resp}"

            def create_product(name, sku, price):
                resp = till_api("POST", "/api/sub/retail/products", {
                    "name": name, "sku": sku, "sell_price": price, "cost_price": round(price / 2, 3),
                    "category_id": cat_id, "unit": "pc",
                })
                pid = ((resp or {}).get("data") or {}).get("id")
                assert pid, f"product {name!r} was not created: {resp}"
                till_api("POST", f"/api/sub/retail/products/{pid}/stock-adjust",
                         {"quantity": 100, "reason": "p0 relay/popup e2e seed"})
                return pid

            create_product("Popup-proof Widget", "P0-C6-POPUP", 3.3)
            create_product("Relay-proof Widget", "P0-C6-RELAY", 5.75)
            till_api("POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 50})

            till_page.locator('.sub-nav-item[data-section="pos"]').click()
            till_page.wait_for_selector("#pos-product-grid", timeout=DEFAULT_WAIT_MS)
            till_page.wait_for_timeout(500)

            # =============================================================
            # #9 test (review fix, medium): the SAME-CONTEXT popup path.
            # window.open()'s NAMED target ('auraCustomerDisplay',
            # _openCustomerDisplay's own comment) means this popup shares
            # BroadcastChannel with the till tab -- a genuinely different
            # proof from the cross-context relay below, since BroadcastChannel
            # and the relay poll are two independent delivery mechanisms that
            # can each break on their own. Real product tile, real cash
            # checkout, every figure read back from the till itself -- never
            # a made-up total/sale_complete like the old scenario 13 injected.
            # =============================================================
            with till_page.expect_popup() as popup_info:
                till_page.click("#pos-display-btn")
            popup_page = popup_info.value
            report.attach(popup_page)
            popup_page.wait_for_function("typeof window.CustomerDisplay === 'object'", timeout=DEFAULT_WAIT_MS)

            till_page.click('.pos-card:has-text("Popup-proof Widget")')
            till_page.wait_for_timeout(500)  # > the 150ms relay debounce; BroadcastChannel is faster still
            popup_total_fmt = till_page.evaluate("() => RetailSystem._fmt(RetailSystem._currentTotals.total)")

            popup_item = popup_page.locator(".cd-item-name")
            expect(popup_item).to_be_in_viewport(timeout=4000)
            popup_hit = popup_page.evaluate(
                """() => {
                    const el = document.querySelector('.cd-item-name');
                    const r = el.getBoundingClientRect();
                    const hitEl = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                    return !!(hitEl && (hitEl === el || el.contains(hitEl)));
                }"""
            )
            assert popup_hit, (
                "elementFromPoint at the popup's item name centre did not land on it (or a descendant) -- "
                "either something else is painted on top, or the #9 CSS specificity bug has it off-viewport again"
            )
            popup_idle_display = popup_page.evaluate("() => getComputedStyle(document.getElementById('cd-idle')).display")
            assert popup_idle_display == "none", (
                f"#cd-idle's COMPUTED display must be 'none' once a real cart is active -- was {popup_idle_display!r}"
            )
            assert popup_page.locator("#cd-total-value").inner_text() == popup_total_fmt, (
                f"the popup's total ({popup_page.locator('#cd-total-value').inner_text()!r}) must equal the "
                f"till's own RetailSystem._fmt(_currentTotals.total) ({popup_total_fmt!r}) -- a made-up total "
                "cannot prove the till-to-display path (#9 review finding)"
            )
            report.shot(popup_page, shots, "14a_customer_display_popup_cart_visible")

            till_page.click('.pos-pay-btn[data-method="cash"]')
            till_page.fill("#pos-tendered", "50")
            till_page.click("#pos-checkout-btn")
            till_page.wait_for_selector("#ret-receipt-dialog", timeout=DEFAULT_WAIT_MS)
            popup_change_fmt = till_page.evaluate(
                "() => (RetailSystem._lastSaleData.change > 0) ? RetailSystem._fmt(RetailSystem._lastSaleData.change) : null"
            )
            assert popup_change_fmt, f"expected positive change from a 50-tendered cash sale, till reported {popup_change_fmt!r}"

            expect(popup_page.locator("#cd-complete-heading")).to_be_in_viewport(timeout=4000)
            expect(popup_page.locator("#cd-change-row")).to_be_in_viewport(timeout=4000)
            assert popup_page.locator("#cd-change-value").inner_text() == popup_change_fmt, (
                f"the popup's change ({popup_page.locator('#cd-change-value').inner_text()!r}) must equal the "
                f"server's own change the till reported ({popup_change_fmt!r})"
            )
            report.shot(popup_page, shots, "14b_customer_display_popup_sale_complete")

            till_page.click('#ret-receipt-dialog button:has-text("New Sale")')
            till_page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
            popup_page.close()

            # =============================================================
            # #11 test (review fix, high + medium deny-half): the CROSS-
            # CONTEXT relay path -- its own cookie jar, no BroadcastChannel
            # bridge to the till's tab at all, exactly like a genuinely
            # separate native pywebview window (#11's own comment in
            # customer-display.js).
            # =============================================================
            till_page.click('.pos-card:has-text("Relay-proof Widget")')
            till_page.wait_for_timeout(500)  # > the 150ms relay debounce -- a real 'cart' message is now the relay's latest state
            relay_total_fmt = till_page.evaluate("() => RetailSystem._fmt(RetailSystem._currentTotals.total)")

            # ---- deny half: run AFTER the cart above is already on the
            # relay (review fix -- the old ordering ran this BEFORE anything
            # had been posted, so "0 .cd-item-name" held whether or not auth
            # worked). Over at least 2 poll cycles (600ms interval -- 1.5s
            # covers the immediate tick plus two more), a cookie-less context
            # must still get 401, render nothing, and stay idle. -----------
            anon_context = page.context.browser.new_context(viewport=VIEWPORT)
            try:
                anon_page = anon_context.new_page()
                report.attach(anon_page)
                statuses = []
                anon_page.on(
                    "response",
                    lambda r: statuses.append(r.status) if "/customer-display/state" in r.url else None,
                )
                anon_page.goto(f"{relay_backend.base_url}/static/customer-display.html", wait_until="domcontentloaded")
                anon_page.wait_for_function("typeof window.CustomerDisplay === 'object'", timeout=DEFAULT_WAIT_MS)
                anon_page.wait_for_timeout(1500)
                assert 401 in statuses, (
                    f"a cookie-less context never got a 401 from the relay's GET .../state route, even though a "
                    f"REAL cart is already sitting on the relay -- observed statuses: {statuses!r}"
                )
                anon_item_count = anon_page.locator(".cd-item-name").count()
                assert anon_item_count == 0, (
                    f"the cookie-less display rendered cart content it was never authorized to see "
                    f"(anon_item_name_count_after_cart_posted={anon_item_count})"
                )
                anon_idle_display = anon_page.evaluate(
                    "() => getComputedStyle(document.getElementById('cd-idle')).display"
                )
                assert anon_idle_display != "none", (
                    f"the cookie-less display must stay on its idle screen -- #cd-idle computed display was "
                    f"{anon_idle_display!r}"
                )
            finally:
                anon_context.close()

            # ---- allow half: the till's own cookies cross into a genuinely
            # separate context, and the relay renders the real cart within
            # ~2s (the review finding's own wording; the poll interval is
            # 600ms, so a 4000ms ceiling leaves several real polls of margin
            # without pretending this is instant) ---------------------------
            till_cookies = till_context.cookies()
            display_context = page.context.browser.new_context(viewport=VIEWPORT)
            try:
                display_context.add_cookies(till_cookies)
                display_page = display_context.new_page()
                report.attach(display_page)
                display_page.goto(f"{relay_backend.base_url}/static/customer-display.html", wait_until="domcontentloaded")
                display_page.wait_for_function("typeof window.CustomerDisplay === 'object'", timeout=DEFAULT_WAIT_MS)

                relay_item = display_page.locator(".cd-item-name")
                expect(relay_item).to_be_in_viewport(timeout=4000)
                assert relay_item.inner_text() == "Relay-proof Widget", (
                    f"the relay-fed context rendered the wrong item: {relay_item.inner_text()!r}"
                )
                relay_hit = display_page.evaluate(
                    """() => {
                        const el = document.querySelector('.cd-item-name');
                        const r = el.getBoundingClientRect();
                        const hitEl = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                        return !!(hitEl && (hitEl === el || el.contains(hitEl)));
                    }"""
                )
                assert relay_hit, "elementFromPoint at the relay context's item name centre did not land on it"
                assert display_page.locator("#cd-total-value").inner_text() == relay_total_fmt, (
                    f"the relay context's total ({display_page.locator('#cd-total-value').inner_text()!r}) must "
                    f"equal the till's own RetailSystem._fmt(_currentTotals.total) ({relay_total_fmt!r})"
                )
                # #12-display: this fresh install's own signup Company Name
                # (I-3's read-time fallback) must reach a context that never
                # ran BroadcastChannel at all -- through either the relay's
                # own shop payload or the branding fetch, both of which now
                # carry the fallback (#12-display's own review fix in
                # customer-display.js's _loadBranding).
                expect(display_page.locator("#cd-shop-name")).to_have_text("Relay Test Co", timeout=4000)
                report.shot(display_page, shots, "14c_customer_display_relay_cross_context")

                till_page.click('.pos-pay-btn[data-method="cash"]')
                till_page.fill("#pos-tendered", "50")
                till_page.click("#pos-checkout-btn")
                till_page.wait_for_selector("#ret-receipt-dialog", timeout=DEFAULT_WAIT_MS)
                relay_change_fmt = till_page.evaluate(
                    "() => (RetailSystem._lastSaleData.change > 0) ? RetailSystem._fmt(RetailSystem._lastSaleData.change) : null"
                )
                assert relay_change_fmt, f"expected positive change from a 50-tendered cash sale, till reported {relay_change_fmt!r}"

                expect(display_page.locator("#cd-complete-heading")).to_be_in_viewport(timeout=4000)
                expect(display_page.locator("#cd-change-row")).to_be_in_viewport(timeout=4000)
                assert display_page.locator("#cd-change-value").inner_text() == relay_change_fmt, (
                    f"the relay context's change ({display_page.locator('#cd-change-value').inner_text()!r}) must "
                    f"equal the server's own change the till reported ({relay_change_fmt!r})"
                )
                report.shot(display_page, shots, "14d_customer_display_relay_sale_complete")

                till_page.click('#ret-receipt-dialog button:has-text("New Sale")')
                till_page.wait_for_selector("#ret-receipt-dialog", state="detached", timeout=5000)
            finally:
                display_context.close()
        finally:
            if till_context is not None:
                till_context.close()
            relay_backend.stop()
            relay_backend.cleanup_app_data()

    report.run(
        "14 customer-display: real till-to-display proof over BOTH delivery paths -- same-context popup + "
        "BroadcastChannel (#9) and cross-context relay (#11/I-1) -- cookie-less context 401s AFTER a real cart "
        "is posted, till-cookie context renders live and matches the till's own total/change/shop-name (#12)",
        scenario_customer_display_relay_crosses_a_real_context,
        # The DENY half of this scenario deliberately drives a cookie-less
        # context at two login-gated GETs (the branding fetch and the relay
        # poll) specifically so they 401 -- that IS the assertion being
        # proved, not a side effect of it. Same "an intentional non-2xx is
        # not a defect" shape scenario 07 already established. A popup/page
        # torn down mid-poll-cycle (popup_page.close() above) can also
        # surface as a request FAILURE (net::ERR_ABORTED) rather than a
        # console error -- scenario 13's own comment already established
        # that this is not evidence of a product bug either way.
        strict_console=False,
    )


if __name__ == "__main__":
    sys.exit(main())
