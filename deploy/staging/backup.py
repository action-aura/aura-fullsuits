"""Phase 9 Milestone 7 -- Aura Owner staging backup (scheduler-triggered).

REVISED after discovering owner/app/system/backup.py already exists (Part
Y) and is more mature than this script's original standalone pg_dump
implementation: it resolves pg_dump/pg_restore correctly (including the
real Windows PostgreSQL install path), never puts the password in argv
(PGPASSWORD env only), redacts credential leakage from error text, computes
the SHA-256 checksum, and records a DatabaseBackupRecord (audit-linked) for
every attempt -- success or failure. Re-implementing any of that here would
have been worse, duplicated code. This script is now a thin real wrapper:
a real Flask app context + a call to the real create_backup(), for use by
the scheduler (Milestone 9) / systemd timer, where there is no logged-in
staff member to be the "initiated_by" actor -- passes None, the same
system-initiated pattern already used elsewhere in this codebase (e.g.
Phase 8's automated scans record events with no human actor).

Adds one thing the in-app function does not: signing-key directory
METADATA capture (file names/sizes only, never key bytes) alongside the
database backup, per backup-policy.md's split between DB backup and
key-material backup.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_OWNER_DIR = str(Path(__file__).resolve().parents[2] / "owner")
if _OWNER_DIR not in sys.path:
    sys.path.insert(0, _OWNER_DIR)


def _signing_key_metadata(signing_key_dir: Path) -> list[dict]:
    if not signing_key_dir.exists():
        return []
    return [{"name": f.name, "size_bytes": f.stat().st_size} for f in sorted(signing_key_dir.iterdir())]


def main() -> int:
    parser = argparse.ArgumentParser(description="Aura Owner staging backup (scheduler-triggered)")
    parser.add_argument("--backup-dir", default=os.environ.get("OWNER_BACKUP_DIR", "var/backups"))
    parser.add_argument("--signing-key-dir", default=os.environ.get("OWNER_SIGNING_KEY_DIRECTORY", "var/signing-keys"))
    parser.add_argument("--label", default="scheduled")
    args = parser.parse_args()

    from app import create_app
    from app.system.backup import BackupError, create_backup

    app = create_app(os.environ.get("OWNER_ENV", "staging"))
    with app.app_context():
        try:
            record = create_backup(args.backup_dir, initiated_by_staff_user_id=None, label=args.label)
        except BackupError as exc:
            print(json.dumps({"status": "FAILED", "error": str(exc)}), file=sys.stderr)
            return 1

        key_metadata = _signing_key_metadata(Path(args.signing_key_dir))
        manifest_path = Path(args.backup_dir) / f"{Path(record.file_path).stem}.signing-key-metadata.json"
        manifest_path.write_text(json.dumps(key_metadata, indent=2), encoding="utf-8")

        print(json.dumps({
            "status": "OK",
            "backup_id": str(record.id),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "file_path": record.file_path,
            "checksum_sha256": record.checksum_sha256,
            "size_bytes": record.size_bytes,
            "signing_key_metadata_file": str(manifest_path),
        }, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
