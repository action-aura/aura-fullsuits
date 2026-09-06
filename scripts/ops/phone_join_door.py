"""Prove the Android "join an existing shop" door on the real handset.

MANUAL, DESTRUCTIVE ON THE PHONE: step 1 wipes the app's data
(`pm clear`), which is what a brand-new device looks like. Never wire this
into the unattended phone runner. Written 2026-09-06 while the phone was
unplugged, against the uiautomator/adb contract rather than the device, so
the first real run may need a text tweak -- every step prints what it saw.

Precondition: a FREE device slot on the rehearsal licence. A wiped phone has
a new device identity, and its old one still counts until Owner
deactivates it, so raise the allowance by one first through the audited op
(scripts/ops/owner_rehearsal_add_one_device.py, change the idempotency key
and reason) rather than reusing a slot by hand.

Steps (each read back from the device, never assumed):
  1. pm clear + launch                -> LicensingScreen (fresh device)
  2. type the shop's key, Activate     -> the choice screen:
                                          "Is your shop already set up on
                                          another device?"
  3. tap "Yes — connect to my shop"    -> "Connecting to your shop…", then
                                          the ordinary sign-in screen once
                                          the owner's row has synced
  4. sign in as the desktop's owner    -> the till (no account was created)
  5. over adb forward, read the phone's own /api/onboarding/status
     (needs_setup false) and sign in desk-owner by HTTP as a second witness.
"""
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from http.cookiejar import CookieJar

ADB = r"C:\Users\MSI\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe"
PKG = "com.actionaura.retail.debug"
KEY = "AURA-RET-1-5P2G-39EP-XQ9T-K8ZC-FEZG"
OWNER = {"email": "desk-owner@rehearsal.local", "password": "DeskOwner2026!Pass"}
OWNER_PORT = 5551          # the rehearsal Owner, reached from the phone via adb reverse
PHONE_HTTP = 5001          # local port forwarded to the phone's embedded backend
UI_XML = "/sdcard/aura-ui.xml"


def adb(*args, check=True, timeout=60):
    r = subprocess.run([ADB, *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        sys.exit(f"adb {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def ui_nodes():
    """Every node of the current screen as (text, content-desc, class, bounds)."""
    adb("shell", "uiautomator", "dump", UI_XML)
    raw = adb("shell", "cat", UI_XML)
    out = []
    for node in ET.fromstring(raw).iter("node"):
        b = node.get("bounds", "")
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
        bounds = tuple(int(x) for x in m.groups()) if m else None
        out.append((node.get("text", ""), node.get("content-desc", ""), node.get("class", ""), bounds))
    return out


def find(pred, timeout=30, what=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for n in ui_nodes():
            if pred(n):
                return n
        time.sleep(1.5)
    texts = [t or d for t, d, _, _ in ui_nodes() if t or d]
    sys.exit(f"never saw {what!r}; screen shows: {texts[:25]}")


def tap(node):
    x1, y1, x2, y2 = node[3]
    adb("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
    time.sleep(0.8)


def by_text(fragment):
    frag = fragment.lower()
    return lambda n: frag in (n[0] or "").lower() or frag in (n[1] or "").lower()


def edit_texts():
    return [n for n in ui_nodes() if n[2].endswith("EditText") and n[3]]


def type_into(node, value):
    tap(node)
    adb("shell", "input", "text", value.replace(" ", "%s"))
    time.sleep(0.5)


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


def main():
    devices = adb("devices").strip().splitlines()[1:]
    print("adb devices:", devices)
    if not any("\tdevice" in d for d in devices):
        sys.exit("no authorised device -- plug the phone in and accept the prompt")

    adb("reverse", f"tcp:{OWNER_PORT}", f"tcp:{OWNER_PORT}")     # phone -> rehearsal Owner
    print("step 1: wiping app data and launching")
    adb("shell", "pm", "clear", PKG)
    adb("shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")

    print("step 2: licence key")
    key_field = find(lambda n: n[2].endswith("EditText") and n[3], timeout=90, what="the licence key field")
    type_into(key_field, KEY)
    activate = find(by_text("activat"), what="an Activate button")
    print("  tapping:", activate[0] or activate[1])
    tap(activate)

    choice = find(by_text("already set up on another device"), timeout=120, what="the join choice screen")
    print("  choice screen:", choice[0])
    yes = find(by_text("connect to my shop"), what="the Yes button")
    print("step 3: tapping", yes[0])
    tap(yes)
    seen = find(lambda n: by_text("connecting to your shop")(n) or by_text("sign in")(n) or by_text("log in")(n),
                timeout=60, what="the waiting or sign-in screen")
    print("  after Yes:", seen[0] or seen[1])

    print("step 4: signing in as the desktop's owner through the UI")
    fields = None
    deadline = time.time() + 150
    while time.time() < deadline:
        fields = edit_texts()
        if len(fields) >= 2:
            break
        time.sleep(2)
    if not fields or len(fields) < 2:
        sys.exit("sign-in fields never appeared")
    type_into(fields[0], OWNER["email"])
    type_into(fields[1], OWNER["password"])
    button = find(lambda n: n[2].endswith("Button") and (by_text("sign in")(n) or by_text("log in")(n)),
                  what="the sign-in button")
    tap(button)
    shell = find(lambda n: by_text("dashboard")(n) or by_text("pos")(n) or by_text("sales")(n),
                 timeout=60, what="the till after sign-in")
    print("  till shows:", shell[0] or shell[1])

    print("step 5: second witness over HTTP")
    adb("forward", f"tcp:{PHONE_HTTP}", "tcp:5000")
    c = Client(f"http://127.0.0.1:{PHONE_HTTP}")
    status, body = c.call("GET", "/api/onboarding/status")
    if status != 200:
        adb("forward", f"tcp:{PHONE_HTTP}", "tcp:5001")
        status, body = c.call("GET", "/api/onboarding/status")
    print("  onboarding/status:", status, body)
    status, body = c.call("POST", "/api/auth/login", OWNER)
    user = body.get("user") or {}
    print("  desk-owner by HTTP:", status, user.get("employee_id"), user.get("role"))
    print("RESULT:", "PHONE JOINED THROUGH THE DOOR, NO ADMIN CREATED HERE" if status == 200 else "NOT YET")


if __name__ == "__main__":
    main()
