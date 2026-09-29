"""A peer must not be able to write THIS till's device-local retail_settings.

The `retail_setting` apply branch used to accept any key except `blob_*`.
`numbering_till:<terminal>` is a till's own document-number counter, written
by retail_api.py::_write_local_setting with emit deliberately OFF -- so it is
never legitimately on the wire, and an event that names it is a peer writing
into this till's private state (rewind it to force a duplicate number, or
jump it to burn the series).

Apply-side guard, so it covers create, update AND delete. The allow half
matters as much as the deny half: `branch_code:<uid>` is SHOP-WIDE and must
keep applying, or numbering breaks across tills.
"""
import logging
import re
import sqlite3
import uuid
from pathlib import Path

import pytest

from commercial_runtime.sync import sync_service as ss
from commercial_runtime.sync.sync_service import RETAIL_SYNC_ENTITY_TYPES, SyncService

_SCHEMA = """
CREATE TABLE retail_settings (
    company_id INTEGER, skey TEXT, svalue TEXT,
    PRIMARY KEY (company_id, skey)
);
CREATE TABLE sync_outbox (
    id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL, payload TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE sync_cursor (
    id INTEGER PRIMARY KEY CHECK (id = 1), last_seq INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO sync_cursor (id, last_seq) VALUES (1, 0);
"""

CO = "receiver-co"
TERMINAL = "term-receiver-1"
TILL_KEY = f"numbering_till:{TERMINAL}"
MINE = '{"next": 42}'


@pytest.fixture
def get_conn(tmp_path):
    path = tmp_path / "retail.db"
    c = sqlite3.connect(str(path))
    c.executescript(_SCHEMA)
    c.execute("INSERT INTO retail_settings VALUES (?,?,?)", (CO, TILL_KEY, MINE))
    c.commit()
    c.close()

    def _get():
        conn = sqlite3.connect(str(path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn
    return _get


def _ev(skey, svalue, event_type="update"):
    return {"id": str(uuid.uuid4()), "entity_type": "retail_setting",
            "entity_id": str(uuid.uuid4()), "event_type": event_type,
            "payload": {"skey": skey, "svalue": svalue}}


def _apply(get_conn, events, service=None):
    conn = get_conn()
    service = service or SyncService(
        client_factory=lambda: None, get_conn=get_conn,
        handled_entity_types=RETAIL_SYNC_ENTITY_TYPES,
        local_company_id_provider=lambda: CO)
    try:
        service.apply_pull_result(conn, {"events": events, "cursor": 1})
        conn.commit()
    finally:
        conn.close()
    return service


def _val(get_conn, skey):
    conn = get_conn()
    row = conn.execute("SELECT svalue FROM retail_settings WHERE company_id=? AND skey=?",
                       (CO, skey)).fetchone()
    conn.close()
    return row["svalue"] if row else None


@pytest.mark.parametrize("event_type", ["create", "update"])
def test_peer_cannot_overwrite_this_tills_numbering_counter(get_conn, event_type):
    _apply(get_conn, [_ev(TILL_KEY, '{"next": 1}', event_type)])
    assert _val(get_conn, TILL_KEY) == MINE


def test_peer_cannot_delete_this_tills_numbering_counter(get_conn):
    _apply(get_conn, [_ev(TILL_KEY, None, "delete")])
    assert _val(get_conn, TILL_KEY) == MINE


def test_peer_cannot_plant_a_counter_for_a_terminal_this_till_has_not_written(get_conn):
    _apply(get_conn, [_ev("numbering_till:some-other-terminal", '{"next": 9}')])
    assert _val(get_conn, "numbering_till:some-other-terminal") is None


def test_shop_wide_keys_still_apply_next_to_a_refused_one(get_conn):
    # Allow half: the refused key must not poison the batch or the neighbours.
    uid = "branch-uid-1"
    _apply(get_conn, [
        _ev(TILL_KEY, '{"next": 1}'),
        _ev(f"branch_code:{uid}", "AMM"),
        _ev("base_currency", "JOD"),
    ])
    assert _val(get_conn, TILL_KEY) == MINE
    assert _val(get_conn, f"branch_code:{uid}") == "AMM"
    assert _val(get_conn, "base_currency") == "JOD"


def test_refusal_is_logged_once_per_key(get_conn, caplog):
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        service = _apply(get_conn, [_ev(TILL_KEY, "a"), _ev(TILL_KEY, "b"),
                                    _ev(TILL_KEY, None, "delete")])
        _apply(get_conn, [_ev(TILL_KEY, "c")], service=service)
    hits = [r for r in caplog.records if "device-local" in r.getMessage()]
    assert len(hits) == 1, [r.getMessage() for r in hits]
    assert TILL_KEY in hits[0].getMessage()


def test_literal_matches_retail_api():
    # sync_service cannot import retail_api, so it hardcodes the prefix. If
    # retail_api renames it this fails instead of the guard silently going
    # dead. Derived from retail_api's own source, not a second typed copy.
    src = (Path(ss.__file__).resolve().parents[2]
           / "products" / "retail" / "backend" / "api" / "retail_api.py").read_text(encoding="utf-8")
    m = re.search(r"^_TILL_STATE_KEY_PREFIX\s*=\s*['\"]([^'\"]+)['\"]", src, re.M)
    assert m, "retail_api no longer defines _TILL_STATE_KEY_PREFIX"
    assert m.group(1) in ss._DEVICE_LOCAL_SETTING_PREFIXES
