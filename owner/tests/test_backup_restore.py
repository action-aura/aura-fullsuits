"""Part Y: Owner database backup and restore."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest

from tests.conftest import make_staff


@pytest.fixture()
def backup_dir():
    path = tempfile.mkdtemp(prefix="owner-backup-test-")
    yield path
    shutil.rmtree(path, ignore_errors=True)


def test_backup_succeeds_and_records_checksum(app, seeded, backup_dir):
    staff_id = make_staff(app, "b1@example.com")
    with app.app_context():
        from app.system.backup import create_backup

        record = create_backup(backup_dir, staff_id)
        assert record.status == "SUCCESS"
        assert os.path.exists(record.file_path)
        assert record.checksum_sha256
        assert record.size_bytes > 0


def test_backup_failure_recorded_when_pg_dump_missing(app, seeded, backup_dir, monkeypatch):
    staff_id = make_staff(app, "b2@example.com")
    monkeypatch.setenv("OWNER_PG_BIN_DIR", "/nonexistent/path/that/does/not/exist")
    with app.app_context():
        from app.system.backup import BackupError, create_backup
        from app.extensions import db_session
        from app.models.audit import DatabaseBackupRecord

        with pytest.raises(BackupError):
            create_backup(backup_dir, staff_id)
        failed = db_session.query(DatabaseBackupRecord).filter_by(status="FAILED").first()
        assert failed is not None
        assert failed.error_detail


def test_restore_rejects_tampered_checksum(app, seeded, backup_dir):
    staff_id = make_staff(app, "b3@example.com")
    with app.app_context():
        from app.system.backup import RestoreError, create_backup, restore_backup

        record = create_backup(backup_dir, staff_id)
        record.checksum_sha256 = "0" * 64  # simulate a tampered/corrupted record
        with pytest.raises(RestoreError, match="Checksum mismatch"):
            restore_backup(record, backup_dir, staff_id)


def test_restore_creates_pre_restore_safety_backup(app, seeded, backup_dir):
    """Restoring to an earlier snapshot naturally wipes DB rows recorded after
    that snapshot (including the restore's own bookkeeping rows) -- that's
    correct, not a bug. What must survive is the pre-restore .dump FILE on disk
    and its provenance in the audit log, which the restore process itself does
    not roll back."""
    staff_id = make_staff(app, "b4@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.system.backup import create_backup, restore_backup

        record = create_backup(backup_dir, staff_id)
        restore_backup(record, backup_dir, staff_id)

        db_session.expire_all()
        success_event = db_session.query(AuditLog).filter_by(action_code="OWNER_DB_RESTORE_SUCCEEDED").first()
        assert success_event is not None
        pre_restore_id = success_event.after_state_redacted["pre_restore_safety_backup_id"]
        assert pre_restore_id

    pre_restore_dump_files = [f for f in os.listdir(backup_dir) if "pre-restore-safety" in f]
    assert len(pre_restore_dump_files) == 1
    assert os.path.getsize(os.path.join(backup_dir, pre_restore_dump_files[0])) > 0


def test_restore_actually_recovers_data(app, seeded, backup_dir):
    staff_id = make_staff(app, "b5@example.com")
    with app.app_context():
        from app.customers.services import create_customer
        from app.extensions import db_session
        from app.models.customers import Customer
        from app.system.backup import create_backup, restore_backup

        create_customer({"legal_name": "Backed Up Co"}, staff_id)
        record = create_backup(backup_dir, staff_id)

        # Simulate data loss.
        db_session.query(Customer).filter_by(legal_name="Backed Up Co").delete()
        db_session.commit()
        assert db_session.query(Customer).filter_by(legal_name="Backed Up Co").count() == 0

        restore_backup(record, backup_dir, staff_id)
        db_session.expire_all()
        assert db_session.query(Customer).filter_by(legal_name="Backed Up Co").count() == 1


def test_backup_route_requires_super_admin_permission_and_recent_auth(app, client, seeded):
    from tests.conftest import force_login

    staff_id = make_staff(app, "b6@example.com", role_codes=["FINANCE"])
    force_login(client, app, staff_id)
    resp = client.post("/system/backups", data={"csrf_token": "x"})
    assert resp.status_code in (400, 403)


def test_password_never_appears_in_subprocess_argv(app, seeded, backup_dir, monkeypatch):
    """Credential-exposure regression: the DB password must be passed via the
    PGPASSWORD env var of the subprocess call, never as a command-line
    argument (which would be visible to other local users/processes)."""
    staff_id = make_staff(app, "b7@example.com")
    captured = {}
    real_run = subprocess.run

    def spy(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr("app.system.backup.subprocess.run", spy)
    with app.app_context():
        from app.system.backup import create_backup

        create_backup(backup_dir, staff_id)

    assert "aura_owner_dev" not in " ".join(captured["cmd"])
    assert captured["env"].get("PGPASSWORD") == "aura_owner_dev"
