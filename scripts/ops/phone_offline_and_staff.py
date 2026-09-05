"""Two phone tests that never touch the screen (adb forward to the embedded
backend only):

  A. staff created FROM THE PHONE (as the owner, now logged in there) reaches
     the laptop and can log in on the laptop -- the reverse of what was
     proved on 2026-09-05.
  B. offline sale: cut the phone's link to the relay (adb reverse removed),
     ring a sale on the phone, restore the link, watch the sale land on the
     laptop. The offline-first promise on real hardware.
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

ADB = r"C:\Users\MSI\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe"
DESK = "http://127.0.0.1:5010"
OWNER = {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"}
TAG = time.strftime("%H%M%S")
NEW_EMAIL = f"phone-made-cashier-{TAG}@rehearsal.local"
NEW_PASS = "PhoneCashier2026!Pass"
WAIT = 120


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except Exception:  # noqa: BLE001
                return exc.code, {"raw": raw[:120].decode(errors="replace")}


def adb(*args):
    return subprocess.run([ADB, *args], capture_output=True, text=True, check=False).stdout.strip()


def phone_base():
    for local, remote in ((18080, 5000), (18081, 5001)):
        adb("forward", f"tcp:{local}", f"tcp:{remote}")
        try:
            if Client(f"http://127.0.0.1:{local}").call("GET", "/api/health")[0] == 200:
                return f"http://127.0.0.1:{local}"
        except Exception:  # noqa: BLE001
            pass
    sys.exit("phone backend unreachable")


def poll(fn, label):
    deadline = time.time() + WAIT
    while time.time() < deadline:
        if fn():
            print(f"  {label}: yes after {int(WAIT - (deadline - time.time()))}s")
            return True
        time.sleep(5)
    print(f"  {label}: NOT within {WAIT}s")
    return False


PHONE = phone_base()
phone = Client(PHONE)
print("owner login on phone:", phone.call("POST", "/api/auth/login", OWNER)[0])
desk = Client(DESK)
print("owner login on desk:", desk.call("POST", "/api/auth/login", OWNER)[0])

# ── A. staff from the phone ────────────────────────────────────────────────
status, body = phone.call("POST", "/api/admin/employees", {"email": NEW_EMAIL, "role": "cashier"})
print("A. create cashier ON PHONE:", status, json.dumps(body)[:120])
token = body.get("setup_token") or body.get("token")
link = body.get("setup_link", "")
if not token and "token=" in link:
    token = link.split("token=")[-1]
if token:
    status, body = phone.call("POST", "/api/auth/employee/setup", {"token": token, "password": NEW_PASS})
    print("A. set password ON PHONE:", status, json.dumps(body)[:100])
else:
    print("A. no setup token in the response; skipping password step", json.dumps(body)[:200])


def desk_has_it():
    s, b = desk.call("GET", "/api/admin/employees")
    return s == 200 and any(e.get("email") == NEW_EMAIL and e.get("status") == "active" for e in b.get("employees", []))


poll(desk_has_it, "A. cashier (active) visible on the LAPTOP")
c = Client(DESK)
s, b = c.call("POST", "/api/auth/login", {"email": NEW_EMAIL, "password": NEW_PASS})
print("A. phone-made cashier logs in on the LAPTOP:", s, (b.get("user") or {}).get("employee_id"))

# ── B. offline sale ────────────────────────────────────────────────────────
s, b = phone.call("GET", "/api/sub/retail/products?q=SYNC-0905C")
prod = next((p for p in b.get("data", []) if p.get("sku") == "SYNC-0905C"), None) if s == 200 else None
if not prod:
    sys.exit("B. product SYNC-0905C not on the phone; cannot ring")
pid = prod["id"]
print("B. cutting the phone's link to the relay")
adb("reverse", "--remove", "tcp:5551")
print("   reverse list now:", repr(adb("reverse", "--list")))
phone.call("POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 5})
s, b = phone.call("POST", "/api/sub/retail/sales", {
    "items": [{"product_id": pid, "quantity": 1}], "payment_method": "cash", "amount_paid": 20,
    "idempotency_key": f"offline-sale-{TAG}",
})
sale_no = (b.get("data") or {}).get("sale_number")
print("B. sale rung OFFLINE on the phone:", s, sale_no)
s, b = phone.call("GET", "/api/sub/retail/products?q=SYNC-0905C")
print("B. phone stock after sale:", next((p.get("total_stock") for p in b.get("data", []) if p.get("sku") == "SYNC-0905C"), None))
time.sleep(20)
s, b = desk.call("GET", "/api/sub/retail/sales/recent")
print("B. laptop sees it while phone is offline (should be False):", any(x.get("sale_number") == sale_no for x in b.get("data", [])))
print("B. restoring the link")
adb("reverse", "tcp:5551", "tcp:5551")
print("   reverse list now:", repr(adb("reverse", "--list")))


def desk_has_sale():
    s2, b2 = desk.call("GET", "/api/sub/retail/sales/recent")
    return s2 == 200 and any(x.get("sale_number") == sale_no for x in b2.get("data", []))


ok = poll(desk_has_sale, "B. laptop sees the offline sale after reconnect")
s, b = desk.call("GET", "/api/sub/retail/products?q=SYNC-0905C")
print("B. laptop stock now:", next((p.get("total_stock") for p in b.get("data", []) if p.get("sku") == "SYNC-0905C"), None))
