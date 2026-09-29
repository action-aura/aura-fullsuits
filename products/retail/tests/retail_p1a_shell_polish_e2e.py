"""
retail_p1a_shell_polish_e2e.py -- P1a crew S, final polish round (F5-F8), proved
in a REAL browser against a REAL backend. STANDALONE SCRIPT, NOT A PYTEST FILE.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_p1a_shell_polish_e2e.py

F5  a device-bound item (Audit Log, adminOnly) is SHOWN, DISABLED, with the
    sentence "This can only be changed on the main computer" printed on the row,
    on a computer that is not the main one -- in the sidebar AND the phone More
    sheet. Clicking explains and never navigates. On the main computer it works.
    The "other computer" is real: the backend is RESTARTED after the registry
    DB is edited (device_context caches the device row in-process, so flipping
    the DB under a live server proves nothing -- same technique as the
    coordinator's joined2.py).
F6  the identity chip reads "name . Role . Branch . Computer", skipping absent
    parts. /api/devices/me is fetched for real and PATCHED in the route handler
    only to add the fields crew D is adding (branch_name / device_name), so the
    real device row is still what the shell reads.
F7  the Connected group header shows its FULL name in every theme, EN and AR, at
    1366 (wrapped is fine, clipped is not).
F8  the Arabic status word for Off is "متوقّف", not the verb "إيقاف" (stop);
    English still says "Off". The new keys are overlaid onto /static/locales/
    ar.json in the route handler, because the coordinator merges i18n-S.json
    into the catalogue -- the shell must work with either catalogue.

Reachability rule (contract 6b): every "can a person click it" goes through
human_click (box inside viewport, elementFromPoint at the centre is the element,
then a raw mouse click).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retail_p1a_shell_e2e import (  # noqa: E402
    BACKEND_DIR, DEFAULT_WAIT_MS, REPO_ROOT, VIEWPORT, Backend, _api_login, _owner_company_id,
    _seed_cashier, assert_reachable, human_click, probe,
)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SENTENCE = "This can only be changed on the main computer"
OWNER_EMAIL = "tareq@polish.test"
OWNER_PASSWORD = "PolishOwner_123!"
LOCALES = BACKEND_DIR.parent / "frontend" / "locales"
THEMES = ("light", "sand", "dark", "night", "dusk")

# The F8 keys, exactly as they sit in i18n-S.json for the coordinator to merge.
AR_OVERLAY = {
    SENTENCE: "يمكن تغيير هذا من الجهاز الرئيسي فقط",
    "Off (status)": "متوقّف",
    "Connected (status)": "متصل",
    "Needs attention (status)": "يحتاج إلى انتباه",
    "Failing (status)": "لا يعمل",
    "Can't check (status)": "تعذّر التحقق",
    "Checking… (status)": "جارٍ التحقق…",
}
BARE_VERB_OFF = "إيقاف"

RESULTS: list[tuple[str, bool, str]] = []


def check(name, fn):
    try:
        fn()
        RESULTS.append((name, True, ""))
        print(f"[PASS] {name}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append((name, False, str(e)))
        print(f"[FAIL] {name}\n       {e}")


def open_page(browser, base, viewport, *, email=OWNER_EMAIL, password=OWNER_PASSWORD, lang="en",
              coarse=False, devices_patch=None, overlay_ar=True):
    """Fresh context signed in as `email`. devices_patch: dict merged into the
    REAL /api/devices/me `device` object (None = untouched). overlay_ar: add the
    F8 keys to the served ar.json. Returns (ctx, page)."""
    ctx = browser.new_context(viewport=viewport, reduced_motion="reduce", has_touch=coarse)
    page = ctx.new_page()
    if devices_patch is not None:
        def _me(route, _req=None):
            resp = route.fetch()
            body = resp.json()
            body.setdefault("device", {})
            body["device"] = dict(body.get("device") or {}, **devices_patch)
            route.fulfill(response=resp, body=json.dumps(body), content_type="application/json")
        page.route("**/api/devices/me", _me)
    if overlay_ar:
        def _ar(route, _req=None):
            resp = route.fetch()
            body = resp.json()
            body.update(AR_OVERLAY)
            route.fulfill(response=resp, body=json.dumps(body, ensure_ascii=False),
                          content_type="application/json; charset=utf-8")
        page.route("**/static/locales/ar.json", _ar)
    page.goto(base, wait_until="domcontentloaded")
    _api_login(page, email, password)
    page.goto(base, wait_until="domcontentloaded")
    page.locator("#sub-header-section").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
    page.wait_for_timeout(600)
    set_lang(page, lang)
    return ctx, page


def set_lang(page, lang):
    if page.evaluate("() => AuraI18n.current") != lang:
        page.evaluate("(l) => AuraI18n.setLang(l)", lang)
        page.wait_for_function("(l) => AuraI18n.current === l", arg=lang, timeout=DEFAULT_WAIT_MS)
        page.wait_for_timeout(600)


def open_group_of(page, section_id):
    """Open (by a real click) the sidebar group that holds `section_id`."""
    key = page.evaluate(
        "(id) => { const it = document.querySelector(`.sub-nav-group-items [data-section=\"${id}\"]`);"
        " return it ? it.closest('.sub-nav-group-items').dataset.groupItems : null; }", section_id)
    assert key, f"no sidebar group contains [data-section={section_id}] at all"
    hidden = page.evaluate(
        "(k) => document.querySelector(`.sub-nav-group-items[data-group-items=\"${k}\"]`).hasAttribute('hidden')", key)
    if hidden:
        human_click(page, f'button[data-group-toggle="{key}"]', f"group header {key}")
        page.wait_for_timeout(250)
    return key


def toast_texts(page):
    return page.evaluate(
        "() => [...document.querySelectorAll('[role=status],[role=alert]')].map(e => e.innerText.trim()).filter(Boolean)")


def state_of(page):
    return page.evaluate("() => ({section: SubsystemApp.currentSection, hash: location.hash, admin: SubsystemApp.isAdminDevice})")


def flip_admin_device(data_dir, company_id, *, fake_id=None):
    """Move the main-computer flag off this computer onto a fake 'Front till'
    (fake_id given -> insert it) or delete that fake (fake_id given, remove=...)."""
    os.environ["AURA_APP_DATA"] = str(data_dir)
    for p in (str(REPO_ROOT), str(BACKEND_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)
    from commercial_runtime.identity.registry_db import get_conn
    conn = get_conn()
    try:
        conn.execute("UPDATE devices SET is_admin_device=0 WHERE company_id=?", (company_id,))
        if fake_id:
            conn.execute(
                "INSERT INTO devices (id, company_id, device_label, platform, is_admin_device, status, first_seen_at)"
                " VALUES (?,?,?,?,1,'active',?)",
                (fake_id, company_id, "Front till", "WINDOWS", datetime.utcnow().isoformat()))
        conn.commit()
    finally:
        conn.close()


def restart(backend):
    backend.stop()
    backend.start()


def main() -> int:
    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_p1a_polish_"))
    backend = Backend(data_dir)
    shots = Path(tempfile.mkdtemp(prefix="aura_retail_p1a_polish_shots_"))
    print(f"Screenshots: {shots}")
    cashier_email = f"cash-{uuid.uuid4().hex[:6]}@polish.test"
    cashier_pw = "PolishCashier_123!"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            backend.start()
            # ---- owner signs up through the real first-run form --------------
            c0 = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            p0 = c0.new_page()
            p0.goto(backend.base_url, wait_until="domcontentloaded")
            p0.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            p0.fill("#su-name", "Tareq")
            p0.fill("#su-company", "Polish Shop")
            p0.fill("#su-email", OWNER_EMAIL)
            p0.fill("#su-pass", OWNER_PASSWORD)
            p0.fill("#su-pass2", OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()
            company_id = _owner_company_id(data_dir)
            _seed_cashier(data_dir, company_id, cashier_email, cashier_pw)

            # =================================================================
            # MAIN COMPUTER (unchanged behaviour, both halves of F5)
            # =================================================================
            ctx, pg = open_page(browser, backend.base_url, VIEWPORT)

            def main_audit_log_works():
                assert state_of(pg)["admin"] is True, "the signing-up computer should be the main computer"
                open_group_of(pg, "audit-log")
                it = pg.evaluate("() => { const e = document.querySelector('[data-section=\"audit-log\"]');"
                                 " return {cls: e.className, dis: e.getAttribute('aria-disabled'), txt: e.innerText}; }")
                assert "is-main-only" not in it["cls"] and it["dis"] is None, f"main computer shows Audit Log disabled: {it}"
                assert SENTENCE not in it["txt"], f"main computer prints the main-only sentence on Audit Log: {it}"
                human_click(pg, '.sub-nav-item[data-section="audit-log"]', "Audit Log (main)")
                pg.wait_for_timeout(1200)
                assert state_of(pg)["section"] == "audit-log", f"main computer: click did not navigate: {state_of(pg)}"

            check("F5 main computer: Audit Log is normal and a click opens it", main_audit_log_works)

            # =================================================================
            # F6 -- identity chip. Real /api/devices/me, patched to add D's fields.
            # =================================================================
            def chip_text(page):
                return page.evaluate("() => document.querySelector('#sub-identity-chip-btn .sub-identity-text').innerText.replace(/\\s+/g,' ').trim()")   # not the 'TA' avatar initials

            def chip_parts(page):
                return page.evaluate("() => [...document.getElementById('sub-identity-chip-btn').querySelectorAll('.sub-identity-name,.sub-role-pill,.sub-identity-place,.sub-identity-badge,.sub-identity-meta > *')].map(e => e.innerText.trim()).filter(Boolean)")
            ctx.close()

            def f6_full_chip():
                c, p = open_page(browser, backend.base_url, VIEWPORT,
                                 devices_patch={"branch_name": "Amman Branch", "device_name": "Front till"})
                try:
                    txt = chip_text(p)
                    assert txt.startswith("Tareq"), f"name not title-cased local-part 'Tareq': {txt!r}"
                    for part in ("Amman Branch", "Front till"):
                        assert part in txt, f"{part!r} missing from chip {txt!r}"
                    assert txt.index("Tareq") < txt.index("Owner") < txt.index("Amman Branch") < txt.index("Front till"), \
                        f"order should be name, Role, Branch, Computer: {txt!r}"
                    assert txt.count("Amman Branch") == 1, f"branch shown more than once: {txt!r}"
                    r = probe(p, "#sub-identity-chip-btn")
                    assert r["inside"], f"chip box not fully inside the viewport: {r}"
                    # nothing inside the chip is cut off horizontally
                    cut = p.evaluate("() => [...document.querySelectorAll('#sub-identity-chip-btn *')].filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow !== 'visible' && e.clientWidth > 0).map(e => e.className + ':' + e.scrollWidth + '>' + e.clientWidth)")
                    assert not cut, f"chip content is clipped at 1366: {cut}"
                    p.screenshot(path=str(shots / "f6_chip_full.png"), clip={"x": 0, "y": 0, "width": 1366, "height": 80})
                finally:
                    c.close()

            check("F6 chip: name . Role . Branch . Computer, in order, once, unclipped", f6_full_chip)

            def f6_partial_chips():
                for patch, want, absent in (
                    ({"branch_name": "Amman Branch", "device_name": None, "device_label": None, "platform": None}, "Amman Branch", None),
                    ({"branch_name": None, "device_name": "Front till"}, "Front till", "Amman Branch"),
                    ({"branch_name": None, "device_name": None, "device_label": None, "platform": None}, None, "Amman Branch"),
                ):
                    c, p = open_page(browser, backend.base_url, VIEWPORT, devices_patch=patch)
                    try:
                        txt = chip_text(p)
                        assert txt.startswith("Tareq"), f"name missing for patch {patch}: {txt!r}"
                        if want:
                            assert want in txt, f"{want!r} missing for patch {patch}: {txt!r}"
                        if absent:
                            assert absent not in txt, f"absent part {absent!r} printed for patch {patch}: {txt!r}"
                        assert " ·  " not in txt and "· ·" not in txt and not txt.rstrip().endswith("·"), f"dangling separator for {patch}: {txt!r}"
                    finally:
                        c.close()

            check("F6 chip: absent parts are skipped (no dangling separator)", f6_partial_chips)

            def f6_real_api_fields():
                """Read-only look at what /api/devices/me really serves today."""
                c, p = open_page(browser, backend.base_url, VIEWPORT)
                try:
                    me = p.evaluate("() => fetch('/api/devices/me', {credentials:'include'}).then(r => r.json())")
                    dev = me.get("device") or {}
                    have_branch = "branch_name" in dev or "branch_name" in me
                    print(f"       [info] real /api/devices/me carries branch_name: {have_branch}; "
                          f"device keys: {sorted(dev.keys())}")
                    if have_branch and dev.get("branch_name"):
                        assert dev["branch_name"] in chip_text(p), \
                            f"API serves branch_name={dev['branch_name']!r} but the chip shows {chip_text(p)!r}"
                finally:
                    c.close()

            check("F6 chip: real API field check (branch shown once crew D serves it)", f6_real_api_fields)

            # =================================================================
            # F7 -- Connected header label fully readable, 5 themes x EN/AR
            # =================================================================
            F7_JS = """() => {
              const b = document.querySelector('[data-group-toggle="connected"]');
              if (!b) return {found:false};
              const lab = b.querySelector('.sub-nav-group-label-text');
              const cs = getComputedStyle(lab);
              const r = lab.getBoundingClientRect(), br = b.getBoundingClientRect();
              const side = document.querySelector('#sub-nav') || b.parentElement;
              const sr = side.getBoundingClientRect();
              const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.25;
              const word = b.querySelector('.sub-nav-status-word');
              const wr = word ? word.getBoundingClientRect() : null;
              const hitAt = (rect) => { const e = document.elementFromPoint(rect.left + rect.width/2, rect.top + rect.height/2); return !!(e && b.contains(e)); };
              // does every line of the label fit? scrollWidth>clientWidth = clipped by overflow
              return {found:true, text: lab.textContent.trim(), sw: lab.scrollWidth, cw: lab.clientWidth,
                textOverflow: cs.textOverflow, overflow: cs.overflow, lines: Math.round(r.height / lh),
                labInsideBtn: r.left >= br.left - 0.5 && r.right <= br.right + 0.5,
                btnInsideSide: br.left >= sr.left - 0.5 && br.right <= sr.right + 0.5,
                wordText: word ? word.textContent.trim() : null,
                wordVisible: !!wr && wr.width > 0 && hitAt(wr),
                wordRight: wr ? wr.right : null, btnRight: br.right, wordLeft: wr ? wr.left : null, labRight: r.right, labLeft: r.left,
                dir: getComputedStyle(b).direction};
            }"""

            def f7_label_readable():
                for lang in ("en", "ar"):
                    c, p = open_page(browser, backend.base_url, VIEWPORT, lang=lang)
                    try:
                        for th in THEMES:
                            p.evaluate("(t) => ThemeEngine.apply(t)", th)
                            p.wait_for_timeout(250)
                            m = p.evaluate(F7_JS)
                            tag = f"[{lang}/{th}]"
                            assert m["found"], f"{tag} no Connected group header at all"
                            want = "Connected services" if lang == "en" else "الخدمات المتصلة"
                            assert m["text"] == want or want in m["text"], f"{tag} label text is {m['text']!r}, want {want!r}"
                            assert m["textOverflow"] != "ellipsis", f"{tag} label still uses text-overflow:ellipsis"
                            assert m["sw"] <= m["cw"] + 1, f"{tag} label is clipped: scrollWidth {m['sw']} > clientWidth {m['cw']}"
                            assert m["lines"] <= 2, f"{tag} label takes {m['lines']} lines (>2)"
                            assert m["labInsideBtn"] and m["btnInsideSide"], f"{tag} label/header spills outside its box: {m}"
                            assert m["wordVisible"], f"{tag} the status word {m['wordText']!r} is not visible/hit at 1366"
                            if lang == "en":
                                assert m["labRight"] <= m["wordLeft"] + 1, f"{tag} label overlaps the status word: {m}"
                            else:
                                assert m["labLeft"] >= m["wordRight"] - 1, f"{tag} (RTL) label overlaps the status word: {m}"
                            if th == "sand":
                                p.screenshot(path=str(shots / f"f7_connected_{lang}_{th}.png"), clip={"x": 0, "y": 60, "width": 320, "height": 700})
                    finally:
                        c.close()

            check("F7 Connected header label fully readable at 1366, 5 themes x EN/AR", f7_label_readable)

            # =================================================================
            # F8 -- Arabic status words
            # =================================================================
            def f8_words():
                c, p = open_page(browser, backend.base_url, VIEWPORT, lang="ar")
                try:
                    got = p.evaluate("() => Object.fromEntries(['off','ok','attention','failing','unknown','checking'].map(s => [s, SubsystemApp._connectedStatusWord(s)]))")
                    want = {"off": AR_OVERLAY["Off (status)"], "ok": AR_OVERLAY["Connected (status)"],
                            "attention": AR_OVERLAY["Needs attention (status)"], "failing": AR_OVERLAY["Failing (status)"],
                            "unknown": AR_OVERLAY["Can't check (status)"], "checking": AR_OVERLAY["Checking… (status)"]}
                    assert got == want, f"AR status words {got} != {want}"
                    assert got["off"] != BARE_VERB_OFF, "AR Off still reads as the verb 'stop'"
                    # ...and what is PAINTED, not only what the helper returns
                    painted = p.evaluate("() => [...document.querySelectorAll('.sub-nav-status-word')].map(e => e.textContent.trim())")
                    assert painted, "no painted status words at all"
                    assert BARE_VERB_OFF not in painted, f"the verb 'stop' is painted on screen: {painted}"
                    assert AR_OVERLAY["Off (status)"] in painted, f"'{AR_OVERLAY['Off (status)']}' not painted; painted={painted}"
                finally:
                    c.close()
                c, p = open_page(browser, backend.base_url, VIEWPORT, lang="en")
                try:
                    en = p.evaluate("() => Object.fromEntries(['off','ok','attention','failing','unknown','checking'].map(s => [s, SubsystemApp._connectedStatusWord(s)]))")
                    assert en == {"off": "Off", "ok": "Connected", "attention": "Needs attention", "failing": "Failing",
                                  "unknown": "Can't check", "checking": "Checking…"}, f"English words changed: {en}"
                finally:
                    c.close()

            check("F8 Arabic Off is 'متوقّف' (not the verb) and painted; all 6 words; English unchanged", f8_words)

            def f8_old_catalogue_degrades():
                """Without the merged keys the shell must fall back to the old
                catalogue words, never print a raw 'Off (status)'."""
                c, p = open_page(browser, backend.base_url, VIEWPORT, lang="ar", overlay_ar=False)
                try:
                    painted = p.evaluate("() => [...document.querySelectorAll('.sub-nav-status-word')].map(e => e.textContent.trim())")
                    assert painted and not any("(status)" in w for w in painted), f"raw context key leaked: {painted}"
                finally:
                    c.close()

            check("F8 an Arabic catalogue without the new keys never shows '(status)'", f8_old_catalogue_degrades)

            # =================================================================
            # JOINED COMPUTER (F5 disabled + explain). Restart technique.
            # =================================================================
            fake = str(uuid.uuid4())
            backend.stop()
            flip_admin_device(data_dir, company_id, fake_id=fake)
            backend.start()

            def joined_sidebar():
                c, p = open_page(browser, backend.base_url, VIEWPORT)
                try:
                    assert state_of(p)["admin"] is False, "restart did not produce a non-main computer"
                    open_group_of(p, "audit-log")
                    info = p.evaluate("""() => { const e = document.querySelector('.sub-nav-item[data-section="audit-log"]');
                        const h = e.querySelector('.sub-nav-hint'); const hr = h.getBoundingClientRect();
                        const c = document.elementFromPoint(hr.left + hr.width/2, hr.top + hr.height/2);
                        return {cls: e.className, dis: e.getAttribute('aria-disabled'), label: e.querySelector('.sub-nav-label').innerText,
                                hint: h.innerText, hintHit: !!(c && e.contains(c)), hintW: hr.width, hintH: hr.height, hintClipped: h.scrollWidth > h.clientWidth + 1,
                                aria: e.getAttribute('aria-label')}; }""")
                    assert "is-main-only" in info["cls"] and info["dis"] == "true", f"not shown disabled: {info}"
                    assert info["hint"].strip() == SENTENCE, f"visible sentence wrong: {info['hint']!r}"
                    assert info["hintHit"] and info["hintW"] > 0 and not info["hintClipped"], f"sentence not readable on the row: {info}"
                    assert SENTENCE in info["aria"] and "Audit Log" in info["aria"], f"aria-label lacks name+reason: {info['aria']!r}"
                    before = state_of(p)
                    human_click(p, '.sub-nav-item[data-section="audit-log"]', "Audit Log (joined)")
                    p.wait_for_timeout(600)
                    assert any(SENTENCE in t for t in toast_texts(p)), f"click gave no explaining toast: {toast_texts(p)}"
                    after = state_of(p)
                    assert after["section"] == before["section"] and after["hash"] == before["hash"], \
                        f"click on a disabled item NAVIGATED: {before} -> {after}"
                    p.screenshot(path=str(shots / "f5_joined_sidebar.png"))
                finally:
                    c.close()

            check("F5 joined computer sidebar: Audit Log disabled, sentence visible, click explains, no navigation", joined_sidebar)

            def joined_deeplink():
                c, p = open_page(browser, backend.base_url, VIEWPORT)
                try:
                    p.evaluate("() => SubsystemApp._navigate('audit-log')")
                    p.wait_for_timeout(1200)
                    st = state_of(p)
                    assert st["section"] != "audit-log", f"deep link opened the device-bound screen on a joined computer: {st}"
                    assert any(SENTENCE in t for t in toast_texts(p)), f"deep link gave no explanation: {toast_texts(p)}"
                    body = p.evaluate("() => (document.getElementById('sub-content')||document.body).innerText.slice(0,400)")
                    assert "Audit Log" not in body.split("\n")[0], f"Audit Log screen rendered: {body!r}"
                finally:
                    c.close()

            check("F5 joined computer: deep link _navigate('audit-log') is redirected home with the explanation", joined_deeplink)

            def joined_phone_more_sheet():
                c, p = open_page(browser, backend.base_url, {"width": 390, "height": 844}, coarse=True)
                try:
                    human_click(p, "#sub-tab-more", "More tab")
                    p.wait_for_timeout(500)
                    row_sel = '.sub-more-row[data-section="audit-log"]'
                    human_click_probe = probe(p, row_sel)
                    assert human_click_probe["found"], "Audit Log row absent from the More sheet on a joined computer"
                    p.evaluate("() => document.querySelector('.sub-more-row[data-section=\"audit-log\"]').scrollIntoView({block:'nearest'})")
                    p.wait_for_timeout(120)
                    info = p.evaluate("""() => { const e = document.querySelector('.sub-more-row[data-section="audit-log"]');
                        const h = e.querySelector('.sub-more-row-hint'); const hr = h.getBoundingClientRect();
                        return {cls: e.className, dis: e.getAttribute('aria-disabled'), hint: h.innerText.trim(), hintInside: hr.left >= 0 && hr.right <= innerWidth && hr.top >= 0 && hr.bottom <= innerHeight,
                                clipped: h.scrollWidth > h.clientWidth + 1}; }""")
                    assert "is-main-only" in info["cls"] and info["dis"] == "true", f"More row not disabled: {info}"
                    assert info["hint"] == SENTENCE and info["hintInside"] and not info["clipped"], f"sentence not readable in the sheet: {info}"
                    before = state_of(p)
                    human_click(p, row_sel, "Audit Log More row", scroll_list=True)
                    p.wait_for_timeout(500)
                    assert any(SENTENCE in t for t in toast_texts(p)), f"tap gave no explaining toast: {toast_texts(p)}"
                    assert state_of(p)["section"] == before["section"], "tap navigated"
                    assert p.evaluate("() => !!document.getElementById('sub-more-sheet')"), "sheet closed on a refused tap (person is still choosing)"
                    p.screenshot(path=str(shots / "f5_joined_more_sheet.png"))
                finally:
                    c.close()

            check("F5 joined computer, phone More sheet: row disabled, sentence visible, tap explains, sheet stays", joined_phone_more_sheet)

            def joined_ar():
                c, p = open_page(browser, backend.base_url, VIEWPORT, lang="ar")
                try:
                    open_group_of(p, "audit-log")
                    hint = p.evaluate("() => document.querySelector('.sub-nav-item[data-section=\"audit-log\"] .sub-nav-hint').innerText.trim()")
                    assert hint == AR_OVERLAY[SENTENCE], f"AR sentence not shown on the row: {hint!r}"
                    m = probe(p, '.sub-nav-item[data-section="audit-log"]')
                    assert m["inside"] and m["hit"], f"AR disabled row not reachable: {m}"
                finally:
                    c.close()

            check("F5 joined computer (Arabic): sentence translated on the row", joined_ar)

            def cashier_sees_nothing():
                c, p = open_page(browser, backend.base_url, VIEWPORT, email=cashier_email, password=cashier_pw)
                try:
                    n = p.evaluate("() => document.querySelectorAll('[data-section=\"audit-log\"]').length")
                    t = p.evaluate("() => document.body.innerText.includes(%s)" % json.dumps(SENTENCE))
                    assert n == 0 and not t, f"a cashier sees the device-bound entry ({n}) or the sentence ({t})"
                finally:
                    c.close()

            check("F5 a cashier on a joined computer sees NO Audit Log entry and no sentence", cashier_sees_nothing)

            # =================================================================
            # UNCLAIMED computer
            # =================================================================
            backend.stop()
            os.environ["AURA_APP_DATA"] = str(data_dir)
            from commercial_runtime.identity.registry_db import get_conn  # noqa: E402
            rc = get_conn()
            rc.execute("DELETE FROM devices WHERE id=?", (fake,))
            rc.commit()
            rc.close()
            backend.start()

            def unclaimed():
                c, p = open_page(browser, backend.base_url, VIEWPORT)
                try:
                    st = state_of(p)
                    assert st["admin"] is False, f"expected an unclaimed (non-main) computer: {st}"
                    open_group_of(p, "audit-log")
                    cls = p.evaluate("() => document.querySelector('.sub-nav-item[data-section=\"audit-log\"]').className")
                    assert "is-main-only" in cls, f"unclaimed computer: Audit Log not shown disabled ({cls})"
                    human_click(p, '.sub-nav-item[data-section="audit-log"]', "Audit Log (unclaimed)")
                    p.wait_for_timeout(500)
                    assert state_of(p)["section"] != "audit-log", "unclaimed computer navigated to the device-bound screen"
                finally:
                    c.close()

            check("F5 unclaimed computer: same disabled + explain behaviour", unclaimed)
        finally:
            browser.close()
            backend.stop()
            shutil.rmtree(data_dir, ignore_errors=True)

    print("\n" + "=" * 78)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail else ""))
    print(f"\n{passed}/{len(RESULTS)} checks passed.")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
