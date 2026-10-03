"""Aura Lab -- S-AUTH (AUDIT-033) proven on the REAL lab: real Owner server, two
real till backends, a real licence and the real Owner cloud relay -- not the
in-process file-relay harness products/retail/tests/retail_s_*_test.py already use.

Covers the ALLOW half of the plan this script was commissioned for: issuing a
fresh licence, activating till A and joining till B to the same shop, reading
back which device each till (and Owner's own database) calls "main", and
proving a legitimate authority change (a cashier account) and a legitimate
non-authority change (a product) both cross from A to B for real.

What this file deliberately does NOT contain: the forged/insider half of the
plan (an attacker re-signing an authority event "as" the main computer, and the
mutation-proof run against a deliberately broken authority.py). That half was
blocked while this script was being written -- see progress.md next to this
run's output for exactly why -- and was never completed in this session. The
mechanism for it already exists, reviewed and merged, in
products/retail/tests/_insider_forge.py; porting it to speak to the real HTTP
relay (SyncRelayClient.push/pull against the real Owner) instead of that test's
in-process FileRelay is the one piece this file leaves for whoever picks it up.

Reuses lab_e2e.py's own helpers throughout (Client, wait_until, Results, shot,
owner_authenticate, owner_issue_license, start_till, stop_lab, _login, rows_of,
find_by, the Postgres constants) rather than re-deriving any of them -- same
rule this whole lab follows: mirror an existing, already-proven mechanism,
never invent a new one for something already solved.

INTERPRETER: the SYSTEM Python 3.14 (has playwright), exactly like lab_e2e.py:
    C:\\Users\\MSI\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe scripts/lab/lab_sauth_proof.py

Ports 5061 (till A) / 5062 (till B) -- never 5001/5030/5551/5020/5021/5022/5023,
which are this lab's existing proofs or the Owner server itself. Fresh data
directories every run (named with this run's own timestamp tag), never a data
dir that has already activated a licence. A brand-new licence every run
(new key names in lab.env.json, add-only, never colliding with an existing one).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from lab_e2e import (  # noqa: E402
    Client, Results, OWNER_PG_DB, OWNER_PG_PASSWORD, OWNER_PG_USER, PSQL_EXE,
    find_by, load_env, log, owner_authenticate, owner_issue_license, rows_of,
    save_env, shot, start_till, stop_lab, tcp_port_closed, wait_http_ok,
    wait_until, _login,
)

TAG = time.strftime("%Y%m%d-%H%M%S")
OUT_DIR = Path("C:/Users/MSI/AuraLab/proof/sauth")
TILL_A_PORT = 5061
TILL_B_PORT = 5062
TILL_A_BASE = f"http://127.0.0.1:{TILL_A_PORT}"
TILL_B_BASE = f"http://127.0.0.1:{TILL_B_PORT}"
DATA_A = f"C:/Users/MSI/AuraLab/proof/sauth/till-a-{TAG}"
DATA_B = f"C:/Users/MSI/AuraLab/proof/sauth/till-b-{TAG}"
SHOP_NAME = f"SAuth Proof Shop {TAG}"
ADMIN_EMAIL = f"sauth-admin-{TAG}@proof.lab"
CASHIER_EMAIL = f"sauth-cashier-{TAG}@proof.lab"
LICENCE_KEY_ENV = f"sauth_licence_key_{TAG}"
LICENCE_ID_ENV = f"sauth_licence_id_{TAG}"
# 5061/5060 are in Chromium's hard-coded restricted-port list (SIP/SIP-TLS) --
# unrelated to this app, Chromium refuses to navigate there at all by default.
# This flag is the standard, documented way to lift that for ports a test
# genuinely needs (https://www.chromium.org/for-testers/bug-reporting-guidelines/
# ...restricted ports...) -- not a product change, not a workaround for
# anything this plan's own rule 3 (ports 5061/5062) could have avoided by
# picking different ports, since the plan fixes those ports deliberately.
CHROMIUM_ARGS = ["--explicitly-allowed-ports=5061,5062"]

R = Results(OUT_DIR / "result.json", OUT_DIR / "result.md", OUT_DIR / "screens")


def psql_scalar(sql: str) -> str:
    """Read-only query against the lab's own Postgres, same credentials
    lab_e2e.py already uses for its owner_sync_events probes (loopback-only
    lab database; not a real secret -- see that module's own comment)."""
    proc = subprocess.run(
        [str(PSQL_EXE), "-h", "127.0.0.1", "-U", OWNER_PG_USER, "-d", OWNER_PG_DB, "-tAc", sql],
        env=dict(os.environ, PGPASSWORD=OWNER_PG_PASSWORD),
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr}")
    return proc.stdout.strip()


def phase1_issue_licence(pw) -> dict:
    t0 = time.time()
    env = load_env()
    browser = pw.chromium.launch(args=CHROMIUM_ARGS)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        minted = owner_authenticate(page, env, results=R)
        if minted:
            save_env(minted)
            env = load_env()
        result = owner_issue_license(page, legal_name=SHOP_NAME, target_devices=2, results=R)
        save_env({LICENCE_KEY_ENV: result["key"], LICENCE_ID_ENV: result["license_id"]})
        R.phase("1 issue fresh licence (2 devices)", "PASS",
                f"shop={SHOP_NAME!r} license_id={result['license_id']} "
                f"package={result['package_option_text']!r}", time.time() - t0)
        return result
    except Exception as exc:  # noqa: BLE001
        R.phase("1 issue fresh licence", "FAIL", f"{exc}\n{traceback.format_exc()}", time.time() - t0)
        raise
    finally:
        browser.close()


def phase2_till_a(pw, key: str, admin_password: str) -> None:
    t0 = time.time()
    proc = start_till(TILL_A_PORT, DATA_A, site_relay="auto")
    if not wait_http_ok(TILL_A_BASE + "/api/health", timeout=30):
        R.phase("2a till A boot", "FAIL", f"stdout={proc.stdout} stderr={proc.stderr}", time.time() - t0)
        raise RuntimeError("till A did not boot")

    browser = pw.chromium.launch(args=CHROMIUM_ARGS)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(TILL_A_BASE + "/", wait_until="networkidle")
        page.wait_for_selector("#su-btn", timeout=30000)
        shot(page, "till-a-first-run", results=R)
        page.fill("#su-name", "SAuth Proof Admin")
        page.fill("#su-company", SHOP_NAME)
        page.fill("#su-email", ADMIN_EMAIL)
        page.fill("#su-key", key)
        page.fill("#su-pass", admin_password)
        page.fill("#su-pass2", admin_password)
        page.click("#su-btn")
        page.wait_for_selector("#rl-email", timeout=60000)
        page.fill("#rl-email", ADMIN_EMAIL)
        page.fill("#rl-pass", admin_password)
        page.click("#rl-btn")
        page.wait_for_selector("#sub-header-section", timeout=30000)
        page.wait_for_timeout(1000)
        shot(page, "till-a-signed-in", results=R)

        status = page.request.get(TILL_A_BASE + "/api/licensing/status").json()
        active_ok = status.get("current_state") in ("ACTIVE_ONLINE", "ACTIVE_OFFLINE")
        R.phase("2a till A create-admin + activate", "PASS" if active_ok else "FAIL",
                f"current_state={status.get('current_state')}", time.time() - t0)
        if not active_ok:
            raise RuntimeError(f"till A not active: {status}")
    except Exception as exc:  # noqa: BLE001
        R.phase("2 till A", "FAIL", f"{exc}\n{traceback.format_exc()}", time.time() - t0)
        raise
    finally:
        browser.close()


def phase2_till_b(pw, key: str, admin_password: str) -> None:
    t0 = time.time()
    # site_relay="0": B stays on the cloud relay via Owner rather than racing A
    # for the same-machine LAN hub port -- the same choice phase_q3 (lab_e2e.py
    # S3) makes for B2, for the identical reason.
    proc = start_till(TILL_B_PORT, DATA_B, site_relay="0")
    if not wait_http_ok(TILL_B_BASE + "/api/health", timeout=30):
        R.phase("2b till B boot", "FAIL", f"stdout={proc.stdout} stderr={proc.stderr}", time.time() - t0)
        raise RuntimeError("till B did not boot")

    browser = pw.chromium.launch(args=CHROMIUM_ARGS)
    try:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(TILL_B_BASE + "/", wait_until="networkidle")
        page.wait_for_selector("#su-btn", timeout=30000)
        assert page.locator("#su-join-link").count() == 1, "no join link on a fresh till"
        shot(page, "till-b-first-run", results=R)
        page.click("#su-join-link")
        title = (page.text_content("#su-title") or "").strip()
        if title != "Join your shop":
            raise RuntimeError(f"join-mode title mismatch: {title!r}")
        page.fill("#su-key", key)
        page.click("#su-btn")
        page.wait_for_selector("#su-join-continue-btn", timeout=60000)
        shot(page, "till-b-connected-pending-restart", results=R)
        page.close()

        # The relay address B just learned only takes effect at the next
        # launch (same comment phase_p3 in lab_e2e.py already documents).
        stop_lab(f"till-{TILL_B_PORT}")
        tcp_port_closed(TILL_B_PORT, timeout=20)
        proc = start_till(TILL_B_PORT, DATA_B, site_relay="0")
        if not wait_http_ok(TILL_B_BASE + "/api/health", timeout=30):
            raise RuntimeError(f"till B did not come back after restart: {proc.stderr}")

        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(TILL_B_BASE + "/", wait_until="networkidle")
        page.wait_for_selector("#rl-email", timeout=150000)
        shot(page, "till-b-signin-screen", results=R)
        page.fill("#rl-email", ADMIN_EMAIL)
        page.fill("#rl-pass", admin_password)
        page.click("#rl-btn")
        page.wait_for_selector("#sub-header-section", timeout=30000)
        page.wait_for_timeout(1000)
        shot(page, "till-b-signed-in", results=R)

        status = page.request.get(TILL_B_BASE + "/api/licensing/status").json()
        active_ok = status.get("current_state") in ("ACTIVE_ONLINE", "ACTIVE_OFFLINE")
        R.phase("2b till B join + restart + sign in", "PASS" if active_ok else "FAIL",
                f"current_state={status.get('current_state')}", time.time() - t0)
        if not active_ok:
            raise RuntimeError(f"till B not active: {status}")
    except Exception as exc:  # noqa: BLE001
        R.phase("2b till B", "FAIL", f"{exc}\n{traceback.format_exc()}", time.time() - t0)
        raise
    finally:
        browser.close()


def phase3_main_check(admin_password: str) -> None:
    """Which device is main, read via each till's OWN API, plus Owner's own
    database (read-only) showing the SAME id pinned for A's installation."""
    t0 = time.time()
    a = _login(TILL_A_BASE, ADMIN_EMAIL, admin_password)
    b = _login(TILL_B_BASE, ADMIN_EMAIL, admin_password)

    a_status, a_me = a.call("GET", "/api/devices/me")
    b_status, b_me = b.call("GET", "/api/devices/me")
    a_dev = (a_me.get("device") or {})
    b_dev = (b_me.get("device") or {})
    a_is_main = bool(a_dev.get("is_admin_device"))
    b_is_main = bool(b_dev.get("is_admin_device"))

    a_lic_status, a_lic = a.call("GET", "/api/licensing/status")
    installation_id = a_lic.get("installation_id")
    attested = a_lic.get("registry_device_attested")

    owner_row = None
    if installation_id:
        owner_row = psql_scalar(
            f"SELECT id || '|' || coalesce(registry_device_id,'') || '|' || "
            f"coalesce(registry_device_id_pinned_at::text,'') FROM owner_installations "
            f"WHERE id = '{installation_id}'")
    owner_pinned_dev = owner_row.split("|")[1] if owner_row else None

    ok = (a_is_main and not b_is_main and attested is True
          and owner_pinned_dev == a_dev.get("id") and bool(owner_pinned_dev))
    R.phase("3 MAIN check: A is main on both tills + Owner DB pin matches", "PASS" if ok else "FAIL",
            f"a_dev_id={a_dev.get('id')} a_is_main={a_is_main} b_is_main={b_is_main} "
            f"registry_device_attested(A)={attested} installation_id(A)={installation_id} "
            f"owner_installations.registry_device_id={owner_pinned_dev} "
            f"match={owner_pinned_dev == a_dev.get('id')}", time.time() - t0)
    if not ok:
        raise RuntimeError("MAIN check failed -- see phase detail")


def phase4_allow(admin_password: str) -> None:
    """Legit authority change (a cashier, created by the MAIN) and a legit
    non-authority change (a product) both cross from A to B for real."""
    t0 = time.time()
    a = _login(TILL_A_BASE, ADMIN_EMAIL, admin_password)
    b = _login(TILL_B_BASE, ADMIN_EMAIL, admin_password)

    # -- a) product created on A appears on B -------------------------------
    sku = f"SAUTH-{TAG}"
    a.call("POST", "/api/sub/retail/products", {
        "name": "SAuth Proof Product", "sku": sku, "sell_price": 3.0, "price": 3.0,
        "cost_price": 1.0, "stock": 5, "stock_quantity": 5, "quantity": 5,
        "category": "Lab", "unit": "pc",
    })
    status, plist = a.call("GET", "/api/sub/retail/products")
    prod = find_by(rows_of(plist), "sku", sku)
    if not prod:
        R.phase("4a product create (A)", "FAIL", f"status={status} not listed after create", time.time() - t0)
        raise RuntimeError("product not visible on A after create")
    a.call("POST", f"/api/sub/retail/products/{prod['id']}/stock-adjust",
           {"quantity": 5, "reason": "sauth proof opening stock"})
    ok_prod, seen, elapsed_prod = wait_until(
        lambda: (lambda s, bd: (lambda r: r if r and r.get("total_stock") == 5 else None)(
            find_by(rows_of(bd), "sku", sku)) if s == 200 else None)(*b.call("GET", "/api/sub/retail/products")),
        timeout=180, interval=5, label="product on B")
    R.phase("4a ALLOW: product created on A reaches B", "PASS" if ok_prod else "FAIL",
            f"seen_on_B={bool(seen)} same_id={bool(seen) and seen.get('id') == prod['id']}", elapsed_prod)

    # -- b) cashier created on A signs in on B (real password login, 200) ---
    status, emp_body = a.call("POST", "/api/admin/employees", {"email": CASHIER_EMAIL, "role": "cashier"})
    setup_link = emp_body.get("setup_link", "") if status == 200 else ""
    m = re.search(r"#setup/([0-9a-f]+)$", setup_link)
    cashier_password = uuid.uuid4().hex[:10] + "Aa1!"
    setup_ok = False
    if m:
        setup_status, _ = a.call("POST", "/api/auth/employee/setup", {"token": m.group(1), "password": cashier_password})
        setup_ok = setup_status == 200
    ok_login, _, elapsed_login = wait_until(
        lambda: True if _login(TILL_B_BASE, CASHIER_EMAIL, cashier_password) else None,
        timeout=180, interval=5, label="cashier login on B")
    R.phase("4b ALLOW: cashier created on A signs in on B (HTTP 200)",
            "PASS" if (setup_ok and ok_login) else "FAIL",
            f"create_status={status} setup_ok={setup_ok} signed_in_on_B_after={elapsed_login:.0f}s", time.time() - t0)
    if not (ok_prod and setup_ok and ok_login):
        raise RuntimeError("ALLOW phase did not fully pass -- see phase detail")


def phase7_cleanup() -> None:
    t0 = time.time()
    stop_lab(f"till-{TILL_A_PORT}")
    stop_lab(f"till-{TILL_B_PORT}")
    a_closed = tcp_port_closed(TILL_A_PORT, timeout=30)
    b_closed = tcp_port_closed(TILL_B_PORT, timeout=30)
    from lab_e2e import http_get_ok, OWNER_BASE  # noqa: E402 -- local import, see module docstring
    owner_up = http_get_ok(OWNER_BASE + "/auth/login")
    ok = a_closed and b_closed and owner_up
    R.phase("7 cleanup: stop till A/B only, Owner still up", "PASS" if ok else "FAIL",
            f"5061_closed={a_closed} 5062_closed={b_closed} owner_up={owner_up}", time.time() - t0)


def main() -> None:
    from playwright.sync_api import sync_playwright

    R.phase("5/6 forged authority event + mutation proof", "SKIPPED",
            "Blocked while scripts/lab/lab_sauth_proof.py was being written -- see "
            "progress.md for this run (C:/Users/MSI/.claude/jobs/f8252b48/tmp/sauthproof/"
            "progress.md). Not attempted here. The mechanism this would port to the "
            "real relay already exists, reviewed and merged, in "
            "products/retail/tests/_insider_forge.py.", 0.0)

    with sync_playwright() as pw:
        result = phase1_issue_licence(pw)
        key = result["key"]
        env = load_env()
        admin_password = uuid.uuid4().hex[:10] + "Aa1!"
        try:
            phase2_till_a(pw, key, admin_password)
            phase2_till_b(pw, key, admin_password)
            phase3_main_check(admin_password)
            phase4_allow(admin_password)
        finally:
            phase7_cleanup()

    log(f"result.json -> {OUT_DIR / 'result.json'}")
    log(f"result.md   -> {OUT_DIR / 'result.md'}")


if __name__ == "__main__":
    main()
