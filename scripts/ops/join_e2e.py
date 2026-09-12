"""Join an existing shop, backend half, on a genuinely fresh till (:5012):

  1. fresh data dir, no admin ever created: onboarding says needs_setup
  2. POST /api/licensing/activate with the shop's key -- no session
  3. the hook seeds company_settings with the licence id (read back from
     the till's own registry.db)
  4. (the caller restarts the till so the discovered relay URL is used)
  5. needs_setup flips to false by itself as the owner's row arrives
  6. the desktop's owner signs in on the fresh till: 200
Run with `before-restart` first, then restart the till, then `after-restart`.
"""
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

TILL = "http://127.0.0.1:5012"
KEY = "AURA-RET-1-5P2G-39EP-XQ9T-K8ZC-FEZG"
OWNER = {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"}
REG = r"C:\Users\MSI\.claude\jobs\215b2785\tmp\fourth-till\database\registry.db"
PHASE = sys.argv[1] if len(sys.argv) > 1 else "before-restart"
WAIT = 150


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
                return exc.code, {"raw": raw[:160].decode(errors="replace")}


def registry(sql):
    conn = sqlite3.connect(REG)
    try:
        return conn.execute(sql).fetchall()
    except sqlite3.OperationalError as exc:
        return f"(err {exc})"
    finally:
        conn.close()


c = Client(TILL)
for _ in range(60):
    try:
        status, body = c.call("GET", "/api/onboarding/status")
        break
    except Exception:  # noqa: BLE001
        time.sleep(1)
else:
    sys.exit("till 5012 never answered")
print("phase:", PHASE)
print("onboarding/status:", status, body)
print("registry users:", registry("SELECT email, role, employee_id, company_id FROM users"))
print("registry company_settings:", registry("SELECT company_id FROM company_settings"))

if PHASE == "before-restart":
    status, body = c.call("POST", "/api/licensing/activate", {"license_key": KEY})
    print("activate (no session):", status, json.dumps(body)[:200])
    print("registry company_settings after activation:", registry("SELECT company_id FROM company_settings"))
    print("licensing/status:", c.call("GET", "/api/licensing/status")[1].get("current_state"))
    print("NEXT: restart the till so the discovered relay URL takes effect, then run after-restart")
    sys.exit(0)

deadline = time.time() + WAIT
while time.time() < deadline:
    status, body = c.call("GET", "/api/onboarding/status")
    if status == 200 and body.get("needs_setup") is False:
        print(f"needs_setup flipped to False after {int(WAIT - (deadline - time.time()))}s")
        break
    time.sleep(5)
else:
    print("needs_setup still", body)
print("registry users now:", registry("SELECT email, role, employee_id, company_id FROM users"))
status, body = c.call("POST", "/api/auth/login", OWNER)
print("desk-owner signs in on the FRESH till:", status, (body.get("user") or {}).get("employee_id"), (body.get("user") or {}).get("role"))
print("RESULT:", "JOINED WITHOUT CREATING AN ADMIN" if status == 200 else "NOT YET")
