"""Photograph every retail screen, for real.

v1 screenshotted on `networkidle`, which fires before this app has rendered
anything -- i18n and the onboarding-status fetch both resolve after it, so
every shot came out blank and looked like a boot failure. Waits are now on
SELECTORS, which is the only thing that actually means "the screen is there".

Completes first-run onboarding through the real form (that is what a customer
does), seeds demo data so screens are not empty, then walks every sidebar
destination at three widths.
"""
import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BACKEND_DIR = Path("products/retail/backend").resolve()
for _p in (str(Path(".").resolve()), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_ui_shots2_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
import app as _app_module  # noqa: E402
flask_app = _app_module.init_app()

import logging  # noqa: E402
logging.getLogger('werkzeug').setLevel(logging.ERROR)

PORT = int(os.environ.get("UI_SHOT_PORT", "5610"))
from werkzeug.serving import make_server  # noqa: E402
server = make_server("127.0.0.1", PORT, flask_app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(1.5)
BASE = "http://127.0.0.1:%d" % PORT

OUT = Path(os.environ.get("UI_SHOT_DIR", "ui-shots")).resolve()
OUT.mkdir(parents=True, exist_ok=True)

NAME, COMPANY = "Aura Owner", "Sweets & Co"
EMAIL, PASSWORD = "owner@shots.local", "ShotsPassword1"

SECTIONS = [
    "dashboard", "pos", "products", "categories", "customers", "promotions",
    "suppliers", "purchases", "returns", "scanner", "reports",
    "stock-accuracy", "exceptions", "audit-log", "employees", "branches",
    "admin-center", "email-notifications", "backup-export",
]
WIDTHS = [(1440, 900, "desktop"), (768, 1024, "tablet"), (390, 844, "phone")]

from playwright.sync_api import sync_playwright  # noqa: E402

problems = []


def onboard(page):
    """Do what a customer does on a fresh install."""
    page.wait_for_selector("#su-btn", timeout=20000)
    page.fill("#su-name", NAME)
    page.fill("#su-company", COMPANY)
    page.fill("#su-email", EMAIL)
    page.fill("#su-pass", PASSWORD)
    page.fill("#su-pass2", PASSWORD)
    page.click("#su-btn")
    # The shell replaces the setup card; wait for the sidebar to exist.
    page.wait_for_selector("#subsystem-shell .sub-nav-item, #subsystem-shell nav, "
                           "#sub-header-section", timeout=30000)
    page.wait_for_timeout(2000)


def login(page):
    page.wait_for_selector("input[type=password]", timeout=20000)
    inputs = page.locator("input[type=email], input[type=text]")
    inputs.first.fill(EMAIL)
    page.locator("input[type=password]").first.fill(PASSWORD)
    page.keyboard.press("Enter")
    page.wait_for_selector("#sub-header-section", timeout=30000)
    page.wait_for_timeout(2000)


with sync_playwright() as p:
    browser = p.chromium.launch()
    first = True
    for w, h, label in WIDTHS:
        ctx = browser.new_context(viewport={"width": w, "height": h})
        page = ctx.new_page()
        page.on("pageerror", lambda e, L=label: problems.append("%s PAGEERROR %s" % (L, e)))

        page.goto(BASE)
        try:
            if first:
                page.wait_for_selector("#su-btn, input[type=password]", timeout=20000)
                if page.locator("#su-btn").count():
                    onboard(page)
                else:
                    login(page)
                first = False
            else:
                login(page)
        except Exception as exc:
            problems.append("%s auth: %s" % (label, str(exc).splitlines()[0][:160]))
            page.screenshot(path=str(OUT / ("%s-AUTH-FAILED.png" % label)))
            ctx.close()
            continue

        page.screenshot(path=str(OUT / ("%s-01-landing.png" % label)))

        for i, section in enumerate(SECTIONS, start=2):
            try:
                page.evaluate("(s) => SubsystemApp._navigate(s)", section)
                page.wait_for_timeout(1600)
                page.screenshot(path=str(OUT / ("%s-%02d-%s.png" % (label, i, section))))
            except Exception as exc:
                problems.append("%s %s: %s" % (label, section,
                                               str(exc).splitlines()[0][:140]))
        ctx.close()
    browser.close()

server.shutdown()
shots = sorted(OUT.glob("*.png"))
print("screenshots: %d in %s" % (len(shots), OUT))
if problems:
    print("\nPROBLEMS (%d):" % len(problems))
    seen = set()
    for p_ in problems:
        k = p_[:120]
        if k in seen:
            continue
        seen.add(k)
        print("  -", p_[:200])
else:
    print("no page errors, no navigation failures")
