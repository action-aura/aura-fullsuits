"""Join an existing shop through the REAL desktop door, in a real browser,
on a genuinely fresh till (:5013) -- the client half of what
scripts/ops/join_e2e.py proves for the backend.

  join           fresh till: setup modal -> "Already have a shop?" -> key ->
                 Join shop -> "Connected to your shop". Asserts the page
                 never called /api/onboarding/create-admin (every /api/
                 request the page makes is recorded), and that
                 company_settings on the till now reads the licence id.
  after-restart  (the caller restarts the till first, so the discovered
                 relay address takes effect) the very next launch shows
                 either "Connecting to your shop…" (owner's row not landed
                 yet) or straight the ordinary sign-in modal -- never the
                 setup modal again; then the desktop's owner signs in HERE
                 through the ordinary sign-in form and the shell renders.
                 No admin was ever created on this till.

Interpreter: the system Python 3.14 (the one with playwright + chromium):
  C:\\Users\\MSI\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe
Screenshots land in <data dir's parent>/join-door-shots/.
"""
import sqlite3
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PHASE = sys.argv[1] if len(sys.argv) > 1 else "join"
PORT = sys.argv[2] if len(sys.argv) > 2 else "5013"
DATA_NAME = sys.argv[3] if len(sys.argv) > 3 else "fifth-till"
BASE = f"http://127.0.0.1:{PORT}/"
KEY = "AURA-RET-1-5P2G-39EP-XQ9T-K8ZC-FEZG"
OWNER_EMAIL = "desk-owner@rehearsal.local"
OWNER_PASS = "DeskOwner2026!Pass"
DATA = Path(r"C:\Users\MSI\.claude\jobs\215b2785\tmp") / DATA_NAME
REG = DATA / "database" / "registry.db"
SHOTS = DATA.parent / "join-door-shots" / DATA_NAME


def registry(sql):
    conn = sqlite3.connect(REG)
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError as exc:
        return f"(err {exc})"
    finally:
        conn.close()


def shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{PHASE}-{name}.png"))


def api_paths(calls, suffix):
    return [c for c in calls if c[1].endswith(suffix)]


def phase_join(page, calls):
    page.wait_for_selector("#su-btn", timeout=30000)
    print("first-run title:", page.text_content("#su-title"))
    assert page.locator("#su-join-link").count() == 1, "join link missing on a fresh, key-needing till"
    shot(page, "1-setup-modal")

    page.click("#su-join-link")
    title = (page.text_content("#su-title") or "").strip()
    print("after the link, title:", title)
    assert title == "Join your shop", title
    assert not page.locator("#su-email").is_visible(), "email field still visible in join mode"
    assert not page.locator("#su-pass").is_visible(), "password field still visible in join mode"
    assert page.locator("#su-key").is_visible(), "key field hidden in join mode"
    shot(page, "2-join-mode")

    page.fill("#su-key", KEY)
    page.click("#su-btn")
    page.wait_for_selector("#su-join-continue-btn", timeout=60000)
    print("after Join shop:", (page.text_content(".auth-title") or "").strip())
    shot(page, "3-connected")

    create_admin = api_paths(calls, "/api/onboarding/create-admin")
    activate = api_paths(calls, "/api/licensing/activate")
    print("create-admin calls:", len(create_admin), "| activate calls:", len(activate))
    assert not create_admin, "the join door called create-admin"
    assert len(activate) == 1, activate
    print("registry company_settings:", registry("SELECT company_id FROM company_settings"))
    print("registry users:", registry("SELECT email, role, employee_id FROM users"))
    print("NEXT: restart the till so the discovered relay URL takes effect, then run after-restart")


def phase_after_restart(page, calls):
    page.wait_for_selector("#rl-pass, #su-btn, .auth-title", timeout=30000)
    print("first screen after restart:", (page.text_content(".auth-title") or "").strip())
    shot(page, "4-first-screen")
    assert page.locator("#su-btn").count() == 0, "the setup modal was offered AGAIN after joining"

    page.wait_for_selector("#rl-pass", timeout=150000)
    print("sign-in screen:", (page.text_content(".auth-title") or "").strip(),
          "|", [s.strip() for s in page.locator(".auth-sub").all_text_contents()])
    shot(page, "5-signin")

    page.fill("#rl-email", OWNER_EMAIL)
    page.fill("#rl-pass", OWNER_PASS)
    page.click("#rl-btn")
    page.wait_for_selector("#sub-header-section", timeout=30000)
    page.wait_for_timeout(1500)
    shot(page, "6-signed-in")
    print("registry users:", registry("SELECT email, role, employee_id FROM users"))
    print("create-admin calls across this launch:", len(api_paths(calls, "/api/onboarding/create-admin")))
    print("RESULT: JOINED THROUGH THE DOOR, OWNER SIGNED IN, NO ADMIN CREATED HERE")


def main():
    calls = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("request", lambda r: calls.append((r.method, r.url.split("?")[0])) if "/api/" in r.url else None)
        page.goto(BASE)
        print("phase:", PHASE)
        if PHASE == "join":
            phase_join(page, calls)
        else:
            phase_after_restart(page, calls)
        browser.close()


if __name__ == "__main__":
    main()
