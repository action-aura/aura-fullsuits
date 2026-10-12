"""BACKUP-1: the download route resolves ONLY names the listing shows."""
import os
import sqlite3

import pytest
from flask import Flask

from commercial_runtime.backup.routes import make_backup_blueprint
from commercial_runtime.backup.service import create_backup


@pytest.fixture
def rig(tmp_path):
    app_data = tmp_path / "app"
    db_dir = app_data / "database"
    (db_dir / "subsystems").mkdir(parents=True)
    for p in (db_dir / "registry.db", db_dir / "subsystems" / "retail.db"):
        c = sqlite3.connect(p)
        c.execute("create table t(x)")
        c.commit()
        c.close()
    backups = app_data / "backups"
    made = create_backup("retail", str(app_data), str(backups), "1.0.0")
    # Decoys a traversal / loose guard would serve:
    (app_data / "x.zip").write_bytes(b"OUTSIDE")                       # parent of backup dir
    (backups / "secret.txt").write_bytes(b"INSIDE-BUT-NOT-A-BACKUP")   # in dir, not listed
    flask_app = Flask(__name__)
    flask_app.secret_key = "t"
    flask_app.register_blueprint(make_backup_blueprint("retail", str(db_dir), "1.0.0"))
    client = flask_app.test_client()
    with client.session_transaction() as s:
        s["mt_user_id"] = 1
        s["mt_role"] = "admin"
    return client, os.path.basename(made["path"]), app_data


@pytest.mark.parametrize("bad", [
    "..%2Fx.zip", "..%5Cx.zip", "%2E%2E%2Fx.zip", "%2Fetc%2Fpasswd", "C%3A%5Cwindows%5Cwin.ini",
    "secret.txt", "aura-retail-backup-nope.aurabak.zip", "..%2F..%2Fapp%2Fx.zip",
])
def test_download_refuses_anything_not_in_the_listing(rig, bad):
    client, _name, _dir = rig
    r = client.get(f"/api/backup/download/{bad}", follow_redirects=True)  # a leading-slash name is 308-merged first
    assert r.status_code in (400, 404), (bad, r.status_code)
    assert b"OUTSIDE" not in r.data and b"INSIDE" not in r.data


def test_download_serves_a_listed_name(rig):
    client, name, _dir = rig
    r = client.get(f"/api/backup/download/{name}")
    assert r.status_code == 200
    assert r.data[:2] == b"PK"


def test_download_requires_admin(rig):
    client, name, _dir = rig
    with client.session_transaction() as s:
        s["mt_role"] = "cashier"
    assert client.get(f"/api/backup/download/{name}").status_code == 403


def test_settings_roundtrip_and_status_route(rig):
    client, _name, _dir = rig
    assert client.get("/api/backup/settings").get_json()["backup_schedule"] == "daily"
    r = client.post("/api/backup/settings", json={"backup_schedule": "off", "backup_dir_secondary": ""})
    assert r.status_code == 200 and r.get_json()["backup_schedule"] == "off"
    assert client.post("/api/backup/settings", json={"backup_schedule": "weekly"}).status_code == 400
    assert client.post("/api/backup/settings", json={"backup_dir_secondary": "C:\\Program Files\\x"}).status_code == 400
    st = client.get("/api/backup/status").get_json()
    assert st["status"] == "ok" and st["backup_schedule"] == "off" and st["warning"] is False
