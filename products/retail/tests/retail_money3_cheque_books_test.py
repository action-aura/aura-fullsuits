"""MONEY-3 M3 (retail v51): cheques post to the books.

BEFORE: a cheque's money leg moved the customer / supplier balance (a `payments` row) and posted
NO general-ledger journal, so GL Accounts Receivable / Payable sat above the party balances by
every cheque that was alive (retail_party_balance_mixed_day_test measured it: AR +60, AP +40) and
the trial balance's subledger tie said "does not tie". NOW every transition posts its own
journal, in the same transaction, with a uid that names the FACT (cheque + crossing / stint), so a
retry or a second computer observing the same bank fact books it once.

What each test proves, and what it cannot:
  * the per-transition test asserts the EXACT two lines and the EXACT uid of every journal, plus
    that a retried transition (409) and a direct re-post leave the count unchanged -- a count alone
    could pass with the wrong accounts, lines alone with a double post.
  * the tie test puts a cheque in EVERY state at once (customer: pending, deposited, cleared,
    endorsed, bounced, cancelled, written off; supplier: pending, cleared, bounced, cancelled) and
    asserts both tie flags AND the three accounts' exact balances -- a flag alone would pass on a
    wrong-but-balanced split between the accounts.
  * the upgrade test removes the cheque journals from a populated DB, stamps it v50 and migrates
    through the REAL `ensure_schema_version` chain: the upgrade re-derives each cheque's history
    (M4: it no longer posts one dated-today "opening" journal per alive cheque), so every cheque --
    dead ones too -- ends with exactly the journals, by uid, that the live path posted for the same
    events; ties restored, replay is a no-op. The fixture is built by the real routes, never a
    hand-written row. (M4 changed this test: it can no longer catch "a dead cheque got NO journal"
    -- a bounced cheque now carries its apply and its reversal, which net to zero; the
    retail_money3_cheque_reconcile_test.py upgrade tests cover dates and rolling upgrades.)

MUTATIONS (retail_api.py / general_ledger.py / schema.py; each performed, red quoted in the report):
  M3-a  `_post_cheque_books`: skip the `apply` leg           -> test_every_transition_posts_the_exact_journal_once
  M3-b  `_post_cheque_books`: bounce-after-clear books the holding account (ignore the bank leg)
                                                              -> test_every_transition_posts_the_exact_journal_once
  M3-c  `cheque_journal_uid`: random uid instead of a pure function  (+ M3-d below)
                                                              -> test_a_repost_of_the_same_fact_is_one_journal
  M3-e  `_post_cheque_books`: the bank leg is not posted for 'cleared'
                                                              -> test_the_books_tie_with_a_cheque_in_every_state
  M3-f  migration: book only the apply leg (skip every later event)
                                                              -> test_upgrade_books_each_alive_cheque_once
  M3-g  migration: drop the entry-version guard / skip-if-journalled guard
                                                              -> test_upgrade_books_each_alive_cheque_once

Run (one file per process, AUDIT-010):
    pytest products/retail/tests/retail_money3_cheque_books_test.py -q
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_money3_m3_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA), AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)
for _k in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL"):
    os.environ.pop(_k, None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
import database.schema as sch  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'
D = Decimal


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def shop():
    email = f'm3c-{uuid.uuid4().hex[:8]}@test.local'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password('Money3PW12345'), 'admin', 'active'))
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': 'Money3PW12345'})
    assert r.status_code == 200, r.get_json()
    c.company_id = company_id
    c.customer = _customer(c)
    c.supplier = _supplier(c)
    return c


def _customer(c):
    r = c.post(f'{API}/customers', json={'name': f'Chq Customer {uuid.uuid4().hex[:5]}'})
    assert r.status_code == 200, r.get_json()
    cid = r.get_json()['data']['id']
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited', credit_limit=1000000 WHERE id=?", (cid,))
    conn.commit(); conn.close()
    return cid


def _supplier(c):
    r = c.post(f'{API}/suppliers', json={'name': f'Chq Supplier {uuid.uuid4().hex[:5]}'})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _credit_sale(c, amount):
    p = c.post(f'{API}/products', json={'name': 'P', 'sku': f'P-{uuid.uuid4().hex[:6]}', 'cost_price': 1,
                                        'sell_price': amount, 'tax_rate': 0, 'initial_stock': 50})
    assert p.status_code == 200, p.get_json()
    r = c.post(f'{API}/sales', json={
        'items': [{'product_id': p.get_json()['data']['id'], 'quantity': 1, 'unit_price': amount}],
        'customer_id': c.customer, 'payment_method': 'credit', 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()


def _cheque(c, direction, amount, number=None):
    party = c.customer if direction == 'in' else c.supplier
    r = c.post(f'{API}/cheques', json={
        'direction': direction, 'party_id': party, 'amount': amount, 'due_date': '2026-12-01',
        'cheque_number': number or f'CH-{uuid.uuid4().hex[:6]}'})
    assert r.status_code == 201, r.get_json()
    return r.get_json()['data']['id']


def _do(c, cid, action, **body):
    return c.post(f'{API}/cheques/{cid}/{action}', json=body)


def _ok(c, cid, action, **body):
    r = _do(c, cid, action, **body)
    assert r.status_code == 200, (action, r.get_json())


def _journals(cheque_id):
    """[(uid, [(code, debit, credit), ...])] for every journal booking this cheque, in post order."""
    conn = get_retail_conn()
    try:
        out = []
        for j in conn.execute("SELECT id, uid, memo FROM gl_journals WHERE source_uid=? ORDER BY id",
                              (cheque_id,)).fetchall():
            lines = conn.execute(
                "SELECT a.code, l.debit, l.credit FROM gl_journal_lines l JOIN gl_accounts a ON a.id=l.account_id "
                "WHERE l.journal_id=? ORDER BY l.id", (j['id'],)).fetchall()
            out.append((j['uid'], j['memo'], [(r['code'], D(str(r['debit'])), D(str(r['credit']))) for r in lines]))
        return out
    finally:
        conn.close()


def _lines(entry):
    return entry[2]


def _tb(c):
    r = c.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    return {a['code']: D(str(a['balance'])) for a in d['accounts']}, d


def dr_cr(debit_code, credit_code, amount):
    amount = D(str(amount))
    return [(debit_code, amount, D(0)), (credit_code, D(0), amount)]


# ── the per-transition proof ────────────────────────────────────────────────────

def test_every_transition_posts_the_exact_journal_once(shop):
    c = shop
    _credit_sale(c, 500.0)

    # A customer cheque through received -> deposited -> cleared -> bounced-after-clear -> reinstated.
    ch = _cheque(c, 'in', 100.0, 'CH-LIFE')
    j = _journals(ch)
    assert len(j) == 1 and j[0][0] == gl.cheque_journal_uid(ch, 'apply', 0)
    assert _lines(j[0]) == dr_cr('1150', '1100', 100), j
    _ok(c, ch, 'deposit')
    assert len(_journals(ch)) == 1, 'a deposit moves no money in the books (still under collection)'
    _ok(c, ch, 'clear')
    j = _journals(ch)
    assert len(j) == 2 and j[1][0] == gl.cheque_journal_uid(ch, 'bank', 0)
    assert _lines(j[1]) == dr_cr('1000', '1150', 100), j
    # a retried clear is refused and books nothing
    assert _do(c, ch, 'clear').status_code == 409
    assert len(_journals(ch)) == 2
    _ok(c, ch, 'bounce', reason='returned by the bank')
    j = _journals(ch)
    assert len(j) == 3 and j[2][0] == gl.cheque_journal_uid(ch, 'reverse_bank', 1)
    assert _lines(j[2]) == dr_cr('1100', '1000', 100), 'bounce AFTER clearing reverses the BANK leg'
    _ok(c, ch, 'reinstate')
    j = _journals(ch)
    assert len(j) == 4 and j[3][0] == gl.cheque_journal_uid(ch, 'apply', 2)
    assert _lines(j[3]) == dr_cr('1150', '1100', 100)
    _ok(c, ch, 'clear')
    j = _journals(ch)
    assert j[4][0] == gl.cheque_journal_uid(ch, 'bank', 2), 'a new stint has its own bank-leg identity'

    # Bounced / cancelled straight from pending reverse the HOLDING account; a write-off books nothing.
    b = _cheque(c, 'in', 40.0)
    _ok(c, b, 'bounce', reason='nsf')
    jb = _journals(b)
    assert len(jb) == 2 and jb[1][0] == gl.cheque_journal_uid(b, 'reverse', 1)
    assert _lines(jb[1]) == dr_cr('1100', '1150', 40)
    _ok(c, b, 'write-off', reason='uncollectable')
    assert len(_journals(b)) == 2
    x = _cheque(c, 'in', 25.0)
    _ok(c, x, 'cancel', reason='mistyped')
    assert _lines(_journals(x)[1]) == dr_cr('1100', '1150', 25)

    # Endorsing a cheque away books the bank leg (see _post_cheque_books).
    e = _cheque(c, 'in', 10.0)
    _ok(c, e, 'endorse', to_party_type='supplier', to_party_id=c.supplier)
    assert _lines(_journals(e)[1]) == dr_cr('1000', '1150', 10)
    # ... so an endorsed cheque that bounces reverses the BANK leg, like a cleared one
    # (verifier addition: mutating `banked` to ('cleared',) only went unnoticed without this).
    _ok(c, e, 'bounce', reason='returned to the supplier')
    je = _journals(e)
    assert je[2][0] == gl.cheque_journal_uid(e, 'reverse_bank', 1)
    assert _lines(je[2]) == dr_cr('1100', '1000', 10), 'bounce AFTER endorsing reverses the BANK leg'

    # Supplier cheques mirror it on AP / Cheques Payable.
    s = _cheque(c, 'out', 60.0)
    js = _journals(s)
    assert _lines(js[0]) == dr_cr('2000', '2050', 60)
    _ok(c, s, 'clear')
    assert _lines(_journals(s)[1]) == dr_cr('2050', '1000', 60)
    _ok(c, s, 'bounce', reason='stopped')
    assert _lines(_journals(s)[2]) == dr_cr('1000', '2000', 60), 'the bank gives it back, the debt returns'
    s2 = _cheque(c, 'out', 15.0)
    _ok(c, s2, 'cancel', reason='void')
    assert _lines(_journals(s2)[1]) == dr_cr('2050', '2000', 15)

    # Replaying a journal that already exists returns the SAME one and posts nothing.
    conn = get_retail_conn()
    try:
        before = conn.execute("SELECT COUNT(*) FROM gl_journals WHERE source_uid=?", (ch,)).fetchone()[0]
        first = conn.execute("SELECT id FROM gl_journals WHERE uid=?", (gl.cheque_journal_uid(ch, 'apply', 0),)).fetchone()[0]
        again = gl.post_cheque_journal(conn, c.company_id, 1, ch, 'CH-LIFE', 'in', 'apply', 0, 'JOD', 100.0, 'replay')
        conn.commit()
        assert again == first
        assert conn.execute("SELECT COUNT(*) FROM gl_journals WHERE source_uid=?", (ch,)).fetchone()[0] == before
    finally:
        conn.close()


def test_a_repost_of_the_same_fact_is_one_journal():
    """The uid is a pure function of (cheque, kind, index): two computers that observe the same
    bank fact mint the SAME uid. A random uid would let both post."""
    a = gl.cheque_journal_uid('chq-1', 'apply', 0)
    assert a == gl.cheque_journal_uid('chq-1', 'apply', 0)
    assert a != gl.cheque_journal_uid('chq-1', 'apply', 2)
    assert a != gl.cheque_journal_uid('chq-1', 'reverse', 0)
    assert a != gl.cheque_journal_uid('chq-2', 'apply', 0)


def test_the_trial_balance_stays_balanced_through_a_full_lifecycle(shop):
    c = shop
    _credit_sale(c, 300.0)
    ch = _cheque(c, 'in', 80.0)
    for action, body in (('deposit', {}), ('clear', {}), ('bounce', {'reason': 'nsf'}), ('reinstate', {}),
                         ('bounce', {'reason': 'nsf again'}), ('write-off', {'reason': 'gone'})):
        _ok(c, ch, action, **body)
        bal, tb = _tb(c)
        assert tb['is_balanced'] is True, (action, tb['total_debit'], tb['total_credit'])
    bal, tb = _tb(c)
    assert bal['1150'] == 0 and bal['1100'] == 300, 'a dead cheque leaves nothing under collection and the debt back'
    assert tb['subledger']['ar_ties'] is True, tb['subledger']


# ── the tie checks, every state at once ─────────────────────────────────────────

def _every_state(c):
    """Builds one cheque per state and returns the expected account balances."""
    _credit_sale(c, 1000.0)
    pending = _cheque(c, 'in', 50.0)
    deposited = _cheque(c, 'in', 20.0); _ok(c, deposited, 'deposit')
    cleared = _cheque(c, 'in', 30.0); _ok(c, cleared, 'clear')
    endorsed = _cheque(c, 'in', 10.0); _ok(c, endorsed, 'endorse', to_party_type='supplier', to_party_id=c.supplier)
    bounced = _cheque(c, 'in', 15.0); _ok(c, bounced, 'bounce', reason='nsf')
    cancelled = _cheque(c, 'in', 5.0); _ok(c, cancelled, 'cancel', reason='x')
    written = _cheque(c, 'in', 7.0); _ok(c, written, 'bounce', reason='x'); _ok(c, written, 'write-off', reason='x')
    s_pending = _cheque(c, 'out', 40.0)
    s_cleared = _cheque(c, 'out', 25.0); _ok(c, s_cleared, 'clear')
    s_bounced = _cheque(c, 'out', 12.0); _ok(c, s_bounced, 'bounce', reason='x')
    s_cancelled = _cheque(c, 'out', 8.0); _ok(c, s_cancelled, 'cancel', reason='x')
    return {'pending': pending, 'cleared': cleared, 'bounced': bounced}


def test_the_books_tie_with_a_cheque_in_every_state(shop):
    c = shop
    _every_state(c)
    bal, tb = _tb(c)
    sub = tb['subledger']
    assert sub['ar_ties'] is True and sub['ap_ties'] is True, sub
    assert D(str(sub['ar_gl'])) == D(str(sub['ar_subledger']))
    # exact balances: AR = 1000 sale - alive customer cheques (50+20+30+10); AP = -(alive supplier 40+25)
    assert bal['1100'] == D(1000 - 110), bal['1100']
    assert bal['1150'] == D(70), 'pending 50 + deposited 20 are still under collection'
    assert bal['2000'] == D(-65) and bal['2050'] == D(40), (bal['2000'], bal['2050'])
    # bank: cleared 30 + endorsed 10 in, supplier cleared 25 out
    assert bal['1000'] == D(30 + 10 - 25), bal['1000']
    assert tb['is_balanced'] is True


# ── the v50 -> v51 upgrade ──────────────────────────────────────────────────────

def _to_v50_without_cheque_journals(c):
    """Turn the live DB into what a v50 install looked like: no cheque journals, no outbox
    events for them, user_version 50. (The append-only DELETE triggers are dropped for this
    manufacturing step only; the migration chain's IF NOT EXISTS recreates them.)"""
    conn = get_retail_conn()
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM gl_journals WHERE company_id=? AND source_type='cheque'", (c.company_id,)).fetchall()]
        assert ids, 'precondition: the fixture must have cheque journals to remove'
        conn.execute("DROP TRIGGER IF EXISTS trg_gl_journals_no_delete")
        conn.execute("DROP TRIGGER IF EXISTS trg_gl_journal_lines_no_delete")
        marks = ','.join('?' * len(ids))
        conn.execute(f"DELETE FROM gl_journal_lines WHERE journal_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM gl_journals WHERE id IN ({marks})", ids)
        conn.execute("DELETE FROM sync_outbox WHERE entity_type='gl_journal' AND payload LIKE '%\"source_type\": \"cheque\"%'")
        conn.execute("PRAGMA user_version = 50")
        conn.commit()
        return conn.execute("PRAGMA database_list").fetchone()[2]
    finally:
        conn.close()


def _migrate(db):
    from commercial_runtime.security.migration_safety import ensure_schema_version
    backup_dir = tempfile.mkdtemp(prefix='aura-m3-backup-')
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
        shutil.rmtree(backup_dir, ignore_errors=True)


def _uids_by_cheque(c):
    conn = get_retail_conn()
    try:
        out = {}
        for r in conn.execute("SELECT source_uid, uid FROM gl_journals WHERE company_id=? AND source_type='cheque'",
                              (c.company_id,)).fetchall():
            out.setdefault(r['source_uid'], []).append(r['uid'])
        return out
    finally:
        conn.close()


def test_upgrade_books_each_alive_cheque_once(shop):
    c = shop
    ids = _every_state(c)
    bal_before, tb_before = _tb(c)
    live_uids = {k: sorted(v) for k, v in _uids_by_cheque(c).items()}
    assert len(live_uids) == 11, live_uids
    db = _to_v50_without_cheque_journals(c)

    # Precondition, the gap v51 closes: with the journals gone the books are above the subledger.
    bal_gap, tb_gap = _tb(c)
    assert tb_gap['subledger']['ar_ties'] is False and tb_gap['subledger']['ap_ties'] is False, tb_gap['subledger']

    _migrate(db)
    bal, tb = _tb(c)
    sub = tb['subledger']
    assert sub['ar_ties'] is True and sub['ap_ties'] is True, sub
    assert tb['is_balanced'] is True
    # Same TOTALS as the live-journal run ...
    for code in ('1100', '1150', '2000', '2050', '1000'):
        assert bal[code] == bal_before[code], (code, bal[code], bal_before[code])
    # ... and, uid for uid, the SAME journals the live path posted for the same events: the upgrade
    # replays each cheque's history rather than posting a lump, so a peer that upgraded earlier
    # (or a live transition) and this computer mint the identical journals and they collapse.
    assert {k: sorted(v) for k, v in _uids_by_cheque(c).items()} == live_uids
    pend = _journals(ids['pending'])
    assert len(pend) == 1 and pend[0][0] == gl.cheque_journal_uid(ids['pending'], 'apply', 0)
    assert _lines(pend[0]) == dr_cr('1150', '1100', 50)
    clr = _journals(ids['cleared'])
    assert [j[0] for j in clr] == [gl.cheque_journal_uid(ids['cleared'], 'apply', 0),
                                   gl.cheque_journal_uid(ids['cleared'], 'bank', 0)]
    assert _lines(clr[1]) == dr_cr('1000', '1150', 30), 'an already-banked cheque books its bank leg'
    bnc = _journals(ids['bounced'])
    assert [j[0] for j in bnc] == [gl.cheque_journal_uid(ids['bounced'], 'apply', 0),
                                   gl.cheque_journal_uid(ids['bounced'], 'reverse', 1)]

    # Replaying the chain (a failed launch re-enters it) books nothing more.
    conn = get_retail_conn()
    try:
        conn.execute("PRAGMA user_version = 50")
        conn.commit()
    finally:
        conn.close()
    _migrate(db)
    again, tb2 = _tb(c)
    assert again == bal and tb2['subledger']['ar_ties'] is True

    # After the upgrade a cheque keeps working: bouncing the banked one reverses the bank leg and
    # the books still tie (the opening stands in for apply + bank).
    _ok(c, ids['cleared'], 'bounce', reason='post-upgrade')
    bal3, tb3 = _tb(c)
    assert tb3['subledger']['ar_ties'] is True, tb3['subledger']
    assert bal3['1000'] == bal['1000'] - D(30)


def _step(db, user_version):
    """Run ONLY the cheque-books step against `db` stamped `user_version` (a chain replay)."""
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA user_version = {user_version}")
        sch._migrate_add_cheque_books(conn)
        conn.commit()
    finally:
        conn.close()


def _count_cheque_journals(company_id):
    conn = get_retail_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM gl_journals WHERE company_id=? AND source_type='cheque'",
                            (company_id,)).fetchone()[0]
    finally:
        conn.close()


def test_a_cheque_already_journalled_is_not_booked_again(shop):
    """A computer that upgraded earlier has posted (and synced) this cheque's journals. The step
    derives the SAME uids from the same events, so it finds them present and posts nothing on top
    (M4: this used to be a skip-if-journalled guard; the uid makes the guard unnecessary)."""
    c = shop
    _credit_sale(c, 100.0)
    ch = _cheque(c, 'in', 40.0)
    before = _count_cheque_journals(c.company_id)
    assert before == 1
    conn = get_retail_conn()
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    conn.close()
    _step(db, 50)                      # a replay at the OLD version: the entry guard lets it run
    assert _count_cheque_journals(c.company_id) == before, 'booked a cheque that already had its journal'
    _, tb = _tb(c)
    assert tb['subledger']['ar_ties'] is True


def test_the_step_only_runs_on_a_genuine_upgrade(shop):
    """Entry user_version >= 51 means the upgrade already happened: an alive cheque with no journal
    at that point must NOT be given an opening one (it would be a replay on a later bump)."""
    c = shop
    _credit_sale(c, 100.0)
    _cheque(c, 'in', 40.0)
    db = _to_v50_without_cheque_journals(c)
    _step(db, 51)
    assert _count_cheque_journals(c.company_id) == 0, 'the step ran although the DB was already at v51'
    _step(db, 50)
    assert _count_cheque_journals(c.company_id) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
