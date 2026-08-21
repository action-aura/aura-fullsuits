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
import sys
import tempfile
import uuid
from pathlib import Path

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


def test_shell_reads_the_key_the_server_actually_sends():
    """The cross-file assertion. app-shell.js used to read
    `sess.user.capabilities`, which the server has never sent, so
    `this.capabilities` stayed null and `hasCapability()` answered true for
    everyone -- including the cashier the whole mechanism exists to gate."""
    src = SHELL_JS.read_text(encoding='utf-8')
    # Any `Array.isArray(<identifier>.capabilities)` counts -- what is being
    # asserted is that the session object is read at its TOP level, not that a
    # particular local variable name survives. `.user.capabilities` has a dot
    # segment in between and is excluded by \w+ not matching `sess.user`.
    reads_top_level = re.search(r"Array\.isArray\(\s*\w+\.capabilities\s*\)", src)
    assert reads_top_level, \
        "app-shell.js does not read the top-level `capabilities` key that " \
        "/api/auth/session returns -- only a nested `<obj>.user.capabilities`, " \
        "which the server has never sent. That leaves `this.capabilities` null " \
        "forever, and hasCapability() fails OPEN on null, so every gate built on " \
        "it renders as if unrestricted while looking correct in review."


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
    'Sales by Employee',
    'No sales in this period.',
    'Sales by employee are not available on this version.',
    'Could not load sales by employee.',
    'Sales recorded before this release show no employee or till.',
    'Invoice',
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
    'Audit Log',
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
