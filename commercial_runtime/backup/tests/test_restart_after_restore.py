"""INSTALL-1 (F16): a successful restore schedules the self-restart, a failed
one never does.

Both halves count calls to the restart hook (ENGINEERING.md 1: "the process
did not die" cannot tell "the hook was never asked" from "asked and declined").
The restart.py tests stub the exit function: a test that really os._exit'ed
would only prove the runner can be killed.
"""
import os
import sqlite3
import threading

import pytest
from flask import Flask

from commercial_runtime.backup.routes import make_backup_blueprint
from commercial_runtime.backup.service import create_backup
from commercial_runtime.install_support import restart


def _rig(tmp_path, hook):
    app_data = tmp_path / "app"
    db_dir = app_data / "database"
    (db_dir / "subsystems").mkdir(parents=True)
    for p in (db_dir / "registry.db", db_dir / "subsystems" / "retail.db"):
        c = sqlite3.connect(p)
        c.execute("create table t(x)")
        c.commit()
        c.close()
    made = create_backup("retail", str(app_data), str(app_data / "backups"), "1.0.0")
    flask_app = Flask(__name__)
    flask_app.secret_key = "t"
    flask_app.register_blueprint(
        make_backup_blueprint("retail", str(db_dir), "1.0.0", restart_after_restore=hook))
    client = flask_app.test_client()
    with client.session_transaction() as s:
        s["mt_user_id"] = 1
        s["mt_role"] = "admin"
    return client, os.path.basename(made["path"])


def test_successful_restore_asks_for_the_restart_and_says_so(tmp_path):
    calls = []
    client, name = _rig(tmp_path, lambda: calls.append(1) or True)
    r = client.post("/api/backup/restore", json={"filename": name})
    assert r.status_code == 200
    assert calls == [1]
    body = r.get_json()
    assert body["restarting"] is True
    assert "restarting" in body["message"].lower()


def test_successful_restore_without_opt_in_keeps_the_manual_message(tmp_path):
    client, name = _rig(tmp_path, lambda: False)    # dev run / test: hook declines
    body = client.post("/api/backup/restore", json={"filename": name}).get_json()
    assert body["restarting"] is False
    assert "Restart the application" in body["message"]


@pytest.mark.parametrize("payload", [{"filename": "not-listed.aurabak.zip"}, {}])
def test_failed_restore_never_schedules_a_restart(tmp_path, payload):
    calls = []
    client, _name = _rig(tmp_path, lambda: calls.append(1) or True)
    r = client.post("/api/backup/restore", json=payload)
    assert r.status_code == 400
    assert calls == []


def test_a_restore_that_raises_never_schedules_a_restart(tmp_path, monkeypatch):
    calls = []
    client, name = _rig(tmp_path, lambda: calls.append(1) or True)
    import commercial_runtime.backup.routes as routes

    def boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(routes, "restore_backup", boom)
    assert client.post("/api/backup/restore", json={"filename": name}).status_code == 500
    assert calls == []


def test_a_broken_restart_hook_does_not_fail_a_restore_that_succeeded(tmp_path):
    def broken():
        raise RuntimeError("thread limit")

    client, name = _rig(tmp_path, broken)
    r = client.post("/api/backup/restore", json={"filename": name})
    assert r.status_code == 200 and r.get_json()["restarting"] is False


# ── restart.py itself ──────────────────────────────────────────────────────
def test_schedule_restart_exits_75_after_the_delay_when_opted_in():
    exits, slept = [], []
    done = threading.Event()
    ok = restart.schedule_restart(
        0.25, env={restart.RESTART_ENV: "1"},
        exit_fn=lambda code: (exits.append(code), done.set()),
        sleep_fn=slept.append)
    assert ok is True
    assert done.wait(5)
    assert exits == [75] and slept == [0.25]


@pytest.mark.parametrize("env", [{}, {restart.RESTART_ENV: "0"}, {restart.RESTART_ENV: ""}])
def test_schedule_restart_does_nothing_without_the_opt_in(env):
    exits = []
    assert restart.schedule_restart(0, env=env, exit_fn=exits.append, sleep_fn=lambda s: None) is False
    threading.Event().wait(0.2)           # give a wrongly-started thread time to fire
    assert exits == []


def test_exit_code_is_the_one_the_launcher_listens_for():
    assert restart.RESTART_EXIT_CODE == 75
