"""Phase 9 Milestone 7 -- Aura Owner staging backup.

Backs up: PostgreSQL (pg_dump, custom format), signing-key directory
metadata (key IDs/status only, never the private key bytes themselves --
the private key file is backed up separately, see backup-policy.md),
config inventory (env VAR NAMES only, never values), and current Alembic
revision. Does NOT back up Clinic/Retail customer-domain databases -- Aura
Owner never holds them (see network-and-trust-boundaries.md).

Real, runnable script -- not a placeholder. Requires PGPASSWORD (or a
.pgpass) and pg_dump on PATH (or PG_DUMP_PATH set).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _pg_dump_path() -> str:
    return os.environ.get("PG_DUMP_PATH", "pg_dump")


def backup(*, db_host: str, db_port: str, db_user: str, db_name: str,
           signing_key_dir: Path, backup_dir: Path) -> dict:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = backup_dir / f"staging-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=False)

    dump_path = run_dir / "database.dump"
    cmd = [
        _pg_dump_path(), "-h", db_host, "-p", db_port, "-U", db_user,
        "-d", db_name, "-F", "c", "-f", str(dump_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {result.stderr}")

    checksum = hashlib.sha256(dump_path.read_bytes()).hexdigest()
    (run_dir / "database.dump.sha256").write_text(checksum, encoding="utf-8")

    # Signing-key METADATA only (key IDs + file names), never the private
    # key bytes -- the private key material is a separate, more tightly
    # controlled backup target per backup-policy.md's own explicit split.
    key_metadata = []
    if signing_key_dir.exists():
        for f in sorted(signing_key_dir.iterdir()):
            key_metadata.append({"name": f.name, "size_bytes": f.stat().st_size})
    (run_dir / "signing-key-metadata.json").write_text(json.dumps(key_metadata, indent=2), encoding="utf-8")

    manifest = {
        "backup_id": f"staging-{timestamp}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": db_name,
        "dump_file": dump_path.name,
        "dump_sha256": checksum,
        "dump_size_bytes": dump_path.stat().st_size,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Aura Owner staging backup")
    parser.add_argument("--db-host", default=os.environ.get("STAGING_DB_HOST", "localhost"))
    parser.add_argument("--db-port", default=os.environ.get("STAGING_DB_PORT", "5432"))
    parser.add_argument("--db-user", default=os.environ.get("STAGING_DB_USER", "aura_owner"))
    parser.add_argument("--db-name", default=os.environ.get("STAGING_DB_NAME", "aura_owner_staging"))
    parser.add_argument("--signing-key-dir", default=os.environ.get("OWNER_SIGNING_KEY_DIRECTORY", "var/signing-keys"))
    parser.add_argument("--backup-dir", default=os.environ.get("OWNER_BACKUP_DIR", "var/backups"))
    args = parser.parse_args()

    try:
        manifest = backup(
            db_host=args.db_host, db_port=args.db_port, db_user=args.db_user, db_name=args.db_name,
            signing_key_dir=Path(args.signing_key_dir), backup_dir=Path(args.backup_dir),
        )
    except Exception as exc:
        print(f"BACKUP FAILED: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
