"""Seed a ready-to-use demo shop in the lab Owner, for installing the desktop
app and the phone app somewhere else and only having to sign in.

What it leaves behind:
  - one licence, "Aura Demo Shop", with 4 device seats (one is spent here);
  - the shop's owner account and one cashier account, created on a throwaway
    seed till on this laptop and synced to Owner, so every device that later
    activates with the key receives them and can sign in straight away;
  - a small sample catalogue with opening stock, and the shop currency set to
    JOD, synced the same way.

Why a seed till at all: activation cannot be done ahead of time for another
machine. Each device's licence is signed for that device's own key, which on
Windows is DPAPI-protected to that PC's user and on Android lives in the
phone's own storage. What CAN be prepared in advance is the shop itself, so
the far devices join an existing shop (the "connect to my shop" path) instead
of each founding a new one.

The seed till runs with site relay OFF (-SiteRelay 0): the phone-proof hub
till already holds the LAN hub role on this network, and the election does
not filter by licence yet, so a second licensed till here would fight it.

Never mints a second licence or a second set of accounts: every value is
written to lab.env.json the first time and reused after.

Run with the SYSTEM Python 3.14 (Playwright lives there):
    python scripts/lab/lab_demo_shop.py
"""
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab_e2e as E  # noqa: E402

SEED_PORT = 5046  # 5040 is held by Windows (svchost, CDPSvc) on this machine
SEED_BASE = f"http://127.0.0.1:{SEED_PORT}"
SEED_DATA = str(E.LAB_ROOT / "demo-seed-till")
OWNER_EMAIL = "owner@aurademo.shop"
CASHIER_EMAIL = "cashier@aurademo.shop"
SEATS = 4
RESULTS = E.Results(E.PROOF_DIR / "result-demo.json", E.PROOF_DIR / "result-demo.md",
                    E.PROOF_DIR / "screens-demo")

# Prices in JOD (3 decimal places). Stock is the opening count.
CATALOGUE = [
    ("Arabic Coffee 250g", "DEMO-COF-250", 4.500, 2.900, 40),
    ("Black Tea 100 bags", "DEMO-TEA-100", 2.250, 1.400, 60),
    ("Mineral Water 1.5L", "DEMO-WAT-150", 0.350, 0.180, 120),
    ("Dates Medjool 500g", "DEMO-DAT-500", 6.000, 4.100, 25),
    ("Olive Oil 1L", "DEMO-OIL-1L", 7.750, 5.600, 18),
    ("Za'atar 200g", "DEMO-ZAT-200", 2.000, 1.150, 35),
]


def _strong_password() -> str:
    return uuid.uuid4().hex[:6] + "Aa1!" + uuid.uuid4().hex[:4]


def issue(pw) -> str:
    env = E.load_env()
    if env.get("demo_licence_key"):
        E.log("demo licence already issued -- reusing it (never mint a second)")
        return env["demo_licence_key"]
    t0 = time.time()
    browser = pw.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        minted = E.owner_authenticate(page, env, results=RESULTS)
        if minted:
            E.save_env(minted)
        result = E.owner_issue_license(page, legal_name="Aura Demo Shop", target_devices=SEATS,
                                       results=RESULTS)
        E.save_env({"demo_licence_key": result["key"], "demo_licence_id": result["license_id"]})
        RESULTS.phase(f"D1 issue demo licence ({SEATS} devices)", "PASS",
                      f"license_id={result['license_id']} included={result['included_devices']}",
                      time.time() - t0)
        return result["key"]
    finally:
        browser.close()


def seed(pw) -> None:
    key = issue(pw)
    env = E.load_env()
    if not env.get("demo_owner_password"):
        E.save_env({"demo_owner_password": _strong_password()})
        env = E.load_env()
    password = env["demo_owner_password"]

    t0 = time.time()
    E.start_till(SEED_PORT, SEED_DATA, site_relay="0")
    if not E.wait_http_ok(SEED_BASE + "/api/health", timeout=45):
        raise RuntimeError("seed till did not boot")

    browser = pw.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(SEED_BASE + "/", wait_until="networkidle")
        status = page.request.get(SEED_BASE + "/api/licensing/status").json()
        if status.get("current_state") not in ("ACTIVE_ONLINE", "ACTIVE_OFFLINE"):
            page.wait_for_selector("#su-btn", timeout=30000)
            page.fill("#su-name", "Demo Owner")
            page.fill("#su-company", "Aura Demo Shop")
            page.fill("#su-email", OWNER_EMAIL)
            page.fill("#su-key", key)
            page.fill("#su-pass", password)
            page.fill("#su-pass2", password)
            page.click("#su-btn")
            page.wait_for_selector("#rl-email", timeout=60000)
        status = page.request.get(SEED_BASE + "/api/licensing/status").json()
        RESULTS.phase("D2 seed till activated, owner account created",
                      "PASS" if status.get("current_state") in ("ACTIVE_ONLINE", "ACTIVE_OFFLINE") else "FAIL",
                      f"current_state={status.get('current_state')}", time.time() - t0)
    finally:
        browser.close()

    owner = E._login(SEED_BASE, OWNER_EMAIL, password)

    t = time.time()
    owner.call("POST", "/api/sub/retail/settings/credit", {"base_currency": "JOD"})
    _, plist = owner.call("GET", "/api/sub/retail/products")
    have = {r.get("sku") for r in E.rows_of(plist)}
    made = 0
    for name, sku, price, cost, qty in CATALOGUE:
        if sku in have:
            continue
        owner.call("POST", "/api/sub/retail/products", {
            "name": name, "sku": sku, "sell_price": price, "price": price,
            "cost_price": cost, "category": "Demo", "unit": "pc"})
        _, plist = owner.call("GET", "/api/sub/retail/products")
        prod = E.find_by(E.rows_of(plist), "sku", sku)
        if prod:
            owner.call("POST", f"/api/sub/retail/products/{prod['id']}/stock-adjust",
                       {"quantity": qty, "reason": "demo opening stock"})
            made += 1
    RESULTS.phase("D3 currency JOD + sample catalogue", "PASS",
                  f"created {made} product(s); catalogue size {len(CATALOGUE)}", time.time() - t)

    t = time.time()
    if not env.get("demo_cashier_password"):
        status, body = owner.call("POST", "/api/admin/employees", {"email": CASHIER_EMAIL, "role": "cashier"})
        link = body.get("setup_link", "") if status == 200 else ""
        m = re.search(r"#setup/([0-9a-f]+)$", link)
        cashier_password = _strong_password()
        ok = False
        if m:
            s2, _ = owner.call("POST", "/api/auth/employee/setup",
                               {"token": m.group(1), "password": cashier_password})
            ok = s2 == 200
        if ok:
            E.save_env({"demo_cashier_password": cashier_password})
        RESULTS.phase("D4 cashier account", "PASS" if ok else "FAIL",
                      f"create_status={status} setup_ok={ok}", time.time() - t)
    else:
        RESULTS.phase("D4 cashier account", "PASS", "already created earlier -- reused", 0.0)


def main() -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        seed(pw)


if __name__ == "__main__":
    main()
