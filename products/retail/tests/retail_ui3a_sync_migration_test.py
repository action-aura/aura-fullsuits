"""UI-3a (schema v54): the migration and the wire.

  * v53 -> v54 adds sale_items.list_price, sales.discount_reason, sales.discount_approved_by;
    replaying the step (or the whole chain) is a no-op, and a database that already has a column
    keeps its data.
  * A v54 receiver STORES the three fields a v54 sender ships, and NULLs them (never 0, never '')
    for a v53 sender that omits them.
  * A payload from a NEWER sender with keys this build has never heard of is ignored, not an error
    -- the same property that lets a v53 receiver (whose INSERT never names the new columns)
    take a v54 sender's events: the receiver reads each key it knows with `p.get(...)` and drops the
    rest. The test pins that by applying a payload loaded with unknown keys.

Run: pytest products/retail/tests/retail_ui3a_sync_migration_test.py -q
"""
from __future__ import annotations

import os
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

for _k in ("AURA_SYNC_RELAY_URL", "AURA_OWNER_LICENSING_URL"):
    os.environ.pop(_k, None)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RETAIL_BACKEND_DIR = _REPO_ROOT / "products" / "retail" / "backend"
for _p in (str(_REPO_ROOT), str(_RETAIL_BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import database.schema as schema  # noqa: E402
import api.retail_api as _retail_api_module  # noqa: E402
from api.retail_api import _ensure_credit_schema  # noqa: E402
from commercial_runtime.identity import registry_db  # noqa: E402
from commercial_runtime.sync.sync_service import SyncService  # noqa: E402

CID = "company-ui3a-sync-test"
SALE_UID = str(uuid.uuid4())
ITEM_UID = str(uuid.uuid4())
NEW_COLS = (("sale_items", "list_price"), ("sales", "discount_reason"), ("sales", "discount_approved_by"))


@pytest.fixture
def get_conn(tmp_path, monkeypatch):
    reg_dir = tmp_path / "registry_db"
    monkeypatch.setattr(registry_db, "_db_dir", str(reg_dir))
    monkeypatch.setattr(registry_db, "DB_PATH", str(reg_dir / "registry.db"))
    registry_db.init_registry_db()
    root = tmp_path / "install"
    subsys = root / "subsystems"
    subsys.mkdir(parents=True, exist_ok=True)
    original = (schema.BASE_DIR, schema.SUBSYS_DIR)
    schema.BASE_DIR, schema.SUBSYS_DIR = str(root), str(subsys)
    try:
        schema.init_retail()
    finally:
        schema.BASE_DIR, schema.SUBSYS_DIR = original
    db_path = subsys / "retail.db"

    def _get():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        return c

    # `sales.due_date` and friends are created lazily by the route layer, not by schema.py.
    previous = _retail_api_module._CREDIT_SCHEMA_READY
    _retail_api_module._CREDIT_SCHEMA_READY = False
    try:
        c = _get()
        try:
            _ensure_credit_schema(c)
            c.commit()
        finally:
            c.close()
    finally:
        _retail_api_module._CREDIT_SCHEMA_READY = previous
    return _get


def _cols(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()}


def test_a_fresh_install_has_the_three_columns_and_is_stamped_with_the_current_version(get_conn):
    conn = get_conn()
    try:
        assert schema.RETAIL_SCHEMA_VERSION == 58  # v56 UPDATE: head moved 55 -> 56 (SYNC-BOOT Phase 2, after MONEY-5 v55). Kept an EXACT pin at the merge (the p2 branch had loosened it to >= 54).  # v57 UPDATE: head moved 56 -> 57 (STOCK-TAKE, ROADMAP v57 claim); same exact pin, not loosened.  # v58 UPDATE: head moved 57 -> 58 (KEEP-SELLING C3 pause clock, ROADMAP v58 claim); same exact pin, not loosened.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 58  # v56 UPDATE: same exact pin.  # v57 UPDATE: head moved 56 -> 57 (STOCK-TAKE, ROADMAP v57 claim); same exact pin, not loosened.  # v58 UPDATE: head moved 57 -> 58 (KEEP-SELLING C3 pause clock, ROADMAP v58 claim); same exact pin, not loosened.
        for table, col in NEW_COLS:
            assert col in _cols(conn, table), (table, col)
    finally:
        conn.close()


def test_the_step_is_idempotent_and_keeps_existing_data(get_conn):
    conn = get_conn()
    try:
        conn.execute("INSERT INTO sales (company_id, uid, sale_number, total, discount_reason, "
                     "discount_approved_by) VALUES (?,?,?,?,?,?)",
                     (CID, SALE_UID, "S-1", 1, "Damaged", "mgr-uid"))
        conn.commit()
        schema._migrate_add_ui3a_discount_columns(conn)
        schema._migrate_add_ui3a_discount_columns(conn)
        row = conn.execute("SELECT discount_reason, discount_approved_by FROM sales WHERE uid=?",
                           (SALE_UID,)).fetchone()
        assert tuple(row) == ("Damaged", "mgr-uid")
    finally:
        conn.close()


def test_the_step_adds_each_missing_column_to_a_v53_shaped_database(get_conn):
    conn = get_conn()
    try:
        for table, col in NEW_COLS:                       # make it v53-shaped
            conn.execute(f'ALTER TABLE "{table}" DROP COLUMN {col}')
        conn.commit()
        for table, col in NEW_COLS:
            assert col not in _cols(conn, table)
        schema._migrate_add_ui3a_discount_columns(conn)
        conn.commit()
        for table, col in NEW_COLS:
            assert col in _cols(conn, table)
    finally:
        conn.close()


def test_the_pin_attempts_table_is_created_by_the_step_keeps_its_rows_and_replays(get_conn):
    """Verifier round: the manager-PIN lockout counter lives in the retail DB, in the v54 step
    (v54 had not shipped, so no v55). A database that ALREADY ran the earlier v54 has no such table;
    replaying the step must add it, and replaying again must not touch the rows it holds."""
    conn = get_conn()
    try:
        cols = [r[1] for r in conn.execute('PRAGMA table_info("pin_approval_attempts")').fetchall()]
        assert cols == ["company_id", "approver_uid", "window_start", "failures", "locked_until"], cols
        conn.execute("DROP TABLE pin_approval_attempts")           # a v54 that ran before this table existed
        conn.commit()
        schema._migrate_add_ui3a_discount_columns(conn)
        conn.execute("INSERT INTO pin_approval_attempts VALUES ('c','a','2026-10-13 10:00:00',3,NULL)")
        conn.commit()
        schema._migrate_add_ui3a_discount_columns(conn)
        schema._migrate_add_ui3a_discount_columns(conn)
        assert tuple(conn.execute("SELECT failures FROM pin_approval_attempts").fetchone()) == (3,)
        # the primary key is what makes the atomic upsert one row per approver
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO pin_approval_attempts VALUES ('c','a',NULL,1,NULL)")
    finally:
        conn.close()


def _svc(get_conn):
    return SyncService(client_factory=lambda: None, get_conn=get_conn,
                       local_company_id_provider=lambda: CID)


def _apply(get_conn, ev):
    conn = get_conn()
    try:
        ok = _svc(get_conn)._apply_event(conn, ev, local_company_id=CID)
        conn.commit()
        return ok
    finally:
        conn.close()


def _sale_ev(uid, **over):
    p = {"uid": uid, "sale_number": "S-" + uid[:6], "subtotal": 9.181, "discount_amount": 0.65,
         "tax_amount": 1.0, "total": 9.5, "amount_paid": 9.5, "change_amount": 0,
         "payment_method": "cash", "status": "completed", "created_at": "2026-10-13 09:00:00"}
    p.update(over)
    return {"entity_type": "sale", "event_type": "create", "entity_id": uid, "payload": p}


def _item_ev(uid, sale_uid, product_id, **over):
    p = {"uid": uid, "sale_uid": sale_uid, "product_id": product_id, "quantity": 1,
         "unit_price": 3.0, "discount_pct": 0, "tax_rate": 16, "line_total": 3.0, "unit_cost": 1.0}
    p.update(over)
    return {"entity_type": "sale_item", "event_type": "create", "entity_id": uid, "payload": p}


def _product(get_conn):
    pid = str(uuid.uuid4())
    conn = get_conn()
    try:
        conn.execute("INSERT INTO products (id, company_id, name, sku, sell_price) VALUES (?,?,?,?,?)",
                     (pid, CID, "P", "SKU-" + uuid.uuid4().hex[:6], 3.5))
        conn.commit()
        return pid
    finally:
        conn.close()


def _row(get_conn, sql, *args):
    conn = get_conn()
    try:
        return dict(conn.execute(sql, args).fetchone())
    finally:
        conn.close()


def test_a_v54_sender_is_stored_in_full(get_conn):
    pid = _product(get_conn)
    assert _apply(get_conn, _sale_ev(SALE_UID, discount_reason="Round down", discount_approved_by="mgr-uid"))
    assert _apply(get_conn, _item_ev(ITEM_UID, SALE_UID, pid, list_price=3.5))
    assert _row(get_conn, "SELECT discount_reason, discount_approved_by FROM sales WHERE uid=?", SALE_UID) == {
        "discount_reason": "Round down", "discount_approved_by": "mgr-uid"}
    assert _row(get_conn, "SELECT list_price FROM sale_items WHERE uid=?", ITEM_UID) == {"list_price": 3.5}


def test_a_v53_sender_that_omits_the_fields_leaves_them_null_not_zero(get_conn):
    pid = _product(get_conn)
    assert _apply(get_conn, _sale_ev(SALE_UID))
    assert _apply(get_conn, _item_ev(ITEM_UID, SALE_UID, pid))
    assert _row(get_conn, "SELECT discount_reason, discount_approved_by FROM sales WHERE uid=?", SALE_UID) == {
        "discount_reason": None, "discount_approved_by": None}
    assert _row(get_conn, "SELECT list_price FROM sale_items WHERE uid=?", ITEM_UID) == {"list_price": None}


def test_unknown_keys_from_a_newer_sender_are_ignored_not_an_error(get_conn):
    pid = _product(get_conn)
    assert _apply(get_conn, _sale_ev(SALE_UID, from_the_future="x", discount_future_flag=True))
    assert _apply(get_conn, _item_ev(ITEM_UID, SALE_UID, pid, from_the_future={"a": 1}))
    assert _row(get_conn, "SELECT COUNT(*) AS n FROM sales WHERE uid=?", SALE_UID)["n"] == 1


def test_a_real_v53_receiver_takes_a_v54_senders_events(get_conn, tmp_path):
    """The actual pre-UI-3a sync_service.py (commit 4db70a0b, rc.11), run against a v53-SHAPED
    database (the three columns dropped), applying a v54 sender's payloads. Its INSERTs never
    name the new columns and it reads keys with p.get(), so the extras must be ignored."""
    import importlib.util
    import subprocess
    try:
        old_src = subprocess.run(
            ["git", "show", "4db70a0b:commercial_runtime/sync/sync_service.py"],
            cwd=str(_REPO_ROOT), capture_output=True, check=True).stdout
    except Exception as exc:   # pragma: no cover - a shallow clone has no history
        pytest.skip(f"rc.11 sync_service.py not available from git: {exc}")
    old_path = tmp_path / "old_sync_service_v53.py"
    old_path.write_bytes(old_src)
    spec = importlib.util.spec_from_file_location("old_sync_service_v53", str(old_path))
    old_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old_mod)
    conn = get_conn()
    for table, col in NEW_COLS:
        conn.execute(f'ALTER TABLE "{table}" DROP COLUMN {col}')
    conn.commit(); conn.close()
    pid = _product(get_conn)
    svc = old_mod.SyncService(client_factory=lambda: None, get_conn=get_conn,
                              local_company_id_provider=lambda: CID)
    conn = get_conn()
    try:
        assert svc._apply_event(conn, _sale_ev(SALE_UID, discount_reason="Round down",
                                               discount_approved_by="mgr-uid"), local_company_id=CID)
        assert svc._apply_event(conn, _item_ev(ITEM_UID, SALE_UID, pid, list_price=3.5), local_company_id=CID)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM sales WHERE uid=?", (SALE_UID,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sale_items WHERE uid=?", (ITEM_UID,)).fetchone()[0] == 1
    finally:
        conn.close()


def test_a_real_v53_receiver_WITH_the_skew_guard_parks_a_v54_sale_instead_of_dropping_its_fields(get_conn, tmp_path):
    """The production arrangement: a v53 install runs the schema-version skew guard, which refuses
    (parks, replayably) an event from a NEWER sender rather than applying it and silently losing the
    columns it has never heard of. So a v53 till does not 'ignore' a v54 sale -- it holds it until it is
    upgraded, then applies it whole. Proved against the real rc.11 sync_service.py."""
    import importlib.util
    import subprocess
    try:
        old_src = subprocess.run(
            ["git", "show", "4db70a0b:commercial_runtime/sync/sync_service.py"],
            cwd=str(_REPO_ROOT), capture_output=True, check=True).stdout
    except Exception as exc:   # pragma: no cover
        pytest.skip(f"rc.11 sync_service.py not available from git: {exc}")
    old_path = tmp_path / "old_sync_service_v53_guarded.py"
    old_path.write_bytes(old_src)
    spec = importlib.util.spec_from_file_location("old_sync_service_v53_guarded", str(old_path))
    old_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old_mod)
    svc = old_mod.SyncService(client_factory=lambda: None, get_conn=get_conn,
                              local_company_id_provider=lambda: CID,
                              local_schema_version_provider=lambda: 53)
    ev = _sale_ev(SALE_UID, discount_reason="Round down", discount_approved_by="mgr-uid")
    ev["payload"]["_sender_schema_version"] = 54
    conn = get_conn()
    try:
        svc._apply_event(conn, ev, local_company_id=CID)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM sales WHERE uid=?", (SALE_UID,)).fetchone()[0] == 0, \
            "a v53 receiver applied a v54 sale: the new fields would have been silently dropped"
        reasons = [r[0] for r in conn.execute("SELECT reason FROM sync_apply_quarantine").fetchall()]
        assert "schema_version_too_new" in reasons, reasons
    finally:
        conn.close()
