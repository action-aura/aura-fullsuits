"""Aura Retail -- Phase 0 backend items owned by crew C2 (retail-hardware-
viewports). One file, one section per item, each proven HTTP-driven through
the real Flask routes (never a raw call into the handler function), matching
this directory's own established convention (retail_gl_route_test.py,
retail_balance_sheet_test.py).

Covers:
  #6  (I-9)  create_sale response lines carry the product's own name/
             variant_label, including the idempotent-replay branch's
             existing GET /sales/<id> fetch-detail path.
  #11(b) (I-1) the customer-display relay: POST/GET .../state, GET
             .../status -- in-memory, company+device scoped, the deny half
             (no session -> 401), and "gone on restart".
  #12 (I-3)  branding_settings_get's read-time signup-name fallback.
  #13 (I-4)  default_tax_rate: unset stays 0, set flows into create_product
             and the import, explicit 0 is never promoted.
  #15(d) (I-5) trial-balance `?as_of=` filtering and the new Closing Dr/Cr
             columns.
  #15(e) (I-6) GET /products/stock-value.
  #17 (I-7)  audit log `actor_name` resolution (and the confirmed
             `audit_log.user_id` == `users.id`, NOT `users.uid`, trap).
  #18 (I-8)  GET /ai/status.

Self-contained bootstrap, matching retail_gl_route_test.py's own convention
(no shared conftest.py exists for products/retail/tests/). One company per
test via the `client` fixture (role='admin' -- `session['mt_role']=='admin'`
bypasses every `@mt_require_capability`/`_require_company_admin` check, per
mt_auth.py's own documented admin-bypass, so this one fixture covers every
capability this file's items need with no per-test permission seeding).

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- this file
pollutes and is polluted by any other retail_*_test.py sharing a process;
run it alone, matching products/run_all_tests.py's own convention.

MUTATION PROOFS -- performed BY HAND against the exact lines this crew's
report names, each restored and reconfirmed green immediately after
capturing the RED output. Quoted in the crew's structured report, not
duplicated here (matching retail_balance_sheet_test.py's own convention of
keeping the proof log in the file that introduced it -- this file's own log
lives in the report the harness reads, since it is produced fresh on every
run of this session rather than accumulated release over release).

Run:
    pytest products/retail/tests/retail_p0_backend_test.py -q
"""
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_p0_backend_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402
import api.retail_api as retail_api_module  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture(autouse=True)
def _reset_customer_display_state():
    """Every test gets a clean relay -- state is process-global (interface
    I-1's own design: in-memory, keyed by company+device, gone on restart),
    and this shared pytest process runs many tests/companies. Clearing
    before AND after is belt-and-braces: a test that fails mid-assertion
    still leaves the next one clean."""
    retail_api_module._CUSTOMER_DISPLAY_STATE.clear()
    retail_api_module._CUSTOMER_DISPLAY_LAST_SEEN.clear()
    yield
    retail_api_module._CUSTOMER_DISPLAY_STATE.clear()
    retail_api_module._CUSTOMER_DISPLAY_LAST_SEEN.clear()


@pytest.fixture
def client():
    email = f'p0be-{uuid.uuid4().hex[:8]}@test.local'
    password = 'P0BackendPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'))
    conn.commit(); conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id

    # doc_sequences carries a bare (not company-scoped) UNIQUE constraint on
    # (company_id, doc_type) -- fine on its own, but `sales.sale_number` also
    # carries a bare UNIQUE (see retail_returns_wave0_test.py's identical
    # comment), so every fresh company in this shared temp DB must start its
    # own sale numbering from a random point or collide with another test's
    # first "SALE-000001".
    import random as _random
    rconn = get_retail_conn()
    try:
        rconn.execute(
            "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
            (company_id, _random.randint(1, 5_000_000)),
        )
        rconn.commit()
    finally:
        rconn.close()
    return c


def _seed_product(c, **kw):
    payload = {'name': 'P0 Widget', 'sku': f'P0W-{uuid.uuid4().hex[:8]}', 'cost_price': 1.0,
               'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 100}
    payload.update(kw)
    r = c.post(f'{API}/products', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sell(c, pid, **kw):
    payload = {'items': [{'product_id': pid, 'quantity': 1}],
               'payment_method': 'cash', 'amount_paid': 999999,
               'idempotency_key': str(uuid.uuid4())}
    payload.update(kw)
    r = c.post(f'{API}/sales', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


# ═════════════════════════════════════════════════════════════════════════
# #6 (I-9) -- receipt names: create_sale's response lines carry the
# product's own name/variant_label, never just the id.
# ═════════════════════════════════════════════════════════════════════════

def test_sale_response_lines_carry_product_name(client):
    pid = _seed_product(client, name='Espresso Machine')
    data = _sell(client, pid)
    assert data['lines'], data
    line = data['lines'][0]
    assert line['name'] == 'Espresso Machine', line
    assert 'variant_label' in line, line
    assert line['variant_label'] is None, (
        "an ordinary, non-variant product must carry variant_label=None, "
        f"not a missing key: {line!r}")


def test_sale_response_lines_carry_variant_label_for_a_variant_product(client):
    """A VARIANT's line must carry both the parent's shared name AND its own
    variant_label -- the exact 'Polo Shirt' + 'Red / L' shape this item's own
    bug report describes (a variant sold with no size/colour shown)."""
    parent_id = _seed_product(client, name='Polo Shirt', initial_stock=0)
    child = client.post(f'{API}/products', json={
        'name': 'Polo Shirt', 'sku': f'P0W-VAR-{uuid.uuid4().hex[:8]}',
        'sell_price': 20.0, 'tax_rate': 0, 'initial_stock': 5,
        'parent_product_id': parent_id, 'variant_label': 'Red / L',
    })
    assert child.status_code == 200, child.get_json()
    child_id = child.get_json()['data']['id']

    data = _sell(client, child_id)
    line = data['lines'][0]
    assert line['name'] == 'Polo Shirt', line
    assert line['variant_label'] == 'Red / L', line


def test_a_four_item_sale_names_every_line_and_none_is_the_bare_id():
    """The probe's own reproduction: 4 items, 4 names, zero '#<uuid>'
    fallbacks. A fresh client/company per call (this helper does not take
    the shared `client` fixture) -- deliberately independent of the other
    tests in this file."""
    email = f'p0be-4line-{uuid.uuid4().hex[:8]}@test.local'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password('P0BackendPW1'), 'admin', 'active'))
    conn.commit(); conn.close()
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': email, 'password': 'P0BackendPW1'}).status_code == 200
    import random as _random
    rconn = get_retail_conn()
    rconn.execute("INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
                  (company_id, _random.randint(1, 5_000_000)))
    rconn.commit(); rconn.close()

    names = ['Milk 1L', 'Bread Loaf', 'Eggs Dozen', 'Butter 250g']
    pids = [_seed_product(c, name=n) for n in names]
    r = c.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1} for pid in pids],
        'payment_method': 'cash', 'amount_paid': 999999,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    lines = r.get_json()['data']['lines']
    assert len(lines) == 4, lines
    assert {ln['name'] for ln in lines} == set(names)
    import re as _re
    for ln in lines:
        assert not _re.search(r'#[0-9a-f]{8}-[0-9a-f]{4}-', ln['name']), ln


def test_idempotent_replay_still_returns_only_id_and_sale_number(client):
    """Pre-existing, deliberate shape -- unchanged by this item. The full
    receipt (with names) for a RETRIED request comes from the client
    fetching GET /sales/<id> afterwards (already correct -- see that
    route's own `p.name as product_name` join), not from widening this
    narrow replay response."""
    pid = _seed_product(client)
    idem = str(uuid.uuid4())
    first = _sell(client, pid, idempotency_key=idem)
    r2 = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 999999, 'idempotency_key': idem})
    assert r2.status_code == 200, r2.get_json()
    replay = r2.get_json()['data']
    assert set(replay.keys()) == {'id', 'sale_number'}, replay
    assert replay['id'] == first['id']

    detail = client.get(f'{API}/sales/{first["id"]}')
    assert detail.status_code == 200, detail.get_json()
    assert detail.get_json()['data']['items'][0]['product_name']


# ═════════════════════════════════════════════════════════════════════════
# #11(b) (I-1) -- the customer-display relay
# ═════════════════════════════════════════════════════════════════════════

def test_relay_round_trip_and_status_dot(client):
    message = {'type': 'cart', 'items': [{'name': 'Widget', 'quantity': 1,
               'unitPrice': '10.000', 'lineTotal': '10.000'}],
               'subtotal': '10.000', 'discount': None, 'tax': '0.000',
               'total': '10.000', 'loyalty': 0, 'amountDue': '10.000'}
    shop = {'name': 'Sweets & Co', 'lang': 'en', 'theme': 'light'}

    status0 = client.get(f'{API}/customer-display/status')
    assert status0.status_code == 200, status0.get_json()
    assert status0.get_json()['display_last_seen_ms_ago'] is None, (
        "never-polled display must report null, not 0")

    post = client.post(f'{API}/customer-display/state', json={'message': message, 'shop': shop})
    assert post.status_code == 200, post.get_json()
    body = post.get_json()
    assert body['ok'] is True
    assert body['seq'] == 1

    get1 = client.get(f'{API}/customer-display/state?since=0')
    assert get1.status_code == 200, get1.get_json()
    g1 = get1.get_json()
    assert g1['seq'] == 1
    assert g1['message'] == message
    assert g1['shop'] == shop

    # The display just polled (get1 above) -- status must now report a real,
    # tiny ms figure, never null.
    status1 = client.get(f'{API}/customer-display/status')
    assert status1.get_json()['display_last_seen_ms_ago'] is not None


def test_get_state_returns_204_when_no_newer_message(client):
    """TRAP named explicitly in this item's own text."""
    fresh = client.get(f'{API}/customer-display/state?since=0')
    assert fresh.status_code == 204, fresh.get_data(as_text=True)

    client.post(f'{API}/customer-display/state', json={
        'message': {'type': 'cart'}, 'shop': {}})
    stale = client.get(f'{API}/customer-display/state?since=1')
    assert stale.status_code == 204, stale.get_data(as_text=True)

    caught_up = client.get(f'{API}/customer-display/state?since=0')
    assert caught_up.status_code == 200


def test_a_second_post_bumps_the_sequence_and_replaces_the_message(client):
    client.post(f'{API}/customer-display/state', json={'message': {'type': 'cart', 'n': 1}, 'shop': {}})
    r2 = client.post(f'{API}/customer-display/state', json={'message': {'type': 'sale_complete', 'n': 2}, 'shop': {}})
    assert r2.get_json()['seq'] == 2

    latest = client.get(f'{API}/customer-display/state?since=1')
    assert latest.status_code == 200
    assert latest.get_json()['message'] == {'type': 'sale_complete', 'n': 2}
    assert latest.get_json()['seq'] == 2


def test_status_polling_never_itself_counts_as_the_display_being_seen(client):
    """Deliberately distinct from `.../state` GET: the TILL polls `.../status`
    to READ whether the display is alive; it must not make the till look like
    the display."""
    for _ in range(3):
        client.get(f'{API}/customer-display/status')
    assert client.get(f'{API}/customer-display/status').get_json()['display_last_seen_ms_ago'] is None


def test_relay_routes_require_a_session_the_deny_half(client):
    """TRAP named explicitly in this item's own text: 'a context without
    cookies gets 401 and stays idle'. Real till posts a message first, so a
    would-be leak is not vacuous (there is something to fail to see)."""
    client.post(f'{API}/customer-display/state', json={'message': {'type': 'cart'}, 'shop': {}})

    anon = app.test_client()  # no login at all -- a genuinely separate context
    assert anon.post(f'{API}/customer-display/state', json={'message': {'type': 'cart'}, 'shop': {}}).status_code == 401
    assert anon.get(f'{API}/customer-display/state?since=0').status_code == 401
    assert anon.get(f'{API}/customer-display/status').status_code == 401


def test_relay_state_is_gone_after_a_simulated_restart(client):
    """In-memory, no schema change (interface I-1's own text): a real process
    restart empties the module-level dicts this relay is built on, which is
    exactly what this test does directly rather than actually killing the
    process. Checked BEFORE the post-restart `.../state` poll below, not
    after: that poll is itself a GET on `.../state`, which -- correctly,
    by design -- re-records 'seen' the instant it runs, so asserting
    `display_last_seen_ms_ago is None` AFTER it would fail for the RIGHT
    code doing the WRONG thing in the test, not a real defect."""
    client.post(f'{API}/customer-display/state', json={'message': {'type': 'cart'}, 'shop': {}})
    assert client.get(f'{API}/customer-display/state?since=0').status_code == 200

    retail_api_module._CUSTOMER_DISPLAY_STATE.clear()
    retail_api_module._CUSTOMER_DISPLAY_LAST_SEEN.clear()

    assert client.get(f'{API}/customer-display/status').get_json()['display_last_seen_ms_ago'] is None, (
        "last-seen tracking must not survive a restart either")

    after_restart = client.get(f'{API}/customer-display/state?since=0')
    assert after_restart.status_code == 204, (
        "state must not survive a restart -- got a message the relay should have forgotten")


def test_relay_survives_a_restart_for_a_display_that_stayed_open():
    """P0 item #11(routes) reviewer finding: a display that stayed OPEN
    across the restart (the Edge/Chrome --app fallback, or any browser tab
    left running) keeps its OLD `_lastSeq` and keeps polling with it -- it
    never re-sends `since=0`. Before the fix, `current['seq'] <= since` was
    true for since=3 against a freshly-restarted process's seq=1, so this
    exact case got 204 forever, and every one of those 204s still counted as
    'seen' (the display's own dot showed green on a frozen screen). This is
    the scenario `test_relay_state_is_gone_after_a_simulated_restart` just
    above does not cover -- that test polls with the CORRECT post-restart
    `since=0`, which was always fine; this one polls with the STALE
    pre-restart `since`, which was the actual bug.

    P0 round 2 (item B4, verifier finding on the test itself, not the
    fix): the ORIGINAL version of this test simulated "a backend restart"
    by calling `retail_api_module._CUSTOMER_DISPLAY_STATE.clear()` on the
    SAME process's module-level dict -- contracts.md rule 6's "run the real
    thing, never by reading source text or standing in for the real
    boundary" applies just as much to a fixture that manufactures a
    same-process facsimile of a restart as it does to reading code instead
    of running it (ENGINEERING.md failure shape 2: the FIXTURE can quietly
    make the very state that would hide a real defect -- here, hide a bug
    that only exists when GENUINE Python module globals are reinitialized,
    which a dict `.clear()` call cannot exercise: it proves the ROUTE
    handles an empty dict, not that a real process boundary is safe).
    It also baked in the CURRENT fix's own implementation detail (a bare
    `.clear()` always leaves the very next `seq` at 1) as if it were a
    requirement, when the actual fix does not need it: `customer_display_
    get_state`'s `since == current['seq']` equality check (rather than
    `<=`) resyncs a stale caller regardless of what the new process's first
    `seq` happens to be.

    So this version spawns a GENUINE second OS process -- a fresh Python
    interpreter, a fresh `import api.retail_api` and therefore a fresh
    `_CUSTOMER_DISPLAY_STATE = {}` at module scope, no dict-clearing
    anywhere in it -- against the SAME `AURA_APP_DATA` this file's own
    bootstrap already licensed and migrated. That mirrors what a real
    desktop-app restart actually is (same data directory on disk, a brand
    new process on top of it): the on-disk SQLite files (`users`,
    `retail_settings`, ...) persist across that boundary and the in-memory
    relay dicts do not, by construction -- which is the exact asymmetry
    `customer_display_get_state`'s fix has to be correct about. Uses its
    own standalone user/company (not the shared `client` fixture) so the
    subprocess only needs a fresh login, matching
    `test_a_four_item_sale_names_every_line_and_none_is_the_bare_id`'s own
    "independent of the fixture" convention a few sections above.
    """
    email = f'p0be-restart-{uuid.uuid4().hex[:8]}@test.local'
    password = 'P0BackendPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'))
    conn.commit(); conn.close()

    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': email, 'password': password}).status_code == 200

    for n in range(1, 4):
        r = c.post(f'{API}/customer-display/state', json={'message': {'type': 'cart', 'n': n}, 'shop': {}})
        assert r.get_json()['seq'] == n
    stale_since = 3  # the display's own remembered _lastSeq, from before the restart

    state_path = f'{API}/customer-display/state'
    stuck_path = f'{API}/customer-display/state?since={stale_since}'
    child_script = (
        "import os, sys, json\n"
        f"sys.path.insert(0, {str(SUITE_ROOT)!r})\n"
        f"sys.path.insert(0, {str(BACKEND_DIR)!r})\n"
        f"os.environ.update(AURA_STANDALONE='1', AURA_BUNDLE_DIR={str(BACKEND_DIR)!r}, AURA_APP_DATA={str(DATA)!r})\n"
        "os.environ.pop('AURA_DEV', None)\n"
        "os.environ.pop('AURA_RETAIL_DEMO_MODE', None)\n"
        # A brand-new `import app` in THIS process is the whole point: it
        # re-runs `api.retail_api`'s module body from scratch, giving it a
        # fresh, empty `_CUSTOMER_DISPLAY_STATE` -- never touched, cleared,
        # or otherwise reached into from here.
        "import app as _app_module2\n"
        "app2 = _app_module2.init_app()\n"
        "app2.config['TESTING'] = True\n"
        "c2 = app2.test_client()\n"
        f"login = c2.post('/api/auth/login', json={{'email': {email!r}, 'password': {password!r}}})\n"
        "assert login.status_code == 200, login.get_data(as_text=True)\n"
        f"post = c2.post({state_path!r}, "
        "json={'message': {'type': 'cart', 'n': 'after restart'}, 'shop': {}})\n"
        "assert post.status_code == 200, post.get_data(as_text=True)\n"
        f"stuck = c2.get({stuck_path!r})\n"
        "print(json.dumps({'post_seq': post.get_json()['seq'], "
        "'stuck_status': stuck.status_code, "
        "'stuck_body': stuck.get_json() if stuck.status_code == 200 else None}))\n"
    )
    result = subprocess.run([sys.executable, '-c', child_script], capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, (
        "the child process (a genuine second backend launch against the same "
        f"AURA_APP_DATA) crashed instead of restarting cleanly:\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr[-4000:]!r}"
    )
    out_lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
    assert out_lines, f"the child process printed nothing; stderr={result.stderr[-4000:]!r}"
    out = json.loads(out_lines[-1])

    # Deliberately NOT asserted to be any specific number (see the docstring
    # above) -- a genuine new process's first seq is whatever the app
    # computes it to be; the fix must not, and does not, depend on it being
    # exactly 1.
    assert isinstance(out['post_seq'], int) and out['post_seq'] >= 1, out

    assert out['stuck_status'] == 200, (
        "a display polling with a since value from BEFORE a GENUINE backend "
        f"restart (a real new process) must resync (200), not 204 forever: {out!r}"
    )
    assert out['stuck_body']['message'] == {'type': 'cart', 'n': 'after restart'}, out


def test_relay_post_rejects_an_oversized_body(client):
    """Reviewer finding (severity high): customer_display_set_state had no
    shape or size validation of any kind and requires only CAP_SELL, the
    base capability every cashier holds -- an authenticated cashier session
    could tie up threads/memory on the same waitress process (12 threads
    total) that must also serve create_sale. Checked from BOTH the
    Content-Length header (never reaches `request.json`) and the decoded
    shape's own re-serialized size (the backstop for a body sent without
    that header) -- see `_CUSTOMER_DISPLAY_MAX_BYTES`'s own comment."""
    huge_name = 'x' * (retail_api_module._CUSTOMER_DISPLAY_MAX_BYTES + 1)
    r = client.post(f'{API}/customer-display/state', json={
        'message': {'type': 'cart', 'junk': huge_name}, 'shop': {},
    })
    assert r.status_code == 413, (r.status_code, r.get_json())

    # The allow half: an ordinary cart-sized payload still sails through --
    # the cap must not be so tight it breaks a real till.
    ordinary = client.post(f'{API}/customer-display/state', json={
        'message': {'type': 'cart', 'items': [{'name': 'Widget', 'quantity': 1,
                    'unitPrice': '10.000', 'lineTotal': '10.000'}] * 20,
                    'subtotal': '200.000', 'total': '200.000'},
        'shop': {'name': 'Sweets & Co', 'lang': 'en', 'theme': 'light'},
    })
    assert ordinary.status_code == 200, ordinary.get_json()

    # And the state was genuinely never written for the refused request --
    # not "refused after the write", which would not be a gate at all.
    conn = get_retail_conn()
    conn.close()  # no table backs this relay (in-memory, interface I-1) --
    # the observable proof is that a subsequent GET never sees the oversized
    # junk payload, since the ordinary POST above is what actually landed.
    latest = client.get(f"{API}/customer-display/state?since=0")
    assert 'junk' not in json.dumps(latest.get_json())


def _sized_arabic_relay_payload(target_bytes):
    """Grows an Arabic cart's filler field until the payload's REAL UTF-8
    byte size -- measured EXACTLY the way the fixed route now measures it
    (`json.dumps(..., ensure_ascii=False, separators=(',', ':'))
    .encode('utf-8')`) -- is as close as possible to `target_bytes`, then
    trims back to strictly under it. Arabic codepoints are 2 bytes each in
    real UTF-8 but 6 ASCII characters each once escaped (`\\uXXXX`) -- the
    ~3x inflation the round-2 finding measured (a 41,899-byte real cart
    refused by a cap sized for ~64KB of ascii-escaped text). Self-
    calibrating rather than a hand-guessed ratio, so the test proves
    something about the ACTUAL measurement the route performs, not about
    an assumed expansion factor."""
    message = {'type': 'cart', 'items': [], 'subtotal': '0.000', 'total': '0.000', 'filler': ''}
    shop = {'name': 'ملابس وإكسسوارات', 'lang': 'ar', 'theme': 'sand'}
    unit = 'سلعة عربية طويلة الاسم بعدد كبير من الأحرف '

    def _real_utf8_bytes():
        return len(json.dumps({'message': message, 'shop': shop},
                               ensure_ascii=False, separators=(',', ':')).encode('utf-8'))

    while _real_utf8_bytes() < target_bytes:
        message['filler'] += unit
    while _real_utf8_bytes() > target_bytes:
        message['filler'] = message['filler'][:-1]
    payload = {'message': message, 'shop': shop}
    # Sent with the SAME encoding used to calibrate `target_bytes`, so the
    # wire's real Content-Length (the FIRST, already-correct check) and
    # this route's own internal re-measurement (the SECOND, fixed-by-V3
    # check) are both exercised against the identical real byte count --
    # exactly what a real till's `fetch(..., {body: JSON.stringify(...)})`
    # sends (no ascii-escaping), never a contrived encoding.
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return payload, body


def test_relay_post_allows_an_arabic_cart_measured_in_real_utf8_bytes(client):
    """P0 round 3 (item V3, reviewer finding): before this fix, the relay's
    internal re-measurement (`_measured_bytes`) re-serialized the ALREADY-
    DECODED payload with Python's DEFAULT `ensure_ascii=True`, inflating
    every Arabic character ~3x -- so a cart whose REAL wire size (and
    therefore real Content-Length) was comfortably under the cap could
    still be refused by this second, purely-internal check. Calibrated to
    just under the cap in REAL bytes so the old bug (which only ever
    OVER-counts) would have refused it, and the fix accepts it."""
    cap = retail_api_module._CUSTOMER_DISPLAY_MAX_BYTES
    payload, body = _sized_arabic_relay_payload(cap - 500)
    assert len(body) < cap, (
        f"fixture sanity: the real UTF-8 body must itself be under the cap, got {len(body)}")

    r = client.post(f'{API}/customer-display/state', data=body,
                     content_type='application/json; charset=utf-8')
    assert r.status_code == 200, (
        f"a real UTF-8 Arabic cart of {len(body)} bytes (cap is {cap}) was refused: "
        f"{r.status_code} {r.get_data(as_text=True)!r}"
    )

    # The state genuinely landed -- not a 200 that silently dropped it.
    latest = client.get(f'{API}/customer-display/state?since=0')
    assert latest.status_code == 200, latest.get_json()
    assert latest.get_json()['message']['filler'] == payload['message']['filler']


def test_relay_post_still_refuses_an_arabic_cart_just_over_the_real_cap(client):
    """The deny half (contracts rule 8: prove both directions): a cart whose
    REAL UTF-8 size is genuinely over the cap must still be refused after
    V3 -- fixing the measurement to real bytes must not turn the cap into a
    no-op for legitimately oversized Arabic content."""
    cap = retail_api_module._CUSTOMER_DISPLAY_MAX_BYTES
    payload, body = _sized_arabic_relay_payload(cap + 500)
    assert len(body) > cap, (
        f"fixture sanity: the real UTF-8 body must itself be over the cap, got {len(body)}")

    r = client.post(f'{API}/customer-display/state', data=body,
                     content_type='application/json; charset=utf-8')
    assert r.status_code == 413, (len(body), cap, r.status_code, r.get_data(as_text=True))

    # And it was never written -- the oversized cart must not reach a
    # display polling right after the refusal.
    after = client.get(f'{API}/customer-display/state?since=0')
    assert after.status_code == 204, (
        f"an over-cap POST must never be stored: {after.status_code} {after.get_data(as_text=True)!r}")


def test_relay_state_does_not_leak_between_companies(client):
    """The (company_id, device) key's company half, proven directly: a
    second company sharing this same process (and therefore the same
    `local_terminal_id()`) must never see the first company's posted cart."""
    client.post(f'{API}/customer-display/state', json={'message': {'type': 'cart', 'mine': True}, 'shop': {}})

    email = f'p0be-other-{uuid.uuid4().hex[:8]}@test.local'
    other_cid = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), other_cid, 'EMP-0001', email, hash_password('P0BackendPW1'), 'admin', 'active'))
    conn.commit(); conn.close()
    other = app.test_client()
    assert other.post('/api/auth/login', json={'email': email, 'password': 'P0BackendPW1'}).status_code == 200

    leaked = other.get(f'{API}/customer-display/state?since=0')
    assert leaked.status_code == 204, (
        f"company B must never see company A's relay state, got {leaked.get_json()!r}")


# ═════════════════════════════════════════════════════════════════════════
# #12 (I-3) -- shop name read-time fallback
# ═════════════════════════════════════════════════════════════════════════

def _write_config_company_name(name):
    import json as _json
    with open(DATA / 'config.json', 'w', encoding='utf-8') as f:
        _json.dump({'company_id': name}, f)


def test_branding_falls_back_to_signup_name_when_branding_is_empty(client):
    _write_config_company_name('Sweets & Co')
    r = client.get(f'{API}/settings/branding')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['branding_business_name'] == '', (
        "the fallback must be READ-TIME only -- the stored value stays empty")
    assert data['business_name'] == 'Sweets & Co', data
    assert data['business_name_source'] == 'signup', data


def test_branding_business_name_wins_over_signup_name(client):
    _write_config_company_name('Sweets & Co')
    r = client.post(f'{API}/settings/branding', json={'branding_business_name': 'Aura Retail Demo'})
    assert r.status_code == 200, r.get_json()

    got = client.get(f'{API}/settings/branding')
    data = got.get_json()['data']
    assert data['business_name'] == 'Aura Retail Demo', data
    assert data['business_name_source'] == 'branding', data


def test_branding_reports_none_when_neither_branding_nor_signup_name_exists(client):
    _write_config_company_name('')
    r = client.get(f'{API}/settings/branding')
    data = r.get_json()['data']
    assert data['business_name'] == '', data
    assert data['business_name_source'] == 'none', data


# ═════════════════════════════════════════════════════════════════════════
# #13 (I-4) -- default_tax_rate
# ═════════════════════════════════════════════════════════════════════════

def test_default_tax_rate_reports_null_when_unset(client):
    r = client.get(f'{API}/settings/tax')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['default_tax_rate'] is None


def test_unset_default_tax_rate_new_product_stores_zero(client):
    pid = _seed_product(client, name='No Tax Yet')
    del_data = client.get(f'{API}/products')
    row = next(p for p in del_data.get_json()['data'] if p['id'] == pid)
    assert row['tax_rate'] == 0, row


def test_setting_default_tax_rate_flows_into_a_new_product_without_tax_rate(client):
    setr = client.post(f'{API}/settings/tax', json={'default_tax_rate': 16})
    assert setr.status_code == 200, setr.get_json()
    assert setr.get_json()['data']['default_tax_rate'] == 16.0

    got = client.get(f'{API}/settings/tax')
    assert got.get_json()['data']['default_tax_rate'] == 16.0

    r = client.post(f'{API}/products', json={
        'name': 'Jordan-Rated Item', 'sku': f'P0W-{uuid.uuid4().hex[:8]}',
        'sell_price': 10.0, 'initial_stock': 5})  # NOTE: no tax_rate key at all
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()['data']['id']
    row = next(p for p in client.get(f'{API}/products').get_json()['data'] if p['id'] == pid)
    assert row['tax_rate'] == 16.0, row


def test_default_tax_rate_flows_into_the_sync_outbox_create_payload_too(client):
    """Reviewer finding (severity high): create_product stores the RESOLVED
    tax_rate locally (proven above), but its own sync_outbox 'create' event
    used to send the raw `data.get('tax_rate', 0)` -- 0 for every product
    whose request omitted the key, which is the normal case a shop relies on
    the default for. Every peer device (phone, second till) therefore
    stored tax_rate 0 and sold the item untaxed -- item #13's day-one
    defect, reaching every OTHER device even after the local fix."""
    assert client.post(f'{API}/settings/tax', json={'default_tax_rate': 16}).status_code == 200

    r = client.post(f'{API}/products', json={
        'name': 'Synced At The Default', 'sku': f'P0W-{uuid.uuid4().hex[:8]}',
        'sell_price': 10.0, 'initial_stock': 5})  # NOTE: no tax_rate key at all
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()['data']['id']

    conn = get_retail_conn()
    row = conn.execute(
        "SELECT payload FROM sync_outbox WHERE entity_type='product' AND entity_id=? "
        "AND event_type='create' ORDER BY id DESC LIMIT 1", (pid,)
    ).fetchone()
    conn.close()
    assert row is not None, "create_product did not queue a sync_outbox 'create' event for this product"
    payload = json.loads(row['payload'])
    assert payload['tax_rate'] == 16.0, (
        f"the sync payload must carry the RESOLVED default tax rate, not the raw request field: {payload!r}")


def test_explicit_zero_tax_rate_is_never_promoted_to_the_default():
    """THE ALLOW/DENY PAIR THIS ITEM'S OWN TEXT ASKS FOR, in one test: a shop
    default of 16 must not overtax a product that explicitly typed 0
    (zero-rated). Independent client/company (module state from another test
    setting default_tax_rate=16 must not leak in, and shouldn't -- proven by
    setting it fresh here too)."""
    email = f'p0be-zerorate-{uuid.uuid4().hex[:8]}@test.local'
    cid = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), cid, 'EMP-0001', email, hash_password('P0BackendPW1'), 'admin', 'active'))
    conn.commit(); conn.close()
    c = app.test_client()
    assert c.post('/api/auth/login', json={'email': email, 'password': 'P0BackendPW1'}).status_code == 200
    assert c.post(f'{API}/settings/tax', json={'default_tax_rate': 16}).status_code == 200

    r = c.post(f'{API}/products', json={
        'name': 'Zero Rated Item', 'sku': f'P0W-{uuid.uuid4().hex[:8]}',
        'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 5})
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()['data']['id']
    row = next(p for p in c.get(f'{API}/products').get_json()['data'] if p['id'] == pid)
    assert row['tax_rate'] == 0, (
        f"an explicit tax_rate=0 must stay 0 even with a nonzero shop default, got {row['tax_rate']}")


def _csv_bytes(rows, headers):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader()
    for row in rows:
        w.writerow(row)
    return buf.getvalue().encode('utf-8')


def test_import_uses_default_tax_rate_for_a_blank_cell_and_keeps_an_explicit_zero(client):
    """import_api.py's own two write sites (the update-existing-SKU path and
    the create-new-SKU path) both need this -- a shop that imports its whole
    catalogue with the Tax Rate column blank on most rows must not sell at
    0.000 across the board just because a default was never asked at import
    time."""
    assert client.post(f'{API}/settings/tax', json={'default_tax_rate': 16}).status_code == 200

    sku_blank = f'IMP-{uuid.uuid4().hex[:8]}'
    sku_zero = f'IMP-{uuid.uuid4().hex[:8]}'
    body = _csv_bytes([
        {'Product Name': 'Imported No Tax Cell', 'SKU': sku_blank, 'Selling Price': '10', 'Tax Rate': ''},
        {'Product Name': 'Imported Zero Rated', 'SKU': sku_zero, 'Selling Price': '10', 'Tax Rate': '0'},
    ], ['Product Name', 'SKU', 'Selling Price', 'Tax Rate'])
    r = client.post('/api/import/execute', data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price","tax_rate":"Tax Rate"}',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['imported'] == 2, r.get_json()

    conn = get_retail_conn()
    rows = {row['sku']: row['tax_rate'] for row in conn.execute(
        "SELECT sku, tax_rate FROM products WHERE company_id=? AND sku IN (?,?)",
        (client.test_company_id, sku_blank, sku_zero)).fetchall()}
    conn.close()
    assert float(rows[sku_blank]) == 16.0, (
        f"a blank Tax Rate cell must inherit the shop default, got {rows!r}")
    assert float(rows[sku_zero]) == 0.0, (
        f"an explicit 0 in the sheet must stay 0, got {rows!r}")


def test_import_new_sku_sync_payload_carries_the_default_tax_rate_too(client):
    """Reviewer finding (severity high), import_api.py's mirror of the
    create_product fix above: the new-SKU create path's own sync_outbox
    payload used to send the raw `rec.get('tax_rate') or 0` instead of
    `_resolve_import_tax_rate(rec, default_tax_rate)` -- the same value the
    INSERT two lines above it already uses. A bulk-imported catalogue with
    the Tax Rate column blank on most rows therefore reached every OTHER
    device at tax_rate 0, even though this device's own products table held
    the correct default."""
    assert client.post(f'{API}/settings/tax', json={'default_tax_rate': 16}).status_code == 200

    sku_blank = f'IMP-{uuid.uuid4().hex[:8]}'
    body = _csv_bytes([
        {'Product Name': 'Imported Sync No Tax Cell', 'SKU': sku_blank, 'Selling Price': '10', 'Tax Rate': ''},
    ], ['Product Name', 'SKU', 'Selling Price', 'Tax Rate'])
    r = client.post('/api/import/execute', data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price","tax_rate":"Tax Rate"}',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['imported'] == 1, r.get_json()

    conn = get_retail_conn()
    pid = conn.execute(
        "SELECT id FROM products WHERE company_id=? AND sku=?", (client.test_company_id, sku_blank)
    ).fetchone()['id']
    row = conn.execute(
        "SELECT payload FROM sync_outbox WHERE entity_type='product' AND entity_id=? "
        "AND event_type='create' ORDER BY id DESC LIMIT 1", (pid,)
    ).fetchone()
    conn.close()
    assert row is not None, "the importer did not queue a sync_outbox 'create' event for this new SKU"
    payload = json.loads(row['payload'])
    assert payload['tax_rate'] == 16.0, (
        f"the import's sync payload must carry the RESOLVED default tax rate, not the raw sheet cell: {payload!r}")


def test_reimporting_an_existing_sku_with_no_tax_column_does_not_overwrite_its_stored_rate(client):
    """P0 round 2 (item #13 sibling, adversarial-verifier finding): the test
    above (`test_import_uses_default_tax_rate_for_a_blank_cell_and_keeps_an_
    explicit_zero`) claims in its own docstring to cover "the
    update-existing-SKU path", but its fixture only ever imports BRAND NEW
    skus (fresh uuids every time) -- the UPDATE branch of
    `_handle_retail_products` was never actually exercised, an
    ENGINEERING.md failure-shape-2 vacuous guard. With the shop default at
    16, RE-importing an EXISTING product stored at tax_rate 0 (e.g.
    zero-rated bread) with NO tax column at all in the sheet used to
    silently retax it to 16 -- the existing-row UPDATE wrote
    `_resolve_import_tax_rate(rec, default_tax_rate)` unconditionally,
    correct for a brand-new SKU (the INSERT path) but wrong for a row that
    already carries a real, operator-set value. items.md #13 promises
    "Existing products are untouched"."""
    assert client.post(f'{API}/settings/tax', json={'default_tax_rate': 16}).status_code == 200
    sku = f'IMP-EXIST-{uuid.uuid4().hex[:8]}'
    pid = _seed_product(client, name='Zero Rated Bread', sku=sku, sell_price=1.25, tax_rate=0)

    body = _csv_bytes([
        {'Product Name': 'Zero Rated Bread', 'SKU': sku, 'Selling Price': '1.25'},
    ], ['Product Name', 'SKU', 'Selling Price'])
    r = client.post('/api/import/execute', data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get('updated') == 1, r.get_json()

    conn = get_retail_conn()
    row = conn.execute("SELECT tax_rate FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    assert float(row['tax_rate']) == 0.0, (
        f"re-importing with NO tax column must not retax an existing product at the shop default, "
        f"got {row['tax_rate']}")


def test_reimporting_an_existing_sku_with_a_blank_tax_cell_does_not_overwrite_its_stored_rate(client):
    """Same defect, the OTHER blank shape a real supplier sheet takes: a Tax
    Rate COLUMN present, but the cell itself left empty for this row."""
    assert client.post(f'{API}/settings/tax', json={'default_tax_rate': 16}).status_code == 200
    sku = f'IMP-EXIST-{uuid.uuid4().hex[:8]}'
    pid = _seed_product(client, name='Zero Rated Bread 2', sku=sku, sell_price=1.25, tax_rate=0)

    body = _csv_bytes([
        {'Product Name': 'Zero Rated Bread 2', 'SKU': sku, 'Selling Price': '1.25', 'Tax Rate': ''},
    ], ['Product Name', 'SKU', 'Selling Price', 'Tax Rate'])
    r = client.post('/api/import/execute', data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price","tax_rate":"Tax Rate"}',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get('updated') == 1, r.get_json()

    conn = get_retail_conn()
    row = conn.execute("SELECT tax_rate FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    assert float(row['tax_rate']) == 0.0, (
        f"a blank Tax Rate cell on a re-import must not retax an existing product, got {row['tax_rate']}")


def test_reimporting_an_existing_sku_with_an_explicit_tax_cell_still_updates_it(client):
    """The allow half of the same fix: a sheet that DOES carry a tax value
    for an existing row is a real correction, and must still land -- the
    fix above must not turn into "an existing product's tax_rate can never
    change through import again"."""
    assert client.post(f'{API}/settings/tax', json={'default_tax_rate': 16}).status_code == 200
    sku = f'IMP-EXIST-{uuid.uuid4().hex[:8]}'
    pid = _seed_product(client, name='Retax Me', sku=sku, sell_price=1.25, tax_rate=0)

    body = _csv_bytes([
        {'Product Name': 'Retax Me', 'SKU': sku, 'Selling Price': '1.25', 'Tax Rate': '5'},
    ], ['Product Name', 'SKU', 'Selling Price', 'Tax Rate'])
    r = client.post('/api/import/execute', data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price","tax_rate":"Tax Rate"}',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get('updated') == 1, r.get_json()

    conn = get_retail_conn()
    row = conn.execute("SELECT tax_rate FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    assert float(row['tax_rate']) == 5.0, (
        f"an explicit tax cell on a re-import must still update the stored rate, got {row['tax_rate']}")


def test_reimporting_an_existing_sku_with_no_tax_column_does_not_push_tax_rate_onto_the_sync_wire(client):
    """The sync half of the same fix: `_changed_fields` must not name
    `tax_rate` when the sheet said nothing about it -- naming it anyway
    would push this device's UNCHANGED value onto every peer as if it HAD
    changed, the identical untouched-field-clobber shape import_api.py's own
    stage 6b-i comment already guards against for `deleted_at_utc`."""
    assert client.post(f'{API}/settings/tax', json={'default_tax_rate': 16}).status_code == 200
    sku = f'IMP-EXIST-{uuid.uuid4().hex[:8]}'
    pid = _seed_product(client, name='Zero Rated Bread 3', sku=sku, sell_price=1.25, tax_rate=0)

    body = _csv_bytes([
        {'Product Name': 'Zero Rated Bread 3', 'SKU': sku, 'Selling Price': '1.30'},
    ], ['Product Name', 'SKU', 'Selling Price'])
    r = client.post('/api/import/execute', data={
        'system': 'retail', 'entity': 'products',
        'mapping': '{"name":"Product Name","sku":"SKU","sell_price":"Selling Price"}',
        'file': (io.BytesIO(body), 'products.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get('updated') == 1, r.get_json()

    conn = get_retail_conn()
    row = conn.execute(
        "SELECT payload FROM sync_outbox WHERE entity_type='product' AND entity_id=? "
        "AND event_type='update' ORDER BY id DESC LIMIT 1", (pid,)
    ).fetchone()
    conn.close()
    assert row is not None, "the re-import did not queue an 'update' sync event"
    payload = json.loads(row['payload'])
    assert 'tax_rate' not in (payload.get('_changed_fields') or []), (
        f"tax_rate must not be named as changed when the sheet carried no tax cell: {payload!r}")
    assert payload['tax_rate'] == 0.0, (
        f"even unclaimed, the payload's tax_rate must be this device's TRUE stored value (0), never "
        f"the resolved default, so a peer applying some OTHER changed field never picks up 16: {payload!r}")


def test_default_tax_rate_range_is_validated(client):
    bad = client.post(f'{API}/settings/tax', json={'default_tax_rate': 150})
    assert bad.status_code == 400, bad.get_json()

    good = client.post(f'{API}/settings/tax', json={'default_tax_rate': 0})
    assert good.status_code == 200, good.get_json()
    assert good.get_json()['data']['default_tax_rate'] == 0.0


def test_default_tax_rate_rejects_nan_and_infinity(client):
    """P0 round 2 (item B1): before this fix, `float('nan') < 0` and
    `float('nan') > 100` are BOTH False (every comparison against NaN is
    False), so the range check two lines below silently let a JSON body
    carrying a literal `NaN` straight through to `str(rate)` == 'nan',
    stored verbatim -- a shop-wide tax default that silently became "not a
    number" forever, re-parsed as `float('nan')` by every future
    `create_product`/import that reads it, with no 400 anywhere. Sent with
    `data=json.dumps(...)` + an explicit content type, not this file's usual
    `json=` kwarg, so the literal (non-standard, but Python-stdlib-emitted
    and -accepted) `NaN`/`Infinity` tokens actually reach the wire -- this
    is not a contrived encoding, it is what a real client sending
    `float('nan')` through Python's own `json.dumps` produces."""
    for bad in (float('nan'), float('inf'), float('-inf')):
        r = client.post(f'{API}/settings/tax', data=json.dumps({'default_tax_rate': bad}),
                         content_type='application/json')
        assert r.status_code == 400, (bad, r.get_json())
        assert 'finite' in r.get_json().get('message', ''), (bad, r.get_json())

    # P0 round 3 (item V2, reviewer finding): `float(True) == 1.0` /
    # `float(False) == 0.0`, so a JSON boolean used to sail through `float()`
    # with no exception -- {"default_tax_rate": true} stored and audited as
    # a real 1% shop-wide default before this check existed. Sent with the
    # ordinary `json=` kwarg (unlike the NaN/Infinity cases above): `true`/
    # `false` ARE valid JSON, so no special encoding is needed to get the
    # literal onto the wire.
    for bad_bool in (True, False):
        r = client.post(f'{API}/settings/tax', json={'default_tax_rate': bad_bool})
        assert r.status_code == 400, (bad_bool, r.get_json())
        assert 'number' in r.get_json().get('message', ''), (bad_bool, r.get_json())

    # The allow half: an ordinary finite rate is unaffected by the new check.
    good = client.post(f'{API}/settings/tax', json={'default_tax_rate': 12.5})
    assert good.status_code == 200, good.get_json()
    assert good.get_json()['data']['default_tax_rate'] == 12.5


def _default_tax_rate_change_audit_count(cid):
    conn = registry_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE company_id=? AND action='RETAIL_DEFAULT_TAX_RATE_CHANGED'",
        (str(cid),),
    ).fetchone()[0]
    conn.close()
    return n


def _last_default_tax_rate_change_context(cid):
    conn = registry_conn()
    row = conn.execute(
        "SELECT new_value_json FROM audit_logs WHERE company_id=? AND action='RETAIL_DEFAULT_TAX_RATE_CHANGED' "
        "ORDER BY rowid DESC LIMIT 1",
        (str(cid),),
    ).fetchone()
    conn.close()
    return json.loads(row['new_value_json'])


def test_default_tax_rate_change_is_audited_with_old_and_new(client):
    """P0 round 2 (item B1): a NEW audit event, RETAIL_DEFAULT_TAX_RATE_CHANGED
    -- mirroring the pre-existing RETAIL_TAX_MODE_CHANGED immediately above it
    in retail_api.py, and written to the SAME registry `audit_logs` table
    `commercial_runtime/security/audit.py::record()` always uses (confirmed
    by reading that module's `log_audit()` before writing this query, never
    assumed) -- fires only when the value actually MOVES, with both the old
    and new figures, and never on a same-value resave."""
    cid = client.test_company_id
    before_count = _default_tax_rate_change_audit_count(cid)

    # unset -> 16: a real change.
    r = client.post(f'{API}/settings/tax', json={'default_tax_rate': 16})
    assert r.status_code == 200, r.get_json()
    assert _default_tax_rate_change_audit_count(cid) == before_count + 1
    assert _last_default_tax_rate_change_context(cid) == {'old': None, 'new': 16.0}

    # 16 -> 16 (re-saving the SAME value): no new row -- a settings screen
    # that reloads and re-saves its own unchanged form must not fill the
    # audit log with rows that say nothing happened.
    r = client.post(f'{API}/settings/tax', json={'default_tax_rate': 16})
    assert r.status_code == 200, r.get_json()
    assert _default_tax_rate_change_audit_count(cid) == before_count + 1, (
        "re-posting the identical rate must not write a second audit row")

    # 16 -> 7.5: a second real change, old/new both non-null this time.
    r = client.post(f'{API}/settings/tax', json={'default_tax_rate': 7.5})
    assert r.status_code == 200, r.get_json()
    assert _default_tax_rate_change_audit_count(cid) == before_count + 2
    assert _last_default_tax_rate_change_context(cid) == {'old': 16.0, 'new': 7.5}


def test_default_tax_rate_bad_request_message_names_both_keys(client):
    """P0 round 2 (item B1): the 400 for a body carrying NEITHER key now
    names both `tax_calculation_mode` and `default_tax_rate` -- this route's
    own two accepted keys -- rather than only the first, which used to read
    as if `default_tax_rate` alone were never a legal call on its own (it
    always was, via the OR-condition this same route already applies)."""
    r = client.post(f'{API}/settings/tax', json={})
    assert r.status_code == 400, r.get_json()
    msg = r.get_json().get('message', '')
    assert 'tax_calculation_mode' in msg and 'default_tax_rate' in msg, msg


# ═════════════════════════════════════════════════════════════════════════
# #15(d) (I-5) -- trial balance ?as_of= and Closing Dr/Cr
# ═════════════════════════════════════════════════════════════════════════

def test_trial_balance_as_of_excludes_a_future_dated_sale_and_closings_are_equal(client, monkeypatch):
    """Same monkeypatch-`gl._today` technique retail_balance_sheet_test.py's
    own `test_as_of_date_boundary_is_inclusive_of_that_date_and_excludes_
    the_day_after` uses -- no route lets a caller override `business_date`
    directly, so controlling what `gl._today()` returns for the duration of
    one call is the sanctioned way to post a REAL journal dated something
    other than today."""
    pid = _seed_product(client, sell_price=50.0, tax_rate=0, cost_price=0)

    as_of = '2026-03-10'
    the_day_before = '2026-03-09'
    the_day_after = '2026-03-11'

    for business_date in (the_day_before, as_of, the_day_after):
        monkeypatch.setattr(gl, '_today', lambda d=business_date: d)
        r = _sell(client, pid, amount_paid=50.0)
        assert r

    no_filter = client.get(f'{API}/reports/trial-balance')
    assert no_filter.status_code == 200, no_filter.get_json()
    assert no_filter.get_json()['data']['as_of'] is None

    filtered = client.get(f'{API}/reports/trial-balance?as_of={as_of}')
    assert filtered.status_code == 200, filtered.get_json()
    data = filtered.get_json()['data']
    assert data['as_of'] == as_of

    cash = next(a for a in data['accounts'] if a['code'] == gl.ACCOUNT_CASH)
    # day-before (50) + on-the-day (50) = 100; day-after (50) excluded.
    assert cash['debit'] == 100.0, (
        f"expected 100.0 (day-before + on-the-day; day-after excluded), got {cash['debit']}")

    total_closing_debit = sum(a['closing_debit'] for a in data['accounts'])
    total_closing_credit = sum(a['closing_credit'] for a in data['accounts'])
    assert total_closing_debit == data['closing_debit']
    assert total_closing_credit == data['closing_credit']
    assert data['closing_debit'] == data['closing_credit'], (
        f"closing Dr/Cr totals must be equal: {data['closing_debit']} != {data['closing_credit']}")
    # Every account's own row: exactly one of the pair is nonzero, never both.
    for a in data['accounts']:
        assert a['closing_debit'] == 0 or a['closing_credit'] == 0, a


def test_trial_balance_route_is_byte_compatible_when_as_of_is_omitted(client):
    """The no-filter path must be untouched: same figures a caller who never
    asks for `as_of` always got."""
    pid = _seed_product(client, sell_price=20.0, tax_rate=0, cost_price=0)
    _sell(client, pid, amount_paid=20.0)

    r = client.get(f'{API}/reports/trial-balance')
    data = r.get_json()['data']
    assert data['is_balanced'] is True
    assert data['as_of'] is None
    cash = next(a for a in data['accounts'] if a['code'] == gl.ACCOUNT_CASH)
    assert cash['debit'] == 20.0


def test_trial_balance_as_of_rejects_a_malformed_date(client):
    r = client.get(f'{API}/reports/trial-balance?as_of=not-a-date')
    assert r.status_code == 400, r.get_json()


def test_trial_balance_non_padded_as_of_matches_the_padded_one(client, monkeypatch):
    """P0 round 2 (item B2): `datetime.strptime(as_of, '%Y-%m-%d')` is
    LENIENT about a non-zero-padded month/day -- '2026-9-1' parses to the
    same calendar date as '2026-09-01' -- but before this fix the RAW,
    un-normalized string then reached the SQL parameter, compared as plain
    TEXT against `gl_journals.business_date` (always stored zero-padded).
    SQLite's TEXT comparison disagrees with calendar order the instant one
    side is not padded: '2026-09-15' < '2026-9-1' lexicographically (the 6th
    character '0' sorts before '9'), so an as_of of '2026-9-1' would
    silently straddle dates it should exclude. Reproduced directly: a sale
    dated the day BEFORE as_of and a sale dated well AFTER it (a date whose
    un-padded bound would, before the fix, wrongly include it too) -- the
    non-padded query must give the IDENTICAL totals the padded one does,
    excluding the later sale in both."""
    pid = _seed_product(client, sell_price=30.0, tax_rate=0, cost_price=0)

    monkeypatch.setattr(gl, '_today', lambda d='2026-09-01': d)
    assert _sell(client, pid, amount_paid=30.0)

    monkeypatch.setattr(gl, '_today', lambda d='2026-10-15': d)
    assert _sell(client, pid, amount_paid=30.0)

    padded = client.get(f'{API}/reports/trial-balance?as_of=2026-09-01')
    assert padded.status_code == 200, padded.get_json()
    padded_data = padded.get_json()['data']

    non_padded = client.get(f'{API}/reports/trial-balance?as_of=2026-9-1')
    assert non_padded.status_code == 200, non_padded.get_json()
    non_padded_data = non_padded.get_json()['data']

    # The echoed `as_of` itself is normalized -- proves the fix is a real
    # reassignment, not merely a query that happens to still work.
    assert non_padded_data['as_of'] == '2026-09-01', non_padded_data['as_of']

    padded_cash = next(a for a in padded_data['accounts'] if a['code'] == gl.ACCOUNT_CASH)['debit']
    non_padded_cash = next(a for a in non_padded_data['accounts'] if a['code'] == gl.ACCOUNT_CASH)['debit']
    assert padded_cash == non_padded_cash == 30.0, (
        f"a non-padded as_of must return the SAME totals as the padded one, excluding the "
        f"Oct-15 sale in both: padded={padded_cash}, non_padded={non_padded_cash}")

    # Sanity: the Oct-15 sale is real and genuinely excluded, not merely
    # absent -- the all-time (no as_of) total must include both sales.
    no_filter = client.get(f'{API}/reports/trial-balance')
    all_time_cash = next(a for a in no_filter.get_json()['data']['accounts'] if a['code'] == gl.ACCOUNT_CASH)['debit']
    assert all_time_cash == 60.0, all_time_cash


def test_trial_balance_closing_column_flips_side_for_an_abnormal_balance(client):
    """The exact bug this item's own text names: 'Inventory at -0.792 and no
    side shown'. Inventory (1200, an ASSET, normal_balance='debit') posted
    with MORE credits than debits is an ABNORMAL balance -- it must show on
    the CREDIT side of the closing columns, never a negative number sitting
    in the debit column. A raw INSERT straight into gl_journal_lines/
    gl_journals (never through a route -- no real document can leave the
    ledger this lopsided; `post_journal`'s own balance invariant would abort
    it) is how retail_gl_route_test.py's own `test_ar_ties_reports_false_
    when_subledger_diverges_from_gl` manufactures its divergence too."""
    cid = client.test_company_id
    conn = get_retail_conn()
    gl.ensure_default_chart(conn, cid)
    inv_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_INVENTORY)
    ).fetchone()[0]
    cash_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_CASH)
    ).fetchone()[0]
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, memo, "
        "source_type, source_id, currency) VALUES (?,?,?,?,?,?,?,?)",
        (cid, None, '2026-01-01', 'TEST-ABNORMAL', 'abnormal inventory balance', 'test', None, 'JOD'))
    jid = cur.lastrowid
    cur.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, cash_id, 0.792, 0, ''))
    cur.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, inv_id, 0, 0.792, ''))
    conn.commit(); conn.close()

    r = client.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    inv = next(a for a in data['accounts'] if a['code'] == gl.ACCOUNT_INVENTORY)
    assert inv['balance'] == -0.792, inv  # existing signed-balance figure, unchanged by this item
    assert inv['closing_debit'] == 0, inv
    assert inv['closing_credit'] == 0.792, (
        f"an abnormal (credit) balance on a debit-normal account must show on the "
        f"CREDIT closing column, not a negative debit: {inv!r}")
    assert data['closing_debit'] == data['closing_credit'], (
        "closing Dr/Cr totals must still balance even with one abnormal account")


def test_trial_balance_as_of_never_falsely_reports_does_not_tie(client, monkeypatch):
    """Reviewer finding: `report_trial_balance` handed its as-of `tb` to
    `gl.subledger_tie`, which compared it against TODAY's LIVE `customers`/
    `suppliers.credit_balance` -- no history table backs that column. Any
    past `as_of` after AR/AP has moved therefore reported `ap_ties`/
    `ar_ties` False on a ledger that in fact ties, because the two sides of
    the comparison were never asking about the same date.

    Reproduces the exact measured shape: an unpaid PO TODAY (AP=10), then
    `as_of=yesterday`. Before the fix: ap_gl=0.0 (correct -- nothing had
    posted by yesterday) vs ap_subledger=10.0 (today's live balance),
    ap_ties=False. After the fix: the comparison is skipped for a genuine
    past date (`applicable: False`) and `ap_ties` reports True rather than a
    false accusation."""
    today = '2026-04-10'
    yesterday = '2026-04-09'
    monkeypatch.setattr(gl, '_today', lambda: today)

    pid = _seed_product(client, sell_price=10.0, cost_price=5.0, initial_stock=0)
    sr = client.post(f'{API}/suppliers', json={'name': f'P0 Supplier {uuid.uuid4().hex[:6]}'})
    assert sr.status_code == 200, sr.get_json()
    sid = sr.get_json()['data']['id']
    por = client.post(f'{API}/purchase-orders', json={
        'supplier_id': sid, 'amount_paid': 0,
        'items': [{'product_id': pid, 'quantity': 1, 'unit_cost': 10}],
    })
    assert por.status_code == 200, por.get_json()

    # Sanity: all-time and as_of=today both tie -- the bug is specific to a
    # date BEFORE any activity, never the no-filter/current-day path.
    for query in ('', f'?as_of={today}'):
        r = client.get(f'{API}/reports/trial-balance{query}')
        assert r.status_code == 200, (query, r.get_json())
        sl = r.get_json()['data']['subledger']
        assert sl['ap_ties'] is True, (query, sl)

    past = client.get(f'{API}/reports/trial-balance?as_of={yesterday}')
    assert past.status_code == 200, past.get_json()
    sl = past.get_json()['data']['subledger']
    assert sl['ap_gl'] == 0.0, (
        f"the GL side as of yesterday must correctly show nothing posted yet, got {sl!r}")
    assert sl['ap_ties'] is True, (
        f"a past as_of must never report a false 'Does Not Tie' against today's live subledger balance: {sl!r}")
    assert sl['applicable'] is False, (
        f"a past as_of's subledger comparison must be marked not-applicable, not silently skipped: {sl!r}")


def test_trial_balance_as_of_not_applicable_when_a_journal_is_dated_after_it(client, monkeypatch):
    """P0 round 3 (item V5, reviewer finding): the OLD applicability check
    read the SERVER'S OWN CLOCK (`tb['as_of'] < _today()`), which is wrong
    the moment a journal can be business-dated AFTER this server's own
    "today" -- clock/timezone skew between synced tills (the v41 GL journal
    sync) makes this a real case, not a hypothetical one. Reproduces the
    exact measured shape (probe3.log): a credit sale posted TODAY (which
    ties normally), then a SECOND journal business-dated TOMORROW. At
    `as_of=today`, the OLD clock check (`today < today` is False) fell
    through to a REAL comparison and reported a false 'Does Not Tie',
    because `tb` correctly excludes the tomorrow-dated journal
    (`business_date <= as_of`) while the LIVE subledger balance the route
    compares it against already counts it (the sale itself already
    happened; only its journal's date is ahead of `as_of`). The new,
    data-driven check instead asks "does any journal exist dated after
    as_of" -- true here -- and marks the comparison not-applicable, exactly
    as a genuine past `as_of` already does in the test just above, without
    ever asking what day the server's clock thinks it is."""
    today = '2026-05-10'
    tomorrow = '2026-05-11'
    monkeypatch.setattr(gl, '_today', lambda: today)

    pid = _seed_product(client, sell_price=10.0, cost_price=5.0, initial_stock=5)
    cr = client.post(f'{API}/customers', json={'name': f'P0 Customer {uuid.uuid4().hex[:6]}'})
    assert cr.status_code == 200, cr.get_json()
    customer_id = cr.get_json()['data']['id']
    # create_customer deliberately cannot grant credit (see that route's own
    # comment) -- a fresh customer defaults to credit_mode='none', which
    # create_sale refuses outright. PATCH it to 'unlimited' first, exactly
    # the field update_customer's own credit-terms gate exists for.
    patch = client.patch(f'{API}/customers/{customer_id}', json={'credit_mode': 'unlimited'})
    assert patch.status_code == 200, patch.get_json()

    # A credit sale TODAY -- posts to AR normally; ties at as_of=today with
    # nothing dated ahead of it yet (sanity, before the future journal
    # below exists).
    sr = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'customer_id': customer_id, 'payment_method': 'credit', 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4()),
    })
    assert sr.status_code == 200, sr.get_json()

    r = client.get(f'{API}/reports/trial-balance?as_of={today}')
    assert r.status_code == 200, r.get_json()
    sl = r.get_json()['data']['subledger']
    assert sl['ar_ties'] is True, ('sanity: before any future-dated activity, as_of=today must tie', sl)
    assert 'applicable' not in sl, ('sanity: no future-dated journal exists yet', sl)

    # A second document's journal, business-dated TOMORROW -- a raw INSERT
    # straight into gl_journals/gl_journal_lines (no real route posts an
    # explicit future business_date; append-only triggers would also
    # refuse an UPDATE after the fact), matching this file's own
    # test_trial_balance_closing_column_flips_side_for_an_abnormal_balance
    # convention for manufacturing a shape no ordinary route call can
    # produce on its own.
    conn = get_retail_conn()
    gl.ensure_default_chart(conn, client.test_company_id)
    ar_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (client.test_company_id, gl.ACCOUNT_AR)
    ).fetchone()[0]
    rev_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?",
        (client.test_company_id, gl.ACCOUNT_SALES_REVENUE)
    ).fetchone()[0]
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, memo, "
        "source_type, source_id, currency) VALUES (?,?,?,?,?,?,?,?)",
        (client.test_company_id, None, tomorrow, 'TEST-FUTURE', 'future-dated journal', 'test', None, 'JOD'))
    jid = cur.lastrowid
    cur.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, ar_id, 10.0, 0, ''))
    cur.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, rev_id, 0, 10.0, ''))
    conn.commit(); conn.close()

    r = client.get(f'{API}/reports/trial-balance?as_of={today}')
    assert r.status_code == 200, r.get_json()
    sl = r.get_json()['data']['subledger']
    assert sl['applicable'] is False, (
        f"a journal dated after as_of exists -- the comparison must be marked not-applicable, "
        f"never a false 'Does Not Tie' against it: {sl!r}")
    assert sl['ar_ties'] is True, (
        f"must report True (there is nothing false to report), not a false accusation against "
        f"activity as_of correctly excluded: {sl!r}")
    # NOTE: the all-time (no as_of) path is deliberately NOT re-checked here.
    # `tb.get('as_of') is None` short-circuits the new future-journal query
    # entirely, so the code path is untouched -- but this fixture's raw
    # journal insert has no matching `customers.credit_balance` write (no
    # real route posted it), which would make an all-time comparison
    # genuinely disagree for a reason that has nothing to do with V5. That
    # invariant is already covered, for the real `as_of=None` path, by
    # every OTHER test in this file and by retail_gl_route_test.py's own
    # exact-shape test.


# ═════════════════════════════════════════════════════════════════════════
# #15(e) (I-6) -- GET /products/stock-value
# ═════════════════════════════════════════════════════════════════════════

def test_stock_value_route_matches_metrics_inventory_value(client):
    """P0 round 2 (item B4, verifier finding): this test used to call
    `metrics.inventory_value` a SECOND time as its own 'expected' figure --
    the exact function the route itself calls internally -- so a bug shared
    by both call sites (a wrong COALESCE order, a wrong currency rounding)
    would agree with itself and this test would stay green regardless
    (ENGINEERING.md failure shape: "the pass condition IS the bug
    signature" cousin -- two calls to the SAME function can only ever prove
    they were called with the same arguments, never that either answer is
    RIGHT). Replaced with a number computed BY HAND from the fixture:
    neither product below has ever been received through a PO, so
    `avg_cost` is 0 for both and `metrics.inventory_value`'s own documented
    fallback chain (confirmed independently by
    `test_list_products_cost_basis_matches_the_books_never_the_raw_cost_
    price` just below) prices every unit at its typed `cost_price` --
    25 * 4.0 + 10 * 2.0 = 120.0, arithmetic a reader can check against the
    fixture without opening metrics.py at all."""
    _seed_product(client, sell_price=10.0, cost_price=4.0, initial_stock=25)
    _seed_product(client, sell_price=5.0, cost_price=2.0, initial_stock=10)

    r = client.get(f'{API}/products/stock-value')
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert set(body.keys()) == {'value', 'currency'}, body

    expected = 25 * 4.0 + 10 * 2.0  # = 120.0, by hand -- see docstring above
    assert body['value'] == expected, (body, expected)


def test_list_products_cost_basis_matches_the_books_never_the_raw_cost_price(client):
    """Reviewer finding: `list_products` returned the raw `products.avg_cost`
    column (`p.*`), which is 0 for any product never received through a PO
    -- so the 'Avg cost' cell showed JD 0.000 while the SAME row's units
    counted at `cost_price` in the 'Stock value at cost' header total. Both
    figures must come from the identical fallback chain
    `metrics.inventory_value` already uses."""
    pid = _seed_product(client, sell_price=10.0, cost_price=4.0, initial_stock=49)

    products = client.get(f'{API}/products').get_json()['data']
    row = next(p for p in products if p['id'] == pid)
    assert row['avg_cost'] == 0.0, (
        "sanity: the raw column is 0 for a never-received product -- this is the value that used "
        f"to leak into the UI's 'Avg cost' cell unfiltered: {row!r}")
    assert row['cost_basis'] == 4.0, (
        f"cost_basis must fall back to cost_price when avg_cost is 0 (never received through a PO), "
        f"matching metrics.inventory_value's own fallback chain: {row!r}")

    stock_value = client.get(f'{API}/products/stock-value').get_json()['value']
    assert stock_value == 49 * 4.0, (stock_value, row)
    # THE ACTUAL BUG: the header total and this row's own cost_basis must
    # agree about what "cost" means for the SAME units.
    assert row['cost_basis'] * 49 == stock_value, (row, stock_value)


def test_list_products_cost_basis_prefers_avg_cost_after_a_po_receipt(client):
    """The allow/moving-average half: after a PO receipt, avg_cost becomes
    real and cost_basis must prefer it over the typed cost_price -- the
    exact 'coffee received at 1.000 still shows 2.900' defect item #15(e)
    names."""
    pid = _seed_product(client, sell_price=10.0, cost_price=2.9, initial_stock=0)
    sr = client.post(f'{API}/suppliers', json={'name': f'P0 Supplier {uuid.uuid4().hex[:6]}'})
    sid = sr.get_json()['data']['id']
    por = client.post(f'{API}/purchase-orders', json={
        'supplier_id': sid, 'amount_paid': 0,
        'items': [{'product_id': pid, 'quantity': 10, 'unit_cost': 1.0}],
    })
    assert por.status_code == 200, por.get_json()
    po_id = por.get_json()['data']['id']
    # receive_purchase_order reads its lines back off `purchase_order_items`
    # (the rows create_purchase_order already wrote), not from the request
    # body -- no body needed here.
    recv = client.post(f'{API}/purchase-orders/{po_id}/receive')
    assert recv.status_code == 200, recv.get_json()

    row = next(p for p in client.get(f'{API}/products').get_json()['data'] if p['id'] == pid)
    assert row['cost_basis'] == 1.0, (
        f"cost_basis must equal the API's own moving average after a PO receipt, not the stale "
        f"typed cost_price (2.9): {row!r}")


def test_stock_value_route_requires_a_session():
    anon = app.test_client()
    assert anon.get(f'{API}/products/stock-value').status_code == 401


def _create_branch(c, name):
    r = c.post(f'{API}/branches', json={'name': name})
    assert r.status_code == 200, r.get_json()
    bid = r.get_json()['data']['id']
    conn = get_retail_conn()
    row = conn.execute("SELECT uid FROM branches WHERE id=?", (bid,)).fetchone()
    conn.close()
    return bid, row['uid']


def _branch_scoped_manager(owner, branch_uid):
    """A real manager account, scoped to `branch_uid` through the SAME PUT
    .../branch-scope route employees.js's own 'Change Branch' control
    calls -- matches retail_p0_owner_guard_test.py's / retail_branch_scope_
    enforcement_test.py's own convention of exercising the real grant route
    rather than hand-writing `branch_scope_uid`. No re-login dance needed
    here (unlike those two files' delegated-manager fixtures): this account
    never logs in until AFTER scoping, so there is no earlier session for
    the scope write's `session_version` bump to invalidate."""
    email = f'p0be-scoped-{uuid.uuid4().hex[:8]}@test.local'
    password = 'P0BackendPW1'
    r = owner.post('/api/admin/employees', json={
        'email': email, 'role': 'manager', 'permissions': {'retail': 'full'},
    })
    assert r.status_code == 200, r.get_json()
    conn = registry_conn()
    row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    manager_id = row['id']
    conn.execute("UPDATE users SET password_hash=?, status='active' WHERE id=?",
                 (hash_password(password), manager_id))
    conn.commit(); conn.close()

    r = owner.put(f'/api/admin/employees/{manager_id}/branch-scope',
                  json={'branch_scope_uid': branch_uid})
    assert r.status_code == 200, r.get_json()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


def test_stock_value_scoped_manager_gets_only_their_own_branch(client):
    """P0 round 3 (item V4, reviewer finding): `product_stock_value` never
    called `_coerce_branch_id_for_scope` at all -- the one report route in
    this wave that skipped it (`dashboard_stats`/`sales-trend`/`cash_flow`
    above all do). Measured: a Branch-A-scoped manager received the SAME
    company-wide figure the owner sees, leaking every other branch's stock
    value into a header scoped staff are meant to see only their own
    branch through. Two branches stocked at the SAME cost but different
    quantities, so a wrong (company-wide) figure cannot be mistaken for a
    coincidence."""
    main_id, main_uid = _create_branch(client, f'Main {uuid.uuid4().hex[:6]}')
    down_id, _down_uid = _create_branch(client, f'Downtown {uuid.uuid4().hex[:6]}')

    pid = _seed_product(client, cost_price=4.0, initial_stock=0)
    r = client.post(f'{API}/products/{pid}/stock-adjust',
                     json={'quantity': 25, 'reason': 'seed', 'branch_id': main_id})
    assert r.status_code == 200, r.get_json()
    r = client.post(f'{API}/products/{pid}/stock-adjust',
                     json={'quantity': 10, 'reason': 'seed', 'branch_id': down_id})
    assert r.status_code == 200, r.get_json()

    manager = _branch_scoped_manager(client, main_uid)

    scoped = manager.get(f'{API}/products/stock-value')
    assert scoped.status_code == 200, scoped.get_json()
    assert scoped.get_json()['value'] == 25 * 4.0, (
        f"a Branch-Main-scoped manager must see only Branch Main's own stock "
        f"value, not the company-wide total: {scoped.get_json()!r}")

    # The allow half: the OWNER (unscoped) still gets the company-wide
    # figure, unaffected by this fix -- never narrowed to whoever happens
    # to call it.
    unscoped = client.get(f'{API}/products/stock-value')
    assert unscoped.status_code == 200, unscoped.get_json()
    assert unscoped.get_json()['value'] == 25 * 4.0 + 10 * 4.0, unscoped.get_json()


# ═════════════════════════════════════════════════════════════════════════
# #17 (I-7) -- audit log actor_name
# ═════════════════════════════════════════════════════════════════════════

def _promote_this_device_to_admin(company_id):
    """list_audit_log gates on the admin-DEVICE, a separate axis from the
    admin-ROLE this file's `client` fixture already holds -- mirrors
    retail_audit_log_test.py's own `_promote_to_admin`."""
    from commercial_runtime.identity import device_context, device_registry
    conn = registry_conn()
    device_id = device_context.local_device_uuid()
    device_registry.upsert_local_device(conn, device_id, company_id)
    device_registry.set_admin_device(conn, company_id, device_id)
    conn.close()
    device_context._cached_device = None


def test_audit_log_rows_carry_actor_name_for_a_real_user(client):
    """CONFIRMS THE TRAP THIS ITEM NAMES: `audit_log.user_id` holds
    `users.id` (the local primary key `_uid()` stamps), never `users.uid` --
    calling `_resolve_actor_identities` with its default `key_column='uid'`
    against these values would resolve nothing. This test would have stayed
    green with `actor_name` silently null forever if that confirmation were
    skipped."""
    _promote_this_device_to_admin(client.test_company_id)
    _seed_product(client, name='Audited Widget')

    r = client.get(f'{API}/audit-log?action=PRODUCT_CREATED')
    assert r.status_code == 200, r.get_json()
    rows = r.get_json()['data']
    assert rows, "expected at least the PRODUCT_CREATED row this test just wrote"
    row = rows[0]
    assert row['actor_name'], (
        f"actor_name did not resolve for a real, freshly-created user: {row!r}")

    conn = registry_conn()
    real_email = conn.execute("SELECT email FROM users WHERE id=?", (row['user_id'],)).fetchone()['email']
    conn.close()
    assert row['actor_name'] == real_email, (row['actor_name'], real_email)


def test_audit_log_actor_name_is_null_not_invented_when_the_user_cannot_be_resolved(client):
    """The deny half: a row whose user_id resolves to nobody (an account
    since deleted, or -- as here -- a value that was never a real user id at
    all) gets actor_name=None, never a fabricated name."""
    _promote_this_device_to_admin(client.test_company_id)

    ghost_uid = str(uuid.uuid4())
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO audit_log (company_id,user_id,action,entity,entity_id,details) VALUES (?,?,?,?,?,?)",
        (client.test_company_id, ghost_uid, 'GHOST_ACTION', 'test', '1', ''))
    conn.commit(); conn.close()

    r = client.get(f'{API}/audit-log?action=GHOST_ACTION')
    assert r.status_code == 200, r.get_json()
    rows = r.get_json()['data']
    assert len(rows) == 1, rows
    assert rows[0]['actor_name'] is None, rows[0]


# ═════════════════════════════════════════════════════════════════════════
# #18 (I-8) -- GET /ai/status
# ═════════════════════════════════════════════════════════════════════════

def test_ai_status_is_false_when_no_endpoint_is_configured(client, monkeypatch):
    monkeypatch.setattr(retail_api_module, 'AURA_AI_ENDPOINT_URL', '')
    r = client.get(f'{API}/ai/status')
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {'configured': False}


def test_ai_status_is_true_when_an_endpoint_is_configured(client, monkeypatch):
    monkeypatch.setattr(retail_api_module, 'AURA_AI_ENDPOINT_URL', 'http://ai.test.local/api/generate')
    r = client.get(f'{API}/ai/status')
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {'configured': True}


def test_ai_status_requires_a_session():
    anon = app.test_client()
    assert anon.get(f'{API}/ai/status').status_code == 401
