"""
Aura Retail -- local backup/restore regression suite (Wave 0, AUDIT-019).

Exercises commercial_runtime.backup.service directly against synthetic
SQLite databases (never real business data -- see
docs/corrections/wave0/backup-and-restore-foundation.md), plus the Retail
backup HTTP endpoints' admin-only gating.

Covers: backup produces a validated manifest + checksums, restore round-trip
preserves data, restore refuses a cross-product backup, restore refuses a
backup with a tampered/corrupted member, restore preserves a rollback copy
of whatever was live before replacing it, and create_backup never leaves a
partial file behind when a source database is missing.

Run:
    pytest products/retail/tests/retail_backup_restore_test.py -v
"""
import io
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from commercial_runtime.backup.service import (  # noqa: E402
    create_backup, restore_backup, BackupError, is_safe_backup_dir, SCHEMA_VERSION,
)


def _make_app_data(with_data=True):
    root = Path(tempfile.mkdtemp(prefix="aura_backup_src_"))
    db_dir = root / 'database'
    (db_dir / 'subsystems').mkdir(parents=True, exist_ok=True)
    if with_data:
        reg = sqlite3.connect(str(db_dir / 'registry.db'))
        reg.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)")
        reg.execute("INSERT INTO users VALUES ('u1','synthetic@test.local')")
        reg.commit(); reg.close()

        prod = sqlite3.connect(str(db_dir / 'subsystems' / 'retail.db'))
        prod.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT)")
        prod.execute("INSERT INTO products (name) VALUES ('Synthetic Widget')")
        prod.commit(); prod.close()
    return root


def test_create_backup_produces_manifest_with_valid_checksums():
    src = Path(tempfile.mkdtemp(prefix="aura_backup_"))
    try:
        app_data = _make_app_data()
        dest = Path(tempfile.mkdtemp(prefix="aura_backup_dest_"))
        result = create_backup('retail', str(app_data), str(dest), '1.2.3')
        assert os.path.isfile(result['path'])
        manifest = result['manifest']
        assert manifest['product_code'] == 'retail'
        assert manifest['schema_version'] == SCHEMA_VERSION
        assert manifest['app_version'] == '1.2.3'
        assert {f['name'] for f in manifest['files']} == {'registry.db', 'retail.db'}

        with zipfile.ZipFile(result['path']) as zf:
            assert zf.testzip() is None
            names = set(zf.namelist())
            assert names == {'manifest.json', 'registry.db', 'retail.db'}
    finally:
        shutil.rmtree(src, ignore_errors=True)


def test_restore_round_trip_preserves_data():
    app_data = _make_app_data()
    dest = Path(tempfile.mkdtemp(prefix="aura_backup_dest_"))
    result = create_backup('retail', str(app_data), str(dest), '1.0.0')

    target = Path(tempfile.mkdtemp(prefix="aura_backup_target_"))
    (target / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
    restore_result = restore_backup('retail', str(target), result['path'])
    assert set(restore_result['restored']) == {'registry.db', 'retail.db'}

    prod = sqlite3.connect(str(target / 'database' / 'subsystems' / 'retail.db'))
    row = prod.execute("SELECT name FROM products").fetchone()
    prod.close()
    assert row[0] == 'Synthetic Widget'

    reg = sqlite3.connect(str(target / 'database' / 'registry.db'))
    row = reg.execute("SELECT email FROM users").fetchone()
    reg.close()
    assert row[0] == 'synthetic@test.local'


def test_restore_refuses_cross_product_backup():
    app_data = _make_app_data()
    dest = Path(tempfile.mkdtemp(prefix="aura_backup_dest_"))
    result = create_backup('retail', str(app_data), str(dest), '1.0.0')

    target = Path(tempfile.mkdtemp(prefix="aura_backup_target_"))
    (target / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
    with pytest.raises(BackupError, match='cross-product'):
        restore_backup('clinic', str(target), result['path'])


def test_restore_refuses_corrupted_member():
    app_data = _make_app_data()
    dest = Path(tempfile.mkdtemp(prefix="aura_backup_dest_"))
    result = create_backup('retail', str(app_data), str(dest), '1.0.0')

    # Tamper the archive: overwrite retail.db's bytes without updating the
    # manifest's recorded checksum.
    tampered_dir = Path(tempfile.mkdtemp(prefix="aura_backup_tamper_"))
    with zipfile.ZipFile(result['path']) as zf:
        zf.extractall(tampered_dir)
    with open(tampered_dir / 'retail.db', 'ab') as f:
        f.write(b'CORRUPTION')
    tampered_zip = tampered_dir / 'tampered.zip'
    with zipfile.ZipFile(tampered_zip, 'w') as zf:
        for name in ('manifest.json', 'registry.db', 'retail.db'):
            zf.write(tampered_dir / name, name)

    target = Path(tempfile.mkdtemp(prefix="aura_backup_target_"))
    (target / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
    with pytest.raises(BackupError, match='[Cc]hecksum'):
        restore_backup('retail', str(target), str(tampered_zip))


def test_restore_rejects_incompatible_schema_version():
    app_data = _make_app_data()
    dest = Path(tempfile.mkdtemp(prefix="aura_backup_dest_"))
    result = create_backup('retail', str(app_data), str(dest), '1.0.0')

    tampered_dir = Path(tempfile.mkdtemp(prefix="aura_backup_schema_"))
    with zipfile.ZipFile(result['path']) as zf:
        zf.extractall(tampered_dir)
    import json
    manifest_path = tampered_dir / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['schema_version'] = SCHEMA_VERSION + 999
    manifest_path.write_text(json.dumps(manifest))
    tampered_zip = tampered_dir / 'tampered.zip'
    with zipfile.ZipFile(tampered_zip, 'w') as zf:
        for name in ('manifest.json', 'registry.db', 'retail.db'):
            zf.write(tampered_dir / name, name)

    target = Path(tempfile.mkdtemp(prefix="aura_backup_target_"))
    (target / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
    with pytest.raises(BackupError, match='schema_version'):
        restore_backup('retail', str(target), str(tampered_zip))


def test_restore_preserves_rollback_copy_of_previous_live_database():
    app_data = _make_app_data()
    dest = Path(tempfile.mkdtemp(prefix="aura_backup_dest_"))
    result = create_backup('retail', str(app_data), str(dest), '1.0.0')

    target = Path(tempfile.mkdtemp(prefix="aura_backup_target_"))
    (target / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
    old_reg = sqlite3.connect(str(target / 'database' / 'registry.db'))
    old_reg.execute("CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT)")
    old_reg.execute("INSERT INTO users VALUES ('old-user','old-live-data@test.local')")
    old_reg.commit(); old_reg.close()

    restore_result = restore_backup('retail', str(target), result['path'])
    rollback_dir = Path(restore_result['rollback_dir'])
    assert rollback_dir.is_dir()

    preserved = sqlite3.connect(str(rollback_dir / 'registry.db'))
    row = preserved.execute("SELECT email FROM users").fetchone()
    preserved.close()
    assert row[0] == 'old-live-data@test.local'

    live = sqlite3.connect(str(target / 'database' / 'registry.db'))
    row = live.execute("SELECT email FROM users").fetchone()
    live.close()
    assert row[0] == 'synthetic@test.local'  # live db now reflects the restored backup


def test_create_backup_leaves_no_partial_file_when_source_db_missing():
    app_data = _make_app_data(with_data=False)  # no registry.db / retail.db on disk at all
    dest = Path(tempfile.mkdtemp(prefix="aura_backup_dest_"))
    with pytest.raises(BackupError):
        create_backup('retail', str(app_data), str(dest), '1.0.0')
    leftovers = list(dest.iterdir())
    assert leftovers == [], f"backup dir should be empty on failure, found: {leftovers}"


def test_is_safe_backup_dir_rejects_program_files():
    assert is_safe_backup_dir(r'C:\Program Files\AuraRetail\backups') is False
    assert is_safe_backup_dir(r'C:\Program Files (x86)\AuraRetail\backups') is False


def test_is_safe_backup_dir_accepts_ordinary_writable_dir(tmp_path):
    assert is_safe_backup_dir(str(tmp_path / 'aura-backups')) is True


# ── HTTP endpoint admin gating ──────────────────────────────────────────────

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_backup_http_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

import app as _app_module  # noqa: E402

_flask_app = _app_module.init_app()
_flask_app.config["TESTING"] = True


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def test_backup_create_endpoint_requires_authentication():
    client = _flask_app.test_client()
    r = client.post('/api/backup/create')
    assert r.status_code == 401


def test_backup_create_endpoint_requires_admin_role():
    from commercial_runtime.identity.registry_db import get_conn as registry_conn
    from commercial_runtime.security.passwords import hash_password
    email = f"backup-nonadmin-{uuid.uuid4().hex[:8]}@test.local"
    password = "NonAdminPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0002", email, hash_password(password), "employee", "active"),
    )
    conn.commit()
    conn.close()

    client = _flask_app.test_client()
    client.post('/api/auth/login', json={'email': email, 'password': password})
    r = client.post('/api/backup/create')
    assert r.status_code == 403


def test_restore_upload_filename_is_sanitized_against_path_traversal():
    """Regression: request.files['file'].filename is attacker-controlled and
    was previously interpolated unsanitized into the server-side save path
    -- a filename like "../../evil" must not escape the backup directory."""
    from commercial_runtime.identity.registry_db import get_conn as registry_conn
    from commercial_runtime.security.passwords import hash_password
    email = f"backup-admin-{uuid.uuid4().hex[:8]}@test.local"
    password = "AdminPW12345"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0003", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    client = _flask_app.test_client()
    client.post('/api/auth/login', json={'email': email, 'password': password})

    escape_target = DATA.parent / 'traversal-canary.zip'
    try:
        r = client.post('/api/backup/restore', data={
            'file': (io.BytesIO(b'not a real zip'), '../../../../traversal-canary.zip'),
        }, content_type='multipart/form-data')
        # Malformed zip content -> rejected, but the important assertion is
        # that nothing was ever written outside the backup directory.
        assert r.status_code in (400, 500)
        assert not escape_target.exists(), "upload must never be saved outside the backup directory"
    finally:
        if escape_target.exists():
            escape_target.unlink()
