"""Find every clipped or overflowing element in Owner, English AND Arabic.

The Show/Hide toggle spilling out of the password box was reported one bug at
a time by a human looking at a screenshot. This finds the whole class:

  * text clipped by its own box (scrollWidth > clientWidth)
  * an element extending past its parent's inline edge
  * the page scrolling horizontally at all

Arabic is swept too, because a fixed width that just fits an English label is
exactly what breaks on a longer translation.
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
PORT = int(os.environ.get("OWNER_UI_PORT", "5690"))
BASE = "http://127.0.0.1:%d" % PORT
LOGIN = BASE + "/auth/login"
EMAIL, PASSWORD = "uishots@aura.local", "UiShots2026!Pass"
OUT = Path(r"C:\Users\MSI\.claude\jobs\b602c1c7\tmp\owner-overflow")
OUT.mkdir(parents=True, exist_ok=True)


def totp(secret, when=None):
    key = base64.b32decode(re.sub(r"\s+", "", secret).upper() + "=" * (-len(secret) % 8))
    d = hmac.new(key, struct.pack(">Q", int((when or time.time()) // 30)), hashlib.sha1).digest()
    o = d[-1] & 0x0F
    return "%06d" % ((struct.unpack(">I", d[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)


DETECT = """() => {
  const bad = [];
  const seen = new Set();
  document.querySelectorAll('body *').forEach(el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return;
    // .aura-sr-only is the standard visually-hidden idiom -- a 1px box with
    // clipped overflow, on purpose, for screen readers. Reporting it as
    // "clipped text" is a false alarm, and a detector that cries wolf gets
    // ignored, which is how the real overflow sat unnoticed in the first place.
    if (el.closest('.aura-sr-only') || (el.className || '').toString().includes('aura-sr-only')) return;
    // Absolutely-positioned panels (dropdowns, popovers) are MEANT to escape
    // their parent's box.
    if (s.position === 'absolute' || s.position === 'fixed' || s.position === 'sticky') return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;

    const label = el.tagName.toLowerCase() +
      (el.id ? '#' + el.id : '') +
      (el.className && typeof el.className === 'string'
        ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
    const txt = (el.innerText || el.value || '').trim().slice(0, 28);

    // 1. Text clipped by its own box.
    if (el.scrollWidth > el.clientWidth + 1 && s.overflowX !== 'auto' &&
        s.overflowX !== 'scroll' && el.children.length === 0) {
      const k = 'clip|' + label + '|' + txt;
      if (!seen.has(k)) { seen.add(k);
        bad.push(`CLIPPED  ${label} "${txt}" scrollW=${el.scrollWidth} clientW=${el.clientWidth}`); }
    }

    // 2. Sticking out past the parent's inline box.
    const p = el.parentElement;
    if (p && p !== document.body) {
      const ps = getComputedStyle(p);
      if (ps.overflow === 'visible' && ps.position !== 'static') { /* fallthrough */ }
      const pr = p.getBoundingClientRect();
      const over = Math.max(r.right - pr.right, pr.left - r.left);
      if (over > 2 && ps.overflowX === 'visible' && s.position !== 'fixed') {
        const k = 'out|' + label + '|' + txt;
        if (!seen.has(k)) { seen.add(k);
          bad.push(`OVERFLOWS ${label} "${txt}" by ${Math.round(over)}px past ${p.tagName.toLowerCase()}${p.id ? '#' + p.id : ''}`); }
      }
    }
  });
  const doc = document.documentElement;
  if (doc.scrollWidth > doc.clientWidth + 1) {
    bad.unshift(`PAGE SCROLLS HORIZONTALLY scrollW=${doc.scrollWidth} clientW=${doc.clientWidth}`);
  }
  return bad;
}"""

env = dict(os.environ, OWNER_UI_PORT=str(PORT))
proc = subprocess.Popen([VENV_PY, SERVE], env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(90):
    try:
        urllib.request.urlopen(LOGIN, timeout=2); break
    except Exception:
        if proc.poll() is not None:
            print("server died"); raise SystemExit(1)
        time.sleep(1)
print("owner up on", BASE)

from playwright.sync_api import sync_playwright  # noqa: E402

findings = {}
TOGGLE_REPORT = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.set_default_timeout(25000)

        # ---- the login screen, in BOTH languages, before authenticating ----
        for lang, url in (("en", LOGIN), ("ar", BASE + "/locale/ar?next=/auth/login")):
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector("input[name=password]")
            page.wait_for_timeout(600)
            findings["login-" + lang] = page.evaluate(DETECT)
            page.screenshot(path=str(OUT / ("login-%s.png" % lang)),
                            animations="disabled", timeout=10000)
            # The specific control that started this.
            print("\n[%s] password toggle geometry:" % lang, page.evaluate("""() => {
              const btn = document.querySelector('.aura-password-toggle');
              const inp = document.querySelector('.aura-password-field input');
              if (!btn || !inp) return '(toggle not rendered)';
              const b = btn.getBoundingClientRect(), i = inp.getBoundingClientRect();
              return JSON.stringify({
                label: btn.innerText.trim(),
                clipped: btn.scrollWidth > btn.clientWidth + 1,
                btnW: Math.round(b.width), scrollW: btn.scrollWidth, clientW: btn.clientWidth,
                spillsRight: Math.round(b.right - i.right),
                spillsLeft: Math.round(i.left - b.left),
              });
            }"""))

        # back to English for the authenticated sweep
        page.goto(BASE + "/locale/en?next=/auth/login", wait_until="domcontentloaded")
        page.wait_for_selector("input[name=email]")
        page.fill("input[name=email]", EMAIL)
        page.fill("input[name=password]", PASSWORD)
        with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
            page.click("button[type=submit]")
        page.wait_for_timeout(800)
        if "mfa-enroll" in page.url:
            body = page.evaluate("() => document.body.innerText")
            m = re.search(r"\b([A-Z2-7]{16,})\b", body)
            if m:
                page.locator("input:not([type=hidden])").last.fill(totp(m.group(1)))
                with page.expect_navigation(wait_until="domcontentloaded", timeout=60000):
                    page.click("button[type=submit]")
                page.wait_for_timeout(700)
        if "recovery" in page.url:
            try:
                page.click("button[type=submit]", timeout=4000)
            except Exception:
                pass
            page.goto(BASE + "/", wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

        pages = ["/", "/customers", "/subscriptions", "/catalog", "/leads",
                 "/quotes", "/orders", "/invoices", "/payments", "/attention"]
        for path in pages:
            try:
                page.goto(BASE + path, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(700)
                findings[path] = page.evaluate(DETECT)
            except Exception as exc:
                findings[path] = ["(could not load: %s)" % str(exc).splitlines()[0][:90]]
        b.close()
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()

print("\n" + "=" * 62)
total = 0
for where, items in findings.items():
    if not items:
        continue
    total += len(items)
    print("\n%s  (%d)" % (where, len(items)))
    for i in items[:8]:
        print("   ", i[:150])
print("\nTOTAL findings: %d across %d pages" % (total, len(findings)))
