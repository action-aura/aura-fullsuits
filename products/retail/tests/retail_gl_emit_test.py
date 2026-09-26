"""Aura Retail -- core/retail/general_ledger.py Wave 1 EMIT-side coverage
(schema v41, ROADMAP.md's 2026-09-26 "retail schema v41 CLAIMED: one set of
books across devices (GL journal sync)" entry, BOOKS-WAVE1-PLAN.md section
2.4). Builder A's assigned test file (that plan's section 6, "Builder A").

WHAT WAVE 1 CLAIMS AT THE EMIT LAYER, AND THEREFORE WHAT THIS FILE HAS TO
PROVE (core/retail/general_ledger.py). The schema side (database/schema.py,
`uid`/`source_uid` columns, the write-once trigger, the W1-0 opening-balance
fix) is a SEPARATE builder's own file and own test file
(retail_v41_gl_sync_migration_test.py) -- this file proves only what
general_ledger.py itself is responsible for:

  1. `post_journal` mints a `uid`, derives a `source_uid`, and queues
     exactly one `gl_journal` / `create` sync-outbox event per journal, in
     the SAME transaction -- a rollback removes all three together.
  2. `journal_sync_payload` carries every non-local `gl_journals` column
     (derived from live `PRAGMA table_info`, never a hand-typed list) and
     never a local `account_id`/`branch_id`/`company_id`/`source_id`/
     `reversed_journal_id` on the wire.
  3. A reversal's payload names the reversed journal, and the `source_uid`
     it derives, by that journal's own `uid` -- never a local integer id.
  4. `ensure_default_chart` completes a chart the sync APPLY side partially
     seeded (only the accounts a pulled journal's lines referenced), not
     just a chart that already has SOME rows.
  5. `backfill_legacy_journals` gives every pre-v41 (`uid IS NULL`) journal
     a DETERMINISTIC identity -- proved by running it independently
     against two byte-identical copies of one database and comparing the
     assigned uids, not merely asserting "some uid was assigned" -- and
     emits each legacy journal AT MOST ONCE across repeated calls.
  6. `backfill_legacy_journals` detects and reports pre-existing
     "suspected duplicate opening balance" journals (the W1-0 residue a
     shop may already carry from before Builder B's fix landed) without
     ever reversing them itself.

Follows the SAME bootstrap convention as retail_gl_posting_test.py (no
shared conftest.py exists for products/retail/tests/): its own temp
AURA_APP_DATA per test via `_install()`, real `init_retail()`, no Flask app
anywhere in this file.

CRITICAL: exactly ONE pytest process per file (AUDIT-010) -- these test
files pollute each other in a shared pytest process; see products/
run_all_tests.py. This wave's own plan additionally forbids running this
suite (or any other) through run_all_tests.py or against the live lab.

Run:
    pytest products/retail/tests/retail_gl_emit_test.py -q
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent                    # aura-fullsuits
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install(prefix='aura-retail-gl-emit-'):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    db_path = os.path.join(sch.SUBSYS_DIR, 'retail.db')
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn, db_path


@pytest.fixture
def conn():
    c, _ = _install()
    yield c
    c.close()


@pytest.fixture
def gl():
    from core.retail import general_ledger as _gl
    return _gl


CID = 1


def _outbox_rows(conn, entity_type='gl_journal'):
    rows = conn.execute(
        "SELECT id, entity_id, event_type, payload FROM sync_outbox WHERE entity_type=? "
        "ORDER BY created_at",
        (entity_type,)
    ).fetchall()
    return [dict(r, payload=json.loads(r['payload'])) for r in rows]


def _post_simple_sale(gl, conn, cid=CID, source_id=1, currency='JOD', branch_id=None):
    return gl.post_journal(
        conn, cid, branch_id, '2026-09-24', 'sale', source_id, currency,
        [
            {'account_code': gl.ACCOUNT_CASH, 'debit': '4.007', 'memo': 'Tender retained'},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': '4.007', 'memo': 'Sale S-1'},
        ],
        reference='S-1',
    )


# ── 1. post_journal queues exactly one gl_journal event per journal ──────

def test_post_sale_queues_exactly_one_gl_journal_event_per_journal(conn, gl):
    """RED under M-A1 (remove the `_queue_gl_journal_sync_event` call from
    `post_journal`): the outbox would carry zero `gl_journal` rows instead
    of one."""
    jid = _post_simple_sale(gl, conn)
    conn.commit()

    events = _outbox_rows(conn)
    assert len(events) == 1, f"expected exactly one gl_journal event, got {len(events)}"
    assert events[0]['event_type'] == 'create'
    journal_uid = conn.execute(
        "SELECT uid FROM gl_journals WHERE id=?", (jid,)
    ).fetchone()['uid']
    assert events[0]['entity_id'] == journal_uid, (
        "entity_id must be the journal's own uid, never its local integer id "
        "(BOOKS-WAVE1-PLAN.md section 2.3: entity_id=uid)"
    )

    # A second, independent journal must queue its OWN event, not skip one --
    # this is "exactly one per journal", not "exactly one ever".
    _post_simple_sale(gl, conn, source_id=2)
    conn.commit()
    assert len(_outbox_rows(conn)) == 2


# ── 2. Payload carries every non-local column, never a local id ─────────

def test_gl_journal_payload_carries_every_non_local_column(conn, gl):
    """Keys derived from live `PRAGMA table_info(gl_journals)`, never a
    hand-typed list -- the same ratchet convention retail_v41_gl_sync_
    migration_test.py's own `_table_columns` already established. RED
    under M-A2 (drop `business_date` from the payload)."""
    jid = _post_simple_sale(gl, conn)
    payload = gl.journal_sync_payload(conn, jid)

    all_columns = {row[1] for row in conn.execute('PRAGMA table_info(gl_journals)').fetchall()}
    # These local-identity/tenant columns are NEVER on the wire as
    # themselves -- they are translated (branch_id -> branch_uid,
    # reversed_journal_id -> reversed_journal_uid) or dropped entirely
    # (id, company_id, source_id) -- see BOOKS-WAVE1-PLAN.md section 2.3,
    # "These are never on the wire".
    local_only = {'id', 'company_id', 'branch_id', 'source_id', 'reversed_journal_id'}
    non_local = all_columns - local_only
    assert non_local, "sanity: gl_journals must have at least one non-local column"

    for column in non_local:
        assert column in payload, (
            f"gl_journals.{column} is not a local-identity column but is "
            f"missing from journal_sync_payload's own keys: {sorted(payload)}"
        )

    # And the translated/derived companions must ALSO be present.
    for extra in ('branch_uid', 'reversed_journal_uid', 'lines', 'accounts'):
        assert extra in payload, f"payload is missing {extra!r}: {sorted(payload)}"

    # Nothing local-only ever leaks onto the wire under its own name.
    for column in local_only:
        assert column not in payload, (
            f"payload leaks the local column {column!r} verbatim: {payload}"
        )


def test_payload_lines_use_account_codes_never_local_ids(conn, gl):
    """RED under M-A3 (emit `account_id` instead of `account_code`)."""
    jid = _post_simple_sale(gl, conn)
    payload = gl.journal_sync_payload(conn, jid)

    assert len(payload['lines']) == 2
    for line in payload['lines']:
        assert 'account_code' in line
        assert isinstance(line['account_code'], str)
        assert 'account_id' not in line, f"line leaks a local account_id: {line}"
        assert 'id' not in line, f"line leaks a local row id: {line}"
    codes = {line['account_code'] for line in payload['lines']}
    assert codes == {gl.ACCOUNT_CASH, gl.ACCOUNT_SALES_REVENUE}


# ── 3. uid is persisted on the origin row ────────────────────────────────

def test_journal_uid_is_persisted_on_the_origin_row(conn, gl):
    """RED under M-A4 (store NULL instead of a minted uid)."""
    jid = _post_simple_sale(gl, conn)
    row = conn.execute("SELECT uid, source_uid FROM gl_journals WHERE id=?", (jid,)).fetchone()
    assert row['uid'] is not None
    # A real uuid4, not merely "some non-null string".
    parsed = uuid.UUID(row['uid'])
    assert str(parsed) == row['uid']


# ── 4. Reversal payload names the reversed journal by uid ───────────────

def test_reversal_payload_names_the_reversed_journal_by_uid(conn, gl):
    jid = _post_simple_sale(gl, conn)
    original_uid = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()['uid']

    rev_id = gl.reverse_journal(conn, CID, jid, posted_by='test')
    payload = gl.journal_sync_payload(conn, rev_id)

    assert payload['reversed_journal_uid'] == original_uid
    assert payload['source_uid'] == original_uid, (
        "BOOKS-WAVE1-PLAN.md section 2.3: for a reversal, source_uid IS the "
        "reversed journal's own uid"
    )
    assert payload['source_type'] == 'reversal'


# ── 5. A rolled-back document leaves no gl_journal outbox row ───────────

def test_rolled_back_document_leaves_no_gl_outbox_row(conn, gl):
    before_journals = conn.execute("SELECT COUNT(*) FROM gl_journals").fetchone()[0]
    before_events = len(_outbox_rows(conn))

    _post_simple_sale(gl, conn, source_id=99)
    # Never committed -- simulate the caller's own route hitting an error
    # AFTER post_journal returned but before its own conn.commit().
    conn.rollback()

    after_journals = conn.execute("SELECT COUNT(*) FROM gl_journals").fetchone()[0]
    after_events = len(_outbox_rows(conn))
    assert after_journals == before_journals, "rollback must remove the journal header too"
    assert after_events == before_events, (
        "rollback must remove the queued gl_journal outbox row -- it was "
        "inserted on the SAME connection/transaction as the journal itself"
    )


# ── 6. ensure_default_chart completes a partially-seeded chart ──────────

def test_partially_seeded_chart_is_completed_before_posting(conn, gl):
    """Simulates what the sync APPLY side does BEFORE this pass exists
    (BOOKS-WAVE1-PLAN.md section 2.5 point 4): seed only the two accounts
    one pulled sale journal's lines referenced, never the full
    DEFAULT_CHART. A later LOCAL return needs 4900 Sales Returns and
    Allowances, which was never seeded. RED under M-A5 (restore the old
    `COUNT(*)` fast path): `ensure_default_chart` would see `already=2`,
    return immediately, and `post_journal` would then raise
    `GeneralLedgerAccountError` for account 4900."""
    conn.execute(
        "INSERT INTO gl_accounts (company_id, code, name, account_type, normal_balance, parent_id) "
        "VALUES (?,?,?,?,?,NULL)",
        (CID, gl.ACCOUNT_CASH, 'Cash and Bank', 'asset', 'debit')
    )
    conn.execute(
        "INSERT INTO gl_accounts (company_id, code, name, account_type, normal_balance, parent_id) "
        "VALUES (?,?,?,?,?,NULL)",
        (CID, gl.ACCOUNT_SALES_REVENUE, 'Sales Revenue', 'revenue', 'credit')
    )
    conn.commit()
    seeded_before = conn.execute(
        "SELECT COUNT(*) FROM gl_accounts WHERE company_id=?", (CID,)
    ).fetchone()[0]
    assert seeded_before == 2, "sanity: only a partial chart is seeded, not empty and not full"

    # A local return posting to 4900 (contra-revenue, parent 4000) --
    # 4000 itself is not seeded either, so this also exercises seeding a
    # parent that a partial sync-seed never touched.
    jid = gl.post_journal(
        conn, CID, None, '2026-09-24', 'return', 1, 'JOD',
        [
            {'account_code': gl.ACCOUNT_SALES_RETURNS, 'debit': '2.000', 'memo': 'Return R-1'},
            {'account_code': gl.ACCOUNT_CASH, 'credit': '2.000', 'memo': 'Cash refunded'},
        ],
        reference='R-1',
    )
    assert jid is not None

    codes = {
        row[0] for row in conn.execute(
            "SELECT code FROM gl_accounts WHERE company_id=?", (CID,)
        ).fetchall()
    }
    assert codes == {c for c, *_ in gl.DEFAULT_CHART}, (
        f"the chart was not completed -- codes present: {sorted(codes)}"
    )


# ── 7-8. backfill_legacy_journals: deterministic, and at-most-once ──────

def _insert_legacy_journal(conn, gl, cid, source_type, source_id, business_date,
                            reference, cash_debit, revenue_credit, memo='legacy'):
    """A raw INSERT with NO `uid`/`source_uid` -- exactly the pre-v41 shape
    (retail_v41_gl_sync_migration_test.py's own `_seed_journal` docstring
    makes the identical point: making 'legacy' journals with the NEW code
    would defeat the whole point of this fixture, per BOOKS-WAVE1-PLAN.md
    section 6's own trap #5). `ensure_default_chart` first, so the raw
    INSERT below has real account ids to point at."""
    gl.ensure_default_chart(conn, cid)
    cash_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_CASH)
    ).fetchone()[0]
    rev_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_SALES_REVENUE)
    ).fetchone()[0]
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO gl_journals "
        "(company_id, branch_id, business_date, reference, memo, source_type, "
        " source_id, currency, reversed_journal_id, posted_by) "
        "VALUES (?,?,?,?,?,?,?,?,NULL,?)",
        (cid, None, business_date, reference, memo, source_type, source_id, 'JOD', 'System')
    )
    jid = cur.lastrowid
    cur.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, cash_id, cash_debit, 0, memo)
    )
    cur.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, rev_id, 0, revenue_credit, memo)
    )
    conn.commit()
    return jid


def test_backfill_uids_are_deterministic_across_two_copies_of_one_db(gl):
    """RED under M-A6 (mint a fresh `uuid.uuid4()` per row instead of
    `legacy_journal_uid`): two independent runs against byte-identical
    copies of the SAME pre-v41 history would then disagree about that
    history's uids -- exactly the failure a restored backup must never
    produce (BOOKS-WAVE1-PLAN.md section 2.4, legacy_journal_uid's own
    "deterministic so a pre-v41 backup restored onto a replacement device
    gets the same uids" requirement)."""
    conn_a, db_path_a = _install(prefix='aura-retail-gl-emit-detA-')
    _insert_legacy_journal(conn_a, gl, CID, 'sale', 1, '2026-09-20', 'S-100', 5.000, 5.000)
    _insert_legacy_journal(conn_a, gl, CID, 'sale', 2, '2026-09-21', 'S-101', 7.500, 7.500)
    conn_a.close()

    # `db_path_b` lands inside the SAME tmp tree `_install` already
    # registered in `_TMP_DIRS` (it is just `db_path_a` plus a suffix, not
    # a new top-level directory), so `teardown_module` cleans it up for
    # free -- no separate registration needed.
    db_path_b = db_path_a + '.copy'
    shutil.copyfile(db_path_a, db_path_b)

    conn_a2 = sqlite3.connect(db_path_a, timeout=30)
    conn_a2.row_factory = sqlite3.Row
    conn_b = sqlite3.connect(db_path_b, timeout=30)
    conn_b.row_factory = sqlite3.Row
    try:
        result_a = gl.backfill_legacy_journals(conn_a2)
        result_b = gl.backfill_legacy_journals(conn_b)
        assert result_a['assigned'] == result_b['assigned'] == 2

        rows_a = {
            r['reference']: r['uid']
            for r in conn_a2.execute("SELECT reference, uid FROM gl_journals ORDER BY id").fetchall()
        }
        rows_b = {
            r['reference']: r['uid']
            for r in conn_b.execute("SELECT reference, uid FROM gl_journals ORDER BY id").fetchall()
        }
        assert rows_a == rows_b, (
            f"two independent backfills of byte-identical history disagreed "
            f"about the assigned uids: {rows_a} vs {rows_b}"
        )
        assert all(v is not None for v in rows_a.values())
    finally:
        conn_a2.close()
        conn_b.close()


def test_backfill_emits_each_legacy_journal_once_across_two_runs(conn, gl):
    """RED under M-A7 (widen the candidate SELECT to ignore the `uid IS
    NULL` filter): a second call would then re-select the already-assigned
    row and queue a second, duplicate outbox event for it."""
    jid = _insert_legacy_journal(conn, gl, CID, 'sale', 1, '2026-09-20', 'S-200', 3.000, 3.000)

    result_1 = gl.backfill_legacy_journals(conn)
    assert result_1['assigned'] == 1
    assert result_1['emitted'] == 1
    uid_after_first = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()['uid']
    assert uid_after_first is not None
    events_after_first = len(_outbox_rows(conn))
    assert events_after_first == 1

    result_2 = gl.backfill_legacy_journals(conn)
    assert result_2['assigned'] == 0, "a second run must find nothing left to backfill"
    assert result_2['emitted'] == 0
    uid_after_second = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid,)).fetchone()['uid']
    assert uid_after_second == uid_after_first, "the uid must not change on a second run"
    events_after_second = len(_outbox_rows(conn))
    assert events_after_second == 1, (
        f"a second backfill run queued {events_after_second - events_after_first} "
        f"extra gl_journal event(s) for a journal that already had its uid"
    )


# ── 9. suspected_duplicate_opening detection ─────────────────────────────

def test_backfill_reports_suspected_duplicate_opening_balances(conn, gl):
    """Simulates the pre-Builder-B-fix W1-0 residue directly at the row
    level (never by replaying the real migration chain -- that is
    retail_v41_gl_sync_migration_test.py's own job): two 'opening_balance'
    journals for the same company, as every schema bump between v38 and
    the W1-0 fix would have produced. The FIRST (lowest id) is the
    legitimate original; the SECOND is the duplicate this detector must
    name."""
    first_id = _insert_legacy_journal(
        conn, gl, CID, 'opening_balance', None, '2026-09-01', 'OPENING-BALANCE',
        100.000, 100.000, memo='Seeded from pre-v38 AR/AP subledger'
    )
    second_id = _insert_legacy_journal(
        conn, gl, CID, 'opening_balance', None, '2026-09-01', 'OPENING-BALANCE',
        100.000, 100.000, memo='Seeded from pre-v38 AR/AP subledger'
    )
    assert second_id > first_id

    result = gl.backfill_legacy_journals(conn)
    suspects = {s['id'] for s in result['suspected_duplicate_opening']}
    assert first_id not in suspects, "the earliest opening balance must NOT be flagged"
    assert second_id in suspects, "the later, duplicate opening balance must be flagged"

    # Not flagged when there is only ever ONE opening balance for a company.
    conn2, _ = _install(prefix='aura-retail-gl-emit-single-opening-')
    try:
        only_id = _insert_legacy_journal(
            conn2, gl, 1, 'opening_balance', None, '2026-09-01', 'OPENING-BALANCE',
            50.000, 50.000
        )
        result2 = gl.backfill_legacy_journals(conn2)
        assert result2['suspected_duplicate_opening'] == [], (
            f"a single, legitimate opening balance must never be flagged: "
            f"{result2['suspected_duplicate_opening']}"
        )
        assert only_id  # sanity, keeps the variable used
    finally:
        conn2.close()


def test_backfill_flags_an_opening_balance_posted_after_a_real_transaction(conn, gl):
    """The detector's SECOND clause, which nothing else exercises: the only
    opening balance a company has, posted AFTER a real transaction. That is
    the W1-0 shape on a shop that carried NO credit at v38 (so v38 posted no
    opening balance), then made credit sales -- each already posting its AR
    to the ledger -- and then crossed v39/v40: the unguarded replay booked
    that same, already-posted AR a second time as an "opening" balance."""
    _insert_legacy_journal(conn, gl, CID, 'sale', 1, '2026-09-10', 'S-1', 12.000, 12.000)
    late_opening_id = _insert_legacy_journal(
        conn, gl, CID, 'opening_balance', None, '2026-09-21', 'OPENING-BALANCE',
        12.000, 12.000, memo='Seeded from pre-v38 AR/AP subledger')
    result = gl.backfill_legacy_journals(conn)
    assert [s['id'] for s in result['suspected_duplicate_opening']] == [late_opening_id], (
        f"an opening balance posted after a real transaction must be flagged: "
        f"{result['suspected_duplicate_opening']}"
    )


def _insert_pulled_journal(conn, gl, cid, source_type, business_date, reference,
                           cash_debit, revenue_credit, memo='pulled'):
    """The row shape the sync APPLY side writes for a journal that arrived
    from a PEER (sync_service.py's `gl_journal` branch): a raw INSERT that
    carries the sender's `uid` from the start. Such a row is never a
    backfill candidate, and it lands at whatever local id this device's
    autoincrement gives it -- i.e. AFTER every local journal already here."""
    gl.ensure_default_chart(conn, cid)
    cash_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_CASH)
    ).fetchone()[0]
    rev_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_SALES_REVENUE)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, memo, "
        "source_type, source_id, currency, reversed_journal_id, posted_by, posted_at, uid, source_uid) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, None, business_date, reference, memo, source_type, None, 'JOD', None,
         'migration', '2026-09-01 08:00:00', str(uuid.uuid4()), None),
    )
    jid = cur.lastrowid
    conn.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, cash_id, cash_debit, 0, memo))
    conn.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, rev_id, 0, revenue_credit, memo))
    conn.commit()
    return jid


def test_duplicate_detector_ignores_a_peers_opening_balance_that_arrived_by_sync(conn, gl):
    """FALSE POSITIVE, found by the wave verifier. BOOKS-WAVE1-PLAN.md
    section 2.6: every device posted its OWN opening balance at v38, from its
    own device-local `credit_balance`, and those are deliberately all synced
    -- "adding the history together is therefore the true shop-wide ledger".
    So on a two-till shop where both tills carried credit at v38, each till
    ends up holding TWO legitimate opening balances once sync runs: its own
    (low local id) and its peer's (pulled later, high local id).

    The detector used to scan every `opening_balance` row on the device, on
    every boot, and flag any that was not the company's earliest -- so the
    PEER's legitimate opening balance was logged as a "suspected duplicate"
    on every boot of every such till, with the log line telling the owner
    "only the owner can decide whether to reverse_journal() the extras".
    Acting on that would reverse a real balance.

    W1-0 residue can only ever be LOCAL pre-v41 history (the guard stops new
    ones), and local pre-v41 history is exactly the backfill's own candidate
    set -- a pulled journal always arrives with a uid and is never one. RED
    before the fix: the pulled opening balance was flagged on the next boot.
    """
    _insert_legacy_journal(
        conn, gl, CID, 'opening_balance', None, '2026-09-01', 'OPENING-BALANCE',
        100.000, 100.000, memo='Seeded from pre-v38 AR/AP subledger'
    )
    _insert_legacy_journal(conn, gl, CID, 'sale', 1, '2026-09-02', 'S-1', 4.007, 4.007)
    first_boot = gl.backfill_legacy_journals(conn)
    assert first_boot['assigned'] == 2
    assert first_boot['suspected_duplicate_opening'] == [], (
        f"sanity: one local opening balance, posted first, is not residue: "
        f"{first_boot['suspected_duplicate_opening']}"
    )

    # The peer's own sale and its own legitimate opening balance arrive by sync.
    _insert_pulled_journal(conn, gl, CID, 'sale', '2026-09-03', 'S-PEER-1', 9.001, 9.001)
    peer_opening_id = _insert_pulled_journal(
        conn, gl, CID, 'opening_balance', '2026-09-01', 'OPENING-BALANCE', 55.500, 55.500,
        memo='Seeded from pre-v38 AR/AP subledger')

    next_boot = gl.backfill_legacy_journals(conn)
    assert next_boot['assigned'] == 0
    flagged = {s['id'] for s in next_boot['suspected_duplicate_opening']}
    assert peer_opening_id not in flagged, (
        f"a peer's legitimate opening balance (local id {peer_opening_id}), "
        f"which arrived by sync, was reported as a suspected W1-0 duplicate: "
        f"{next_boot['suspected_duplicate_opening']}"
    )
    assert next_boot['suspected_duplicate_opening'] == []


# ── 10. F1: the legacy hash is derived WITH source_uid, never None ──────

def test_backfill_hashes_with_the_resolved_source_uid_not_none(conn, gl):
    """F1 (BOOKS-WAVE1-PLAN.md decision F1; v2 finding 4): `legacy_journal_
    uid`'s canonical header must carry THIS row's own DERIVED `source_uid`
    -- derived BEFORE hashing, via the IDENTICAL `_derive_source_uid`
    derivation `post_journal` itself uses -- never `None` regardless of
    what the row's real `source_uid` resolves to. Before this fix, the raw
    candidate row `backfill_legacy_journals` selects has no `source_uid`
    column at all (it is NULL until this exact pass resolves it), and
    `source_uid` was derived AFTER calling `legacy_journal_uid`, so every
    legacy journal's uid was computed as though `source_uid` were always
    `None` -- silently, forever, because F1 also says this formula can
    never change after it ships.

    Uses a legacy 'reversal' (never 'sale'/'return') deliberately: its
    `source_uid` resolves against `gl_journals` itself (the reversed
    journal's own uid, `_derive_source_uid`'s own docstring), which this
    test fully controls without needing a real `sales`/`returns` row."""
    jid_a = _insert_legacy_journal(conn, gl, CID, 'sale', None, '2026-09-20', 'S-300', 6.000, 6.000)
    result_a = gl.backfill_legacy_journals(conn)
    assert result_a['assigned'] == 1
    uid_a = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid_a,)).fetchone()['uid']
    assert uid_a is not None

    cash_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (CID, gl.ACCOUNT_CASH)
    ).fetchone()[0]
    rev_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (CID, gl.ACCOUNT_SALES_REVENUE)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, memo, "
        "source_type, source_id, currency, reversed_journal_id, posted_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (CID, None, '2026-09-21', 'REV-1', 'legacy reversal', 'reversal', None, 'JOD',
         jid_a, 'System'),
    )
    jid_b = cur.lastrowid
    conn.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid_b, rev_id, 6.0, 0.0, 'legacy reversal'))
    conn.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid_b, cash_id, 0.0, 6.0, 'legacy reversal'))
    conn.commit()

    result_b = gl.backfill_legacy_journals(conn)
    assert result_b['assigned'] == 1
    row_b = dict(conn.execute("SELECT * FROM gl_journals WHERE id=?", (jid_b,)).fetchone())
    assert row_b['source_uid'] == uid_a, (
        "sanity: the reversal's own source_uid must resolve to the reversed "
        "journal's own uid"
    )
    lines_b = conn.execute(
        "SELECT a.code AS account_code, l.debit, l.credit, l.memo FROM gl_journal_lines l "
        "JOIN gl_accounts a ON a.id=l.account_id WHERE l.journal_id=? ORDER BY l.id",
        (jid_b,)
    ).fetchall()

    header_with = dict(row_b, source_uid=uid_a)
    header_without = dict(row_b, source_uid=None)
    expected_with = gl.legacy_journal_uid(header_with, lines_b)
    expected_without = gl.legacy_journal_uid(header_without, lines_b)
    assert expected_with != expected_without, "sanity: source_uid must change the hash"
    assert row_b['uid'] == expected_with, (
        f"backfill assigned {row_b['uid']!r} to a reversal whose resolved "
        f"source_uid is {uid_a!r} -- expected {expected_with!r} (hashed WITH "
        f"the resolved source_uid); it instead matches "
        f"{'the WITHOUT-source_uid value' if row_b['uid'] == expected_without else 'neither candidate'} "
        f"-- F1 requires source_uid to be derived and folded into the header "
        f"BEFORE hashing, never after"
    )


# ── 11. F2: an opening-balance restore hashes to ONE fact ───────────────

def test_backfill_hashes_two_opening_balance_restores_to_one_fact(conn, gl):
    """F2 (BOOKS-WAVE1-PLAN.md decision F2; v2 finding 2), modelled on
    verifier 2's own `repro4_prev38_restore.py`: a pre-v38 backup restored
    onto a REPLACEMENT device regenerates its opening balance at RESTORE
    time -- a different local `id`, `business_date`, `reference`/`memo`
    text and `posted_at` than the ORIGINAL -- but represents the SAME
    imported debt. `legacy_journal_uid` must hash these to the SAME uid
    for `source_type == 'opening_balance'` ONLY, so a third device pulling
    both the original's and the replacement's backfilled events holds the
    true AR ONCE, never twice (repro4's own finding: a third device booked
    AR 100.010 where the true figure was 50.005)."""
    original = {
        'id': 4, 'business_date': '2026-08-01', 'reference': 'OPENING-BALANCE',
        'memo': 'Seeded from pre-v38 AR/AP subledger at migration time',
        'source_type': 'opening_balance', 'source_uid': None, 'currency': 'JOD',
        'posted_by': 'migration', 'posted_at': '2026-08-01 09:00:00',
    }
    # The replacement device's own regenerated backfill -- restore-time
    # local id/business_date/posted_at, identical imported figure.
    replacement = {
        'id': 17, 'business_date': '2026-09-24', 'reference': 'OPENING-BALANCE',
        'memo': 'Seeded from pre-v38 AR/AP subledger at migration time',
        'source_type': 'opening_balance', 'source_uid': None, 'currency': 'JOD',
        'posted_by': 'migration', 'posted_at': '2026-09-24 10:11:12',
    }
    lines = [
        {'account_code': gl.ACCOUNT_AR, 'debit': 50.005, 'credit': 0.0, 'memo': None},
        {'account_code': gl.ACCOUNT_EQUITY, 'debit': 0.0, 'credit': 50.005, 'memo': None},
    ]
    uid_original = gl.legacy_journal_uid(original, lines)
    uid_replacement = gl.legacy_journal_uid(replacement, lines)
    assert uid_original == uid_replacement, (
        f"two opening-balance restores of the identical imported debt hashed "
        f"to DIFFERENT uids ({uid_original!r} vs {uid_replacement!r}) -- a "
        f"third device would book the same AR twice (verifier 2's repro4)"
    )

    # The IDENTICAL-IMPORT case (BOOKS-WAVE1-PLAN.md F2's own second test
    # case): the same shop's opening balance imported a second time, same
    # figures, different local id/date/posted_at again -- also one fact.
    reimport = {
        'id': 42, 'business_date': '2026-10-01', 'reference': 'OPENING-BALANCE',
        'memo': 'Seeded from pre-v38 AR/AP subledger at migration time',
        'source_type': 'opening_balance', 'source_uid': None, 'currency': 'JOD',
        'posted_by': 'migration', 'posted_at': '2026-10-01 00:00:00',
    }
    uid_reimport = gl.legacy_journal_uid(reimport, lines)
    assert uid_reimport == uid_original

    # An ORDINARY (non-opening-balance) journal with the exact same lines
    # must NOT collapse this way -- only opening_balance gets the narrowed
    # canonical form; everything else still hashes on id/date/etc.
    ordinary = dict(original, source_type='sale')
    ordinary_uid = gl.legacy_journal_uid(ordinary, lines)
    assert ordinary_uid != uid_original


# ── 12. F3: a uid collision skips one row, never aborts the rest ────────

def test_backfill_skips_a_uid_collision_and_continues_past_it(conn, gl):
    """F3 (BOOKS-WAVE1-PLAN.md decision F3; v2 finding 3), modelled on
    verifier 2's own `repro6_poison_row.py`: a local legacy row whose
    deterministic uid ALREADY belongs to ANOTHER local row (a "pulled
    twin" -- e.g. a cloned install that already synced this exact journal
    in from a peer, under its own already-backfilled uid, before THIS
    device's own backfill got to the matching local row) must be SKIPPED,
    once, logged with both ids, and never abort the rest of the backfill.
    Before this fix, the `UPDATE`'s `sqlite3.IntegrityError` propagated
    straight out of the un-guarded loop, aborting the WHOLE backfill --
    every OTHER legacy row was never backfilled either, on this boot or
    any future one (the same poisoned row is selected first every time)."""
    jid_1 = _insert_legacy_journal(conn, gl, CID, 'customer_payment', None, '2026-09-10', 'LEG-1', 5.005, 5.005)
    jid_2 = _insert_legacy_journal(conn, gl, CID, 'customer_payment', None, '2026-09-11', 'LEG-2', 5.005, 5.005)
    jid_3 = _insert_legacy_journal(conn, gl, CID, 'customer_payment', None, '2026-09-12', 'LEG-3', 5.005, 5.005)

    # The "twin": a row already carrying jid_2's OWN deterministic uid,
    # exactly as if a peer's copy of this SAME journal had already arrived
    # and been applied (the sync APPLY side's own shape for a pulled
    # gl_journal -- uid/source_uid named directly on INSERT).
    row2 = dict(conn.execute("SELECT * FROM gl_journals WHERE id=?", (jid_2,)).fetchone())
    lines2 = conn.execute(
        "SELECT a.code AS account_code, l.debit, l.credit, l.memo FROM gl_journal_lines l "
        "JOIN gl_accounts a ON a.id=l.account_id WHERE l.journal_id=? ORDER BY l.id",
        (jid_2,)
    ).fetchall()
    twin_uid = gl.legacy_journal_uid(dict(row2, source_uid=None), lines2)
    cash_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (CID, gl.ACCOUNT_CASH)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, memo, "
        "source_type, source_id, currency, reversed_journal_id, posted_by, posted_at, "
        "uid, source_uid) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (CID, None, row2['business_date'], row2['reference'], row2['memo'],
         'customer_payment', None, 'JOD', None, 'migration', row2['posted_at'],
         twin_uid, None),
    )
    twin_id = cur.lastrowid
    conn.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (twin_id, cash_id, 5.005, 0.0, None))
    conn.commit()

    result = gl.backfill_legacy_journals(conn)

    assert len(result['uid_collisions']) == 1, (
        f"expected exactly one uid collision, got: {result['uid_collisions']}"
    )
    collision = result['uid_collisions'][0]
    assert collision['id'] == jid_2
    assert collision['existing_id'] == twin_id
    assert collision['uid'] == twin_uid

    row_2_after = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (jid_2,)).fetchone()
    assert row_2_after['uid'] is None, (
        "the colliding row must be left uid-NULL (never half-assigned) so a "
        "future boot may retry it"
    )

    # Every OTHER legacy row -- 1 and 3 -- must still get its uid AND its
    # outbox event, in this SAME boot, despite the collision.
    assert result['assigned'] == 2
    assert result['emitted'] == 2
    for other_id in (jid_1, jid_3):
        other = conn.execute("SELECT uid FROM gl_journals WHERE id=?", (other_id,)).fetchone()
        assert other['uid'] is not None, (
            f"row {other_id} was never backfilled -- the uid collision "
            f"aborted the rest of the backfill"
        )

    events = _outbox_rows(conn)
    assert len(events) == 2, (
        f"expected exactly 2 outbox events (rows 1 and 3), got {len(events)}: "
        f"the colliding row (2) must never be emitted"
    )
    emitted_entity_ids = {e['entity_id'] for e in events}
    assert twin_uid not in emitted_entity_ids, (
        "the colliding row's own (never-assigned) uid must never appear in "
        "the outbox"
    )

    # A second boot is a clean no-op on rows 1 and 3 (already assigned),
    # and re-reports the SAME collision on row 2 rather than raising again
    # or silently giving up -- the skip is retryable, not a crash.
    second_boot = gl.backfill_legacy_journals(conn)
    assert second_boot['assigned'] == 0
    assert len(second_boot['uid_collisions']) == 1
    assert second_boot['uid_collisions'][0]['id'] == jid_2


# ── 13. F4: sub-quantum lines are dropped AFTER rounding, not before ────

def test_validated_lines_drops_sub_quantum_lines_after_rounding(gl):
    """F4 (BOOKS-WAVE1-PLAN.md decision F4; v2 finding 5): a sub-currency-
    quantum line (JOD's quantum is 0.001, a fil) must be dropped AFTER
    quantizing, never before -- a journal made ENTIRELY of such lines is
    refused outright (`GeneralLedgerImbalanceError`, "every line supplied
    was zero"), never accepted with zero-value lines silently written.

    Before this fix, a line that is nonzero BEFORE rounding but rounds to
    ZERO survived the (pre-rounding) zero check, got quantized to
    0.000/0.000, and was APPENDED to `clean_lines` -- so `post_journal`
    would happily post (and queue for sync) a journal consisting entirely
    of 0/0 lines, which the sync APPLY side's own mirror
    (`sync_service._validate_gl_journal_lines`) then parks as
    `gl_unbalanced` FOREVER, because its OWN pre-fix zero-check ran on the
    already-rounded wire values (exactly 0.0/0.0) and dropped them,
    leaving nothing (verifier 2's own `repro5_page_rollback_and_
    subquantum.py`, part b)."""
    with pytest.raises(gl.GeneralLedgerImbalanceError):
        gl.validated_lines(
            [
                {'account_code': gl.ACCOUNT_CASH, 'debit': 0.0004, 'credit': 0.0},
                {'account_code': gl.ACCOUNT_SALES_REVENUE, 'debit': 0.0, 'credit': 0.0004},
            ],
            'JOD',
        )

    # A MIX -- one real line plus one sub-quantum line -- must drop ONLY
    # the sub-quantum line and still balance and validate normally.
    clean_lines, total_debit, total_credit = gl.validated_lines(
        [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 4.007, 'credit': 0.0},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'debit': 0.0, 'credit': 4.007},
            {'account_code': gl.ACCOUNT_CASH, 'debit': 0.0002, 'credit': 0.0},
        ],
        'JOD',
    )
    assert len(clean_lines) == 2, f"the sub-quantum line must be dropped: {clean_lines}"
    assert total_debit == total_credit


# ── 14. Third verifier (2026-09-26): what F2/F3 still let through ──────────

def _insert_legacy_opening(conn, gl, cid, business_date, ar_amount,
                           ar_memo='Opening balance: pre-v38 customer AR history',
                           plug_memo='Opening balance plug'):
    """One v38-shape `opening_balance` journal -- the exact lines
    `_migrate_add_general_ledger` writes for a shop whose customers owed
    it `ar_amount` (Dr 1100 AR / Cr 3000 equity plug), as a RAW insert with
    `uid` NULL (plan section 6, trap 5)."""
    gl.ensure_default_chart(conn, cid)
    ar_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_AR)
    ).fetchone()[0]
    eq_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, gl.ACCOUNT_EQUITY)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, memo, "
        "source_type, source_id, currency, reversed_journal_id, posted_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, None, business_date, 'OPENING-BALANCE',
         'Seeded from pre-v38 AR/AP subledger at migration time',
         'opening_balance', None, 'JOD', None, 'migration'),
    )
    jid = cur.lastrowid
    conn.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, ar_id, ar_amount, 0.0, ar_memo))
    conn.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) VALUES (?,?,?,?,?)",
        (jid, eq_id, 0.0, ar_amount, plug_memo))
    conn.commit()
    return jid


def _account_net(conn, cid, code):
    row = conn.execute(
        "SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM gl_journal_lines l "
        "JOIN gl_accounts a ON a.id = l.account_id WHERE a.company_id=? AND a.code=?",
        (cid, code)
    ).fetchone()
    return Decimal(str(row[0])).quantize(Decimal('0.001'))


def test_identical_w1_0_duplicate_opening_balances_each_reach_every_device(conn, gl):
    """W1-0 residue with IDENTICAL lines -- a shop whose `credit_balance`
    did not move between its v38 and v39 upgrades got the SAME opening
    balance posted twice, to the fil (schema.py's W1-0 docstring). Plan
    section 2.6 decides what happens to such residue: "send them anyway, so
    both devices hold identical (equally wrong) books and a single
    reverse_journal corrects every device".

    F2 hashes an opening balance on currency + lines only, so both copies
    used to hash to the SAME uid; F3 then treated the second as a "uid
    collision" and skipped it -- left uid NULL, never emitted, but still
    counted in THIS device's own books. Result: this till books AR twice,
    every peer once, forever; and an owner who reverses the extra copy (as
    the W1-0 WARNING invites) posts a reversal that names no uid, which every
    peer applies against an opening balance it never had.

    RED before the fix: one uid collision, one event, receiver AR 100.005
    against the origin's 200.010."""
    first = _insert_legacy_opening(conn, gl, CID, '2026-09-20', 100.005)
    second = _insert_legacy_opening(conn, gl, CID, '2026-09-21', 100.005)

    result = gl.backfill_legacy_journals(conn)
    assert result['uid_collisions'] == [], (
        f"a W1-0 duplicate opening balance on the SAME device was treated as "
        f"a pulled twin and skipped: {result['uid_collisions']}"
    )
    uids = [conn.execute("SELECT uid FROM gl_journals WHERE id=?", (j,)).fetchone()['uid']
            for j in (first, second)]
    assert all(uids) and uids[0] != uids[1], f"expected two distinct uids, got {uids}"
    events = _outbox_rows(conn)
    assert sorted(e['entity_id'] for e in events) == sorted(uids)

    # F2's own guarantee still holds for the FIRST copy: a replacement device
    # that regenerates ONE opening balance from the same pre-v38 backup (new
    # id, date, posted_at) must hash to the original's first uid.
    regenerated = {
        'id': 1, 'business_date': '2026-10-02', 'reference': 'OPENING-BALANCE',
        'memo': 'Seeded from pre-v38 AR/AP subledger at migration time',
        'source_type': 'opening_balance', 'source_uid': None, 'currency': 'JOD',
        'posted_by': 'migration', 'posted_at': '2026-10-02 08:00:00',
    }
    lines = conn.execute(
        "SELECT a.code AS account_code, l.debit, l.credit, l.memo FROM gl_journal_lines l "
        "JOIN gl_accounts a ON a.id=l.account_id WHERE l.journal_id=? ORDER BY l.id", (first,)
    ).fetchall()
    assert gl.legacy_journal_uid(regenerated, lines) == uids[0]

    # A peer that pulls this device's outbox holds the SAME books.
    from commercial_runtime.sync.sync_service import SyncService
    recv_conn, recv_db = _install(prefix='aura-retail-gl-emit-w10-recv-')

    def _recv_get_conn():
        c = sqlite3.connect(recv_db, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        return c

    try:
        svc = SyncService(client_factory=lambda: None, get_conn=_recv_get_conn,
                          local_company_id_provider=lambda: CID)
        pulled = [{'entity_type': 'gl_journal', 'event_type': 'create',
                   'entity_id': e['entity_id'], 'payload': e['payload']} for e in events]
        c = _recv_get_conn()
        svc.apply_pull_result(c, {'events': pulled, 'cursor': len(pulled)})
        c.commit()
        c.close()
        origin_ar = _account_net(conn, CID, gl.ACCOUNT_AR)
        receiver_ar = _account_net(recv_conn, CID, gl.ACCOUNT_AR)
        assert origin_ar == Decimal('200.010')
        assert receiver_ar == origin_ar, (
            f"books diverged: the origin holds AR {origin_ar}, a peer that "
            f"pulled its whole outbox holds {receiver_ar}"
        )
    finally:
        recv_conn.close()


def test_opening_balance_uid_ignores_line_memos(gl):
    """F2, exactly as decided: an opening balance's canonical form is its
    currency plus each line's (account code, debit, credit) -- never a memo.
    The line memos are migration-code prose ('Opening balance: pre-v38
    customer AR history'); any later rewording or translation of that prose
    would otherwise give a replacement device's regenerated opening balance
    a new uid, and every peer would book the same imported debt twice.
    RED before the fix: the line memo was hashed."""
    header = {
        'id': 3, 'business_date': '2026-09-20', 'reference': 'OPENING-BALANCE',
        'memo': 'Seeded from pre-v38 AR/AP subledger at migration time',
        'source_type': 'opening_balance', 'source_uid': None, 'currency': 'JOD',
        'posted_by': 'migration', 'posted_at': '2026-09-20 09:00:00',
    }
    worded = [
        {'account_code': '1100', 'debit': 50.005, 'credit': 0.0,
         'memo': 'Opening balance: pre-v38 customer AR history'},
        {'account_code': '3000', 'debit': 0.0, 'credit': 50.005, 'memo': 'Opening balance plug'},
    ]
    reworded = [dict(worded[0], memo='opening AR (reworded)'), dict(worded[1], memo=None)]
    assert gl.legacy_journal_uid(header, worded) == gl.legacy_journal_uid(header, reworded)
    # The amounts still decide it: one fil more is a different fact.
    other = [dict(worded[0], debit=50.006), dict(worded[1], credit=50.006)]
    assert gl.legacy_journal_uid(header, other) != gl.legacy_journal_uid(header, worded)


def test_a_boot_backfill_that_fails_mid_chunk_never_strands_a_uid_without_its_event(conn, gl, monkeypatch):
    """Every uid the backfill assigns must reach the outbox in the SAME
    commit, or not be assigned at all: a uid with no event is never a
    candidate again (`uid IS NULL` is the only selector), so that journal
    never reaches any other device.

    F3 wrapped each row's UPDATE in a SAVEPOINT. When no transaction is open
    -- the first row of a boot, and the first row after every chunk commit
    -- that SAVEPOINT *is* the transaction, and SQLite's RELEASE of the
    outermost savepoint is a COMMIT. So the first row's uid committed on its
    own, before its outbox row was even inserted. Any failure (or a killed
    process) before the chunk commit then rolled back the event and kept the
    uid. Verifier 2's repro3 through the real boot path, which v2 had found
    clean before F3.

    RED before the fix: row 1 keeps its uid after boot 1 and has no event,
    and boot 2 cannot repair it."""
    import database.schema as sch
    ids = [
        _insert_legacy_journal(conn, gl, CID, 'customer_payment', None, f'2026-09-1{i}',
                               f'LEG-{i}', 10.001 + i, 10.001 + i)
        for i in range(3)
    ]
    real_queue = gl._queue_gl_journal_sync_event
    calls = {'n': 0}

    def _flaky_queue(c, journal_id):
        calls['n'] += 1
        if calls['n'] == 2:
            raise sqlite3.OperationalError('simulated: database or disk is full')
        return real_queue(c, journal_id)

    def _stranded():
        out = []
        for r in conn.execute("SELECT id, uid FROM gl_journals WHERE uid IS NOT NULL").fetchall():
            n = conn.execute(
                "SELECT COUNT(*) FROM sync_outbox WHERE entity_type='gl_journal' AND entity_id=?",
                (r['uid'],)
            ).fetchone()[0]
            if n != 1:
                out.append((r['id'], n))
        return out

    monkeypatch.setattr(gl, '_queue_gl_journal_sync_event', _flaky_queue)
    sch.init_retail()  # boot 1: the backfill fails on its 2nd row; _init_retail logs it
    monkeypatch.setattr(gl, '_queue_gl_journal_sync_event', real_queue)
    assert calls['n'] == 2, f"fixture: the simulated failure never fired ({calls['n']} calls)"
    assert _stranded() == [], (
        f"boot 1 failed mid-chunk and left (journal id, event count) {_stranded()}: "
        f"a uid with no outbox event is never selected again, so that journal "
        f"never reaches any other device"
    )
    # And the unfinished chunk is ONE unit (plan R4: "the backfill commits
    # every 1000 rows"), not a commit per row -- 20k per-row commits on a
    # phone's first v41 boot is a real cost, and a per-row commit is exactly
    # what a SAVEPOINT with no transaction around it silently does.
    assigned_after_failure = conn.execute(
        "SELECT COUNT(*) FROM gl_journals WHERE uid IS NOT NULL").fetchone()[0]
    assert assigned_after_failure == 0, (
        f"{assigned_after_failure} row(s) of the failed chunk were committed "
        f"on their own -- the chunk is not one transaction"
    )

    sch.init_retail()  # boot 2: healthy
    rows = conn.execute(
        "SELECT id, uid FROM gl_journals WHERE id IN (?,?,?) ORDER BY id", ids
    ).fetchall()
    assert all(r['uid'] for r in rows), [dict(r) for r in rows]
    assert _stranded() == []
    assert len(_outbox_rows(conn)) == 3


def test_a_caller_that_commits_after_a_backfill_failure_still_keeps_only_whole_pairs(conn, gl, monkeypatch):
    """The same invariant one level down, for any caller other than
    init_retail (whose `finally` rolls back): if a caller swallows the
    backfill's exception and COMMITS, what it commits must still be whole
    (uid, event) pairs -- the failing row is undone by its own savepoint,
    not left half-assigned in the open transaction. schema.py's F10 comment
    relies on this. RED without the per-row undo: the failing row keeps a
    uid with no event."""
    for i in range(3):
        _insert_legacy_journal(conn, gl, CID, 'customer_payment', None, f'2026-09-2{i}',
                               f'LEG-C{i}', 20.001 + i, 20.001 + i)
    real_queue = gl._queue_gl_journal_sync_event
    calls = {'n': 0}

    def _flaky_queue(c, journal_id):
        calls['n'] += 1
        if calls['n'] == 2:
            raise sqlite3.OperationalError('simulated: database or disk is full')
        return real_queue(c, journal_id)

    monkeypatch.setattr(gl, '_queue_gl_journal_sync_event', _flaky_queue)
    with pytest.raises(sqlite3.OperationalError):
        gl.backfill_legacy_journals(conn)
    conn.commit()  # a careless caller
    stranded = [
        r['id'] for r in conn.execute("SELECT id, uid FROM gl_journals WHERE uid IS NOT NULL").fetchall()
        if conn.execute(
            "SELECT COUNT(*) FROM sync_outbox WHERE entity_type='gl_journal' AND entity_id=?",
            (r['uid'],)
        ).fetchone()[0] != 1
    ]
    assert stranded == [], f"journal(s) {stranded} kept a uid with no outbox event"


# ── 15. Third verifier, retry (2026-09-26): the formula is pinned, and F1+F3 ──

def _raw_legacy(conn, gl, cid, source_type, business_date, reference, lines,
                source_id=None, reversed_journal_id=None, posted_at='2026-09-10 10:00:00'):
    """One pre-v41 journal as a RAW insert (uid NULL, plan section 6 trap 5)
    with an EXPLICIT `posted_at`, so two rows can be made identical in every
    column except their local id. `lines` is [(code, debit, credit), ...]."""
    gl.ensure_default_chart(conn, cid)
    cur = conn.execute(
        "INSERT INTO gl_journals (company_id, branch_id, business_date, reference, memo, "
        "source_type, source_id, currency, reversed_journal_id, posted_by, posted_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (cid, None, business_date, reference, None, source_type, source_id, 'JOD',
         reversed_journal_id, 'System', posted_at),
    )
    jid = cur.lastrowid
    for code, debit, credit in lines:
        account_id = conn.execute(
            "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (cid, code)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) "
            "VALUES (?,?,?,?,?)", (jid, account_id, debit, credit, None))
    conn.commit()
    return jid


def _pull_into(db_path, events):
    """Applies `events` (outbox rows as `_outbox_rows` returns them) to the
    install at `db_path` through the real `apply_pull_result`, the path a
    pull tick takes."""
    from commercial_runtime.sync.sync_service import SyncService

    def _get_conn():
        c = sqlite3.connect(db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        return c

    svc = SyncService(client_factory=lambda: None, get_conn=_get_conn,
                      local_company_id_provider=lambda: CID)
    c = _get_conn()
    try:
        svc.apply_pull_result(c, {'events': [
            {'entity_type': 'gl_journal', 'event_type': 'create',
             'entity_id': e['entity_id'], 'payload': e['payload']} for e in events
        ], 'cursor': len(events)})
        c.commit()
    finally:
        c.close()


def test_legacy_journal_uid_formula_is_pinned(gl):
    """F1's premise: "the uid formula can never change after it ships" --
    a restored pre-v41 backup must hash its history to the uids the original
    device already sent, forever. Nothing pinned the formula itself: every
    other test compares the function with itself, so a reordered key, a
    different quantization or a dropped field would stay green while every
    replacement device re-sent its whole history under new uids.

    Two pins. (1) Golden values, computed once and written down. (2) For the
    ordinary case, an independent re-derivation from BOOKS-WAVE1-PLAN.md
    section 2.4's own field list (local id, business_date, reference, memo,
    source_type, source_uid, currency, posted_by, posted_at; lines sorted by
    (code, debit, credit, memo), amounts as strings quantized to the
    currency) -- written here, not imported, so it cannot move with the code."""
    header = {'id': 7, 'business_date': '2026-09-20', 'reference': 'S-100', 'memo': None,
              'source_type': 'sale', 'source_uid': '6f1c2b1e-0c4a-4f7e-9a51-2d8f3b6c9e10',
              'currency': 'JOD', 'posted_by': 'System', 'posted_at': '2026-09-20 10:11:12'}
    lines = [{'account_code': '4000', 'debit': 0.0, 'credit': 4.007, 'memo': 'Sale S-100'},
             {'account_code': '1000', 'debit': 4.007, 'credit': 0.0, 'memo': 'Tender retained'}]
    assert gl.legacy_journal_uid(header, lines) == '435c3b7d-3ee6-58d0-9b66-3e7d820134c2'

    namespace = uuid.uuid5(uuid.NAMESPACE_DNS, 'gl-legacy.aura-fullsuits.internal')
    plan_canonical = json.dumps({
        'id': 7, 'business_date': '2026-09-20', 'reference': 'S-100', 'memo': None,
        'source_type': 'sale', 'source_uid': '6f1c2b1e-0c4a-4f7e-9a51-2d8f3b6c9e10',
        'currency': 'JOD', 'posted_by': 'System', 'posted_at': '2026-09-20 10:11:12',
        'lines': [['1000', '4.007', '0.000', 'Tender retained'],
                  ['4000', '0.000', '4.007', 'Sale S-100']],
    }, sort_keys=True)
    assert gl.legacy_journal_uid(header, lines) == str(uuid.uuid5(namespace, plan_canonical))

    # F2's opening-balance form: currency + (code, debit, credit) lines only,
    # plus the occurrence index -- no id, date, reference, memo or posted_at.
    opening = dict(header, id=3, reference='OPENING-BALANCE', memo='x',
                   source_type='opening_balance', source_uid=None, posted_by='migration')
    opening_lines = [{'account_code': '1100', 'debit': 50.005, 'credit': 0.0, 'memo': 'a'},
                     {'account_code': '3000', 'debit': 0.0, 'credit': 50.005, 'memo': 'b'}]
    assert gl.legacy_journal_uid(opening, opening_lines) == 'cab24c51-aea5-5554-9a2c-88fad7074dfa'
    assert gl.legacy_journal_uid(dict(opening, opening_occurrence=1), opening_lines) == \
        'c3e8c064-444d-5e5e-a670-1da43d0ce3bf'


def test_two_identical_ordinary_legacy_journals_stay_two_facts(conn, gl):
    """F2 narrows ONLY the opening balance. Two ordinary journals that agree
    in every column but their local id (two same-amount customer payments
    with the same reference, posted in the same second) are two payments.
    The local id is what keeps them apart; without it both hash to one uid,
    F3 skips the second as a "pulled twin", and this device books both while
    every peer books one. RED when the id is dropped from the ordinary form."""
    lines = [(gl.ACCOUNT_CASH, 3.003, 0.0), (gl.ACCOUNT_AR, 0.0, 3.003)]
    first = _raw_legacy(conn, gl, CID, 'customer_payment', '2026-09-10', 'PAY-7', lines)
    second = _raw_legacy(conn, gl, CID, 'customer_payment', '2026-09-10', 'PAY-7', lines)

    result = gl.backfill_legacy_journals(conn)
    assert result['uid_collisions'] == [], result['uid_collisions']
    uids = [conn.execute("SELECT uid FROM gl_journals WHERE id=?", (j,)).fetchone()['uid']
            for j in (first, second)]
    assert all(uids) and uids[0] != uids[1], uids
    events = _outbox_rows(conn)
    assert sorted(e['entity_id'] for e in events) == sorted(uids)

    _, recv_db = _install(prefix='aura-retail-gl-emit-twopay-recv-')
    _pull_into(recv_db, events)
    recv = sqlite3.connect(recv_db)
    try:
        assert recv.execute(
            "SELECT COUNT(*) FROM gl_journals WHERE source_type='customer_payment'"
        ).fetchone()[0] == 2
    finally:
        recv.close()


def test_a_collided_journals_legacy_reversal_is_skipped_with_it_not_resent(gl):
    """F1 + F3 together, on the clone shape F3 exists for (verifier 2's
    repro6): A and C are byte-identical copies of one pre-v41 database that
    holds a journal X and its reversal R (X + R = 0). A backfills first and
    sends both. C's own backfill runs only after it has pulled A's copies.

    X on C collides with its pulled twin and is skipped, uid left NULL (F3).
    F1 then derives R's source_uid from X's stored uid -- NULL on C, but X's
    real uid on A -- so C's R hashed to a uid A never used, was not a
    collision, and was SENT: every peer booked the same reversal twice. RED
    before the fix: A's cash reads -7.007 after pulling C, where the truth is
    0.000. A collided row's reversal must derive its source_uid from the uid
    that row hashed to (its twin's), so it is recognised as the same fact."""
    conn_a, db_a = _install(prefix='aura-retail-gl-emit-clone-a-')
    try:
        x_id = _raw_legacy(conn_a, gl, CID, 'customer_payment', '2026-09-10', 'PAY-1',
                           [(gl.ACCOUNT_CASH, 7.007, 0.0), (gl.ACCOUNT_AR, 0.0, 7.007)])
        r_id = _raw_legacy(conn_a, gl, CID, 'reversal', '2026-09-11', 'PAY-1',
                           [(gl.ACCOUNT_AR, 7.007, 0.0), (gl.ACCOUNT_CASH, 0.0, 7.007)],
                           source_id=x_id, reversed_journal_id=x_id,
                           posted_at='2026-09-11 10:00:00')
        # The clone: sqlite's backup API, not a file copy -- the install runs
        # in WAL mode, so a bare copy of the main file can miss committed rows.
        db_c = db_a + '.clone'
        clone = sqlite3.connect(db_c)
        try:
            conn_a.backup(clone)
        finally:
            clone.close()

        gl.backfill_legacy_journals(conn_a)
        a_events = _outbox_rows(conn_a)
        assert len(a_events) == 2

        _pull_into(db_c, a_events)
        conn_c = sqlite3.connect(db_c, timeout=30)
        conn_c.row_factory = sqlite3.Row
        conn_c.execute('PRAGMA foreign_keys=ON')
        try:
            result_c = gl.backfill_legacy_journals(conn_c)
            assert sorted(c['id'] for c in result_c['uid_collisions']) == [x_id, r_id], (
                f"C's own X and R are the same two facts A already sent, so both "
                f"must be recognised as pulled twins: {result_c}"
            )
            c_events = _outbox_rows(conn_c)
            assert c_events == [], (
                f"C re-sent {[(e['payload']['source_type'], e['payload']['reversed_journal_uid']) for e in c_events]} "
                f"-- facts every peer already holds"
            )
        finally:
            conn_c.close()

        _pull_into(db_a, c_events)
        assert _account_net(conn_a, CID, gl.ACCOUNT_CASH) == Decimal('0.000'), (
            "A's books moved after pulling C's backfill: the same reversal was booked twice"
        )
    finally:
        conn_a.close()
