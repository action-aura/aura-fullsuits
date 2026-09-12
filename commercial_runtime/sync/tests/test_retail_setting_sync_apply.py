"""Tests for `_apply_event`'s `retail_setting` branch.

2026-09-05: one licence, two devices, and the phone showed "$" while the
desktop showed "JD" -- `retail_settings` (company_id, skey, svalue) was never
a synced entity, so currency, tax mode, credit defaults, business day and
branding text were per DEVICE when every one of them describes the SHOP.
`retail_api.py::_queue_setting_sync_event` is the emit side (covered by
retail_settings_sync_emit_test.py); this file covers the APPLY side: writing
a pulled `retail_setting` event into the RECEIVER's own `retail_settings`
row under the RECEIVER's own company_id, never the sender's.

Fixture shape copied from test_sync_service.py's category-branch tests: a
real sqlite file under tmp_path, a minimal schema matching the columns
`_apply_event` actually touches, a real `SyncService` with
`handled_entity_types=RETAIL_SYNC_ENTITY_TYPES` and a
`local_company_id_provider`, applied through `apply_pull_result` exactly as
a real pull would.
"""
import sqlite3
import uuid

import pytest

from commercial_runtime.sync.sync_service import (
    REGISTRY_SYNC_ENTITY_TYPES,
    RETAIL_SYNC_ENTITY_TYPES,
    SyncService,
)

_SCHEMA = """
CREATE TABLE retail_settings (
    company_id INTEGER,
    skey TEXT,
    svalue TEXT,
    PRIMARY KEY (company_id, skey)
);
CREATE TABLE sync_outbox (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sync_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_seq INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
"""

RECEIVER_COMPANY_ID = "receiver-co"
OTHER_COMPANY_ID = "some-other-co"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "retail.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def get_conn(db_path):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c
    return _get_conn


def _event(skey, svalue, event_type="update", **overrides):
    ev = {
        "id": str(uuid.uuid4()),
        "entity_type": "retail_setting",
        "entity_id": str(uuid.uuid4()),
        "event_type": event_type,
        "payload": {"skey": skey, "svalue": svalue},
    }
    ev.update(overrides)
    return ev


def _apply(get_conn, events, cursor=1, local_company_id=RECEIVER_COMPANY_ID):
    """One-shot apply of a pre-built event batch through the REAL pull path
    -- mirrors test_sync_service.py's own `_apply` helper exactly, so this
    exercises `apply_pull_result`'s real `local_company_id` eager-fetch gate
    rather than bypassing it."""
    conn = get_conn()
    service = SyncService(client_factory=lambda: None, get_conn=get_conn,
                          local_company_id_provider=lambda: local_company_id)
    try:
        service.apply_pull_result(conn, {"events": events, "cursor": cursor})
        conn.commit()
    finally:
        conn.close()
    return service


def _all_settings(get_conn):
    conn = get_conn()
    rows = [dict(r) for r in conn.execute("SELECT * FROM retail_settings").fetchall()]
    conn.close()
    return rows


def _setting(get_conn, company_id, skey):
    conn = get_conn()
    row = conn.execute(
        "SELECT svalue FROM retail_settings WHERE company_id=? AND skey=?",
        (company_id, skey),
    ).fetchone()
    conn.close()
    return row["svalue"] if row else None


# ── 1/2. update creates/overwrites under the RECEIVER's own company_id ─────

def test_update_event_writes_under_the_receivers_own_company_id(get_conn):
    _apply(get_conn, [_event("base_currency", "USD")])

    rows = _all_settings(get_conn)
    assert len(rows) == 1
    assert rows[0]["company_id"] == RECEIVER_COMPANY_ID
    assert rows[0]["skey"] == "base_currency"
    assert rows[0]["svalue"] == "USD"


def test_the_payload_carries_no_company_id_and_the_row_never_lands_under_another_one(get_conn):
    ev = _event("base_currency", "USD")
    assert "company_id" not in ev["payload"], (
        "the emit side must never put a company_id on the wire -- see "
        "_queue_setting_sync_event's docstring and the category branch's "
        "identical 'cross-device company_id bug fix' note"
    )
    _apply(get_conn, [ev])
    assert _setting(get_conn, OTHER_COMPANY_ID, "base_currency") is None


def test_second_update_is_last_write_wins_one_row_total(get_conn):
    _apply(get_conn, [_event("base_currency", "USD")])
    _apply(get_conn, [_event("base_currency", "JOD")])

    rows = _all_settings(get_conn)
    assert len(rows) == 1, "last-write-wins: a second update must not create a second row"
    assert rows[0]["svalue"] == "JOD"


# ── 3. delete ────────────────────────────────────────────────────────────

def test_delete_event_removes_the_row(get_conn):
    _apply(get_conn, [_event("base_currency", "USD")])
    assert _setting(get_conn, RECEIVER_COMPANY_ID, "base_currency") == "USD"

    _apply(get_conn, [_event("base_currency", None, event_type="delete")])

    assert _setting(get_conn, RECEIVER_COMPANY_ID, "base_currency") is None, (
        "a delete event pulled on its own (no accompanying create/update in "
        "the same batch) must still clear the row -- if this fails, "
        "apply_pull_result's eager local_company_id fetch (gated on "
        "event_type in ('create','update')) never ran for this delete-only "
        "batch, so the branch deleted WHERE company_id=NULL and matched "
        "nothing"
    )


def test_delete_of_a_missing_key_is_a_silent_no_op_not_an_error(get_conn):
    service = _apply(get_conn, [_event("never_set_key", None, event_type="delete")])
    assert _all_settings(get_conn) == []
    # No exception escaped _apply (which would have propagated out of
    # apply_pull_result above) -- that is the "returns True" contract this
    # branch shares with every other "absent row on delete is fine" branch
    # in this module (e.g. user_permission's identical posture).
    assert service is not None


# ── 4. blob-prefixed keys are refused ───────────────────────────────────────

def test_blob_prefixed_key_is_never_written(get_conn):
    _apply(get_conn, [_event("blob_branding_logo", "data:image/png;base64,AAAA")])
    assert _all_settings(get_conn) == [], (
        "a kilobyte logo must never reach this branch -- refused on the "
        "emit side (_queue_setting_sync_event) AND belt-and-braces here"
    )


# ── 5. unrecognised event_type is ignored ───────────────────────────────────

def test_unrecognised_event_type_is_ignored_not_applied(get_conn):
    _apply(get_conn, [_event("base_currency", "USD", event_type="frobnicate")])
    assert _all_settings(get_conn) == []


# ── 6. entity-type set membership ───────────────────────────────────────────

def test_retail_setting_is_in_the_retail_set_and_not_the_registry_set():
    assert "retail_setting" in RETAIL_SYNC_ENTITY_TYPES
    assert "retail_setting" not in REGISTRY_SYNC_ENTITY_TYPES
