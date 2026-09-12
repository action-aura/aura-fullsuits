"""
Aura Retail -- the owner's employee-management screen (multi-device Phase 1).

docs/launch-readiness/multi-device-design.md §3 says the employee half of the
account model is "already-written" server code whose "missing piece is purely
UI: nothing in `products/retail/frontend/` calls `/api/admin/employees`." This
file is that missing piece plus the contract it stands on.

It is written from the routes as they ACTUALLY behave, which is not the same
as how a screen author would assume they behave, and the differences are the
reason several of these tests exist at all:

  - `update_role` refuses the owner row with **409**, not 400, and its message
    is deliberately absent from the locale catalogs because the UI is expected
    to never offer a role control on that row in the first place. So the
    screen has to be structurally incapable of asking, not merely good at
    displaying the refusal -- `test_the_owner_row_offers_no_role_or_status_control`.
  - `update_pin` deliberately does NOT bump `session_version`, unlike every
    other write in that file. That asymmetry IS design §3's "attribution,
    never authorization" rule, so it is pinned here rather than left as a
    comment somebody later "fixes".
  - `update_status` has none of `update_role`'s guards: no 404 for an unknown
    id, no validation of the status string, and no owner-lockout bar. That is
    a live server-side gap this screen cannot close from the client, so the
    compensating control -- never rendering the control on the owner row -- is
    asserted instead, and the gap is reported upstream.

The frontend half is asserted against the SERVED static assets, the same
technique retail_licensing_nav_discoverability_test.py uses -- that test
exists because a fully-built page that nothing links to is invisible in every
other kind of test, which is precisely the failure this whole task is fixing.

Run:
    pytest products/retail/tests/retail_employee_management_test.py -v
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
LOCALES_DIR = FRONTEND_DIR / 'locales'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_employee_mgmt_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

#: `@mt_login_required` and nothing else -- borrowed from
#: retail_auth_hardening_test.py for the same reason it uses it: a 401 here can
#: only have come from the session check, not from a licence or subsystem gate
#: stacked on top.
AUTH_ONLY_ROUTE = '/api/devices'

OWNER_EMAIL = 'emp-screen-owner@test.local'
OWNER_PASSWORD = 'OwnerScreenPW1'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _owner():
    """The one admin this install allows. `create-admin` is gated on "no valid
    admin exists", so the first caller creates and every later caller logs in
    -- same shape as retail_registry_v3_accounts_test.py's `_admin_client`."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': OWNER_EMAIL, 'password': OWNER_PASSWORD,
    })
    if r.status_code == 409:
        r = client.post('/api/auth/login', json={'email': OWNER_EMAIL, 'password': OWNER_PASSWORD})
    assert r.status_code == 200, r.get_json()
    return client


def _owner_id():
    conn = registry_conn()
    try:
        return conn.execute("SELECT id FROM users WHERE email=?", (OWNER_EMAIL,)).fetchone()['id']
    finally:
        conn.close()


def _new_employee(owner, role='cashier'):
    """Through the REAL creation route, so every test below stands on the
    contract the screen actually calls rather than on a hand-built row."""
    email = f'emp-{uuid.uuid4().hex[:8]}@test.local'
    r = owner.post('/api/admin/employees', json={'email': email, 'role': role})
    assert r.status_code == 200, r.get_json()
    conn = registry_conn()
    try:
        user_id = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()['id']
    finally:
        conn.close()
    return user_id, email, r.get_json()


def _user_row(user_id):
    conn = registry_conn()
    try:
        return dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
    finally:
        conn.close()


def _caps(user_id):
    conn = registry_conn()
    try:
        return {
            r['subsystem']: r['access_level']
            for r in conn.execute("SELECT subsystem, access_level FROM user_permissions WHERE user_id=?",
                                  (user_id,)).fetchall()
        }
    finally:
        conn.close()


def _activate(user_id, password):
    """Give an invited (`pending_setup`, password_hash='PENDING') account a
    real password so it can log in, without going through the emailed invite
    -- the invite flow has its own coverage; these tests need a live session
    belonging to a non-owner."""
    conn = registry_conn()
    conn.execute("UPDATE users SET password_hash=?, status='active' WHERE id=?",
                 (hash_password(password), user_id))
    conn.commit()
    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# The list the screen reads
# ═════════════════════════════════════════════════════════════════════════════

def test_the_list_carries_code_role_status_and_whether_a_pin_is_set():
    owner = _owner()
    user_id, email, _ = _new_employee(owner, role='manager')

    rows = owner.get('/api/admin/employees').get_json()['employees']
    row = next(r for r in rows if r['id'] == user_id)

    assert row['employee_id'].startswith('EMP-')
    assert row['email'] == email
    assert row['role'] == 'manager'
    assert row['status'] == 'pending_setup'
    assert row['has_pin'] in (0, False), "a freshly invited account has no PIN"

    assert owner.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '4821'}).status_code == 200
    rows = owner.get('/api/admin/employees').get_json()['employees']
    row = next(r for r in rows if r['id'] == user_id)
    assert row['has_pin'] in (1, True)


def test_the_list_never_carries_a_pin_hash():
    """A four-digit secret is defensible behind 600k PBKDF2 iterations on a
    stolen registry.db and indefensible sitting in a rendered page with the
    till's devtools one keypress away: 10,000 candidates against a hash you
    already hold is seconds of work. The screen needs a BOOLEAN, and the
    boolean is computed in SQL so the hash never leaves the process."""
    owner = _owner()
    user_id, _, _ = _new_employee(owner)
    assert owner.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '1357'}).status_code == 200

    body = owner.get('/api/admin/employees')
    assert 'pin_hash' not in body.get_data(as_text=True)
    for row in body.get_json()['employees']:
        assert 'pin_hash' not in row


# ═════════════════════════════════════════════════════════════════════════════
# Role changes
# ═════════════════════════════════════════════════════════════════════════════

#: Derived from `user_accounts.ROLE_CAPABILITIES`, never copied out of it.
#: Which codes separate a manager from a cashier is that module's live design
#: surface -- it was revised while this screen was being built (refund moved
#: to a cashier default; cash-approve left manager, because a role holding it
#: alongside cash-close could sign off its own shortfall). A test that pinned
#: a snapshot of the matrix would fail on every legitimate revision of it and
#: would be testing the constant rather than the route. What has to hold no
#: matter how the matrix is tuned is the MECHANISM: a promotion grants exactly
#: what the new role says, a demotion takes it back.
_MANAGER_ONLY = sorted(user_accounts.capabilities_for_role('manager')
                       - user_accounts.capabilities_for_role('cashier'))
_CASHIER_KEEPS = sorted(user_accounts.capabilities_for_role('cashier'))


def test_the_two_assignable_roles_actually_differ():
    """Guard against the tests below passing vacuously: if manager and cashier
    ever defaulted to the same set, `_MANAGER_ONLY` would be empty and a
    promotion that granted nothing would look correct."""
    assert _MANAGER_ONLY, 'manager and cashier have identical defaults'
    assert _CASHIER_KEEPS, 'a cashier with no capabilities cannot work a till'


#: One fixed vocabulary word/phrase per capability CODE -- not a snapshot of
#: which role holds which code, which is exactly the part `_MANAGER_ONLY` /
#: `_CASHIER_KEEPS` above compute at import time so this test never hardcodes
#: it. A capability CODE's English name is stable; which role is GRANTED it
#: is the axis that moved once already (see the comment above `_MANAGER_ONLY`)
#: and will again. `CAP_CASH_APPROVE` and `CAP_EMPLOYEES` are owner-only under
#: every role this screen assigns, so their phrases must never appear in the
#: invite-modal copy at all -- included here so the "and no others" half of
#: the assertion below has something to check.
_CAP_PHRASE = {
    user_accounts.CAP_SELL: 'sell',
    user_accounts.CAP_REFUND: 'refund',
    user_accounts.CAP_DISCOUNT: 'discount',
    user_accounts.CAP_STOCK_ADJUST: 'adjust stock',
    user_accounts.CAP_REPORTS: 'read reports',
    user_accounts.CAP_CASH_CLOSE: 'close their own drawer',
    user_accounts.CAP_CASH_APPROVE: 'approve',
    user_accounts.CAP_EMPLOYEES: 'employee',
}


def test_the_role_explainer_copy_names_exactly_the_capabilities_each_role_holds():
    """The sentence under the role picker in the invite modal is the entire
    basis on which an owner decides which role to hand somebody, so it has to
    say what the roles actually grant -- not a snapshot of what they granted
    the day this sentence was last edited by hand. AUDIT: an earlier version
    claimed "A cashier can sell and close their own drawer[, but not refund].
    A manager can also refund[, ...]" after `CAP_REFUND` had already moved
    onto the cashier's OWN default set (see the comment above `_MANAGER_ONLY`)
    -- the copy simply never got updated when the matrix did, and nothing
    caught it because `test_the_two_assignable_roles_actually_differ` only
    checks that a delta exists, not that the screen describes it correctly.

    This test reads `user_accounts.capabilities_for_role` at runtime the same
    way `_MANAGER_ONLY`/`_CASHIER_KEEPS` do, and checks the copy names exactly
    that set on each side of the sentence -- so a future re-tuning of the
    matrix (like the one that caused this bug) fails THIS test instead of
    shipping silently wrong again.
    """
    src = _served('/static/employees.js')
    m = re.search(r"t\('(A cashier can[^']*)'\)", src)
    assert m, 'the role-explainer sentence was not found as a scannable t(...) literal'
    sentence = m.group(1)

    parts = sentence.split('. ')
    assert len(parts) == 2, f'expected exactly two sentences (cashier baseline, manager delta): {sentence!r}'
    cashier_clause, manager_clause = parts

    for code in _CASHIER_KEEPS:
        phrase = _CAP_PHRASE[code]
        assert phrase in cashier_clause, \
            f'a cashier holds {code!r} but the copy never says {phrase!r}: {cashier_clause!r}'
    for code in _MANAGER_ONLY:
        phrase = _CAP_PHRASE[code]
        assert phrase in manager_clause, \
            f'a manager is granted {code!r} over a cashier but the copy never says {phrase!r}: {manager_clause!r}'

    cashier_codes = set(_CASHIER_KEEPS)
    manager_only_codes = set(_MANAGER_ONLY)
    for code in user_accounts.CAPABILITY_CODES:
        phrase = _CAP_PHRASE[code]
        if code not in cashier_codes:
            assert phrase not in cashier_clause, \
                f'a cashier is NOT granted {code!r} but the baseline clause names it anyway: {cashier_clause!r}'
        if code not in manager_only_codes:
            assert phrase not in manager_clause, \
                f'{code!r} is not part of the manager-over-cashier delta but the copy names it anyway ' \
                f'(owner-only capabilities must never appear here): {manager_clause!r}'


def test_a_promotion_grants_the_new_roles_capabilities():
    """Promotion has to actually promote. Capability rows are seeded once per
    account and `seed_capabilities_for_user` is INSERT OR IGNORE, so a role
    change that only rewrote `users.role` would leave all eight grants exactly
    as the OLD role left them -- the screen would say "Manager" and the person
    would still be refused everything a manager is for."""
    owner = _owner()
    user_id, _, _ = _new_employee(owner, role='cashier')
    before = _caps(user_id)
    assert all(before[code] == 'none' for code in _MANAGER_ONLY)

    r = owner.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'})
    assert r.status_code == 200, r.get_json()

    assert _user_row(user_id)['role'] == 'manager'
    after = _caps(user_id)
    assert all(after[code] == 'full' for code in _MANAGER_ONLY), after
    assert after['retail.employees'] == 'none', 'only the owner manages accounts'


def test_a_demotion_takes_those_capabilities_back_again():
    """The mirror case, and the reason the route DELETEs before re-seeding
    rather than just re-seeding: a demotion that left the old grants in place
    is cosmetic."""
    owner = _owner()
    user_id, _, _ = _new_employee(owner, role='manager')
    assert all(_caps(user_id)[code] == 'full' for code in _MANAGER_ONLY)

    assert owner.put(f'/api/admin/employees/{user_id}/role', json={'role': 'cashier'}).status_code == 200
    after = _caps(user_id)
    assert all(after[code] == 'none' for code in _MANAGER_ONLY), after
    assert all(after[code] == 'full' for code in _CASHIER_KEEPS), \
        'a demotion must not strip what the lower role is supposed to keep'


def test_a_role_change_leaves_the_legacy_subsystem_grant_alone():
    """The reset is bounded to the eight namespaced codes on purpose. The
    legacy `subsystem='retail'` row is what `mt_require_subsystem` reads on
    ~80 routes TODAY, so an unbounded DELETE would lock the person out of the
    entire retail app as a side effect of changing their job title."""
    owner = _owner()
    email = f'emp-legacy-{uuid.uuid4().hex[:8]}@test.local'
    assert owner.post('/api/admin/employees', json={
        'email': email, 'role': 'cashier', 'permissions': {'retail': 'full'},
    }).status_code == 200
    conn = registry_conn()
    try:
        user_id = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()['id']
    finally:
        conn.close()

    assert owner.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'}).status_code == 200
    assert _caps(user_id)['retail'] == 'full'


def test_a_role_change_revokes_the_employees_live_session():
    """`create_session` stamps `mt_role` into the cookie at login and nothing
    ever re-reads it, so without a `session_version` bump a demoted manager
    keeps manager authority for as long as they stay logged in -- which on a
    till is until the shop closes. Part A of this phase made the bump real;
    this is the route that has to use it."""
    owner = _owner()
    user_id, email, _ = _new_employee(owner, role='manager')
    _activate(user_id, 'StaffLivePW1')

    staff = app.test_client()
    assert staff.post('/api/auth/login', json={'email': email, 'password': 'StaffLivePW1'}).status_code == 200
    assert staff.get(AUTH_ONLY_ROUTE).status_code == 200

    assert owner.put(f'/api/admin/employees/{user_id}/role', json={'role': 'cashier'}).status_code == 200

    assert staff.get(AUTH_ONLY_ROUTE).status_code == 401, \
        "a role change that only takes effect at next login is not a role change"


def test_a_role_change_moves_the_row_version():
    owner = _owner()
    user_id, _, _ = _new_employee(owner, role='cashier')
    before = _user_row(user_id)['row_version']

    assert owner.put(f'/api/admin/employees/{user_id}/role', json={'role': 'manager'}).status_code == 200
    after = _user_row(user_id)
    assert after['row_version'] > before
    assert after['updated_at_utc'].endswith('+00:00')


def test_the_role_route_refuses_anything_outside_the_assignable_domain():
    """Refuse, never quietly downgrade. 'admin' is refused for the same reason
    `create_employee` refuses it: this install allows exactly one owner
    account, so "promote to admin" is not an operation that exists."""
    owner = _owner()
    user_id, _, _ = _new_employee(owner)

    for bad in ('admin', 'wizard', '', None):
        r = owner.put(f'/api/admin/employees/{user_id}/role', json={'role': bad})
        assert r.status_code == 400, f'{bad!r} was accepted'
    assert _user_row(user_id)['role'] == 'cashier', "a refused change must change nothing"


def test_the_owner_account_cannot_be_demoted():
    """`onboarding_status` decides whether to show first-run setup by asking
    for a row with `role='admin'` and a real password. Demote the only admin
    and that query finds nothing: the install answers `needs_setup: true` and
    offers a stranger the create-admin screen on a shop full of live data.
    This is a hijack path, not merely a lockout.

    409, not 400: the route treats this as a conflict with the install's
    one-owner invariant rather than as malformed input, and the screen must
    not assume every refusal is a 400."""
    owner = _owner()
    r = owner.put(f'/api/admin/employees/{_owner_id()}/role', json={'role': 'cashier'})
    assert r.status_code == 409, r.get_json()
    assert _user_row(_owner_id())['role'] == 'admin'
    assert app.test_client().get('/api/onboarding/status').get_json()['needs_setup'] is False


# ═════════════════════════════════════════════════════════════════════════════
# Deactivation -- and the fact that it is reversible
# ═════════════════════════════════════════════════════════════════════════════

def test_deactivation_is_reversible():
    """The task asks whether the backend supports re-enabling. It does:
    `status` is a plain column and the same route writes both values, so
    "deactivate" is not a delete and nothing about it is one-way."""
    owner = _owner()
    user_id, email, _ = _new_employee(owner)
    _activate(user_id, 'ReversiblePW1')

    assert owner.put(f'/api/admin/employees/{user_id}/status', json={'status': 'disabled'}).status_code == 200
    blocked = app.test_client().post('/api/auth/login', json={'email': email, 'password': 'ReversiblePW1'})
    assert blocked.status_code != 200

    assert owner.put(f'/api/admin/employees/{user_id}/status', json={'status': 'active'}).status_code == 200
    allowed = app.test_client().post('/api/auth/login', json={'email': email, 'password': 'ReversiblePW1'})
    assert allowed.status_code == 200, allowed.get_json()


def test_the_owner_row_offers_no_role_or_status_control():
    """The compensating control for a server-side gap this screen cannot close.

    `update_role` refuses the owner row itself (409). `update_status` does
    NOT: it writes whatever status it is handed, to whatever id it is handed,
    with no owner bar -- and disabling the single owner is unrecoverable,
    because a disabled admin still has a real `password_hash`, so
    `onboarding_status` keeps answering `needs_setup: false` and
    `create-admin` keeps answering 409, while `authenticate_registry_user`
    refuses the login outright. Every route that could undo it sits behind the
    admin session nobody can obtain any more.

    Until that guard exists server-side, the screen must be structurally
    incapable of asking for it -- which is the same standard `update_role`'s
    own docstring sets for the role control ("the UI structurally never offers
    a role control on the owner row"). Asserting the rendered row markup is
    how that stays true through later edits.

    This used to assert that a call to `_isOwnerRow(...)` fed a `?` ternary
    somewhere in the served file, which reads as "the owner predicate feeds
    a ternary somewhere in this file" --
    and it does, just not where the docstring above claims: `_load()`'s sort
    comparator (`return this._isOwnerRow(a) ? -1 : 1`, ordering the owner row
    first) matches that regex too, and matches it BEFORE the pattern ever
    reaches `_row()`'s action cells. Proven by mutation: swapping `_row()`'s
    two `${owner ? '' : ...}` guards for `${false ? '' : ...}` -- which makes
    it render "Change Role" and "Deactivate" ON THE OWNER ROW, the exact
    lockout this test exists to prevent -- left the old assertion GREEN,
    because `_isOwnerRow(a) ?` in the untouched sort comparator still
    satisfied it. A security test that cannot fail when the control it
    guards is deleted is not a test.

    Anchored to the actual action-cell branches instead: the `${owner ? ''
    : ...}` guard immediately preceding the button carrying
    `data-action="role"`, and the one immediately preceding the buttons
    carrying `data-action="status"`, both keyed on the SAME local `owner`
    binding `_row()` computes from `_isOwnerRow(e)`. Neither pattern exists
    anywhere else in the file (the sort comparator uses the method call form,
    `_isOwnerRow(a) ?`, not the local-variable form, `owner ?`), so this
    cannot be satisfied by any branch other than the one it claims to guard.

    (The action buttons carry the id as a `data-id` attribute rather than an
    inline `onclick="...('${id}')"` call -- see C6/_onTableClick in
    employees.js -- which is why this anchors on `data-action="..."` rather
    than on a `_openRole`/`_setStatus` method-call substring.)
    """
    src = _served('/static/employees.js')
    assert '_isOwnerRow' in src, \
        'the row renderer does not distinguish the owner row from an employee row'
    assert re.search(r"owner\s*\?\s*''\s*:\s*`[^`]*data-action=\"role\"", src), \
        'the role control in the row action cell is not guarded by the owner predicate'
    assert re.search(r"owner\s*\?\s*''\s*:\s*\([^)]*data-action=\"status\"", src), \
        'the deactivate control in the row action cell is not guarded by the owner predicate'


def test_an_unknown_employee_is_a_404_on_the_routes_that_check():
    """A route that answers 200 to a write it did not perform teaches the
    screen to show a success toast for nothing. `role` and `pin` both look the
    row up first; `status` does not, which is part of the gap reported above.
    """
    owner = _owner()
    ghost = str(uuid.uuid4())
    assert owner.put(f'/api/admin/employees/{ghost}/role', json={'role': 'cashier'}).status_code == 404
    assert owner.put(f'/api/admin/employees/{ghost}/pin', json={'pin': '1234'}).status_code == 404
    assert owner.delete(f'/api/admin/employees/{ghost}/pin').status_code == 404


# ═════════════════════════════════════════════════════════════════════════════
# PINs -- attribution, never authorization
# ═════════════════════════════════════════════════════════════════════════════

def test_a_pin_is_stored_hashed_and_verifies():
    owner = _owner()
    user_id, _, _ = _new_employee(owner)

    assert owner.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '2468'}).status_code == 200
    stored = _user_row(user_id)['pin_hash']
    assert stored and '2468' not in stored, "the PIN itself must never be stored"

    conn = registry_conn()
    try:
        assert user_accounts.verify_user_pin(conn, user_id, '2468') is True
        assert user_accounts.verify_user_pin(conn, user_id, '2469') is False
    finally:
        conn.close()


def test_a_pin_typed_on_an_arabic_keypad_verifies_from_an_ascii_one():
    """This product ships Arabic and is RTL, and an Arabic soft keyboard emits
    ARABIC-INDIC digits. The route must fold at set-time exactly the way
    `verify_user_pin` folds at verify-time, or a PIN set on the phone can
    never be entered on the desktop till. Digits are built from code points
    rather than pasted as glyphs so this file stays pure LTR source."""
    owner = _owner()
    user_id, _, _ = _new_employee(owner)
    arabic_indic = ''.join(chr(0x0660 + d) for d in (7, 0, 1, 3))  # ٧٠١٣

    assert owner.put(f'/api/admin/employees/{user_id}/pin', json={'pin': arabic_indic}).status_code == 200
    conn = registry_conn()
    try:
        assert user_accounts.verify_user_pin(conn, user_id, '7013') is True
        assert user_accounts.verify_user_pin(conn, user_id, arabic_indic) is True
    finally:
        conn.close()


def test_a_malformed_pin_is_refused_with_the_translated_message():
    owner = _owner()
    user_id, _, _ = _new_employee(owner)
    en = json.loads((LOCALES_DIR / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((LOCALES_DIR / 'ar.json').read_text(encoding='utf-8'))

    for bad in ('123', '12345', 'abcd', '12 4', ''):
        r = owner.put(f'/api/admin/employees/{user_id}/pin', json={'pin': bad})
        assert r.status_code == 400, f'{bad!r} was accepted as a PIN'
        message = r.get_json()['error']
        assert message in en and message in ar, f'not translatable: {message!r}'
        assert ar[message] != en[message]

    assert _user_row(user_id)['pin_hash'] in (None, ''), "a refused PIN must not be stored"


def test_clearing_a_pin_leaves_the_account_able_to_log_in():
    """The rule, tested rather than merely commented: a PIN is not a
    credential. Removing it changes who gets stamped on a row, not whether the
    person can sign in."""
    owner = _owner()
    user_id, email, _ = _new_employee(owner)
    _activate(user_id, 'PinlessPW1')
    assert owner.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '9090'}).status_code == 200

    assert owner.delete(f'/api/admin/employees/{user_id}/pin').status_code == 200
    assert _user_row(user_id)['pin_hash'] in (None, '')

    assert app.test_client().post(
        '/api/auth/login', json={'email': email, 'password': 'PinlessPW1'}
    ).status_code == 200

    conn = registry_conn()
    try:
        assert user_accounts.verify_user_pin(conn, user_id, '9090') is False, \
            "a cleared PIN must match nothing at all"
    finally:
        conn.close()


def test_setting_a_pin_does_not_revoke_the_session():
    """Attribution, not authority: a PIN change grants and removes nothing, so
    logging the shop out over one would be friction with no security in it.
    Contrast `test_a_role_change_revokes_the_employees_live_session` -- the
    difference between the two is the whole distinction design §3 draws."""
    owner = _owner()
    user_id, email, _ = _new_employee(owner)
    _activate(user_id, 'PinKeepsPW1')

    staff = app.test_client()
    assert staff.post('/api/auth/login', json={'email': email, 'password': 'PinKeepsPW1'}).status_code == 200
    before = _user_row(user_id)['session_version']

    assert owner.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '3141'}).status_code == 200

    assert _user_row(user_id)['session_version'] == before
    assert staff.get(AUTH_ONLY_ROUTE).status_code == 200


def test_a_pin_write_still_moves_the_row_version():
    """`users` is an admin-device single-writer table and `row_version` is the
    reject-stale marker a peer compares -- a row that changed without moving
    it is a row the other device will decline to take."""
    owner = _owner()
    user_id, _, _ = _new_employee(owner)
    before = _user_row(user_id)['row_version']
    assert owner.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '5150'}).status_code == 200
    assert _user_row(user_id)['row_version'] > before


# ═════════════════════════════════════════════════════════════════════════════
# Only the owner
# ═════════════════════════════════════════════════════════════════════════════

def test_every_employee_route_refuses_a_non_owner():
    """The screen hides itself for a non-owner, but hiding is not enforcement
    -- these routes are what actually stops a cashier minting themselves a
    manager account with a hand-made request."""
    owner = _owner()
    target_id, _, _ = _new_employee(owner)
    staff_id, staff_email, _ = _new_employee(owner)
    _activate(staff_id, 'NotOwnerPW1')

    staff = app.test_client()
    assert staff.post('/api/auth/login', json={'email': staff_email, 'password': 'NotOwnerPW1'}).status_code == 200

    assert staff.get('/api/admin/employees').status_code == 403
    assert staff.post('/api/admin/employees', json={'email': 'x@y.local'}).status_code == 403
    assert staff.put(f'/api/admin/employees/{target_id}/role', json={'role': 'manager'}).status_code == 403
    assert staff.put(f'/api/admin/employees/{target_id}/status', json={'status': 'disabled'}).status_code == 403
    assert staff.put(f'/api/admin/employees/{target_id}/pin', json={'pin': '1111'}).status_code == 403
    assert staff.delete(f'/api/admin/employees/{target_id}/pin').status_code == 403

    assert _user_row(target_id)['role'] == 'cashier'
    assert _user_row(target_id)['status'] == 'pending_setup'
    assert _user_row(target_id)['pin_hash'] in (None, '')


# ═════════════════════════════════════════════════════════════════════════════
# The screen itself -- asserted against the SERVED assets
# ═════════════════════════════════════════════════════════════════════════════

def _served(path):
    r = app.test_client().get(path)
    assert r.status_code == 200, f'{path} is not served'
    return r.get_data(as_text=True)


def test_the_employees_module_is_served_and_calls_the_real_route():
    """The exact check that fails today: design §3's "nothing in
    products/retail/frontend/ calls /api/admin/employees"."""
    src = _served('/static/employees.js')
    assert '/api/admin/employees' in src
    assert 'window.RetailEmployees' in src


def test_the_shell_loads_the_employees_module_before_it_needs_it():
    """A served-but-unloaded script is the licensing-page bug again: the
    module exists, the router names it, and `window.RetailEmployees` is
    undefined at runtime."""
    html = _served('/static/index.html')
    assert '/static/employees.js' in html
    assert html.index('/static/employees.js') < html.index('/static/app-shell.js'), \
        'employees.js must load before app-shell.js, which is what boots the shell'


def test_the_retail_router_dispatches_the_employees_section():
    src = _served('/static/subsystem-retail.js')
    assert re.search(r"case\s*'employees'\s*:", src), \
        'the section id in the nav has no branch in RetailSystem.render'
    assert 'RetailEmployees' in src


def test_the_nav_entry_is_gated_on_the_owner_role_not_on_the_admin_device():
    """Two different axes, and using the wrong one breaks the feature in both
    directions. `adminOnly` in this file means `this.isAdminDevice` -- the
    DEVICE axis (design §3: "commercial_runtime/identity's device-admin is the
    device axis and is not users.role='admin'"). The routes gate on
    `session['mt_role'] == 'admin'`, the USER axis. Reusing `adminOnly` would
    hide the screen from the owner whenever they are on their phone, and show
    it to a cashier standing at the admin terminal, who would then be 403'd by
    every button on it."""
    src = _served('/static/app-shell.js')
    assert "id: 'employees'" in src, 'no Employees nav entry'
    assert 'ownerOnly' in src, 'no owner-role gate exists in the nav filter'
    assert re.search(r"!item\.ownerOnly\s*\|\|\s*this\.role\s*===\s*'admin'", src), \
        'the ownerOnly flag is declared but the nav filter does not honour it'

    entry = re.search(r"\{[^{}]*id:\s*'employees'[^{}]*\}", src)
    assert entry, 'could not locate the Employees nav entry'
    assert 'ownerOnly' in entry.group(0)
    assert 'adminOnly' not in entry.group(0), \
        'the Employees screen must not hang off the admin-DEVICE flag'


def test_the_screen_degrades_honestly_for_a_non_owner():
    """"Degrade honestly" means it must refuse itself with a reason, not
    render a table and then fill it with a 403. The renderer therefore checks
    the role before it fetches anything."""
    src = _served('/static/employees.js')
    assert re.search(r"SubsystemApp\s*\.\s*role", src) or re.search(r"_isOwner", src), \
        'the renderer never consults the logged-in role'
    assert 'Employee management is available to the store owner only.' in src


def test_the_screen_says_the_invite_link_is_single_use_and_expires():
    """`create_employee` mints a 7-day single-use `secure_links` token. An
    owner who does not know that will paste it into a group chat and wonder
    why the second person cannot use it."""
    src = _served('/static/employees.js')
    assert 'This link works once and expires in 7 days.' in src


def test_the_screen_states_the_pin_rule():
    """Design §3's rule, on the screen where an owner actually sets a PIN --
    the one place it can stop somebody treating four digits as a permission
    system."""
    src = _served('/static/employees.js')
    assert 'A PIN identifies who is acting. It does not grant permission.' in src


def test_the_screen_does_not_invent_a_name_field():
    """`registry.db users` has no `name` column (registry_db.py:113-131) and
    `create_employee` accepts no name -- `create_admin`'s `name` goes to
    config.json and belongs to nobody in the table. A form field that looked
    like it named the person would be discarded in transit, silently."""
    src = _served('/static/employees.js')
    assert not re.search(r"""\bname\s*:\s*(document|this\._val)""", src), \
        'the create payload appears to carry a name the backend will drop'


# ═════════════════════════════════════════════════════════════════════════════
# Localization -- both catalogs, or an Arabic till renders English
# ═════════════════════════════════════════════════════════════════════════════

#: `t('...')` where the `t` is the WHOLE identifier. The lookbehind is not
#: decoration: without it this pattern also matches the tail of `this._get(`,
#: `document.createElement(`, `localStorage.setItem(` and every other
#: identifier ending in "t", so the scan would report `'/api/admin/employees'`
#: and `'div'` as untranslated user-visible strings and the parity test below
#: would fail for reasons that have nothing to do with localization. (Found by
#: running it: those were the first two "missing" keys it reported.)
_T_CALL = re.compile(r"(?<![A-Za-z0-9_$.])t\('((?:[^'\\]|\\.)*)'\)")


def _translated_literals(src):
    return set(_T_CALL.findall(src))


def test_the_screen_only_uses_the_scannable_form_of_the_translation_helper():
    """The scanner reads `t('...')`. Any OTHER call shape hides its string
    from the parity test underneath -- and a parity test that silently skips
    part of the screen is worse than no parity test, because it reports green
    for a page that renders English on an Arabic till.

    Three shapes are therefore banned outright: double quotes, template
    literals, and `t(expr || 'literal')` -- that last one is the tempting one,
    because `t((res && res.error) || 'Could not change the role.')` reads
    perfectly well and buries a user-visible sentence where nothing can see
    it. employees.js routes those through `_fail(res, t('...'))` instead, so
    the fallback is a scannable literal at the call site.
    """
    src = _served('/static/employees.js')
    assert not re.search(r'(?<![A-Za-z0-9_$.])t\("', src), \
        'double-quoted t() calls are invisible to the parity scan'
    assert not re.search(r'(?<![A-Za-z0-9_$.])t\(`', src), \
        'template-literal t() calls are invisible to the parity scan'
    assert not re.search(r"(?<![A-Za-z0-9_$.])t\(\s*\(", src), \
        "t(expr || 'literal') hides the literal from the parity scan"


def test_every_translated_string_on_the_screen_exists_in_both_catalogs():
    en = json.loads((LOCALES_DIR / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((LOCALES_DIR / 'ar.json').read_text(encoding='utf-8'))
    literals = _translated_literals(_served('/static/employees.js'))
    assert literals, 'no translated strings found -- the screen is not going through t()'

    missing_en = sorted(s for s in literals if s not in en)
    missing_ar = sorted(s for s in literals if s not in ar)
    assert missing_en == [], f'missing from en.json: {missing_en}'
    assert missing_ar == [], f'missing from ar.json: {missing_ar}'

    untranslated = sorted(s for s in literals if ar[s] == en[s])
    assert untranslated == [], f'present but not actually translated: {untranslated}'


def test_the_nav_label_and_section_title_are_translated():
    """`_renderShell` and `_navigate` both put the nav entry's `label` through
    `t()`, so a label that is not a catalog key leaves the sidebar and the
    header in English on an Arabic till."""
    en = json.loads((LOCALES_DIR / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((LOCALES_DIR / 'ar.json').read_text(encoding='utf-8'))
    src = _served('/static/app-shell.js')
    entry = re.search(r"\{[^{}]*id:\s*'employees'[^{}]*\}", src)
    assert entry
    label = re.search(r"label:\s*'([^']+)'", entry.group(0)).group(1)
    assert label in en and label in ar
    assert ar[label] != en[label]


def test_the_backend_refusals_an_owner_can_actually_reach_are_translated():
    """The screen renders server `error` text through `t()`, and `t()`
    translates by matching the WHOLE English sentence, so any refusal a real
    owner can trigger must exist as a key in both catalogs or it renders in
    English on an Arabic till.

    "Can actually reach" is the filter, and it is doing work. Two of this
    route family's refusals are deliberately NOT catalog keys -- the owner-row
    409 and the 404 -- because the screen is built so an owner cannot ask for
    either: the owner row renders no role control, and the ids come from a
    list the screen just fetched. They are API-level backstops for a hand-made
    request, and translating a sentence no user can see would be catalog
    weight pretending to be coverage. The four below are the opposite: every
    one of them is a normal Tuesday (empty field, re-inviting somebody who
    already has an account, a three-digit PIN).
    """
    en = json.loads((LOCALES_DIR / 'en.json').read_text(encoding='utf-8'))
    ar = json.loads((LOCALES_DIR / 'ar.json').read_text(encoding='utf-8'))

    owner = _owner()
    user_id, taken_email, _ = _new_employee(owner)

    surfaced = [
        owner.post('/api/admin/employees', json={'email': ''}).get_json()['error'],
        owner.post('/api/admin/employees', json={'email': taken_email}).get_json()['error'],
        owner.post('/api/admin/employees', json={'email': 'x@y.local', 'role': 'wizard'}).get_json()['error'],
        owner.put(f'/api/admin/employees/{user_id}/pin', json={'pin': '1'}).get_json()['error'],
    ]
    for message in surfaced:
        assert message in en, f'missing from en.json: {message!r}'
        assert message in ar, f'missing from ar.json: {message!r}'
        assert ar[message] != en[message], f'not actually translated: {message!r}'
