"""Verifies, stages and installs a bootstrap copy on the device that asked for it.

TWO STEPS, because the database cannot be swapped under a running app:

  `stage(...)`        -- runs inside the live app. Verifies the package, refuses
                         it if it is wrong, migrates it, and parks it under
                         `<app data>/device/` with a `bootstrap_pending.json`.
                         Touches nothing live.
  `apply_staged(...)` -- runs at the NEXT START, before any database is opened
                         (app.py's boot seam). Carries this device's own rows
                         across, rewrites the cursor, and swaps the file in with
                         `restore_backup`'s discipline: the old database is
                         copied to a rollback directory first, `-wal`/`-shm`
                         sidecars are removed (a stale WAL replayed onto a
                         different database file corrupts it), and the swap is
                         `os.replace`.

WHAT IS REFUSED, and why each is a refusal and not a warning:
  * a manifest nobody in this licence signed (a copy from a stranger would hand
    this computer a whole shop of invented data);
  * a different company's copy (`COMPANY_MISMATCH`: the wrong shop's books);
  * a copy from a NEWER schema (this build cannot read columns it has never
    heard of; installing it would be the silent-column-drop the schema-skew
    guard exists to prevent). An OLDER copy is migrated forward first;
  * an OPEN cash session (`OPEN_CASH_SESSION`): the swap replaces the database,
    and a drawer that is mid-count would lose money records that exist nowhere
    else. Checked at stage time AND again at apply time -- the drawer may have
    been opened in between.

WHAT SURVIVES THE SWAP, from this device's OLD database (classes.py):
  LOCAL_CARRY / BLOCK_IF_ROWS tables and the LOCAL_CARRY rows of ROW_LEVEL tables
  (numbering counters, PIN lockouts, e-invoice credentials, its hub bookkeeping),
  and its UNPUSHED outbox rows. The outbox rows are put back in the new
  `sync_outbox` (so they still reach the relay) and flagged `reapply_outbox` in
  the marker: the copy was taken before they existed, so the device must
  re-apply its own pending writes to the new database or it would lose, locally,
  what it already told the shop it did. (Pushing them is not enough -- the relay
  excludes a device's own events from its pulls.)

THE MARKER `<app data>/device/sync_bootstrap.json` carries what the first
pulls after the install must do once: `include_own_once` (ask the relay for this
device's OWN events too -- see `SyncService`), `reapply_outbox`. Same shape and
reason as `reannounce.py`'s marker: a small device-local file, never synced, no
schema bump.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from commercial_runtime.sync import authority
from commercial_runtime.sync.bootstrap import classes, errors
from commercial_runtime.sync.bootstrap.errors import BootstrapError
from commercial_runtime.sync.bootstrap.producer import MANIFEST_NAME, PACKAGE_FORMAT

logger = logging.getLogger(__name__)

MARKER_NAME = "sync_bootstrap.json"
PENDING_NAME = "bootstrap_pending.json"
STAGING_DIRNAME = "bootstrap_staging"
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024   # a shop database larger than this is not a "copy to a new computer"


# ── the marker (include_own_once / reapply_outbox) ──────────────────────────

def marker_path(app_data_dir) -> Path:
    return Path(app_data_dir) / "device" / MARKER_NAME


def read_marker(app_data_dir) -> dict:
    """The marker, or {}. An unreadable file reads as empty: the worst outcome
    is one flag not firing, which the next restore/bootstrap sets again."""
    try:
        raw = json.loads(marker_path(app_data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_marker(app_data_dir, marker: dict) -> None:
    path = marker_path(app_data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(marker, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


ALL_STREAMS = ("retail", "registry")


def mark_include_own_once(app_data_dir, reason: str, streams=ALL_STREAMS) -> None:
    """Arms `include_own_once` for `streams`. Called after a bootstrap install
    (retail only: the registry database is never swapped), and after a
    same-installation backup restore (both streams: `restore_backup` replaces
    registry.db AND retail.db, and the relay's self-exclusion then loses whatever
    this device pushed after the backup was taken -- ROADMAP SYNC-BOOT)."""
    marker = read_marker(app_data_dir)
    armed = {str(x) for x in marker.get("include_own_once", []) if isinstance(marker.get("include_own_once"), list)}
    armed.update(streams)
    marker["include_own_once"] = sorted(armed)
    marker["include_own_reason"] = reason
    write_marker(app_data_dir, marker)


def include_own_once_pending(app_data_dir, stream: str = "retail") -> bool:
    armed = read_marker(app_data_dir).get("include_own_once")
    return isinstance(armed, list) and stream in armed


def clear_include_own_once(app_data_dir, stream: str = "retail") -> None:
    marker = read_marker(app_data_dir)
    armed = marker.get("include_own_once")
    if not isinstance(armed, list) or stream not in armed:
        return
    left = [x for x in armed if x != stream]
    if left:
        marker["include_own_once"] = left
    else:
        marker.pop("include_own_once", None)
        marker.pop("include_own_reason", None)
    write_marker(app_data_dir, marker)


def reapply_outbox_pending(app_data_dir) -> bool:
    return read_marker(app_data_dir).get("reapply_outbox") is True


def clear_reapply_outbox(app_data_dir) -> None:
    marker = read_marker(app_data_dir)
    if marker.pop("reapply_outbox", None) is not None:
        write_marker(app_data_dir, marker)


# ── verification ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VerifiedPackage:
    """A package that passed every check. Either the database is held in memory (`db_bytes`, the
    bytes path every desktop transport uses) or it is still INSIDE the zip on disk (`zip_path`, the
    Android path: a phone must not hold a whole shop in RAM) -- [write_db] copies it out either way."""
    manifest: dict
    db_bytes: Optional[bytes] = None
    zip_path: Optional[str] = None
    db_member: str = ""

    def write_db(self, dest: Path) -> None:
        if self.db_bytes is not None:
            Path(dest).write_bytes(self.db_bytes)
            return
        with zipfile.ZipFile(self.zip_path, "r") as zf, zf.open(self.db_member) as src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out, 1024 * 1024)


def verify_package(
    data,
    *,
    spec: classes.BootstrapSpec,
    trust_store,
    own_licence: Optional[str],
    expected_company_id: Optional[str],
) -> VerifiedPackage:
    """Everything that can be checked WITHOUT touching this device: shape, the
    signature, the shop, the schema, the checksum. Raises `BootstrapError`.

    `data` is the zip's bytes, or a PATH to the zip (str / os.PathLike): the path form hashes the
    database member in 1 MiB steps and never holds it in memory (SYNC-BOOT Phase 3, Android)."""
    from_path = isinstance(data, (str, os.PathLike))
    size = os.path.getsize(data) if from_path else len(data)
    if size > MAX_PACKAGE_BYTES:
        raise BootstrapError(errors.BAD_PACKAGE, "the package is larger than any shop copy should be")
    try:
        zf = zipfile.ZipFile(str(data) if from_path else io.BytesIO(data), "r")
    except (zipfile.BadZipFile, OSError) as exc:
        raise BootstrapError(errors.BAD_PACKAGE, f"not a valid zip: {exc}") from exc
    with zf:
        if set(zf.namelist()) != {MANIFEST_NAME, spec.db_name}:
            raise BootstrapError(errors.BAD_PACKAGE, "the package does not hold exactly a manifest and the database")
        if zf.testzip() is not None:
            raise BootstrapError(errors.BAD_PACKAGE, "the package failed its zip integrity check")
        try:
            envelope = json.loads(zf.read(MANIFEST_NAME))
        except ValueError as exc:
            raise BootstrapError(errors.BAD_PACKAGE, f"the manifest is not JSON: {exc}") from exc
        if from_path:
            digest, db_size = hashlib.sha256(), 0
            with zf.open(spec.db_name) as member:
                for block in iter(lambda: member.read(1024 * 1024), b""):
                    digest.update(block)
                    db_size += len(block)
            db_bytes = None
        else:
            db_bytes = zf.read(spec.db_name)
            digest, db_size = hashlib.sha256(db_bytes), len(db_bytes)

    if not isinstance(envelope, dict) or not isinstance(envelope.get("payload"), dict):
        raise BootstrapError(errors.BAD_PACKAGE, "the manifest envelope is malformed")
    verdict = authority.verify_event(envelope, trust_store=trust_store, own_licence=own_licence)
    if isinstance(verdict, authority.Refusal):
        raise BootstrapError(
            errors.MANIFEST_NOT_TRUSTED, f"the copy is not signed by this shop ({verdict.reason}: {verdict.detail})")
    manifest = authority.strip_auth(envelope["payload"])

    if manifest.get("format") != PACKAGE_FORMAT or manifest.get("product") != spec.product:
        raise BootstrapError(errors.BAD_PACKAGE, "the package is not a bootstrap copy for this product")
    if not expected_company_id or str(manifest.get("company_id")) != str(expected_company_id):
        raise BootstrapError(
            errors.COMPANY_MISMATCH, "the copy belongs to a different shop than this computer")
    version = manifest.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise BootstrapError(errors.BAD_PACKAGE, "the manifest names no schema version")
    if version > spec.schema_version:
        raise BootstrapError(
            errors.SCHEMA_NEWER,
            f"the copy is schema v{version} but this program only speaks v{spec.schema_version}; update this computer first")
    as_of = manifest.get("as_of")
    if not isinstance(as_of, int) or isinstance(as_of, bool) or as_of < 0:
        raise BootstrapError(errors.BAD_PACKAGE, "the manifest names no valid relay position")
    if digest.hexdigest() != manifest.get("db_sha256") or db_size != manifest.get("db_size"):
        raise BootstrapError(errors.CHECKSUM_MISMATCH, "the database does not match its signed checksum")
    if from_path:
        return VerifiedPackage(manifest=manifest, zip_path=str(data), db_member=spec.db_name)
    return VerifiedPackage(manifest=manifest, db_bytes=db_bytes)


# ── blockers ────────────────────────────────────────────────────────────────

def open_blockers(live_db_path: str, spec: classes.BootstrapSpec) -> list:
    """BLOCK_IF_ROWS tables of the device's OWN live database that hold a row
    that forbids an install, as [(table, count)]. A database that does not exist
    yet, or lacks the table, blocks nothing."""
    if not os.path.exists(live_db_path):
        return []
    conn = sqlite3.connect(live_db_path, timeout=30)
    try:
        out = []
        live = set(classes.user_tables(conn))
        for table, predicate in spec.block_where.items():
            if table not in live:
                continue
            n = conn.execute(f'SELECT COUNT(*) FROM "{table}" WHERE {predicate}').fetchone()[0]
            if n:
                out.append((table, int(n)))
        return out
    finally:
        conn.close()


def _refuse_if_behind_own_cursor(live_db_path: str, spec: classes.BootstrapSpec, as_of: int, relay_url: str) -> None:
    """Defense in depth: never install a copy that is OLDER than what this device has already applied from
    the SAME relay. Installing it would rewind the database below events the device already holds and set
    its cursor back, silently undoing applied work. Owner's `complete` refuses a copy below its prune point,
    but the device does not trust that alone: its own cursor is the authority on what it already has.
    Cursors from different relays (cloud vs a LAN hub) are separate sequence spaces and are not compared."""
    if not os.path.exists(live_db_path):
        return
    conn = sqlite3.connect(live_db_path, timeout=30)
    try:
        if spec.cursor_table not in classes.user_tables(conn):
            return
        row = conn.execute(f'SELECT last_seq, relay_url FROM "{spec.cursor_table}" WHERE id = 1').fetchone()
    finally:
        conn.close()
    if row is None or row[1] != relay_url or row[0] is None:
        return
    if int(row[0]) > int(as_of):
        raise BootstrapError(
            errors.COPY_BEHIND_CURSOR,
            f"the copy is complete only up to relay position {as_of} but this computer has already applied "
            f"up to {row[0]} from the same relay; installing it would lose that work")


def _refuse_if_blocked(live_db_path: str, spec: classes.BootstrapSpec) -> None:
    blockers = open_blockers(live_db_path, spec)
    if blockers:
        raise BootstrapError(
            errors.OPEN_CASH_SESSION,
            "close the open cash drawer before this computer takes a fresh copy of the shop "
            f"({', '.join(f'{t}: {n}' for t, n in blockers)})")


# ── stage ───────────────────────────────────────────────────────────────────

def _live_db_path(app_data_dir, spec: classes.BootstrapSpec) -> str:
    return str(Path(app_data_dir) / "database" / "subsystems" / spec.db_name)


def staging_dir(app_data_dir) -> Path:
    return Path(app_data_dir) / "device" / STAGING_DIRNAME


def pending_path(app_data_dir) -> Path:
    return Path(app_data_dir) / "device" / PENDING_NAME


def pending(app_data_dir) -> Optional[dict]:
    try:
        raw = json.loads(pending_path(app_data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def check_staged_database(path: str, spec: classes.BootstrapSpec) -> None:
    """The staged file is at THIS build's schema, every table in it has a class,
    and SQLite agrees it is intact. Raises `BootstrapError`."""
    conn = sqlite3.connect(path)
    try:
        now_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if now_version != spec.schema_version:
            raise BootstrapError(
                errors.MIGRATION_FAILED,
                f"the staged copy is schema v{now_version}, expected v{spec.schema_version}")
        problems = spec.problems(conn)
        if problems:
            raise BootstrapError(
                errors.UNCLASSIFIED_TABLE, "the copy holds tables with no class: " + "; ".join(problems[:5]))
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            raise BootstrapError(errors.BAD_PACKAGE, f"the staged copy failed integrity_check: {ok}")
    finally:
        conn.close()


def stage(
    data,
    *,
    spec: classes.BootstrapSpec,
    app_data_dir,
    relay_url: str,
    trust_store,
    own_licence: Optional[str],
    expected_company_id: Optional[str],
    registry_fast_forward: Optional[int] = None,
) -> dict:
    """Verify `data` and park it for the next start. Returns the pending record.
    Raises `BootstrapError` and leaves nothing behind on any refusal.

    A copy OLDER than this build is staged as it is and migrated forward at
    install (`apply_staged(migrate_fn=...)`), not here: the product's migration
    machinery works through process-wide database paths, and re-pointing them
    while the app is serving requests would send another thread's query to the
    staged file. At the next start nothing else is running yet."""
    app = Path(app_data_dir)
    verified = verify_package(
        data, spec=spec, trust_store=trust_store, own_licence=own_licence,
        expected_company_id=expected_company_id)
    _refuse_if_blocked(_live_db_path(app, spec), spec)
    _refuse_if_behind_own_cursor(
        _live_db_path(app, spec), spec, int(verified.manifest["as_of"]), relay_url)

    stage_dir = staging_dir(app)
    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True, exist_ok=True)
    staged_db = stage_dir / spec.db_name
    try:
        verified.write_db(staged_db)
        if verified.manifest["schema_version"] == spec.schema_version:
            check_staged_database(str(staged_db), spec)
        else:
            # Older: structural sanity only; the full check runs after the migration.
            conn = sqlite3.connect(str(staged_db))
            try:
                ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            finally:
                conn.close()
            if ok != "ok":
                raise BootstrapError(errors.BAD_PACKAGE, f"the staged copy failed integrity_check: {ok}")
        record = {
            "bootstrap_id": verified.manifest["bootstrap_id"],
            "as_of": verified.manifest["as_of"],
            "relay_url": relay_url,
            "company_id": verified.manifest["company_id"],
            "copy_schema_version": verified.manifest["schema_version"],
            "registry_fast_forward": registry_fast_forward,
            "staged_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = pending_path(app).with_name(PENDING_NAME + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(tmp, pending_path(app))
        return record
    except BaseException:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise


# ── apply (next start) ──────────────────────────────────────────────────────

def _carry_local_rows(staged: sqlite3.Connection, spec: classes.BootstrapSpec) -> dict:
    """Copy this device's own rows from `old.*` (attached live database) into
    the staged copy. Returns {table: rows_carried}."""
    carried = {}
    old_tables = set(
        r[0] for r in staged.execute(
            "SELECT name FROM old.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall())
    for table in classes.user_tables(staged):
        cls = spec.class_of(table)
        if cls not in classes.CARRIED_FROM_REQUESTER and cls != classes.ROW_LEVEL:
            continue
        if table not in old_tables:
            continue
        cols = [c for c in classes.table_columns(staged, table) if c in classes.table_columns(staged, table, "old")]
        if not cols:
            continue
        col_sql = ",".join(f'"{c}"' for c in cols)
        if cls == classes.ROW_LEVEL:
            rule = spec.row_rules[table]
            rows = staged.execute(f'SELECT {col_sql} FROM old."{table}"').fetchall()
            key_ix = cols.index(rule.key_column) if rule.key_column in cols else None
            if key_ix is None:
                continue
            keep = [r for r in rows if rule.class_of_key(r[key_ix]) in classes.CARRIED_FROM_REQUESTER]
            for r in keep:
                staged.execute(
                    f'INSERT OR REPLACE INTO "{table}" ({col_sql}) VALUES ({",".join("?" * len(cols))})', tuple(r))
            if keep:
                carried[table] = len(keep)
        else:
            staged.execute(f'DELETE FROM main."{table}"')
            cur = staged.execute(f'INSERT INTO main."{table}" ({col_sql}) SELECT {col_sql} FROM old."{table}"')
            if cur.rowcount:
                carried[table] = cur.rowcount
    return carried


def _carry_outbox(staged: sqlite3.Connection, spec: classes.BootstrapSpec) -> int:
    old_tables = set(
        r[0] for r in staged.execute(
            "SELECT name FROM old.sqlite_master WHERE type='table'").fetchall())
    if spec.outbox_table not in old_tables:
        return 0
    cols = [c for c in classes.table_columns(staged, spec.outbox_table)
            if c in classes.table_columns(staged, spec.outbox_table, "old")]
    col_sql = ",".join(f'"{c}"' for c in cols)
    staged.execute(f'DELETE FROM main."{spec.outbox_table}"')
    cur = staged.execute(
        f'INSERT INTO main."{spec.outbox_table}" ({col_sql}) SELECT {col_sql} FROM old."{spec.outbox_table}"')
    return cur.rowcount or 0


def _rewrite_cursor(staged: sqlite3.Connection, spec: classes.BootstrapSpec, as_of: int, relay_url: str) -> None:
    n = staged.execute(
        f'UPDATE "{spec.cursor_table}" SET last_seq = ?, relay_url = ? WHERE id = 1', (as_of, relay_url)).rowcount
    if not n:
        staged.execute(
            f'INSERT INTO "{spec.cursor_table}" (id, last_seq, relay_url) VALUES (1, ?, ?)', (as_of, relay_url))


def _fast_forward_registry(app_data_dir, seq: Optional[int], relay_url: str) -> None:
    """Move the REGISTRY stream's cursor to `seq` if it is behind. The registry
    database is never swapped (authority is never copied), so this is the whole
    of its part in a bootstrap."""
    if not seq:
        return
    path = Path(app_data_dir) / "database" / "registry.db"
    if not path.exists():
        return
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        have = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_cursor'").fetchone()
        if not have:
            return
        row = conn.execute("SELECT last_seq, relay_url FROM sync_cursor WHERE id = 1").fetchone()
        if row is None:
            conn.execute("INSERT INTO sync_cursor (id, last_seq, relay_url) VALUES (1, ?, ?)", (int(seq), relay_url))
        elif int(row[0]) < int(seq) or row[1] != relay_url:
            conn.execute("UPDATE sync_cursor SET last_seq = ?, relay_url = ? WHERE id = 1", (int(seq), relay_url))
        conn.commit()
    finally:
        conn.close()


#: After this many FAILED installs of the same staged copy (not 'blocked': that is a drawer, which resolves
#: itself) the copy is thrown away and the device stops asking for another: a corrupt or unmigratable copy
#: would otherwise be re-requested and re-installed forever. A person has to look (needs_support).
MAX_INSTALL_FAILURES = 3


def _note_install_failure(app: Path, why: str) -> int:
    marker = read_marker(app)
    failures = int(marker.get("install_failures") or 0) + 1
    marker["install_failures"] = failures
    marker["last_install_failure"] = why
    write_marker(app, marker)
    if failures >= MAX_INSTALL_FAILURES:
        pending_path(app).unlink(missing_ok=True)
        shutil.rmtree(staging_dir(app), ignore_errors=True)
        logger.error("bootstrap: %d failed installs in a row (%s); the staged copy was discarded and this "
                     "computer will not ask for another until support clears it", failures, why)
    return failures


def require_inside(directory, path) -> Path:
    """`path` resolved, provided it is a file inside `directory` (symlinks and `..` followed). The Android
    stage-file route takes a path from the Kotlin side; it must never become a way to stage -- or, since the
    file is deleted afterwards, to delete -- anything outside the app's own download directory."""
    base = Path(directory).resolve()
    target = Path(path).resolve()
    if base not in target.parents:
        raise BootstrapError(errors.BAD_PACKAGE, "the copy is not in the download directory")
    return target


def status(app_data_dir, spec: classes.BootstrapSpec) -> dict:
    """What a requester that cannot see this device's files needs to know before it spends 50 MB of
    bandwidth (Android's Kotlin loop, via /_internal/bootstrap/info):
      can_stage / block_reason  -- an open cash drawer refuses a copy at stage AND at swap, so asking
                                   for one now would only download a copy that is then refused;
      pending_staged            -- a copy is already staged (a crash between staging and the requester
                                   recording it must not start a second request);
      install_failures / needs_support -- consecutive failed installs; at MAX_INSTALL_FAILURES the
                                   requester stops and says so."""
    app = Path(app_data_dir)
    blockers = open_blockers(_live_db_path(app, spec), spec)
    marker = read_marker(app)
    failures = int(marker.get("install_failures") or 0)
    return {
        "can_stage": not blockers,
        "block_reason": (f"{errors.OPEN_CASH_SESSION}: " + ", ".join(f"{t}: {n}" for t, n in blockers)) if blockers else None,
        "pending_staged": pending(app) is not None,
        "install_failures": failures,
        "needs_support": failures >= MAX_INSTALL_FAILURES,
        "last_install_failure": marker.get("last_install_failure"),
    }


def apply_staged(app_data_dir, spec: classes.BootstrapSpec, *, migrate_fn: Optional[Callable[[str], None]] = None,
                 now: Optional[datetime] = None) -> Optional[dict]:
    """Swap a staged copy in. Call at start, BEFORE any database of this product
    is opened. Returns None when nothing is staged, else a report whose `status`
    is 'installed' or 'blocked' (a drawer was opened since staging: the staged
    copy is kept and the install is retried at the next start). Never raises:
    a failure leaves the old database exactly where it was and says why."""
    app = Path(app_data_dir)
    record = pending(app)
    if record is None:
        return None
    live = _live_db_path(app, spec)
    staged_db = staging_dir(app) / spec.db_name
    report = {"status": "failed", "bootstrap_id": record.get("bootstrap_id"), "as_of": record.get("as_of")}
    try:
        if not staged_db.exists():
            pending_path(app).unlink(missing_ok=True)
            report["why"] = "staged copy missing"
            _note_install_failure(app, report["why"])
            return report
        try:
            _refuse_if_blocked(live, spec)
        except BootstrapError as exc:
            report.update(status="blocked", why=exc.message)
            logger.warning("bootstrap: install postponed -- %s", exc.message)
            return report

        try:
            # The cursor may have moved since staging (the device kept pulling while it waited for a quiet moment).
            _refuse_if_behind_own_cursor(live, spec, int(record["as_of"]), str(record["relay_url"]))
        except BootstrapError as exc:
            # Not a failed install: the copy is simply STALE and the device no longer needs it. Throw it away
            # and do not count it toward the give-up cap.
            pending_path(app).unlink(missing_ok=True)
            shutil.rmtree(staging_dir(app), ignore_errors=True)
            report.update(status="stale", why=exc.message)
            logger.warning("bootstrap: staged copy discarded -- %s", exc.message)
            return report

        copy_version = record.get("copy_schema_version")
        if isinstance(copy_version, int) and copy_version < spec.schema_version:
            if migrate_fn is None:
                raise BootstrapError(
                    errors.MIGRATION_FAILED, f"the copy is schema v{copy_version} and no migration was supplied")
            try:
                migrate_fn(str(staged_db))
            except Exception as exc:  # noqa: BLE001 -- any migration failure is a refusal, not a crash
                raise BootstrapError(errors.MIGRATION_FAILED, f"migrating the copy failed: {exc}") from exc
        check_staged_database(str(staged_db), spec)

        carried_outbox = 0
        carried = {}
        conn = sqlite3.connect(str(staged_db), timeout=30)
        try:
            if os.path.exists(live):
                conn.execute("ATTACH DATABASE ? AS old", (live,))
                carried = _carry_local_rows(conn, spec)
                carried_outbox = _carry_outbox(conn, spec)
                conn.commit()
                conn.execute("DETACH DATABASE old")
            _rewrite_cursor(conn, spec, int(record["as_of"]), str(record["relay_url"]))
            conn.commit()
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise BootstrapError(errors.BAD_PACKAGE, f"the carried copy failed integrity_check: {ok}")
            conn.execute("PRAGMA journal_mode = DELETE")
        finally:
            conn.close()

        stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
        rollback_dir = Path(live).parent.parent / f"pre-bootstrap-{stamp}"
        rollback_dir.mkdir(parents=True, exist_ok=True)
        Path(live).parent.mkdir(parents=True, exist_ok=True)
        if os.path.exists(live):
            old = sqlite3.connect(live, timeout=30)
            try:
                old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                old.close()
            shutil.copy2(live, rollback_dir / spec.db_name)
        for suffix in ("-wal", "-shm"):
            sidecar = live + suffix
            if os.path.exists(sidecar):
                os.remove(sidecar)
        try:
            os.replace(str(staged_db), live)
        except OSError as exc:
            saved = rollback_dir / spec.db_name
            if saved.exists():
                shutil.copy2(saved, live)
            raise BootstrapError(errors.PRODUCER_FAILED, f"could not replace the live database: {exc}") from exc

        _fast_forward_registry(app, record.get("registry_fast_forward"), str(record["relay_url"]))
        marker = read_marker(app)
        armed = {x for x in (marker.get("include_own_once") or []) if isinstance(marker.get("include_own_once"), list)}
        armed.add("retail")
        marker.update({
            "include_own_once": sorted(armed), "include_own_reason": "bootstrap",
            "reapply_outbox": carried_outbox > 0,
            "bootstrap_id": record.get("bootstrap_id"), "as_of": record.get("as_of"),
            "installed_at": (now or datetime.now(timezone.utc)).isoformat(),
        })
        marker.pop("install_failures", None)
        marker.pop("last_install_failure", None)
        write_marker(app, marker)
        pending_path(app).unlink(missing_ok=True)
        shutil.rmtree(staging_dir(app), ignore_errors=True)
        report.update(status="installed", rollback_dir=str(rollback_dir),
                      carried=carried, carried_outbox=carried_outbox)
        logger.info("bootstrap: installed a shop copy complete up to relay position %s "
                    "(old database kept in %s)", record.get("as_of"), rollback_dir)
        return report
    except BootstrapError as exc:
        report["why"] = exc.message
        logger.error("bootstrap: install failed -- %s; the old database is untouched", exc.message)
        _note_install_failure(app, exc.message)
        return report
    except Exception as exc:  # noqa: BLE001 -- boot must never die on this
        report["why"] = f"{type(exc).__name__}: {exc}"
        logger.exception("bootstrap: install failed unexpectedly; the old database is untouched")
        try:
            _note_install_failure(app, report["why"])
        except Exception:  # noqa: BLE001
            logger.exception("bootstrap: could not record the failed install")
        return report
