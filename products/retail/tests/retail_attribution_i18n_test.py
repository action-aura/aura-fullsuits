"""Aura Retail -- the two contracts the attribution UI rests on.

This file covers the halves of that work that a Node test in this directory
structurally cannot: the SERVER side of the capability contract, and the
bilingual catalog.

── Why the session-shape test is here and not in the JS suite ──────────────

The capability-gating bug this wave fixed was not a logic error inside either
file. app-shell.js asked for `sess.user.capabilities`; get_session()
(commercial_runtime/identity/onboarding_routes.py) returns `capabilities` as a
TOP-LEVEL key beside `user`. Each side was internally consistent and each side
had (or could have had) passing tests; what nobody owned was the seam. The
result was a gate that read correctly in review and did nothing at runtime for
every role on every install, because `hasCapability()` fails OPEN on a missing
list by design.

retail_reports_capability_gate_test.js asserts the CLIENT half against a
fixture. A fixture is only worth what its fidelity to the server is worth, so
this file boots the real app, logs in as each role, and asserts the server
half against the same shape -- and additionally asserts that app-shell.js
literally reads the key the server literally sends. That last assertion is the
only one of the set that would have caught the original bug, because it is the
only one that spans both files.

── Why the catalog test is here ────────────────────────────────────────────

retail_localization_test.py already enforces key-set parity and rejects any
Arabic value byte-identical to its English key, and both of those stay green.
Neither can tell whether a string the new UI actually renders was ever ADDED
to the catalog -- a string missing from both files passes parity perfectly and
renders as untranslated English on an Arabic page. So the assertions below run
in the other direction: start from the strings the frontend renders, and
require each one to exist in both catalogs with real Arabic.

Run:
    pytest products/retail/tests/retail_attribution_i18n_test.py -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
FRONTEND_DIR = PRODUCT_DIR / 'frontend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_attribution_i18n_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts as _accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

EN_PATH = FRONTEND_DIR / 'locales' / 'en.json'
AR_PATH = FRONTEND_DIR / 'locales' / 'ar.json'
SHELL_JS = FRONTEND_DIR / 'app-shell.js'
RETAIL_JS = FRONTEND_DIR / 'subsystem-retail.js'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _client_for(role):
    """A logged-in client whose user_permissions rows are seeded from `role`,
    exactly as account creation and the registry v3 migration seed them."""
    email = f"attr-{uuid.uuid4().hex[:10]}@test.local"
    password = "AttrTestPW1"
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), str(uuid.uuid4()), "EMP-0001", email, hash_password(password), role, "active"),
    )
    user_id = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    _accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()
    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})
    return client


def _en():
    return json.loads(EN_PATH.read_text(encoding='utf-8'))


def _ar():
    return json.loads(AR_PATH.read_text(encoding='utf-8'))


# ═════════════════════════════════════════════════════════════════════════════
# The seam: where the server puts `capabilities`, and where the shell looks
# ═════════════════════════════════════════════════════════════════════════════

def test_session_returns_capabilities_at_the_top_level_not_under_user():
    """Pins the payload shape the JS fixtures copy. If this ever moves, the
    fixtures in retail_reports_capability_gate_test.js are lying and this test
    is the one that says so."""
    body = _client_for("cashier").get("/api/auth/session").get_json()
    assert isinstance(body.get("capabilities"), list), \
        f"/api/auth/session must expose `capabilities` as a top-level list; got {body!r}"
    assert "capabilities" not in (body.get("user") or {}), \
        "`capabilities` appeared under `user`. Either location is defensible, but " \
        "the frontend reads ONE of them -- moving it without updating app-shell.js " \
        "silently disables every capability gate in the product."


#: Node harness for the cross-file assertion below.
#:
#: The Python half of this file can boot the real server and get the real
#: session body; it cannot execute app-shell.js. So it hands that body to
#: `node`, which loads the REAL app-shell.js (no fixture, no copy) into a vm
#: sandbox, calls the one method under test, and reports back what the shell
#: actually resolved. Both halves of the seam are therefore the real thing.
#:
#: The stub set is the minimum for app-shell.js's module body to finish
#: evaluating -- it ends in a DOMContentLoaded registration and a
#: table-labelling IIFE that opens a MutationObserver inside its own
#: try/catch. Neither is under test.
_SHELL_PROBE_JS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const shellFile = process.argv[2];
const session = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

function el() {
  return {
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {},
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild() {}, addEventListener() {}, removeEventListener() {}, remove() {},
    getAttribute() { return null; }, setAttribute() {},
    querySelector() { return el(); }, querySelectorAll() { return []; },
  };
}

const store = {};
const sandbox = {
  console,
  t: (s) => s,
  setTimeout, clearTimeout, setInterval, clearInterval,
  navigator: { userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
  location: { hash: '', href: 'http://localhost/' },
  localStorage: {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: (k) => { delete store[k]; },
  },
  document: {
    readyState: 'complete',
    body: el(),
    head: { appendChild() {} },
    documentElement: { getAttribute() { return null; }, setAttribute() {}, style: { setProperty() {} } },
    getElementById() { return null; },
    createElement() { return el(); },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    addEventListener() {},
  },
};
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(shellFile, 'utf8'), sandbox, { filename: shellFile });

const App = sandbox.SubsystemApp;
if (!App) { console.log(JSON.stringify({ error: 'app-shell.js did not expose window.SubsystemApp' })); process.exit(0); }

App._adoptSessionCapabilities(session);
const probes = {};
for (const code of JSON.parse(process.argv[4])) probes[code] = App.hasCapability(code);
console.log(JSON.stringify({ capabilities: App.capabilities, hasCapability: probes }));
"""


def _shell_resolve(session_body, probe_codes):
    """Run app-shell.js under node against `session_body` and report what it
    resolved. Returns {'capabilities': <list|null>, 'hasCapability': {...}}."""
    node = shutil.which('node')
    if node is None:
        # Mirrors products/run_all_tests.py exactly: a missing `node` is a
        # LOUD failure, never a silent skip, unless the developer opted in.
        # A silent skip is how the JS suite went unrun in CI for as long as
        # it did, and this assertion is the only one in the repo that spans
        # the server/shell seam -- skipping it by accident restores the
        # original blind spot.
        if os.environ.get('AURA_ALLOW_MISSING_NODE') == '1':
            pytest.skip("node is not on PATH and AURA_ALLOW_MISSING_NODE=1 was set explicitly")
        pytest.fail(
            "node is not on PATH, so the shell half of this assertion cannot run. "
            "This is the only test that executes app-shell.js against a real "
            "server response; without it the seam is unguarded. Set "
            "AURA_ALLOW_MISSING_NODE=1 to skip deliberately on a machine with no "
            "Node installed. CI must never set it."
        )
    probe_dir = DATA / "shell_probe"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_js = probe_dir / "probe.js"
    probe_js.write_text(_SHELL_PROBE_JS, encoding='utf-8')
    session_json = probe_dir / "session.json"
    session_json.write_text(json.dumps(session_body), encoding='utf-8')

    proc = subprocess.run(
        [node, str(probe_js), str(SHELL_JS), str(session_json), json.dumps(list(probe_codes))],
        capture_output=True, text=True,
        # Explicit, because the default on Windows is the ANSI code page and
        # app-shell.js is UTF-8 with emoji in its nav labels -- a decode error
        # here would surface as a mangled failure message about something else
        # entirely. `replace` so a stray byte can never mask a real result.
        encoding='utf-8', errors='replace',
    )
    assert proc.returncode == 0, \
        f"the app-shell.js probe crashed (exit {proc.returncode}):\n{proc.stderr[-2000:]}"
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert 'error' not in out, out['error']
    return out


def test_shell_resolves_grants_from_the_real_session_body():
    """The cross-file assertion, and the only one in this repo that spans both
    sides of the seam with neither side stubbed.

    app-shell.js used to read `sess.user.capabilities`, which the server has
    never sent, so `this.capabilities` stayed null and `hasCapability()`
    answered true for everyone -- including the cashier the whole mechanism
    exists to gate.

    ── Why this test executes JavaScript instead of grepping for a pattern ───

    Until 2026-08-21 this assertion was a regex over app-shell.js:

        re.search(r"Array\\.isArray\\(\\s*\\w+\\.capabilities\\s*\\)", src)

    which is satisfied by `Array.isArray(this.capabilities)` inside
    `hasCapability()` -- a line that has nothing to do with where the session
    body is read. Measured, not argued: reintroducing the original bug (making
    `_adoptSessionCapabilities` fall through to `sess.user.capabilities`) left
    that assertion GREEN (`1 passed`) while the behavioural JS suite went red
    on two cases. A test whose subject is "these two files agree" cannot be a
    text search over one of them: any string anywhere in a 117KB file
    satisfies it, and the thing it claims to protect is a runtime value.

    So this drives the REAL shell with the REAL server's REAL response body --
    no fixture on either side -- and asks the question the cashier landing and
    the Reports gate ask: what does hasCapability() answer?
    """
    body = _client_for("cashier").get("/api/auth/session").get_json()
    # Guard the premise before asserting on the consequence, and fail on it
    # SEPARATELY so the two causes are never confused.
    #
    # `capabilities: null` is a real, legitimate server answer -- get_session()
    # emits it for "could not compute", deliberately distinct from `[]` for
    # "computed, holds nothing". When the server says null, the shell resolving
    # to null and failing open is CORRECT, and every assertion below would then
    # be checking the fail-open path rather than the wiring. A bare failure on
    # `hasCapability` would read as "the gate is broken" when the real news is
    # "the registry lookup failed"; this line says which.
    assert isinstance(body.get("capabilities"), list) and body["capabilities"], \
        f"premise failed -- the server did not compute a capability list for this " \
        f"cashier, so there is nothing to assert the shell against. That is a " \
        f"server-side fault (get_session's registry read), not a shell one. Body: {body!r}"

    out = _shell_resolve(body, [_accounts.CAP_REPORTS, _accounts.CAP_SELL])

    assert out['capabilities'] == body['capabilities'], (
        "app-shell.js did not resolve the grant list out of the body the server "
        f"actually sent. Server sent {body['capabilities']!r}; the shell ended up "
        f"with {out['capabilities']!r}. The historical cause is reading "
        "`sess.user.capabilities`, a key /api/auth/session has never carried, "
        "which leaves `this.capabilities` null forever."
    )
    assert out['hasCapability'][_accounts.CAP_REPORTS] is False, (
        "the shell told a cashier they hold retail.reports. `this.capabilities` "
        "is null, and hasCapability() fails OPEN on null by design -- so this is "
        "what a wiring mistake in _adoptSessionCapabilities() looks like from the "
        "outside: every gate in the product renders as if unrestricted."
    )
    assert out['hasCapability'][_accounts.CAP_SELL] is True, (
        "Sanity: a capability the cashier genuinely holds must still answer true, "
        "or this test would pass against a shell that denies everything."
    )


def test_shell_resolves_grants_for_an_owner_too():
    """The same seam from the permissive side. A shell that answered `false`
    for everyone would satisfy the cashier assertion above perfectly while
    hiding every gated screen from the person who owns the shop.

    Note what this case CANNOT catch, so nobody mistakes it for redundancy
    with the one above: hasCapability() fails OPEN on an unresolved list, so
    reintroducing the original wiring bug leaves this assertion green. The
    cashier case is the discriminating one; this one only stops the
    over-correction."""
    body = _client_for("admin").get("/api/auth/session").get_json()
    out = _shell_resolve(body, [_accounts.CAP_REPORTS])
    assert out['hasCapability'][_accounts.CAP_REPORTS] is True, (
        "the shell denied retail.reports to an owner whose session response "
        f"lists it. Server sent {body.get('capabilities')!r}; the shell resolved "
        f"{out['capabilities']!r}."
    )


def test_cashier_session_genuinely_lacks_reports_capability():
    body = _client_for("cashier").get("/api/auth/session").get_json()
    assert _accounts.CAP_REPORTS not in body["capabilities"], \
        "A cashier must not hold retail.reports -- the Reports screen's five " \
        "panels all read @mt_require_capability(CAP_REPORTS) routes."
    assert _accounts.CAP_SELL in body["capabilities"], \
        "Sanity: a cashier must still hold retail.sell, or this test would pass " \
        "against an empty grant list that proves nothing."


def test_owner_session_holds_reports_capability():
    body = _client_for("admin").get("/api/auth/session").get_json()
    assert _accounts.CAP_REPORTS in body["capabilities"], \
        "The owner must hold retail.reports -- the gate is meant to hide a screen " \
        "the server would refuse, not the screen itself."


# ═════════════════════════════════════════════════════════════════════════════
# Bilingual catalog for the strings this wave introduced
# ═════════════════════════════════════════════════════════════════════════════

#: Every user-visible English string the attribution work added. Listed
#: explicitly rather than scraped, because a scraper over a 200KB template-
#: literal file cannot separate a rendered label from a CSS selector or a URL
#: fragment -- and a scraper that silently matches nothing is a test that
#: silently checks nothing. test_declared_new_strings_are_really_rendered
#: below closes the drift risk from the other side: each entry must actually
#: appear in the frontend source, so this list cannot rot into fiction.
NEW_UI_STRINGS = (
    'Till',
    'Not recorded',
    'Account removed',
    'Sales by Employee',
    'No sales in this period.',
    'Sales by employee are not available on this version.',
    'Could not load sales by employee.',
    'Sales recorded before this release show no employee or till.',
    'Invoice',
    'Customer',
    'Payment',
    'The activity log is limited to managers and the store owner.',
)

#: The six cells of the sale-detail header grid. `Till` alone used to go
#: through t() while its five siblings were hardcoded English -- identical in
#: English, half-Arabic on an Arabic page. Two of the five (`Customer`,
#: `Payment`) were in NEITHER catalog, so i18n.js's DOM sweep could not rescue
#: them either; the other three happened to be catalog keys already and were
#: rescued by luck rather than by design, which is why the wrapping is now
#: uniform. The behavioural half of this -- that each label actually passes
#: through t() at render time -- is
#: retail_attribution_ui_test.js::testSaleDetailGridLabelsAllGoThroughT; this
#: half is that each one exists in both catalogs to be looked up.
SALE_DETAIL_GRID_LABELS = ('Customer', 'Cashier', 'Till', 'Payment', 'Status', 'Total')


def test_sale_detail_grid_labels_are_all_in_both_catalogs():
    en, ar = _en(), _ar()
    gaps = [s for s in SALE_DETAIL_GRID_LABELS if s not in en or s not in ar]
    assert gaps == [], (
        f"sale-detail grid labels missing from a catalog: {gaps}. These six sit "
        "in one row of one grid; a label that is not a catalog key renders as "
        "English beside five Arabic ones, and t() cannot report the difference "
        "because it returns its argument unchanged for an unknown key."
    )


def test_new_strings_exist_in_both_catalogs():
    en, ar = _en(), _ar()
    missing_en = [s for s in NEW_UI_STRINGS if s not in en]
    missing_ar = [s for s in NEW_UI_STRINGS if s not in ar]
    assert missing_en == [], f"new UI strings absent from en.json: {missing_en}"
    assert missing_ar == [], f"new UI strings absent from ar.json: {missing_ar}"


def test_new_strings_have_real_arabic():
    """Not merely present, and not the English copied across. i18n.js's t()
    falls back to the English key for anything missing, so a half-added string
    fails silently on an Arabic page instead of raising."""
    en, ar = _en(), _ar()
    for s in NEW_UI_STRINGS:
        value = ar.get(s, '')
        assert value.strip(), f"'{s}' has an empty Arabic translation"
        assert value != en.get(s), f"'{s}' is not translated (Arabic == English)"
        assert re.search(r'[؀-ۿ]', value), \
            f"'{s}' has no Arabic script in its translation: {value!r}"


def test_declared_new_strings_are_really_rendered():
    """Keeps NEW_UI_STRINGS honest in the other direction: a key nobody
    renders is dead catalog weight, and a list allowed to drift stops being
    evidence of anything."""
    src = RETAIL_JS.read_text(encoding='utf-8')
    unused = [s for s in NEW_UI_STRINGS if s not in src]
    assert unused == [], \
        f"declared as new UI strings but not present in subsystem-retail.js: {unused}"


# ═════════════════════════════════════════════════════════════════════════════
# The capability-refusal panel, taken as a whole surface
# ═════════════════════════════════════════════════════════════════════════════
#
# A hand-written list of the strings on a panel is exactly the fixture that
# hides this bug class: the list is written by whoever adds a string, so the
# one string they forgot to wrap or forgot to translate is also the one they
# forget to list, and the test then agrees with the omission. NEW_UI_STRINGS
# above accepts that risk deliberately for a 200KB file (a scraper cannot tell
# a rendered label from a CSS selector there) and pays for it with
# test_declared_new_strings_are_really_rendered.
#
# This panel is small and structurally bounded, so it can be scraped honestly
# instead: `_renderCapabilityRestricted` is the single markup block both
# refusal screens go through, and every string on the rendered panel comes from
# either that body (the "Point of Sale" button) or the options object its
# caller passes (icon/title/message). Read those regions and the list cannot
# fall behind the panel.
#
# The bug this catches, which the last pass reported as fixed: the Audit Log
# refusal reads `title: t('Audit Log')` -- rendered TWICE, as the <h2> and the
# <h3> -- and 'Audit Log' was in neither catalog. Its message and its button
# were both translated, so on an Arabic page the panel came out with an English
# heading over an Arabic sentence and an Arabic button. Nothing was red,
# because t() returns its argument unchanged for a key it cannot find and
# retail_localization_test.py's parity check is perfectly satisfied by a string
# absent from BOTH files.

#: `this._renderCapabilityRestricted(c, { ... });` -- the options object each
#: refusal screen passes. Non-greedy to the first `});`, which is the end of
#: the call. The method DEFINITION does not match: its parameter list reads
#: `(c, opts) {`, so there is no `{` directly after `c,`.
_CAP_RESTRICTED_CALL = re.compile(r'_renderCapabilityRestricted\(\s*c\s*,\s*\{(.*?)\n\s*\}\)\s*;', re.S)


def _method_body(src, signature):
    """Source of one 2-space-indented object method, signature line to its
    closing `\\n  },`. Raises rather than returning empty if the shape ever
    changes -- a scraper that silently matches nothing is a test that silently
    checks nothing, which is the failure mode this whole section exists to
    avoid."""
    start = src.index(signature)
    end = src.index('\n  },', start)
    return src[start:end]


def _panel_string_regions():
    """(shared-body literals, [per-call-site literals]) for the refusal panel."""
    src = RETAIL_JS.read_text(encoding='utf-8')
    shared = _method_body(src, '_renderCapabilityRestricted(c, opts) {')
    calls = _CAP_RESTRICTED_CALL.findall(src)
    lit = lambda blob: {m.replace("\\'", "'") for m in _T_LITERAL.findall(blob)}
    return lit(shared), [lit(c) for c in calls]


def test_capability_refusal_panel_scrape_actually_found_the_panel():
    """Guard on the instrument, asserted before the instrument is trusted. If
    the regex or the method-body slice stops matching, the catalog test below
    would pass on an empty set and report a fully-translated panel that nobody
    looked at."""
    shared, per_call = _panel_string_regions()
    assert shared, (
        "no t() literal found in _renderCapabilityRestricted's body -- the "
        "shared markup contributes at least the 'Point of Sale' button label, "
        "so an empty result means the slice stopped matching, not that the "
        "panel changed."
    )
    assert len(per_call) >= 2, (
        f"expected both retail.reports refusal screens (Reports and Audit Log) "
        f"to call _renderCapabilityRestricted; found {len(per_call)} call site(s)."
    )
    thin = [i for i, s in enumerate(per_call) if len(s) < 2]
    assert thin == [], (
        f"call site(s) {thin} contributed fewer than two t() literals. Each "
        "passes a title AND a message; fewer means the options blob was "
        "truncated by the regex, or a label was rendered without t()."
    )


def test_every_string_on_the_capability_refusal_panel_is_translated():
    """Every word a refused user reads, in both catalogs, with real Arabic.

    Half-translated is the specific defect: it is invisible in English, it is
    invisible in review (every string is wrapped in t(), so the source looks
    right), and it produces a panel that an Arabic-speaking shopkeeper reads as
    a broken screen rather than a policy."""
    en, ar = _en(), _ar()
    shared, per_call = _panel_string_regions()
    strings = set(shared).union(*per_call)

    missing = sorted(s for s in strings if s not in en or s not in ar)
    assert missing == [], (
        f"strings rendered on the capability-refusal panel that are absent from "
        f"a catalog: {missing}. t() returns its argument unchanged for an "
        "unknown key, so each of these renders as English beside its translated "
        "siblings on the same panel."
    )

    untranslated = sorted(
        s for s in strings
        if not ar[s].strip() or ar[s] == en[s] or not re.search(r'[؀-ۿ]', ar[s])
    )
    assert untranslated == [], (
        f"present in ar.json but not actually translated: {untranslated}. An "
        "Arabic value that is blank, or byte-identical to the English, or "
        "carries no Arabic script is the same screen as a missing key with a "
        "passing parity check on top."
    )


#: Strings already wrapped in t() by earlier waves that were never added to
#: either catalog. Recorded, not fixed, and recorded rather than ignored.
#:
#: t() returns its argument unchanged for any key it cannot find (i18n.js:
#: `return (text in d) ? d[text] : text`), which is the right fallback and
#: also the reason this rots invisibly: the code LOOKS localized, review sees
#: t() everywhere, and the screen is simply English forever in Arabic mode. No
#: existing test could see it -- retail_localization_test.py compares the two
#: catalogs against each other, and two catalogs that both lack a string are
#: in perfect parity.
#:
#: Three features are affected: the Audit Log viewer (Timestamp/User/Action/
#: Entity, its filter controls and its pager), the low-stock reorder-request
#: panel, and the WhatsApp reports entry point. Each belongs to whoever owns
#: that area; translating 41 strings across three features was not this
#: change's job, and doing it silently would bury them.
#:
#: The point of the list is the ratchet below: this baseline may shrink, never
#: grow. A new untranslated string fails immediately, at the wave that adds
#: it, instead of being discovered by an Arabic-speaking user.
KNOWN_UNTRANSLATED_BASELINE = frozenset({
    'Accept',
    'Accept this request and draft a local purchase order?',
    'Action',
    'All actions',
    'All entities',
    # 'Audit Log' was here and is deliberately gone. It is the TITLE of the
    # capability-refusal panel (rendered twice, <h2> and <h3>) whose message
    # and button were translated in the previous pass, so leaving it exempt
    # kept that panel half-Arabic while the change that produced it read as
    # complete. It is also the Audit Log screen's own heading and its nav
    # label, so translating it improves those too -- the rest of that screen's
    # strings stay listed below, because they belong to whoever owns that
    # feature and 41 strings was never this change's job.
    'Automatically drafted when a sale drops a product at or below its reorder level. '
    'Accept drafts a local purchase order for this device; Decline dismisses it.',
    'Branch',
    'Category deleted',
    'Clear',
    'Could not accept this request.',
    'Could not decline this request.',
    'Could not delete this category.',
    'Could not delete this customer.',
    'Could not delete this supplier.',
    'Could not load the audit log.',
    'Customer deleted',
    'Decline',
    'Decline this reorder request?',
    'Entity',
    'From',
    'Low-Stock Reorder Requests',
    'Manage WhatsApp Reports',
    'Next ›',
    'No audit entries match these filters.',
    'No pending reorder requests.',
    'Note',
    'Page',
    'Purchase order drafted',
    'Request accepted',
    'Request declined',
    'Requested',
    'Send daily sales, shift-close, low-stock, and overdue-balance reports to multiple '
    'phone numbers by role or branch. Off by default.',
    'Supplier deleted',
    'Timestamp',
    'To',
    'User',
    'WhatsApp Reports',
    'of',
    'total entries',
    '‹ Prev',
})

#: t('...') with a single-quoted literal. Template-literal and variable
#: arguments are skipped deliberately -- a t(`...${x}...`) call has no fixed
#: key to look up, and treating one as a key would report a phantom.
_T_LITERAL = re.compile(r"\bt\(\s*'((?:[^'\\]|\\.)*)'\s*\)")


def _t_literals():
    return {m.replace("\\'", "'") for m in _T_LITERAL.findall(RETAIL_JS.read_text(encoding='utf-8'))}


def test_no_new_untranslated_ui_strings():
    """Ratchet. Every t() literal in subsystem-retail.js must be a catalog key,
    except the baseline above."""
    en = _en()
    gaps = {s for s in _t_literals() if s not in en} - KNOWN_UNTRANSLATED_BASELINE
    assert gaps == set(), (
        f"t() is called with {len(gaps)} string(s) that exist in neither catalog: "
        f"{sorted(gaps)}. t() returns its argument unchanged for an unknown key, so "
        "these render as English on an Arabic page while looking localized in the "
        "source. Add them to BOTH locales/en.json and locales/ar.json."
    )


def test_untranslated_baseline_does_not_contain_fixed_strings():
    """The other half of the ratchet: an entry translated later must be removed
    from the baseline, or the list stops describing the codebase and starts
    granting permanent exemptions to strings that no longer need one."""
    en = _en()
    fixed = sorted(s for s in KNOWN_UNTRANSLATED_BASELINE if s in en)
    assert fixed == [], \
        f"these are in en.json now and must be dropped from KNOWN_UNTRANSLATED_BASELINE: {fixed}"


def test_catalog_parity_still_holds_after_the_additions():
    """Duplicates retail_localization_test.py's parity check on purpose. That
    file is a different process in the runner; a contributor adding a key here
    should get the failure from the file they are editing, not from a suite
    they may not have thought to run."""
    en, ar = _en(), _ar()
    assert set(en) == set(ar), \
        f"missing in ar: {sorted(set(en) - set(ar))} / extra in ar: {sorted(set(ar) - set(en))}"


# ═════════════════════════════════════════════════════════════════════════════
# Bidirectional text
# ═════════════════════════════════════════════════════════════════════════════

def test_no_translatable_word_is_concatenated_with_an_identifier():
    """The named open example of this bug class. `Invoice ${sale.sale_number}`
    inside one text node fails twice under dir="rtl": the leading Latin word
    is the first strong character, so the whole heading resolves
    left-to-right; and i18n.js's dictionary sweep matches a text node's FULL
    trimmed text, so a concatenated heading can never match 'Invoice' and
    stays English on an Arabic page forever."""
    src = RETAIL_JS.read_text(encoding='utf-8')
    hits = re.findall(
        r'<(\w+)[^>]*>\s*([A-Za-z][A-Za-z ]{2,})\$\{[^}]*(?:_number|_id|\.id)\b[^}]*\}',
        src,
    )
    # <title> is excluded, and the exclusion is a scope statement rather than a
    # convenience: the one match it removes is `_printReceipt`'s
    # `<title>Receipt ${saleData.sale_number}</title>`, which is the window
    # title of a separate print document. i18n.js's sweep runs over
    # document.body of the APP document and never reaches it, that document
    # carries no dir="rtl" of its own, and a print-window title is not laid
    # out beside Arabic text. Narrowing here keeps the check aimed at rendered
    # in-app text nodes, which is what the rule is about.
    offenders = [f'<{tag}>{word}${{...}}' for tag, word in hits if tag.lower() != 'title']
    assert offenders == [], \
        "a translatable word is concatenated with an identifier in one text node: " \
        f"{offenders}. Put the word in its own text node and wrap the identifier " \
        "in <bdi> so both the translation sweep and the bidi algorithm can see them apart."
