"""SyncService -- drains the local `sync_outbox` table to Owner's relay
(push) and applies Owner-relayed events from other devices to local tables
(pull). Background-loop shape (`start`/`stop`/`_schedule_next`) mirrors
`commercial_runtime/licensing_contracts/checkin_scheduler.py`'s
`LicenseCheckInScheduler` exactly: a self-rescheduling `threading.Timer`,
daemon thread, `threading.Event` stop flag.

Scope note: only `entity_type in ("category", "product", "customer",
"supplier")` is understood by `_apply_event` -- this sub-project
(multi-device-sync-foundation, retail-catalog-party-sync-expansion) only
wires category/product/customer/supplier routes through the outbox. A
future entity type arriving from Owner is silently skipped, not an error --
forward compatibility for a relay that may carry entity types this
particular product build doesn't know how to apply yet.

Cross-device `company_id` bug fix (2026-08-07, found in live device
testing): a pulled event's `payload["company_id"]` is the SENDING device's
own `company_id` -- NOT a shared/authoritative tenant id. `company_id` is
derived once, locally, at onboarding, as `md5(admin_email)`
(`commercial_runtime/identity/onboarding_routes.py::create_admin`), so two
devices activated with different admin emails on the SAME license end up
with two different `company_id` values. Blindly writing the payload's
`company_id` into a pulled row makes it permanently invisible to the
RECEIVING device's own `WHERE company_id=?` queries (e.g.
`retail_api.py::list_categories`), which always filter on that device's OWN
`company_id` (`_cid()` -> `session['company_id']`, set at login from that
device's own `users` row). `_apply_event` therefore IGNORES
`payload["company_id"]` entirely and stamps the RECEIVING device's own
locally-authoritative `company_id` (read fresh from the registry DB's
`company_settings` table -- see `local_company_id_from_registry` below) onto
every pulled row instead. `_apply_event` runs on a background thread with no
Flask request/session context, so it cannot call `_cid()`/read
`session.get('company_id')` -- it must read the value directly from disk.

Outbox-wedge fix (2026-08-10 audit, HIGH severity): `push_once()` used to
read the ENTIRE `sync_outbox` with no limit and push it in one call, while
Owner's relay enforces a 200-event `_MAX_PUSH_BATCH` and rejects anything
larger with a business-rejection (`RelayRejected`, never retried
automatically -- see `relay_client.py`'s module docstring on why a rejection
and a transient `NetworkError` are deliberately different exception types).
Because `push_once()` also leaves the outbox completely untouched on ANY
failure (correct for a transient/offline failure, see `push_once`'s own
docstring), a single bulk import or a few days offline queuing >200 rows
would wedge the outbox PERMANENTLY: every subsequent tick reads the same
oversized batch, gets the same rejection, and nothing ever shrinks. Two
fixes, in the order they had to land:

1. `read_outbox` now takes a `limit` (default `DEFAULT_OUTBOX_PUSH_LIMIT`,
   comfortably under the relay's 200-row cap) and orders by
   `created_at, rowid` -- see `RETAIL_SCHEMA_VERSION`'s v7 comment in
   `database/schema.py` for why `created_at` alone was never a safe sort key,
   and why the tiebreaker is SQLite's own hidden `rowid` rather than the
   table's declared `id` column: `sync_outbox.id` is a client-generated UUID
   (`_queue_sync_event` in `retail_api.py`), not an autoincrementing key, so
   it has no relationship to insertion order at all. `rowid` is SQLite's own
   implicit, monotonically-assigned integer for any ordinary (non-`WITHOUT
   ROWID`) table -- exactly what an autoincrement tiebreaker would have
   given this table if `id` itself had been one. `push_once()` now drains in
   bounded per-tick chunks: one `limit`-sized read/push/ack per call, with
   a large backlog draining over several ticks rather than one oversized
   call. Correct only because of the ordering fix above -- a chunk boundary
   landing between an ambiguously-ordered create and its own later update
   would otherwise risk pushing them out of order.

2. Chunking alone only stops a NEW install from wedging via one big import;
   it does nothing for a single row that is itself genuinely unpushable
   (malformed payload, a reference to since-deleted data, ...) -- that row
   would still fail identically forever, just in a smaller batch. On a
   `RelayRejected`, `push_once()` now records the rejection against every
   row in the batch (`attempt_count`/`last_error`), and once a batch has
   been rejected `DEAD_LETTER_THRESHOLD` times in a row, bisects it --
   repeatedly halving and re-pushing sub-batches -- to isolate the actual
   offending row(s), moves each one to `sync_dead_letter` (removed from the
   active outbox, so it can never block anything again), and lets the rest
   of the batch keep draining normally. `RelayRejected` is escalated to
   `logger.error` (was `logger.info`) the moment bisection actually starts --
   a wedge condition is no longer a silent, INFO-level, forever-retried
   no-op.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Callable, Optional

from commercial_runtime.sync.relay_client import RelayRejected

logger = logging.getLogger(__name__)

# Comfortably under Owner relay's `_MAX_PUSH_BATCH = 200` (owner/app/sync/
# routes.py -- not present in every worktree, since it's Owner-side code;
# see this module's docstring) -- leaves headroom rather than pushing right
# up to the server's own limit.
DEFAULT_OUTBOX_PUSH_LIMIT = 100

# How many CONSECUTIVE RelayRejected responses the exact same (still
# unbisected) batch has to accumulate before push_once() gives up retrying
# it whole and starts bisecting to find the actual offending row(s). Chosen
# as 5, not 1, deliberately: only a genuine business rejection increments
# this counter at all (a transient NetworkError never does -- see
# push_once() below), so even attempt #1 already means Owner understood and
# rejected the request. The extra headroom to 5 exists as defense-in-depth
# against a rejection that reflects transient SERVER-side state rather than
# a truly malformed payload (e.g. a brief Owner-side hiccup misreported as a
# business rejection instead of a 5xx), and gives an operator watching the
# new ERROR-level log a few ticks' warning before a row is irreversibly
# quarantined. At the default 10s tick interval that is well under a
# minute; a route-triggered nudge() reaches it far faster. Still bounded and
# still small -- nowhere near "forever".
DEAD_LETTER_THRESHOLD = 5

# SQLite's own per-statement bound-parameter cap is 999
# (SQLITE_MAX_VARIABLE_NUMBER's historical default, sometimes compiled
# lower). Comfortably under that so ack_outbox() never depends on exactly
# which build of SQLite this runs against.
_ACK_CHUNK_SIZE = 500


def local_company_id_from_registry() -> Optional[str]:
    """Default `local_company_id_provider`: reads THIS device's own single,
    locally-authoritative `company_id` directly from the registry database
    -- never from a pulled event's payload (see module docstring above).

    Every install has exactly one `company_id`: `create_admin` (the only
    account-creation path with no existing-admin requirement) writes the
    `users` row and the `company_settings` row with the SAME `company_id` in
    one transaction, and is only reachable while no valid admin exists yet
    -- so `company_settings` always holds exactly this device's one real
    `company_id` once onboarding has completed. Falls back to the admin
    `users` row (the same source `_cid()`/`create_session` populate the
    session from) if `company_settings` is somehow missing, and returns
    `None` (never a fabricated default) if neither exists yet -- e.g. a pull
    landing before this device has completed its own onboarding, which
    `_apply_event` treats as "cannot apply yet", not "guess company_id=1".
    """
    from commercial_runtime.identity.registry_db import get_conn as _registry_get_conn

    conn = _registry_get_conn()
    try:
        row = conn.execute("SELECT company_id FROM company_settings LIMIT 1").fetchone()
        if row and row["company_id"]:
            return row["company_id"]
        row = conn.execute("SELECT company_id FROM users WHERE role='admin' LIMIT 1").fetchone()
        return row["company_id"] if row else None
    finally:
        conn.close()


class SyncService:
    def __init__(
        self,
        client_factory: Callable[[], "SyncRelayClient"],
        get_conn: Callable[[], "sqlite3.Connection"],
        local_company_id_provider: Optional[Callable[[], Optional[str]]] = None,
        *,
        push_batch_limit: int = DEFAULT_OUTBOX_PUSH_LIMIT,
    ):
        """`client_factory` is called fresh on every push_once()/pull_once()
        attempt, not once at construction time -- deliberately, mirroring
        `commercial_runtime/licensing_contracts/routes.py`'s own
        `_build_context()`, which rebuilds its object graph on every request
        rather than caching it at blueprint-creation time. A SyncRelayClient
        built once at process start would freeze whatever `installation_id`
        was on disk at that moment (commonly None, before the operator has
        even activated the license yet); rebuilding on every attempt means
        activation completing later in the same process is picked up on the
        very next tick with no restart required.

        `local_company_id_provider`, when supplied, is called fresh on every
        `apply_pull_result()` batch that actually needs it (never cached) --
        see the module docstring's "Cross-device `company_id` bug fix" note.
        `_apply_event` uses its return value, never the pulled payload's own
        `company_id`, when writing a pulled row locally. Deliberately no
        default here (unlike `local_company_id_from_registry` being a free
        function callers may pass in): a `SyncService` constructed without
        one raises loudly the first time a category event actually needs it,
        rather than silently touching a real on-disk registry DB a test (or
        some future caller) never intended to read.

        `push_batch_limit` -- see the module docstring's "Outbox-wedge fix"
        note. Constructor-overridable (default `DEFAULT_OUTBOX_PUSH_LIMIT`)
        purely so tests can exercise chunking without needing to insert
        hundreds of real rows; production code should never need to pass
        this."""
        self._client_factory = client_factory
        self._get_conn = get_conn
        self._local_company_id_provider = local_company_id_provider
        self._push_batch_limit = push_batch_limit
        self._timer: Optional[threading.Timer] = None
        self._stopped = threading.Event()
        # Serializes push_once/pull_once against each other -- the 10s timer
        # tick and an immediate route-triggered nudge() can otherwise land on
        # two different threads at once, both reading/draining the same
        # sync_outbox rows.
        self._lock = threading.Lock()

    def push_once(self) -> None:
        """Drains ONE bounded chunk (`self._push_batch_limit` rows, oldest
        first) of sync_outbox to Owner per call -- see the module docstring's
        "Outbox-wedge fix" note for why this is chunked at all. Reads the
        current rows, pushes them, and ONLY deletes exactly those rows on
        success -- a transient push failure (`NetworkError`: network or
        Owner temporarily unavailable) raises back to the caller with the
        outbox left completely untouched, exactly as before (nothing read is
        deleted, and nothing new that arrived concurrently is at risk, since
        only the specific ids just pushed are ever deleted). A full backlog
        larger than one chunk drains over multiple calls -- the normal timer
        tick (or a route-triggered nudge()) simply picks up the next chunk
        next time, since ack_outbox() always removes exactly (and only) the
        rows that were just genuinely accepted.

        A business rejection (`RelayRejected` -- Owner reached the request
        and said no) is handled differently from a transient failure: see
        `_handle_rejected_batch` below."""
        with self._lock:
            conn = self._get_conn()
            try:
                events = self.read_outbox(conn, limit=self._push_batch_limit)
                if not events:
                    return
                try:
                    self._client_factory().push(events)
                except RelayRejected as exc:
                    self._handle_rejected_batch(conn, events, exc)
                else:
                    self.ack_outbox(conn, [e["id"] for e in events])
                    conn.commit()
            finally:
                # conn.close() with no prior commit() discards any
                # uncommitted work on this connection -- if push() raised
                # and _handle_rejected_batch already committed everything it
                # did (see that method), there is nothing left to lose here;
                # this only guarantees the connection itself is never
                # leaked.
                conn.close()

    def _handle_rejected_batch(self, conn, events: list, exc: "RelayRejected") -> None:
        """Called from push_once() the moment Owner rejects the current
        chunk as a whole. Records the rejection against every row in the
        batch, then either re-raises (ordinary swallow-and-retry-next-tick,
        via run_once()'s own try/except -- ordinary "not yet at threshold"
        path) or, once the SAME batch has now been rejected
        `DEAD_LETTER_THRESHOLD` times in a row, bisects it to isolate and
        quarantine the actual offending row(s) -- see `_bisect_and_quarantine`
        below. Caller (push_once) is responsible for `conn.close()`; this
        method commits its own work as it goes so a mid-bisection failure
        (e.g. a `NetworkError` on one sub-batch) never loses attempt/
        dead-letter progress already made on the OTHER sub-batch."""
        ids = [e["id"] for e in events]
        self._record_rejection(conn, ids, exc)
        conn.commit()
        if not self._is_batch_over_threshold(conn, ids):
            raise exc  # below threshold -- ordinary retry-next-tick path
        logger.error(
            "Sync outbox batch of %d row(s) rejected %d+ times in a row "
            "(reason_code=%s); bisecting to isolate the offending row(s) "
            "instead of leaving the whole outbox wedged behind them.",
            len(events), DEAD_LETTER_THRESHOLD, exc.reason_code,
        )
        self._bisect_and_quarantine(conn, events)

    def _record_rejection(self, conn, ids: list, exc: "RelayRejected") -> None:
        """Increments attempt_count and records last_error for exactly the
        given outbox row ids -- called once per rejected (sub-)batch, so
        every row that was actually part of THIS rejection is tracked, never
        rows outside it."""
        if not ids:
            return
        for start in range(0, len(ids), _ACK_CHUNK_SIZE):
            chunk = ids[start:start + _ACK_CHUNK_SIZE]
            conn.execute(
                "UPDATE sync_outbox SET attempt_count = attempt_count + 1, last_error = ? "
                "WHERE id IN ({})".format(",".join("?" * len(chunk))),
                [f"{exc.reason_code}: {exc}"] + chunk,
            )

    def _is_batch_over_threshold(self, conn, ids: list) -> bool:
        """True once ANY row in `ids` has reached DEAD_LETTER_THRESHOLD
        attempts. A batch that has never been bisected always has every row
        at the same attempt_count (they only ever fail together), so this is
        equivalent to checking the batch as a whole -- checking `any(...)`
        rather than `all(...)` keeps it correct once bisection is under way
        too, where different sub-batches can be at different counts."""
        if not ids:
            return False
        rows = conn.execute(
            "SELECT attempt_count FROM sync_outbox WHERE id IN ({})".format(",".join("?" * len(ids))),
            ids,
        ).fetchall()
        return any(r["attempt_count"] >= DEAD_LETTER_THRESHOLD for r in rows)

    def _bisect_and_quarantine(self, conn, events: list) -> None:
        """Recursively narrows a confirmed-bad `events` batch down to the
        individual offending row(s): splits it in half, pushes each half as
        its OWN push() call, and recurses into whichever half still gets
        rejected. A half that pushes successfully is genuinely accepted by
        Owner right there (it is NOT re-pushed again by push_once() after
        this returns) -- immediately ack'd and committed. A single-row
        "half" (the recursion's base case) that is STILL rejected after
        everything else has been ruled out IS the offender -- moved to
        sync_dead_letter and removed from the active outbox.

        A `NetworkError` at any point during bisection (a real connectivity
        blip, not a business rejection) is deliberately allowed to propagate
        all the way out of push_once() unhandled -- whatever bisection has
        already resolved (ack'd or dead-lettered) up to that point stays
        committed, and whatever is left unresolved simply stays in
        sync_outbox with its attempt_count as far as this got; the next tick
        re-reads it and continues narrowing rather than this being
        misdiagnosed as "the row is bad" from a transient blip."""
        if len(events) == 1:
            self._dead_letter_row(conn, events[0], "isolated by bisection: still rejected with nothing left to narrow against")
            conn.commit()
            return

        mid = len(events) // 2
        for half in (events[:mid], events[mid:]):
            if not half:
                continue
            try:
                self._client_factory().push(half)
            except RelayRejected as exc:
                half_ids = [e["id"] for e in half]
                self._record_rejection(conn, half_ids, exc)
                conn.commit()
                self._bisect_and_quarantine(conn, half)
            else:
                self.ack_outbox(conn, [e["id"] for e in half])
                conn.commit()

    def _dead_letter_row(self, conn, event: dict, note: str) -> None:
        """Moves exactly one sync_outbox row (by id) to sync_dead_letter and
        removes it from the active outbox, so it can never block another
        batch again. Reads the row fresh from sync_outbox (rather than
        trusting the in-memory `event` dict, whose `payload` is already
        JSON-decoded) so sync_dead_letter's `payload` column stays the same
        raw-JSON-TEXT shape sync_outbox itself uses."""
        row = conn.execute("SELECT * FROM sync_outbox WHERE id=?", (event["id"],)).fetchone()
        if row is None:
            return  # already moved by an earlier/concurrent call -- nothing to do
        conn.execute(
            "INSERT INTO sync_dead_letter "
            "(id, entity_type, entity_id, event_type, payload, created_at, attempt_count, last_error, dead_lettered_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                row["id"], row["entity_type"], row["entity_id"], row["event_type"], row["payload"],
                row["created_at"], row["attempt_count"], row["last_error"] or note,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.execute("DELETE FROM sync_outbox WHERE id=?", (row["id"],))
        logger.error(
            "Sync outbox row %s (entity_type=%s entity_id=%s event_type=%s) permanently "
            "rejected after %d attempt(s); moved to sync_dead_letter for manual review. "
            "last_error=%s",
            row["id"], row["entity_type"], row["entity_id"], row["event_type"],
            row["attempt_count"], row["last_error"],
        )

    def pull_once(self) -> None:
        """Pulls events newer than the local cursor and applies them, then
        advances the cursor to Owner's returned cursor value -- all in the
        one local transaction, so a mid-apply failure leaves the cursor
        exactly where it was (next attempt re-pulls the same range, not a
        gap)."""
        with self._lock:
            conn = self._get_conn()
            try:
                since = self.read_cursor(conn)
                result = self._client_factory().pull(since)  # raises on failure
                self.apply_pull_result(conn, result)
                conn.commit()
            finally:
                conn.close()

    def read_outbox(self, conn, limit: int = DEFAULT_OUTBOX_PUSH_LIMIT) -> list:
        """Reads (never deletes) up to `limit` of the OLDEST current
        sync_outbox rows, in the same shape push() expects. Split out of
        push_once() (multi-device-sync-foundation, Task 9) so Android's local
        `/_internal` sync routes can read the batch Kotlin is about to push
        WITHOUT this Python process ever calling `self._client_factory().push()`
        itself -- Android's Python never holds the signing key (see
        commercial_runtime/licensing_contracts/android_bridge_identity.py),
        so Kotlin makes the actual signed HTTP call and this method only
        hands it the rows to sign and send.

        Ordered `created_at, rowid` -- NOT `created_at` alone. `created_at`
        is a plain string and, pre-schema-v7, was not even guaranteed to be
        in the same (T-separated isoformat) shape for every row -- see
        `database/schema.py`'s `RETAIL_SCHEMA_VERSION` v7 comment and this
        module's own docstring for the full story. The tiebreaker is
        SQLite's own hidden `rowid` (implicit on any ordinary, non-`WITHOUT
        ROWID` table -- sync_outbox is not one), monotonically assigned in
        insertion order, rather than this table's declared `id` column:
        `id` is a client-generated UUID (`_queue_sync_event` in
        `retail_api.py`), which carries no relationship to insertion order
        at all and would make a just-as-broken tiebreaker as no tiebreaker.
        `limit` bounds a chunk to `DEFAULT_OUTBOX_PUSH_LIMIT` rows by default
        -- see the module docstring's "Outbox-wedge fix" note -- so a large
        backlog drains over several calls instead of one oversized push()."""
        rows = conn.execute(
            "SELECT * FROM sync_outbox ORDER BY created_at, rowid LIMIT ?", (limit,)
        ).fetchall()
        return [
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

    def ack_outbox(self, conn, ids: list) -> None:
        """Deletes exactly the given outbox row ids -- the second half of
        the read_outbox()/ack_outbox() split push_once() now composes
        itself, and what Android's `/_internal/sync/outbox/ack` route calls
        once Kotlin's push to Owner has genuinely succeeded. Caller commits.

        Chunked at `_ACK_CHUNK_SIZE` (well under SQLite's own ~999
        bound-parameter cap per statement) -- `read_outbox`'s own default
        limit keeps a normal desktop-driven ack well under that on its own,
        but `ids` can also arrive from Android's `/_internal/outbox/ack`
        route with however many ids Kotlin's own (unbounded, out of scope
        for this fix -- see commercial_runtime/sync/sync_service.py's module
        docstring) push batch happened to contain, so this chunks
        unconditionally rather than assuming the caller already bounded
        it."""
        if not ids:
            return
        for start in range(0, len(ids), _ACK_CHUNK_SIZE):
            chunk = ids[start:start + _ACK_CHUNK_SIZE]
            conn.execute(
                "DELETE FROM sync_outbox WHERE id IN ({})".format(",".join("?" * len(chunk))), chunk
            )

    def read_cursor(self, conn) -> int:
        cursor_row = conn.execute("SELECT last_seq FROM sync_cursor WHERE id=1").fetchone()
        return cursor_row["last_seq"] if cursor_row else 0

    def apply_pull_result(self, conn, result: dict) -> None:
        """Applies a pull result (the exact shape SyncRelayClient.pull()
        returns: `{"events": [...], "cursor": N}`) to local tables and
        advances the cursor. Split out of pull_once() (Task 9) so it can be
        called two ways with identical semantics: pull_once() calls it right
        after fetching the result itself (Windows), and Android's local
        `/_internal/sync/pull-apply` route calls it with a result Kotlin
        already fetched directly from Owner (Android's Python never makes
        that signed HTTP call itself). Caller commits."""
        events = result.get("events", [])
        # Fetched at most once per batch (not once per event) -- it is the
        # SAME value (this device's own company_id) for every event in the
        # batch. Only actually needed for a category create/update (the ONLY
        # writes that stamp a company_id) -- a batch of pure deletes (keyed
        # by categories.id alone) or unknown entity types never touches the
        # provider at all, so a SyncService with no provider configured can
        # still apply those without raising.
        local_company_id = None
        if any(
            ev.get("entity_type") in ("category", "product", "customer", "supplier") and ev.get("event_type") in ("create", "update")
            for ev in events
        ):
            local_company_id = self._get_local_company_id()
        for ev in events:
            self._apply_event(conn, ev, local_company_id)
        conn.execute("UPDATE sync_cursor SET last_seq=? WHERE id=1", (result["cursor"],))

    def _get_local_company_id(self) -> str:
        """Returns THIS device's own locally-authoritative company_id --
        never the pulled payload's. Raises rather than silently applying a
        pulled row under the wrong (or no) company_id: a misconfigured
        SyncService (no provider wired) or a device that hasn't finished its
        own onboarding yet (provider returns None/empty) must surface loudly
        here, exactly like a network failure -- pull_once()'s caller already
        swallows this per-tick (see run_once()'s docstring) and retries."""
        if self._local_company_id_provider is None:
            raise RuntimeError(
                "SyncService has no local_company_id_provider configured; "
                "cannot apply a pulled category event without knowing this "
                "device's own company_id."
            )
        company_id = self._local_company_id_provider()
        if not company_id:
            raise RuntimeError(
                "local_company_id_provider returned no company_id for this "
                "device (onboarding not yet complete?); cannot apply pulled "
                "category events until it does."
            )
        return company_id

    def _apply_event(self, conn, ev: dict, local_company_id: Optional[str] = None) -> None:
        entity_type = ev.get("entity_type")
        if entity_type not in ("category", "product", "customer", "supplier"):
            return
        p = ev.get("payload") or {}
        event_type = ev.get("event_type")
        if entity_type == "category":
            if event_type in ("create", "update"):
                # `local_company_id` -- THIS device's own company_id -- not
                # `p.get("company_id")`, which is the SENDING device's company_id
                # and is never valid to write into a local row here. See the
                # module docstring's "Cross-device company_id bug fix" note.
                conn.execute(
                    "INSERT INTO categories (id, company_id, name, description) VALUES (?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description",
                    (p.get("id"), local_company_id, p.get("name"), p.get("description", "")),
                )
            elif event_type == "delete":
                conn.execute("DELETE FROM categories WHERE id=?", (p.get("id"),))
        elif entity_type == "product":
            if event_type in ("create", "update"):
                # AUDIT-follow-up (2026-08-10): supplier_id and status were both
                # missing here even though retail_api.py's outbox payload already
                # carries both -- supplier_id never propagated cross-device at
                # all, and a soft-delete/restore (products.status) round-trip
                # via an "update" event silently never took effect on other
                # devices. `status` is parameterized (never a hardcoded 'active'
                # literal) so `excluded.status` reflects the SENDING device's
                # actual current status -- a create event's payload never
                # carries status, so p.get(..., "active") preserves the
                # previous create-time default exactly.
                conn.execute(
                    "INSERT INTO products (id, company_id, sku, barcode, name, category_id, supplier_id, "
                    "cost_price, sell_price, tax_rate, unit, reorder_level, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET sku=excluded.sku, barcode=excluded.barcode, name=excluded.name, "
                    "category_id=excluded.category_id, supplier_id=excluded.supplier_id, "
                    "cost_price=excluded.cost_price, sell_price=excluded.sell_price, "
                    "tax_rate=excluded.tax_rate, unit=excluded.unit, reorder_level=excluded.reorder_level, "
                    "status=excluded.status",
                    (p.get("id"), local_company_id, p.get("sku"), p.get("barcode", ""), p.get("name"),
                     p.get("category_id"), p.get("supplier_id"), p.get("cost_price", 0), p.get("sell_price", 0),
                     p.get("tax_rate", 0), p.get("unit", "pcs"), p.get("reorder_level", 5),
                     p.get("status", "active")),
                )
            elif event_type == "delete":
                conn.execute("UPDATE products SET status='inactive' WHERE id=?", (p.get("id"),))
        elif entity_type == "customer":
            if event_type in ("create", "update"):
                # `status` parameterized for the same reason as the product
                # upsert above -- see that block's comment.
                conn.execute(
                    "INSERT INTO customers (id, company_id, name, phone, email, address, status) "
                    "VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, phone=excluded.phone, "
                    "email=excluded.email, address=excluded.address, status=excluded.status",
                    (p.get("id"), local_company_id, p.get("name"), p.get("phone", ""),
                     p.get("email", ""), p.get("address", ""), p.get("status", "active")),
                )
            elif event_type == "delete":
                conn.execute("UPDATE customers SET status='inactive' WHERE id=?", (p.get("id"),))
        elif entity_type == "supplier":
            if event_type in ("create", "update"):
                # `status` parameterized for the same reason as the product
                # upsert above -- see that block's comment.
                conn.execute(
                    "INSERT INTO suppliers (id, company_id, name, phone, email, address, status) "
                    "VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, phone=excluded.phone, "
                    "email=excluded.email, address=excluded.address, status=excluded.status",
                    (p.get("id"), local_company_id, p.get("name"), p.get("phone", ""),
                     p.get("email", ""), p.get("address", ""), p.get("status", "active")),
                )
            elif event_type == "delete":
                conn.execute("UPDATE suppliers SET status='inactive' WHERE id=?", (p.get("id"),))

    def run_once(self) -> None:
        """The one entry point the timer tick (and the manual/CLI caller)
        uses. Never lets a relay/network failure escape -- an unreachable or
        rejecting relay is an expected, ordinary condition (offline device),
        not a reason to crash the host app. The next scheduled tick (or the
        next route-triggered nudge) simply retries.

        push and pull are each wrapped in their OWN try/except -- found via
        a real test (test_run_once_swallows_push_failure_and_still_attempts_
        pull), not by inspection: a single shared try/except around both
        calls (the shape this originally mirrored from the brief's own
        draft) means a push failure -- which is not always "offline"; it can
        be a real, persistent RelayRejected against one bad outbox row
        (e.g. a malformed event a human has to go fix) -- would silently
        skip pull() forever until that one row is resolved. This device
        should keep receiving OTHER devices' updates regardless of whether
        its own outbox is currently able to drain."""
        try:
            self.push_once()
        except Exception:
            logger.info("Sync push attempt failed (offline or rejected); will retry on the next tick.", exc_info=True)
        try:
            self.pull_once()
        except Exception:
            logger.info("Sync pull attempt failed (offline or rejected); will retry on the next tick.", exc_info=True)

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
