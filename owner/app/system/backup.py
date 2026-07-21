"""Owner PostgreSQL database backup and recovery (Part Y).

Separate from Retail/Clinic local SQLite backups (commercial_runtime/backup) --
this backs up Owner's own PostgreSQL database only, via pg_dump/pg_restore.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone

from app.audit.services import record as audit_record
from app.extensions import db_session, get_engine
from app.models.audit import DatabaseBackupRecord

APP_VERSION = "phase5-foundation"
SCHEMA_REVISION_UNKNOWN = "unknown"


class BackupError(RuntimeError):
    pass


class RestoreError(RuntimeError):
    pass


def _pg_bin(tool: str) -> str:
    bin_dir = os.environ.get("OWNER_PG_BIN_DIR", "")
    return os.path.join(bin_dir, tool) if bin_dir else tool


def _libpq_url() -> str:
    """pg_dump/pg_restore don't understand the '+psycopg' SQLAlchemy dialect
    suffix -- strip it down to a plain libpq-compatible URL. Must render with
    the real password: str(engine.url) masks it as '***' by design."""
    uri = get_engine().url.render_as_string(hide_password=False)
    return uri.replace("postgresql+psycopg://", "postgresql://")


def _sha256_of_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _current_schema_revision() -> str:
    with get_engine().connect() as conn:
        result = conn.exec_driver_sql("SELECT version_num FROM alembic_version").first()
        return result[0] if result else SCHEMA_REVISION_UNKNOWN


def create_backup(backup_dir: str, initiated_by_staff_user_id, label: str = "manual") -> DatabaseBackupRecord:
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"aura-owner-backup-{label}-{timestamp}.dump"
    file_path = os.path.join(backup_dir, filename)

    cmd = [_pg_bin("pg_dump"), "--format=custom", "--file", file_path, _libpq_url()]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        record = DatabaseBackupRecord(
            file_path=file_path, owner_app_version=APP_VERSION, schema_revision=_current_schema_revision(),
            checksum_sha256="", status="FAILED", initiated_by_staff_user_id=initiated_by_staff_user_id, error_detail=str(exc),
        )
        db_session.add(record)
        db_session.commit()
        audit_record(
            actor_staff_user_id=initiated_by_staff_user_id, actor_role_snapshot=None, action_code="OWNER_DB_BACKUP_FAILED",
            entity_type="database_backup", entity_public_id=str(record.id), result="FAILURE", reason=str(exc),
        )
        raise BackupError(str(exc)) from exc

    if result.returncode != 0 or not os.path.exists(file_path):
        record = DatabaseBackupRecord(
            file_path=file_path, owner_app_version=APP_VERSION, schema_revision=_current_schema_revision(),
            checksum_sha256="", status="FAILED", initiated_by_staff_user_id=initiated_by_staff_user_id,
            error_detail=(result.stderr or "pg_dump failed")[:2000],
        )
        db_session.add(record)
        db_session.commit()
        audit_record(
            actor_staff_user_id=initiated_by_staff_user_id, actor_role_snapshot=None, action_code="OWNER_DB_BACKUP_FAILED",
            entity_type="database_backup", entity_public_id=str(record.id), result="FAILURE", reason=record.error_detail,
        )
        raise BackupError(record.error_detail)

    checksum = _sha256_of_file(file_path)
    record = DatabaseBackupRecord(
        file_path=file_path, owner_app_version=APP_VERSION, schema_revision=_current_schema_revision(),
        checksum_sha256=checksum, size_bytes=os.path.getsize(file_path), status="SUCCESS",
        initiated_by_staff_user_id=initiated_by_staff_user_id,
    )
    db_session.add(record)
    db_session.commit()
    audit_record(
        actor_staff_user_id=initiated_by_staff_user_id, actor_role_snapshot=None, action_code="OWNER_DB_BACKUP_SUCCEEDED",
        entity_type="database_backup", entity_public_id=str(record.id),
        after_state={"file_path": file_path, "checksum_sha256": checksum, "size_bytes": record.size_bytes},
    )
    return record


def restore_backup(backup_record: DatabaseBackupRecord, backup_dir: str, initiated_by_staff_user_id) -> DatabaseBackupRecord:
    """Verifies checksum, takes a pre-restore safety backup, then restores.
    Any failure at the checksum step aborts before touching the live database."""
    if not os.path.exists(backup_record.file_path):
        raise RestoreError(f"Backup file not found: {backup_record.file_path}")
    actual_checksum = _sha256_of_file(backup_record.file_path)
    if actual_checksum != backup_record.checksum_sha256:
        audit_record(
            actor_staff_user_id=initiated_by_staff_user_id, actor_role_snapshot=None, action_code="OWNER_DB_RESTORE_REJECTED",
            entity_type="database_backup", entity_public_id=str(backup_record.id), result="FAILURE",
            reason="checksum mismatch -- refusing to restore a modified or corrupted backup file",
        )
        raise RestoreError("Checksum mismatch -- refusing to restore a modified or corrupted backup file.")

    # Pre-restore safety backup -- preserves current state before anything is touched (Part Y).
    pre_restore = create_backup(backup_dir, initiated_by_staff_user_id, label="pre-restore-safety")

    # Capture plain values now -- both ORM objects are about to be detached.
    backup_record_id = str(backup_record.id)
    backup_record_file_path = backup_record.file_path
    pre_restore_id = str(pre_restore.id)

    # pg_restore's --clean issues DROP TABLE (ACCESS EXCLUSIVE locks). Our own
    # app connection, if left open, holds weaker locks from prior reads in this
    # same not-yet-committed transaction that would block those DROPs forever.
    # End our transaction and return the connection to the pool before restoring.
    db_session.commit()
    db_session.remove()
    get_engine().dispose()

    cmd = [
        _pg_bin("pg_restore"), "--clean", "--if-exists", "--no-owner", "--dbname", _libpq_url(), backup_record_file_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        audit_record(
            actor_staff_user_id=initiated_by_staff_user_id, actor_role_snapshot=None, action_code="OWNER_DB_RESTORE_FAILED",
            entity_type="database_backup", entity_public_id=backup_record_id, result="FAILURE",
            reason=(result.stderr or "pg_restore failed")[:2000],
            after_state={"pre_restore_safety_backup_id": pre_restore_id},
        )
        raise RestoreError((result.stderr or "pg_restore failed")[:2000])

    audit_record(
        actor_staff_user_id=initiated_by_staff_user_id, actor_role_snapshot=None, action_code="OWNER_DB_RESTORE_SUCCEEDED",
        entity_type="database_backup", entity_public_id=backup_record_id,
        after_state={"pre_restore_safety_backup_id": pre_restore_id},
    )
    return db_session.get(DatabaseBackupRecord, backup_record_id)
