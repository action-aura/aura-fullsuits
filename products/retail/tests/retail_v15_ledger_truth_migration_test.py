"""
Aura Retail -- schema v15 regression coverage: the ledger becomes able to
reproduce the cache (launch-readiness Phase 3,
docs/launch-readiness/phase3-ledger-truth.md, ROADMAP.md's 2026-08-21
reservation). See database/schema.py::
_migrate_seed_opening_counts_and_gate_drift and
core/retail/stock_reconciliation.py.

WHAT V15 CLAIMS, AND THEREFORE WHAT THIS FILE HAS TO PROVE.

`inventory_balances.quantity_on_hand` is a stored number five writers keep in
step with `inventory_movements` by hand. v15 says that after it runs, the
balance is DERIVABLE from the ledger -- and it backs that claim by refusing to
advance `PRAGMA user_version` when it is not true. Phase 5 (devices exchanging
movements and each recomputing its own balances) is unsafe without it: on an
install whose ledger cannot reproduce its balances, sync replaces one
correct-looking number with a different correct-looking number and nobody can
say which was right.

THE FOUR THINGS THIS FILE EXISTS TO HOLD DOWN, all four of which were once
broken here at the same time and all four of which were MEASURED rather than
argued about:

  1. THE ORDINARY LEGACY SHOP MUST ADVANCE, AND MUST COME OUT HOLDING WHAT IT
     WENT IN HOLDING. The importer used to write an absolute
     `SET quantity_on_hand=?` with no movement row (git 13c1c92,
     api/import_api.py:1146) and every sale since wrote one, so the common
     legacy key has sales and no opening row. The seeder covered only keys
     with NO movements, skipped every one of them, and the gate refused --
     which, since app.py calls `init_retail()` unconditionally with no
     handler, meant the POS would not start. The refusal named `repair_drift`
     as the recovery; `_has_ledger_history` was True because of those sales;
     the repair wrote the sales-only sum as the balance:

         shelves [112, 57, 7]  ->  REFUSED (seeded 0)
         -> repair_drift reports "repaired 3, skipped 0"
         -> shelves [-8, -3, -1]
         -> relaunch advances to v15 reporting zero drift

     An opening count is "what must have been on the shelf before the
     recorded history, for that history to end where the balance says it
     does" -- the RESIDUAL, by definition. Section 1b asserts exact shelf
     figures either side of the migration, and asserts the repair is a no-op
     both before and after it.

  2. THE GATE MUST STILL BE ABLE TO FAIL. Fixing (1) by seeding whatever
     closes the gap would make the gate incapable of refusing any input,
     which is the opposite failure and just as bad. Only POSITIVE residuals
     are seeded: a negative one says the ledger accounts for more goods than
     the shelf shows, and "the shop opened with minus thirty units" is not a
     fact about a shop. `test_seeding_a_negative_residual_would_make_the_gate
     _incapable_of_failing` forces that sign check off and requires the
     previously-refused install to sail through.

  3. AN INSTALL MUST ALWAYS HAVE A SUPPORTED WAY FORWARD. `compute_drift`
     reports `repairable: False` for the two STRUCTURAL impossibilities -- a
     NULL branch (`inventory_balances.branch_id` is NOT NULL, so no balance
     row could hold it) and a missing product row
     (`inventory_movements.product_id` has an enforced FK, so no movement row
     can be written for it). A gate demanding GLOBAL zero drift freezes such
     an install's `user_version` forever, not just for v15 but for every
     migration after it. The orphan balance reached that wedge from a third
     direction and was measured doing it: three full launch -> repair ->
     launch cycles left `user_version` at 14, 14, 14, with the seeder, the
     gate and the repair each refusing the same row.

  4. "IT ADVANCED" IS NOT "IT ADVANCED CORRECTLY". The ambiguous NULL-branch
     case asserts the version moved AND that the ambiguous rows were left
     unassigned rather than guessed onto a branch, because a guess would also
     have advanced.

EVERY GUARD IS MUTATION-PROVED. A guard nobody has watched fail is a guard
nobody knows the shape of, so each one has a control test that restores the
broken behaviour and requires the disaster to reappear -- the wipe, the
wedge, the double-count, the softened gate. Two of those controls assert the
catastrophe itself, which reads badly on purpose: if they ever stop
reproducing, the guard beside them is no longer what is holding the stock in
place and the test beside it has quietly become a test of nothing.

Every scenario also asserts its fixture HAS drift before the migration is
allowed to run; `test_the_fixture_really_is_drifted_before_the_migration_
runs` states that guard on its own.

Self-contained bootstrap, matching every other file in this directory (no
shared conftest.py exists here): own temp app-data dir, real `init_retail()`.
Run:

    pytest products/retail/tests/retail_v15_ledger_truth_migration_test.py -v
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
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


# ── fixture bedrock ─────────────────────────────────────────────────────────

def _fresh_app_data(prefix):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    os.environ['AURA_APP_DATA'] = tmp
    return tmp


def _install():
    """A REAL install: fresh temp AURA_APP_DATA, the genuine `init_retail()`
    boot path, the full v0 -> current migration chain, real demo seed data.

    Deliberately not a hand-built minimal schema. The gate reads
    `inventory_balances`, `inventory_movements`, `branches`, `products` and
    `audit_log` and runs after fourteen other migration steps; a fixture that
    supplied only the two tables under test would prove the step works
    somewhere this code never runs.
    """
    tmp = _fresh_app_data('aura-retail-v15-')

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    return sch, os.path.join(sch.SUBSYS_DIR, 'retail.db')


def _open(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _new_company(conn, branch_count=1):
    """A second tenant in the same database, with its own branches.

    A locally-derived md5-shaped `company_id`, which is what onboarding
    actually produces (commercial_runtime/identity/onboarding_routes.py::
    create_admin derives md5(admin_email)) -- so the scoping this migration
    does per company is exercised against a realistic key rather than the
    integer 1 the demo seed uses.
    """
    company_id = uuid.uuid4().hex
    branch_ids = []
    for i in range(branch_count):
        cur = conn.execute(
            'INSERT INTO branches (company_id,name) VALUES (?,?)',
            (company_id, f'Branch {i + 1}'))
        branch_ids.append(cur.lastrowid)
    return company_id, branch_ids


def _product(conn, company_id, sku):
    product_id = str(uuid.uuid4())
    conn.execute(
        'INSERT INTO products (id,company_id,sku,name,cost_price,sell_price) '
        'VALUES (?,?,?,?,?,?)',
        (product_id, company_id, sku, f'Product {sku}', 4.0, 10.0))
    return product_id


def _balance(conn, company_id, product_id, branch_id, quantity):
    conn.execute(
        'INSERT INTO inventory_balances (company_id,product_id,branch_id,quantity_on_hand) '
        'VALUES (?,?,?,?)',
        (company_id, product_id, branch_id, quantity))


def _movement(conn, company_id, product_id, branch_id, quantity,
              movement_type='sale_out'):
    conn.execute(
        'INSERT INTO inventory_movements '
        '(company_id,product_id,branch_id,movement_type,quantity,reference,created_by) '
        'VALUES (?,?,?,?,?,?,?)',
        (company_id, product_id, branch_id, movement_type, quantity, 'FIXTURE', 'System'))


def _rewind_to_v14(db_path):
    """Put the version marker back so the next boot re-runs the whole chain.

    The scenario rows are inserted into an already-migrated database (that is
    what a real install upgrading LOOKS like -- its data predates v15 by
    definition), so the marker is what decides whether v15 gets to see them.
    """
    conn = _open(db_path)
    try:
        conn.execute('PRAGMA user_version = 14')
        conn.commit()
    finally:
        conn.close()


def _user_version(db_path):
    conn = _open(db_path)
    try:
        return conn.execute('PRAGMA user_version').fetchone()[0]
    finally:
        conn.close()


def _drift(db_path, company_id):
    from core.retail.stock_reconciliation import compute_drift
    conn = _open(db_path)
    try:
        return compute_drift(conn, company_id)
    finally:
        conn.close()


def _balances_snapshot(db_path, company_id):
    conn = _open(db_path)
    try:
        return {
            (r['product_id'], r['branch_id']): r['quantity_on_hand']
            for r in conn.execute(
                'SELECT product_id,branch_id,quantity_on_hand FROM inventory_balances '
                'WHERE company_id=?', (company_id,)).fetchall()
        }
    finally:
        conn.close()


def _movements_of(db_path, company_id, product_id=None):
    conn = _open(db_path)
    try:
        if product_id is None:
            rows = conn.execute(
                'SELECT * FROM inventory_movements WHERE company_id=? ORDER BY id',
                (company_id,)).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM inventory_movements WHERE company_id=? AND product_id=? ORDER BY id',
                (company_id, product_id)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _v15_audit(db_path, company_id):
    conn = _open(db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log WHERE company_id=? AND action='STOCK_LEDGER_SEEDED_V15' "
            "ORDER BY id", (company_id,)).fetchall()]
    finally:
        conn.close()


# ── 0. the anti-vacuity guard, stated on its own ────────────────────────────

def test_the_fixture_really_is_drifted_before_the_migration_runs():
    """A shop whose books already balance proves nothing about a gate.

    This is the guard every other test in this file repeats inline. It is
    stated once on its own so that if the fixture ever stops producing drift
    -- a changed seed, a writer that starts backfilling movements, a
    `compute_drift` that stops looking at the balances side -- the failure
    names that cause directly instead of turning every scenario below into a
    test of nothing.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        product_id = _product(conn, company_id, 'SKU-DRIFT')
        _balance(conn, company_id, product_id, branch_id, 42)
        conn.commit()
    finally:
        conn.close()

    before = _drift(db_path, company_id)
    assert [(r['sku'], r['drift']) for r in before] == [('SKU-DRIFT', 42.0)], before
    assert before[0]['ledger_balance'] == 0.0, (
        'the ledger already explains this balance -- the fixture is not the '
        'unbacked-balance case it claims to be')
    assert sch.RETAIL_SCHEMA_VERSION == 15


# ── 1. the ordinary case: balances with no ledger history ───────────────────

def test_a_balance_with_no_movements_is_seeded_and_drift_becomes_zero():
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        stocked = _product(conn, company_id, 'SKU-STOCKED')
        emptied = _product(conn, company_id, 'SKU-EMPTY')
        _balance(conn, company_id, stocked, branch_id, 120)
        _balance(conn, company_id, emptied, branch_id, 0)
        conn.commit()
    finally:
        conn.close()

    assert _drift(db_path, company_id), 'fixture has no drift -- see test 0'
    balances_before = _balances_snapshot(db_path, company_id)
    _rewind_to_v14(db_path)

    sch.init_retail()

    assert _user_version(db_path) == 15
    assert _drift(db_path, company_id) == []

    # The ledger moved to explain the cache. The cache did NOT move: v15 is a
    # migration, not a repair, and overwriting a balance would destroy the
    # evidence that one of the five writers is broken.
    assert _balances_snapshot(db_path, company_id) == balances_before

    seeded = {m['product_id']: m for m in _movements_of(db_path, company_id)}
    assert set(seeded) == {stocked, emptied}
    assert seeded[stocked]['quantity'] == 120.0
    assert seeded[stocked]['movement_type'] == sch.V15_OPENING_COUNT_TYPE
    # A zero balance with nothing behind it is "never counted", not "counted,
    # found nothing" -- after v15 that difference has to be visible in the
    # ledger rather than inferred from an absence, so it is seeded too.
    assert seeded[emptied]['quantity'] == 0.0
    assert seeded[emptied]['movement_type'] == sch.V15_OPENING_COUNT_TYPE


def test_the_seeded_movement_claims_nothing_it_cannot_prove():
    """No fabricated actor, no fabricated terminal, a real uid, a real
    creation instant, and a reason on the row saying what it is.

    v13 left `created_at_utc` NULL on every history row it touched because
    those rows already existed and their real instant was unknowable. These
    rows are being created right now, so their creation time is a fact about
    them rather than a guess about the past -- which is exactly why the actor
    columns stay NULL while this one does not. Nobody counted this stock.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        product_id = _product(conn, company_id, 'SKU-ACTOR')
        _balance(conn, company_id, product_id, branch_id, 7.5)
        conn.commit()
    finally:
        conn.close()

    assert _drift(db_path, company_id), 'fixture has no drift -- see test 0'
    _rewind_to_v14(db_path)
    sch.init_retail()

    rows = _movements_of(db_path, company_id, product_id)
    assert len(rows) == 1, rows
    row = rows[0]

    assert row['actor_user_uid'] is None, (
        'the migration stamped a user on stock nobody counted -- the same '
        'fabrication v13 refused when it left created_at_utc NULL on history')
    assert row['terminal_id'] is None, (
        'this device cannot prove which terminal would have counted stock '
        'that predates the ledger')
    assert row['created_by'] == 'System', (
        "created_by must stay the schema's existing non-person sentinel; a "
        "real name here would be a claim about a person who did nothing")

    # ...and the things it CAN prove, it does.
    assert row['quantity'] == 7.5
    assert row['movement_type'] == sch.V15_OPENING_COUNT_TYPE == 'opening_count'
    assert row['reference'] == sch.V15_OPENING_COUNT_REFERENCE
    assert 'inferred' in (row['notes'] or '').lower(), row['notes']
    assert 'nobody counted' in (row['notes'] or '').lower(), (
        'the row must say on itself that the number was inferred, or the next '
        'reader concludes somebody counted this stock')

    stamped = datetime.fromisoformat(row['created_at_utc'])
    assert stamped.utcoffset() is not None and stamped.utcoffset().total_seconds() == 0, (
        f"created_at_utc must be an offset-aware UTC instant, got {row['created_at_utc']!r}")

    # A real RFC-4122 uuid4, not `lower(hex(randomblob(16)))`: Owner's sync
    # ingest gates on uuid.UUID(entity_id), and a value that merely looks
    # random is rejected on the wire, where it is most expensive to find.
    assert uuid.UUID(row['uid']).version == 4


def test_the_type_is_not_the_one_the_importer_treats_as_a_declaration():
    """`opening_count` and `opening_stock` must stay different types.

    api/import_api.py sums `movement_type='opening_stock'` (plus its own
    import reference) to work out how much of a product's stock has already
    been DECLARED, so it can post a delta rather than re-adding the whole
    figure on every re-import. These rows were declared by nobody -- the
    migration inferred them from the cached balance. Filing an inference
    under the type reserved for a human declaration would make the importer
    treat a guess as evidence.
    """
    sch, _db_path = _install()
    assert sch.V15_OPENING_COUNT_TYPE != 'opening_stock'
    source = (BACKEND_DIR / 'api' / 'import_api.py').read_text(encoding='utf-8')
    assert "movement_type='opening_stock'" in source, (
        'the importer no longer sums opening_stock -- re-check whether the '
        'two types still need to differ before deleting this guard')
    assert f"movement_type='{sch.V15_OPENING_COUNT_TYPE}'" not in source


# ── 1b. THE ORDINARY LEGACY SHOP -- stock, sales, and no opening rows ───────
#
# This is the shape the overwhelming majority of real installs actually have,
# and until the residual fix it was the shape v15 REFUSED. The importer used
# to write an absolute `SET quantity_on_hand=?` with no movement row (git
# 13c1c92, api/import_api.py:1146) and every sale since wrote one, so the
# common legacy key has sales and no opening. The old seeder covered only
# keys with NO movements at all, skipped every one of these, and the gate
# refused -- which, because app.py calls init_retail() unconditionally with
# no handler, meant the POS would not start. The refusal then named
# `repair_drift` as the recovery, `_has_ledger_history` was True because of
# those sales, and the repair wrote the sales-only sum as the balance.
#
# Measured end to end, not theorised:
#     shelves [112, 57, 7]  ->  REFUSED (seeded 0)
#     -> repair_drift reports "repaired 3, skipped 0"
#     -> shelves [-8, -3, -1]
#     -> relaunch advances to v15 reporting zero drift
#
# Every test in this section asserts EXACT stock figures before and after,
# because "it advanced" and "it advanced without moving a single unit of
# anybody's stock" are different claims and only the second one is the
# promise v15 makes.

_LEGACY_SHELVES = ((112, -8), (57, -3), (7, -1))


def _legacy_shop(sch, db_path, shelves=_LEGACY_SHELVES):
    """A pre-v15 shop: real stock on the shelf, real sales in the ledger, and
    no opening row anywhere -- because the stock was there before the
    movement table was ever written to.

    Returns (company_id, branch_id, {product_id: (on_hand, sold)}).
    """
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        keys = {}
        for index, (on_hand, sold) in enumerate(shelves):
            product_id = _product(conn, company_id, f'SKU-LEGACY-{index}')
            _balance(conn, company_id, product_id, branch_id, on_hand)
            _movement(conn, company_id, product_id, branch_id, sold, 'sale_out')
            keys[product_id] = (on_hand, sold)
        conn.commit()
    finally:
        conn.close()
    return company_id, branch_id, keys


def _shelf_figures(db_path, company_id):
    return sorted(_balances_snapshot(db_path, company_id).values(), reverse=True)


def test_the_ordinary_legacy_shop_advances_and_every_shelf_figure_is_unchanged():
    """THE defect this whole section exists for.

    A shop with stock and sales and no opening rows must ADVANCE, and it must
    come out holding exactly what it went in holding.
    """
    sch, db_path = _install()
    company_id, branch_id, keys = _legacy_shop(sch, db_path)

    # Anti-vacuity: the fixture really is drifted, by the full shelf figure.
    before = _drift(db_path, company_id)
    assert sorted(round(r['drift'], 4) for r in before) == [8.0, 60.0, 120.0], before
    assert all(r['repairable'] for r in before), (
        'these keys are ordinary repairable drift; if they were not, this '
        'fixture would be about the wedge cases instead')
    shelves_before = _shelf_figures(db_path, company_id)
    assert shelves_before == [112.0, 57.0, 7.0]

    _rewind_to_v14(db_path)
    sch.init_retail()

    assert _user_version(db_path) == 15, (
        'the ordinary legacy shop was refused; the POS does not boot, because '
        'app.py calls init_retail() unconditionally with no handler')
    assert _drift(db_path, company_id) == []
    assert _shelf_figures(db_path, company_id) == [112.0, 57.0, 7.0], (
        'v15 moved stock. It is a migration, not a repair: the LEDGER moves to '
        'explain the cache, never the other way round')

    # And the inferred opening is the residual -- what must have been there
    # before the sales for the shelf to read what it reads.
    for product_id, (on_hand, sold) in keys.items():
        opening = [m for m in _movements_of(db_path, company_id, product_id)
                   if m['movement_type'] == sch.V15_OPENING_COUNT_TYPE]
        assert [m['quantity'] for m in opening] == [float(on_hand - sold)], (
            f'{product_id}: expected an opening count of {on_hand - sold} '
            f'({on_hand} on hand + {abs(sold)} sold since), got {opening}')


def test_the_old_zero_movement_seeder_really_did_refuse_that_shop():
    """The control for the test above, without which it proves nothing.

    A re-creation of `_v15_seed_opening_counts`' pre-fix body: seed only keys
    with NO movements at all. If the identical fixture advances under it, the
    residual is not what fixed anything and the test above is a test of
    nothing.
    """
    sch, db_path = _install()
    company_id, _branch_id, _keys = _legacy_shop(sch, db_path)
    shelves_before = _shelf_figures(db_path, company_id)

    real_seeder = sch._v15_seed_opening_counts

    def zero_movement_seeder(conn, cid, movement_columns, drifted):
        unbacked = conn.execute(
            "SELECT b.product_id, b.branch_id, b.quantity_on_hand "
            "FROM inventory_balances b WHERE b.company_id=? "
            "  AND NOT EXISTS (SELECT 1 FROM inventory_movements m "
            "      WHERE m.company_id=b.company_id AND m.product_id=b.product_id "
            "        AND m.branch_id IS b.branch_id) "
            "  AND EXISTS (SELECT 1 FROM products p WHERE p.id=b.product_id)",
            (cid,)).fetchall()
        return real_seeder(conn, cid, movement_columns, [
            {'product_id': r[0], 'branch_id': r[1], 'drift': float(r[2] or 0),
             'repairable': True} for r in unbacked])

    _rewind_to_v14(db_path)
    sch._v15_seed_opening_counts = zero_movement_seeder
    try:
        with pytest.raises(sch.RetailLedgerDriftError) as excinfo:
            sch.init_retail()
    finally:
        sch._v15_seed_opening_counts = real_seeder

    assert _user_version(db_path) == 14
    assert 'SKU-LEGACY-0' in str(excinfo.value)
    # The old seeder seeded literally nothing here -- every key had a sale.
    assert not [m for m in _movements_of(db_path, company_id)
                if m['movement_type'] == sch.V15_OPENING_COUNT_TYPE], (
        'the pre-fix seeder covered these keys after all, so the residual '
        'changed nothing and the test above is vacuous')
    assert _shelf_figures(db_path, company_id) == shelves_before


def test_repair_drift_on_that_shop_before_v15_touches_no_stock_at_all():
    """THE WIPE PATH, RUN DELIBERATELY. It must be dead.

    This is the exact sequence a real operator followed: the app would not
    start, the refusal named `repair_drift`, they ran it. Every key here has
    ledger history, so `SKIP_NO_LEDGER_HISTORY` does not fire -- the guard
    that has to hold is `SKIP_LEDGER_NOT_ESTABLISHED`, and what it refuses is
    every repair that would LOWER a balance on a database whose ledger has
    not yet been made able to reproduce its cache.
    """
    from core.retail.stock_reconciliation import (
        SKIP_LEDGER_NOT_ESTABLISHED, repair_drift)

    sch, db_path = _install()
    company_id, _branch_id, _keys = _legacy_shop(sch, db_path)
    _rewind_to_v14(db_path)
    assert _user_version(db_path) == 14
    assert _drift(db_path, company_id), 'fixture has no drift -- see test 0'

    conn = sch.get_retail_conn()
    try:
        result = repair_drift(conn, company_id)
    finally:
        conn.close()

    assert result['repaired'] == [], (
        f"repair_drift rewrote stock on a pre-v15 database: {result['repaired']}")
    assert sorted(r['skip_reason'] for r in result['skipped']) == (
        [SKIP_LEDGER_NOT_ESTABLISHED] * 3)
    assert _shelf_figures(db_path, company_id) == [112.0, 57.0, 7.0], (
        "the documented recovery wiped the shop's stock -- the measured "
        "[112, 57, 7] -> [-8, -3, -1]")


def test_repair_drift_after_v15_still_leaves_that_shop_untouched():
    """The other half of the same promise: once the migration has passed,
    running the repair anyway must be a no-op, because there is nothing left
    to repair. The seeded opening counts made the cache derivable, not
    disposable.
    """
    from core.retail.stock_reconciliation import repair_drift

    sch, db_path = _install()
    company_id, _branch_id, _keys = _legacy_shop(sch, db_path)
    _rewind_to_v14(db_path)
    sch.init_retail()
    assert _user_version(db_path) == 15

    conn = sch.get_retail_conn()
    try:
        result = repair_drift(conn, company_id)
    finally:
        conn.close()

    assert result == {'repaired': [], 'skipped': []}
    assert _shelf_figures(db_path, company_id) == [112.0, 57.0, 7.0]


def test_without_the_not_established_guard_that_same_repair_destroys_the_stock():
    """The control for the wipe test, and the ugliest assertion in this file:
    it asserts the disaster.

    With `_ledger_is_established` forced True -- i.e. the code as it stood --
    the identical call on the identical fixture turns [112, 57, 7] into
    [-8, -3, -1] and reports "repaired 3, skipped 0" while doing it. If this
    ever stops reproducing, the guard above is no longer what is holding the
    stock in place and the test above needs a new reason to exist.
    """
    import core.retail.stock_reconciliation as recon

    sch, db_path = _install()
    company_id, _branch_id, _keys = _legacy_shop(sch, db_path)
    _rewind_to_v14(db_path)

    real_check = recon._ledger_is_established
    recon._ledger_is_established = lambda conn: True
    try:
        conn = sch.get_retail_conn()
        try:
            result = recon.repair_drift(conn, company_id)
        finally:
            conn.close()
    finally:
        recon._ledger_is_established = real_check

    assert (len(result['repaired']), len(result['skipped'])) == (3, 0), (
        'the unguarded repair did NOT touch these rows, so the guard is not '
        'what protects them and the wipe test above is vacuous')
    assert _shelf_figures(db_path, company_id) == [-1.0, -3.0, -8.0], (
        'the unguarded repair did not produce the measured wipe, so this '
        'control no longer demonstrates the failure it exists to demonstrate')


# ── 2. the wedge case: NULL-branch movements ────────────────────────────────

def test_an_ambiguous_null_branch_install_still_advances_with_the_count_surfaced():
    """THE most important test in this file.

    A legacy movement with `branch_id IS NULL` can never be reconciled:
    `inventory_balances.branch_id` is NOT NULL, so no balance row could ever
    hold it, and `compute_drift` reports it `repairable: False` forever. With
    two branches there is no unambiguous answer about where that stock was.

    If the gate demanded global zero drift, this install could never advance
    its schema version AGAIN -- not for v15, not for anything after it. That
    is the permanent wedge the v13 duplicate-uid defect produced, reached
    from another direction. So it advances, and it advances carrying a
    counted, recorded "needs assignment" queue rather than a guessed branch:
    guessing puts real stock in the wrong shop, and a wrong number looks
    right.
    """
    from core.retail.stock_reconciliation import unassigned_movements

    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, branch_ids = _new_company(conn, branch_count=2)
        homeless = _product(conn, company_id, 'SKU-HOMELESS')
        placed = _product(conn, company_id, 'SKU-PLACED')
        _movement(conn, company_id, homeless, None, 9, 'purchase_in')
        _balance(conn, company_id, placed, branch_ids[0], 30)
        conn.commit()
    finally:
        conn.close()

    before = _drift(db_path, company_id)
    assert before, 'fixture has no drift -- see test 0'
    assert any(not r['repairable'] for r in before), (
        'no unrepairable row in the fixture -- this test would then be about '
        'the ordinary case and would pass under a global-zero gate too')
    _rewind_to_v14(db_path)

    sch.init_retail()

    assert _user_version(db_path) == 15, (
        'an install carrying ONE ambiguous legacy row can now never take '
        'another migration -- the permanent wedge this gate shape exists to '
        'avoid')

    after = _drift(db_path, company_id)
    assert [r['repairable'] for r in after] == [False], after
    assert after[0]['product_id'] == homeless

    # The ambiguous row was left where it was, not placed on a branch.
    conn = _open(db_path)
    try:
        still_null = conn.execute(
            'SELECT COUNT(*) FROM inventory_movements '
            'WHERE company_id=? AND branch_id IS NULL', (company_id,)).fetchone()[0]
        queue = unassigned_movements(conn, company_id)
    finally:
        conn.close()
    assert still_null == 1, 'the migration guessed a branch for an ambiguous row'
    assert [(q['sku'], q['quantity']) for q in queue] == [('SKU-HOMELESS', 9.0)]

    # ...and the count is recorded, because "the gate passed" and "the gate
    # passed while excluding one row it could not place" are different facts.
    audit = _v15_audit(db_path, company_id)
    assert len(audit) == 1, audit
    assert audit[0]['user_id'] is None, (
        'no person seeded these counts or declined to assign that branch')
    details = json.loads(audit[0]['details'])
    assert details['unassigned'] == 1, details
    assert details['branches_resolved'] == 0, details
    assert details['seeded'] == 1, details


def test_a_single_branch_company_has_its_null_branch_movements_resolved():
    """One branch is one place the stock can be, which is not a guess.

    Also pins the ORDER of the two steps. Resolving the NULL branch gives
    (product, branch) real ledger history; seeding first would already have
    written an opening count for that key on the grounds that it had none,
    and the two would sum to double the stock and fail the gate.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn, branch_count=1)
        product_id = _product(conn, company_id, 'SKU-ONEBRANCH')
        _movement(conn, company_id, product_id, None, 25, 'purchase_in')
        _balance(conn, company_id, product_id, branch_id, 25)
        conn.commit()
    finally:
        conn.close()

    before = _drift(db_path, company_id)
    assert len(before) == 2, before   # the orphan movement AND the unbacked balance
    assert sorted(r['repairable'] for r in before) == [False, True]
    _rewind_to_v14(db_path)

    sch.init_retail()

    assert _user_version(db_path) == 15
    assert _drift(db_path, company_id) == []

    rows = _movements_of(db_path, company_id, product_id)
    assert len(rows) == 1, (
        'the balance was given an opening count on top of the movement that '
        'was just resolved onto its branch -- the two steps ran in the wrong '
        f'order and the stock is now double-counted: {rows}')
    assert rows[0]['branch_id'] == branch_id
    assert rows[0]['movement_type'] == 'purchase_in', (
        'the legacy row was rewritten rather than assigned a branch')


def test_a_single_branch_shop_with_null_branch_sales_advances_with_stock_intact():
    """The realistic version of the test above: a shop that has SOLD things.

    The pre-branch history is a NULL-branch sale, the shelf holds 120, and
    there is one branch. Both of the migration's writes are exercised at once
    and their order is the only thing that makes the answer right: the sale
    is resolved onto the one branch it can have happened at, and only THEN is
    the opening count computed against it (128 = 120 on hand + 8 sold).

    Before the residual fix this fixture was the third measured disaster --
    the resolve gave the key ledger history, the zero-movement seeder
    therefore skipped it, the gate refused, and the documented recovery took
    120 to -8.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn, branch_count=1)
        product_id = _product(conn, company_id, 'SKU-NULLBRANCH-SOLD')
        _balance(conn, company_id, product_id, branch_id, 120)
        _movement(conn, company_id, product_id, None, -8, 'sale_out')
        conn.commit()
    finally:
        conn.close()

    before = _drift(db_path, company_id)
    assert sorted(round(r['drift'], 4) for r in before) == [8.0, 120.0], before
    assert any(not r['repairable'] for r in before), (
        'the NULL-branch row is not being reported unrepairable, so this '
        'fixture is not the case it claims to be')
    _rewind_to_v14(db_path)

    sch.init_retail()

    assert _user_version(db_path) == 15
    assert _drift(db_path, company_id) == []
    assert _balances_snapshot(db_path, company_id) == {(product_id, branch_id): 120.0}, (
        'the shop went in holding 120 and did not come out holding 120')

    rows = _movements_of(db_path, company_id, product_id)
    assert {(r['movement_type'], r['quantity'], r['branch_id']) for r in rows} == {
        ('sale_out', -8.0, branch_id),
        (sch.V15_OPENING_COUNT_TYPE, 128.0, branch_id),
    }, rows


def test_seeding_before_resolving_the_branch_would_double_count_the_sale():
    """The control that pins the ORDER of the two writes.

    Running the seed first computes the opening count without the sale that
    is about to land on the key, and the two then disagree by exactly what
    was sold. If this ever advances, the ordering in
    `_migrate_seed_opening_counts_and_gate_drift` is no longer load-bearing
    and the comment saying it is has gone stale.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn, branch_count=1)
        product_id = _product(conn, company_id, 'SKU-ORDER')
        _balance(conn, company_id, product_id, branch_id, 120)
        _movement(conn, company_id, product_id, None, -8, 'sale_out')
        conn.commit()
    finally:
        conn.close()
    _rewind_to_v14(db_path)

    real_resolve = sch._v15_resolve_unambiguous_null_branches
    real_seed = sch._v15_seed_opening_counts
    live_tables_seen = {}

    def deferred_resolve(conn, cid, live_tables):
        # Step 2 does nothing on the way in; it is performed after the
        # seeding instead. This is literally steps 2 and 4 swapped.
        live_tables_seen[cid] = live_tables
        return 0

    def seed_then_resolve(conn, cid, movement_columns, drifted):
        seeded = real_seed(conn, cid, movement_columns, drifted)
        real_resolve(conn, cid, live_tables_seen[cid])
        return seeded

    sch._v15_resolve_unambiguous_null_branches = deferred_resolve
    sch._v15_seed_opening_counts = seed_then_resolve
    try:
        with pytest.raises(sch.RetailLedgerDriftError) as excinfo:
            sch.init_retail()
    finally:
        sch._v15_resolve_unambiguous_null_branches = real_resolve
        sch._v15_seed_opening_counts = real_seed

    assert live_tables_seen, 'the resolve step was never reached'
    assert _user_version(db_path) == 14, (
        'seeding before resolving advanced anyway, so the documented ordering '
        'is no longer load-bearing and the comment saying it is has gone stale')
    seeded = [m['quantity'] for m in _movements_of(db_path, company_id, product_id)
              if m['movement_type'] == sch.V15_OPENING_COUNT_TYPE]
    assert seeded == [120.0], (
        f'expected the wrong-order seeder to infer 120 rather than 128: {seeded}')
    # 120 seeded + (-8) resolved onto the same key = 112 in the ledger against
    # a shelf that says 120: short by exactly what was sold.
    assert [round(r['drift'], 4) for r in _drift(db_path, company_id)] == [8.0]
    assert 'SKU-ORDER' in str(excinfo.value)


# ── 2b. the wedge case, third direction: an orphan balance ──────────────────

def _orphan_install():
    """A balance whose product row is GONE.

    `inventory_movements.product_id` has a FOREIGN KEY to products(id) and
    `_conn()` turns enforcement ON, so no movement can ever be written for
    this key: v15 cannot seed it an opening count and `repair_drift` cannot
    rewrite it from a ledger that can never exist. No current code path
    produces such a row -- `delete_product` is a soft `status='inactive'` and
    `demo_wipe` clears balances before products -- but FK enforcement was OFF
    everywhere before Wave 0/AUDIT-016, and `_migrate_products_to_uuid`
    remaps only child ids present in its `id_map`, so the PRECEDING migration
    preserves orphans by construction.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        orphan = _product(conn, company_id, 'SKU-GONE')
        alive = _product(conn, company_id, 'SKU-ALIVE')
        _balance(conn, company_id, orphan, branch_id, 40)
        _balance(conn, company_id, alive, branch_id, 25)
        _movement(conn, company_id, alive, branch_id, -5, 'sale_out')
        conn.commit()
        # FK enforcement off for exactly this one statement, because the
        # point is to build the row a pre-AUDIT-016 database already has.
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.execute('DELETE FROM products WHERE id=?', (orphan,))
        conn.commit()
    finally:
        conn.close()
    return sch, db_path, company_id, branch_id, orphan, alive


def test_an_orphan_balance_lets_the_install_advance_instead_of_wedging_it():
    """AN INSTALL MUST ALWAYS HAVE A SUPPORTED WAY FORWARD.

    Measured before the fix, over three full launch -> repair -> launch
    cycles: `user_version` stayed 14, 14, 14. The seeder skipped the row
    (FK), the gate refused over it, and `repair_drift` refused it too. Not a
    slow recovery -- no recovery. That is the v13 duplicate-uid wedge reached
    from a third direction, and it is the exact failure mode this phase's
    design document was written to avoid.
    """
    from core.retail.stock_reconciliation import SKIP_NO_PRODUCT, orphan_balances

    sch, db_path, company_id, branch_id, orphan, alive = _orphan_install()

    before = _drift(db_path, company_id)
    assert sorted(round(r['drift'], 4) for r in before) == [30.0, 40.0], before
    _rewind_to_v14(db_path)

    sch.init_retail()

    assert _user_version(db_path) == 15, (
        'an install carrying one orphan balance can now never take another '
        'migration -- and no operator action changes that')

    # The honest half of the work still landed: the live key was seeded its
    # residual and no longer drifts.
    after = _drift(db_path, company_id)
    assert [(r['product_id'], r['blocked_reason']) for r in after] == [
        (orphan, SKIP_NO_PRODUCT)], after
    assert after[0]['repairable'] is False
    assert after[0]['sku'] is None, (
        'the sku reads as a name precisely BECAUSE the product is gone; if it '
        'resolves, this fixture is not an orphan')

    # Nothing moved on either shelf.
    assert _balances_snapshot(db_path, company_id) == {
        (orphan, branch_id): 40.0, (alive, branch_id): 25.0}

    # ...and the row is SURFACED rather than silently dropped, because "the
    # gate passed" and "the gate passed while excluding a balance whose
    # product no longer exists" are different facts.
    conn = _open(db_path)
    try:
        queue = orphan_balances(conn, company_id)
    finally:
        conn.close()
    assert queue == [{'product_id': orphan, 'branch_id': branch_id,
                      'branch_name': 'Branch 1', 'quantity_on_hand': 40.0}], queue

    details = json.loads(_v15_audit(db_path, company_id)[0]['details'])
    assert details['orphaned'] == 1, details
    assert details['net_drift_orphaned'] == 40.0, details
    assert details['seeded'] == 1, details


def test_repair_drift_leaves_an_orphan_balance_alone_and_says_why():
    """The repair cannot fix it either -- there is no ledger to rewrite it
    from -- so it refuses by NAME rather than falling through to a generic
    reason, and it does not touch the stock while doing so.
    """
    from core.retail.stock_reconciliation import SKIP_NO_PRODUCT, repair_drift

    sch, db_path, company_id, branch_id, orphan, _alive = _orphan_install()
    _rewind_to_v14(db_path)
    sch.init_retail()

    conn = sch.get_retail_conn()
    try:
        result = repair_drift(conn, company_id)
    finally:
        conn.close()

    assert result['repaired'] == []
    assert [(r['product_id'], r['skip_reason']) for r in result['skipped']] == [
        (orphan, SKIP_NO_PRODUCT)]
    assert _balances_snapshot(db_path, company_id)[(orphan, branch_id)] == 40.0


def test_calling_that_row_repairable_really_does_wedge_the_install_forever():
    """The control for the two tests above, and the one that reproduces the
    measured 14, 14, 14.

    `repairable` began life as `branch_id is not None`. Restoring exactly
    that predicate must put the install back in the wedge; if it does not,
    the `no_product` classification is not what is holding the door open.
    """
    import core.retail.stock_reconciliation as recon

    sch, db_path, company_id, _branch_id, _orphan, _alive = _orphan_install()

    real_compute = recon.compute_drift

    def branch_only_repairable(conn, cid, tolerance=recon.DEFAULT_TOLERANCE):
        return [dict(row,
                     repairable=row['branch_id'] is not None,
                     blocked_reason=(None if row['branch_id'] is not None
                                     else recon.SKIP_NO_BRANCH))
                for row in real_compute(conn, cid, tolerance)]

    versions = []
    recon.compute_drift = branch_only_repairable
    try:
        for _cycle in range(3):
            _rewind_to_v14(db_path)
            with pytest.raises(sch.RetailLedgerDriftError):
                sch.init_retail()
            versions.append(_user_version(db_path))
            conn = sch.get_retail_conn()
            try:
                result = recon.repair_drift(conn, company_id)
            finally:
                conn.close()
            assert result['repaired'] == [], (
                'the repair moved the orphan balance after all, so there was '
                'an exit and this control does not demonstrate a wedge')
    finally:
        recon.compute_drift = real_compute

    assert versions == [14, 14, 14], (
        'the pre-fix predicate did NOT wedge the install, so the no_product '
        'classification is not what fixed it and the tests above are vacuous')


# ── 3. the gate refuses ─────────────────────────────────────────────────────

def _undeducible_install():
    """A shop whose drift CANNOT be seeded away: THE LEDGER ACCOUNTS FOR MORE
    STOCK THAN THE SHELF CLAIMS.

    50 units are recorded as having arrived and the shelf says 20. The only
    opening count that closes that gap is MINUS THIRTY, and "the shop began
    with less than nothing" is not a fact about a shop -- it is shrinkage,
    loss, or a writer that decremented a balance without recording why. Every
    one of those is a human decision, so the migration refuses and says so.

    THIS FIXTURE USED TO BE THE OTHER DIRECTION -- balance 100, one sale of
    -5 -- and that was the defect wearing the costume of a test. A shop with
    100 on the shelf and one recorded sale of 5 is not undeducible at all: it
    plainly opened with 105, which is exactly what the residual says and
    exactly what v15 now seeds. Calling it "genuinely undeducible" was what
    made the gate look like it was working while it refused the single most
    common legacy install in the fleet.

    The `innocent` key beside it is that ordinary shape -- stock, sales, no
    opening row -- and it is what proves the refusal does not discard the
    honest half of its own work.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        broken = _product(conn, company_id, 'SKU-BROKEN')
        innocent = _product(conn, company_id, 'SKU-INNOCENT')
        _movement(conn, company_id, broken, branch_id, 50, 'purchase_in')
        _balance(conn, company_id, broken, branch_id, 20)
        _balance(conn, company_id, innocent, branch_id, 60)
        _movement(conn, company_id, innocent, branch_id, -4, 'sale_out')
        conn.commit()
    finally:
        conn.close()
    return sch, db_path, company_id, branch_id, broken, innocent


def test_a_genuinely_undeducible_install_fails_the_gate_and_does_not_advance():
    """The second anti-vacuity guard: the gate must be able to FAIL.

    A gate that has never refused anything is decoration, and this programme
    has shipped a guard whose pass condition was the bug signature before.
    """
    sch, db_path, company_id, _branch, _broken, _innocent = _undeducible_install()

    before = _drift(db_path, company_id)
    assert any(r['sku'] == 'SKU-BROKEN' for r in before), 'fixture is not drifted'
    _rewind_to_v14(db_path)

    with pytest.raises(sch.RetailLedgerDriftError) as excinfo:
        sch.init_retail()

    assert _user_version(db_path) == 14, (
        'v15 called this shop derivable and advanced the marker anyway')

    message = str(excinfo.value)
    assert 'SKU-BROKEN' in message, (
        f'the refusal does not name the offending row, so nobody can act on '
        f'it: {message}')
    assert 'repair_drift' in message, (
        'the refusal does not name a recovery, which is how a detected '
        'problem becomes a support ticket about a crash')
    assert 'SKU-INNOCENT' not in message, (
        'a balance the migration successfully seeded is being reported as an '
        'offender')


def test_seeding_a_negative_residual_would_make_the_gate_incapable_of_failing():
    """THE ANTI-VACUITY CONTROL FOR THE RESIDUAL ITSELF.

    Making the gate stop refusing the ordinary legacy shop is only half a
    fix; the other half is that it must still refuse. The line that keeps it
    real is `drift > DEFAULT_TOLERANCE` -- seed the residual only where it is
    POSITIVE. Drop the sign check and every drift on earth becomes
    "explainable", the gate can no longer fail on any input, and Phase 3 has
    shipped a decoration.

    This forces the sign check off and requires the previously-refused
    install to sail through, which is what proves the check is load-bearing
    rather than incidental.
    """
    import core.retail.stock_reconciliation as recon

    sch, db_path, company_id, _branch_id, broken, _innocent = _undeducible_install()

    # The seeder re-imports DEFAULT_TOLERANCE at CALL time, so lowering it
    # past every possible residual turns `drift > DEFAULT_TOLERANCE` into
    # `True` and nothing else changes -- the seeded value stays the residual,
    # sign and all. `compute_drift` and `repair_drift` bound the same name as
    # a DEFAULT ARGUMENT at def time, so the gate itself is untouched and
    # keeps measuring at the real tolerance. That is what makes this a
    # mutation of one guard rather than of the whole module.
    real_tolerance = recon.DEFAULT_TOLERANCE
    _rewind_to_v14(db_path)
    recon.DEFAULT_TOLERANCE = -1e9
    try:
        sch.init_retail()
    finally:
        recon.DEFAULT_TOLERANCE = real_tolerance

    assert _user_version(db_path) == 15, (
        'the undeducible install was refused even with the sign check off, so '
        'something else is doing the refusing and the real test below does '
        'not prove what it claims')
    # ...and look at what it advanced WITH: a goods ledger stating that this
    # shop opened with MINUS THIRTY units on the shelf. Nothing downstream
    # would ever question it -- valuation, COGS and reorder maths all read
    # this table as fact.
    fabricated = [m for m in _movements_of(db_path, company_id, broken)
                  if m['movement_type'] == sch.V15_OPENING_COUNT_TYPE]
    assert [m['quantity'] for m in fabricated] == [-30.0], fabricated
    assert _drift(db_path, company_id) == [], (
        'the softened gate did not even produce the clean bill of health it '
        'was softened to produce')


def test_no_seeded_opening_count_is_ever_negative():
    """Stated as a property over a deliberately mixed shop, because a single
    negative row in the goods ledger is a claim that a shelf once held less
    than nothing -- and unlike drift, nothing downstream would ever question
    it. Valuation, COGS and reorder maths all read this table as fact.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        for sku, on_hand, movements in (
                ('MIX-LEGACY', 112, [(-8, 'sale_out')]),
                ('MIX-CLEAN', 20, [(20, 'purchase_in')]),
                ('MIX-SHRUNK', 20, [(50, 'purchase_in')]),
                ('MIX-EMPTY', 0, []),
                ('MIX-UNBACKED', 9, []),
        ):
            product_id = _product(conn, company_id, sku)
            _balance(conn, company_id, product_id, branch_id, on_hand)
            for quantity, movement_type in movements:
                _movement(conn, company_id, product_id, branch_id, quantity, movement_type)
        conn.commit()
    finally:
        conn.close()

    _rewind_to_v14(db_path)
    with pytest.raises(sch.RetailLedgerDriftError):
        sch.init_retail()   # MIX-SHRUNK is undeducible and must still refuse

    seeded = [m for m in _movements_of(db_path, company_id)
              if m['movement_type'] == sch.V15_OPENING_COUNT_TYPE]
    assert seeded, 'nothing was seeded at all, so this asserts nothing'
    negative = [(m['product_id'], m['quantity']) for m in seeded if m['quantity'] < 0]
    assert negative == [], f'the migration wrote negative opening counts: {negative}'
    # 120 for the legacy key, 0 for the never-counted empty one, 9 for the
    # unbacked one; MIX-CLEAN already balances and MIX-SHRUNK is refused.
    assert sorted(m['quantity'] for m in seeded) == [0.0, 9.0, 120.0], seeded


def test_the_refused_migration_still_commits_the_opening_counts_it_seeded():
    """The refusal must not discard the honest half of its own work, and the
    reason is not tidiness.

    `ensure_schema_version` does not commit on the failure path. If v15's
    seeding went with it, the recovery this very failure tells the operator
    to run -- `repair_drift` -- would find every pre-existing balance still
    backed by NO ledger rows, compute its ledger total as ZERO, and write
    that into `quantity_on_hand`. The documented recovery would wipe the
    shop's stock. Seeding durably first is what makes it safe to point an
    operator at.
    """
    sch, db_path, company_id, _branch, broken, innocent = _undeducible_install()
    _rewind_to_v14(db_path)

    with pytest.raises(sch.RetailLedgerDriftError):
        sch.init_retail()

    seeded = [m for m in _movements_of(db_path, company_id, innocent)
              if m['movement_type'] == sch.V15_OPENING_COUNT_TYPE]
    assert len(seeded) == 1, (
        'the seeded opening count was rolled back with the refusal -- running '
        'the recovery now would zero this balance')
    assert seeded[0]['quantity'] == 64.0, (
        'the seeded opening count is not the RESIDUAL: this key holds 60 and '
        'has sold 4, so it must have opened with 64. Seeding 60 (or nothing) '
        'leaves the key drifted by exactly what it has sold since')
    # The broken key is untouched: no invented movement papered over it, and
    # in particular no NEGATIVE opening count was fabricated to close a gap
    # that only a person can explain.
    assert [m['movement_type'] for m in _movements_of(db_path, company_id, broken)] == ['purchase_in']


def test_the_documented_recovery_actually_recovers_and_costs_no_other_stock():
    """The whole loop: refuse -> owner repairs -> relaunch -> advance.

    A gate whose failure has no exit is a brick, and this one's exit is the
    explicit, owner-initiated repair. What it must NOT do on the way out is
    take any stock with it -- and note the DIRECTION here, which is the whole
    reason this exit survives `SKIP_LEDGER_NOT_ESTABLISHED`: every row the
    gate still refuses has a ledger accounting for MORE than the shelf, so
    the repair RAISES the balance. It cannot reach a row it would lower.
    """
    from core.retail.stock_reconciliation import repair_drift

    sch, db_path, company_id, _branch, broken, innocent = _undeducible_install()
    _rewind_to_v14(db_path)
    with pytest.raises(sch.RetailLedgerDriftError):
        sch.init_retail()
    assert _user_version(db_path) == 14

    conn = sch.get_retail_conn()
    try:
        result = repair_drift(conn, company_id)
    finally:
        conn.close()

    assert [r['sku'] for r in result['repaired']] == ['SKU-BROKEN'], result
    assert result['skipped'] == []

    balances = _balances_snapshot(db_path, company_id)
    assert balances[(broken, _branch)] == 50.0, (
        'the repair did not rewrite the drifted balance from its ledger')
    assert balances[(innocent, _branch)] == 60.0, (
        'the repair moved a balance that was never drifted -- the seeded '
        'opening count was supposed to make it derivable, not disposable')

    sch.init_retail()
    assert _user_version(db_path) == 15
    assert _drift(db_path, company_id) == []


def test_one_shops_refusal_does_not_hide_another_shops_or_stop_its_seeding():
    """One install CAN host more than one company (see CLAUDE.md), and the
    gate runs per company.

    Stopping at the first refusal would leave every later company's seeding
    undone and its drift undiscovered -- the operator would fix one shop,
    relaunch, and meet the next one, learning about them a relaunch at a
    time. Every company's honest work lands; the refusal names all of them.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        companies = []
        for tag in ('A', 'B'):
            company_id, (branch_id,) = _new_company(conn)
            broken = _product(conn, company_id, f'SKU-BROKEN-{tag}')
            innocent = _product(conn, company_id, f'SKU-INNOCENT-{tag}')
            _movement(conn, company_id, broken, branch_id, 50, 'purchase_in')
            _balance(conn, company_id, broken, branch_id, 20)
            _balance(conn, company_id, innocent, branch_id, 60)
            _movement(conn, company_id, innocent, branch_id, -4, 'sale_out')
            companies.append((company_id, innocent))
        conn.commit()
    finally:
        conn.close()

    for company_id, _innocent in companies:
        assert _drift(db_path, company_id), 'fixture has no drift -- see test 0'
    _rewind_to_v14(db_path)

    with pytest.raises(sch.RetailLedgerDriftError) as excinfo:
        sch.init_retail()

    message = str(excinfo.value)
    for tag in ('A', 'B'):
        assert f'SKU-BROKEN-{tag}' in message, (
            f'the refusal stopped at the first company, so shop {tag} is '
            f'invisible until the operator has fixed and relaunched: {message}')
    for company_id, innocent in companies:
        assert [m['quantity'] for m in _movements_of(db_path, company_id, innocent)
                if m['movement_type'] == sch.V15_OPENING_COUNT_TYPE] == [64.0], (
            'a later company never got its opening counts seeded')


# ── 4. idempotence ──────────────────────────────────────────────────────────

def test_a_second_run_seeds_nothing_and_changes_nothing():
    """`ensure_schema_version` re-runs the ENTIRE chain from the top whenever
    the marker is behind, so every step has to be a clean no-op on a database
    that already has its change. A second opening count per balance would
    double the stock in the ledger and fail the very gate that just passed.
    """
    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        product_id = _product(conn, company_id, 'SKU-TWICE')
        _balance(conn, company_id, product_id, branch_id, 15)
        conn.commit()
    finally:
        conn.close()

    assert _drift(db_path, company_id), 'fixture has no drift -- see test 0'
    _rewind_to_v14(db_path)
    sch.init_retail()

    first = _movements_of(db_path, company_id)
    balances = _balances_snapshot(db_path, company_id)
    assert len(first) == 1

    _rewind_to_v14(db_path)
    sch.init_retail()

    assert _movements_of(db_path, company_id) == first, (
        'the second run wrote to the ledger; a repeated opening count doubles '
        'the stock it was supposed to explain')
    assert _balances_snapshot(db_path, company_id) == balances
    assert _drift(db_path, company_id) == []
    assert _user_version(db_path) == 15


# ── 5. repair_drift's own transaction ───────────────────────────────────────

def _drifted_pair(sch, db_path):
    """Two products, each with real ledger history and a cache that disagrees
    -- the shape a broken writer leaves behind, and the shape a repair is
    allowed to fix."""
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        first = _product(conn, company_id, 'SKU-RACE-1')
        second = _product(conn, company_id, 'SKU-RACE-2')
        for product_id in (first, second):
            _movement(conn, company_id, product_id, branch_id, 20, 'purchase_in')
            _balance(conn, company_id, product_id, branch_id, 20)
        # Move the cache behind the API's back, exactly as the two broken
        # writers did, without depending on either still being broken.
        conn.execute(
            'UPDATE inventory_balances SET quantity_on_hand=quantity_on_hand+5 '
            'WHERE company_id=?', (company_id,))
        conn.commit()
    finally:
        conn.close()
    return company_id, branch_id, first, second


def _race(sch, db_path, company_id, branch_id, product_id, repair):
    """Drive `repair` while another connection tries to ring up a sale in the
    window between its recompute and its overwrite.

    The stall is injected by wrapping `compute_drift`, which is where the
    window opens: everything the repair writes is derived from the totals
    that call returned, so a sale committing afterwards is a sale the repair
    is about to erase.
    """
    import core.retail.stock_reconciliation as recon

    started, outcome = threading.Event(), {}

    def racer():
        started.wait(10)
        conn = sch.get_retail_conn()
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute(
                'INSERT INTO inventory_movements '
                '(company_id,product_id,branch_id,movement_type,quantity,created_by) '
                'VALUES (?,?,?,?,?,?)',
                (company_id, product_id, branch_id, 'sale_out', -1, 'System'))
            conn.execute(
                'UPDATE inventory_balances SET quantity_on_hand=quantity_on_hand-1 '
                'WHERE company_id=? AND product_id=? AND branch_id=?',
                (company_id, product_id, branch_id))
            conn.commit()
            outcome['sold'] = True
        except sqlite3.OperationalError as exc:
            outcome['sold'] = False
            outcome['error'] = str(exc)
        finally:
            conn.close()

    real_compute = recon.compute_drift

    def stalling_compute(conn, cid, tolerance=recon.DEFAULT_TOLERANCE):
        rows = real_compute(conn, cid, tolerance)
        if not started.is_set():
            started.set()
            time.sleep(0.5)
        return rows

    thread = threading.Thread(target=racer, daemon=True)
    thread.start()
    recon.compute_drift = stalling_compute
    try:
        conn = sch.get_retail_conn()
        try:
            repair(conn, company_id)
        finally:
            conn.close()
    finally:
        recon.compute_drift = real_compute
        started.set()
        thread.join(30)
    assert not thread.is_alive(), 'the racing writer never finished'
    return outcome


def test_repair_drift_under_a_concurrent_sale_leaves_a_complete_repair_or_none():
    """`repair_drift` takes its own BEGIN IMMEDIATE (Phase 3) instead of
    trusting every caller to remember one.

    The failure this prevents is not a half-written repair -- Python's
    sqlite3 opens an implicit transaction on the first DML, so the writes
    were always all-or-nothing. It is a STALE one: without the write lock
    held across the recompute AND the overwrite, a sale that commits between
    them is counted into the ledger total that was read and then erased by
    the balance that is written, so the repair itself manufactures the drift
    it was run to remove.

    The assertion is therefore the property, not the mechanism: whatever the
    interleaving, the sale is either fully recorded or not recorded at all,
    and the books balance afterwards.
    """
    from core.retail.stock_reconciliation import compute_drift, repair_drift

    sch, db_path = _install()
    company_id, branch_id, first, _second = _drifted_pair(sch, db_path)
    assert _drift(db_path, company_id), 'fixture has no drift -- see test 0'

    outcome = _race(sch, db_path, company_id, branch_id, first, repair_drift)

    conn = _open(db_path)
    try:
        remaining = compute_drift(conn, company_id)
        movements = conn.execute(
            'SELECT COUNT(*) FROM inventory_movements '
            "WHERE company_id=? AND product_id=? AND movement_type='sale_out'",
            (company_id, first)).fetchone()[0]
    finally:
        conn.close()

    assert remaining == [], (
        f'the repair left the books unbalanced: {remaining}. A sale committed '
        f'in the recompute/overwrite window and was erased by a total read '
        f'before it existed.')
    if outcome.get('sold'):
        assert movements == 1, 'the sale committed but its movement is gone'
    else:
        assert movements == 0
        assert 'lock' in outcome.get('error', '').lower(), outcome


def test_the_same_race_really_does_corrupt_the_books_without_that_lock():
    """The control, without which the test above proves nothing.

    A re-creation of `repair_drift`'s pre-Phase-3 body: recompute, then
    write, with no transaction of its own -- exactly what the old docstring
    asked every caller to wrap for it. If this passes cleanly, the race is
    not reachable in the harness and the test above is a test of nothing.
    """
    from core.retail.stock_reconciliation import compute_drift

    def unlocked_repair(conn, company_id):
        import core.retail.stock_reconciliation as recon
        for row in recon.compute_drift(conn, company_id):
            if not row['repairable']:
                continue
            conn.execute(
                'UPDATE inventory_balances SET quantity_on_hand=? '
                'WHERE company_id=? AND product_id=? AND branch_id=?',
                (row['ledger_balance'], company_id, row['product_id'], row['branch_id']))
        conn.commit()

    sch, db_path = _install()
    company_id, branch_id, first, _second = _drifted_pair(sch, db_path)

    outcome = _race(sch, db_path, company_id, branch_id, first, unlocked_repair)
    assert outcome.get('sold') is True, (
        'the racing sale never committed, so this control did not exercise '
        'the window it exists to demonstrate')

    conn = _open(db_path)
    try:
        remaining = compute_drift(conn, company_id)
    finally:
        conn.close()
    assert remaining, (
        'the unlocked repair did NOT corrupt the books, so the harness cannot '
        'reach this race and the locked test above is vacuous')
    assert [r['drift'] for r in remaining] == [1.0], remaining


def test_repair_drift_joins_a_transaction_the_caller_already_opened():
    """The route above this function opens BEGIN IMMEDIATE itself and writes
    its own per-balance audit rows inside it. Taking a second BEGIN would
    simply raise, breaking every existing caller in order to enforce a rule
    they were already following, so the lock is taken only when there is
    none.
    """
    from core.retail.stock_reconciliation import repair_drift

    sch, db_path = _install()
    company_id, _branch_id, _first, _second = _drifted_pair(sch, db_path)

    conn = sch.get_retail_conn()
    try:
        conn.execute('BEGIN IMMEDIATE')
        result = repair_drift(conn, company_id)
        assert conn.in_transaction, (
            'repair_drift committed the caller mid-transaction -- the audit '
            'rows the caller is about to write are no longer atomic with it')
        conn.rollback()
    finally:
        conn.close()

    assert len(result['repaired']) == 2
    # The caller rolled back, so the repair must be gone with it.
    assert len(_drift(db_path, company_id)) == 2, (
        "repair_drift wrote outside the caller's transaction")


def test_repair_drift_refuses_to_zero_a_balance_the_ledger_has_never_heard_of():
    """"The ledger says nothing" is not "the ledger says zero", and the
    difference is a shop's entire opening stock.

    On any install that predates v15 EVERY balance looks like this, so a
    repair without this guard answers "your stock is all zero" and writes it
    -- confidently, in one transaction, with an audit trail. v15 seeds an
    opening count for exactly these rows so they stop existing; this guard is
    what keeps the window before it (and any install where v15's gate has not
    yet passed) from being catastrophic.
    """
    from core.retail.stock_reconciliation import (
        SKIP_NO_LEDGER_HISTORY, repair_drift)

    sch, db_path = _install()
    conn = _open(db_path)
    try:
        company_id, (branch_id,) = _new_company(conn)
        product_id = _product(conn, company_id, 'SKU-UNBACKED')
        _balance(conn, company_id, product_id, branch_id, 250)
        conn.commit()
    finally:
        conn.close()

    assert _drift(db_path, company_id), 'fixture has no drift -- see test 0'

    conn = sch.get_retail_conn()
    try:
        result = repair_drift(conn, company_id)
    finally:
        conn.close()

    assert result['repaired'] == []
    assert [r['skip_reason'] for r in result['skipped']] == [SKIP_NO_LEDGER_HISTORY]
    assert _balances_snapshot(db_path, company_id)[(product_id, branch_id)] == 250.0, (
        "the repair wiped a shop's opening stock because its ledger was silent")


def test_repair_drift_writes_its_own_audit_row_without_naming_a_person():
    """An audit-less repair is a stock rewrite nobody can trace, and the old
    contract left that trail to whoever remembered. Written inside the
    repair's own transaction so a repair that lands without one is
    impossible; `user_id` stays NULL when nobody named themselves, because
    inventing an attribution is worse than an honest blank.
    """
    from core.retail.stock_reconciliation import repair_drift

    sch, db_path = _install()
    company_id, _branch_id, _first, _second = _drifted_pair(sch, db_path)

    conn = sch.get_retail_conn()
    try:
        repair_drift(conn, company_id)
    finally:
        conn.close()

    conn = _open(db_path)
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM audit_log WHERE company_id=? AND action='STOCK_RECONCILE_REPAIR'",
            (company_id,)).fetchall()]
    finally:
        conn.close()

    assert len(rows) == 1, rows
    assert rows[0]['user_id'] is None
    details = json.loads(rows[0]['details'])
    assert details['repaired_count'] == 2, details
    assert details['skipped_count'] == 0, details
