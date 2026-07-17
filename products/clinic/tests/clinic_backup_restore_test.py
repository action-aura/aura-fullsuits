"""
Aura Clinic -- local backup/restore regression suite (Wave 0, AUDIT-019).

Retail's test suite (products/retail/tests/retail_backup_restore_test.py)
already exercises the shared commercial_runtime.backup.service logic in
depth (manifest/checksums, corruption, schema-version gating, rollback
preservation, no-partial-file-on-failure). This file focuses on what is
specific to Clinic: backups are tagged product_code='clinic', a Retail
backup must be refused here (and vice versa, covered on the Retail side),
and the Clinic backup HTTP endpoints enforce the same admin-only gating.

See docs/corrections/wave0/backup-and-restore-foundation.md.

Run:
    pytest products/clinic/tests/clinic_backup_restore_test.py -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from commercial_runtime.backup.service import create_backup, restore_backup, BackupError  # noqa: E402


def _make_app_data(product_db_name):
    root = Path(tempfile.mkdtemp(prefix="aura_clinic_backup_src_"))
    db_dir = root / 'database'
    (db_dir / 'subsystems').mkdir(parents=True, exist_ok=True)
    reg = sqlite3.connect(str(db_dir / 'registry.db'))
    reg.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
    reg.commit(); reg.close()
    prod = sqlite3.connect(str(db_dir / 'subsystems' / f'{product_db_name}.db'))
    prod.execute("CREATE TABLE clinic_patients (id INTEGER PRIMARY KEY, name TEXT)")
    prod.execute("INSERT INTO clinic_patients (name) VALUES ('Synthetic Patient')")
    prod.commit(); prod.close()
    return root


def test_clinic_backup_round_trip():
    app_data = _make_app_data('clinic')
    dest = Path(tempfile.mkdtemp(prefix="aura_clinic_backup_dest_"))
    result = create_backup('clinic', str(app_data), str(dest), '1.0.0')
    assert result['manifest']['product_code'] == 'clinic'

    target = Path(tempfile.mkdtemp(prefix="aura_clinic_backup_target_"))
    (target / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
    restore_backup('clinic', str(target), result['path'])

    prod = sqlite3.connect(str(target / 'database' / 'subsystems' / 'clinic.db'))
    row = prod.execute("SELECT name FROM clinic_patients").fetchone()
    prod.close()
    assert row[0] == 'Synthetic Patient'


def test_clinic_refuses_retail_backup():
    app_data = _make_app_data('retail')
    dest = Path(tempfile.mkdtemp(prefix="aura_retail_backup_dest_"))
    result = create_backup('retail', str(app_data), str(dest), '1.0.0')

    target = Path(tempfile.mkdtemp(prefix="aura_clinic_backup_target_"))
    (target / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
    with pytest.raises(BackupError, match='cross-product'):
        restore_backup('clinic', str(target), result['path'])


# ── HTTP endpoint admin gating ──────────────────────────────────────────────

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_backup_http_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

import app as _app_module  # noqa: E402

_flask_app = _app_module.init_app()
_flask_app.config["TESTING"] = True


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def test_clinic_backup_create_endpoint_requires_authentication():
    client = _flask_app.test_client()
    r = client.post('/api/backup/create')
    assert r.status_code == 401


def test_clinic_backup_list_endpoint_requires_authentication():
    client = _flask_app.test_client()
    r = client.get('/api/backup/list')
    assert r.status_code == 401
