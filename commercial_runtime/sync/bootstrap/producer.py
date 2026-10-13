"""Builds a bootstrap copy of a live product database.

THE CONTRACT with the device that installs the result: "this database is the
shop as of relay position `as_of`; pull from there and you have everything,
once, with nothing missing and nothing twice."

WHY `as_of` COMES FROM THE COPY AND NOWHERE ELSE. The source database is live: a
sale lands, a pull applies a batch, the cursor moves. If `as_of` were read from
the source a moment after (or before) the copy was taken, it could name a
position the copy does not actually contain -- a copy missing events it claims
to hold (silent loss, forever) or holding events it will be sent again
(double-applied). So the copy is taken in ONE read transaction (`VACUUM INTO`
is a single consistent snapshot, and the fallback is SQLite's backup API, also a
point-in-time image), and `as_of` is read out of the COPY's own `sync_cursor`.
Whatever the cursor says is, by construction, exactly what that copy applied --
events parked in `sync_apply_quarantine` travel with it (a SYNCED table), so a
parked event is neither lost nor re-applied without its parking row.

SCRUB. Each table is handled by its class (`classes.py`): AUTHORITY and the
LOCAL_* classes are emptied; SYNCED/DERIVED stay. `secure_delete` is ON and the
copy is VACUUMed afterwards, because a DELETE alone leaves the deleted bytes in
free pages of the file -- the producer's e-invoice credentials, its hub's site
log, its pin lockouts would all still be readable in the zip. A test greps the
shipped file's raw bytes for a planted secret.

SIGNING. The manifest is wrapped as an event and signed with the SAME `_auth`
envelope authority events use (`authority.sign_event`), so the installer
verifies it with the code that already decides who is a member of this licence.
A copy nobody in the shop vouches for is refused. The manifest carries the
database's sha256, so the signature covers the data without signing megabytes.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from commercial_runtime.sync import authority
from commercial_runtime.sync.bootstrap import classes, errors
from commercial_runtime.sync.bootstrap.errors import BootstrapError

logger = logging.getLogger(__name__)

PACKAGE_FORMAT = "aura-bootstrap/1"
MANIFEST_NAME = "manifest.json"
#: Entity type of the signed manifest envelope. Not an authority entity type
#: (those are judged by the sync engine's rules); it only borrows the envelope.
MANIFEST_ENTITY_TYPE = "bootstrap_manifest"


@dataclass(frozen=True)
class BuiltBootstrap:
    data: bytes                 # the zip
    manifest: dict              # the unsigned payload (as_of, company_id, ...)
    as_of: int


@dataclass(frozen=True)
class BootstrapOffer:
    """What a hub's `/bootstrap` route hands back: the zip, the relay position it
    is complete up to, and where the device's REGISTRY stream should resume (the
    log's end at the time -- the main computer's fresh re-announce lands after it)."""
    data: bytes
    as_of: int
    registry_fast_forward: int


def _vacuum_into(source_path: str, copy_path: str) -> None:
    src = sqlite3.connect(source_path, timeout=30)
    try:
        src.execute("VACUUM INTO ?", (copy_path,))
    finally:
        src.close()


def take_snapshot(source_path: str, copy_path: str) -> str:
    """A consistent copy of `source_path` at `copy_path`. `VACUUM INTO` first
    (one read transaction, compacted); the backup module's snapshot (SQLite's
    online backup API, also point-in-time) if that is unavailable or fails.
    Returns which method produced the copy. Never a raw file copy: a WAL database
    copied byte-for-byte can be torn."""
    try:
        _vacuum_into(source_path, copy_path)
        return "vacuum_into"
    except sqlite3.Error as exc:
        logger.info("bootstrap: VACUUM INTO unavailable (%s); using the backup snapshot", exc)
        try:
            if os.path.exists(copy_path):
                os.remove(copy_path)
        except OSError:
            pass
    from commercial_runtime.backup import service as backup_service
    try:
        backup_service._snapshot_db(source_path, copy_path)
    except (backup_service.BackupError, sqlite3.Error) as exc:
        raise BootstrapError(errors.PRODUCER_FAILED, f"could not snapshot the database: {exc}") from exc
    return "backup_snapshot"


def _scrub(conn: sqlite3.Connection, spec: classes.BootstrapSpec) -> dict:
    """Empty every table (or row) the copy must not carry. Returns
    {table: rows_removed} for the manifest's audit trail."""
    conn.execute("PRAGMA secure_delete = ON")
    removed = {}
    for table in classes.user_tables(conn):
        cls = spec.class_of(table)
        if cls is None:
            raise BootstrapError(
                errors.UNCLASSIFIED_TABLE,
                f"table {table!r} has no bootstrap class, so it cannot be copied safely")
        if cls in classes.KEEP_IN_COPY:
            continue
        if cls == classes.ROW_LEVEL:
            rule = spec.row_rules[table]
            doomed = [
                r[0] for r in conn.execute(f'SELECT rowid, "{rule.key_column}" FROM "{table}"').fetchall()
                if rule.class_of_key(r[1]) not in classes.KEEP_IN_COPY
            ]
            for start in range(0, len(doomed), 500):
                chunk = doomed[start:start + 500]
                conn.execute(
                    f'DELETE FROM "{table}" WHERE rowid IN ({",".join("?" * len(chunk))})', chunk)
            if doomed:
                removed[table] = len(doomed)
            continue
        cur = conn.execute(f'DELETE FROM "{table}"')
        if cur.rowcount:
            removed[table] = cur.rowcount
    return removed


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_bootstrap(
    *,
    spec: classes.BootstrapSpec,
    source_path: str,
    company_id: str,
    signer: Optional[authority.LocalSigner],
    expected_relay_url: Optional[str] = None,
    requester: Optional[str] = None,
    stream: str = "retail",
) -> BuiltBootstrap:
    """See the module docstring. `expected_relay_url`: the relay whose sequence
    space `as_of` must be in; the copy's own cursor must be bound to it, or the
    number would mean nothing to the requester (a hub's position in its OWN site
    log is not a position in Owner's)."""
    if signer is None or signer.sign_fn is None:
        raise BootstrapError(errors.NO_SIGNER, "this computer cannot sign a bootstrap copy")
    with tempfile.TemporaryDirectory(prefix="aura-bootstrap-") as tmp:
        copy_path = os.path.join(tmp, spec.db_name)
        method = take_snapshot(source_path, copy_path)
        conn = sqlite3.connect(copy_path)
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f'SELECT last_seq, relay_url FROM "{spec.cursor_table}" WHERE id = 1').fetchone()
            if row is None:
                raise BootstrapError(errors.PRODUCER_FAILED, "the copy has no sync cursor row")
            as_of = int(row["last_seq"])
            if expected_relay_url is not None and row["relay_url"] != expected_relay_url:
                raise BootstrapError(
                    errors.PRODUCER_NOT_ON_RELAY,
                    f"this computer's cursor belongs to relay {row['relay_url']!r}, not {expected_relay_url!r}")
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            removed = _scrub(conn, spec)
            conn.commit()
            conn.execute("VACUUM")          # free pages would still hold the scrubbed bytes
            conn.execute("PRAGMA journal_mode = DELETE")
            problem = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if problem != "ok":
                raise BootstrapError(errors.PRODUCER_FAILED, f"the copy failed integrity_check: {problem}")
        finally:
            conn.close()

        bootstrap_id = str(uuid.uuid4())
        manifest = {
            "format": PACKAGE_FORMAT,
            "product": spec.product,
            "bootstrap_id": bootstrap_id,
            "company_id": str(company_id),
            "schema_version": schema_version,
            "as_of": as_of,
            "stream": stream,
            "source_relay_url": row["relay_url"],
            "db_name": spec.db_name,
            "db_sha256": _sha256_file(copy_path),
            "db_size": os.path.getsize(copy_path),
            "requester": requester,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "snapshot_method": method,
            "scrubbed_rows": removed,
        }
        envelope = authority.sign_event(
            {"entity_type": MANIFEST_ENTITY_TYPE, "entity_id": bootstrap_id,
             "event_type": "create", "payload": manifest},
            signer)
        zip_path = os.path.join(tmp, "package.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST_NAME, json.dumps(envelope, sort_keys=True))
            zf.write(copy_path, spec.db_name)
        with open(zip_path, "rb") as f:
            data = f.read()
    return BuiltBootstrap(data=data, manifest=manifest, as_of=as_of)
