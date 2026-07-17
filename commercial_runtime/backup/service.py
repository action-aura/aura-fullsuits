"""
Aura FullSuits -- local backup/restore foundation (Wave 0, AUDIT-019).

Offline-only. No network calls, no cloud upload, no scheduler. A backup is
one zip file containing:
  - registry.db   (shared multi-tenant identity/licensing db)
  - <product>.db  (the product's own subsystem db, e.g. retail.db/clinic.db)
  - manifest.json (product code, schema version, app version, timestamp,
                    per-file sha256 checksum + size)

Both source databases are copied via sqlite3's native backup API
(`Connection.backup()`), which takes an internal read lock and produces a
transactionally-consistent snapshot even while the source db is open for
writes (including WAL mode) -- unlike a raw file copy, which can capture a
torn/inconsistent image if a write is in flight or if `-wal`/`-shm` sidecar
files haven't been checkpointed yet.

Restore is destructive to the live databases by nature, so it always
preserves the pre-restore files (renamed, not deleted) before replacing them,
and validates product identity + checksums + SQLite integrity before
touching anything on disk.
"""
import os
import io
import json
import sqlite3
import hashlib
import zipfile
import tempfile
import shutil
from datetime import datetime, timezone

# Bump only on a breaking change to either db's schema shape. Wave 0 has a
# single supported shape, so restore requires an exact match.
SCHEMA_VERSION = 1

SUPPORTED_PRODUCTS = ('retail', 'clinic')

MANIFEST_NAME = 'manifest.json'
REGISTRY_DB_NAME = 'registry.db'


class BackupError(Exception):
    """Raised for any backup/restore failure. Message is safe to show/log."""


def _utc_stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def is_safe_backup_dir(path):
    """
    Reject directories that resolve inside a Windows "Program Files" tree
    (the app's own install location, which is not guaranteed writable and
    must never silently receive customer backups) or that don't exist as a
    directory. Callers are expected to let the operator choose the backup
    directory explicitly.
    """
    resolved = os.path.abspath(path).lower()
    for bad in ('\\program files\\', '\\program files (x86)\\'):
        if bad in (resolved + '\\'):
            return False
    return True


def _product_db_paths(app_data_dir):
    """Both source db paths, matching database/schema.py's own layout
    (<app_data>/database/registry.db, <app_data>/database/subsystems/<name>.db)."""
    database_dir = os.path.join(app_data_dir, 'database')
    return {
        'registry': os.path.join(database_dir, REGISTRY_DB_NAME),
        'subsystems_dir': os.path.join(database_dir, 'subsystems'),
    }


def _snapshot_db(source_path, dest_path):
    """Consistent point-in-time copy of a live SQLite db via the native
    backup API, never a raw file copy."""
    if not os.path.exists(source_path):
        raise BackupError(f"Source database not found: {os.path.basename(source_path)}")
    src = sqlite3.connect(source_path, timeout=30)
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def create_backup(product_code, app_data_dir, dest_dir, app_version):
    """
    Build one backup archive for `product_code` ('retail' or 'clinic') from
    the live databases under `app_data_dir`, written into `dest_dir`.

    Returns {'path': <final zip path>, 'manifest': {...}} on success.
    Raises BackupError on any failure; never leaves a partial file at the
    final destination (written to a temp path first, validated, then moved
    into place with an atomic rename).
    """
    if product_code not in SUPPORTED_PRODUCTS:
        raise BackupError(f"Unknown product_code: {product_code}")
    if not is_safe_backup_dir(dest_dir):
        raise BackupError("Refusing to write a backup inside a Program Files directory")
    os.makedirs(dest_dir, exist_ok=True)

    paths = _product_db_paths(app_data_dir)
    registry_src = paths['registry']
    product_src = os.path.join(paths['subsystems_dir'], f'{product_code}.db')

    stamp = _utc_stamp()
    final_name = f'aura-{product_code}-backup-{stamp}-app{app_version}-schema{SCHEMA_VERSION}.aurabak.zip'
    final_path = os.path.join(dest_dir, final_name)

    with tempfile.TemporaryDirectory(prefix='aura-backup-') as tmp:
        registry_snap = os.path.join(tmp, REGISTRY_DB_NAME)
        product_snap = os.path.join(tmp, f'{product_code}.db')
        try:
            _snapshot_db(registry_src, registry_snap)
            _snapshot_db(product_src, product_snap)
        except sqlite3.DatabaseError as e:
            raise BackupError(f"Failed to snapshot source database: {e}") from e

        files_meta = []
        for name, snap_path in ((REGISTRY_DB_NAME, registry_snap), (f'{product_code}.db', product_snap)):
            files_meta.append({
                'name': name,
                'size': os.path.getsize(snap_path),
                'sha256': _sha256_file(snap_path),
            })

        manifest = {
            'format_version': 1,
            'product_code': product_code,
            'schema_version': SCHEMA_VERSION,
            'app_version': app_version,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'files': files_meta,
        }
        manifest_path = os.path.join(tmp, MANIFEST_NAME)
        with open(manifest_path, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2)

        tmp_zip_path = os.path.join(tmp, 'staging.zip')
        with zipfile.ZipFile(tmp_zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.write(manifest_path, MANIFEST_NAME)
            zf.write(registry_snap, REGISTRY_DB_NAME)
            zf.write(product_snap, f'{product_code}.db')

        # Validate the archive we just wrote before it is ever presented as
        # a successful backup -- a corrupt/partial zip must never be moved
        # into the final destination.
        with zipfile.ZipFile(tmp_zip_path, 'r') as zf:
            bad = zf.testzip()
            if bad is not None:
                raise BackupError(f"Backup archive failed integrity check: {bad}")

        tmp_final = final_path + '.partial'
        shutil.copyfile(tmp_zip_path, tmp_final)
        os.replace(tmp_final, final_path)  # atomic on the same filesystem

    return {'path': final_path, 'manifest': manifest}


def _read_manifest(zf):
    try:
        raw = zf.read(MANIFEST_NAME)
    except KeyError:
        raise BackupError("Backup archive is missing manifest.json")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BackupError(f"Backup manifest is not valid JSON: {e}") from e
    required = ('product_code', 'schema_version', 'app_version', 'created_at', 'files')
    missing = [k for k in required if k not in manifest]
    if missing:
        raise BackupError(f"Backup manifest is missing required field(s): {', '.join(missing)}")
    return manifest


def _integrity_check(db_path):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute('PRAGMA integrity_check').fetchone()
        if not row or row[0] != 'ok':
            raise BackupError(f"Database failed PRAGMA integrity_check: {row}")
    finally:
        conn.close()


def restore_backup(product_code, app_data_dir, backup_zip_path):
    """
    Restore `backup_zip_path` over the live databases under `app_data_dir`.
    Refuses to restore a backup for a different product. The caller is
    responsible for ensuring the application is not concurrently writing to
    the target databases during this call (Wave 0 scope: manual, offline,
    single-user operation -- no in-process locking of other processes).

    Returns {'restored': [...], 'rollback_dir': <path>} on success.
    Raises BackupError before touching any live file if validation fails.
    """
    if product_code not in SUPPORTED_PRODUCTS:
        raise BackupError(f"Unknown product_code: {product_code}")
    if not os.path.isfile(backup_zip_path):
        raise BackupError(f"Backup file not found: {backup_zip_path}")

    try:
        zf = zipfile.ZipFile(backup_zip_path, 'r')
    except zipfile.BadZipFile as e:
        raise BackupError(f"Backup file is not a valid zip archive: {e}") from e

    with zf:
        if zf.testzip() is not None:
            raise BackupError("Backup archive failed integrity check (corrupt member)")

        manifest = _read_manifest(zf)

        if manifest['product_code'] != product_code:
            raise BackupError(
                f"Refusing cross-product restore: backup is for "
                f"'{manifest['product_code']}', not '{product_code}'"
            )
        if manifest['schema_version'] != SCHEMA_VERSION:
            raise BackupError(
                f"Backup schema_version {manifest['schema_version']} is not compatible "
                f"with this build's supported schema_version {SCHEMA_VERSION}"
            )

        expected_names = {REGISTRY_DB_NAME, f'{product_code}.db'}
        manifest_names = {entry['name'] for entry in manifest['files']}
        if manifest_names != expected_names:
            raise BackupError("Backup manifest file list does not match expected database set")

        with tempfile.TemporaryDirectory(prefix='aura-restore-') as tmp:
            extracted = {}
            for entry in manifest['files']:
                name = entry['name']
                try:
                    data = zf.read(name)
                except KeyError:
                    raise BackupError(f"Backup archive is missing declared file: {name}")
                actual_sha256 = hashlib.sha256(data).hexdigest()
                if actual_sha256 != entry['sha256']:
                    raise BackupError(f"Checksum mismatch for {name}: backup file is corrupted")
                if len(data) != entry['size']:
                    raise BackupError(f"Size mismatch for {name}: backup file is corrupted")
                out_path = os.path.join(tmp, name)
                with open(out_path, 'wb') as f:
                    f.write(data)
                extracted[name] = out_path

            # SQLite readability + logical integrity, not just checksum validity.
            for name, path in extracted.items():
                try:
                    _integrity_check(path)
                except sqlite3.DatabaseError as e:
                    raise BackupError(f"{name} is not a readable SQLite database: {e}") from e

            paths = _product_db_paths(app_data_dir)
            os.makedirs(paths['subsystems_dir'], exist_ok=True)
            live_registry = paths['registry']
            live_product = os.path.join(paths['subsystems_dir'], f'{product_code}.db')

            rollback_dir = os.path.join(
                os.path.dirname(live_registry), 'pre-restore-' + _utc_stamp()
            )
            os.makedirs(rollback_dir, exist_ok=True)

            # Preserve whatever currently exists (may be absent on a clean
            # install) before touching anything, so a failed replace can be
            # undone.
            preserved = []
            try:
                for live_path, name in ((live_registry, REGISTRY_DB_NAME), (live_product, f'{product_code}.db')):
                    if os.path.exists(live_path):
                        dest = os.path.join(rollback_dir, name)
                        shutil.copy2(live_path, dest)
                        preserved.append((live_path, name))
                    for sidecar_suffix in ('-wal', '-shm'):
                        sidecar = live_path + sidecar_suffix
                        if os.path.exists(sidecar):
                            os.remove(sidecar)

                for live_path, name in ((live_registry, REGISTRY_DB_NAME), (live_product, f'{product_code}.db')):
                    os.replace(extracted[name], live_path)
            except OSError as e:
                # Best-effort rollback of anything already replaced.
                for live_path, name in preserved:
                    saved = os.path.join(rollback_dir, name)
                    if os.path.exists(saved):
                        try:
                            shutil.copy2(saved, live_path)
                        except OSError:
                            pass
                raise BackupError(f"Restore failed while replacing live databases: {e}") from e

    return {'restored': sorted(expected_names), 'rollback_dir': rollback_dir}
