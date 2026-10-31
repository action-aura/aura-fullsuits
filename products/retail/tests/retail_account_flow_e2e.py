"""Real-browser E2E for the new customer flow: the REAL retail backend (app.py, unmodified) + REAL
Chromium + a FAKE Owner (fake_owner_server.py).

STANDALONE SCRIPT, NOT a pytest file (same reason as retail_smoke_e2e.py: Playwright lives in the
SYSTEM Python, the backend and the fake Owner need the repo venv). Run with the SYSTEM python:
  C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe products/retail/tests/retail_account_flow_e2e.py

  A. a fresh install opens -> is sent to "Sign in with your Aura account" (not a licence-key form)
     -> a WRONG password is refused and the field is blanked
     -> the temporary password -> forced password change (too short / mismatch refused)
     -> the contract modal: cannot be dismissed (Escape, click outside), box locked until scrolled to the
        end, Accept locked until ticked, DRAFT watermark -> Accept
     -> activate -> ROLE step -> ADMIN (e-mail prefilled, separate local password) -> Employees
     -> create a cashier user -> the cashier signs in -> a real sale is accepted
     -> the Aura passwords are nowhere: not storage, not the DOM, not any file in the app data, not the log
  B. a second fresh install, contract now lawyer-approved: no DRAFT watermark; no password step (already
     changed); -> ROLE step -> CASHIER -> the wait screen, and NO create-account form.

Mutation mode: ACCT_MUTATE=<key> serves a deliberately broken copy of one frontend file to the browser
(the file on disk is never touched). The run must then go RED -- see MUTANTS.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

WT = Path(__file__).resolve().parents[3]
OUT = Path(tempfile.mkdtemp(prefix="aura_account_flow_e2e_"))
VENV = "C:/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe"
FAKE = str(Path(__file__).resolve().with_name("fake_owner_server.py"))

for k in [k for k in os.environ if k.startswith("AURA_")]:
    del os.environ[k]
os.environ["AURA_SITE_RELAY_ENABLED"] = "0"

spec = importlib.util.spec_from_file_location("smoke", WT / "products/retail/tests/retail_smoke_e2e.py")
smoke = importlib.util.module_from_spec(spec)
sys.modules["smoke"] = smoke
spec.loader.exec_module(smoke)
smoke.HEALTH_TIMEOUT_SECONDS = 150.0
WAIT_MS = 60_000

# Distinctive values: a grep for them in storage / files / logs can only match the real thing.
EMAIL = "boss.shop@example.test"
TEMP_PW = "Temp-" + "Sentinel-7Q4Z"        # the seller-typed temporary password
NEW_PW = "Fresh-" + "Sentinel-9K2M-pw"     # what the customer chooses at the forced change
LOCAL_PW = "LocalBoss-" + "Sentinel-3X8V"   # the SEPARATE till password of the boss account

MUTANTS = {
    # key: (file glob served, old, new)
    "esc_closes": ("**/licensing-account.js", "if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); }",
                   "if (ev.key === 'Escape') { close(); }"),
    "click_outside_closes": ("**/licensing-account.js", "overlay.appendChild(card);",
                             "overlay.appendChild(card); overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });"),
    "no_scroll_gate": ("**/licensing-account.js", "const agree = el('input', { id: 'contract-agree', type: 'checkbox', disabled: true });",
                       "const agree = el('input', { id: 'contract-agree', type: 'checkbox' });"),
    "accept_ignores_tick": ("**/licensing-account.js", "accept.disabled = !(reachedEnd && agree.checked);",
                            "accept.disabled = !reachedEnd;"),
    "draft_hidden": ("**/licensing-account.js", "if (c.draft) {", "if (false) {"),
    "draft_always": ("**/licensing-account.js", "if (c.draft) {", "if (true) {"),
    "password_mismatch_ok": ("**/licensing-account.js", "if (a.input.value !== b.input.value) {", "if (false) {"),
    "password_not_blanked": ("**/licensing-account.js", "const pending = api.post('/api/licensing/account/sign-in', { email: typedEmail, password: p.input.value });\n          p.input.value = '';",
                             "const pending = api.post('/api/licensing/account/sign-in', { email: typedEmail, password: p.input.value });"),
    "password_in_storage": ("**/licensing-account.js", "const pending = api.post('/api/licensing/account/sign-in', { email: typedEmail, password: p.input.value });",
                            "try { sessionStorage.setItem('pw', p.input.value); } catch (e) {} const pending = api.post('/api/licensing/account/sign-in', { email: typedEmail, password: p.input.value });"),
    "role_step_skipped": ("**/app-shell.js", "if (roleStep && !this._needsActivation(lic)) {", "if (false) {"),
    # rc.19: the step lives on the device's backend, not only in localStorage. Blind the server read and a
    # reload with a wiped localStorage must go RED (A6f).
    "role_server_ignored": ("**/app-shell.js", "if (body && body.role_step && body.role_step.state) return body.role_step;", ""),
    # rc.19: a reload AFTER choosing boss resumes the create-account form, not the join wait screen (A7e).
    "admin_resume_lost": ("**/app-shell.js", "if (roleStep.state === 'ADMIN') {", "if (false) {"),
    # rc.19: with no other device known the wait screen must not promise an account "on its way" (B5e).
    "wait_always_on_its_way": ("**/app-shell.js", "const knowsOtherDevice = !!(opts && opts.otherDevice);", "const knowsOtherDevice = true;"),
    "no_email_prefill": ("**/app-shell.js", "if (emailEl && !emailEl.value) emailEl.value = this._setupPrefillEmail;", ""),
    "cashier_gets_setup": ("**/app-shell.js", "    return this._waitForShopAccount(undefined, undefined, { otherDevice: true });\n  },\n\n  // ── First-run brand intro",
                           "    return this.showSetupModal();\n  },\n\n  // ── First-run brand intro"),
    "no_gate_redirect": ("**/app-shell.js",
                         "if (this._needsActivation(lic) && lic.current_state !== 'ACTIVATING' && !this._isActivationPending()) {\n        location.href = '/static/licensing.html?gate=1';",
                         "if (false) {\n        location.href = '/static/licensing.html?gate=1';"),
}
MUTATE = os.environ.get("ACCT_MUTATE")

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (("  :: " + str(detail)) if detail else ""), flush=True)


def fake_get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


class Fake:
    def __init__(self, approved):
        self.dir = Path(tempfile.mkdtemp(prefix="fake_owner_", dir=str(OUT)))
        self.proc = subprocess.Popen([VENV, FAKE, str(self.dir), EMAIL, TEMP_PW, "--approved", "1" if approved else "0"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        t0 = time.time()
        while not (self.dir / "port.txt").exists():
            if time.time() - t0 > 60:
                raise RuntimeError("fake Owner did not start")
            time.sleep(0.3)
        self.port = int((self.dir / "port.txt").read_text())
        self.anchor = str(self.dir / "anchor.json")

    def state(self):
        return fake_get(self.port, "/__state")

    def stop(self):
        self.proc.terminate()


def point_backend_at(fake):
    os.environ["AURA_OWNER_LICENSING_URL"] = f"http://127.0.0.1:{fake.port}/api/licensing/v1"
    os.environ["AURA_LICENSING_TRUST_ANCHOR_PATH"] = fake.anchor
    os.environ["AURA_LICENSING_TRUST_ANCHOR_OVERRIDE_CONFIRM"] = "1"


def install_mutation(page):
    if not MUTATE:
        return
    glob, old, new = MUTANTS[MUTATE]

    def handler(route):
        resp = route.fetch()
        body = resp.text()
        if old not in body:
            print(f"MUTATION ANCHOR NOT FOUND for {MUTATE}", flush=True)
            sys.exit(3)
        route.fulfill(response=resp, body=body.replace(old, new, 1))
    page.route(glob, handler)


def visible(page, selector, timeout=WAIT_MS):
    try:
        page.locator(selector).first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def sign_in(page, password):
    page.fill("#account-email", EMAIL)
    page.fill("#account-password", password)
    page.click("#account-signin-btn")


def scan_for(needles, roots, extra_texts):
    """Which needles appear in any file under roots (raw bytes, so sqlite/WAL pages count) or extra text."""
    hits = []
    for root in roots:
        for f in Path(root).rglob("*"):
            if not f.is_file():
                continue
            try:
                data = f.read_bytes()
            except OSError:
                continue
            for n in needles:
                if n.encode("utf-8") in data:
                    hits.append((str(f.name), n))
    for label, text in extra_texts:
        for n in needles:
            if n in (text or ""):
                hits.append((label, n))
    return hits


def scenario_admin(pw, fake, backend):
    page = pw.new_page(viewport=smoke.VIEWPORT, reduced_motion="reduce")
    install_mutation(page)
    page.goto(backend.base_url, wait_until="domcontentloaded")
    # A1: lands on the account sign-in, not a licence-key form
    ok = visible(page, "#account-email")
    check("A1 a fresh install is sent to 'Sign in with your Aura account'", ok and "licensing.html" in page.url, page.url)
    check("A1b the licence-key field is folded away, not the first thing", not page.locator("#license-key-input").is_visible())
    page.screenshot(path=str(OUT / "A1_sign_in.png"))

    # A2: a wrong password is refused, blanked, and the screen stays
    sign_in(page, "definitely-wrong-password")
    page.wait_for_timeout(1500)
    msg = page.locator("#message-area").inner_text() if page.locator("#message-area").count() else ""
    check("A2 a wrong password is refused with a plain sentence", "not right" in msg, msg)
    check("A2b the wrong password is blanked from the field", page.input_value("#account-password") == "", repr(page.input_value("#account-password")))
    check("A2c still on the sign-in step", page.locator("#account-signin-btn").is_visible())

    # A3: the temporary password -> forced password change
    sign_in(page, TEMP_PW)
    check("A3 the temporary password leads to the forced password change", visible(page, "#account-new-password"))
    page.fill("#account-new-password", "short")
    page.fill("#account-new-password2", "short")
    page.click("#account-password-btn")
    page.wait_for_timeout(600)
    check("A3b a too-short password is refused", page.locator("#account-new-password").is_visible()
          and "10 characters" in page.locator("#message-area").inner_text())
    page.fill("#account-new-password", NEW_PW)
    page.fill("#account-new-password2", NEW_PW + "x")
    page.click("#account-password-btn")
    page.wait_for_timeout(600)
    check("A3c two different passwords are refused (no request needed)", page.locator("#account-new-password").is_visible()
          and "not the same" in page.locator("#message-area").inner_text())
    page.fill("#account-new-password", NEW_PW)
    page.fill("#account-new-password2", NEW_PW)
    page.click("#account-password-btn")

    # A4: the contract modal
    check("A4 the contract modal opens after the password change", visible(page, "#contract-modal"))
    check("A4b DRAFT watermark shown (version not lawyer-approved)", page.locator("#contract-draft").count() == 1)
    check("A4c the heading is Arabic and the body is right-to-left",
          page.get_attribute("#contract-title", "lang") == "ar" and page.get_attribute("#contract-body", "dir") == "rtl")
    check("A4d the box is locked before the end is reached", page.locator("#contract-agree").is_disabled())
    check("A4e Accept is locked", page.locator("#contract-accept-btn").is_disabled())
    page.keyboard.press("Escape")
    page.mouse.click(4, 4)
    page.wait_for_timeout(300)
    check("A4f Escape and a click outside do NOT dismiss it", page.locator("#contract-modal").count() == 1)
    page.evaluate("() => { const b = document.getElementById('contract-body'); b.scrollTop = b.scrollHeight; }")
    page.wait_for_timeout(300)
    check("A4g reaching the end unlocks the box", page.locator("#contract-agree").is_enabled())
    check("A4h ... but Accept stays locked until it is ticked", page.locator("#contract-accept-btn").is_disabled())
    page.check("#contract-agree")
    check("A4i ticking unlocks Accept", page.locator("#contract-accept-btn").is_enabled())
    page.uncheck("#contract-agree")
    check("A4j un-ticking locks Accept again", page.locator("#contract-accept-btn").is_disabled())
    page.screenshot(path=str(OUT / "A4_contract.png"))
    page.check("#contract-agree")
    page.click("#contract-accept-btn")

    # A5: activate
    check("A5 one licence -> ready to activate", visible(page, "#account-activate-btn"))
    page.click("#account-activate-btn")

    # A6: role step
    check("A6 after activation the app asks which computer this is", visible(page, "#role-admin-btn"))
    check("A6b the licensing page no longer shows", "licensing.html" not in page.url, page.url)
    page.screenshot(path=str(OUT / "A6_role.png"))
    st = fake.state()
    check("A6c Owner recorded exactly one acceptance of the exact text",
          len(st["acceptances"]) == 1 and len(st["acceptances"][0]["sha256"]) == 64, st["acceptances"])
    check("A6d Owner activated by account ticket", [a["kind"] for a in st["activations"]] == ["account_ticket"], st["activations"])
    check("A6e every signed request verified (no bad signature)", st["bad_signatures"] == 0, st["bad_signatures"])

    # A6f (rc.19): a reload / restart before choosing shows the role step AGAIN -- even with the webview's
    # localStorage wiped, because the step is kept on the device's own backend.
    page.evaluate("() => localStorage.clear()")
    page.reload(wait_until="domcontentloaded")
    check("A6f a reload (localStorage wiped) before choosing still shows the role step",
          visible(page, "#role-admin-btn") and not page.locator("text=Connecting to your shop").is_visible())

    # A7: ADMIN
    page.click("#role-admin-btn")
    check("A7 ADMIN opens the create-account form", visible(page, "#su-name"))
    check("A7b the Aura e-mail is prefilled", page.input_value("#su-email") == EMAIL, page.input_value("#su-email"))
    # A7e (rc.19): a reload AFTER choosing boss resumes the create-account form (it used to drop on
    # "Connecting to your shop... your account is on its way", a lie for a first device), and the role
    # step is not asked a second time.
    page.reload(wait_until="domcontentloaded")
    check("A7e reload after choosing boss resumes create-account, not the wait screen or the role step",
          visible(page, "#su-name") and not page.locator("#role-admin-btn").is_visible()
          and not page.locator("text=Connecting to your shop").is_visible())
    check("A7f ... with the Aura e-mail still prefilled", page.input_value("#su-email") == EMAIL, page.input_value("#su-email"))
    page.fill("#su-name", "Boss Person")
    page.fill("#su-company", "Account Flow Co")
    page.fill("#su-pass", LOCAL_PW)
    page.fill("#su-pass2", LOCAL_PW)
    page.click("#su-btn")
    if not visible(page, ".sub-nav-item"):
        page.screenshot(path=str(OUT / "A7_after_create_FAILED.png"))
        check("A7g the boss account is created and the app opens", False, page.inner_text("body")[:300].replace(chr(10), " | "))
        return
    page.wait_for_timeout(1500)
    active = page.evaluate("""() => { const a = document.getElementById('sub-header-section'); return a ? a.innerText : null; }""")
    check("A7c the new boss computer lands on Employees", active is not None and "Employees" in active, active)
    dev = page.evaluate("""async () => { const r = await fetch('/api/devices/me', {credentials:'include'}); return r.json(); }""")
    check("A7d this computer is the main (admin) computer",
          bool(dev and dev.get("device") and dev["device"].get("is_admin_device")), dev.get("device", {}).get("is_admin_device") if dev else dev)

    def api(method, path, body=None):
        return page.evaluate(
            """async ({method, path, body}) => {
                const r = await fetch(path, {method, credentials: 'include',
                  headers: body !== null ? {'Content-Type': 'application/json'} : {},
                  body: body !== null ? JSON.stringify(body) : undefined});
                let j = null; try { j = await r.json(); } catch (e) {}
                return {status: r.status, body: j};
            }""", {"method": method, "path": path, "body": body})

    # A8: cashier user, sign-in, sale
    pr = api("POST", "/api/sub/retail/products", {"name": "Flow Widget", "sku": "FLOW-1", "sell_price": 5.0, "tax_rate": 0, "initial_stock": 50})
    pid = ((pr["body"] or {}).get("data") or {}).get("id")
    check("A8 the boss creates a product", pr["status"] == 200 and pid, pr["status"])
    emp = api("POST", "/api/admin/employees", {"email": "cashier.one@example.test", "role": "cashier", "name": "Cash One"})
    link = (emp["body"] or {}).get("setup_link", "")
    token = link.split("#setup/")[-1] if "#setup/" in link else ""
    check("A8b the boss creates a cashier user", emp["status"] == 200 and token, emp["status"])
    cash_pw = "Cashier-Pass-" + "5521"
    setup = api("POST", "/api/auth/employee/setup", {"token": token, "password": cash_pw})
    check("A8c the cashier sets a password from the setup link", setup["status"] == 200, setup)
    cashier = pw.new_page()
    cashier.goto(backend.base_url + "/static/licensing.html", wait_until="domcontentloaded")
    login = cashier.evaluate("""async ({email, password}) => { const r = await fetch('/api/auth/login', {method:'POST', credentials:'include',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({email, password})}); return r.status; }""",
                             {"email": "cashier.one@example.test", "password": cash_pw})
    check("A8d the cashier signs in", login == 200, login)
    sale = cashier.evaluate("""async (pid) => { const r = await fetch('/api/sub/retail/sales', {method:'POST', credentials:'include',
        headers:{'Content-Type':'application/json'}, body: JSON.stringify({items:[{product_id: pid, quantity: 1}]})});
        return {status: r.status}; }""", pid)
    check("A8e a real sale by the cashier is accepted", sale["status"] in (200, 201), sale)
    cashier.close()

    # A9: the Aura passwords are nowhere
    storage = page.evaluate("""() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])""")
    check("A9 the Aura passwords are not in browser storage", not scan_for([TEMP_PW, NEW_PW], [], [("storage", storage)]), storage[:200])
    dom = page.content()
    check("A9b ... nor in the page", not scan_for([TEMP_PW, NEW_PW], [], [("dom", dom), ("url", page.url)]))
    hits = scan_for([TEMP_PW, NEW_PW], [backend.app_data_dir], [])
    check("A9c ... nor in any file the app wrote (databases, WAL, logs)", not hits, hits[:5])
    sent = [r for r in fake.state()["requests"] if r["path"].endswith("/account-sign-ins")]
    check("A9d the sign-in request really carried (signed) credentials", sent and "password" in sent[0]["keys"], sent[:1])
    page.close()


def scenario_cashier(pw, fake, backend):
    page = pw.new_page(viewport=smoke.VIEWPORT, reduced_motion="reduce")
    install_mutation(page)
    page.goto(backend.base_url, wait_until="domcontentloaded")
    check("B1 second fresh install: account sign-in", visible(page, "#account-email"))
    sign_in(page, NEW_PW)   # the password was already changed in scenario A
    check("B2 contract modal (no password step: already changed)", visible(page, "#contract-modal")
          and page.locator("#account-new-password").count() == 0)
    check("B2b NO draft watermark on an approved text", page.locator("#contract-draft").count() == 0)
    page.evaluate("() => { const b = document.getElementById('contract-body'); b.scrollTop = b.scrollHeight; }")
    page.check("#contract-agree")
    page.click("#contract-accept-btn")
    check("B3 ready to activate", visible(page, "#account-activate-btn"))
    page.click("#account-activate-btn")
    check("B4 role step appears", visible(page, "#role-cashier-btn"))
    page.click("#role-cashier-btn")
    check("B5 CASHIER waits for the shop's account", visible(page, "text=Connecting to your shop"))
    check("B5b ... and is NOT offered a create-account form", page.locator("#su-name").count() == 0)
    page.screenshot(path=str(OUT / "B5_cashier_wait.png"))
    check("B5c a cashier computer is told its account is on its way from the other device",
          "on its way" in page.locator("#aura-relogin-modal").inner_text())
    # B5d (rc.19): a restart after choosing cashier keeps waiting -- the role step is not asked again.
    page.reload(wait_until="domcontentloaded")
    check("B5d reload after choosing cashier: still the wait screen, role step NOT asked again",
          visible(page, "text=Connecting to your shop") and not page.locator("#role-cashier-btn").is_visible())
    # B5e (rc.19): when nothing says another device exists, the wait screen must NOT say an account is
    # "on its way" (that sentence is false for the first computer of a new shop).
    page.evaluate("() => { SubsystemApp._stopWaitForShopAccountPoll(); SubsystemApp._waitForShopAccount(); }")
    neutral = page.locator("#aura-relogin-modal").inner_text()
    page.evaluate("() => SubsystemApp._stopWaitForShopAccountPoll()")
    check("B5e no known other device: the wait screen does not claim an account is on its way",
          "Connecting to your shop" in neutral and "on its way" not in neutral, neutral[:200])
    page.close()


def main():
    from playwright.sync_api import sync_playwright
    fake = Fake(approved=False)
    backend = backend2 = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                class PW:
                    def new_page(self, **kw):
                        return browser.new_context(**kw).new_page()
                pw = PW()
                point_backend_at(fake)
                backend = smoke.Backend()
                backend.start()
                scenario_admin(pw, fake, backend)
                backend.stop()
                fake.proc.terminate()
                fake = Fake(approved=True)
                # the same account, now with its password already changed
                point_backend_at(fake)
                backend2 = smoke.Backend()
                backend2.start()
                # the new fake starts with the TEMP password and must_change; mirror scenario A's change
                import urllib.request as ur
                ur.urlopen(ur.Request(f"http://127.0.0.1:{fake.port}/__set", data=json.dumps({"approved": True}).encode(),
                                      headers={"Content-Type": "application/json"}), timeout=10).read()
                scenario_cashier_prepare(fake)
                scenario_cashier(pw, fake, backend2)
            finally:
                browser.close()
    finally:
        for b in (backend, backend2):
            if b:
                b.stop()
        fake.stop()
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed  (artifacts: {OUT})", flush=True)
    for name, _, detail in failed:
        print("  FAILED:", name, detail)
    sys.exit(1 if failed else 0)


def scenario_cashier_prepare(fake):
    """The second fake Owner is brand new, so it still holds the TEMP password. Scenario B signs in with
    the password scenario A chose, so set that on the fake's account the way Owner would have it."""
    urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{fake.port}/__account", data=json.dumps({"email": EMAIL, "password": NEW_PW, "must_change": False}).encode(),
        headers={"Content-Type": "application/json"}), timeout=10).read()


if __name__ == "__main__":
    main()
