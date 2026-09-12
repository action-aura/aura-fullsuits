"""One owner login on every device -- prove it on two real tills.

Till A (:5010) was set up by desk-owner; till B (:5011) by third-owner. Both
sit on one licence. Each device had parked the OTHER's admin row as
duplicate_employee_id (every install mints ADMIN-0001). After the
renumbering fix the quarantine retry on the next pull should apply them, so:
desk-owner must be able to log in on B, third-owner on A, and each device's
staff list must show the other owner under the next free code.
"""
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

A = "http://127.0.0.1:5010"
B = "http://127.0.0.1:5011"
DESK = {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"}
THIRD = {"email": "third-owner@rehearsal.local", "password": "ThirdOwner2026!Pass"}
DBS = {
    "A": r"C:\Users\MSI\.claude\jobs\215b2785\tmp\desktop-till\database\registry.db",
    "B": r"C:\Users\MSI\.claude\jobs\215b2785\tmp\third-till\database\registry.db",
}
WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 120


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


def wait_up(base):
    for _ in range(90):
        try:
            Client(base).call("GET", "/api/health")
            return
        except Exception:  # noqa: BLE001
            time.sleep(1)
    sys.exit(f"{base} never came up")


def login_until(base, creds, label):
    deadline = time.time() + WAIT
    last = None
    while time.time() < deadline:
        c = Client(base)
        try:
            last, body = c.call("POST", "/api/auth/login", creds)
        except Exception as exc:  # noqa: BLE001
            last, body = None, str(exc)
        if last == 200:
            print(f"  {label}: 200 after {int(WAIT - (deadline - time.time()))}s -> {body.get('user', {}).get('employee_id')} {body.get('user', {}).get('role')}")
            return c
        time.sleep(5)
    print(f"  {label}: STILL {last} after {WAIT}s")
    return None


def staff(c, label):
    status, body = c.call("GET", "/api/admin/employees")
    rows = body.get("employees", []) if status == 200 else []
    print(f"  {label} staff list:", [(r["email"], r["employee_id"], r["role"]) for r in rows])


def quarantine(label):
    conn = sqlite3.connect(DBS[label])
    try:
        rows = conn.execute("SELECT entity_type, reason FROM sync_apply_quarantine").fetchall()
    except sqlite3.OperationalError:
        rows = "(no table)"
    finally:
        conn.close()
    print(f"  {label} quarantine:", rows)


wait_up(A)
wait_up(B)
print("before:")
quarantine("A")
quarantine("B")
print("cross logins:")
b_as_desk = login_until(B, DESK, "desk-owner on B")
a_as_third = login_until(A, THIRD, "third-owner on A")
print("after:")
quarantine("A")
quarantine("B")
if a_as_third:
    staff(a_as_third, "A (as third-owner)")
if b_as_desk:
    staff(b_as_desk, "B (as desk-owner)")
print("RESULT:", "ONE OWNER LOGIN EVERYWHERE" if (a_as_third and b_as_desk) else "NOT YET")
