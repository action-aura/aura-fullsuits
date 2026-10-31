"""Real-browser E2E for the 7-day trial lock: the REAL retail backend (app.py, unmodified) + REAL Chromium.

STANDALONE SCRIPT, NOT a pytest file (same reason as retail_smoke_e2e.py: Playwright lives in the SYSTEM
Python, the lease seeder needs the venv). Run with the SYSTEM python:
  C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe products/retail/tests/retail_trial_lock_e2e.py
Screenshots and downloaded files go to a temp folder printed at the end.

Sequence (signup first while unlicensed, THEN seed, per retail_smoke_e2e.py's harness note):
  1. fresh install, owner signs up through the real sign-up form
  2. seed a forged PILOT lease whose term_end is TOMORROW  -> Check Now -> still selling, a real POST /sales = 200
  3. seed a forged PILOT lease whose term_end is in the PAST -> restart the backend with a dead Owner URL (so
     the till is genuinely OFFLINE) -> Check Now -> state flips to TRIAL_ENDED by evaluation (the seeder never
     writes that state), POST /sales = 403, backup create = 200 + a real file download, reads = 200,
     the licensing page shows the lock screen; screenshots saved
  4. flip the lease to a PAID ACTIVE one (term_end next year) -> Check Now -> unlocked, POST /sales = 200
"""
import importlib.util
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
import tempfile
OUT = Path(tempfile.mkdtemp(prefix="aura_trial_lock_e2e_"))
SEED = str(Path(__file__).resolve().with_name("retail_trial_lock_seed.py"))
VENV = "C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe"   # same venv retail_smoke_e2e.py uses

for k in [k for k in os.environ if k.startswith("AURA_")]:
    del os.environ[k]
os.environ["AURA_SITE_RELAY_ENABLED"] = "0"

spec = importlib.util.spec_from_file_location("smoke", WT / "products/retail/tests/retail_smoke_e2e.py")
smoke = importlib.util.module_from_spec(spec)
sys.modules["smoke"] = smoke
spec.loader.exec_module(smoke)

# The smoke harness waits 30 s for /api/health -- plenty on an idle machine, not when a full test suite is
# running beside it (measured: backend boot >30 s at 100% CPU). A slow boot is not a lock failure.
smoke.HEALTH_TIMEOUT_SECONDS = 150.0
WAIT_MS = 60_000

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (("  :: " + str(detail)) if detail else ""), flush=True)


def banner_text(page):
    """The licence banner's text once it has painted (it is drawn after a status fetch), or None."""
    try:
        page.wait_for_function("() => !!document.getElementById('aura-license-banner')", timeout=WAIT_MS)
    except Exception:  # noqa: BLE001 -- no banner at all is a legitimate answer for the caller to judge
        return None
    return page.evaluate("() => document.getElementById('aura-license-banner').innerText")


def seed(app_data, term_end, status="PILOT"):
    r = subprocess.run([VENV, SEED, str(app_data), term_end, status], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def stored_state(app_data):
    import sqlite3
    con = sqlite3.connect(str(Path(app_data) / "database" / "subsystems" / "licensing.db"))
    try:
        return con.execute("select current_state, subscription_status from licensing_state").fetchone()
    finally:
        con.close()


def main():
    from playwright.sync_api import sync_playwright

    today = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    past = (today - timedelta(days=10)).isoformat()
    nextyear = (today + timedelta(days=365)).isoformat()

    backend = smoke.Backend()
    backend2 = None
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            backend.start()
            ctx = browser.new_context(viewport=smoke.VIEWPORT, reduced_motion="reduce")
            page = ctx.new_page()
            page.goto(backend.base_url, wait_until="domcontentloaded")
            smoke._dismiss_intro_overlay_if_present(page)
            page.locator("#su-name").wait_for(state="visible", timeout=WAIT_MS)
            page.fill("#su-name", "Trial Proof Admin")
            page.fill("#su-company", "Trial Proof Co")
            page.fill("#su-email", "trial-proof@example.test")
            page.fill("#su-pass", "TrialProof123!")
            page.fill("#su-pass2", "TrialProof123!")
            page.click("#su-btn")
            page.locator(".sub-nav-item").first.wait_for(state="visible", timeout=WAIT_MS)
            check("1 signed up through the real form", True)

            def api(method, path, body=None):
                return page.evaluate(
                    """async ({method, path, body}) => {
                        const r = await fetch(path, {method, credentials: 'include',
                          headers: body !== null ? {'Content-Type': 'application/json'} : {},
                          body: body !== null ? JSON.stringify(body) : undefined});
                        let j = null; try { j = await r.json(); } catch (e) {}
                        return {status: r.status, body: j};
                    }""", {"method": method, "path": path, "body": body})

            # A fresh install is unlicensed = read-only (403 on product create), so give it a PAID lease first.
            seed(backend.app_data_dir, nextyear, "ACTIVE")
            pr = api("POST", "/api/sub/retail/products", {
                "name": "Proof Widget", "sku": "TRIAL-PROOF-1", "sell_price": 5.0, "tax_rate": 0, "initial_stock": 100})
            pid = ((pr["body"] or {}).get("data") or {}).get("id")
            check("1b product created", pr["status"] == 200 and pid, pr["status"])

            # ---- 2. PILOT, term_end tomorrow -> sells ----------------------------------------------
            info = seed(backend.app_data_dir, tomorrow)
            backend.stop()
            os.environ["AURA_OWNER_LICENSING_URL"] = "https://127.0.0.1:9"   # dead on purpose: the till is OFFLINE
            backend2 = smoke.Backend(app_data_dir=backend.app_data_dir)
            backend2.start()
            page.goto(backend2.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            login = page.evaluate("""async () => { const r = await fetch('/api/auth/login', {method:'POST', credentials:'include',
                headers:{'Content-Type':'application/json'}, body: JSON.stringify({email:'trial-proof@example.test', password:'TrialProof123!'})});
                return r.status; }""")
            chk = api("POST", "/api/licensing/check-in", {})
            check("2 PILOT term_end=tomorrow: check-in (offline) keeps a selling state",
                  chk["status"] == 200 and chk["body"]["current_state"] not in ("TRIAL_ENDED", "RESTRICTED"),
                  {"state": chk["body"].get("current_state"), "trial": chk["body"].get("trial"), "login": login})
            sale = api("POST", "/api/sub/retail/sales", {"items": [{"product_id": pid, "quantity": 1}]})
            check("2b a real sale is accepted during the trial", sale["status"] == 200, sale["status"])
            page.goto(backend2.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            banner = banner_text(page)
            check("2c the till shows the 'Trial: N days left' bar", banner and "Trial:" in banner, banner)
            page.screenshot(path=str(OUT / "02_trial_running.png"))

            # ---- 3. PILOT, term_end in the past, offline -> TRIAL_ENDED --------------------------------
            seed(backend.app_data_dir, past)
            before = stored_state(backend.app_data_dir)
            check("3 premise: the seeder stored ACTIVE_ONLINE, not the lock", before[0] == "ACTIVE_ONLINE", before)
            chk = api("POST", "/api/licensing/check-in", {})
            after = stored_state(backend.app_data_dir)
            check("3a check-in (offline) EVALUATES the lease to TRIAL_ENDED",
                  after[0] == "TRIAL_ENDED" and chk["body"]["current_state"] == "TRIAL_ENDED", {"stored": after, "trial": chk["body"].get("trial")})
            sale = api("POST", "/api/sub/retail/sales", {"items": [{"product_id": pid, "quantity": 1}]})
            check("3b the till REFUSES a sale (403 LICENSE_INACTIVE)",
                  sale["status"] == 403 and (sale["body"] or {}).get("reason_code") == "LICENSE_INACTIVE", sale)
            lst = api("GET", "/api/sub/retail/products")
            check("3c reads still work (200)", lst["status"] == 200, lst["status"])
            bk = api("POST", "/api/backup/create", {})
            fname = (bk["body"] or {}).get("filename")
            check("3d BACKUP SUCCEEDS while locked", bk["status"] == 200 and fname, {"status": bk["status"], "file": fname})
            exp = page.evaluate("""async () => { const r = await fetch('/api/sub/retail/reports/export/sales?date_from=2000-01-01&date_to=2099-12-31', {credentials:'include'});
                return {status: r.status, ct: r.headers.get('content-type'), len: (await r.text()).length}; }""")
            check("3e EXPORT succeeds while locked (CSV)", exp["status"] == 200 and "csv" in (exp["ct"] or ""), exp)

            page.goto(backend2.base_url + "/static/licensing.html", wait_until="domcontentloaded")
            page.locator("#trial-ended-panel").wait_for(state="visible", timeout=WAIT_MS)
            panel_text = page.locator("#trial-ended-panel").inner_text()
            check("3f the lock screen is on /static/licensing.html with how-to-pay text",
                  "Your data is safe" in panel_text and "Contact your Aura seller" in panel_text and "unlocks automatically" in panel_text,
                  panel_text[:160].replace("\n", " | "))
            page.screenshot(path=str(OUT / "03_lock_screen.png"), full_page=True)
            with page.expect_download(timeout=WAIT_MS) as dl:
                page.click("#trial-backup-btn")
            d = dl.value
            saved = OUT / ("downloaded_" + d.suggested_filename)
            d.save_as(str(saved))
            check("3g the lock screen's Backup button downloads a real backup file", saved.exists() and saved.stat().st_size > 1000,
                  {"file": saved.name, "bytes": saved.stat().st_size})
            with page.expect_download(timeout=WAIT_MS) as dl2:
                page.click("#trial-export-btn")
            d2 = dl2.value
            saved2 = OUT / ("downloaded_" + d2.suggested_filename)
            d2.save_as(str(saved2))
            check("3h the lock screen's Export button downloads a CSV", saved2.exists() and saved2.name.endswith(".csv"),
                  {"file": saved2.name, "bytes": saved2.stat().st_size})
            page.goto(backend2.base_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            banner = banner_text(page)
            check("3i the till shows the trial-ended banner", banner and "Your free trial has ended" in banner, banner)
            page.screenshot(path=str(OUT / "03_till_locked.png"))

            # ---- 4. payment lands: PAID lease -> unlocks, no restart --------------------------------------
            seed(backend.app_data_dir, nextyear, "ACTIVE")
            chk = api("POST", "/api/licensing/check-in", {})
            sale = api("POST", "/api/sub/retail/sales", {"items": [{"product_id": pid, "quantity": 1}]})
            check("4 a paid lease unlocks the same running backend (no restart): sale 200",
                  sale["status"] == 200 and chk["body"]["current_state"] != "TRIAL_ENDED",
                  {"state": chk["body"]["current_state"], "sale": sale["status"]})
            # rc.19: after paying, the licence page said only "Active". It must say until when.
            st4 = api("GET", "/api/licensing/status")
            page.goto(backend2.base_url + "/static/licensing.html", wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            card = page.inner_text("#content") if page.locator("#content").count() else page.inner_text("body")
            page.screenshot(path=str(OUT / "04_paid_licence_page.png"))
            check("4b a paid shop's licence page says 'Paid until <term_end>' (and no trial countdown)",
                  ("Paid until" in card) and (nextyear in card) and ("Free trial" not in card),
                  {"card": card[:200], "state": (st4["body"] or {}).get("current_state")})
        finally:
            browser.close()
            for b in (backend2, backend):
                if b is not None:
                    b.stop()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed  (artefacts: {OUT})")
    (OUT / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    sys.exit(1 if failed else 0)


main()
