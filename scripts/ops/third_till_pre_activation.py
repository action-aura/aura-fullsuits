"""Measure item 13 instead of assuming it: does a CASHIER created BEFORE a
till activates reach another device AFTER it activates?

Drives a fresh third till (:5011, its own AURA_APP_DATA) through the real
API: first-run admin -> create cashier (unactivated!) -> activate on the
shared rehearsal licence -> re-login -> then polls the DESKTOP till (:5010)
for that cashier's email in GET /api/admin/employees. Prints every response.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

THIRD = "http://127.0.0.1:5011"
DESK = "http://127.0.0.1:5010"
KEY = "AURA-RET-1-5P2G-39EP-XQ9T-K8ZC-FEZG"
ADMIN3 = {"name": "Third Owner", "email": "third-owner@rehearsal.local", "password": "ThirdOwner2026!Pass",
          "company_name": "Rehearsal phone shop"}
DESK_ADMIN = {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"}
TAG = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%H%M%S")
PRE_EMAIL = f"pre-activation-cashier-{TAG}@rehearsal.local"


class Client:
    def __init__(self, base):
        self.base = base
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        try:
            with self.opener.open(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except Exception:  # noqa: BLE001
                return exc.code, {"raw": raw[:200].decode(errors="replace")}


third = Client(THIRD)
for _ in range(60):
    try:
        status, body = third.call("GET", "/api/onboarding/status")
        break
    except Exception:  # noqa: BLE001
        time.sleep(1)
else:
    sys.exit("third till never answered on :5011")
print("third onboarding/status:", status, body)
if body.get("needs_setup"):
    print("third create-admin:", *third.call("POST", "/api/onboarding/create-admin", ADMIN3))
print("third login:", third.call("POST", "/api/auth/login", {"email": ADMIN3["email"], "password": ADMIN3["password"]})[0])
print("third licensing before:", third.call("GET", "/api/licensing/status")[1].get("state"))

# THE point: staff created while UNACTIVATED
status, body = third.call("POST", "/api/admin/employees", {"email": PRE_EMAIL, "role": "cashier"})
print("third create cashier BEFORE activation:", status, json.dumps(body)[:160])

status, body = third.call("POST", "/api/licensing/activate", {"license_key": KEY})
print("third activate:", status, json.dumps(body)[:200])
print("third licensing after:", third.call("GET", "/api/licensing/status")[1].get("state"))
print("third re-login (activation revokes sessions):", third.call("POST", "/api/auth/login", {"email": ADMIN3["email"], "password": ADMIN3["password"]})[0])

# Did it leave the device?
desk = Client(DESK)
print("desk login:", desk.call("POST", "/api/auth/login", DESK_ADMIN)[0])
deadline = time.time() + 120
found = None
while time.time() < deadline:
    status, body = desk.call("GET", "/api/admin/employees")
    for e in (body.get("employees") or []) if status == 200 else []:
        if e.get("email") == PRE_EMAIL:
            found = e
            break
    if found:
        break
    time.sleep(5)
print("DESK sees pre-activation cashier:", bool(found), found and {k: found.get(k) for k in ("email", "role", "status", "employee_id")})
print("PRE_EMAIL:", PRE_EMAIL)
