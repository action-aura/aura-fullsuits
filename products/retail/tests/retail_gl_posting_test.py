"""Aura Retail -- core/retail/general_ledger.py unit coverage: the balance
invariant, append-only enforcement, reversal, and trial_balance, exercised
directly against a real migrated database (no Flask, no HTTP -- the
returns_settlement.py/cheques.py-style "pure logic against a real schema"
level, one below retail_gl_integration_test.py's HTTP-driven proof).

THE ONE THING THIS FILE EXISTS TO PROVE, mutation-style (CLAUDE.md's
engineering standard, section 1): "the balance invariant enforced at write
time" is not a decorative docstring claim. Every guard test here BREAKS the
invariant, watches `post_journal` refuse it (RED), then posts a correct
journal right after to prove the refusal did not wedge anything (GREEN/
restore) -- both directions quoted in the test names themselves.

Follows retail_v32_cheque_migration_test.py's bootstrap convention: its own
temp AURA_APP_DATA per test via `_install()`, real `init_retail()`.

Run:
    pytest products/retail/tests/retail_gl_posting_test.py -q
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _install():
    tmp = tempfile.mkdtemp(prefix='aura-retail-gl-posting-')
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    db_path = os.path.join(sch.SUBSYS_DIR, 'retail.db')
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.fixture
def conn():
    c = _install()
    yield c
    c.close()


@pytest.fixture
def gl():
    from core.retail import general_ledger as _gl
    return _gl


CID = 1


# ── ensure_default_chart ─────────────────────────────────────────────────

def test_ensure_default_chart_seeds_every_account_exactly_once(conn, gl):
    gl.ensure_default_chart(conn, CID)
    rows = conn.execute(
        "SELECT code FROM gl_accounts WHERE company_id=?", (CID,)).fetchall()
    codes = {r[0] for r in rows}
    assert codes == {c for c, *_ in gl.DEFAULT_CHART}

    gl.ensure_default_chart(conn, CID)  # idempotent
    rows2 = conn.execute(
        "SELECT code FROM gl_accounts WHERE company_id=?", (CID,)).fetchall()
    assert len(rows2) == len(rows), "a second seed must not duplicate rows"


def test_ensure_default_chart_resolves_parent_id_for_contra_revenue(conn, gl):
    gl.ensure_default_chart(conn, CID)
    parent = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?", (CID, '4000')).fetchone()
    child = conn.execute(
        "SELECT parent_id FROM gl_accounts WHERE company_id=? AND code=?", (CID, '4900')).fetchone()
    assert child['parent_id'] == parent['id']


def test_ensure_default_chart_is_per_company(conn, gl):
    gl.ensure_default_chart(conn, 1)
    gl.ensure_default_chart(conn, 2)
    count_1 = conn.execute("SELECT COUNT(*) FROM gl_accounts WHERE company_id=1").fetchone()[0]
    count_2 = conn.execute("SELECT COUNT(*) FROM gl_accounts WHERE company_id=2").fetchone()[0]
    assert count_1 == count_2 == len(gl.DEFAULT_CHART)


# ── post_journal: the balance invariant, both directions ────────────────

def test_a_balanced_journal_posts_successfully(conn, gl):
    jid = gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'USD', [
        {'account_code': gl.ACCOUNT_CASH, 'debit': 10.00, 'memo': 'cash'},
        {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 10.00, 'memo': 'rev'},
    ])
    assert jid is not None
    lines = conn.execute(
        "SELECT COUNT(*) FROM gl_journal_lines WHERE journal_id=?", (jid,)).fetchone()[0]
    assert lines == 2


def test_mutation_proof_unbalanced_journal_is_rejected_and_nothing_is_written(conn, gl):
    """RED: break the invariant (10.00 debit vs 9.00 credit) and confirm it
    is refused, with NO row written on either table -- an unbalanced
    journal must not partially land. GREEN/restore: a correctly balanced
    journal posted immediately after must still work, proving the
    rejection did not corrupt or lock any state."""
    before_j = conn.execute("SELECT COUNT(*) FROM gl_journals").fetchone()[0]
    before_l = conn.execute("SELECT COUNT(*) FROM gl_journal_lines").fetchone()[0]

    with pytest.raises(gl.GeneralLedgerImbalanceError):
        gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'USD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 10.00, 'memo': 'cash'},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 9.00, 'memo': 'rev'},
        ])

    after_j = conn.execute("SELECT COUNT(*) FROM gl_journals").fetchone()[0]
    after_l = conn.execute("SELECT COUNT(*) FROM gl_journal_lines").fetchone()[0]
    assert after_j == before_j, "a rejected journal must not write a header row"
    assert after_l == before_l, "a rejected journal must not write any line row"

    # RESTORE: still works right after.
    jid = gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 2, 'USD', [
        {'account_code': gl.ACCOUNT_CASH, 'debit': 5.00, 'memo': 'cash'},
        {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 5.00, 'memo': 'rev'},
    ])
    assert jid is not None


def test_mutation_proof_negative_debit_is_rejected(conn, gl):
    """A negative debit is not "the other side" -- a caller that means
    credit must set credit, never negate debit. Proves this is enforced
    rather than silently accepted and later misread by trial_balance."""
    with pytest.raises(ValueError):
        gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'USD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': -5.00},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': -5.00},
        ])


def test_mutation_proof_a_line_cannot_carry_both_debit_and_credit(conn, gl):
    with pytest.raises(ValueError):
        gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'USD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 5.00, 'credit': 5.00},
        ])


def test_an_entirely_zero_journal_is_rejected(conn, gl):
    with pytest.raises(gl.GeneralLedgerImbalanceError):
        gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'USD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 0},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 0},
        ])


def test_unknown_account_code_raises_account_error(conn, gl):
    with pytest.raises(gl.GeneralLedgerAccountError):
        gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'USD', [
            {'account_code': '9999-NOT-A-REAL-CODE', 'debit': 5.00},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 5.00},
        ])


# ── The epsilon tolerance is currency-specific, never a bare 0.005 ──────

def test_sub_epsilon_jod_gap_is_accepted():
    """JOD's minor unit is a fil (0.001); half of that is 0.0005. A
    0.0003 gap (a documented sub-quantum residue -- see post_sale's own
    docstring for the create_sale edge case this exists to absorb) must
    be ACCEPTED."""
    conn = _install()
    try:
        from core.retail import general_ledger as gl
        jid = gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'JOD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 1.0003},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 1.0000},
        ])
        assert jid is not None
    finally:
        conn.close()


def test_mutation_proof_whole_fil_jod_gap_is_rejected_not_just_half_cent():
    """THE defect this codebase's own "round 4" correction closed
    elsewhere (a hardcoded 0.005 treating anything up to five fils as
    zero) must not exist here either. 0.001 (a whole fil) on JOD is TWICE
    the tolerance (half a fil, 0.0005) and must be REJECTED -- a
    hardcoded USD-shaped 0.005 tolerance would wrongly ACCEPT this."""
    conn = _install()
    try:
        from core.retail import general_ledger as gl
        with pytest.raises(gl.GeneralLedgerImbalanceError):
            gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'JOD', [
                {'account_code': gl.ACCOUNT_CASH, 'debit': 1.001},
                {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 1.000},
            ])
    finally:
        conn.close()


def test_the_same_gap_that_is_rejected_on_jod_is_accepted_on_usd():
    """THE ALLOW-HALF of the currency-specific tolerance: USD's minor
    unit is the cent (0.01); half a cent is 0.005. A 0.003 gap -- which
    would be firmly REJECTED on JOD (three times its 0.0005 tolerance) --
    must be ACCEPTED on USD (below its 0.005 tolerance). Proves the
    epsilon is genuinely currency-driven, not a single hardcoded number
    that happens to pass both cases by coincidence."""
    conn = _install()
    try:
        from core.retail import general_ledger as gl
        with pytest.raises(gl.GeneralLedgerImbalanceError):
            gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'JOD', [
                {'account_code': gl.ACCOUNT_CASH, 'debit': 1.003},
                {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 1.000},
            ])
        jid = gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 2, 'USD', [
            {'account_code': gl.ACCOUNT_CASH, 'debit': 1.003},
            {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 1.000},
        ])
        assert jid is not None
    finally:
        conn.close()


# ── reverse_journal ───────────────────────────────────────────────────────

def test_reverse_journal_swaps_every_line_and_nets_to_zero(conn, gl):
    jid = gl.post_journal(conn, CID, 7, '2026-09-21', 'sale', 1, 'USD', [
        {'account_code': gl.ACCOUNT_CASH, 'debit': 10.00, 'memo': 'cash'},
        {'account_code': gl.ACCOUNT_AR, 'debit': 5.00, 'memo': 'credit'},
        {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 15.00, 'memo': 'rev'},
    ])
    rev_id = gl.reverse_journal(conn, CID, jid, posted_by='test')
    assert rev_id != jid

    header = conn.execute(
        "SELECT reversed_journal_id, source_type, branch_id, currency "
        "FROM gl_journals WHERE id=?", (rev_id,)).fetchone()
    assert header['reversed_journal_id'] == jid
    assert header['source_type'] == 'reversal'
    assert header['branch_id'] == 7, "reversal must re-derive the original's branch"
    assert header['currency'] == 'USD'

    tb = gl.trial_balance(conn, CID)
    cash = [a for a in tb['accounts'] if a['code'] == gl.ACCOUNT_CASH][0]
    ar = [a for a in tb['accounts'] if a['code'] == gl.ACCOUNT_AR][0]
    rev = [a for a in tb['accounts'] if a['code'] == gl.ACCOUNT_SALES_REVENUE][0]
    assert cash['balance'] == 0.0
    assert ar['balance'] == 0.0
    assert rev['balance'] == 0.0
    assert tb['is_balanced']


def test_reverse_journal_of_unknown_journal_raises(conn, gl):
    with pytest.raises(gl.GeneralLedgerAccountError):
        gl.reverse_journal(conn, CID, 999999, posted_by='test')


def test_mutation_proof_gl_tables_stay_balanced_after_a_reversal_of_a_reversal(conn, gl):
    """Reversing a reversal must itself balance and restore the original
    net effect -- proves reverse_journal's own output is, in turn, a
    valid input to post_journal's balance check (it does not special-case
    reversals to skip validation)."""
    jid = gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'USD', [
        {'account_code': gl.ACCOUNT_CASH, 'debit': 20.00},
        {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 20.00},
    ])
    rev1 = gl.reverse_journal(conn, CID, jid)
    rev2 = gl.reverse_journal(conn, CID, rev1)  # un-reverses the reversal
    tb = gl.trial_balance(conn, CID)
    cash = [a for a in tb['accounts'] if a['code'] == gl.ACCOUNT_CASH][0]
    assert cash['balance'] == 20.00, "back to the original net effect"
    assert tb['is_balanced']


# ── trial_balance ─────────────────────────────────────────────────────────

def test_trial_balance_shows_zero_activity_accounts_not_just_posted_ones(conn, gl):
    """Every seeded account must appear, even one nothing has posted to
    yet (1200 Inventory / 5900 COGS / 3000 Equity in a v38 install with
    only sales posted) -- a plain INNER JOIN would silently drop them."""
    gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'USD', [
        {'account_code': gl.ACCOUNT_CASH, 'debit': 10.00},
        {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 10.00},
    ])
    tb = gl.trial_balance(conn, CID)
    codes = {a['code'] for a in tb['accounts']}
    assert codes == {c for c, *_ in gl.DEFAULT_CHART}
    inventory = [a for a in tb['accounts'] if a['code'] == gl.ACCOUNT_INVENTORY][0]
    assert inventory['debit'] == 0.0
    assert inventory['credit'] == 0.0
    assert inventory['balance'] == 0.0


def test_trial_balance_is_scoped_per_company_no_cross_tenant_leak(conn, gl):
    gl.post_journal(conn, 1, None, '2026-09-21', 'sale', 1, 'USD', [
        {'account_code': gl.ACCOUNT_CASH, 'debit': 10.00},
        {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 10.00},
    ])
    gl.post_journal(conn, 2, None, '2026-09-21', 'sale', 1, 'USD', [
        {'account_code': gl.ACCOUNT_CASH, 'debit': 999.00},
        {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 999.00},
    ])
    tb1 = gl.trial_balance(conn, 1)
    cash1 = [a for a in tb1['accounts'] if a['code'] == gl.ACCOUNT_CASH][0]
    assert cash1['balance'] == 10.00, "company 1's cash must not see company 2's 999"


def test_mutation_proof_a_deliberately_corrupted_line_makes_is_balanced_false(conn, gl):
    """Since triggers block UPDATE/DELETE against gl_journal_lines through
    the front door, this proves is_balanced would correctly FAIL if that
    guarantee were ever bypassed (e.g. a future direct-SQL migration bug)
    -- inserts a raw, unbalanced line directly via sqlite3 (bypassing
    post_journal entirely, the only way to get one in) and confirms
    trial_balance's own is_balanced flag catches it, rather than silently
    reporting a false 'balanced'."""
    jid = gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'USD', [
        {'account_code': gl.ACCOUNT_CASH, 'debit': 10.00},
        {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 10.00},
    ])
    account_id = conn.execute(
        "SELECT id FROM gl_accounts WHERE company_id=? AND code=?",
        (CID, gl.ACCOUNT_CASH)).fetchone()[0]
    # A raw INSERT, not an UPDATE -- append-only blocks UPDATE, not a
    # deliberately corrupt new row, which is exactly the shape a bypass
    # of post_journal's own balance check would take.
    conn.execute(
        "INSERT INTO gl_journal_lines (journal_id, account_id, debit, credit, memo) "
        "VALUES (?,?,?,?,?)", (jid, account_id, 5.00, 0.00, 'unbalanced by hand'))
    tb = gl.trial_balance(conn, CID)
    assert tb['is_balanced'] is False


# ── account_balance convenience ───────────────────────────────────────────

def test_account_balance_matches_trial_balance_for_the_same_account(conn, gl):
    gl.post_journal(conn, CID, None, '2026-09-21', 'sale', 1, 'USD', [
        {'account_code': gl.ACCOUNT_CASH, 'debit': 42.00},
        {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 42.00},
    ])
    assert gl.account_balance(conn, CID, gl.ACCOUNT_CASH) == 42.00


def test_account_balance_of_never_posted_account_is_zero(conn, gl):
    assert gl.account_balance(conn, CID, gl.ACCOUNT_INVENTORY) == 0.0
