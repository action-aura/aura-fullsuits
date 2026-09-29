"""
retail_p1b_help_e2e.py -- Phase 1b crew P, item 11: the Help dialog, the support
link and the printable till key card, in a REAL browser against a REAL backend.
STANDALONE SCRIPT, NOT A PYTEST FILE.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_p1b_help_e2e.py [--only tag,tag]

Tags: null, card, print, set, look, roles.

What each block proves, and why it is built this way:

  null   both WhatsApp constants ship as null. While null the dialog has NO
         support link and the palette has NO support row: a dead "chat with
         support" link, or one that sends a customer's licence id to a wrong
         number, is worse than none. (Counting the link, not the outcome of
         a click.)
  card   the F-key card is GENERATED from RetailSystem.POS_PAYMENT_METHODS. It
         is checked two ways: against the known F1..F6 (so a broken list is
         caught) AND against a list we mutate in the page (so a card copy-typed
         by hand -- which would agree with the shipped list and still be wrong
         the day a method is added -- is caught). Failure shape 3 in reverse:
         agreeing with the current list alone proves nothing.
  print  the button really prints exactly once, from a hidden iframe holding the
         card, and leaves the app underneath untouched.
  set    with a number set (in-page; the constant is a code edit), the support
         link appears with a wa.me href whose text carries the REAL installation
         id, the REAL version and the screen the person was on.
  roles  a cashier gets Help too (it is not owner-only); nobody gets a Setup guide
         entry while AuraSetupGuide is absent, and the owner does once it exists.
  look   EN/AR x sand/night at 1366x768: dialog inside the viewport, actions
         >= 44px tall, the ids dir=ltr inside an RTL dialog, screenshots on disk.
"""
from __future__ import annotations

import json
import sys
import tempfile
import urllib.parse
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import (  # noqa: E402
    DEFAULT_WAIT_MS, VIEWPORT, Backend, _api_login, _owner_company_id, _seed_cashier,
    assert_reachable, human_click,
)
from retail_p1b_palette_e2e import pick_port  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OWNER_EMAIL = "tareq@help.test"
OWNER_PASSWORD = "HelpOwner_123!"
RESULTS: list[tuple[str, bool, str]] = []
ONLY: set[str] = set()
EXPECTED_FKEYS = [("F1", "Cash"), ("F2", "Card"), ("F3", "Mobile / CliQ"),
                  ("F4", "Transfer"), ("F5", "Credit"), ("F6", "Voucher")]
# The card prints ARABIC whatever the screen language (English line beneath).
# Typed here on purpose, not read from ar.json: reading the dictionary the card
# reads would agree with any dictionary.
EXPECTED_FKEYS_AR = ["نقدًا", "بطاقة", "محفظة إلكترونية / كليك", "حوالة مصرفية", "آجل", "قسيمة"]
# Fixed till keys printed under the F-keys (from _onPOSShortcut).
EXPECTED_OTHER_KEYS = ["Enter", "X", "Delete", "/", "N *"]
FAKE_NUMBER = "+962 79-123 4567"
FAKE_DIGITS = "962791234567"


def check(tag: str, name: str, fn) -> None:
    if ONLY and tag not in ONLY:
        return
    try:
        fn()
        RESULTS.append((f"[{tag}] {name}", True, ""))
        print(f"[PASS] [{tag}] {name}", flush=True)
    except Exception as e:  # noqa: BLE001
        RESULTS.append((f"[{tag}] {name}", False, str(e)))
        print(f"[FAIL] [{tag}] {name}\n       {e}", flush=True)


def wanted(tag: str) -> bool:
    return not ONLY or tag in ONLY


def open_page(browser, base, *, email=OWNER_EMAIL, password=OWNER_PASSWORD, lang="en", theme=None):
    ctx = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
    page = ctx.new_page()
    page.goto(base, wait_until="domcontentloaded")
    _api_login(page, email, password)
    page.goto(base, wait_until="domcontentloaded")
    page.locator("#sub-header-section").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
    page.wait_for_timeout(600)
    if page.evaluate("() => AuraI18n.current") != lang:
        page.evaluate("(l) => AuraI18n.setLang(l)", lang)
        page.wait_for_function("(l) => AuraI18n.current === l", arg=lang, timeout=DEFAULT_WAIT_MS)
        page.wait_for_timeout(600)
    if theme:
        page.evaluate("(t) => ThemeEngine.apply(t)", theme)
        page.wait_for_timeout(150)
    return ctx, page


def open_help_by_clicks(page):
    """Chip -> Help, by human clicks (rule 6b)."""
    human_click(page, "#sub-identity-chip-btn", "identity chip")
    page.locator('#sub-identity-menu [data-action="help"]').wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
    human_click(page, '#sub-identity-menu [data-action="help"]', "chip menu > Help")
    page.locator("#aura-help-dialog").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)


def close_help(page):
    page.keyboard.press("Escape")
    page.wait_for_function("() => !document.getElementById('aura-help-overlay')", timeout=DEFAULT_WAIT_MS)


def wait_context(page):
    """The dialog fills the id and version in when load() resolves."""
    page.wait_for_function(
        "() => { const v = document.getElementById('aura-help-version'), i = document.getElementById('aura-help-install');"
        " return v && i && v.textContent.trim() !== '—' && i.textContent.trim() !== '—'; }", timeout=DEFAULT_WAIT_MS)


def card_rows(page):
    """Rows of the card exactly as the print iframe will show them:
    [key, arabic line, english line]."""
    return page.evaluate("""() => {
      const doc = new DOMParser().parseFromString(AuraHelp.fkeyCardHtml(), 'text/html');
      const card = doc.getElementById('fkey-card');
      if (!card) return null;
      return [...card.querySelectorAll('tbody tr')].map(tr => {
        const ar = tr.querySelector('.ar'), en = tr.querySelector('.en');
        return [tr.children[0].textContent.trim(), ar ? ar.textContent.trim() : '', en ? en.textContent.trim() : ''];
      });
    }""")


def press_fkey_in_till(page, n):
    """Press F<n> as a real key on the real till; return the method it selected."""
    page.keyboard.press(f"F{n}")
    page.wait_for_timeout(120)
    return page.evaluate("() => RetailSystem._paymentMethod")


def main() -> int:
    global ONLY
    args = sys.argv[1:]
    if "--only" in args:
        ONLY = set(args[args.index("--only") + 1].split(","))

    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_p1b_help_"))
    backend = Backend(data_dir)
    backend.port = pick_port()
    backend.base_url = f"http://127.0.0.1:{backend.port}"
    shots = Path(tempfile.mkdtemp(prefix="aura_retail_p1b_help_shots_"))
    print(f"Backend: {backend.base_url}\nScreenshots: {shots}", flush=True)
    cashier_email = f"cash-{uuid.uuid4().hex[:6]}@help.test"
    cashier_pw = "HelpCashier_123!"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            backend.start()
            c0 = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            p0 = c0.new_page()
            p0.goto(backend.base_url, wait_until="domcontentloaded")
            p0.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            p0.fill("#su-name", "Tareq")
            p0.fill("#su-company", "Help Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()
            _seed_cashier(data_dir, _owner_company_id(data_dir), cashier_email, cashier_pw)

            ctx, pg = open_page(browser, backend.base_url)

            # ---- what the server says, to compare with what the dialog shows
            truth = pg.evaluate("""async () => {
              const v = await (await fetch('/api/version', {credentials:'include'})).json();
              const l = await (await fetch('/api/licensing/status', {credentials:'include'})).json();
              const s = l.data || l;
              return { version: v.app_version, install: s.installation_id };
            }""")
            assert truth["version"] and truth["install"], f"fixture problem: server did not give version/install id: {truth}"

            # ================= NULL STATE =================
            def null_state():
                assert pg.evaluate("() => AuraHelp.ACTION_AURA_SUPPORT_WHATSAPP === null && AuraHelp.ACTION_AURA_SALES_WHATSAPP === null"), \
                    "the shipped constants are not both null"
                assert pg.evaluate("() => AuraHelp.supportUrl() === null && AuraHelp.salesUrl() === null")
                open_help_by_clicks(pg)
                assert pg.locator('#aura-help-dialog [data-help="support"]').count() == 0, "a support link exists while the number is null"
                assert pg.locator('#aura-help-dialog [data-help="fkeys"]').count() == 1
                wait_context(pg)
                assert pg.locator("#aura-help-version").inner_text().strip() == truth["version"]
                assert pg.locator("#aura-help-install").inner_text().strip() == truth["install"]
                assert pg.locator('#aura-help-dialog a[href*="wa.me"]').count() == 0
                close_help(pg)
                keys = pg.evaluate("() => AuraPalette.entries().map(e => e.key)")
                assert "help:open" in keys and "help:fkeys" in keys, f"palette lost its Help rows: {keys}"
                assert "help:support" not in keys, "palette offers a support row while the number is null"

            check("null", "number null: no support link in the dialog or the palette; version and installation id are the server's", null_state)

            # ================= CARD =================
            def open_till():
                pg.evaluate("() => SubsystemApp._navigate('pos')")
                pg.wait_for_function("() => SubsystemApp.currentSection === 'pos' && document.querySelectorAll('.pos-pay-btn').length >= 6", timeout=DEFAULT_WAIT_MS)
                pg.wait_for_timeout(600)

            def card_matches_real_keys():
                rows = card_rows(pg)
                f_rows = rows[:6]
                assert [r[0] for r in f_rows] == [k for k, _ in EXPECTED_FKEYS], f"card keys {rows}"
                assert [r[1] for r in f_rows] == EXPECTED_FKEYS_AR, f"card Arabic lines {[r[1] for r in f_rows]}"
                assert [r[2] for r in f_rows] == [n for _, n in EXPECTED_FKEYS], f"card English lines {[r[2] for r in f_rows]}"
                assert [r[0] for r in rows[6:]] == EXPECTED_OTHER_KEYS, f"the other till keys {[r[0] for r in rows[6:]]}"
                # And against the REAL keyboard: press F1..F6 on the real till and
                # see which method each selects; the card row of that key must name it.
                open_till()
                by_method = pg.evaluate("() => Object.fromEntries(RetailSystem.POS_PAYMENT_METHODS.map(m => [m.method, m.label]))")
                # The Credit tile is aria-disabled until a credit customer is on the
                # sale (F5 then only explains itself with a toast). Enable the tile
                # the way a credit customer would, so F5 is measured, not skipped.
                pg.evaluate("() => document.querySelectorAll('.pos-pay-btn[data-method=\"credit\"]').forEach(b => b.setAttribute('aria-disabled', 'false'))")
                for n, (_, english) in enumerate(EXPECTED_FKEYS, start=1):
                    method = press_fkey_in_till(pg, n)
                    assert by_method[method] == english == rows[n - 1][2], \
                        f"F{n} on the real till chose {method!r} ({by_method.get(method)!r}); the card says {rows[n - 1]}"

            check("card", "the card prints F1..F6 (Arabic + English) that match what the real keys do, plus Enter/X/Delete/ / / N*", card_matches_real_keys)

            def card_follows_the_handler_mapping():
                # REORDER the source in the page. The till maps F<n> by POSITION
                # (POS_PAYMENT_METHODS[n-1]); the card must follow the position,
                # not the entry's own `fkey` text (which we leave stale on purpose:
                # a card built from m.fkey keeps printing the OLD order and fails).
                open_till()
                res = pg.evaluate("""() => {
                  const saved = RetailSystem.POS_PAYMENT_METHODS;
                  RetailSystem.POS_PAYMENT_METHODS = saved.slice().reverse();
                  try {
                    const doc = new DOMParser().parseFromString(AuraHelp.fkeyCardHtml(), 'text/html');
                    const card = [...doc.querySelectorAll('#fkey-card tbody tr')].slice(0, 6)
                      .map(tr => [tr.children[0].textContent.trim(), (tr.querySelector('.en') || {}).textContent]);
                    return { card, order: RetailSystem.POS_PAYMENT_METHODS.map(m => m.method) };
                  } finally { RetailSystem.POS_PAYMENT_METHODS = saved; }
                }""")
                want = [["F%d" % (i + 1), n] for i, n in enumerate(reversed([n for _, n in EXPECTED_FKEYS]))]
                assert res["card"] == want, f"the card did not follow the reordered list by position: {res['card']} != {want}"
                # Real key against the same reordered list: F1 must select the
                # entry the card now calls F1.
                pg.evaluate("() => { RetailSystem.POS_PAYMENT_METHODS = RetailSystem.POS_PAYMENT_METHODS.slice().reverse(); }")
                try:
                    method = press_fkey_in_till(pg, 1)
                finally:
                    pg.evaluate("() => { RetailSystem.POS_PAYMENT_METHODS = RetailSystem.POS_PAYMENT_METHODS.slice().reverse(); }")
                assert method == res["order"][0], f"F1 selected {method!r}, the reordered list's first is {res['order'][0]!r}"

            check("card", "the card follows the handler's POSITION mapping when the list is reordered (not m.fkey)", card_follows_the_handler_mapping)

            def card_seventh_method_has_no_key():
                extra = pg.evaluate("""() => {
                  const saved = RetailSystem.POS_PAYMENT_METHODS;
                  RetailSystem.POS_PAYMENT_METHODS = saved.concat([{ method: 'gift', label: 'Gift card', fkey: 'F7' }]);
                  try {
                    const doc = new DOMParser().parseFromString(AuraHelp.fkeyCardHtml(), 'text/html');
                    return [...doc.querySelectorAll('#fkey-card tbody tr')].map(tr => tr.children[0].textContent.trim());
                  } finally { RetailSystem.POS_PAYMENT_METHODS = saved; }
                }""")
                assert "F7" not in extra, "the card printed a key the till does not answer (handler only maps F1..F6)"

            check("card", "a 7th payment method is NOT printed as F7 (the handler only answers F1..F6)", card_seventh_method_has_no_key)

            def card_arabic():
                # The card prints Arabic whatever the SCREEN language: in English
                # and in Arabic UI it is byte-identical.
                html_en = pg.evaluate("() => AuraHelp.fkeyCardHtml()")
                pg.evaluate("() => AuraI18n.setLang('ar')")
                pg.wait_for_function("() => AuraI18n.current === 'ar'", timeout=DEFAULT_WAIT_MS)
                pg.wait_for_timeout(400)
                html = pg.evaluate("() => AuraHelp.fkeyCardHtml()")
                pg.evaluate("() => AuraI18n.setLang('en')")
                pg.wait_for_function("() => AuraI18n.current === 'en'", timeout=DEFAULT_WAIT_MS)
                pg.wait_for_timeout(400)
                assert 'dir="rtl"' in html and 'lang="ar"' in html, "the card is not right-to-left Arabic"
                assert '<td class="k" dir="ltr">F1</td>' in html, "F-key labels must stay left-to-right inside the RTL card"
                assert html == html_en, "the card changed with the screen language (it must always print Arabic)"
                assert html_en.startswith('<!DOCTYPE html><html lang="ar" dir="rtl">'), "an English screen printed a non-Arabic card"

            check("card", "the card is Arabic + RTL on an English screen AND an Arabic screen, F-key labels stay LTR", card_arabic)

            # ================= PRINT =================
            def print_prints_once():
                pg.evaluate("""() => {
                  window.__printCalls = 0;
                  const orig = AuraHelp.fkeyCardHtml;
                  AuraHelp.fkeyCardHtml = function () {
                    return orig.call(this).replace('</body>', '<script>window.print = function () { parent.__printCalls += 1; };<\\/script></body>');
                  };
                }""")
                open_help_by_clicks(pg)
                url_before = pg.url
                human_click(pg, '#aura-help-dialog [data-help="fkeys"]', "Print the till key card")
                try:
                    pg.wait_for_function("() => window.__printCalls >= 1", timeout=DEFAULT_WAIT_MS)
                except Exception:
                    diag = pg.evaluate("""() => { const f = AuraHelp._lastCardFrame;
                      return { calls: window.__printCalls, frame: !!f, connected: !!(f && f.isConnected),
                               ready: f && f.contentDocument && f.contentDocument.readyState,
                               hasScript: !!(f && f.contentDocument && f.contentDocument.querySelector('script')),
                               srcdocHasHook: !!(f && f.srcdoc.includes('__printCalls')) }; }""")
                    raise AssertionError(f"print() never reached the injected counter: {diag}")
                pg.wait_for_timeout(500)
                assert pg.evaluate("() => window.__printCalls") == 1, "print() was not called exactly once"
                framed = pg.evaluate("""() => { const f = AuraHelp._lastCardFrame; const d = f && f.contentDocument;
                    return d ? [...d.querySelectorAll('#fkey-card tbody tr')].map(tr => tr.children[0].textContent.trim()) : null; }""")
                # F1..F6 then the fixed till keys: the whole card, not just a prefix.
                assert framed == [k for k, _ in EXPECTED_FKEYS] + EXPECTED_OTHER_KEYS, f"the printed frame held {framed}"
                assert pg.url == url_before and pg.locator("#aura-help-dialog").count() == 1, "printing disturbed the app underneath"
                close_help(pg)
                pg.reload(wait_until="domcontentloaded")
                pg.locator("#sub-header-section").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                pg.wait_for_timeout(500)

            def print_prints_once_clean():
                # The print check patches AuraHelp in the page and leaves the
                # dialog open on failure; a reload puts the page back so a
                # RED here cannot cascade into the checks that follow.
                try:
                    print_prints_once()
                finally:
                    pg.reload(wait_until="domcontentloaded")
                    pg.locator("#sub-header-section").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                    pg.wait_for_timeout(500)

            check("print", "the button prints the real card exactly once from a hidden frame", print_prints_once_clean)

            # ================= NUMBER SET =================
            def number_set():
                pg.evaluate("() => SubsystemApp._navigate('reports')")
                pg.wait_for_function("() => SubsystemApp.currentSection === 'reports'", timeout=DEFAULT_WAIT_MS)
                pg.evaluate("(n) => { AuraHelp.ACTION_AURA_SUPPORT_WHATSAPP = n; }", FAKE_NUMBER)
                open_help_by_clicks(pg)
                wait_context(pg)
                link = pg.locator('#aura-help-dialog a[data-help="support"]')
                assert link.count() == 1, "no support link although a number is set"
                pg.wait_for_function(
                    "(i) => (document.querySelector('#aura-help-dialog a[data-help=support]').getAttribute('href')||'').includes(encodeURIComponent('Installation: ' + i))",
                    arg=truth["install"], timeout=DEFAULT_WAIT_MS)
                href = link.get_attribute("href")
                assert href.startswith(f"https://wa.me/{FAKE_DIGITS}?text="), f"wrong target: {href}"
                text = urllib.parse.unquote(href.split("?text=", 1)[1])
                assert f"Installation: {truth['install']}" in text, text
                assert f"Version: {truth['version']}" in text, text
                assert "Screen: reports" in text, f"the screen the person was on is missing: {text!r}"
                assert link.get_attribute("target") == "_blank" and "noopener" in (link.get_attribute("rel") or "")
                assert_reachable(pg, '#aura-help-dialog a[data-help="support"]', "support link")
                close_help(pg)
                keys = pg.evaluate("() => AuraPalette.entries().map(e => e.key)")
                assert "help:support" in keys, "palette lacks the support row once a number is set"
                pg.evaluate("() => { AuraHelp.ACTION_AURA_SUPPORT_WHATSAPP = null; }")

            check("set", "number set: the support link carries the real installation id, version and screen; palette row appears", number_set)

            # ================= ROLES =================
            def owner_no_setup_guide_without_module():
                assert pg.evaluate("() => typeof window.AuraSetupGuide") == "undefined", "AuraSetupGuide exists; this check assumes it does not yet"
                human_click(pg, "#sub-identity-chip-btn", "identity chip")
                pg.locator("#sub-identity-menu").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                assert pg.locator('#sub-identity-menu [data-action="setup-guide"]').count() == 0, "Setup guide offered with no module behind it"
                pg.keyboard.press("Escape")
                open_help_by_clicks(pg)
                assert pg.locator('#aura-help-dialog [data-help="setup-guide"]').count() == 0
                close_help(pg)

            check("roles", "owner: no Setup guide entry while the module is absent (no dead row)", owner_no_setup_guide_without_module)

            def owner_setup_guide_when_module_exists():
                pg.reload(wait_until="domcontentloaded")
                pg.locator("#sub-header-section").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                pg.evaluate("() => { window.__guideOpened = 0; window.AuraSetupGuide = { open() { window.__guideOpened += 1; } }; }")
                pg.evaluate("() => SubsystemApp.render && SubsystemApp.render()") if pg.evaluate("() => typeof SubsystemApp.render") == "function" else None
                pg.wait_for_timeout(300)
                human_click(pg, "#sub-identity-chip-btn", "identity chip")
                if pg.locator('#sub-identity-menu [data-action="setup-guide"]').count() == 0:
                    # The chip menu is built at render time; re-launch to rebuild it.
                    pg.keyboard.press("Escape")
                    pg.evaluate("() => SubsystemApp.launch('retail', 'dashboard')")
                    pg.wait_for_timeout(600)
                    human_click(pg, "#sub-identity-chip-btn", "identity chip")
                pg.locator('#sub-identity-menu [data-action="setup-guide"]').wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                pg.keyboard.press("Escape")
                open_help_by_clicks(pg)
                human_click(pg, '#aura-help-dialog [data-help="setup-guide"]', "Help > Setup guide")
                pg.wait_for_function("() => window.__guideOpened === 1", timeout=DEFAULT_WAIT_MS)
                assert pg.locator("#aura-help-overlay").count() == 0, "Help stayed open behind the setup guide"
                assert "help:setup-guide" in pg.evaluate("() => AuraPalette.entries().map(e => e.key)")

            check("roles", "owner: Setup guide appears in chip, Help and palette once the module exists, and opens it once", owner_setup_guide_when_module_exists)
            ctx.close()

            def cashier_gets_help_not_setup():
                c, p = open_page(browser, backend.base_url, email=cashier_email, password=cashier_pw)
                try:
                    p.evaluate("() => { window.AuraSetupGuide = { open() {} }; }")     # even if the module existed
                    p.evaluate("() => SubsystemApp.launch('retail', 'pos')")
                    p.wait_for_timeout(600)
                    human_click(p, "#sub-identity-chip-btn", "identity chip")
                    p.locator("#sub-identity-menu").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                    assert p.locator('#sub-identity-menu [data-action="help"]').count() == 1, "cashier has no Help entry"
                    assert p.locator('#sub-identity-menu [data-action="setup-guide"]').count() == 0, "cashier is offered the Setup guide"
                    human_click(p, '#sub-identity-menu [data-action="help"]', "chip menu > Help")
                    p.locator("#aura-help-dialog").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
                    assert p.locator('#aura-help-dialog [data-help="setup-guide"]').count() == 0
                    assert "help:setup-guide" not in p.evaluate("() => AuraPalette.entries().map(e => e.key)")
                finally:
                    c.close()

            check("roles", "cashier: Help yes; Setup guide never (chip, dialog, palette)", cashier_gets_help_not_setup)

            # ================= LOOK =================
            if wanted("look"):
                for lang in ("en", "ar"):
                    for theme in ("sand", "night"):
                        def look(lang=lang, theme=theme):
                            c, p = open_page(browser, backend.base_url, lang=lang, theme=theme)
                            try:
                                p.evaluate("(n) => { AuraHelp.ACTION_AURA_SUPPORT_WHATSAPP = n; }", FAKE_NUMBER)
                                open_help_by_clicks(p)
                                wait_context(p)
                                g = p.evaluate("""() => {
                                  const d = document.getElementById('aura-help-dialog').getBoundingClientRect();
                                  const acts = [...document.querySelectorAll('.aura-help-action')].map(a => a.getBoundingClientRect());
                                  return { l: d.left, r: d.right, t: d.top, b: d.bottom, vw: innerWidth, vh: innerHeight,
                                           n: acts.length, minH: Math.min(...acts.map(a => a.height)),
                                           dir: getComputedStyle(document.getElementById('aura-help-dialog')).direction,
                                           idDir: getComputedStyle(document.getElementById('aura-help-install')).direction };
                                }""")
                                assert g["n"] == 2, f"expected support + key card actions, got {g['n']}"
                                assert g["l"] >= 0 and g["r"] <= g["vw"] and g["t"] >= 0 and g["b"] <= g["vh"], f"dialog outside viewport: {g}"
                                assert g["minH"] >= 44, f"an action is only {g['minH']}px tall"
                                assert g["dir"] == ("rtl" if lang == "ar" else "ltr") and g["idDir"] == "ltr", g
                                p.screenshot(path=str(shots / f"help_{lang}_{theme}.png"))
                            finally:
                                c.close()
                        check("look", f"{lang}/{theme} 1366x768: dialog inside the viewport, actions >= 44px, ids LTR, screenshot taken", look)
        finally:
            backend.stop()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"\n     {detail}"))
    print(f"\n{passed}/{len(RESULTS)} checks passed.\nScreenshots: {shots}")
    return 0 if passed == len(RESULTS) and RESULTS else 1


if __name__ == "__main__":
    sys.exit(main())
