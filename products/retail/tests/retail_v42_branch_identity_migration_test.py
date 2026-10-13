"""Schema v42 branch-identity migration: it may re-key ONE branch, and it may
never move a single unit of money.

WHAT v42 DOES HERE. `converge_default_branch_uid` gives the shop's placeholder
branch ("Main Branch" / "Main Store") the ONE uid every computer derives from
the licence, records the old uid in `branch_uid_aliases`, and rewrites three
non-money references (`doc_series.branch_uid`, the `branch_code:<uid>` setting,
the `branch_uid` inside `numbering_till:*`). `branches.id` never changes, so
every sale, movement, balance, journal and drawer keeps pointing at the same
local row. This file proves that claim on the money tables themselves, by
hashing every row of them before and after a REAL boot -- not by trusting the
docstring.

WHY THE ROW HASHES ARE TAKEN OVER ALL COLUMNS. A migration that is "money
neutral" but quietly rewrites a `unit_cost` or a `created_at` is not neutral.
The strict form is used everywhere; the census copies are first brought to
v42's doorstep with converge STUBBED OUT (so older migrations, which
legitimately write to money tables, are not blamed on v42), then rewound one
version and booted for real.

THE RULE UNDER TEST is narrow on purpose: rewrite only when there is EXACTLY
ONE active placeholder-named branch AND it is the lowest-id active branch.
`Downtown` at the lowest id must NOT be rewritten just because a "Main Branch"
exists further down -- that guess would re-key the wrong place and drag its
scoped employees and pins with it.

CENSUS COPIES. `census/copies/<device>/` (copies of the lab shops' databases,
taken by `census/copy.py`; nothing live is ever opened). Location comes from
`AURA_CENSUS_COPIES`; if the folder is absent (another machine) those cases
skip and say why. They never open a live AuraLab or %LOCALAPPDATA% database:
each is COPIED again into a temp dir before it is booted.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LIC = 'c0ffee11c0ffee11c0ffee11c0ffee11'      # hex-only: never coerced to a number
OLD_UID = '11111111-1111-4111-8111-111111111111'
OTHER_UID = '22222222-2222-4222-8222-222222222222'
TERMINAL = 'till-uid-1'

MONEY_TABLES = ('sales', 'sale_items', 'payments', 'returns', 'return_items',
                'inventory_movements', 'inventory_balances', 'gl_journals',
                'gl_journal_lines', 'cash_sessions', 'doc_series_counter')

CENSUS_DEFAULT = r'C:\Users\MSI\.claude\jobs\c5a4f4c4\tmp\p2s\census\copies'

_SEQ = itertools.count(1)


# --------------------------------------------------------------- plumbing --
def _activate(app_data: str):
    """Point the schema module at ONE temp install. Returns the module."""
    os.environ['AURA_APP_DATA'] = app_data
    os.environ['AURA_STANDALONE'] = '1'      # no dev demo seeding of branches
    import database.schema as sch
    sch.BASE_DIR = os.path.join(app_data, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    os.makedirs(sch.SUBSYS_DIR, exist_ok=True)
    return sch


def _db_path(sch) -> str:
    return os.path.join(sch.SUBSYS_DIR, 'retail.db')


def _open(path, foreign_keys=False):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(f'PRAGMA foreign_keys={"ON" if foreign_keys else "OFF"}')
    return conn


def _write_licence(sch, license_id):
    """A licensing.db carrying just what `owner_issued_company_id` reads."""
    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'licensing.db'))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS licensing_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            licensing_schema_version INTEGER NOT NULL,
            product_code TEXT NOT NULL, platform TEXT NOT NULL,
            current_state TEXT NOT NULL, owner_installation_id TEXT,
            assertion_envelope_json TEXT, updated_at TEXT NOT NULL)""")
    conn.execute('DELETE FROM licensing_state')
    conn.execute(
        'INSERT INTO licensing_state (id, licensing_schema_version, product_code, platform, '
        'current_state, owner_installation_id, assertion_envelope_json, updated_at) '
        'VALUES (1,1,?,?,?,?,?,?)',
        ('AURA_RETAIL', 'WINDOWS', 'ACTIVE_ONLINE', 'inst-1',
         json.dumps({'payload': {'license_public_id': license_id}}), '2026-09-30T00:00:00'))
    conn.commit()
    conn.close()


def _ins(conn, table, **vals):
    """INSERT one row, filling every NOT NULL / no-default column the caller
    did not name with a typed placeholder, so this file does not hand-copy 15
    money-table schemas (which would rot the next time a column is added)."""
    row = dict(vals)
    for _cid, name, ctype, notnull, dflt, pk in conn.execute(f'PRAGMA table_info({table})').fetchall():
        if name in row:
            continue
        t = (ctype or '').upper()
        if pk and t.startswith('INT'):
            continue                                   # rowid alias
        if not (pk or (notnull and dflt is None)):
            continue
        if 'INT' in t:
            row[name] = 1
        elif any(k in t for k in ('REAL', 'FLOA', 'DOUB', 'NUM', 'DEC')):
            row[name] = 0
        else:
            row[name] = f'{table}-{name}-{next(_SEQ)}'
    cols = ', '.join(row)
    cur = conn.execute(f'INSERT INTO {table} ({cols}) VALUES ({", ".join("?" * len(row))})',
                       list(row.values()))
    return cur.lastrowid


def _seed_money(conn, cid, bid):
    """A small, COHERENT set of money rows on branch `bid`: stock balance ==
    the ledger (so drift is genuinely 0 before, not merely equal), a sale with
    its lines and tender, a return, a drawer, a series counter, a balanced
    journal. Coherent on purpose: a fixture with pre-existing drift could not
    tell "the migration created drift" from "the fixture had it"."""
    from core.retail import general_ledger as gl
    _ins(conn, 'products', id='P1', company_id=cid, sku='SKU-P1', name='Widget',
         cost_price=2.0, sell_price=5.0)
    _ins(conn, 'inventory_movements', company_id=cid, product_id='P1', branch_id=bid,
         movement_type='purchase', quantity=10, unit_cost=2.0, uid='mv-1')
    _ins(conn, 'inventory_movements', company_id=cid, product_id='P1', branch_id=bid,
         movement_type='sale', quantity=-1, unit_cost=2.0, uid='mv-2')
    _ins(conn, 'inventory_balances', company_id=cid, product_id='P1', branch_id=bid,
         quantity_on_hand=9, avg_cost=2.0)
    _ins(conn, 'cash_sessions', id='cs-1', company_id=cid, branch_id=bid, opened_by='cashier',
         opening_float=10.0, status='open')
    sale_id = _ins(conn, 'sales', company_id=cid, sale_number='S-1', branch_id=bid, total=5.0,
                   subtotal=5.0, amount_paid=5.0, payment_method='cash', status='completed',
                   session_id='cs-1', uid='sale-uid-1')
    _ins(conn, 'sale_items', sale_id=sale_id, product_id='P1', quantity=1, unit_price=5.0,
         line_total=5.0, uid='si-uid-1', unit_cost=2.0)
    _ins(conn, 'payments', company_id=cid, sale_id=sale_id, method='cash', amount=5.0,
         status='completed', uid='pay-uid-1')
    ret_id = _ins(conn, 'returns', company_id=cid, return_number='R-1', sale_id=sale_id,
                  branch_id=bid, refund_amount=5.0, status='completed', uid='ret-uid-1')
    _ins(conn, 'return_items', return_id=ret_id, product_id='P1', quantity=1, unit_price=5.0,
         line_total=5.0, uid='ri-uid-1')
    _ins(conn, 'doc_series_counter', series_id='ser-1', last_no=3)
    gl.post_journal(conn, cid, bid, '2026-09-01', 'sale', sale_id, 'JOD',
                    [{'account_code': gl.ACCOUNT_CASH, 'debit': 5},
                     {'account_code': gl.ACCOUNT_SALES_REVENUE, 'credit': 5}],
                    posted_by='System')


def _seed_refs(conn, cid, uid, settings=True):
    """The three NON-money references converge rewrites, all naming `uid`."""
    _ins(conn, 'doc_series', id='ser-1', company_id=cid, doc_type='sale', code='MAI1',
         label='Main till', branch_uid=uid, allocator_terminal_uid=TERMINAL, status='active',
         pad_width=6, start_no=1, row_version=3)
    if settings:
        conn.execute('CREATE TABLE IF NOT EXISTS retail_settings ('
                     'company_id INTEGER, skey TEXT, svalue TEXT, PRIMARY KEY (company_id, skey))')
        conn.execute('INSERT INTO retail_settings VALUES (?,?,?)', (cid, f'branch_code:{uid}', 'MAI'))
        conn.execute('INSERT INTO retail_settings VALUES (?,?,?)',
                     (cid, f'numbering_till:{TERMINAL}',
                      json.dumps({'branch_uid': uid, 'till_number': 1})))


def _build(tmp_path, branches, *, licensed=True, branch_company=None, settings=True,
           canonical_setting_first=False):
    """A REAL install taken back to v41 with the given branches, ready to be
    migrated by the genuine `init_retail()`.

    `branches`: list of (name, uid, status), inserted in order (ids 1..n).
    Money rows and references are attached to the LOWEST-id branch, because
    that is where an unpinned till files its sales.
    """
    sch = _activate(str(tmp_path))
    sch.init_retail()                                  # a true v42 install, no branches
    path = _db_path(sch)
    conn = _open(path)
    # Rewind exactly what v42 added, so the next boot takes the REAL v41 -> v42
    # upgrade path (table creation, converge, announce).
    conn.executescript('DROP TABLE IF EXISTS branch_uid_aliases; DROP TABLE IF EXISTS till_allocations;')
    conn.execute('PRAGMA user_version=41')
    conn.commit()
    cid = branch_company if branch_company is not None else LIC
    for name, uid, status in branches:
        conn.execute('INSERT INTO branches (company_id, name, status, uid) VALUES (?,?,?,?)',
                     (cid, name, status, uid))
    if branches:
        first_id = conn.execute('SELECT MIN(id) FROM branches').fetchone()[0]
        _seed_money(conn, cid, first_id)
        _seed_refs(conn, cid, branches[0][1], settings=settings)
        if canonical_setting_first:
            conn.execute('INSERT INTO retail_settings VALUES (?,?,?)',
                         (cid, f'branch_code:{sch.default_branch_uid(LIC)}', 'SAVED'))
    conn.commit()
    conn.close()
    if licensed:
        _write_licence(sch, LIC)
    return sch


def _digest(conn, table) -> tuple[int, str]:
    cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
    rows = conn.execute(f'SELECT {", ".join(cols)} FROM {table} ORDER BY rowid').fetchall()
    h = hashlib.sha256(repr([tuple(r) for r in rows]).encode()).hexdigest()
    return len(rows), h


def _dump_all(conn) -> dict:
    """Every row of every table, for the 'a second boot is a no-op' check."""
    out = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        try:
            rows = conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(f'SELECT * FROM "{name}"').fetchall()
        out[name] = [tuple(r) for r in rows]
    return out


def _snapshot(sch, cid) -> dict:
    from core.retail import general_ledger as gl
    from core.retail.stock_reconciliation import compute_drift
    conn = _open(_db_path(sch))
    try:
        for t in MONEY_TABLES:
            assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                (t,)).fetchone(), f'money table {t} vanished from the list under test'
        snap = {
            'money': {t: _digest(conn, t) for t in MONEY_TABLES},
            'drift': [dict(d) for d in compute_drift(conn, cid)],
            'trial_balance': json.dumps(gl.trial_balance(conn, cid), sort_keys=True, default=str),
            'integrity': conn.execute('PRAGMA integrity_check').fetchone()[0],
            'user_version': conn.execute('PRAGMA user_version').fetchone()[0],
            'branches': [tuple(r) for r in conn.execute(
                'SELECT id, company_id, name, status, uid FROM branches ORDER BY id').fetchall()],
        }
        conn.rollback()                    # trial_balance may seed the chart lazily; keep it out
        return snap
    finally:
        conn.close()


def _rows(sch, sql, params=()):
    conn = _open(_db_path(sch))
    try:
        return [tuple(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def _assert_money_neutral(before, after):
    # DAY-1c UPDATE: the head is now v43 (payments.cash_session_id), so a v41 database boots all the way to 43 and the pre-migration backup is named v41-to-v43. This is the head-version tripwire, hard-coded on purpose; v43's own migration is proved by retail_day1_v43_migration_test.py, and what this line cannot catch is a v43 defect.
    # v44 UPDATE: the head is now v44 (party_balance tables); same tripwire, same caveat -- v44's own migration is proved by retail_v44_party_balance_migration_test.py.
    # v46 UPDATE: the head is now v46 (v45 PO sync identity, v46 PO receipts); same tripwire, same caveat -- v45/v46's own migrations are proved by their own migration tests, and what this line cannot catch is a v45/v46 defect.
    assert after['user_version'] == 54  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]
    for t in MONEY_TABLES:
        assert before['money'][t] == after['money'][t], f'{t}: rows changed across the v42 boot'
    assert after['drift'] == before['drift']
    assert after['trial_balance'] == before['trial_balance']
    assert after['integrity'] == 'ok'
    # `branches.id` never moves: only the uid cell may differ.
    assert [b[:4] for b in before['branches']] == [b[:4] for b in after['branches']]


def _assert_backup_is_the_pre_migration_state(sch, before_branches):
    bdir = os.path.join(sch.BASE_DIR, 'migration_backups')
    names = sorted(n for n in os.listdir(bdir) if 'v41-to-v53' in n)  # v52 UPDATE: head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3) | prior v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py
    assert names, f'no v41-to-v53 backup in {os.listdir(bdir)}'  # v52 UPDATE: head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3) | prior v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py
    conn = sqlite3.connect(f'file:{os.path.join(bdir, names[-1])}?mode=ro', uri=True)
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 41
        got = [tuple(r) for r in conn.execute(
            'SELECT id, company_id, name, status, uid FROM branches ORDER BY id').fetchall()]
    finally:
        conn.close()
    assert got == before_branches, 'the backup is not the pre-migration state'


def _boot_twice(sch, cid):
    """Snapshot -> real boot -> snapshot -> second boot -> full dump compare."""
    before = _snapshot(sch, cid)
    assert before['user_version'] == 41 and before['drift'] == [] and before['integrity'] == 'ok'
    sch.init_retail()
    after = _snapshot(sch, cid)
    conn = _open(_db_path(sch))
    dump1 = _dump_all(conn)
    conn.close()
    sch.init_retail()
    conn = _open(_db_path(sch))
    dump2 = _dump_all(conn)
    conn.close()
    assert dump1 == dump2, 'a second boot changed the database: the migration is not idempotent'
    _assert_money_neutral(before, after)
    _assert_backup_is_the_pre_migration_state(sch, before['branches'])
    return before, after


def _aliases(sch):
    return _rows(sch, 'SELECT alias_uid, canonical_uid, reason, announced_at_utc '
                      'FROM branch_uid_aliases ORDER BY alias_uid')


def _alias_events(sch):
    return _rows(sch, "SELECT entity_id, event_type FROM sync_outbox WHERE entity_type='branch_alias'")


def _skip_audits(sch):
    return _rows(sch, "SELECT details FROM audit_log WHERE action='DEFAULT_BRANCH_CONVERGE_SKIPPED'")


def _assert_untouched_refs(sch, uid):
    assert _rows(sch, 'SELECT branch_uid, row_version FROM doc_series') == [(uid, 3)]


# ----------------------------------------------------------------- cases --
def test_the_one_placeholder_branch_converges_and_no_money_moves(tmp_path):
    sch = _build(tmp_path, [('Main Branch', OLD_UID, 'active')])
    canon = sch.default_branch_uid(LIC)
    before, after = _boot_twice(sch, LIC)

    assert [b[4] for b in after['branches']] == [canon]
    assert [b[:4] for b in after['branches']] == [(1, LIC, 'Main Branch', 'active')]
    [(alias, canonical, reason, announced)] = _aliases(sch)
    assert (alias, canonical) == (OLD_UID, canon)
    assert reason == 'default_branch_converge'
    assert announced is not None, 'the alias was never announced to the other computers'
    # The three references follow the branch, and `row_version` is NOT bumped
    # (a bump would let this computer outrank a peer's identical rewrite).
    assert _rows(sch, 'SELECT branch_uid, row_version FROM doc_series') == [(canon, 3)]
    keys = dict(_rows(sch, 'SELECT skey, svalue FROM retail_settings'))
    assert f'branch_code:{canon}' in keys and f'branch_code:{OLD_UID}' not in keys
    assert json.loads(keys[f'numbering_till:{TERMINAL}'])['branch_uid'] == canon
    # Announced exactly once, and the second boot (inside _boot_twice) added none.
    assert _alias_events(sch) == [(OLD_UID, 'create')]
    assert _skip_audits(sch) == []


def test_two_placeholders_are_ambiguous_and_nothing_changes(tmp_path):
    sch = _build(tmp_path, [('Main Branch', OLD_UID, 'active'), ('Main Store', OTHER_UID, 'active')])
    before, after = _boot_twice(sch, LIC)
    assert after['branches'] == before['branches']          # no uid moved
    assert _aliases(sch) == []
    assert _alias_events(sch) == []
    _assert_untouched_refs(sch, OLD_UID)
    assert f'branch_code:{OLD_UID}' in dict(_rows(sch, 'SELECT skey, svalue FROM retail_settings'))
    # The refusal is recorded once, not once per launch (two boots ran).
    assert len(_skip_audits(sch)) == 1


def test_a_real_branch_at_the_lowest_id_is_not_rewritten(tmp_path):
    """Downtown sits at id 1 (where the unpinned till files its sales), 'Main
    Branch' at id 2. A name match alone would re-key id 2 and leave the money
    on an unaliased id 1. It must refuse."""
    sch = _build(tmp_path, [('Downtown', OTHER_UID, 'active'), ('Main Branch', OLD_UID, 'active')])
    before, after = _boot_twice(sch, LIC)
    assert after['branches'] == before['branches']
    assert [b[4] for b in after['branches']] == [OTHER_UID, OLD_UID]
    assert _aliases(sch) == []
    _assert_untouched_refs(sch, OTHER_UID)
    assert len(_skip_audits(sch)) == 1


def test_no_licence_means_nothing_to_converge_onto(tmp_path):
    sch = _build(tmp_path, [('Main Branch', OLD_UID, 'active')], licensed=False)
    before, after = _boot_twice(sch, LIC)
    assert after['branches'] == before['branches']
    assert _aliases(sch) == [] and _alias_events(sch) == []
    _assert_untouched_refs(sch, OLD_UID)


def test_a_company_not_yet_rebound_onto_the_licence_is_left_alone(tmp_path):
    """The census' owner install: branches still carry the OLD local company id.
    Converging them would key the wrong tenant; the rebind calls converge itself
    once it has run."""
    sch = _build(tmp_path, [('Main Branch', OLD_UID, 'active')], branch_company=1)
    before, after = _boot_twice(sch, 1)
    assert after['branches'] == before['branches']
    assert _aliases(sch) == [] and _alias_events(sch) == []
    _assert_untouched_refs(sch, OLD_UID)


def test_a_shop_with_no_retail_settings_table_still_converges(tmp_path):
    """`retail_settings` is created lazily by retail_api, so a database that
    never served a request has none. The rewrite must not assume it."""
    sch = _build(tmp_path, [('Main Branch', OLD_UID, 'active')], settings=False)
    assert _rows(sch, "SELECT 1 FROM sqlite_master WHERE name='retail_settings'") == []
    canon = sch.default_branch_uid(LIC)
    before, after = _boot_twice(sch, LIC)
    assert [b[4] for b in after['branches']] == [canon]
    assert _rows(sch, 'SELECT branch_uid FROM doc_series') == [(canon,)]
    assert len(_aliases(sch)) == 1


def test_a_fresh_shop_with_no_branches_yet_skips_quietly(tmp_path):
    sch = _build(tmp_path, [])
    conn = _open(_db_path(sch))
    conn.close()
    sch.init_retail()
    sch.init_retail()
    assert _rows(sch, 'PRAGMA user_version') == [(54,)]   # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]
    assert _aliases(sch) == [] and _skip_audits(sch) == []


def test_a_branch_that_already_carries_the_canonical_uid_gets_no_alias(tmp_path):
    from database.schema import default_branch_uid
    canon = default_branch_uid(LIC)
    sch = _build(tmp_path, [('Main Branch', canon, 'active')])
    before, after = _boot_twice(sch, LIC)
    assert after['branches'] == before['branches']
    assert _aliases(sch) == [] and _alias_events(sch) == []


def test_a_setting_already_saved_under_the_canonical_uid_is_not_overwritten(tmp_path):
    sch = _build(tmp_path, [('Main Branch', OLD_UID, 'active')], canonical_setting_first=True)
    canon = sch.default_branch_uid(LIC)
    _boot_twice(sch, LIC)
    keys = dict(_rows(sch, 'SELECT skey, svalue FROM retail_settings'))
    assert keys[f'branch_code:{canon}'] == 'SAVED', 'a value already saved under the canonical uid must win'
    assert keys[f'branch_code:{OLD_UID}'] == 'MAI', 'the orphan alias key is left, unread'


# ---------------------------------------------------------- census copies --
def _census_root() -> Path:
    return Path(os.environ.get('AURA_CENSUS_COPIES', CENSUS_DEFAULT))


def _census_names() -> list:
    root = _census_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir()
                  if (p / 'database' / 'subsystems' / 'retail.db').exists())


def _census_migrate(name: str, tmp_path):
    """Copy census/<name> to a temp install, bring it to v42's doorstep with
    converge stubbed (so older migrations are not blamed on v42), rewind one
    version, then boot for real. Returns (sch, before, after, licence_id)."""
    app = tmp_path / 'app'
    shutil.copytree(_census_root() / name, app)
    sch = _activate(str(app))
    real_conv, real_ann = sch.converge_default_branch_uid, sch.announce_branch_aliases
    sch.converge_default_branch_uid = lambda conn: {'status': 'stubbed'}
    sch.announce_branch_aliases = lambda conn: 0
    try:
        sch.init_retail()
    finally:
        sch.converge_default_branch_uid, sch.announce_branch_aliases = real_conv, real_ann
    conn = _open(_db_path(sch))
    conn.executescript('DROP TABLE IF EXISTS branch_uid_aliases; DROP TABLE IF EXISTS till_allocations;')
    conn.execute('PRAGMA user_version=41')
    conn.commit()
    conn.close()
    lic = sch.owner_issued_company_id()
    # The company the money belongs to, for drift / trial balance: whatever the
    # branches say (a not-yet-rebound shop keeps its old id).
    rows = _rows(sch, 'SELECT DISTINCT company_id FROM branches')
    cid = rows[0][0] if rows else (lic or 1)
    before = _snapshot(sch, cid)
    sch.init_retail()
    after = _snapshot(sch, cid)
    sch.init_retail()
    conn = _open(_db_path(sch))
    dump2 = _dump_all(conn)
    conn.close()
    sch.init_retail()
    conn = _open(_db_path(sch))
    assert _dump_all(conn) == dump2, f'{name}: a further boot changed the database'
    conn.close()
    return sch, before, after, lic


# A schema-v3 install (2026-08 census, "owner-AuraRetail-stale-v3") cannot be
# brought up AT ALL: the v3 -> v4 customers-uuid step (schema.py ~1834) updates
# `payments.party_id`, a column that only exists on a later version, and raises
# "no such column: party_id" before the chain gets anywhere near v42. That is
# an OLDER migration's defect, not v42's, and it reproduces with converge
# stubbed out. It is marked strict-xfail, restricted to that one exception
# type, so it is visible in every run and turns RED the day someone fixes the
# old step (at which point the mark must be removed and v42 gets measured on
# that copy). It is deliberately not skipped: a skip would hide it.
_ANCIENT_BROKEN_CHAIN = {'owner-AuraRetail-stale-v3'}


def _census_params():
    names = _census_names() or ['none']
    return [pytest.param(n, marks=pytest.mark.xfail(
                strict=True, raises=sqlite3.OperationalError,
                reason='pre-v42 migration step 3->4 cannot upgrade a schema-v3 database '
                       '(no such column: party_id); not caused by v42'))
            if n in _ANCIENT_BROKEN_CHAIN else n
            for n in names]


@pytest.mark.skipif(not _census_names(),
                    reason='no census copies here (AURA_CENSUS_COPIES / job tmp folder absent)')
@pytest.mark.parametrize('name', _census_params())
def test_census_copy_migrates_money_neutral(name, tmp_path):
    sch, before, after, lic = _census_migrate(name, tmp_path)
    # Strict, all columns: v42 (converge + alias + reference rewrite) touched no money row.
    for t in MONEY_TABLES:
        assert before['money'][t] == after['money'][t], f'{name}: {t} changed'
    assert after['drift'] == before['drift']
    assert after['trial_balance'] == before['trial_balance']
    assert after['integrity'] == 'ok' and after['user_version'] == 54  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]
    assert [b[:4] for b in before['branches']] == [b[:4] for b in after['branches']]
    assert any('v41-to-v53' in n for n in os.listdir(os.path.join(sch.BASE_DIR, 'migration_backups')))  # v52 UPDATE: head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3) | prior v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py
    # At most one branch changed uid, and only to the licence-derived one.
    changed = [(b, a) for b, a in zip(before['branches'], after['branches']) if b[4] != a[4]]
    assert len(changed) <= 1
    if changed:
        assert changed[0][1][4] == sch.default_branch_uid(lic)
        assert len(_aliases(sch)) == 1


@pytest.mark.skipif(not _census_names(),
                    reason='no census copies here (AURA_CENSUS_COPIES / job tmp folder absent)')
@pytest.mark.parametrize('pair', [('proof-a2', 'proof-b2'), ('demo-seed-till', 'tunnel-test-till')])
def test_two_computers_of_one_shop_converge_to_one_branch_uid(pair, tmp_path):
    """The point of v42: each computer minted its own random uid for its own
    'Main Branch' (measured in the census), so the same shop had two 'main'
    branches. After the migration both must carry the SAME uid."""
    uids = []
    for i, name in enumerate(pair):
        sub = tmp_path / f'c{i}'
        sub.mkdir()
        sch, before, after, lic = _census_migrate(name, sub)
        assert [b[4] for b in before['branches']] != [b[4] for b in after['branches']], \
            f'{name}: its Main Branch uid never converged (before {before["branches"]})'
        uids.append(tuple(b[4] for b in after['branches']))
        assert uids[-1] == (sch.default_branch_uid(lic),)
    assert uids[0] == uids[1]
