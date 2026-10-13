"""Retail schema v42 (extended) -- `sync_apply_quarantine` gains `auth_key`.

AUDIT-033 / S-AUTH fix round (verifier V findings #7/#8). The unreleased v42 was
EXTENDED rather than a v43 claimed (tags stop at rc.7, rc.8 is a draft; ROADMAP
amended first): `_migrate_quarantine_auth_key`, appended LAST in
`_apply_retail_alters`, rebuilds the table with `auth_key TEXT NOT NULL DEFAULT ''`
and the primary key `(entity_id, event_type, auth_key)`, using the standing `*_new`
idiom in one transaction.

This proves it the way a shop meets it: a REAL install taken back to v41 with
quarantine rows seeded in the OLD shape, then booted by the genuine
`init_retail()` -- so the real `ensure_schema_version` backup and
`integrity_check` run around it. A signed row keeps its signature's key, an
unsigned one gets '', nothing else about any row moves, and a second boot is a
no-op.

Run (ONE FILE PER PYTEST PROCESS -- schema.py resolves its paths at import):
    pytest products/retail/tests/retail_v42_quarantine_auth_key_migration_test.py -q
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]        # products/retail
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OLD_QUARANTINE = """
CREATE TABLE sync_apply_quarantine (
    entity_id TEXT NOT NULL, entity_type TEXT NOT NULL, event_type TEXT NOT NULL,
    payload TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT,
    quarantined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_id, event_type))
"""
SIGNED = {"uid": "u1", "_auth": {"v": 1, "inst": "i", "dev": "d", "pk": "k", "sig": "c2ln", "lease": {}}}


def _activate(app_data: str):
    os.environ['AURA_APP_DATA'] = app_data
    os.environ['AURA_STANDALONE'] = '1'
    import database.schema as sch
    sch.BASE_DIR = os.path.join(app_data, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    os.makedirs(sch.SUBSYS_DIR, exist_ok=True)
    return sch


def _open(sch):
    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _pk(conn):
    return {r[1] for r in conn.execute("PRAGMA table_info(sync_apply_quarantine)").fetchall() if r[5] > 0}


def _rewound_install(tmp_path):
    """A true v42 install taken back to v41 with the OLD quarantine shape and rows in it."""
    sch = _activate(str(tmp_path))
    sch.init_retail()
    conn = _open(sch)
    assert 'auth_key' in [r[1] for r in conn.execute("PRAGMA table_info(sync_apply_quarantine)").fetchall()], \
        "a fresh install must already arrive in the new shape (the chain rebuilds the base CREATE)"
    conn.execute('DROP TABLE sync_apply_quarantine')
    conn.execute(OLD_QUARANTINE)
    conn.executemany(
        "INSERT INTO sync_apply_quarantine (entity_id, entity_type, event_type, payload, reason, detail, "
        "quarantined_at) VALUES (?,?,?,?,?,?,?)", [
            ("signed", "user", "update", json.dumps(SIGNED), "owner_change_needs_main", "d1", "2026-01-01 10:00:00"),
            ("unsigned", "till_allocation", "create", json.dumps({"id": "a"}), "unsigned_authority_event", "d2",
             "2026-01-02 10:00:00"),
            ("sale-child", "sale_item", "create", json.dumps({"sale_uid": "s"}), "missing_parent:sale", None,
             "2026-01-03 10:00:00"),
        ])
    conn.execute('PRAGMA user_version=41')
    conn.commit()
    conn.close()
    return sch


def test_a_v41_database_is_rebuilt_with_auth_key_and_keeps_every_row(tmp_path):
    from commercial_runtime.sync import authority
    sch = _rewound_install(tmp_path)
    sch.init_retail()                                      # the REAL v41 -> v42 upgrade, backup and checks included
    conn = _open(sch)
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 56  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v54 UPDATE: head moved 53 -> 54 (UI-3a); v54's own shape is pinned by retail_ui3a_sync_migration_test. [earlier: v53 UPDATE: head moved 52 -> 53 (MONEY-4c); v53's own shape is pinned by retail_money4c_credit_inherit_test.py. (was:  head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3); v51's own shape is pinned by retail_money3_cheque_books_test.py / retail_money3_supplier_drawer_test.py | prior: v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py)]
        assert _pk(conn) == {"entity_id", "event_type", "auth_key"}
        rows = {r["entity_id"]: dict(r) for r in conn.execute(
            "SELECT entity_id, entity_type, event_type, payload, reason, detail, quarantined_at, auth_key "
            "FROM sync_apply_quarantine")}
        assert set(rows) == {"signed", "unsigned", "sale-child"}
        # Review V3r G2: authority rows (signed OR unsigned) are keyed by the content
        # hash of their payload; a non-authority row (the sale child) keeps ''. What
        # this can no longer catch: '' for an unsigned authority row -- deliberately
        # gone; the sale-child assertion still pins '' for everything else.
        assert rows["signed"]["auth_key"] == authority.quarantine_auth_key(SIGNED, "user")
        assert len(rows["signed"]["auth_key"]) == 64
        assert rows["unsigned"]["auth_key"] == authority.quarantine_auth_key({"id": "a"}, "till_allocation") != ""
        assert rows["sale-child"]["auth_key"] == ""
        assert (rows["signed"]["reason"], rows["signed"]["detail"], rows["signed"]["quarantined_at"]) == \
            ("owner_change_needs_main", "d1", "2026-01-01 10:00:00")
        assert json.loads(rows["signed"]["payload"]) == SIGNED
        assert rows["sale-child"]["detail"] is None
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='sync_apply_quarantine_new'").fetchone()
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        dump1 = conn.execute("SELECT * FROM sync_apply_quarantine ORDER BY entity_id").fetchall()
    finally:
        conn.close()
    sch.init_retail()                                      # a second boot is a no-op
    conn = _open(sch)
    try:
        assert conn.execute("SELECT * FROM sync_apply_quarantine ORDER BY entity_id").fetchall() == dump1
    finally:
        conn.close()


def test_the_pre_migration_backup_still_holds_the_old_shape(tmp_path):
    """`ensure_schema_version` backs the database up BEFORE the rebuild, so a bad
    rebuild is recoverable: the backup is v41 with the two-column key."""
    sch = _rewound_install(tmp_path)
    sch.init_retail()
    bdir = os.path.join(sch.BASE_DIR, 'migration_backups')
    names = sorted(n for n in os.listdir(bdir) if 'v41-to-v56' in n)  # v56 UPDATE: head moved 54 -> 56 (SYNC-BOOT Phase 2; v55 is MONEY-5's and is not on this line of history); v56's own shape is pinned by retail_site_stream_cursors_migration_test. Same exact-number pin as before.  # v52 UPDATE: head moved 51 -> 52 (MONEY-4b); v52's own shape is pinned by retail_money4b_migration_test.py | prior: v51 UPDATE: head moved 50 -> 51 (MONEY-3) | prior v50 UPDATE: head moved 49 -> 50 (W2b/W3a); v50's own shape is pinned by retail_v50_config_sync_migration_test.py | v54 UPDATE: head moved 53 -> 54 (UI-3a); same property, the backup name carries the head
    assert names, os.listdir(bdir)
    conn = sqlite3.connect(f'file:{os.path.join(bdir, names[-1])}?mode=ro', uri=True)
    try:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 41
        pk = {r[1] for r in conn.execute("PRAGMA table_info(sync_apply_quarantine)").fetchall() if r[5] > 0}
        assert pk == {"entity_id", "event_type"}
        assert conn.execute("SELECT COUNT(*) FROM sync_apply_quarantine").fetchone()[0] == 3
    finally:
        conn.close()
