"""Rename a branch on till A (:5010) through the new PUT route and watch the
name converge on till B (:5011) through the relay. Also fixes the test
branch's name left by the branch-limit proof ('Branch test after-addon ...'
-> 'Second Branch'). Every value read back, never assumed."""
import json
import sys
import time
import urllib.error
import urllib.request
from http.cookiejar import CookieJar

A = ("http://127.0.0.1:5010", {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"})
B = ("http://127.0.0.1:5011", {"email": "third-owner@rehearsal.local", "password": "ThirdOwner2026!Pass"})
NEW_NAME = sys.argv[1] if len(sys.argv) > 1 else "Second Branch"
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


def branches(c):
    s, b = c.call("GET", "/api/sub/retail/branches")
    return [(x.get("id"), x.get("name")) for x in b.get("data", [])] if s == 200 else s


a, b = login(*A), login(*B)
print("A branches:", branches(a))
print("B branches:", branches(b))
target = next((bid for bid, name in branches(a) if str(name).startswith("Branch test")), None)
if target is None:
    sys.exit("no 'Branch test…' branch on A to rename")
s, body = a.call("PUT", f"/api/sub/retail/branches/{target}", {"name": NEW_NAME, "address": "", "phone": ""})
print(f"A rename branch {target} -> {NEW_NAME!r}:", s, json.dumps(body)[:120])
print("A branches now:", branches(a))
deadline = time.time() + WAIT
while time.time() < deadline:
    if any(name == NEW_NAME for _, name in branches(b)):
        print(f"B shows {NEW_NAME!r} after {int(WAIT - (deadline - time.time()))}s:", branches(b))
        print("RESULT: RENAME CONVERGED")
        break
    time.sleep(5)
else:
    print("B still:", branches(b))
    print("RESULT: DID NOT CONVERGE")
