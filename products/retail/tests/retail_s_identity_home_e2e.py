"""
retail_s_identity_home_e2e.py -- the identity chip (name . role . branch) and the
Accountant's read-only home, in a REAL browser. STANDALONE SCRIPT, NOT PYTEST.

    C:/Users/MSI/AppData/Local/Python/pythoncore-3.14-64/python.exe ^
        products/retail/tests/retail_s_identity_home_e2e.py [--only tag,tag]

Tags: chip home phone look books

  chip   every seeded person (owner, cashier, manager, branch manager,
         accountant) shows their REAL name, their preset label (Accountant /
         Branch manager -- not the base role "Manager") and the chip's title reads
         "<name> . <role> . <branch>". An invite with no name falls back to the
         email local part. The owner stays "Owner" even if a preset string is
         stray on the page's user record.
  home   a books-only person (accountant) lands on the books home: note + cards,
         NO Point of Sale row, and asking for #pos lands on that home. A cashier
         still lands on the till, a manager on the dashboard with the till row
         (so the predicate cannot be "everybody"). Capabilities not yet known
         (null) never turn anyone into books-only.
  phone  narrow window: the accountant's tab bar has no Till tab; a manager's does.
  look   Arabic/RTL, five themes, 1366x768 and 1024x768: books home fits, no
         sideways scroll, cards reachable. Screenshots go to SHOTS.
  books  UI-verifier item 9. The Accountant's header says Books (not Dashboard), the
         sidebar offers no Returns / Barcode Scanner row (no refund / sell grant) while
         a manager and a cashier still get them, the identity chip never prints the raw
         platform word ("WINDOWS"), and the 60 s dashboard timer is never armed on the
         books home (it re-rendered the page and dropped the focused card) yet IS armed
         for a manager's dashboard, and stops firing if the person becomes books-only.
"""
from __future__ import annotations

import socket
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import retail_s_users_roles_e2e as ur  # noqa: E402
from retail_p1a_shell_e2e import DEFAULT_WAIT_MS, VIEWPORT, Backend  # noqa: E402
from retail_p1b_palette_e2e import check  # noqa: E402
from retail_p1b_palette_e2e import open_page as _open_page  # noqa: E402
import retail_p1b_palette_e2e as _pe  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SMALL = ur.SMALL
PHONE = {"width": 390, "height": 844}
THEMES = ur.THEMES
SHOTS = ur.SHOTS
PEOPLE = {
    # email: (name, role label the chip must show)
    "ui-cashier@usersroles.test": ("Cara Cashier", "Cashier"),
    "ui-manager@usersroles.test": ("Mo Manager", "Manager"),
    "ui-accountant@usersroles.test": ("Amal Accountant", "Accountant"),
    "bm@usersroles.test": ("Bassam BM", "Branch manager"),
}
ACCOUNTANT = "ui-accountant@usersroles.test"
CASHIER = "ui-cashier@usersroles.test"
MANAGER = "ui-manager@usersroles.test"
NONAME = "noname@usersroles.test"


def person_page(browser, base, email, *, viewport=VIEWPORT, lang="en", theme=None):
    ctx, page = _open_page(browser, base, email=email, password=ur.PASSWORDS[email],
                           viewport=viewport, lang=lang, theme=theme)
    return ctx, page


def chip(page):
    page.wait_for_selector(".sub-identity-name", timeout=15000)
    return page.evaluate("""() => ({
        name: document.querySelector('.sub-identity-name').textContent.trim(),
        role: (document.querySelector('.sub-role-pill[data-identity-role]') || {textContent: null}).textContent,
        title: document.getElementById('sub-identity-chip-btn').getAttribute('title'),
        place: (document.querySelector('.sub-identity-place') || {textContent: ''}).textContent.trim()})""")


def wait_landed(page):
    page.wait_for_function(
        "() => !!document.querySelector('.bkh-grid') || !!document.querySelector('#sub-content .ret-title, "
        "#sub-content .pos-wrap, #sub-content .ret-hdr, #sub-content [class*=pos]')", timeout=20000)
    page.wait_for_timeout(500)


def has_pos_nav(page):
    return page.evaluate("() => !!document.querySelector('.sub-nav-item[data-section=\"pos\"], "
                         "[data-section=\"pos\"]')")


# ------------------------------------------------------------------------ cases
def case_chip(browser, base):
    for email, (name, role) in PEOPLE.items():
        ctx, page = person_page(browser, base, email)
        try:
            c = chip(page)
            assert c["name"] == name, f"{email}: chip name {c['name']!r}, want {name!r}"
            assert c["role"] == role, f"{email}: chip role {c['role']!r}, want {role!r}"
            assert c["title"].startswith(f"{name} \u00b7 {role}"), f"{email}: title {c['title']!r}"
            if c["place"]:
                assert c["title"].endswith(c["place"]), f"{email}: title lost the place {c['title']!r} / {c['place']!r}"
        finally:
            ctx.close()
    # owner
    ctx, page = ur.owner_page(browser, base)
    try:
        c = chip(page)
        assert c["role"] == "Owner", f"owner role {c['role']!r}"
        assert "Owner" in c["title"], f"owner title {c['title']!r}"
        # a stray preset string on the owner's record must not relabel the owner
        stray = page.evaluate("""() => { SubsystemApp.currentUser = Object.assign({}, SubsystemApp.currentUser,
            {role_preset: 'accountant'}); return SubsystemApp._identityRoleLabel(); }""")
        assert stray == "Owner", f"stray preset relabelled the owner as {stray!r}"
        # unknown preset on a non-owner falls back to the plain role, never a raw code
        odd = page.evaluate("""() => { const r = SubsystemApp.role; SubsystemApp.role = 'manager';
            SubsystemApp.currentUser = Object.assign({}, SubsystemApp.currentUser, {role_preset: 'zzz_unknown'});
            const v = SubsystemApp._identityRoleLabel(); SubsystemApp.role = r; return v; }""")
        assert odd == "Manager", f"unknown preset printed {odd!r}"
    finally:
        ctx.close()
    # no name typed: falls back to the email local part
    ctx, page = person_page(browser, base, NONAME)
    try:
        c = chip(page)
        assert "noname" in c["name"].lower(), f"unnamed person's chip shows {c['name']!r}"
    finally:
        ctx.close()


def case_home(browser, base):
    ctx, page = person_page(browser, base, ACCOUNTANT)
    try:
        page.wait_for_selector(".bkh-grid", timeout=20000)
        assert page.locator("[data-books-note]").count() == 1, "no books note"
        cards = page.evaluate("() => [...document.querySelectorAll('[data-books-card]')].map(b => b.dataset.booksCard)")
        assert cards and set(cards) <= {"trial-balance", "profit-and-loss", "balance-sheet", "cash-flow",
                                        "receivables", "payables", "reports"}, f"cards {cards}"
        assert "trial-balance" in cards, f"the accountant's own books missing: {cards}"
        assert not has_pos_nav(page), "the accountant is still offered the Point of Sale row"
        # every card leads somewhere this person can open (nav-visible)
        vis = page.evaluate("""() => { const sys = SubsystemApp.systems[SubsystemApp.active];
            return [...document.querySelectorAll('[data-books-card]')].every(b =>
              SubsystemApp._isNavItemVisible(sys.nav.find(i => i.id === b.dataset.booksCard))); }""")
        assert vis, "a card points at a screen the person may not open"
        # with only the bare books.view grant the home offers exactly the screens that grant
        # opens -- worked out here from the nav table's own capability field, not from the page's helper
        fewer = page.evaluate("""() => {
            const keep = SubsystemApp.capabilities; SubsystemApp.capabilities = ['retail.books.view'];
            try {
              SubsystemApp._renderBooksHome();
              const sys = SubsystemApp.systems[SubsystemApp.active];
              const want = SubsystemApp._BOOKS_HOME_CARDS.filter(id => {
                const it = sys.nav.find(i => i.id === id);
                return it && (!it.capability || SubsystemApp.capabilities.includes(it.capability)); });
              const got = [...document.querySelectorAll('[data-books-card]')].map(b => b.dataset.booksCard);
              return {want, got};
            } finally { SubsystemApp.capabilities = keep; SubsystemApp._renderBooksHome(); }
        }""")
        assert sorted(fewer["want"]) == sorted(fewer["got"]), f"books home offered {fewer['got']}, grant opens {fewer['want']}"
        assert len(fewer["got"]) < len(cards), \
            f"bare books.view still shows every card ({fewer['got']} vs {cards}): the visibility filter is untested"
        # a card really navigates
        page.click('[data-books-card="trial-balance"]')
        page.wait_for_function("() => SubsystemApp.currentSection === 'trial-balance'", timeout=8000)
        # a saved #pos / stale bookmark lands on the books home, not the till
        page.evaluate("() => SubsystemApp._navigate('pos')")
        page.wait_for_selector(".bkh-grid", timeout=8000)
        cur = page.evaluate("() => SubsystemApp.currentSection")
        assert cur == "dashboard", f"asked for pos, landed on {cur!r}"
        ur.shot(page, "home-accountant")
    finally:
        ctx.close()
    # cashier: still the till, no books home
    ctx, page = person_page(browser, base, CASHIER)
    try:
        wait_landed(page)
        assert page.locator(".bkh-grid").count() == 0, "cashier got the books home"
        assert has_pos_nav(page), "cashier lost the Point of Sale row"
        assert not page.evaluate("() => SubsystemApp._isBooksOnlyCapabilities()"), "cashier is books-only"
    finally:
        ctx.close()
    # manager: dashboard + till row, no books home
    ctx, page = person_page(browser, base, MANAGER)
    try:
        wait_landed(page)
        assert page.locator(".bkh-grid").count() == 0, "manager got the books home"
        assert has_pos_nav(page), "manager lost the Point of Sale row"
    finally:
        ctx.close()
    # owner: never books-only, even with unknown or books-only capability lists
    ctx, page = ur.owner_page(browser, base)
    try:
        wait_landed(page)
        assert has_pos_nav(page), "owner lost the Point of Sale row"
        res = page.evaluate("""() => { const c = SubsystemApp.capabilities;
            SubsystemApp.capabilities = ['retail.books.view'];
            const a = SubsystemApp._isBooksOnlyCapabilities();
            SubsystemApp.capabilities = c; return a; }""")
        assert res is False, "owner treated as books-only"
    finally:
        ctx.close()
    # unknown grants fail CLOSED to "not books-only" for a non-owner
    ctx, page = person_page(browser, base, MANAGER)
    try:
        res = page.evaluate("""() => { const c = SubsystemApp.capabilities; SubsystemApp.capabilities = null;
            const a = SubsystemApp._isBooksOnlyCapabilities();
            SubsystemApp.capabilities = ['retail.books.view', 'retail.sell'];
            const b = SubsystemApp._isBooksOnlyCapabilities();
            SubsystemApp.capabilities = ['retail.books.view', 'retail.cash.close'];
            const d = SubsystemApp._isBooksOnlyCapabilities();
            SubsystemApp.capabilities = ['retail.reports'];
            const e = SubsystemApp._isBooksOnlyCapabilities();
            SubsystemApp.capabilities = c; return [a, b, d, e]; }""")
        assert res == [False, False, False, False], f"predicate edge cases {res}"
    finally:
        ctx.close()


def case_phone(browser, base):
    ctx, page = person_page(browser, base, ACCOUNTANT, viewport=PHONE)
    try:
        page.wait_for_selector(".bkh-grid", timeout=20000)
        assert page.locator("#sub-tabbar .sub-tab-till").count() == 0, "accountant's phone bar has a Till tab"
        assert page.locator("#sub-tabbar .sub-tab").count() >= 4, "no phone tab bar at all (test is vacuous)"
        assert not page.evaluate("() => document.documentElement.scrollWidth > innerWidth + 1"), "phone: sideways scroll"
        ur.shot(page, "phone-accountant")
    finally:
        ctx.close()
    ctx, page = person_page(browser, base, MANAGER, viewport=PHONE)
    try:
        page.wait_for_selector("#sub-tabbar .sub-tab", timeout=20000)
        assert page.locator("#sub-tabbar .sub-tab-till").count() == 1, "manager's phone bar lost the Till tab"
    finally:
        ctx.close()


def case_look(browser, base):
    for vp_name, vp in (("1366", VIEWPORT), ("1024", SMALL)):
        for lang in ("en", "ar"):
            ctx, page = person_page(browser, base, ACCOUNTANT, viewport=vp, lang=lang)
            try:
                ur.go_lang(page, lang)
                page.evaluate("() => SubsystemApp._navigate('dashboard')")
                page.wait_for_selector(".bkh-grid", timeout=8000)
                page.wait_for_timeout(300)
                assert page.evaluate("() => document.documentElement.dir") == ("rtl" if lang == "ar" else "ltr")
                assert not page.evaluate("() => document.documentElement.scrollWidth > innerWidth + 1"), \
                    f"{vp_name}/{lang}: books home scrolls sideways"
                ok = page.evaluate("""() => [...document.querySelectorAll('.bkh-card')].every(b => {
                    const r = b.getBoundingClientRect(); const t = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                    return r.left >= 0 && r.right <= innerWidth && !!t && (t === b || b.contains(t)); })""")
                assert ok, f"{vp_name}/{lang}: a card is outside the window or covered"
                if lang == "ar":
                    note = page.inner_text("[data-books-note]")
                    assert any("\u0600" <= ch <= "\u06ff" for ch in note), f"English note in Arabic: {note!r}"
                    # ask the label function itself, which translates at call time, so a
                    # chip drawn before the language switched cannot hide an English label
                    lbl = page.evaluate("() => SubsystemApp._identityRoleLabel()")
                    assert any("\u0600" <= ch <= "\u06ff" for ch in lbl), f"English role label in Arabic: {lbl!r}"
                ur.shot(page, f"home-{vp_name}-{lang}")
            finally:
                ctx.close()
    for theme in THEMES:
        for lang in ("en", "ar"):
            ctx, page = person_page(browser, base, ACCOUNTANT, viewport=SMALL, lang=lang, theme=theme)
            try:
                ur.go_lang(page, lang)
                page.evaluate("() => SubsystemApp._navigate('dashboard')")
                page.wait_for_selector(".bkh-grid", timeout=8000)
                page.wait_for_timeout(300)
                bg = page.evaluate("""() => { const c = document.querySelector('.bkh-card'); const s = getComputedStyle(c);
                    return [s.backgroundColor, s.color, getComputedStyle(document.body).backgroundColor]; }""")
                assert bg[0] != bg[2], f"{theme}: card is invisible against the ground {bg}"
                assert bg[0] != bg[1], f"{theme}: card text same colour as its card {bg}"
                assert not page.evaluate("() => document.documentElement.scrollWidth > innerWidth + 1")
                ur.shot(page, f"home-theme-{theme}-{lang}")
            finally:
                ctx.close()


SPY_TIMERS = """() => { window.__ticks = [];
    const real = window.setInterval.bind(window);
    window.setInterval = (fn, ms, ...rest) => { if (ms === 60000) window.__ticks.push(fn); return real(fn, ms, ...rest); };
    return true; }"""


def nav_rows(page):
    return page.evaluate("() => [...document.querySelectorAll('.sub-nav-item[data-section]')].map(e => e.dataset.section)")


def case_books(browser, base):
    # ---- accountant: header, sidebar, chip, timer
    ctx, page = person_page(browser, base, ACCOUNTANT)
    try:
        page.wait_for_selector(".bkh-grid", timeout=20000)
        head = page.inner_text("#sub-header-section").strip()
        assert head == "Books", f"accountant's header says {head!r}, want 'Books'"
        rows = nav_rows(page)
        assert len(rows) >= 4 and "trial-balance" in rows, f"sidebar not really rendered (vacuous): {rows}"
        for gone in ("returns", "scanner"):
            assert gone not in rows, f"accountant is still offered the {gone!r} row: {rows}"
        # chip: the raw platform word must never be printed
        c = chip(page)
        info = page.evaluate("""async () => { const r = await fetch('/api/devices/me', {credentials: 'same-origin'});
            const j = await r.json(); const d = (j && j.device) || {};
            return {label: SubsystemApp.deviceLabel, name: d.device_name || d.device_label || null,
                    platform: d.platform || null}; }""")
        assert info["label"] != "WINDOWS", f"chip printed the raw platform word: {info}"
        if not info["name"] and info["platform"]:
            assert info["label"] == "Windows", f"no device name, platform {info['platform']!r}: label {info['label']!r}"
            assert "WINDOWS" not in (c["title"] or ""), f"chip title has the raw platform word: {c['title']!r}"
        pretty = page.evaluate("""() => ['WINDOWS', 'android', 'IOS', 'MacOS', 'fooBAR', '', null, '  '].map(
            p => SubsystemApp._platformLabel(p))""")
        assert pretty == ["Windows", "Android", "iOS", "macOS", "Foobar", "", "", ""], f"platform words: {pretty}"
        # timer: with the books home open, NO 60 s refresh is armed
        page.evaluate(SPY_TIMERS)
        page.evaluate("() => SubsystemApp._navigate('trial-balance')")
        page.wait_for_function("() => SubsystemApp.currentSection === 'trial-balance'", timeout=8000)
        page.evaluate("() => SubsystemApp._navigate('dashboard')")
        page.wait_for_selector(".bkh-grid", timeout=8000)
        page.wait_for_timeout(500)
        ticks = page.evaluate("() => window.__ticks.length")
        assert ticks == 0, f"{ticks} 60 s refresh timer(s) armed on the books home"
        assert page.evaluate("() => !SubsystemApp._autoRefreshTimer"), "an auto-refresh timer is live on the books home"
        # focus survives: put focus on a card by a real keyboard Tab from the note, wait, still there
        page.focus(".bkh-card")
        marker = page.evaluate("() => { const e = document.activeElement; e.setAttribute('data-keep', '1'); return e.dataset.booksCard; }")
        page.wait_for_timeout(800)
        keep = page.evaluate("() => { const e = document.activeElement; return e && e.dataset ? e.dataset.keep : null; }")
        assert keep == "1", f"focus left the books card {marker!r}"
        ur.shot(page, "books-accountant-en")
        # Arabic header
        ur.go_lang(page, "ar")
        page.evaluate("() => SubsystemApp._navigate('dashboard')")
        page.wait_for_selector(".bkh-grid", timeout=8000)
        ar_head = page.inner_text("#sub-header-section").strip()
        assert any("\u0600" <= ch <= "\u06ff" for ch in ar_head), f"Arabic header is {ar_head!r}"
        ur.shot(page, "books-accountant-ar")
    finally:
        ctx.close()
    # ---- allow half: manager and cashier keep their rows; the manager's timer runs
    for email, want in ((MANAGER, ("returns", "scanner")), (CASHIER, ("returns", "scanner"))):
        ctx, page = person_page(browser, base, email)
        try:
            wait_landed(page)
            rows = nav_rows(page)
            for r in want:
                assert r in rows, f"{email} lost the {r!r} row: {rows}"
        finally:
            ctx.close()
    ctx, page = person_page(browser, base, MANAGER)
    try:
        wait_landed(page)
        page.evaluate(SPY_TIMERS)
        page.evaluate("() => SubsystemApp._navigate('customers')")
        page.wait_for_function("() => SubsystemApp.currentSection === 'customers'", timeout=8000)
        page.evaluate("() => SubsystemApp._navigate('dashboard')")
        page.wait_for_function("() => SubsystemApp.currentSection === 'dashboard' && !!SubsystemApp._autoRefreshTimer",
                               timeout=15000)
        head = page.inner_text("#sub-header-section").strip()
        assert head == "Dashboard", f"manager's header says {head!r}"
        assert page.evaluate("() => window.__ticks.length") == 1, "manager's dashboard did not arm its 60 s refresh"
        # the tick re-renders while the person is a manager ...
        calls = page.evaluate("""() => { const seen = []; const nav = SubsystemApp._navigate.bind(SubsystemApp);
            SubsystemApp._navigate = (id, ...a) => { seen.push(id); return nav(id, ...a); };
            window.__ticks[0](); const n1 = seen.length;
            // ... and goes quiet the moment they are books-only (relogin as someone else)
            const keep = SubsystemApp.capabilities;
            SubsystemApp.capabilities = ['retail.books.view', 'retail.reports'];
            window.__ticks[0](); const n2 = seen.length;
            SubsystemApp.capabilities = keep; SubsystemApp._navigate = nav; return [n1, n2]; }""")
        assert calls == [1, 1], f"tick re-render counts (manager, then books-only): {calls}"
    finally:
        ctx.close()


# -------------------------------------------------------------------------- main
def main() -> int:
    args = sys.argv[1:]
    if "--only" in args:
        _pe.ONLY = set(args[args.index("--only") + 1].split(","))

    def wanted(tag):
        return not _pe.ONLY or tag in _pe.ONLY

    data_dir = Path(tempfile.mkdtemp(prefix="aura_retail_s_home_"))
    ur.DATA["dir"] = data_dir
    backend = Backend(data_dir)
    backend.port = ur.pick_port()
    backend.base_url = f"http://127.0.0.1:{backend.port}"
    base = backend.base_url
    print(f"Backend: {base}", flush=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            backend.start()
            c0 = browser.new_context(viewport=VIEWPORT, reduced_motion="reduce")
            p0 = c0.new_page()
            p0.goto(base, wait_until="domcontentloaded")
            p0.locator("#su-name").wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            p0.fill("#su-name", "Tareq")
            p0.fill("#su-company", "Users Roles Shop")
            p0.fill("#su-email", ur.OWNER_EMAIL)
            p0.fill("#su-pass", ur.OWNER_PASSWORD)
            p0.fill("#su-pass2", ur.OWNER_PASSWORD)
            p0.click("#su-btn")
            p0.locator(".sub-nav-item").first.wait_for(state="visible", timeout=DEFAULT_WAIT_MS)
            from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
            seed_active_license(str(data_dir), product_code="AURA_RETAIL", platform="WINDOWS")
            c0.close()

            ctx, owner = ur.owner_page(browser, base)
            try:
                branch_uid = ur.first_branch_uid(owner, base)
                ur.invite_via_api(owner, base, CASHIER, "cashier", name="Cara Cashier")
                ur.invite_via_api(owner, base, MANAGER, "manager", name="Mo Manager")
                ur.invite_via_api(owner, base, ACCOUNTANT, "accountant", name="Amal Accountant")
                ur.invite_via_api(owner, base, "bm@usersroles.test", "branch_manager",
                                  name="Bassam BM", branch_uid=branch_uid)
                ur.invite_via_api(owner, base, NONAME, "cashier")
            finally:
                ctx.close()
            # Restart so the REAL boot announce records this computer (platform,
            # version, registry schema). It skips at first boot (no company yet)
            # and no heartbeat runs without a sync service; a stand-in SQL
            # write here would hide exactly that.
            backend.stop()
            backend.start()
            print("SEEDING DONE.", flush=True)
            for tag, fn in (("chip", case_chip), ("home", case_home), ("phone", case_phone), ("look", case_look),
                            ("books", case_books)):
                if wanted(tag):
                    check(tag, fn.__name__.replace("case_", ""), lambda f=fn: f(browser, base))
        finally:
            backend.stop()
            browser.close()

    failed = [r for r in _pe.RESULTS if not r[1]]
    print(f"\nRESULTS: {len(_pe.RESULTS) - len(failed)}/{len(_pe.RESULTS)} passed", flush=True)
    for name, ok, msg in failed:
        print(f"  FAIL {name}: {msg}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
