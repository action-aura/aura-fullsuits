"""Prove shop settings travel between two real installs on one licence.

Desktop till (:5010) and the third till (:5011) are both activated on the
rehearsal licence and both pull the Owner relay. Change the currency on A,
watch B report it; change it back on A, watch B follow. Every step is the
real HTTP API and every value is read back, never assumed.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

A = ("http://127.0.0.1:5010", {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"})
B = ("http://127.0.0.1:5011", {"email": "third-owner@rehearsal.local", "password": "ThirdOwner2026!Pass"})
WAIT = int(sys.argv[1]) if len(sys.argv) > 1 else 90


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


def login(base, creds):
    c = Client(base)
    for _ in range(60):
        try:
            status, _ = c.call("POST", "/api/auth/login", creds)
            if status == 200:
                return c
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    sys.exit(f"login failed on {base}")


def currency(c):
    status, body = c.call("GET", "/api/sub/retail/settings/tax")
    d = body.get("data", body) if isinstance(body, dict) else {}
    return status, d.get("base_currency"), d.get("currency_symbol")


def wait_for(c, want, label):
    deadline = time.time() + WAIT
    while time.time() < deadline:
        status, cur, sym = currency(c)
        if cur == want:
            print(f"  {label} now {cur} ({sym}) after {int(WAIT - (deadline - time.time()))}s")
            return True
        time.sleep(5)
    print(f"  {label} STILL {cur} after {WAIT}s (wanted {want})")
    return False


a = login(*A)
b = login(*B)
print("A currency:", currency(a))
print("B currency:", currency(b))

print("A -> USD:", a.call("POST", "/api/sub/retail/settings/credit", {"base_currency": "USD"})[0])
ok1 = wait_for(b, "USD", "B")
print("A -> JOD:", a.call("POST", "/api/sub/retail/settings/credit", {"base_currency": "JOD"})[0])
ok2 = wait_for(b, "JOD", "B")
print("A currency:", currency(a))
print("B currency:", currency(b))
print("RESULT:", "SETTINGS CONVERGE" if (ok1 and ok2) else "DID NOT CONVERGE")
