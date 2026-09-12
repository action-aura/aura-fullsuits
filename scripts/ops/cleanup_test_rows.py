"""Remove the rows the night's proofs left on the laptop till, through the
real delete routes, and watch the deletions converge on the second till.

Deleted: products SYNC-0905A/B/C ('Sync Test Product …'), customers
'Phone Customer 0905' and 'Desk Cashier Customer 0905'. Kept: the sales
(history), 'Second Branch', the staff accounts, the customer 'tareq'.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

A = ("http://127.0.0.1:5010", {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"})
B = ("http://127.0.0.1:5011", {"email": "third-owner@rehearsal.local", "password": "ThirdOwner2026!Pass"})
WAIT = 90


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
                return exc.code, {"raw": raw[:160].decode(errors="replace")}


def login(base, creds):
    c = Client(base)
    for _ in range(60):
        try:
            if c.call("POST", "/api/auth/login", creds)[0] == 200:
                return c
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    sys.exit(f"login failed on {base}")


def products(c):
    s, b = c.call("GET", "/api/sub/retail/products?q=SYNC")
    return [(p["id"], p["sku"]) for p in b.get("data", [])] if s == 200 else s


def customers(c):
    s, b = c.call("GET", "/api/sub/retail/customers")
    return [(x["id"], x["name"]) for x in b.get("data", [])] if s == 200 else s


a, b = login(*A), login(*B)
print("A products:", products(a), "customers:", [n for _, n in customers(a)])
print("B products:", products(b), "customers:", [n for _, n in customers(b)])

for pid, sku in products(a):
    s, body = a.call("DELETE", f"/api/sub/retail/products/{pid}")
    print(f"A delete product {sku}:", s, json.dumps(body)[:100])
for cid, name in customers(a):
    if name.endswith("0905"):
        s, body = a.call("DELETE", f"/api/sub/retail/customers/{cid}")
        print(f"A delete customer {name!r}:", s, json.dumps(body)[:100])

print("A now: products", products(a), "customers", [n for _, n in customers(a)])
deadline = time.time() + WAIT
while time.time() < deadline:
    bp, bc = products(b), [n for _, n in customers(b)]
    if not bp and not any(n.endswith("0905") for n in bc):
        print(f"B converged after {int(WAIT - (deadline - time.time()))}s: products {bp} customers {bc}")
        print("RESULT: DELETIONS CONVERGED")
        break
    time.sleep(5)
else:
    print("B still:", products(b), [n for _, n in customers(b)])
    print("RESULT: DID NOT CONVERGE")
