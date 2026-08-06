"""SyncService -- drains the local `sync_outbox` table to Owner's relay
(push) and applies Owner-relayed events from other devices to local tables
(pull). Background-loop shape (`start`/`stop`/`_schedule_next`) mirrors
`commercial_runtime/licensing_contracts/checkin_scheduler.py`'s
`LicenseCheckInScheduler` exactly: a self-rescheduling `threading.Timer`,
daemon thread, `threading.Event` stop flag.

Scope note: only `entity_type == "category"` is understood by
`_apply_event` -- this sub-project (multi-device-sync-foundation) only wires
category routes through the outbox (Task 4). A future entity type arriving
from Owner (e.g. once products/customers are wired) is silently skipped, not
an error -- forward compatibility for a relay that may carry entity types
this particular product build doesn't know how to apply yet.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SyncService:
    def __init__(self, client_factory: Callable[[], "SyncRelayClient"], get_conn: Callable[[], "sqlite3.Connection"]):
        """`client_factory` is called fresh on every push_once()/pull_once()
        attempt, not once at construction time -- deliberately, mirroring
        `commercial_runtime/licensing_contracts/routes.py`'s own
        `_build_context()`, which rebuilds its object graph on every request
        rather than caching it at blueprint-creation time. A SyncRelayClient
        built once at process start would freeze whatever `installation_id`
        was on disk at that moment (commonly None, before the operator has
        even activated the license yet); rebuilding on every attempt means
        activation completing later in the same process is picked up on the
        very next tick with no restart required."""
        self._client_factory = client_factory
        self._get_conn = get_conn
        self._timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()
        # Serializes push_once/pull_once against each other -- the 10s timer
        # tick and an immediate route-triggered nudge() can otherwise land on
        # two different threads at once, both reading/draining the same
        # sync_outbox rows.
        self._lock = threading.Lock()

    def push_once(self) -> None:
        """Drains sync_outbox to Owner. Reads the current rows, pushes them,
        and ONLY deletes exactly those rows on success -- a push failure
        (network or a real Owner rejection) raises back to the caller with
        the outbox left completely untouched (nothing read is deleted, and
        nothing new that arrived concurrently is at risk, since only the
        specific ids just pushed are ever deleted)."""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute("SELECT * FROM sync_outbox ORDER BY created_at").fetchall()
                if not rows:
                    return
                events = [
                    {
                        "id": r["id"],
                        "entity_type": r["entity_type"],
                        "entity_id": r["entity_id"],
                        "event_type": r["event_type"],
                        "payload": json.loads(r["payload"]),
                        "created_at": r["created_at"],
                    }
                    for r in rows
                ]
                self._client_factory().push(events)  # raises on failure -- nothing below runs
                ids = [r["id"] for r in rows]
                conn.execute(
                    "DELETE FROM sync_outbox WHERE id IN ({})".format(",".join("?" * len(ids))), ids
                )
                conn.commit()
            finally:
                # conn.close() with no prior commit() discards any
                # uncommitted work on this connection -- if push() raised,
                # the DELETE above never ran, so there is nothing to lose;
                # this only guarantees the connection itself is never leaked.
                conn.close()

    def pull_once(self) -> None:
        """Pulls events newer than the local cursor and applies them, then
        advances the cursor to Owner's returned cursor value -- all in the
        one local transaction, so a mid-apply failure leaves the cursor
        exactly where it was (next attempt re-pulls the same range, not a
        gap)."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor_row = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()
                since = cursor_row["last_seq"] if cursor_row else 0
                result = self._client_factory().pull(since)  # raises on failure
                for ev in result.get("events", []):
                    self._apply_event(conn, ev)
                conn.execute("UPDATE sync_cursor SET last_seq=? WHERE id=1", (result["cursor"],))
                conn.commit()
            finally:
                conn.close()

    def _apply_event(self, conn, ev: dict) -> None:
        if ev.get("entity_type") != "category":
            return  # only category is in scope for this sub-project
        p = ev.get("payload") or {}
        event_type = ev.get("event_type")
        if event_type in ("create", "update"):
            conn.execute(
                "INSERT INTO categories (id, company_id, name, description) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description",
                (p.get("id"), p.get("company_id"), p.get("name"), p.get("description", "")),
            )
        elif event_type == "delete":
            conn.execute("DELETE FROM categories WHERE id=?", (p.get("id"),))

    def run_once(self) -> None:
        """The one entry point the timer tick (and the manual/CLI caller)
        uses. Never lets a relay/network failure escape -- an unreachable or
        rejecting relay is an expected, ordinary condition (offline device),
        not a reason to crash the host app. The next scheduled tick (or the
        next route-triggered nudge) simply retries."""
        try:
            self.push_once()
            self.pull_once()
        except Exception:
            logger.info("Sync relay attempt failed (offline or rejected); will retry on the next tick.", exc_info=True)

    def start(self, interval_seconds: float = 10.0) -> None:
        self._stopped.clear()
        self._schedule_next(interval_seconds)

    def stop(self) -> None:
        self._stopped.set()
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _schedule_next(self, interval_seconds: float) -> None:
        if self._stopped.is_set():
            return

        def _tick():
            try:
                self.run_once()
            finally:
                self._schedule_next(interval_seconds)

        self._timer = threading.Timer(interval_seconds, _tick)
        self._timer.daemon = True
        self._timer.start()


# ── Module-level "nudge" registration ───────────────────────────────────────
# Task 4's category routes call nudge() right after committing a row into
# sync_outbox, so a push happens as close to immediately as possible rather
# than waiting for the next 10s timer tick -- without giving retail_api.py
# (or any other route module) a direct import-time dependency on however
# app.py happened to construct the SyncService (base URL, signer,
# installation id, ...). app.py calls register_active_service() once, at
# import time, only when SYNC_RELAY_BASE_URL is configured; when it is not,
# nothing ever registers and nudge() is a no-op.
_active_service: Optional[SyncService] = None


def register_active_service(service: SyncService) -> None:
    global _active_service
    _active_service = service


def unregister_active_service() -> None:
    global _active_service
    _active_service = None


def nudge() -> None:
    """Best-effort, non-blocking push attempt. Safe to call unconditionally
    from a route handler: a no-op when no service is registered (sync
    inert), and never lets a slow/unreachable relay add latency to the
    caller's HTTP response -- push_once() runs on its own short-lived daemon
    thread, with any failure swallowed exactly like a normal missed timer
    tick (the next scheduled run_once() retries)."""
    service = _active_service
    if service is None:
        return

    def _push():
        try:
            service.push_once()
        except Exception:
            logger.info("Sync nudge push failed (offline or rejected); the next timer tick will retry.", exc_info=True)

    threading.Thread(target=_push, daemon=True).start()
