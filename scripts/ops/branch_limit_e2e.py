"""The branch add-on, end to end, on the real desktop till (:5010).

  1. force a licence check-in so the till holds the assertion Owner signs
     NOW (plan says max_branches = 1)
  2. read the entitlement back from the till's own licensing.db
  3. try to create a second branch -> must be refused with BRANCH_LIMIT
Run again with `after-addon` once EXTRA_BRANCH is attached to the
subscription and AVAILABLE: check-in -> max_branches = 2 -> the create
succeeds.
"""
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

DESK = "http://127.0.0.1:5010"
OWNER = {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"}
LIC_DB = r"C:\Users\MSI\.claude\jobs\215b2785\tmp\desktop-till\database\subsystems\licensing.db"
PHASE = sys.argv[1] if len(sys.argv) > 1 else "before-addon"


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


def till_entitlements():
    conn = sqlite3.connect(LIC_DB)
    try:
        row = conn.execute("SELECT entitlements_json, current_state FROM licensing_state LIMIT 1").fetchone()
    finally:
        conn.close()
    return (json.loads(row[0]) if row and row[0] else {}), (row[1] if row else None)


c = Client(DESK)
for _ in range(60):
    try:
        if c.call("POST", "/api/auth/login", OWNER)[0] == 200:
            break
    except Exception:  # noqa: BLE001
        time.sleep(1)
print("phase:", PHASE)
print("entitlements on till before check-in:", till_entitlements())
status, body = c.call("POST", "/api/licensing/check-in", {})
print("check-in:", status, json.dumps(body)[:160])
ent, state = till_entitlements()
print("entitlements on till after check-in:", ent, "state", state)
status, body = c.call("GET", "/api/sub/retail/branches")
branches = body.get("data", []) if status == 200 else []
print("branches now:", status, [(b.get("id"), b.get("name")) for b in branches])
create_status, body = c.call("POST", "/api/sub/retail/branches", {"name": f"Branch test {PHASE} {int(time.time())}", "address": "", "phone": ""})
print("create branch:", create_status, json.dumps(body)[:220])
status, body = c.call("GET", "/api/sub/retail/branches")
print("branches after:", len(body.get("data", [])) if status == 200 else status)
expected = 403 if PHASE == "before-addon" else 200
print("RESULT:", "AS EXPECTED" if create_status == expected else "UNEXPECTED",
      f"(create answered {create_status}, expected {expected})")
