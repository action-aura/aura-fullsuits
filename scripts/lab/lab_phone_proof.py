"""Server half of the real-phone proof: issue a licence for it and bring up a
desktop hub till activated on that licence, so the phone has something to
join over Wi-Fi.

Reuses lab_e2e.py's proven helpers (Owner sign-in with TOTP, the three-act
issuance screen, the headless till launcher, the beacon capture) rather than
re-implementing them. Kept separate from lab_e2e.py because the phone half is
driven over adb, not Playwright, and must never burn a seat on the licences
lab_e2e.py's own proofs use.

Run with the SYSTEM Python 3.14 (Playwright lives there):
    python scripts/lab/lab_phone_proof.py hub
"""
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab_e2e as E  # noqa: E402

HUB_PORT = 5030
HUB_BASE = f"http://127.0.0.1:{HUB_PORT}"
HUB_DATA = str(E.LAB_ROOT / "phone-proof-hub")
ADMIN_EMAIL = "admin@phoneproof.lab"
RESULTS = E.Results(E.PROOF_DIR / "result-phone.json", E.PROOF_DIR / "result-phone.md",
                    E.PROOF_DIR / "screens-phone")


def issue(pw) -> str:
    env = E.load_env()
    if env.get("phone_proof_licence_key"):
        E.log("phone proof licence already issued -- reusing it (never mint a second)")
        return env["phone_proof_licence_key"]
    t0 = time.time()
    browser = pw.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        minted = E.owner_authenticate(page, env, results=RESULTS)
        if minted:
            E.save_env(minted)
        result = E.owner_issue_license(page, legal_name="Lab Phone Proof", target_devices=2,
                                       results=RESULTS)
        E.save_env({"phone_proof_licence_key": result["key"],
                    "phone_proof_licence_id": result["license_id"]})
        RESULTS.phase("P1 issue phone-proof licence (2 devices)", "PASS",
                      f"license_id={result['license_id']} included={result['included_devices']}",
                      time.time() - t0)
        return result["key"]
    finally:
        browser.close()


def hub(pw) -> None:
    key = issue(pw)
    env = E.load_env()
    if not env.get("phone_proof_admin_password"):
        E.save_env({"phone_proof_admin_password": uuid.uuid4().hex[:10] + "Aa1!"})
        env = E.load_env()
    password = env["phone_proof_admin_password"]

    t0 = time.time()
    E.start_till(HUB_PORT, HUB_DATA, site_relay="auto")
    if not E.wait_http_ok(HUB_BASE + "/api/health", timeout=45):
        raise RuntimeError("hub till did not boot")

    browser = pw.chromium.launch()
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(HUB_BASE + "/", wait_until="networkidle")
        status = page.request.get(HUB_BASE + "/api/licensing/status").json()
        if status.get("current_state") not in ("ACTIVE_ONLINE", "ACTIVE_OFFLINE"):
            page.wait_for_selector("#su-btn", timeout=30000)
            page.fill("#su-name", "Phone Proof Owner")
            page.fill("#su-company", "Lab Phone Proof")
            page.fill("#su-email", ADMIN_EMAIL)
            page.fill("#su-key", key)
            page.fill("#su-pass", password)
            page.fill("#su-pass2", password)
            page.click("#su-btn")
            page.wait_for_selector("#rl-email", timeout=60000)
        status = page.request.get(HUB_BASE + "/api/licensing/status").json()
        RESULTS.phase("P2 desktop hub till activated", "PASS"
                      if status.get("current_state") in ("ACTIVE_ONLINE", "ACTIVE_OFFLINE") else "FAIL",
                      f"current_state={status.get('current_state')}", time.time() - t0)
    finally:
        browser.close()

    up, _, secs = E.wait_until(lambda: E.tcp_port_listening(E.SITE_RELAY_TCP_PORT) or None,
                               timeout=120, interval=5, label="hub :5443")
    beacon = E.capture_one_beacon(timeout=30)
    RESULTS.phase("P3 desktop hub listening + beacon", "PASS" if up and beacon.get("received") else "FAIL",
                  f"listening={up} after {secs:.0f}s; beacon url={beacon.get('url')}", secs)


ADB = ("C:/Users/MSI/AppData/Local/Microsoft/WinGet/Packages/"
       "Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe/platform-tools/adb.exe")
PHONE_FWD_PORT = 18090


def _adb(*args, timeout=30):
    import subprocess
    return subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)


def _phone_base() -> str:
    """Forward a local port to the phone's embedded backend. It asks for 5000
    and falls back to 5001 when 5000 is taken on the device, so try both.
    The forward only lets this script talk to the phone's OWN local API (the
    same calls its screens make); it is not a sync path."""
    for device_port in (5000, 5001):
        _adb("forward", f"tcp:{PHONE_FWD_PORT}", f"tcp:{device_port}")
        if E.wait_http_ok(f"http://127.0.0.1:{PHONE_FWD_PORT}/api/health", timeout=5):
            return f"http://127.0.0.1:{PHONE_FWD_PORT}"
    raise RuntimeError("phone backend not reachable on device port 5000 or 5001")


def wifi() -> None:
    """Prove desktop <-> phone sync over Wi-Fi only.

    The USB tunnel to Owner is removed FIRST, so the phone can no longer reach
    Owner's relay at its baked 127.0.0.1:5551. From then on the only way a row
    can travel between the two devices is the phone's Kotlin sync talking to
    the desktop hub at its beacon address on the shop Wi-Fi."""
    env = E.load_env()
    password = env["phone_proof_admin_password"]
    _adb("reverse", "--remove", "tcp:5551")
    reverse_left = _adb("reverse", "--list").stdout.strip()
    RESULTS.phase("W0 USB tunnel to Owner removed", "PASS" if "5551" not in reverse_left else "FAIL",
                  f"adb reverse --list after removal: {reverse_left or '(empty)'}", 0.0)

    # Diagnostic only, never a gate: on this MIUI phone `logcat -d` has been
    # observed to hang rather than dump and exit (2026-09-24, twice), so a
    # timeout here records "unavailable" instead of aborting the proof.
    import subprocess
    try:
        joined = _adb("logcat", "-d", "-s", "HubAutoJoin", timeout=20).stdout.strip().splitlines()[-3:]
        joined_text = " | ".join(joined) or "(no HubAutoJoin lines yet)"
    except subprocess.TimeoutExpired:
        joined_text = "(logcat -d hung on this device; not read)"
    RESULTS.phase("W1 phone HubAutoJoin log", "MEASURED", joined_text, 0.0)

    desk = E._login(HUB_BASE, ADMIN_EMAIL, password)
    phone = E._login(_phone_base(), ADMIN_EMAIL, password)

    sku = f"WIFI-P1-{int(time.time())}"
    t = time.time()
    desk.call("POST", "/api/sub/retail/products", {
        "name": "Wi-Fi Proof Tea", "sku": sku, "sell_price": 1.25, "price": 1.25,
        "cost_price": 0.5, "category": "Lab", "unit": "pc"})
    _, plist = desk.call("GET", "/api/sub/retail/products")
    prod = E.find_by(E.rows_of(plist), "sku", sku)
    if not prod:
        raise RuntimeError("product not visible on the desktop after create")
    desk.call("POST", f"/api/sub/retail/products/{prod['id']}/stock-adjust",
              {"quantity": 12, "reason": "wifi proof opening stock"})
    ok, seen, secs = E.wait_until(
        lambda: (lambda s, bd: (lambda r: r if r and r.get("total_stock") == 12 else None)(
            E.find_by(E.rows_of(bd), "sku", sku)) if s == 200 else None)(*phone.call("GET", "/api/sub/retail/products")),
        timeout=240, interval=5, label="desktop product on phone (Wi-Fi only)")
    RESULTS.phase("W2 product made on desktop reaches phone over Wi-Fi", "PASS" if ok else "FAIL",
                  f"same_id={bool(seen) and seen.get('id') == prod['id']} stock={seen.get('total_stock') if seen else None}",
                  secs)
    if not ok:
        return

    phone.call("POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 5})
    _, sale_body = phone.call("POST", "/api/sub/retail/sales", {
        "items": [{"product_id": prod["id"], "quantity": 2}],
        "payment_method": "cash", "amount_paid": 5.0, "idempotency_key": f"wifi-proof-{sku}"})
    sale_number = ((sale_body or {}).get("data") or {}).get("sale_number")
    if not sale_number:
        RESULTS.phase("W3 sale rung on phone", "FAIL", f"body={sale_body}", time.time() - t)
        return
    ok, _, secs = E.wait_until(
        lambda: (lambda s, bd: next((r for r in E.rows_of(bd) if r.get("sale_number") == sale_number), None)
                 if s == 200 else None)(*desk.call("GET", "/api/sub/retail/sales/recent")),
        timeout=240, interval=5, label="phone sale on desktop (Wi-Fi only)")
    _, pd = desk.call("GET", "/api/sub/retail/products")
    _, pp = phone.call("GET", "/api/sub/retail/products")
    stock_d = (E.find_by(E.rows_of(pd), "sku", sku) or {}).get("total_stock")
    stock_p = (E.find_by(E.rows_of(pp), "sku", sku) or {}).get("total_stock")
    RESULTS.phase("W3 sale rung on phone reaches desktop over Wi-Fi; stock 10 on both",
                  "PASS" if ok and stock_d == 10 and stock_p == 10 else "FAIL",
                  f"sale_on_desktop={ok} stock_desktop={stock_d} stock_phone={stock_p}", secs)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "hub"
    if cmd == "wifi":
        wifi()
        return
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        if cmd == "issue":
            issue(pw)
        elif cmd == "hub":
            hub(pw)
        else:
            sys.exit(f"unknown command {cmd!r}; use issue|hub|wifi")


if __name__ == "__main__":
    main()
