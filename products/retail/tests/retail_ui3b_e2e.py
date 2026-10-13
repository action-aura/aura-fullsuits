"""UI-3b -- one map (Reports & Books tabs, gear -> hub, lean identity menu), remembered section,
till rail labels, header, and dialogs below a top banner -- driven in a REAL browser.

Boots the real Flask backend (retail_p1c_e2e_boot.py's recipe), signs up the owner, seeds a catalogue,
makes a real cashier, then drives headless Chromium EN + AR at 1366x768 and 1024x768 and LOOKS at
screenshots in $AURA_E2E_SHOTS (ui3b_*.png).

Run with the SYSTEM Python (Playwright lives there):
    py -3.14 products/retail/tests/retail_ui3b_e2e.py [map|remember|rail|banner ...]

Mutants (each must turn exactly its own checks red; served in memory, the files on disk are untouched):
    P1C_MUTATE=ui3b_overlay_inset   dialogs ignore the banner inset      -> 'banner' items fail
    P1C_MUTATE=ui3b_ai_top          AI panel ignores the banner          -> 'banner' AI items fail
    P1C_MUTATE=ui3b_help_cap        Help dialog cap ignores the banner   -> 'banner' Help items fail at 1024
    P1C_MUTATE=ui3b_rail_64         rail back to 64px                    -> 'rail' items fail
    P1C_MUTATE=ui3b_tabs_never      the Reports & Books bar never shows  -> 'map' items fail
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retail_p1c_e2e_boot as B  # noqa: E402

from playwright.sync_api import sync_playwright  # noqa: E402

SIZES = [(1366, 768), (1024, 768)]
OWNER = ("owner@p1c.local", "P1cTillTest12345!")
CASHIER = ("cashier@ui3b.local", "Ui3bCashierPW1!")
TABS = ["reports", "trial-balance", "profit-and-loss", "balance-sheet", "cash-flow", "expenses"]


def shot(page, name):
    page.screenshot(path=str(B.SHOTS / f"ui3b_{name}.png"))


def set_lang(page, lang):
    page.evaluate("(l) => AuraI18n.setLang(l)", lang)
    page.wait_for_timeout(500)


def set_size(page, w, h):
    page.set_viewport_size({"width": w, "height": h})
    page.wait_for_timeout(250)


def make_cashier(email, password):
    import uuid
    from commercial_runtime.identity import user_accounts
    from commercial_runtime.identity.registry_db import get_conn
    from commercial_runtime.security.passwords import hash_password
    conn = get_conn()
    if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        conn.close()
        return
    company_id = conn.execute("SELECT company_id FROM users WHERE role='admin' LIMIT 1").fetchone()[0]
    uid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (uid, company_id, "EMP-0098", email, hash_password(password), "cashier", "active"))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), uid, "retail", "full"))
    user_accounts.seed_capabilities_for_user(conn, uid, "cashier")
    conn.commit(); conn.close()


def login_as(page, creds):
    status, body = B.api(page, "POST", "/api/auth/login", {"email": creds[0], "password": creds[1]})
    assert status == 200, f"login failed {status} {body}"
    page.reload(); B.dismiss_intro_overlay(page)
    page.wait_for_selector("#sub-header-section", timeout=20000)
    page.wait_for_timeout(900)


def section(page):
    return page.evaluate("() => SubsystemApp.currentSection")


def tab_ids(page):
    return page.evaluate("() => [...document.querySelectorAll('#sub-section-tabs [data-tab-section]')].map(b => b.dataset.tabSection)")


def active_tab(page):
    return page.evaluate("() => { const b = document.querySelector('#sub-section-tabs .is-active'); return b ? b.dataset.tabSection : null; }")


def settled(page, ms=1200):
    page.wait_for_function("() => !document.querySelector('#sub-content .sub-loading')", timeout=15000)
    page.wait_for_timeout(ms)


# ═════════════════════════════════ item 1 + 4: one map, header ═════════════════════════════════
def item_map(page, ids):
    for lang in ("en", "ar"):
        for (w, h) in SIZES:
            set_size(page, w, h); set_lang(page, lang)
            tag = f"{w}x{h} {lang}"

            def sidebar_and_tabs():
                B.go(page, "dashboard")
                page.evaluate("() => SubsystemApp._ensureSidebarGroupOpen('Reports')")
                page.wait_for_timeout(250)
                groups = page.evaluate("() => [...document.querySelectorAll('.sub-nav-group-label-text')].map(e => e.textContent.trim())")
                assert not any(g in ("Books", "الدفاتر") for g in groups), f"a Books GROUP is still in the sidebar: {groups}"
                row = page.locator('.sub-nav-item[data-section="reports"]')
                assert row.count() == 1, "no single 'Reports & Books' row"
                label = row.inner_text().strip()
                want = "Reports & Books" if lang == "en" else "التقارير والدفاتر"
                assert label == want, f"row says {label!r}, expected {want!r}"
                assert page.locator('.sub-nav-item[data-section="trial-balance"]').count() == 0, "Trial Balance still has its own row"
                shot(page, f"map_01_sidebar_{w}x{h}_{lang}")
                row.click()
                settled(page)
                assert section(page) == "reports", section(page)
                assert tab_ids(page) == TABS, f"tabs {tab_ids(page)}"
                assert active_tab(page) == "reports"
                assert page.locator('.sub-nav-item.active[data-section="reports"]').count() == 1, "the row is not highlighted"
                shot(page, f"map_02_tabs_reports_{w}x{h}_{lang}")
                for tid in TABS[1:]:
                    page.locator(f'#sub-section-tabs [data-tab-section="{tid}"]').click()
                    settled(page, 900)
                    assert section(page) == tid and active_tab(page) == tid, (tid, section(page), active_tab(page))
                    assert page.locator('.sub-nav-item.active[data-section="reports"]').count() == 1, f"{tid}: row not highlighted"
                    body = page.inner_text("#sub-content")
                    assert "Failed to load" not in body and "فشل" not in body[:40], f"{tid} failed to load: {body[:120]}"
                    assert page.locator("#sub-content .sub-loading").count() == 0
                    # the bar is still there AFTER the screen painted (it lives outside #sub-content)
                    assert tab_ids(page) == TABS, f"{tid}: the tab bar vanished"
                    if tid in ("trial-balance", "expenses"):
                        shot(page, f"map_03_tab_{tid}_{w}x{h}_{lang}")
                B.go(page, "products")
                assert page.locator("#sub-section-tabs").is_hidden(), "the bar stayed up outside Reports & Books"
            B.check(f"map: {tag}: no Books group; one 'Reports & Books' row; six working tabs; bar hides elsewhere", sidebar_and_tabs)

            def deep_link():
                B.go(page, "cash-flow", 1500)
                assert active_tab(page) == "cash-flow" and tab_ids(page) == TABS
                assert page.locator('.sub-nav-item.active[data-section="reports"]').count() == 1
            B.check(f"map: {tag}: a deep link into a tab (cash-flow) opens that tab under the same row", deep_link)

            def gear_to_hub():
                B.go(page, "dashboard")
                assert page.locator("#sub-settings-menu").count() == 0
                page.click("#sub-settings-gear-btn")
                page.wait_for_selector("#sh-root", timeout=15000)
                page.wait_for_timeout(900)
                assert section(page) == "admin-center"
                secs = page.evaluate("() => [...document.querySelectorAll('#sh-root [data-sh]')].map(b => b.dataset.sh)")
                for s in ("team", "branches", "data-backup", "plan-devices"):
                    assert s in secs, f"hub has no {s!r}: {secs}"
                shot(page, f"map_04_hub_{w}x{h}_{lang}")
                page.locator('#sh-root [data-sh="branches"]').click()
                page.wait_for_selector('[data-sh-pane="branches"] #branch-table', timeout=15000)
                assert page.locator('[data-sh-pane="branches"] [onclick*="_openAddBranch"]').count() >= 1, "no Add Branch in the hub pane"
                shot(page, f"map_05_hub_branches_{w}x{h}_{lang}")
                page.locator('#sh-root [data-sh="plan-devices"]').click()
                page.wait_for_selector('[data-sh-pane="plan-devices"]:not([hidden]) *', timeout=15000)
            B.check(f"map: {tag}: the gear opens the hub in ONE click; Team/Branches/Data/Plan sections work; Add Branch is there", gear_to_hub)

            def identity_and_header():
                B.go(page, "dashboard")
                page.click("#sub-identity-chip-btn")
                page.wait_for_timeout(250)
                items = page.evaluate("() => [...document.querySelectorAll('#sub-identity-menu .sub-identity-menu-item:not([hidden])')].map(e => e.textContent.trim())")
                joined = " | ".join(items)
                assert "Plan" not in joined and "الخطة" not in joined, f"Plan & devices is in the identity menu: {items}"
                assert len(items) >= 3 and len(items) <= 5, items
                shot(page, f"map_06_identity_menu_{w}x{h}_{lang}")
                page.keyboard.press("Escape"); page.wait_for_timeout(200)
                txt = page.inner_text("#aura-lang-toggle").replace("\n", " ").strip()
                assert "EN" in txt and "ع" in txt and "|" in txt, f"language button reads {txt!r}"
                for sel in ("#aura-palette-trigger", "#aura-lang-toggle", ".sub-header-right button[onclick*=openPicker]", "#sub-fullscreen-btn"):
                    el = page.locator(sel)
                    assert el.count() == 1, sel
                    assert (el.get_attribute("aria-label") or "").strip() and (el.get_attribute("title") or "").strip(), f"{sel}: no aria-label/title"
                chip = page.inner_text("#sub-identity-chip-btn")
                assert "Windows" not in chip and "ويندوز" not in chip, f"the chip still says the platform: {chip!r}"
                shot(page, f"map_07_header_{w}x{h}_{lang}")
            B.check(f"map: {tag}: identity menu has no Plan & devices; header 'EN | ع' + labelled buttons; chip has no platform word", identity_and_header)
    set_size(page, 1366, 768); set_lang(page, "en")

    def cashier_view():
        make_cashier(*CASHIER)
        login_as(page, CASHIER)
        for lang in ("en", "ar"):
            set_lang(page, lang)
            assert section(page) == "pos", section(page)
            assert page.locator('.sub-nav-item[data-section="reports"]').count() == 0, "a cashier sees Reports & Books"
            assert page.locator("#sub-settings-gear-btn").count() == 0, "a cashier sees the gear"
            assert page.locator("#sub-section-tabs").is_hidden()
            shot(page, f"map_08_cashier_till_{lang}")
            for tid in ("trial-balance", "expenses"):
                page.evaluate("(s) => SubsystemApp._navigate(s)", tid)
                page.wait_for_timeout(700)
                assert page.locator("#sub-section-tabs").is_hidden(), "the bar showed for a cashier on a deep link"
            B.go(page, "pos")   # leave the deep-linked screen before the next language pass
        login_as(page, OWNER)
    B.check("map: a real CASHIER sees no Reports & Books, no gear, no tab bar (EN + AR), lands on the till", cashier_view)


# ═════════════════════════════════ item 2: remember where I was ═════════════════════════════════
def item_remember(page, ids):
    set_lang(page, "en"); set_size(page, 1366, 768)

    def owner_restores():
        B.go(page, "sales", 1200)
        page.reload(); B.dismiss_intro_overlay(page)
        page.wait_for_selector("#sub-header-section", timeout=20000); page.wait_for_timeout(1200)
        assert section(page) == "sales", f"after a reload the owner landed on {section(page)!r}"
        B.go(page, "cash-flow", 1200)
        page.reload(); B.dismiss_intro_overlay(page)
        page.wait_for_selector("#sub-header-section", timeout=20000); page.wait_for_timeout(1500)
        assert section(page) == "cash-flow" and active_tab(page) == "cash-flow", (section(page), active_tab(page))
        shot(page, "remember_01_cash_flow_after_reload")
    B.check("remember: the owner comes back to Sales, and to a Reports & Books TAB, after a reload", owner_restores)

    def cashier_till():
        make_cashier(*CASHIER)
        login_as(page, CASHIER)
        B.go(page, "customers", 800)
        page.reload(); B.dismiss_intro_overlay(page)
        page.wait_for_selector("#sub-header-section", timeout=20000); page.wait_for_timeout(1200)
        assert section(page) == "pos", f"a sell-only cashier landed on {section(page)!r}, not the till"
        login_as(page, OWNER)
    B.check("remember: a sell-only cashier ALWAYS lands on the till, even after leaving it", cashier_till)

    def storage_blocked():
        page.evaluate("() => { localStorage.removeItem('aura_last_section_' + SubsystemApp.currentUser.id); }")
        page.reload(); B.dismiss_intro_overlay(page)
        page.wait_for_selector("#sub-header-section", timeout=20000); page.wait_for_timeout(1000)
        assert section(page) == "dashboard", section(page)
    B.check("remember: with nothing stored the owner lands on Today", storage_blocked)
    B.go(page, "dashboard")


# ═════════════════════════════════ item 3: till rail labels ═════════════════════════════════
def item_rail(page, ids):
    for lang in ("en", "ar"):
        for (w, h) in SIZES:
            set_size(page, w, h); set_lang(page, lang)
            tag = f"{w}x{h} {lang}"

            def rail():
                B.go_pos(page)
                m = page.evaluate("""() => {
                    const sb = document.getElementById('sub-sidebar');
                    const r = sb.getBoundingClientRect();
                    const labs = [...sb.querySelectorAll('.sub-nav-item .sub-nav-label')].filter(l => l.offsetParent !== null && !l.closest('.sub-settings-menu-wrap'));
                    const bad = labs.filter(l => { const b = l.getBoundingClientRect(); return b.width < 4 || b.height < 4 || b.right > r.right + 1 || b.left < r.left - 1; })
                                    .map(l => l.textContent.trim());
                    const sizes = [...new Set(labs.map(l => getComputedStyle(l).fontSize))];
                    const items = [...sb.querySelectorAll('.sub-nav-item[data-section]')].filter(i => i.offsetParent !== null && !i.closest('.sub-settings-menu-wrap'));
                    const unlabelled = items.filter(i => !(i.querySelector('.sub-nav-label') && i.querySelector('.sub-nav-label').textContent.trim())).map(i => i.dataset.section);
                    const pay = document.querySelector('.pos-checkout-btn');
                    return { width: r.width, n: labs.length, bad, sizes, unlabelled, nItems: items.length,
                             chargeBottom: pay ? pay.getBoundingClientRect().bottom : null, vh: window.innerHeight,
                             scrollH: document.documentElement.scrollHeight };
                }""")
                assert abs(m["width"] - 96) <= 1, f"rail width {m['width']}"
                assert m["n"] >= 5 and m["n"] >= m["nItems"] - 1, f"only {m['n']} of {m['nItems']} rail items show a label"
                assert not m["bad"], f"labels clipped or outside the rail: {m['bad']}"
                assert not m["unlabelled"], f"rail items with no label: {m['unlabelled']}"
                assert m["sizes"] == ["10.5px"], f"label sizes {m['sizes']}"
                assert m["chargeBottom"] is not None and m["chargeBottom"] <= m["vh"], f"Charge bottom {m['chargeBottom']} > viewport {m['vh']}"
                assert m["scrollH"] <= m["vh"] + 1, f"the page scrolls: {m['scrollH']} > {m['vh']}"
                shot(page, f"rail_01_{w}x{h}_{lang}")
            B.check(f"rail: {tag}: 96px rail, every icon has a 10.5px label inside it, Charge still on screen, no page scroll", rail)
    set_size(page, 1366, 768); set_lang(page, "en")


# ═════════════════════════════════ item 5: dialogs below a top banner ═════════════════════════════════
def item_banner(page, ids):
    page.evaluate("""() => {
        const st = SubsystemApp.LICENSE_BANNER_BLOCKED_STATES[0];
        SubsystemApp._renderLicenseBanner(st);
    }""")
    page.wait_for_timeout(400)
    for lang in ("en", "ar"):
        for (w, h) in SIZES:
            set_size(page, w, h); set_lang(page, lang)
            page.evaluate("() => SubsystemApp._renderLicenseBanner(SubsystemApp.LICENSE_BANNER_BLOCKED_STATES[0])")
            page.wait_for_timeout(400)
            tag = f"{w}x{h} {lang}"
            B.go(page, "dashboard")

            def help_below_banner():
                bottom = page.evaluate("() => document.getElementById('aura-license-banner').getBoundingClientRect().bottom")
                assert bottom > 20, f"no licence banner on screen (bottom {bottom})"
                page.evaluate("() => AuraHelp.open()")
                page.wait_for_selector(".ret-modal-overlay", timeout=5000)
                page.wait_for_timeout(500)
                m = page.evaluate("""() => {
                    const ov = document.querySelector('.ret-modal-overlay');
                    const box = ov.firstElementChild;
                    const head = ov.querySelector('h1,h2,h3,[role=heading],.ret-modal-title,.ret-modal-header');
                    return { ovTop: ov.getBoundingClientRect().top, boxTop: box.getBoundingClientRect().top,
                             headTop: head ? head.getBoundingClientRect().top : null,
                             banner: document.getElementById('aura-license-banner').getBoundingClientRect().bottom };
                }""")
                assert m["ovTop"] >= m["banner"] - 1, f"the dialog overlay starts at {m['ovTop']}, under the banner (bottom {m['banner']})"
                assert m["boxTop"] >= m["banner"] - 1, f"the dialog box starts at {m['boxTop']}, under the banner (bottom {m['banner']})"
                if m["headTop"] is not None:
                    assert m["headTop"] >= m["banner"] - 1, f"the dialog title is at {m['headTop']}, under the banner (bottom {m['banner']})"
                shot(page, f"banner_01_help_{w}x{h}_{lang}")
                page.keyboard.press("Escape"); page.wait_for_timeout(300)
                page.evaluate("() => document.querySelectorAll('.ret-modal-overlay').forEach(o => o.remove())")
            B.check(f"banner: {tag}: the Help dialog (box + title) starts below the licence banner", help_below_banner)

            def palette_below_banner():
                page.evaluate("() => AuraPalette.open()")
                page.wait_for_selector(".aura-palette-overlay", timeout=5000)
                page.wait_for_timeout(300)
                # The palette is deliberately NOT moved below the banner (its scrim dims the bar -- the
                # palette_fix contract); what must hold is that its FIELD clears the banner and is what is hit.
                m = page.evaluate("""() => { const i = document.getElementById('aura-palette-input'), r = i.getBoundingClientRect();
                    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                    return { top: r.top, hit: hit === i, banner: document.getElementById('aura-license-banner').getBoundingClientRect().bottom }; }""")
                assert m["top"] >= m["banner"] - 1 and m["hit"], f"palette field at {m['top']} under banner {m['banner']} (hit={m['hit']})"
                shot(page, f"banner_02_palette_{w}x{h}_{lang}")
                page.keyboard.press("Escape"); page.wait_for_timeout(300)
            B.check(f"banner: {tag}: the command palette field still clears the licence banner and is what a click hits", palette_below_banner)

            def ai_panel_below_banner():
                # A closed panel is display:none (its rect is all zeros), so it must be OPEN to be measured.
                page.evaluate("() => { try { SubAI.open('retail'); } catch (e) {} }")
                page.wait_for_function("() => { const p = document.getElementById('sub-ai-panel'); return p && p.getBoundingClientRect().height > 100; }", timeout=5000)
                page.wait_for_timeout(500)
                m = page.evaluate("""() => { const p = document.getElementById('sub-ai-panel'); if (!p) return null;
                    const cs = getComputedStyle(p);
                    return { top: p.getBoundingClientRect().top, csTop: cs.top, pos: cs.position, parent: p.parentElement.tagName + '#' + p.parentElement.id,
                             inset: getComputedStyle(document.documentElement).getPropertyValue('--top-banner-inset'),
                             banner: document.getElementById('aura-license-banner').getBoundingClientRect().bottom }; }""")
                assert m is not None, "the AI panel never rendered (SubAI.open did nothing)"
                assert m["top"] >= m["banner"] - 1, f"AI panel top {m['top']} under banner {m['banner']} ({m})"
                shot(page, f"banner_03_ai_{w}x{h}_{lang}")
                page.evaluate("() => { try { SubAI.close(); } catch (e) {} }")
            B.check(f"banner: {tag}: the AI panel starts below the licence banner", ai_panel_below_banner)
    page.evaluate("() => SubsystemApp._renderLicenseBanner('ACTIVE')")
    page.wait_for_timeout(300)
    set_size(page, 1366, 768); set_lang(page, "en")


ITEMS = {"map": item_map, "remember": item_remember, "rail": item_rail, "banner": item_banner}


def main():
    wanted = [a for a in sys.argv[1:] if a in ITEMS] or list(ITEMS)
    with sync_playwright() as p:
        browser, ctx, page = B.start_shop(p, viewport={"width": 1366, "height": 768})
        ids = B.seed_catalogue(page)
        for k in wanted:
            ITEMS[k](page, ids)
        browser.close()
    B.finish()


if __name__ == "__main__":
    main()
