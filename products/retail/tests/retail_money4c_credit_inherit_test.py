"""Aura Retail -- MONEY-4c (retail schema v53): a customer's credit follows the SHOP default
unless the owner set it.

THE BUG (UI-2 measured it in a real browser): `customers.credit_mode` defaulted to 'none' and
every way of creating a customer stored it, while create_sale resolved terms as
`cust.credit_mode or settings.default_credit_mode` -- and 'none' is truthy. Shop default
"unlimited" + a normally created customer -> Credit tile disabled, credit sale refused.

THE FIX: a fourth stored value 'inherit' ("follow the shop default"); explicit 'none' keeps
meaning "this customer may not buy on credit"; one resolver
(core/retail/credit_terms.py) that every reader uses; v53 converts legacy 'none' rows.

WHAT THIS FILE PROVES (allow AND deny halves of every gate)
  * shop default unlimited + a new customer           -> a credit sale is ALLOWED
  * explicit 'none' + shop default unlimited          -> REFUSED (the deny half)
  * shop default none + 'inherit'                     -> REFUSED
  * shop default limited (limit 100, block) + inherit -> the limit is honoured through inherit
  * a NULL / '' row follows the default; an UNKNOWN stored word fails closed (was: unlimited)
  * PATCH / create validate the mode; an explicit grant at create needs retail.discount
  * GET /customers carries both `credit_mode` (stored) and `effective_credit_mode`
  * create's and update's sync events carry credit_mode NULL when inheriting -- never the word
    'inherit', which a pre-v53 peer reads as UNLIMITED credit (pinned below by running the v52
    rule on both) -- and the mode itself when explicit
  * import: no credit column -> 'inherit'; a column value is stored; garbage -> 'inherit'
  * v53 migration: 'none' -> 'inherit', other modes untouched, idempotent, never re-converts a
    'none' chosen after the upgrade, adds the column to a database that lacked it
  * the lazy column helper adds a missing column with DEFAULT 'inherit'

MUTATIONS (each performed; red quoted in the hand-back)
  M-a  effective_credit_mode ignores 'inherit' (returns it as stored)  -> the allow tests
  M-b  create_customer stores 'none' again                              -> test_new_customer_*
  M-c  migration converts ALL rows, i.e. drops the entry_user_version gate -> test_migration_does_not_reconvert*
  M-d  migration does not convert                                       -> test_migration_converts_*
  M-e  unknown word resolves to the stored word (fail-open)             -> test_unknown_stored_mode_fails_closed
  M-f  create sends the word 'inherit' on the wire                        -> test_create_event_carries_null_*

Run (one file per process):
    pytest products/retail/tests/retail_money4c_credit_inherit_test.py -v
"""
import csv
import io
import json
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import retail_accountant_regate_test as H  # noqa: E402  (builds the isolated app + data dir)
import database.schema as sch  # noqa: E402
from core.retail import credit_terms  # noqa: E402

API = H.API


# ── helpers ──────────────────────────────────────────────────────────────────

def _rows(sql, params=()):
    conn = H.get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _exec(sql, params=()):
    conn = H.get_retail_conn()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _stored(cust_id):
    return _rows("SELECT credit_mode, credit_limit FROM customers WHERE id=?", (cust_id,))[0]


def _shop(default_mode, default_limit=0, enforce='warn'):
    admin, cid, pid = H._new_shop()
    r = admin.post(f'{API}/settings/credit', json={
        'default_credit_mode': default_mode, 'default_credit_limit': str(default_limit),
        'enforce_credit_limit': enforce})
    assert r.status_code == 200, r.get_json()
    return admin, cid, pid


def _new_customer(admin, **extra):
    r = admin.post(f'{API}/customers', json={'name': f'C {uuid.uuid4().hex[:6]}', **extra})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _credit_sale(admin, pid, customer_id, qty=1):
    return admin.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': qty}], 'payment_method': 'credit',
        'amount_paid': 0, 'customer_id': customer_id, 'idempotency_key': str(uuid.uuid4())})


# ── the resolver itself ──────────────────────────────────────────────────────

@pytest.mark.parametrize('stored,default,want', [
    ('inherit', 'unlimited', 'unlimited'), (None, 'unlimited', 'unlimited'), ('', 'limited', 'limited'),
    ('inherit', 'none', 'none'), ('inherit', None, 'none'), ('inherit', 'banana', 'none'),
    ('none', 'unlimited', 'none'), ('limited', 'unlimited', 'limited'), ('unlimited', 'none', 'unlimited'),
    ('banana', 'unlimited', 'none'),
])
def test_resolver_table(stored, default, want):
    assert credit_terms.effective_credit_mode(
        {'credit_mode': stored}, {'default_credit_mode': default}) == want


def test_limit_resolution():
    s = {'default_credit_limit': '100'}
    inh = lambda lim: {'credit_mode': 'inherit', 'credit_limit': lim}  # noqa: E731
    assert credit_terms.effective_credit_limit(inh(0), s) == 100.0           # DEFAULT 0 -> shop limit
    assert credit_terms.effective_credit_limit(inh(None), s) == 100.0
    assert credit_terms.effective_credit_limit(inh(40), s) == 40.0           # a stated limit stands
    assert credit_terms.effective_credit_limit({'credit_mode': 'limited', 'credit_limit': 0}, s) == 0.0
    assert credit_terms.effective_credit_limit({'credit_mode': 'limited', 'credit_limit': None}, s) == 100.0


# ── create_sale through the real routes ──────────────────────────────────────

def test_new_customer_is_stored_inherit_and_follows_a_shop_default_of_unlimited():
    admin, _cid, pid = _shop('unlimited')
    cust = _new_customer(admin)
    assert _stored(cust)['credit_mode'] == 'inherit'
    r = _credit_sale(admin, pid, cust)
    assert r.status_code == 200, r.get_json()          # ALLOW: the shop default now reaches them


def test_explicit_none_is_refused_even_with_a_shop_default_of_unlimited():
    admin, _cid, pid = _shop('unlimited')
    cust = _new_customer(admin)
    assert admin.patch(f'{API}/customers/{cust}', json={'credit_mode': 'none'}).status_code == 200
    r = _credit_sale(admin, pid, cust)
    assert r.status_code == 400 and 'not allowed to buy on credit' in r.get_json()['message']   # DENY


def test_inherit_with_a_shop_default_of_none_is_refused():
    admin, _cid, pid = _shop('none')
    cust = _new_customer(admin)
    r = _credit_sale(admin, pid, cust)
    assert r.status_code == 400 and 'not allowed to buy on credit' in r.get_json()['message']


def test_inherit_honours_the_shop_default_limit():
    admin, _cid, pid = _shop('limited', default_limit=150, enforce='block')   # product costs 100
    cust = _new_customer(admin)
    assert _credit_sale(admin, pid, cust).status_code == 200                   # 100 <= 150
    over = _credit_sale(admin, pid, cust)                                      # 200 > 150
    assert over.status_code == 400 and 'Credit limit exceeded' in over.get_json()['message']
    assert '150.000' in over.get_json()['message']   # the SHOP limit, reached through inherit


def test_changing_the_shop_default_moves_every_inheriting_customer():
    admin, _cid, pid = _shop('none')
    cust = _new_customer(admin)
    assert _credit_sale(admin, pid, cust).status_code == 400
    assert admin.post(f'{API}/settings/credit', json={'default_credit_mode': 'unlimited'}).status_code == 200
    assert _credit_sale(admin, pid, cust).status_code == 200


@pytest.mark.parametrize('blank', [None, ''])
def test_a_null_or_empty_row_follows_the_default(blank):
    admin, _cid, pid = _shop('unlimited')
    cust = _new_customer(admin)
    _exec("UPDATE customers SET credit_mode=? WHERE id=?", (blank, cust))
    assert _credit_sale(admin, pid, cust).status_code == 200


def test_unknown_stored_mode_fails_closed():
    """Before: an unrecognised word fell past both of create_sale's checks as UNLIMITED credit."""
    admin, _cid, pid = _shop('unlimited')
    cust = _new_customer(admin)
    _exec("UPDATE customers SET credit_mode='banana' WHERE id=?", (cust,))
    assert _credit_sale(admin, pid, cust).status_code == 400


# ── writers: validation and capability ───────────────────────────────────────

def test_patch_and_create_validate_the_mode():
    admin, _cid, _pid = _shop('unlimited')
    cust = _new_customer(admin)
    assert admin.patch(f'{API}/customers/{cust}', json={'credit_mode': 'banana'}).status_code == 400
    assert _stored(cust)['credit_mode'] == 'inherit'
    assert admin.post(f'{API}/customers', json={'name': 'x', 'credit_mode': 'banana'}).status_code == 400
    assert admin.post(f'{API}/customers', json={'name': 'x', 'credit_limit': -5}).status_code == 400
    # the allow half: every legal word is accepted on PATCH, 'inherit' included
    for mode in ('inherit', 'none', 'limited', 'unlimited'):
        assert admin.patch(f'{API}/customers/{cust}', json={'credit_mode': mode}).status_code == 200
        assert _stored(cust)['credit_mode'] == mode


def test_create_with_an_explicit_mode_stores_it_and_a_cashier_cannot_grant_one():
    admin, cid, _pid = _shop('none')
    got = _new_customer(admin, credit_mode='limited', credit_limit=75)
    assert dict(_stored(got)) == {'credit_mode': 'limited', 'credit_limit': 75.0}
    cashier, _uid = H._make_user('cashier', cid)
    refused = cashier.post(f'{API}/customers', json={'name': 'sneaky', 'credit_mode': 'unlimited'})
    assert refused.status_code == 403
    # control (allow half): the same cashier creates an ordinary customer, who inherits
    ok = cashier.post(f'{API}/customers', json={'name': 'plain'})
    assert ok.status_code == 200
    assert _stored(ok.get_json()['data']['id'])['credit_mode'] == 'inherit'


def test_customer_list_carries_stored_and_effective_terms():
    admin, _cid, _pid = _shop('unlimited')
    inh = _new_customer(admin)
    own = _new_customer(admin)
    admin.patch(f'{API}/customers/{own}', json={'credit_mode': 'none'})
    rows = {c['id']: c for c in admin.get(f'{API}/customers').get_json()['data']}
    assert (rows[inh]['credit_mode'], rows[inh]['effective_credit_mode']) == ('inherit', 'unlimited')
    assert (rows[own]['credit_mode'], rows[own]['effective_credit_mode']) == ('none', 'none')


# ── sync event shape ─────────────────────────────────────────────────────────

def _create_event_payload(cust_id):
    r = _rows("SELECT payload FROM sync_outbox WHERE entity_type='customer' AND entity_id=? "
              "AND event_type='create'", (cust_id,))
    assert len(r) == 1, 'the create event was not queued'
    return json.loads(r[0]['payload'])


def _update_event_payloads(cust_id):
    return [json.loads(r['payload']) for r in _rows(
        "SELECT payload FROM sync_outbox WHERE entity_type='customer' AND entity_id=? "
        "AND event_type='update' ORDER BY rowid", (cust_id,))]


def test_create_event_carries_null_not_the_word_inherit_and_the_mode_when_explicit():
    admin, _cid, _pid = _shop('unlimited')
    inh = _create_event_payload(_new_customer(admin))
    assert 'credit_mode' in inh and inh['credit_mode'] is None      # the key stays on the wire (B2 ratchet)
    assert inh['credit_limit'] == 0
    exp = _create_event_payload(_new_customer(admin, credit_mode='limited', credit_limit=10))
    assert (exp['credit_mode'], exp['credit_limit']) == ('limited', 10.0)
    none = _create_event_payload(_new_customer(admin, credit_mode='none'))
    assert none['credit_mode'] == 'none'


def test_a_patch_back_to_inherit_also_goes_on_the_wire_as_null():
    admin, _cid, _pid = _shop('unlimited')
    cust = _new_customer(admin)
    admin.patch(f'{API}/customers/{cust}', json={'credit_mode': 'none'})
    admin.patch(f'{API}/customers/{cust}', json={'credit_mode': 'inherit'})
    modes = [p['credit_mode'] for p in _update_event_payloads(cust)]
    assert modes == ['none', None], modes
    assert 'inherit' not in json.dumps(_update_event_payloads(cust))


def test_a_pre_v53_peer_given_the_wire_value_follows_the_shop_default_and_never_exceeds_it():
    """The v52 rule, copied verbatim from create_sale before MONEY-4c:
        credit_mode = cust['credit_mode'] or settings['default_credit_mode']
        if credit_mode == 'none': refuse
        if credit_mode == 'limited' and over limit: refuse/warn
    The WORD 'inherit' is truthy and neither 'none' nor 'limited', so it sails through as
    UNLIMITED credit -- even when the shop default is 'none'. That is why it is never sent
    (previous tests). NULL, what is sent instead, falls through `or` to the shop default: the old
    computer then grants exactly what the shop default grants. This test documents both halves
    so nobody 'simplifies' the null away into the word."""
    def v52_allows(stored, default):
        mode = stored or default
        return mode != 'none' and mode != 'limited'
    assert v52_allows('inherit', 'none') is True       # the hazard the wire value avoids
    for default in ('none', 'limited', 'unlimited'):   # what v53 itself resolves for the same row
        v53 = credit_terms.effective_credit_mode({'credit_mode': None}, {'default_credit_mode': default})
        assert v53 == default
    assert v52_allows(None, 'none') is False           # null never exceeds a shop default of none
    assert v52_allows(None, 'unlimited') is True


# ── import ───────────────────────────────────────────────────────────────────

def _import_customers(client, rows, headers, mapping):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=headers)
    w.writeheader()
    for r in rows:
        w.writerow(r)
    r = client.post('/api/import/execute', data={
        'system': 'retail', 'entity': 'customers', 'mapping': json.dumps(mapping),
        'file': (io.BytesIO(buf.getvalue().encode('utf-8')), 'customers.csv'),
    }, content_type='multipart/form-data')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['success'] is True, r.get_json()


def test_import_without_a_credit_column_inherits_and_with_one_stores_it():
    admin, _cid, _pid = _shop('unlimited')
    tag = uuid.uuid4().hex[:6]
    _import_customers(admin, [{'Name': f'NoCol {tag}', 'Email': f'a{tag}@x.test'}],
                      ['Name', 'Email'], {'name': 'Name', 'email': 'Email'})
    row = _rows("SELECT id, credit_mode FROM customers WHERE name=?", (f'NoCol {tag}',))[0]
    assert row['credit_mode'] == 'inherit'
    assert _create_event_payload(row['id'])['credit_mode'] is None   # null on the wire, never 'inherit'

    rows = [{'Name': f'Own {tag}', 'Email': f'b{tag}@x.test', 'Terms': 'none'},
            {'Name': f'Garb {tag}', 'Email': f'c{tag}@x.test', 'Terms': 'platinum'},
            {'Name': f'Blank {tag}', 'Email': f'd{tag}@x.test', 'Terms': ''},
            {'Name': f'Unl {tag}', 'Email': f'e{tag}@x.test', 'Terms': ' Unlimited '}]
    _import_customers(admin, rows, ['Name', 'Email', 'Terms'],
                      {'name': 'Name', 'email': 'Email', 'credit_mode': 'Terms'})
    got = {r['name']: r['credit_mode'] for r in _rows(
        "SELECT name, credit_mode FROM customers WHERE name LIKE ?", (f'%{tag}',))}
    assert got[f'Own {tag}'] == 'none'                 # an explicit value is stored as given
    assert got[f'Garb {tag}'] == 'inherit'             # never guessed into more credit
    assert got[f'Blank {tag}'] == 'inherit'
    assert got[f'Unl {tag}'] == 'unlimited'


# ── the v53 migration ────────────────────────────────────────────────────────

def _migrate(db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-m4c-backup-')
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
        shutil.rmtree(backup_dir, ignore_errors=True)


def _to_v52():
    """A v52 database: rows hold the modes v52 wrote ('none' by default for everyone),
    user_version 52."""
    conn = H.get_retail_conn()
    try:
        conn.execute("PRAGMA user_version = 52")
        conn.commit()
        return conn.execute("PRAGMA database_list").fetchone()[2]
    finally:
        conn.close()


def _mode(cust):
    return _stored(cust)['credit_mode']


def test_migration_converts_none_to_inherit_and_leaves_the_other_modes():
    admin, _cid, _pid = _shop('unlimited')
    legacy, lim, unl, blank = (_new_customer(admin) for _ in range(4))
    _exec("UPDATE customers SET credit_mode='none' WHERE id=?", (legacy,))      # what v52 stored
    _exec("UPDATE customers SET credit_mode='limited', credit_limit=9 WHERE id=?", (lim,))
    _exec("UPDATE customers SET credit_mode='unlimited' WHERE id=?", (unl,))
    _exec("UPDATE customers SET credit_mode=NULL WHERE id=?", (blank,))
    db = _to_v52()

    _migrate(db)

    assert _mode(legacy) == 'inherit'
    assert (_mode(lim), _stored(lim)['credit_limit']) == ('limited', 9.0)
    assert _mode(unl) == 'unlimited'
    assert _mode(blank) is None                       # the resolver treats NULL as inherit already
    assert _rows("PRAGMA user_version")[0]['user_version'] == sch.RETAIL_SCHEMA_VERSION == 53


def test_migration_does_not_reconvert_a_none_chosen_after_the_upgrade():
    """Idempotence through the version gate: a database already at 53 whose owner set an
    explicit 'none' must keep it when the step runs again (replay, direct call, restore)."""
    admin, _cid, pid = _shop('unlimited')
    cust = _new_customer(admin)
    admin.patch(f'{API}/customers/{cust}', json={'credit_mode': 'none'})        # a deliberate choice, at v53
    conn = H.get_retail_conn()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 53
        sch._migrate_credit_mode_inherit(conn)
        sch._migrate_credit_mode_inherit(conn)
        conn.commit()
    finally:
        conn.close()
    assert _mode(cust) == 'none'
    assert _credit_sale(admin, pid, cust).status_code == 400
    # and the whole chain replayed at 53 changes nothing either
    conn = H.get_retail_conn()
    try:
        db = conn.execute("PRAGMA database_list").fetchone()[2]
    finally:
        conn.close()
    _migrate(db)
    assert _mode(cust) == 'none'


def test_migration_is_idempotent_when_forced_to_run_twice_from_52():
    admin, _cid, _pid = _shop('unlimited')
    cust = _new_customer(admin)
    _exec("UPDATE customers SET credit_mode='none' WHERE id=?", (cust,))
    db = _to_v52()
    _migrate(db)
    first = _rows("SELECT id, credit_mode FROM customers ORDER BY id")
    _to_v52()
    _migrate(db)
    assert _rows("SELECT id, credit_mode FROM customers ORDER BY id") == first


def test_migration_adds_the_column_to_a_database_that_lacked_it_defaulting_to_inherit():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE customers (id TEXT PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO customers VALUES ('a','A')")
    conn.execute("PRAGMA user_version = 52")
    sch._migrate_credit_mode_inherit(conn)
    assert conn.execute("SELECT credit_mode FROM customers").fetchone()[0] == 'inherit'
    conn.execute("INSERT INTO customers (id, name) VALUES ('b','B')")
    assert conn.execute("SELECT credit_mode FROM customers WHERE id='b'").fetchone()[0] == 'inherit'
    # and a database with no customers table at all is a no-op, not an error
    sch._migrate_credit_mode_inherit(sqlite3.connect(':memory:'))


def test_the_lazy_column_helper_adds_a_missing_column_defaulting_to_inherit():
    """LAST in the file: it drops and re-adds the column on this file's shared database."""
    import api.retail_api as ra
    admin, _cid, _pid = _shop('unlimited')
    conn = H.get_retail_conn()
    try:
        conn.execute("ALTER TABLE customers DROP COLUMN credit_mode")
        conn.commit()
        ra._CREDIT_SCHEMA_READY = False
        ra._ensure_credit_schema(conn)
        conn.commit()
        cols = {r[1]: r[4] for r in conn.execute("PRAGMA table_info(customers)").fetchall()}
    finally:
        conn.close()
    assert cols['credit_mode'] == "'inherit'"
    assert _mode(_new_customer(admin)) == 'inherit'
