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
if not token and "#setup/" in link:
    # The real shape (onboarding_routes.py: `setup_link` is
    # ".../#setup/<token>", no query string). Missed by the first cut, which
    # then skipped the password step and reported the cashier's laptop login
    # as a failure -- a 401 for an account with no password yet is correct.
    token = link.rsplit("/", 1)[-1]
if token:
    status, body = phone.call("POST", "/api/auth/employee/setup", {"token": token, "password": NEW_PASS})
    print("A. set password ON PHONE:", status, json.dumps(body)[:100])
else:
    print("A. no setup token in the response; skipping password step", json.dumps(body)[:200])


def desk_has_it():
    # Arrival is what this measures; the status is printed, not demanded --
    # a row lands as pending_setup until the setup link sets a password.
    s, b = desk.call("GET", "/api/admin/employees")
    row = next((e for e in b.get("employees", []) if e.get("email") == NEW_EMAIL), None) if s == 200 else None
    if row:
        print("     laptop row status:", row.get("status"))
    return row is not None


poll(desk_has_it, "A. cashier visible on the LAPTOP")
c = Client(DESK)
s, b = c.call("POST", "/api/auth/login", {"email": NEW_EMAIL, "password": NEW_PASS})
print("A. phone-made cashier logs in on the LAPTOP:", s, (b.get("user") or {}).get("employee_id"))

# ── B. offline sale ────────────────────────────────────────────────────────
# A FRESH product made on the laptop for this run, not a fixture from an
# earlier night: the first cut leaned on SYNC-0905C, which the 2026-09-06
# clean-up had soft-deleted, and reported "not on the phone" as a failure.
B_SKU = f"OFFLINE-{TAG}"
s, b = desk.call("POST", "/api/sub/retail/products", {
    "name": f"Offline sale product {TAG}", "sku": B_SKU, "sell_price": 12.345, "price": 12.345,
    "cost_price": 8.0, "stock": 10, "stock_quantity": 10, "quantity": 10, "category": "Sync test", "unit": "pc",
})
print("B. laptop creates a product for this run:", s, json.dumps(b)[:120])
s, b = desk.call("GET", "/api/sub/retail/products")
desk_prod = next((p for p in b.get("data", []) if str(p.get("sku", "")).upper() == B_SKU), None) if s == 200 else None
if not desk_prod:
    sys.exit("B. the laptop does not list the product it just created")
s, b = desk.call("POST", f"/api/sub/retail/products/{desk_prod['id']}/stock-adjust",
                 {"quantity": 10, "reason": "phone offline-sale proof"})
print("B. laptop stock-adjust +10:", s)


def phone_has_product():
    # The product row and its stock movement are two events; the link is cut
    # right after this, so wait for the STOCK too, or the offline sale is
    # refused for insufficient stock (measured: "stock after sale: 0", 400).
    s2, b2 = phone.call("GET", "/api/sub/retail/products")
    return s2 == 200 and any(str(p.get("sku", "")).upper() == B_SKU and (p.get("total_stock") or 0) >= 10
                             for p in b2.get("data", []))


if not poll(phone_has_product, "B. product AND its stock reached the phone"):
    sys.exit("B. product/stock never reached the phone; cannot ring")
s, b = phone.call("GET", "/api/sub/retail/products")
prod = next(p for p in b.get("data", []) if str(p.get("sku", "")).upper() == B_SKU)
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
s, b = phone.call("GET", "/api/sub/retail/products")
print("B. phone stock after sale:", next((p.get("total_stock") for p in b.get("data", []) if str(p.get("sku", "")).upper() == B_SKU), None))
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
s, b = desk.call("GET", "/api/sub/retail/products")
print("B. laptop stock now:", next((p.get("total_stock") for p in b.get("data", []) if str(p.get("sku", "")).upper() == B_SKU), None))
