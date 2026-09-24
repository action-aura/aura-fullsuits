"""In-process client for a hub's OWN two `SyncService` instances to read and
write THIS hub's own site log directly, instead of Owner's cloud relay.

THE DEFECT THIS MODULE CLOSES, measured on real hardware 2026-09-24 (see
`products/retail/backend/app.py::_enter_hub_self_sync`'s own docstring for
the exact numbers): a Windows till that becomes the LAN hub kept pushing
and pulling Owner's cloud relay directly through its ordinary
`_build_sync_client`. Paired LAN devices talked only to the hub's site log
(`store.py`/`routes.py`). Those were two disconnected worlds -- a phone's
sales never reached the desktop till's own `/sales/recent`, and a product
created on the desktop never reached the phone -- because nothing ever made
the hub's own two sync streams (retail + registry) consume the SAME log its
paired devices write into.

`LocalSiteRelayClient` is the fix: it satisfies `SyncService`'s documented
client contract (`push(events) -> dict`, `pull(since) -> dict`, `.base_url`)
by calling `store.py` directly against the hub's own already-open SQLite
connection, with no HTTP, no TLS, no signature and no nonce. Authentication
exists to prove a REMOTE device's identity to a listener neither of them
controls; the caller here is this same process talking to its own database,
so there is nothing to authenticate. The push/pull semantics below mirror
`routes.py`'s `/push` and `/pull` handlers exactly (same validation order,
same dedup, same exclusion), so a hub applying its own outbox through this
client behaves identically to a paired LAN device pushing the same events
over the wire.

THE ONE HARD RULE THIS FILE ENFORCES: `pull()` NEVER WRITES. See
`SyncService.pull_once`'s own docstring for why -- it calls
`ensure_cursor_matches_relay()` (an `UPDATE sync_cursor` on ITS OWN
connection) before `client.pull()`, and the write lock from that UPDATE
stays held until `pull_once`'s own `conn.commit()`. `store.py` runs in WAL
mode with `busy_timeout=30000` (see `database/schema.py`'s connection
setup), so a SECOND connection -- which is exactly what this client's
`get_conn()` callback opens, deliberately never reusing the caller's own
connection -- attempting a WRITE while that lock is held would block for
the full 30 seconds and then raise "database is locked". The route handler
`routes.py`'s `/pull` calls `store.advance_device_cursor` after every pull;
that call is DELIBERATELY NOT copied here (see `pull()`'s own docstring
below) for exactly this reason -- see this package's test file, test L4,
for the mutation proof.

`RelaySelector` is the runtime switch: the seam that flips BOTH of a hub's
`SyncService` instances (retail and registry) from talking to Owner's cloud
to talking to this hub's own site log, and back is never taken (see
`election.py`'s "WHY STANDING DOWN LOSES NOTHING" and
`app.py::_enter_hub_self_sync`'s own "decision S1" comment for why a
stood-down hub stays LOCAL rather than reverting to cloud). `SyncService`
already calls `self._client_factory()` fresh on every push/pull attempt
(see that class's own `__init__` docstring), so flipping the selector takes
effect on the very next tick with no restart and no re-registration
required.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Callable, Optional

from commercial_runtime.sync.site_relay import store

# A deliberately impossible http(s) URL -- see `SyncService.ensure_cursor_
# matches_relay`'s own docstring: `sync_cursor.relay_url` is compared by
# plain string equality against whatever `getattr(client, "base_url", None)`
# returns, and a relay switch (cloud <-> local, or hub A's site log <-> hub
# B's) is detected exactly when that string changes. A scheme no real HTTP
# relay could ever use (`aura-site-log://`, not `http://`/`https://`) means
# this constant can never collide with a genuine cloud URL or another hub's
# `primary_url()`. It is NOT derived from this hub's own `installation_id`
# on purpose: a licence re-issue changes that id
# (`rehearsal_reissue.py --keep-device`, see MEMORY.md), and this constant
# must stay byte-for-byte identical across a re-issue so a re-issued hub's
# cursor is recognised as "still the same relay" rather than reset again.
LOCAL_RELAY_URL = "aura-site-log://local"


class LocalSiteRelayError(Exception):
    """Mirrors `SyncRelayClientError` (`commercial_runtime/sync/
    relay_client.py`) in shape -- a `.reason_code` short code, matching
    `SyncService._failure_reason`'s own `getattr(exc, "reason_code", None)`
    lookup, so a failure from this client logs a short, human-safe code
    exactly like a real network failure would, not a raw message that could
    embed a payload."""

    def __init__(self, reason_code: str, message: str = ""):
        super().__init__(message or reason_code)
        self.reason_code = reason_code


class LocalSiteRelayClient:
    """The `SyncService` client contract (`push`/`pull`/`.base_url`),
    implemented by calling `store.py` directly against the hub's own
    database rather than over HTTP. One instance is built fresh per call by
    `RelaySelector`/the caller's own factory closure -- see `SyncService.
    __init__`'s own docstring on why `client_factory` is called fresh on
    every attempt, never cached."""

    def __init__(self, *, get_conn: Callable[[], sqlite3.Connection],
                 origin_device_id: Optional[str]):
        self._get_conn = get_conn
        self._origin_device_id = origin_device_id

    @property
    def base_url(self) -> str:
        return LOCAL_RELAY_URL

    def push(self, events: list) -> dict:
        """Validates the WHOLE batch first, exactly like `routes.py`'s
        `/push` handler, so a malformed event never gets a partial write:
        one bad event in a batch of 50 must reject all 50, not silently
        drop the one and write the other 49 -- a caller (this hub's own
        `SyncService.push_once`) that sees an exception assumes NOTHING was
        stored and keeps every event in its outbox for the next attempt."""
        if not self._origin_device_id:
            raise LocalSiteRelayError(
                "NO_INSTALLATION_ID",
                "this hub has no owner_installation_id yet (not activated); "
                "a push with no origin would write NULL origin rows that "
                "read()'s exclusion filter can never distinguish from a "
                "genuine device, so it is refused outright rather than "
                "silently mis-attributed.",
            )
        if not isinstance(events, list) or len(events) > store.MAX_PUSH_BATCH:
            raise LocalSiteRelayError(
                "INVALID_BATCH",
                f"batch of {len(events) if isinstance(events, list) else '?'} "
                f"exceeds the {store.MAX_PUSH_BATCH}-event cap this hub's own "
                f"site relay route enforces on every other pusher.",
            )
        for index, raw in enumerate(events):
            try:
                store.validate_event(raw)
            except store.InvalidEventError as exc:
                raise LocalSiteRelayError(
                    "INVALID_EVENT", f"event_index={index}: {exc}"
                ) from exc

        conn = self._get_conn()
        try:
            stored = store.append_events(conn, events, origin_device_id=self._origin_device_id)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"stored": stored, "received": len(events)}

    def pull(self, since: int) -> dict:
        """READ-ONLY. See this module's own docstring, "THE ONE HARD RULE
        THIS FILE ENFORCES" -- `store.advance_device_cursor` is
        DELIBERATELY NEVER CALLED here, unlike `routes.py`'s `/pull`
        handler, which does call it (that handler owns the REMOTE device's
        cursor bookkeeping; this client's caller is `SyncService.pull_once`
        itself, which already advances ITS OWN `sync_cursor` after this
        call returns -- a second, different cursor table entirely,
        `site_device_cursors`, has no business being touched by the same
        device that is also the hub). Writing here, on a second connection,
        while `pull_once`'s own connection holds the write lock across its
        `ensure_cursor_matches_relay` UPDATE, would block for the full
        30-second `busy_timeout` and then raise "database is locked" --
        see test L4 in this package's test file for the mutation proof."""
        if not self._origin_device_id:
            # Without this guard, `store.read_events_since`'s SQL would run
            # with `exclude_device_id=None`, and `origin_device_id != ?`
            # against a bound NULL is never true in SQL's three-valued
            # logic -- EVERY row would be excluded, including this hub's
            # own, silently returning zero events forever with no error
            # anywhere. Loud failure here beats that silent, permanent
            # blindness.
            raise LocalSiteRelayError(
                "NO_INSTALLATION_ID",
                "this hub has no owner_installation_id yet (not activated); "
                "pulling with no origin cannot correctly exclude this "
                "hub's own rows, so it is refused rather than silently "
                "returning nothing forever.",
            )
        if not isinstance(since, int) or isinstance(since, bool) or since < 0:
            raise LocalSiteRelayError(
                "INVALID_SINCE", f"since must be a non-negative int, got {since!r}"
            )

        conn = self._get_conn()
        try:
            rows = store.read_events_since(conn, since=since, exclude_device_id=self._origin_device_id)
            cursor = store.resolve_pull_cursor(conn, since=since, rows=rows)
        finally:
            # No commit -- there is nothing to commit. `conn.close()` alone
            # would discard any accidental write below this point just as
            # safely, but the absence of a `commit()` call anywhere in this
            # method is itself the load-bearing fact a reader should be
            # able to see at a glance.
            conn.close()
        return {"events": rows, "cursor": cursor}


class RelaySelector:
    """The `client_factory` BOTH of a hub's `SyncService` instances (retail
    and registry) share -- see `app.py::_build_windows_sync_services`'s own
    wiring. Defaults to CLOUD (today's behaviour, byte for byte, for every
    till that never becomes a hub); `use_local()` flips it, once and
    permanently for this process, to the hub's own site log.

    "Once and permanently": there is no `use_cloud()`. See `election.py`'s
    "WHY STANDING DOWN LOSES NOTHING" and decision S1 in this task's own
    plan -- a stood-down hub stays LOCAL and keeps converging with the new
    hub through its own forwarder, because switching back to the cloud
    would strand every LAN row this till accepted while it was hub (the
    cloud excludes a device's own events from what it hands back -- see
    `forwarder.py`'s module docstring, "THE ECHO GUARD", and 0.4 in this
    task's own plan)."""

    def __init__(self, *, cloud_factory: Optional[Callable[[], object]],
                 local_factory: Callable[[], object]):
        self._cloud_factory = cloud_factory
        self._local_factory = local_factory
        self._local = False
        self._lock = threading.Lock()

    def use_local(self) -> bool:
        """Flips this selector to LOCAL. Returns True the first time it
        actually flips something, False on every call after (idempotent --
        `_enter_hub_self_sync` calls this on every re-entry, e.g. a
        re-election after a stand-down-then-reclaim cycle, and must not
        re-log or re-act as if this were a fresh flip)."""
        with self._lock:
            if self._local:
                return False
            self._local = True
            return True

    def is_local(self) -> bool:
        return self._local

    def __call__(self):
        if self._local:
            return self._local_factory()
        if self._cloud_factory is not None:
            return self._cloud_factory()
        raise LocalSiteRelayError(
            "NO_RELAY_CONFIGURED",
            "this selector is in CLOUD mode but was built with no cloud "
            "factory (a LAN-only hub with no AURA_SYNC_RELAY_URL and "
            "nothing from Owner) -- the caller must flip use_local() "
            "before this SyncService pair can push or pull anything.",
        )


def consumer_floor(conn: sqlite3.Connection) -> int:
    """This hub's OWN two sync streams' consumption position into the site
    log, for the housekeeping sweep's pruning floor (`listener.py::
    run_housekeeping_sweep`, `app.py::_hub_own_consumer_floors`). Returns
    `sync_cursor.last_seq` ONLY when that cursor's `relay_url` is THIS
    site log (`LOCAL_RELAY_URL`) -- a cursor still pointed at the cloud (a
    till that has never become a hub, or one mid-switch on its very first
    LOCAL tick before the reset in `ensure_cursor_matches_relay` has run)
    has consumed nothing from the site log at all, and returning its
    (irrelevant, possibly large) cloud `last_seq` here would let
    `pruning.safe_prune_watermark`'s `extra_floors` incorrectly protect
    rows that number has nothing to do with -- or, worse, let a genuinely
    unconsumed local position get pruned out from under this hub's own
    not-yet-applied rows if the two numbers happened to disagree in the
    other direction. Returns 0 (never prunable past) for both "wrong relay"
    and "no row at all", the same conservative default `forwarder.
    read_forward_cursor` and `store.read_device_cursor` already use
    elsewhere in this package."""
    row = conn.execute("SELECT last_seq, relay_url FROM sync_cursor WHERE id = 1").fetchone()
    if row is None:
        return 0
    if row["relay_url"] != LOCAL_RELAY_URL:
        return 0
    return int(row["last_seq"])
