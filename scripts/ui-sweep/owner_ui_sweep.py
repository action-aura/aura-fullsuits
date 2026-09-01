"""Photograph the Owner Control Center, past the mandatory MFA gate.

Owner requires TOTP enrolment on first login and offers no skip -- a good
posture, and a hard gate for any sweep. The enrolment page prints the manual
secret, so the code is computed here (RFC 6238, stdlib only) against this
session's own throwaway account and throwaway database.
"""
import base64
import hashlib
import hmac
import io
import os
import re
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                              line_buffering=True)

VENV_PY = r"C:\Users\MSI\Desktop\aura-fullsuits\.venv-owner\Scripts\python.exe"
SERVE = r"C:\Users\MSI\.claude\jobs\b602c1c7\tmp\owner_serve.py"
PORT = int(os.environ.get("OWNER_UI_PORT", "5670"))
BASE = "http://127.0.0.1:%d" % PORT
LOGIN = BASE + "/auth/login"
OUT = Path(os.environ.get("OWNER_SHOT_DIR",
                          r"C:\Users\MSI\.claude\jobs\b602c1c7\tmp\owner-shots4"))
OUT.mkdir(parents=True, exist_ok=True)

EMAIL, PASSWORD = "uishots@aura.local", "UiShots2026!Pass"


def totp(secret, when=None):
    """RFC 6238, 30s step, 6 digits, SHA1 -- what every authenticator does."""
    key = base64.b32decode(re.sub(r"\s+", "", secret).upper() + "=" * (-len(secret) % 8))
    counter = int((when or time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return "%06d" % (code % 1000000)


env = dict(os.environ, OWNER_UI_PORT=str(PORT))
proc = subprocess.Popen([VENV_PY, SERVE], env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(90):
    if proc.poll() is not None:
        print("server died")
        raise SystemExit(1)
    try:
        urllib.request.urlopen(LOGIN, timeout=2)
        break
    except Exception:
        time.sleep(1)
print("owner up on", BASE)

from playwright.sync_api import sync_playwright  # noqa: E402

problems, shot_fails = [], []
SECRET_HOLDER = {}


def shot(page, name):
    try:
        page.screenshot(path=str(OUT / (name + ".png")),
                        animations="disabled", timeout=10000)
    except Exception as exc:
        shot_fails.append("%s: %s" % (name, str(exc).splitlines()[0][:100]))


def authenticate(page, label):
    page.goto(LOGIN, wait_until="domcontentloaded")
    page.wait_for_selector("input[name=email]")
    shot(page, "%s-01-login" % label)
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
        page.click("button[type=submit]")
    page.wait_for_timeout(800)

    if "mfa-enroll" in page.url:
        shot(page, "%s-02-mfa-enroll" % label)
        body = page.evaluate("() => document.body.innerText")
        m = re.search(r"Manual secret\s*\|?\s*([A-Z2-7 ]{16,})", body)
        if not m:
            m = re.search(r"\b([A-Z2-7]{26,})\b", body)
        if not m:
            problems.append("%s: could not read the MFA secret" % label)
            return False
        secret = re.sub(r"\s+", "", m.group(1))
        SECRET_HOLDER["secret"] = secret
        field = page.locator("input[name=code], input[name=token], input[type=text]").last
        field.fill(totp(secret))
        with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
            page.click("button[type=submit]")
        page.wait_for_timeout(800)

    if "mfa" in page.url and "verify" in page.url or "mfa-challenge" in page.url:
        shot(page, "%s-03-mfa-challenge" % label)
        secret = SECRET_HOLDER.get("secret")
        if secret:
            page.locator("input[type=text]").last.fill(totp(secret))
            with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
                page.click("button[type=submit]")
            page.wait_for_timeout(800)
    return True


try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        for width, height, label in ((1440, 900, "desktop"), (390, 844, "phone")):
            ctx = b.new_context(viewport={"width": width, "height": height})
            page = ctx.new_page()
            page.set_default_timeout(20000)
            page.on("pageerror",
                    lambda e, L=label: problems.append("%s PAGEERROR %s" % (L, e)))

            authenticate(page, label)
            shot(page, "%s-04-landing" % label)
            if label == "desktop":
                print("landed on:", page.url)
                print("heading  :", page.evaluate(
                    "() => (document.querySelector('h1,h2,h3')||{}).innerText || '(none)'"))

            links = page.evaluate("""() => {
              const seen = new Set(), out = [];
              document.querySelectorAll('a[href]').forEach(a => {
                const h = a.getAttribute('href') || '';
                if (!h.startsWith('/') || h.startsWith('//')) return;
                if (/logout|signout|locale\\/|\\.(css|js|png|svg)$/i.test(h)) return;
                if (seen.has(h)) return;
                seen.add(h); out.push(h);
              });
              return out.slice(0, 24);
            }""")
            if label == "desktop":
                print("discovered %d links:" % len(links))
                for h in links:
                    print("   ", h)

            for i, href in enumerate(links, start=5):
                slug = href.strip("/").replace("/", "-").replace("?", "_")[:38] or "root"
                try:
                    page.goto(BASE + href, wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(700)
                    shot(page, "%s-%02d-%s" % (label, i, slug))
                except Exception as exc:
                    problems.append("%s %s: %s" % (label, href,
                                                   str(exc).splitlines()[0][:110]))
            ctx.close()
        b.close()
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

print("\nscreenshots: %d in %s" % (len(list(OUT.glob('*.png'))), OUT))
if shot_fails:
    print("frames that would not photograph (%d):" % len(shot_fails))
    for f in shot_fails[:10]:
        print("  -", f)
if problems:
    print("problems (%d):" % len(problems))
    seen = set()
    for x in problems:
        if x[:90] in seen:
            continue
        seen.add(x[:90])
        print("  -", x[:170])
else:
    print("no page errors")
