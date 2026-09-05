"""After the watcher installed tonight's APK: prove the two new behaviours ON
THE PHONE, through its embedded backend over adb forward.

  1. the desktop's owner (desk-owner) can log in on the phone -- the
     re-numbering fix (the phone already had its own ADMIN-0001)
  2. shop settings follow the desktop: currency USD on the desktop -> phone
     reads USD; back to JOD -> phone follows
Every value is read back from the device, never assumed.
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
DESK_ADMIN = {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"}
CASHIER = {"email": "synced-cashier-1788568313@rehearsal.local", "password": "Cashier2026!Pass"}
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


def phone_base():
    for local, remote in ((18080, 5000), (18081, 5001)):
        subprocess.run([ADB, "forward", f"tcp:{local}", f"tcp:{remote}"], capture_output=True, check=False)
        try:
            status, _ = Client(f"http://127.0.0.1:{local}").call("GET", "/api/health")
            if status == 200:
                return f"http://127.0.0.1:{local}"
        except Exception:  # noqa: BLE001
            pass
    sys.exit("phone backend unreachable on 5000/5001 -- is the app running?")


def currency(c):
    status, body = c.call("GET", "/api/sub/retail/settings/tax")
    d = body.get("data", body) if isinstance(body, dict) else {}
    return status, d.get("base_currency"), d.get("currency_symbol")


def wait_currency(c, want, label):
    deadline = time.time() + WAIT
    while time.time() < deadline:
        status, cur, sym = currency(c)
        if cur == want:
            print(f"  {label} reads {cur} ({sym}) after {int(WAIT - (deadline - time.time()))}s")
            return True
        time.sleep(5)
    print(f"  {label} STILL {cur} after {WAIT}s (wanted {want})")
    return False


PHONE = phone_base()
print("phone backend:", PHONE)

# 1. owner login on the phone
deadline = time.time() + WAIT
owner_ok = False
while time.time() < deadline:
    c = Client(PHONE)
    status, body = c.call("POST", "/api/auth/login", DESK_ADMIN)
    if status == 200:
        u = body.get("user", {})
        print(f"  desk-owner on PHONE: 200 -> {u.get('employee_id')} {u.get('role')} after {int(WAIT - (deadline - time.time()))}s")
        owner_ok = True
        break
    time.sleep(5)
if not owner_ok:
    print("  desk-owner on PHONE: NOT accepted within the wait")

# 2. settings follow the desktop
phone = Client(PHONE)
print("phone cashier login:", phone.call("POST", "/api/auth/login", CASHIER)[0])
desk = Client(DESK)
print("desk admin login:", desk.call("POST", "/api/auth/login", DESK_ADMIN)[0])
print("phone currency now:", currency(phone))
print("desk -> USD:", desk.call("POST", "/api/sub/retail/settings/credit", {"base_currency": "USD"})[0])
ok1 = wait_currency(phone, "USD", "phone")
print("desk -> JOD:", desk.call("POST", "/api/sub/retail/settings/credit", {"base_currency": "JOD"})[0])
ok2 = wait_currency(phone, "JOD", "phone")
print("RESULT:", "OWNER LOGIN + SETTINGS SYNC ON THE PHONE" if (owner_ok and ok1 and ok2)
      else f"owner_login={owner_ok} usd={ok1} jod={ok2}")
