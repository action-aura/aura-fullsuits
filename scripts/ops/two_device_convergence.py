"""Two real devices converging on one shop -- stock and sales.

Desktop till (:5010) is the owner's machine; the phone's embedded backend is
reached through `adb forward` (18080 -> 5000, fallback 18081 -> 5001). Every
call is the real HTTP API of the real artefact. Nothing is assumed: each step
prints the response it actually got and the next step reads state back.

  1. desktop admin creates a product with stock 10
  2. phone cashier sees that product arrive (catalogue down)
  3. phone cashier rings a sale of 2 of it on the PHONE
  4. desktop sees the sale and the product's stock at 8 (sale + stock up)
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
ADMIN = {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"}
CASHIER = {"email": "synced-cashier-1788568313@rehearsal.local", "password": "Cashier2026!Pass"}
TAG = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%H%M%S")
SKU = f"SYNC-{TAG}"
NAME = f"Sync Test Product {TAG}"


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
                return exc.code, {"raw": raw[:200].decode(errors="replace")}


def rows(body):
    return body if isinstance(body, list) else body.get("data", body.get("products", body.get("sales", [])))


def find_product(client, sku):
    status, body = client.call("GET", "/api/sub/retail/products")
    if status != 200:
        return status, None
    for p in rows(body):
        if str(p.get("sku", "")).upper() == sku.upper():
            return status, p
    return status, None


def phone_client():
    for local, remote in ((18080, 5000), (18081, 5001)):
        subprocess.run([ADB, "forward", f"tcp:{local}", f"tcp:{remote}"], capture_output=True, check=False)
        c = Client(f"http://127.0.0.1:{local}")
        try:
            status, _ = c.call("GET", "/api/health")
            if status == 200:
                print(f"phone backend answering via {local}->{remote}")
                return c
        except Exception:  # noqa: BLE001
            pass
    sys.exit("phone backend unreachable on 5000/5001")


desk = Client(DESK)
print("desk admin login:", desk.call("POST", "/api/auth/login", ADMIN)[0])

# 1. product on the desktop
status, body = desk.call("POST", "/api/sub/retail/products", {
    "name": NAME, "sku": SKU, "sell_price": 12.345, "price": 12.345, "cost_price": 8.0,
    "stock": 10, "stock_quantity": 10, "quantity": 10, "category": "Sync test", "unit": "pc",
})
print("desk create product:", status, json.dumps(body)[:200])
status, prod = find_product(desk, SKU)
if not prod:
    sys.exit("product not visible on desktop after create")
pid = prod["id"]
# Stock is branch-scoped and starts at 0; put 10 on the default branch through
# the real adjustment route (CAP_STOCK_ADJUST -- the admin holds it).
status, body = desk.call("POST", f"/api/sub/retail/products/{pid}/stock-adjust",
                         {"quantity": 10, "reason": "two-device convergence test"})
print("desk stock-adjust +10:", status, json.dumps(body)[:160])
status, prod = find_product(desk, SKU)
print("desk product row:", status, {k: prod.get(k) for k in ("id", "sku", "sell_price", "total_stock") if prod and k in prod})

# 2. phone sees it
phone = phone_client()
print("phone cashier login:", phone.call("POST", "/api/auth/login", CASHIER)[0])
deadline = time.time() + 90
seen = None
while time.time() < deadline:
    status, seen = find_product(phone, SKU)
    if seen:
        break
    time.sleep(5)
print("phone sees product:", bool(seen), {k: seen.get(k) for k in ("id", "sku", "sell_price", "total_stock") if seen and k in seen})
if not seen:
    sys.exit("product never reached the phone")
print("same uuid both sides:", seen["id"] == pid)

# 3. sale on the phone
status, body = phone.call("POST", "/api/sub/retail/cash-sessions/open", {"opening_float": 5})
print("phone open drawer:", status, json.dumps(body)[:160])
status, body = phone.call("POST", "/api/sub/retail/sales", {
    "items": [{"product_id": pid, "quantity": 2}],
    "payment_method": "cash", "amount_paid": 30,
    "idempotency_key": f"two-device-sale-{TAG}",
})
print("phone sale:", status, json.dumps(body)[:300])
sale = (body.get("data") or {}) if isinstance(body, dict) else {}
sale_number = sale.get("sale_number")
status, seen = find_product(phone, SKU)
print("phone stock after sale:", {k: seen.get(k) for k in ("total_stock",) if seen and k in seen})

# 4. desktop sees the sale and the stock
deadline = time.time() + 90
found_sale = None
while time.time() < deadline:
    status, body = desk.call("GET", "/api/sub/retail/sales/recent")
    for s in rows(body) if status == 200 else []:
        if sale_number and s.get("sale_number") == sale_number:
            found_sale = s
            break
    if found_sale:
        break
    time.sleep(5)
print("desk sees sale:", bool(found_sale), {k: found_sale.get(k) for k in ("sale_number", "total", "subtotal", "tax", "payment_method", "cashier_name", "employee_id") if found_sale and k in found_sale})
status, prod = find_product(desk, SKU)
print("desk stock after sale:", {k: prod.get(k) for k in ("total_stock",) if prod and k in prod})
