"""Emulator run 2026-10-16: the Android phone's registry cursor stayed 0 forever because Kotlin ignored
Owner's `fast_forward_to`. The fix routes the rule through ONE Python function
(`SyncService.fast_forward_registry_to`) that the desktop pull and the `/_internal/registry/fast-forward`
seam both call. This file pins the seam's HTTP contract and that it gives the SAME answer as the desktop path.

MUTATIONS (run for real): breaking the shared rule (e.g. dropping the registry-stream check or the
backwards guard) turns the seam tests here AND the desktop test in test_sync_bootstrap_hooks.py red.
"""
import sqlite3
from types import SimpleNamespace

import pytest
from flask import Flask

from commercial_runtime.sync.internal_routes import make_sync_internal_blueprint
from commercial_runtime.sync.relay_client import RelayRejected
from commercial_runtime.sync.sync_service import SyncService

SECRET = "s3cret"
H = {"X-Aura-Internal-Secret": SECRET}
URL = "/api/sync/_internal/registry/fast-forward"


class _Repo:
    def load(self):
        return SimpleNamespace(owner_installation_id="inst-1")


def _make(tmp_path, stream, start=0):
    db = str(tmp_path / f"{stream}.db")
    c = sqlite3.connect(db)
    c.executescript("CREATE TABLE sync_cursor (id INTEGER PRIMARY KEY CHECK (id=1), last_seq INTEGER NOT NULL DEFAULT 0);"
                    f"INSERT INTO sync_cursor (id,last_seq) VALUES (1,{start});")
    c.commit()
    c.close()

    def get_conn():
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        return conn

    svc = SyncService(None, get_conn, lambda: None, stream_label=stream)
    app = Flask(__name__)
    app.register_blueprint(make_sync_internal_blueprint(
        sync_service=svc, get_conn=get_conn, state_repository=_Repo(), shared_secret=SECRET))
    return app.test_client(), svc, get_conn


def _cursor(get_conn):
    c = get_conn()
    try:
        return c.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()[0]
    finally:
        c.close()


def test_the_seam_needs_the_internal_secret(tmp_path):
    client, _, get_conn = _make(tmp_path, "registry")
    assert client.post(URL, json={"fast_forward_to": 9}).status_code == 403
    assert _cursor(get_conn) == 0


def test_the_cursor_moves_to_fast_forward_to_and_a_repeat_is_idempotent(tmp_path):
    client, _, get_conn = _make(tmp_path, "registry")
    r = client.post(URL, json={"fast_forward_to": 9}, headers=H)
    assert r.status_code == 200 and r.get_json()["fast_forwarded"] is True and r.get_json()["since"] == 9
    assert _cursor(get_conn) == 9
    again = client.post(URL, json={"fast_forward_to": 9}, headers=H)
    assert again.get_json()["since"] == 9 and _cursor(get_conn) == 9


def test_a_backwards_move_is_refused_and_changes_nothing(tmp_path):
    client, _, get_conn = _make(tmp_path, "registry", start=20)
    r = client.post(URL, json={"fast_forward_to": 9}, headers=H)
    assert r.status_code == 200 and r.get_json()["fast_forwarded"] is False
    assert _cursor(get_conn) == 20


def test_the_retail_stream_is_never_fast_forwarded_through_the_seam(tmp_path):
    """Deny half: skipping retail history is the silent partial copy of the shop."""
    client, _, get_conn = _make(tmp_path, "retail")
    r = client.post(URL, json={"fast_forward_to": 9}, headers=H)
    assert r.get_json()["fast_forwarded"] is False and _cursor(get_conn) == 0


@pytest.mark.parametrize("body", [{}, {"fast_forward_to": "9"}, {"fast_forward_to": True}, {"fast_forward_to": None}])
def test_a_missing_or_non_integer_target_is_a_400(tmp_path, body):
    client, _, get_conn = _make(tmp_path, "registry")
    assert client.post(URL, json=body, headers=H).status_code == 400
    assert _cursor(get_conn) == 0


def test_the_seam_and_the_desktop_path_give_the_same_result(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    seam_client, _, seam_conn = _make(tmp_path / "a", "registry", start=3)
    seam_client.post(URL, json={"fast_forward_to": 9}, headers=H)
    _, svc, desk_conn = _make(tmp_path / "b", "registry", start=3)
    conn = desk_conn()
    try:
        moved = svc._fast_forward_registry(
            conn, RelayRejected("RESYNC_REQUIRED", "x", {"fast_forward_to": 9}), None)
    finally:
        conn.close()
    assert moved is True and _cursor(desk_conn) == _cursor(seam_conn) == 9
