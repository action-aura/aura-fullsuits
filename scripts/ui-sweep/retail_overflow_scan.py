"""Same clipped/overflow detector, against the retail till -- English and Arabic.

The Owner password toggle was found by a human staring at a screenshot. That
does not scale and should not have to. This asks the browser instead, on every
retail screen, at desktop and phone, in both languages.
"""
import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                              line_buffering=True)

BACKEND_DIR = Path("products/retail/backend").resolve()
for _p in (str(Path(".").resolve()), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_overflow_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
import app as _app_module  # noqa: E402
flask_app = _app_module.init_app()
import logging  # noqa: E402
logging.getLogger("werkzeug").setLevel(logging.ERROR)

PORT = 5695
from werkzeug.serving import make_server  # noqa: E402
server = make_server("127.0.0.1", PORT, flask_app, threaded=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
time.sleep(1.5)
BASE = "http://127.0.0.1:%d" % PORT

NAME, COMPANY = "Aura Owner", "Sweets & Co"
EMAIL, PASSWORD = "owner@overflow.local", "OverflowPass1"

SECTIONS = ["dashboard", "pos", "products", "customers", "promotions",
            "suppliers", "purchases", "returns", "reports", "admin-center",
            "email-notifications", "branches", "employees"]

DETECT = """() => {
  const bad = [], seen = new Set();
  document.querySelectorAll('body *').forEach(el => {
    const s = getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden') return;
    if (s.position === 'absolute' || s.position === 'fixed' || s.position === 'sticky') return;
    if (s.overflowX === 'auto' || s.overflowX === 'scroll') return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    const label = el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') +
      (typeof el.className === 'string' && el.className.trim()
        ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
    const txt = (el.innerText || el.value || '').trim().slice(0, 26);
    if (el.scrollWidth > el.clientWidth + 1 && el.children.length === 0) {
      const k = 'c|' + label + txt;
      if (!seen.has(k)) { seen.add(k);
        bad.push(`CLIPPED ${label} "${txt}" ${el.scrollWidth}>${el.clientWidth}`); }
    }
    const p = el.parentElement;
    if (p && p !== document.body) {
      const ps = getComputedStyle(p), pr = p.getBoundingClientRect();
      const over = Math.max(r.right - pr.right, pr.left - r.left);
      if (over > 2 && ps.overflowX === 'visible') {
        const k = 'o|' + label + txt;
        if (!seen.has(k)) { seen.add(k);
          bad.push(`OVERFLOWS ${label} "${txt}" by ${Math.round(over)}px`); }
      }
    }
  });
  const d = document.documentElement;
  if (d.scrollWidth > d.clientWidth + 1) {
    bad.unshift(`PAGE SCROLLS HORIZONTALLY ${d.scrollWidth}>${d.clientWidth}`);
  }
  return bad;
}"""

from playwright.sync_api import sync_playwright  # noqa: E402

findings = {}
with sync_playwright() as p:
    b = p.chromium.launch()
    first = True
    for w, h, size in ((1440, 900, "desktop"), (390, 844, "phone")):
        for lang in ("en", "ar"):
            ctx = b.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            page.set_default_timeout(25000)
            page.goto(BASE)
            try:
                page.wait_for_selector("#su-btn, input[type=password]", timeout=20000)
                if first and page.locator("#su-btn").count():
                    page.fill("#su-name", NAME); page.fill("#su-company", COMPANY)
                    page.fill("#su-email", EMAIL); page.fill("#su-pass", PASSWORD)
                    page.fill("#su-pass2", PASSWORD); page.click("#su-btn")
                    first = False
                else:
                    page.locator("input[type=email], input[type=text]").first.fill(EMAIL)
                    page.locator("input[type=password]").first.fill(PASSWORD)
                    page.keyboard.press("Enter")
                page.wait_for_selector("#sub-header-section", timeout=30000)
                page.wait_for_timeout(1800)
            except Exception as exc:
                findings["%s-%s AUTH" % (size, lang)] = [str(exc).splitlines()[0][:120]]
                ctx.close(); continue

            if lang == "ar":
                try:
                    page.evaluate("() => window.AuraI18n && AuraI18n.set && AuraI18n.set('ar')")
                    page.wait_for_timeout(1200)
                except Exception:
                    pass

            for section in SECTIONS:
                try:
                    page.evaluate("(s) => SubsystemApp._navigate(s)", section)
                    page.wait_for_timeout(1100)
                    hits = page.evaluate(DETECT)
                    if hits:
                        findings["%s/%s %s" % (size, lang, section)] = hits
                except Exception as exc:
                    findings["%s/%s %s ERR" % (size, lang, section)] = [
                        str(exc).splitlines()[0][:110]]
            ctx.close()
    b.close()
server.shutdown()

total = 0
print("\n" + "=" * 60)
for where, items in findings.items():
    total += len(items)
    print("\n%s (%d)" % (where, len(items)))
    for i in items[:6]:
        print("   ", i[:140])
print("\nTOTAL: %d findings across %d screens" % (total, len(SECTIONS) * 4))
