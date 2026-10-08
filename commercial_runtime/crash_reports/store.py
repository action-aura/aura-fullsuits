"""Crash reports -- the local, bounded, durable queue (the outbox).

Same idea as the e-invoicing outbox: write locally first, send later from a
separate thread, so a crash is never lost just because Owner is unreachable
and never slows the till down waiting for the network.

Its own SQLite file, <app_data>/database/subsystems/crash_reports.db. It must
NEVER touch retail.db: the code that records a crash runs while the process is
already in trouble (a half-finished transaction, a locked database), and a
reporter that blocks or corrupts the books to describe a bug is worse than no
reporter. One short transaction per call, on a connection of its own.

Bounded on purpose: at most MAX_ROWS rows, ever, whether or not an Owner is
configured. A till that is offline for a year and crashes in a loop must not
fill its own disk with the record of it.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger("aura.crash_reports")

MAX_ROWS = 200
# Short on purpose: capture runs inside a failing request, and waiting
# seconds for a lock would turn one error into a slow one.
BUSY_TIMEOUT_SECONDS = 2.0

STATE_PENDING = "pending"
STATE_SENT = "sent"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS crash_outbox (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint     TEXT    NOT NULL,
    kind            TEXT    NOT NULL,
    exception_type  TEXT    NOT NULL,
    message         TEXT    NOT NULL,
    frames_json     TEXT    NOT NULL,
    route           TEXT,
    app_version     TEXT    NOT NULL,
    platform        TEXT    NOT NULL,
    first_seen_at   TEXT    NOT NULL,
    last_seen_at    TEXT    NOT NULL,
    count           INTEGER NOT NULL DEFAULT 1,
    sent_count      INTEGER NOT NULL DEFAULT 0,
    state           TEXT    NOT NULL DEFAULT 'pending',
    last_sent_at    TEXT
);
-- One row per (bug, build): the same bug on the same version is a count, not
-- a new row. A new build is a new row so staff can see "fixed in 1.0.3".
CREATE UNIQUE INDEX IF NOT EXISTS uq_crash_outbox_fingerprint_version
    ON crash_outbox (fingerprint, app_version);
CREATE INDEX IF NOT EXISTS ix_crash_outbox_state_seen
    ON crash_outbox (state, last_seen_at);
"""


def utc_now_iso() -> str:
    """Fixed-width UTC timestamp, so string order IS time order (the cap and
    the 'oldest first' sends both sort on it)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class CrashStore:
    def __init__(self, db_path):
        self._path = Path(db_path)

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), timeout=BUSY_TIMEOUT_SECONDS)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        return conn

    # -- writing ---------------------------------------------------------
    def record(self, report: dict) -> int:
        """Store one crash. Returns the running count for its (fingerprint,
        app_version) row.

        A repeat of a known crash bumps `count`, refreshes `last_seen_at` and
        flips the row back to 'pending' EVEN IF it was already sent: the
        sender ships the running total, so Owner learns "this is now 40
        occurrences", not just "it happened once".
        """
        now = utc_now_iso()
        frames_json = json.dumps(report.get("frames") or [], ensure_ascii=False, separators=(",", ":"))
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO crash_outbox
                        (fingerprint, kind, exception_type, message, frames_json, route,
                         app_version, platform, first_seen_at, last_seen_at, count, sent_count, state)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 'pending')
                    ON CONFLICT (fingerprint, app_version) DO UPDATE SET
                        count = count + 1,
                        last_seen_at = excluded.last_seen_at,
                        state = 'pending'
                    """,
                    (
                        report["fingerprint"], report["kind"], report["exception_type"], report["message"],
                        frames_json, report.get("route"), report["app_version"], report["platform"], now, now,
                    ),
                )
                row = conn.execute(
                    "SELECT id, count FROM crash_outbox WHERE fingerprint = ? AND app_version = ?",
                    (report["fingerprint"], report["app_version"]),
                ).fetchone()
                self._enforce_cap(conn, keep_id=row["id"])
            return int(row["count"])
        finally:
            conn.close()

    @staticmethod
    def _enforce_cap(conn: sqlite3.Connection, *, keep_id: int) -> None:
        """HARD CAP: delete the oldest SENT rows first (the record of them
        already reached Owner), then the oldest pending ones. The row just
        written is never the one deleted."""
        total = conn.execute("SELECT COUNT(*) FROM crash_outbox").fetchone()[0]
        excess = total - MAX_ROWS
        if excess <= 0:
            return
        conn.execute(
            """
            DELETE FROM crash_outbox WHERE id IN (
                SELECT id FROM crash_outbox
                WHERE id != ?
                ORDER BY CASE state WHEN 'sent' THEN 0 ELSE 1 END, last_seen_at ASC, id ASC
                LIMIT ?
            )
            """,
            (keep_id, excess),
        )
        log.info("crash reports: store over its %s-row cap, dropped %s oldest row(s)", MAX_ROWS, excess)

    def mark_sent(self, sent: Iterable[tuple]) -> int:
        """`sent` is (row_id, count_that_was_sent) pairs. A row is marked sent
        only if its count is STILL the count that was sent: if the same crash
        happened again while the request was in flight, the row stays pending
        and the newer total goes out on the next run, instead of being
        silently marked as delivered."""
        now = utc_now_iso()
        marked = 0
        conn = self._connect()
        try:
            with conn:
                for row_id, count in sent:
                    cursor = conn.execute(
                        "UPDATE crash_outbox SET state = 'sent', sent_count = ?, last_sent_at = ? "
                        "WHERE id = ? AND count = ?",
                        (count, now, row_id, count),
                    )
                    marked += cursor.rowcount
            return marked
        finally:
            conn.close()

    # -- reading ---------------------------------------------------------
    def pending(self, limit: int) -> list:
        """Oldest-seen pending rows first, as plain dicts."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM crash_outbox WHERE state = 'pending' ORDER BY last_seen_at ASC, id ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]

    def all_rows(self) -> list:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM crash_outbox ORDER BY id ASC").fetchall()
        finally:
            conn.close()
        return [self._row_to_dict(r) for r in rows]

    def summary(self) -> dict:
        conn = self._connect()
        try:
            pending = conn.execute("SELECT COUNT(*) FROM crash_outbox WHERE state = 'pending'").fetchone()[0]
            sent = conn.execute("SELECT COUNT(*) FROM crash_outbox WHERE state = 'sent'").fetchone()[0]
            last = conn.execute("SELECT MAX(last_sent_at) FROM crash_outbox").fetchone()[0]
        finally:
            conn.close()
        return {"pending": pending, "sent": sent, "last_sent_at": last}

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        data = dict(row)
        try:
            data["frames"] = json.loads(data.pop("frames_json"))
        except (TypeError, ValueError):
            data["frames"] = []
        return data


def default_store_path(app_data_dir) -> Path:
    return Path(app_data_dir) / "database" / "subsystems" / "crash_reports.db"


def open_default_store(app_data_dir) -> Optional[CrashStore]:
    """The store for this install, or None if its folder cannot be created.
    Never raises: a till whose crash log cannot be opened still has to boot."""
    try:
        store = CrashStore(default_store_path(app_data_dir))
        store.summary()          # forces the file + schema into existence now, not mid-crash
        return store
    except Exception:  # noqa: BLE001
        log.warning("crash reports: could not open the local store; capture is off for this run", exc_info=True)
        return None
