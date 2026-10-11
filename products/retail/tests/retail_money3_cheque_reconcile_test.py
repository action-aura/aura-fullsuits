"""MONEY-3 M4 (retail v51): a cheque's books follow the MERGED history, not each writer's local view.

BEFORE M4 each computer posted a cheque's journals from ITS OWN fold (`_post_cheque_books`). Two
computers that each did one legal thing offline (A bounces, B clears one deposited cheque) left
1150 = -50 / 1000 = +50 on both books (D1), and -- found while planning -- when A's bounce sorted
first the fold admitted B's `cleared` as an APPLY crossing at index 2 that NOBODY ever wrote a
payments row for, so the status said "cleared" (money in) while the customer's balance said they
still owed (D2). NOW `desired_postings(events)` (core/retail/cheques.py) says what the books
should hold from the full event set and `reconcile_cheque` (api/retail_api.py) makes the books
say it, idempotently, whenever the event set changes -- on the writing computer inside its own
transaction and on every other computer from a post-pull sweep.

What each test proves, and what it cannot:
  * the `desired_postings` unit tests pin the kind choice (reverse vs reverse_bank), the bank
    index, inert events and arrival-order independence -- pure, so they cannot see wiring.
  * the DB tests drive the REAL sweep against a REAL database whose event set is the merged one
    (events inserted the way a sync apply inserts them), so they see wiring but not two processes;
    retail_money3_cheque_reconcile_twodevice_test.py is the two-process half.
  * the upgrade tests go through the REAL `ensure_schema_version` chain.

MUTATIONS (each performed; red quoted in the crew report):
  R-a  cheques.desired_postings: always 'reverse' (never 'reverse_bank')
         -> test_desired_postings_choose_reverse_bank_only_after_a_bank_leg
  R-b  cheques.desired_postings: bank leg keyed on the crossing index being posted, not the stint's apply
         -> test_desired_postings_ordinary_lifecycle
  R-c  cheque_books: do not reverse a journal the merged history no longer wants
         -> test_a_journal_the_history_no_longer_wants_is_reversed_and_comes_back_when_it_does
  R-d  cheque_books: do not un-reverse a journal the history wants again (same test)
  R-e  retail_api.reconcile_cheque: skip the missing-payment writer
         -> test_a_crossing_admitted_by_the_merge_gets_its_payment_row
  R-f  retail_api._apply_cheque_event: reconcile BEFORE the event is queued
         -> test_the_event_is_queued_before_the_journals_it_justifies
  R-g  reconcile_pending_cheques_all_companies: drop the company filter on the cheque lookup
         -> test_the_sweep_is_company_scoped
  R-h  schema._migrate_add_cheque_books: date by today instead of by the event
         -> test_upgrade_dates_each_journal_by_its_event
  F1   schema._migrate_add_cheque_books: do not queue the existing cheques
         -> test_a_v50_database_damaged_by_the_old_race_is_healed_by_the_upgrade_plus_first_sweep
  N8   sweep: no ROLLBACK TO SAVEPOINT / no per-cheque isolation (re-raise)
         -> test_a_failing_cheque_does_not_stop_the_others_...
  N2   cheque_books._chain_end: stop after one link -> test_..._reverse_unreverse_reverse_three_links
  N4   cheque_books: reversal dated today -> test_a_reversal_is_dated_by_the_latest_event_not_by_today
  N1   cheque_books.event_business_date: UTC instead of local -> test_event_business_date_uses_the_local_zone_not_utc

Run (one file per process, AUDIT-010):
    pytest products/retail/tests/retail_money3_cheque_reconcile_test.py -q
"""
import os
import random
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_money3_m4_"))
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
from api import retail_api  # noqa: E402
from core.retail import cheques as engine  # noqa: E402
from core.retail import general_ledger as gl  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'
D = Decimal


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── pure: desired_postings ──────────────────────────────────────────────────────

def E(eid, minute, frm, to):
    """An event row as the fold sees it. Minutes are zero-padded so text order = time order."""
    return {'id': eid, 'event_type': 'x', 'from_status': frm, 'to_status': to,
            'created_at_utc': f'2026-10-01T10:{minute:02d}:00+00:00'}


def kinds(events):
    return [(p.kind, p.index, p.event_id) for p in engine.desired_postings(events)]


def test_desired_postings_ordinary_lifecycle():
    ev = [E('c', 0, None, 'pending'), E('d', 1, 'pending', 'deposited'), E('k', 2, 'deposited', 'cleared'),
          E('b', 3, 'cleared', 'bounced'), E('r', 4, 'bounced', 'pending'), E('k2', 5, 'pending', 'cleared')]
    assert kinds(ev) == [('apply', 0, 'c'), ('bank', 0, 'k'), ('reverse_bank', 1, 'b'),
                         ('apply', 2, 'r'), ('bank', 2, 'k2')]
    # a cheque bounced straight from pending reverses the HOLDING account; a write-off books nothing
    ev = [E('c', 0, None, 'pending'), E('b', 1, 'pending', 'bounced'), E('w', 2, 'bounced', 'written_off')]
    assert kinds(ev) == [('apply', 0, 'c'), ('reverse', 1, 'b')]
    # a deposit alone books nothing; cancel is a reverse
    ev = [E('c', 0, None, 'pending'), E('d', 1, 'pending', 'deposited')]
    assert kinds(ev) == [('apply', 0, 'c')]
    ev = [E('c', 0, None, 'pending'), E('x', 1, 'pending', 'cancelled')]
    assert kinds(ev) == [('apply', 0, 'c'), ('reverse', 1, 'x')]
    # endorsed books the bank leg, like cleared
    ev = [E('c', 0, None, 'pending'), E('e', 1, 'pending', 'endorsed')]
    assert kinds(ev) == [('apply', 0, 'c'), ('bank', 0, 'e')]


def test_desired_postings_choose_reverse_bank_only_after_a_bank_leg():
    # clear sorts first, the bounce (written from 'pending') is a real crossing AFTER the bank leg
    ev = [E('c', 0, None, 'pending'), E('k', 1, 'pending', 'cleared'), E('b', 2, 'pending', 'bounced')]
    assert kinds(ev) == [('apply', 0, 'c'), ('bank', 0, 'k'), ('reverse_bank', 1, 'b')]
    # bounce sorts first: no bank leg yet, so a plain reverse -- and the clear is then an APPLY
    # crossing at index 2 (bounced -> cleared crosses DEAD -> LIVE), which also banks
    ev = [E('c', 0, None, 'pending'), E('b', 1, 'pending', 'bounced'), E('k', 2, 'pending', 'cleared')]
    assert kinds(ev) == [('apply', 0, 'c'), ('reverse', 1, 'b'), ('apply', 2, 'k'), ('bank', 2, 'k')]
    # the endorse flavour of both
    ev = [E('c', 0, None, 'pending'), E('b', 1, 'pending', 'bounced'), E('e', 2, 'pending', 'endorsed')]
    assert kinds(ev) == [('apply', 0, 'c'), ('reverse', 1, 'b'), ('apply', 2, 'e'), ('bank', 2, 'e')]
    ev = [E('c', 0, None, 'pending'), E('e', 1, 'pending', 'endorsed'), E('b', 2, 'pending', 'bounced')]
    assert kinds(ev) == [('apply', 0, 'c'), ('bank', 0, 'e'), ('reverse_bank', 1, 'b')]


def test_desired_postings_ignore_inert_events_and_arrival_order():
    base = [E('c', 0, None, 'pending'), E('d', 1, 'pending', 'deposited'), E('k', 2, 'deposited', 'cleared'),
            # a second clear written elsewhere from 'deposited': inert once 'k' has cleared it
            E('k_dup', 3, 'deposited', 'cleared')]
    assert kinds(base) == [('apply', 0, 'c'), ('bank', 0, 'k')]
    ev = [E('c', 0, None, 'pending'), E('b', 1, 'pending', 'bounced'), E('k', 2, 'pending', 'cleared')]
    want = kinds(ev)
    rng = random.Random(7)
    for _ in range(8):
        shuffled = list(ev)
        rng.shuffle(shuffled)
        assert kinds(shuffled) == want


# ── fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture
def shop():
    email = f'm4-{uuid.uuid4().hex[:8]}@test.local'
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
    _credit_sale(c, 200.0)
    return c


def _customer(c):
    r = c.post(f'{API}/customers', json={'name': f'M4 Customer {uuid.uuid4().hex[:5]}'})
    assert r.status_code == 200, r.get_json()
    cid = r.get_json()['data']['id']
    conn = get_retail_conn()
    conn.execute("UPDATE customers SET credit_mode='unlimited', credit_limit=1000000 WHERE id=?", (cid,))
    conn.commit(); conn.close()
    return cid


def _credit_sale(c, amount):
    p = c.post(f'{API}/products', json={'name': 'P', 'sku': f'P-{uuid.uuid4().hex[:6]}', 'cost_price': 1,
                                        'sell_price': amount, 'tax_rate': 0, 'initial_stock': 50})
    assert p.status_code == 200, p.get_json()
    r = c.post(f'{API}/sales', json={
        'items': [{'product_id': p.get_json()['data']['id'], 'quantity': 1, 'unit_price': amount}],
        'customer_id': c.customer, 'payment_method': 'credit', 'amount_paid': 0,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()


def _cheque(c, amount=80.0, number=None):
    r = c.post(f'{API}/cheques', json={
        'direction': 'in', 'party_id': c.customer, 'amount': amount, 'due_date': '2026-12-01',
        'cheque_number': number or f'CH-{uuid.uuid4().hex[:6]}'})
    assert r.status_code == 201, r.get_json()
    return r.get_json()['data']['id']


def _ok(c, cid, action, **body):
    r = c.post(f'{API}/cheques/{cid}/{action}', json=body)
    assert r.status_code == 200, (action, r.get_json())
    return r.get_json()['data']


def _tb(c):
    r = c.get(f'{API}/reports/trial-balance')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    return {a['code']: D(str(a['balance'])) for a in d['accounts']}, d


def _rows(sql, params=()):
    conn = get_retail_conn()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _balance(customer_id):
    return D(str(_rows("SELECT credit_balance FROM customers WHERE id=?", (customer_id,))[0]['credit_balance']))


def _event_time(cheque_id, event_type='created'):
    raw = _rows("SELECT created_at_utc FROM cheque_events WHERE cheque_id=? AND event_type=?",
                (cheque_id, event_type))[0]['created_at_utc']
    return datetime.fromisoformat(raw)


def _inject_event(cheque_id, event_type, frm, to, at):
    """Insert an event the way a sync apply does (verbatim columns, no business logic), and queue
    the cheque for the post-pull sweep exactly as the apply branch does."""
    cid = _rows("SELECT company_id FROM cheques WHERE id=?", (cheque_id,))[0]['company_id']
    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO cheque_events (id, cheque_id, event_type, from_status, to_status, created_by, "
            "created_at_utc) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), cheque_id, event_type, frm, to, 'peer', at.isoformat()))
        conn.execute("INSERT OR IGNORE INTO cheque_reconcile_pending (company_id, cheque_id) VALUES (?,?)",
                     (cid, cheque_id))
        conn.commit()
    finally:
        conn.close()


def _sweep():
    conn = get_retail_conn()
    try:
        n = retail_api.reconcile_pending_cheques_all_companies(conn)
        conn.commit()
        return n
    finally:
        conn.close()


def _cheque_journals(cheque_id):
    """[(uid, memo, business_date, [(code, debit, credit)])] for the journals that BOOK this cheque."""
    conn = get_retail_conn()
    try:
        out = []
        for j in conn.execute("SELECT id, uid, memo, business_date FROM gl_journals WHERE source_uid=? "
                              "AND source_type='cheque' ORDER BY id", (cheque_id,)).fetchall():
            lines = conn.execute(
                "SELECT a.code, l.debit, l.credit FROM gl_journal_lines l JOIN gl_accounts a ON a.id=l.account_id "
                "WHERE l.journal_id=? ORDER BY l.id", (j['id'],)).fetchall()
            out.append((j['uid'], j['memo'], j['business_date'],
                        [(r['code'], D(str(r['debit'])), D(str(r['credit']))) for r in lines]))
        return out
    finally:
        conn.close()


def _uids(cheque_id):
    return [j[0] for j in _cheque_journals(cheque_id)]


# ── D2: the payments half ───────────────────────────────────────────────────────

def test_a_crossing_admitted_by_the_merge_gets_its_payment_row(shop):
    """Bounce sorts first, the other computer's clear is admitted as an APPLY crossing at index 2.
    Nobody wrote money_uid(cheque, 2): the status said 'cleared' (money in) while the customer
    still owed. The reconciler writes the missing row, the customer's balance follows, and a
    second sweep adds nothing."""
    c = shop
    ch = _cheque(c, 80.0)
    t0 = _event_time(ch)
    _inject_event(ch, 'bounced', 'pending', 'bounced', t0 + timedelta(seconds=1))
    _inject_event(ch, 'cleared', 'pending', 'cleared', t0 + timedelta(seconds=2))
    assert _balance(c.customer) == D(120), 'precondition: only the creation leg exists'

    assert _sweep() >= 1
    pays = {r['uid']: r for r in _rows("SELECT uid, direction, amount, method FROM payments "
                                       "WHERE related_type='cheque'")}
    want = {engine.money_uid(ch, i) for i in (0, 1, 2)}
    assert set(pays) == want, (sorted(pays), sorted(want))
    assert pays[engine.money_uid(ch, 1)]['direction'] == 'out' and pays[engine.money_uid(ch, 2)]['direction'] == 'in'
    assert all(r['method'] == 'check' and r['amount'] == 80 for r in pays.values())
    assert _balance(c.customer) == D(120), 'cleared (money in): 200 owed - 80 = 120'
    assert _uids(ch) == [gl.cheque_journal_uid(ch, 'apply', 0), gl.cheque_journal_uid(ch, 'reverse', 1),
                         gl.cheque_journal_uid(ch, 'apply', 2), gl.cheque_journal_uid(ch, 'bank', 2)]
    bal, tb = _tb(c)
    assert tb['is_balanced'] is True and tb['subledger']['ar_ties'] is True, tb['subledger']
    assert bal['1150'] == 0 and bal['1000'] == 80 and bal['1100'] == 120, bal

    before = (_rows("SELECT COUNT(*) n FROM payments")[0]['n'], _rows("SELECT COUNT(*) n FROM gl_journals")[0]['n'])
    _inject_event_queue_only(ch)
    _sweep()
    after = (_rows("SELECT COUNT(*) n FROM payments")[0]['n'], _rows("SELECT COUNT(*) n FROM gl_journals")[0]['n'])
    assert before == after, 'a second reconcile of the same event set must change nothing'


def _inject_event_queue_only(cheque_id):
    cid = _rows("SELECT company_id FROM cheques WHERE id=?", (cheque_id,))[0]['company_id']
    conn = get_retail_conn()
    try:
        conn.execute("INSERT OR IGNORE INTO cheque_reconcile_pending (company_id, cheque_id) VALUES (?,?)",
                     (cid, cheque_id))
        conn.commit()
    finally:
        conn.close()


# ── journal-before-event ────────────────────────────────────────────────────────

def test_a_journal_the_history_no_longer_wants_is_reversed_and_comes_back_when_it_does(shop):
    """A peer's journal for a bounce arrives BEFORE the bounce event does. While the event is
    missing, the merged history does not want that journal, so it is reversed (churn is allowed);
    when the event lands the journal is wanted again, so the reversal is itself reversed. The
    NET is right at every step and the reversal uids are deterministic."""
    c = shop
    ch = _cheque(c, 80.0)
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM cheques WHERE id=?", (ch,)).fetchone()
    # the peer's journal for crossing 1 lands first
    gl.post_cheque_journal(conn, c.company_id, row['branch_id'], ch, row['cheque_number'], 'in', 'reverse', 1,
                           row['currency'], row['amount'], 'peer bounce')
    conn.commit(); conn.close()
    stray = gl.cheque_journal_uid(ch, 'reverse', 1)
    bal, _ = _tb(c)
    assert bal['1150'] == 0, 'precondition: the stray journal alone cancels the apply leg'

    # another event for the same cheque arrives meanwhile (a non-crossing deposit) -> sweep
    _inject_event(ch, 'deposited', 'pending', 'deposited', _event_time(ch) + timedelta(seconds=1))
    _sweep()
    bal, tb = _tb(c)
    assert bal['1150'] == 80 and bal['1100'] == 120, ('the history without the bounce wants only the apply', bal)
    assert tb['is_balanced'] is True
    chain = _rows("SELECT j.uid FROM gl_journals j WHERE j.reversed_journal_id="
                  "(SELECT id FROM gl_journals WHERE uid=?)", (stray,))
    assert [r['uid'] for r in chain] == [gl.reversal_uid(stray)], chain

    # the bounce lands: written from 'deposited' it is a real crossing at index 1
    _inject_event(ch, 'bounced', 'deposited', 'bounced', _event_time(ch) + timedelta(seconds=2))
    _sweep()
    bal, tb = _tb(c)
    assert bal['1150'] == 0 and bal['1100'] == 200, ('the bounce is wanted again: net back to dead', bal)
    assert tb['is_balanced'] is True and tb['subledger']['ar_ties'] is True, tb['subledger']
    rev1 = gl.reversal_uid(stray)
    assert _rows("SELECT COUNT(*) n FROM gl_journals WHERE uid=?", (gl.reversal_uid(rev1),))[0]['n'] == 1, \
        'the reversal was un-reversed exactly once'
    n_before = _rows("SELECT COUNT(*) n FROM gl_journals")[0]['n']
    _inject_event_queue_only(ch)
    _sweep()
    assert _rows("SELECT COUNT(*) n FROM gl_journals")[0]['n'] == n_before, 'settled: a further sweep posts nothing'


# ── live path ordering ──────────────────────────────────────────────────────────

def test_the_event_is_queued_before_the_journals_it_justifies(shop):
    """A peer must never receive a journal before the event that justifies it, or its reconciler
    would reverse the journal and then un-reverse it. The outbox is a queue: order of insertion."""
    c = shop
    ch = _cheque(c, 80.0)
    data = _ok(c, ch, 'bounce', reason='nsf')
    rows = _rows("SELECT rowid AS pos, entity_type, entity_id, payload FROM sync_outbox ORDER BY rowid")
    ev_pos = [r['pos'] for r in rows if r['entity_type'] == 'cheque_event' and r['entity_id'] == data['event_id']]
    rev_uid = gl.cheque_journal_uid(ch, 'reverse', 1)
    jr_pos = [r['pos'] for r in rows if r['entity_type'] == 'gl_journal' and rev_uid in r['payload']]
    assert len(ev_pos) == 1 and len(jr_pos) == 1, (ev_pos, jr_pos)
    assert ev_pos[0] < jr_pos[0], 'the bounce event must be queued before its reversing journal'
    # the created event precedes the apply journal the same way
    ev0 = _rows("SELECT id FROM cheque_events WHERE cheque_id=? AND event_type='created'", (ch,))[0]['id']
    pos0 = [r['pos'] for r in rows if r['entity_type'] == 'cheque_event' and r['entity_id'] == ev0][0]
    jpos0 = [r['pos'] for r in rows if r['entity_type'] == 'gl_journal'
             and gl.cheque_journal_uid(ch, 'apply', 0) in r['payload']][0]
    assert pos0 < jpos0


# ── sweep scoping ───────────────────────────────────────────────────────────────

def test_the_sweep_is_company_scoped(shop):
    """A pending row names (company, cheque). A row whose company does not own that cheque must
    be dropped WITHOUT posting anything -- never reconcile company 1's cheque into company 2's
    books. The allow half: the same event set queued under the right company IS reconciled."""
    c = shop
    ch = _cheque(c, 80.0)
    t0 = _event_time(ch)
    conn = get_retail_conn()
    conn.execute("INSERT INTO cheque_events (id, cheque_id, event_type, from_status, to_status, created_by, "
                 "created_at_utc) VALUES (?,?,?,?,?,?,?)",
                 (str(uuid.uuid4()), ch, 'cleared', 'pending', 'cleared', 'peer',
                  (t0 + timedelta(seconds=1)).isoformat()))
    conn.execute("INSERT INTO cheque_reconcile_pending (company_id, cheque_id) VALUES (?,?)",
                 (str(uuid.uuid4()), ch))
    conn.commit(); conn.close()
    n_journals = len(_cheque_journals(ch))
    _sweep()
    assert _rows("SELECT COUNT(*) n FROM cheque_reconcile_pending")[0]['n'] == 0, 'the foreign row was not drained'
    assert len(_cheque_journals(ch)) == n_journals, 'a foreign company reconciled this cheque'

    _inject_event_queue_only(ch)
    _sweep()
    assert gl.cheque_journal_uid(ch, 'bank', 0) in _uids(ch), 'the owning company must be reconciled'


# ── upgrade ─────────────────────────────────────────────────────────────────────

def _to_v50_without_cheque_journals(c):
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
    backup_dir = tempfile.mkdtemp(prefix='aura-m4-backup-')
    conn = sqlite3.connect(db, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        ensure_schema_version(conn, db, sch.RETAIL_SCHEMA_VERSION, sch._migrate_retail_schema, backup_dir)
    finally:
        conn.close()
        shutil.rmtree(backup_dir, ignore_errors=True)


def _local_date(dt):
    return dt.astimezone().strftime('%Y-%m-%d')


def test_upgrade_dates_each_journal_by_its_event(shop):
    """D4. A cheque cleared 10 days before the upgrade must have its bank leg on THAT day, not in
    the upgrade day's cash flow; the apply leg on the day it was received."""
    c = shop
    ch = _cheque(c, 80.0)
    _ok(c, ch, 'clear')
    long_ago = datetime.now(timezone.utc) - timedelta(days=12)
    cleared_at = datetime.now(timezone.utc) - timedelta(days=10)
    conn = get_retail_conn()
    conn.execute("UPDATE cheque_events SET created_at_utc=? WHERE cheque_id=? AND event_type='created'",
                 (long_ago.isoformat(), ch))
    conn.execute("UPDATE cheque_events SET created_at_utc=? WHERE cheque_id=? AND event_type='cleared'",
                 (cleared_at.isoformat(), ch))
    conn.commit(); conn.close()
    db = _to_v50_without_cheque_journals(c)
    _migrate(db)
    j = {uid: d for uid, _m, d, _l in _cheque_journals(ch)}
    assert j[gl.cheque_journal_uid(ch, 'apply', 0)] == _local_date(long_ago), j
    assert j[gl.cheque_journal_uid(ch, 'bank', 0)] == _local_date(cleared_at), j
    assert len(j) == 2


def test_upgrade_posts_the_uids_the_live_path_posts_and_is_a_noop_after_a_peers_journals(shop):
    """D3, both directions. (a) The journals the upgrade derives are, uid for uid, the journals the
    live path posts for the same history -- so a peer that upgraded earlier and sent its own
    collapse into them on arrival. (b) Where the peer's journals ARE already here, the upgrade
    posts nothing on top."""
    c = shop
    ch = _cheque(c, 80.0)
    _ok(c, ch, 'deposit'); _ok(c, ch, 'clear'); _ok(c, ch, 'bounce', reason='nsf'); _ok(c, ch, 'reinstate')
    live = _uids(ch)
    assert len(live) == 4
    # (b) the upgrade replayed over a database that already holds the peer's journals
    conn = get_retail_conn()
    db = conn.execute("PRAGMA database_list").fetchone()[2]
    conn.execute("PRAGMA user_version = 50"); conn.commit(); conn.close()
    n = _rows("SELECT COUNT(*) n FROM gl_journals")[0]['n']
    _migrate(db)
    assert _rows("SELECT COUNT(*) n FROM gl_journals")[0]['n'] == n
    # (a) wipe them, upgrade, compare uid sets
    db = _to_v50_without_cheque_journals(c)
    _migrate(db)
    assert sorted(_uids(ch)) == sorted(live)
    _, tb = _tb(c)
    assert tb['is_balanced'] is True and tb['subledger']['ar_ties'] is True


def test_the_upgrade_creates_the_pending_table_scoped_by_company(shop):
    cols = {r['name'] for r in _rows("PRAGMA table_info(cheque_reconcile_pending)")}
    assert {'company_id', 'cheque_id'} <= cols, cols


# ── allow half ──────────────────────────────────────────────────────────────────

def test_an_ordinary_single_computer_lifecycle_posts_exactly_the_same_journals_as_before(shop):
    """The allow half: reconciling from the merged history must be invisible on one computer. The
    uids are pinned as LITERALS so a drift in the namespace or the index convention fails here even
    if the engine and the writer drift together; the dates are today, as `_today()` gave them."""
    c = shop
    ch = _cheque(c, 100.0, 'CH-ALLOW')
    _ok(c, ch, 'deposit'); _ok(c, ch, 'clear'); _ok(c, ch, 'bounce', reason='r'); _ok(c, ch, 'reinstate')
    _ok(c, ch, 'clear')
    j = _cheque_journals(ch)
    assert [x[0] for x in j] == [gl.cheque_journal_uid(ch, k, i) for k, i in (
        ('apply', 0), ('bank', 0), ('reverse_bank', 1), ('apply', 2), ('bank', 2))]
    today = datetime.now().strftime('%Y-%m-%d')
    assert all(x[2] == today for x in j)
    assert [x[1] for x in j] == ['Cheque CH-ALLOW received', 'Cheque CH-ALLOW cleared', 'Cheque CH-ALLOW bounced',
                                 'Cheque CH-ALLOW received', 'Cheque CH-ALLOW cleared']
    # literal pin: uuid5(3e8a52c7-..., "<id>:apply:0") for a fixed id
    assert gl.cheque_journal_uid('chq-1', 'apply', 0) == '7bdeaf0f-b8c7-5e44-badd-488fe4d60b19'
    assert gl.cheque_journal_uid('chq-1', 'bank', 2) == '01d1decc-1dfc-5804-ba26-ca2842757588'
    # and the money legs: exactly one payments row per crossing, none extra
    pays = _rows("SELECT uid FROM payments WHERE related_type='cheque' AND notes='Cheque CH-ALLOW'")
    assert sorted(p['uid'] for p in pays) == sorted(engine.money_uid(ch, i) for i in (0, 1, 2))


# ── verifier round: F1 (upgrade heals a damaged v50) and F2 (mutant killers) ────

def test_a_v50_database_damaged_by_the_old_race_is_healed_by_the_upgrade_plus_first_sweep(shop):
    """F1. The old code, under the race (A bounces, B's clear sorts after the bounce), left a
    cheque whose merged fold admits a crossing at index 2 with NO payments row money_uid(cheque, 2):
    the customer balance said 200 (still owed) while the books, once they follow the fold, say 120.
    The v51 upgrade catches the JOURNALS up but cannot import api/, so unless it QUEUES the cheque
    the payments half never runs for it and AR stays untied until some later cheque event."""
    c = shop
    ch = _cheque(c, 80.0)
    _ok(c, ch, 'bounce', reason='nsf')
    # B's clear reached this computer as a bare event: no payments row, nothing queued (v50 had no queue)
    conn = get_retail_conn()
    conn.execute("INSERT INTO cheque_events (id, cheque_id, event_type, from_status, to_status, created_by, "
                 "created_at_utc) VALUES (?,?,?,?,?,?,?)",
                 (str(uuid.uuid4()), ch, 'cleared', 'pending', 'cleared', 'peer',
                  (_event_time(ch, 'bounced') + timedelta(seconds=1)).isoformat()))
    conn.execute("DROP TABLE cheque_reconcile_pending")   # a v50 database has no queue
    conn.commit(); conn.close()
    assert _balance(c.customer) == D(200), 'precondition: the damage -- the crossing has no payment row'
    assert not _rows("SELECT 1 FROM payments WHERE uid=?", (engine.money_uid(ch, 2),))

    db = _to_v50_without_cheque_journals(c)
    _migrate(db)
    queued = _rows("SELECT company_id, cheque_id FROM cheque_reconcile_pending")
    assert (c.company_id, ch) in {(r['company_id'], r['cheque_id']) for r in queued}, queued
    _, tb = _tb(c)
    assert tb['subledger']['ar_ties'] is False, 'precondition: the journals alone do not heal it'

    _sweep()
    assert _balance(c.customer) == D(120), 'the missing payment row was written'
    _, tb = _tb(c)
    assert tb['is_balanced'] is True and tb['subledger']['ar_ties'] is True, tb['subledger']
    assert _rows("SELECT COUNT(*) n FROM cheque_reconcile_pending")[0]['n'] == 0


def test_a_failing_cheque_does_not_stop_the_others_and_keeps_its_row_and_loses_its_partial_writes(
        shop, monkeypatch):
    """N8. One cheque raising mid-reconcile must (a) not stop the sweep, (b) keep ITS pending row for
    the next tick, (c) roll back what it had already written. The failing cheque is queued FIRST."""
    c = shop
    bad, good = _cheque(c, 80.0), _cheque(c, 50.0)
    for ch in (bad, good):
        _inject_event(ch, 'cleared', 'pending', 'cleared', _event_time(ch) + timedelta(seconds=1))
    real = retail_api.reconcile_cheque

    def flaky(conn, cid, cheque, **kw):
        real(conn, cid, cheque, **kw)          # writes the bank journal and the like ...
        if cheque['id'] == bad:
            raise RuntimeError('boom')         # ... then dies

    monkeypatch.setattr(retail_api, 'reconcile_cheque', flaky)
    assert _sweep() == 1
    assert gl.cheque_journal_uid(good, 'bank', 0) in _uids(good), 'the healthy cheque was not reconciled'
    assert gl.cheque_journal_uid(bad, 'bank', 0) not in _uids(bad), 'the failing cheque kept a partial write'
    left = _rows("SELECT cheque_id FROM cheque_reconcile_pending")
    assert [r['cheque_id'] for r in left] == [bad], left
    monkeypatch.setattr(retail_api, 'reconcile_cheque', real)
    assert _sweep() == 1
    assert gl.cheque_journal_uid(bad, 'bank', 0) in _uids(bad), 'the retry must reconcile it'
    assert _rows("SELECT COUNT(*) n FROM cheque_reconcile_pending")[0]['n'] == 0


def _chain_len(journal_uid):
    n, current = 0, _rows("SELECT id FROM gl_journals WHERE uid=?", (journal_uid,))[0]['id']
    while True:
        nxt = _rows("SELECT id FROM gl_journals WHERE reversed_journal_id=?", (current,))
        if not nxt:
            return n
        current, n = nxt[0]['id'], n + 1


def test_a_journal_survives_reverse_unreverse_reverse_three_links(shop):
    """N2. The chain must be followed to its END, not one link: reversed (1 link), re-instated
    (2 links), withdrawn again (3 links, net-reversed)."""
    c = shop
    ch = _cheque(c, 80.0)
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM cheques WHERE id=?", (ch,)).fetchone()
    gl.post_cheque_journal(conn, c.company_id, row['branch_id'], ch, row['cheque_number'], 'in', 'reverse', 1,
                           row['currency'], row['amount'], 'peer bounce')
    conn.commit(); conn.close()
    stray = gl.cheque_journal_uid(ch, 'reverse', 1)
    t0 = _event_time(ch)

    _inject_event(ch, 'deposited', 'pending', 'deposited', t0 + timedelta(seconds=1))
    _sweep()
    assert _chain_len(stray) == 1 and _tb(c)[0]['1150'] == 80, 'unwanted -> reversed'

    _inject_event(ch, 'bounced', 'deposited', 'bounced', t0 + timedelta(seconds=3))
    _sweep()
    assert _chain_len(stray) == 2 and _tb(c)[0]['1150'] == 0, 'wanted again -> the reversal reversed'

    # a clear sorting between the deposit and the bounce turns the bounce into reverse_bank:
    # (reverse, 1) is unwanted AGAIN
    _inject_event(ch, 'cleared', 'deposited', 'cleared', t0 + timedelta(seconds=2))
    _sweep()
    assert _chain_len(stray) == 3, 'a third link must be added on top of the second'
    bal, tb = _tb(c)
    assert bal['1150'] == 0 and tb['is_balanced'] is True, bal
    n = _rows("SELECT COUNT(*) n FROM gl_journals")[0]['n']
    _inject_event_queue_only(ch)
    _sweep()
    assert _rows("SELECT COUNT(*) n FROM gl_journals")[0]['n'] == n, 'settled: nothing more'


def test_a_reversal_is_dated_by_the_latest_event_not_by_today(shop):
    """N4. The moment the history changed its mind is the latest event's day."""
    c = shop
    ch = _cheque(c, 80.0)
    long_ago = datetime.now(timezone.utc) - timedelta(days=12)
    later = datetime.now(timezone.utc) - timedelta(days=10)
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM cheques WHERE id=?", (ch,)).fetchone()
    conn.execute("UPDATE cheque_events SET created_at_utc=? WHERE cheque_id=? AND event_type='created'",
                 (long_ago.isoformat(), ch))
    gl.post_cheque_journal(conn, c.company_id, row['branch_id'], ch, row['cheque_number'], 'in', 'reverse', 1,
                           row['currency'], row['amount'], 'peer bounce')
    conn.commit(); conn.close()
    _inject_event(ch, 'deposited', 'pending', 'deposited', later)
    _sweep()
    stray = gl.cheque_journal_uid(ch, 'reverse', 1)
    rev = _rows("SELECT business_date FROM gl_journals WHERE uid=?", (gl.reversal_uid(stray),))
    assert rev and rev[0]['business_date'] == _local_date(later), rev
    assert rev[0]['business_date'] != datetime.now().strftime('%Y-%m-%d')


def test_event_business_date_uses_the_local_zone_not_utc(monkeypatch):
    """N1. 22:30 UTC on day D is already day D+1 in a UTC+3 shop. The helper's zone is the device's
    local one (`astimezone()` with no argument); inject UTC+3 as that local zone."""
    from core.retail import cheque_books

    class Plus3(datetime):
        def astimezone(self, tz=None):
            return super().astimezone(tz if tz is not None else timezone(timedelta(hours=3)))

    monkeypatch.setattr(cheque_books, 'datetime', Plus3)
    assert cheque_books.event_business_date('2026-10-01T22:30:00+00:00') == '2026-10-02'
    assert cheque_books.event_business_date('2026-10-01T22:30:00Z') == '2026-10-02'
    assert cheque_books.event_business_date('2026-10-01T20:30:00+00:00') == '2026-10-01'   # allow half


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-x"]))
