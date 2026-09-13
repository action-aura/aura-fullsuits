"""Hub-to-cloud forwarder for the LAN site relay (retail schema v30,
`products/retail/backend/database/schema.py`'s `_migrate_add_site_relay`;
that function's own `site_forward_cursor` COLUMN BY COLUMN section is this
module's other required reading, alongside
`docs/launch-readiness/lan-restaurant-design.md` sec6 ("C. When the
internet returns: store-and-forward, one relay per device"), which this
module implements end to end.

WHAT THIS MODULE IS, in one sentence: the bridge that keeps "every device
talks to exactly ONE relay, ever" (sec6's own invariant) true for the SHOP
as a whole once the hub itself is counted as a device -- paired LAN
devices talk only to the hub's embedded site relay (`store.py`/`routes.py`,
this package's siblings) and never touch the internet directly; this
module is what carries the hub's accumulated site log up to Owner's real
cloud relay whenever the internet is reachable, and carries the cloud's
own stream back down onto the LAN so paired devices receive it on their
next ordinary site-relay pull.

TWO SEQUENCE-SPACES, ONE ROW: the hub is the one device in this whole
design that deliberately sees two independent orderings at once -- its own
site log (`site_sync_events.seq`, this hub's local ordering authority) and
Owner's cloud stream (the cloud relay's own Postgres IDENTITY `seq`, which
this hub only ever learns about through `cloud_client.pull()`'s returned
`cursor` field, never directly). `site_forward_cursor` holds this module's
position in BOTH spaces, together, in one single row -- see that table's
own migration docstring for why splitting the two numbers across two
tables would invite a future change to advance one half without the
other.

OWNERSHIP: this module is `site_forward_cursor`'s ONLY reader and writer
in the entire codebase -- not `store.py` (whose own module docstring
explicitly disclaims it, see its "NOT PART OF THIS MODULE" paragraph), and
not any future route handler. That is deliberate, not an oversight: the
ack-after-success discipline below (the entire reason this module exists
rather than a bare pair of counters bumped from wherever) can only be
reasoned about if exactly one piece of code ever advances either half of
this row. Do NOT move `read_forward_cursor`/`_advance_forwarded_to`/
`_advance_cloud_pull` into `store.py` "for consistency" with how that
module owns the other five site-relay tables -- `store.py` deliberately
does NOT own this one, and giving it a second writer would silently
reintroduce exactly the hazard a single-writer table is supposed to rule
out.

THE ECHO GUARD, THE SINGLE MOST IMPORTANT INVARIANT IN THIS FILE: a row
this hub itself pulled DOWN from the cloud is stored in the site log with
`origin_device_id = store.UPSTREAM_ORIGIN` (see `pull_down`'s own call to
`store.append_events` below). `forward_up` excludes exactly that sentinel
when it reads what to push back UP. Without that exclusion, every event
the cloud ever hands this hub would be forwarded straight back to the
cloud on the very next tick, forever, under the HUB's own signature rather
than the originating device's -- from the cloud's point of view, a second,
distinct device re-asserting facts it already has. Event-UUID dedup on the
cloud side (`_store_events`'s existence-check + SAVEPOINT, sec6) means
this could never corrupt anything -- it would simply never stop: an
unbounded, pointless round trip on every single tick for the rest of the
install's life. See `forward_up`'s own docstring for exactly which line
this depends on, and this package's test file for the mutation proof.

CLOUD CLIENT IS DUCK-TYPED, NOT IMPORTED: this module takes `cloud_client`
as a plain parameter and never imports `relay_client.SyncRelayClient`
itself (see `_CloudRelayClient` below -- a `Protocol`, purely documentary,
never enforced at runtime). The real caller wires the genuine
`commercial_runtime.sync.relay_client.SyncRelayClient`; this package's own
test file wires a small fake instead. This mirrors this module's OTHER
injected dependency, `apply_pulled` (see `pull_down`'s own docstring) --
both exist so this file can be exercised as pure cursor/echo-guard logic
against the real `store.py` and the real schema, with no HTTP and no
import of `sync_service.SyncService` (which does not know this module
exists, and should not have to).

COMMIT/ROLLBACK CONTRACT -- DELIBERATELY DIFFERENT FROM `store.py`'s:
`store.py`'s own module docstring states its functions never call
`conn.commit()`/`conn.rollback()`, because every function there is a
single, already-atomic SQL statement and the CALLER decides when a unit of
work spanning several DAO calls becomes durable. This module is not that
kind of function: `forward_up` and `pull_down` are each a complete unit of
work in their own right -- an external network call followed by a local
watermark write that must never survive without it (or, for `pull_down`,
several local writes that must never survive PARTIALLY) -- exactly the
shape `sync_service.py`'s `push_once`/`pull_once` already are. So, like
those two, both functions below own their own commit on confirmed success,
and `pull_down` additionally owns an explicit rollback on any failure
partway through its three-step transaction. See each function's own
docstring for the specifics.
"""
from __future__ import annotations

import sqlite3
from typing import Callable, Protocol

from commercial_runtime.sync.site_relay import store

# Reuse store.py's own push-batch cap rather than redeclaring it -- see
# that constant's own comment in store.py (`MAX_PUSH_BATCH`) for why a
# batch-size ceiling exists at all (it mirrors Owner's own
# `_MAX_PUSH_BATCH = 200`, the cloud relay's outright-rejection threshold
# for a single push). One canonical value, imported here, rather than a
# second copy that could silently drift from store.py's.
FORWARD_BATCH_SIZE = store.MAX_PUSH_BATCH


class _CloudRelayClient(Protocol):
    """Documents the exact two calls this module makes against Owner's
    cloud relay -- see `commercial_runtime/sync/relay_client.py`'s
    `SyncRelayClient` for the real implementation this stands in for.
    Never imported for real (see module docstring's "CLOUD CLIENT IS
    DUCK-TYPED" paragraph): this `Protocol` exists purely for readers (and
    a type checker), so this module itself takes no dependency on
    `relay_client.py`, `requests`, or the licensing canonicalizer."""

    def push(self, events: list) -> dict:
        """Raises (a `SyncRelayClientError` subclass, in the real client)
        on any failure -- transport-level or a genuine Owner rejection. On
        success, returns Owner's parsed JSON body (e.g. `{"stored": N,
        "received": N}`), which this module never inspects: a non-raising
        return IS the "confirmed successful push" signal `forward_up` acts
        on, exactly like `push_once`'s own `client.push(chunk)` call
        immediately before its own ack."""
        ...

    def pull(self, since: int) -> dict:
        """Raises on failure, identically to `push` above. On success,
        returns `{"events": [...], "cursor": N}` -- the same shape
        `sync_service.py::apply_pull_result`'s own docstring documents for
        the identical call against the SAME relay (this hub, pulling from
        the cloud, is just an ordinary client from Owner's point of
        view)."""
        ...


def read_forward_cursor(conn: sqlite3.Connection) -> tuple[int, int]:
    """Returns `(forwarded_to_seq, cloud_pull_seq)` from the single
    `site_forward_cursor` row -- see module docstring's "TWO
    SEQUENCE-SPACES, ONE ROW" paragraph for why both numbers live
    together. The row always exists once `_migrate_add_site_relay` has run
    (seeded with `INSERT OR IGNORE ... VALUES (1, 0, 0)`), but this still
    falls back to `(0, 0)` if it is somehow missing, mirroring
    `sync_service.py::read_cursor`'s identical defensive fallback for
    `sync_cursor` -- a fresh hub that has never forwarded or pulled
    anything should behave exactly as if both watermarks were zero, not
    raise."""
    row = conn.execute(
        "SELECT forwarded_to_seq, cloud_pull_seq FROM site_forward_cursor WHERE id = 1"
    ).fetchone()
    if row is None:
        return (0, 0)
    return (row["forwarded_to_seq"], row["cloud_pull_seq"])


def _advance_forwarded_to(conn: sqlite3.Connection, seq: int) -> None:
    """Private: `site_forward_cursor` is this module's own state (see
    module docstring's "OWNERSHIP" paragraph) -- nothing outside this file
    should ever write this column directly, which is exactly why this
    function is underscore-prefixed rather than exported alongside
    `read_forward_cursor`.

    Uses `MAX(forwarded_to_seq, ?)` rather than a bare `SET`, the same
    upsert-guard idiom `store.advance_device_cursor` already uses for
    `site_device_cursors`, for the identical reason stated there: a
    watermark that has already advanced past `seq` must never be walked
    backward by a redundant or out-of-order call. `forward_up`'s own
    single-threaded, ascending-`seq` read means `seq` here is always >=
    the currently stored value in ordinary operation -- this guard is a
    zero-cost belt-and-braces against a future caller breaking that
    assumption, not a behaviour this module's own call sites currently
    depend on."""
    conn.execute(
        "UPDATE site_forward_cursor SET "
        "forwarded_to_seq = MAX(forwarded_to_seq, ?), "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE id = 1",
        (seq,),
    )


def _advance_cloud_pull(conn: sqlite3.Connection, seq: int) -> None:
    """Private, mirroring `_advance_forwarded_to` above in every respect
    except which half of `site_forward_cursor` it writes -- see that
    function's own docstring for the full reasoning (ownership, the
    MAX-guard, and why this is not exported)."""
    conn.execute(
        "UPDATE site_forward_cursor SET "
        "cloud_pull_seq = MAX(cloud_pull_seq, ?), "
        "updated_at = CURRENT_TIMESTAMP "
        "WHERE id = 1",
        (seq,),
    )


def forward_up(
    conn: sqlite3.Connection,
    cloud_client: _CloudRelayClient,
    *,
    batch_size: int = FORWARD_BATCH_SIZE,
) -> int:
    """Reads this hub's site log past `forwarded_to_seq`, pushes it to
    Owner's cloud relay under the hub's own signature, and advances the
    watermark ONLY once that push is confirmed successful. Returns the
    number of events forwarded (0 if there was nothing to send)."""
    forwarded_to_seq, _cloud_pull_seq = read_forward_cursor(conn)

    # THE ECHO GUARD (see module docstring). `store.UPSTREAM_ORIGIN` is the
    # sentinel `pull_down` stamps on every row it inserts after pulling it
    # DOWN from the cloud. Excluding it here is what stops this hub from
    # forwarding the cloud's own events straight back to the cloud under
    # its own signature: without this exclusion, `pull_down` handing an
    # event to the site log and `forward_up` reading "everything past the
    # watermark" would see that same row and re-push it forever -- a
    # second, distinct-looking device re-asserting facts the cloud already
    # has. No genuine paired LAN device, and none of the hub's own
    # locally-originated writes, ever uses this sentinel as an
    # `origin_device_id`, so this exclusion can never accidentally swallow
    # a real device's events -- it only ever removes rows `pull_down`
    # itself put there.
    rows = store.read_events_since(
        conn,
        since=forwarded_to_seq,
        exclude_device_id=store.UPSTREAM_ORIGIN,
        limit=batch_size,
    )
    if not rows:
        # Nothing forwardable this tick -- the watermark already correctly
        # reflects "caught up to everything there is", so there is nothing
        # to advance it TO. Touching it here (e.g. to "now") would gain
        # nothing and risks masking a future caller bug that treats a
        # nonzero write as evidence real progress happened.
        return 0

    # Wire shape the cloud relay actually accepts -- exactly the six
    # fields `store.py`'s own `_REQUIRED_EVENT_KEYS` names, nothing else.
    # `row["seq"]` (this HUB's own local ordering number) is deliberately
    # NOT included: it means nothing to Owner's cloud relay, which has its
    # own, unrelated seq space, and Owner's `_build_event` does not expect
    # it.
    #
    # PRESERVING THE ORIGINAL `id` AND `created_at` IS LOAD-BEARING, NOT
    # COSMETIC (sec6 of the design doc, and this task's own plan). The
    # cloud relay dedups incoming events on `id` alone -- re-minting a
    # fresh id per event here would turn every retried push (a dropped
    # response after Owner already durably stored the batch, a crash
    # between this call and this function's own cursor advance below)
    # into a brand-new, never-before-seen id from the cloud's point of
    # view, i.e. a duplicate row on every single retry -- precisely the
    # failure `id`-based dedup exists to prevent. Likewise `created_at` is
    # the ORIGIN device's own clock (see `site_sync_events.created_at`'s
    # own migration comment: "ORIGIN device's own clock, relayed verbatim,
    # never rewritten") -- it is the only ordering information that
    # device contributed, and overwriting it with this hub's own clock at
    # forward time would discard that information permanently, one hop
    # before it would otherwise have reached Owner.
    events = [
        {
            "id": row["id"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "event_type": row["event_type"],
            "payload": row["payload"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]

    # Raises on any failure -- transport-level (offline) or a genuine
    # Owner rejection -- and if it raises, EVERYTHING below this line
    # never runs: the watermark stays exactly where `read_forward_cursor`
    # found it, so the next call re-reads and re-pushes this identical
    # batch. Deliberately NOT wrapped in a try/except here -- `run_once`
    # (below) is where a push failure is caught, logged, and turned into a
    # non-raising result; this function's own job is only to get the
    # ack-after-success ORDERING right, not to decide how a failure is
    # reported. A watermark advanced on an unconfirmed push would silently
    # lose every event in this batch, permanently, with nothing anywhere
    # recording that it happened -- that is the one outcome this ordering
    # exists to make impossible.
    cloud_client.push(events)

    # Advance + commit ONLY here, after `push` returned without raising --
    # this is the entire ack-after-success discipline this function exists
    # to implement, mirroring `push_once`'s own `ack_outbox(...)` +
    # `conn.commit()` pair immediately after its own `client.push(chunk)`
    # call. `rows[-1]["seq"]` is safe to use unconditionally (rather than
    # re-querying `store.max_event_seq`) because `read_events_since`
    # returns oldest-first (`ORDER BY seq`), so the last element is always
    # the highest `seq` actually included in THIS batch -- not necessarily
    # the log's true maximum if `limit` truncated it, which is exactly
    # right: a truncated batch must only advance to what was actually
    # pushed, never past events this call never sent.
    _advance_forwarded_to(conn, rows[-1]["seq"])
    conn.commit()
    return len(rows)


def pull_down(
    conn: sqlite3.Connection,
    cloud_client: _CloudRelayClient,
    *,
    apply_pulled: Callable[[list], None],
) -> int:
    """Pulls Owner's cloud stream past `cloud_pull_seq`, applies it to the
    hub's own database, and delivers it to every paired LAN device by
    inserting it into the site log under `store.UPSTREAM_ORIGIN`. Returns
    the number of events applied (0 if there was nothing new to pull)."""
    _forwarded_to_seq, cloud_pull_seq = read_forward_cursor(conn)

    # Raises on failure exactly like `forward_up`'s own `cloud_client.push`
    # call above -- deliberately not wrapped here either; `run_once` is
    # where a pull failure becomes a non-raising, logged result.
    result = cloud_client.pull(since=cloud_pull_seq)
    events = result.get("events", [])
    # Owner's own `cursor` field. On an empty-rows response this may be
    # Owner's own zero-rows CLAMP -- the cloud-side mirror of the same
    # `min(since, max_seq)` shape `store.resolve_pull_cursor` implements
    # on THIS hub's own pull side -- so it is not necessarily equal to the
    # `since` this call sent. Falling back to `cloud_pull_seq` itself if
    # the key is somehow absent means "no change" rather than crashing on
    # a malformed response.
    returned_cursor = result.get("cursor", cloud_pull_seq)

    if not events:
        # Still worth persisting: Owner's own clamp may have corrected an
        # out-of-range `since` this hub previously recorded (a
        # restored-from-backup hub, a version-skewed client) down to
        # Owner's true position -- advancing to that corrected value now
        # is what lets this hub self-heal rather than repeating the same
        # corrected pull forever. Guarded by strict `>` (never `>=`) so
        # the ordinary "nothing new happened, cursor unchanged" case never
        # issues a pointless write.
        if returned_cursor > cloud_pull_seq:
            _advance_cloud_pull(conn, returned_cursor)
            conn.commit()
        return 0

    # ONE TRANSACTION, ALL THREE STEPS OR NONE -- mirrors `pull_once`'s own
    # "cursor never advances on a failed apply" discipline exactly. A
    # partially-applied pull (business tables written but the site log or
    # the watermark left stale, or vice-versa) would leave this hub unable
    # to tell what it actually did on its next attempt. Rolling back on
    # ANY failure here, then re-raising, guarantees the NEXT call re-reads
    # the identical `since` and re-does the identical work -- never a gap,
    # never a partially-applied state surviving a crash.
    try:
        # (a) Apply to the hub's own database. An injected callable, not a
        # direct `SyncService.apply_pull_result` import, so this module
        # never depends on `sync_service.py` (see module docstring) and
        # stays testable with a plain function/fake. The real caller wires
        # a closure over the SAME `conn` this function received (e.g.
        # `apply_pull_result(conn, {"events": events, "cursor":
        # returned_cursor})`) -- that shared connection is what makes "ONE
        # transaction" a genuine guarantee rather than a comment: the
        # site-relay tables and the hub's ordinary business tables live in
        # the exact same SQLite file (`_migrate_add_site_relay` is
        # additive onto the same retail database every other migration
        # writes to), so a single connection's commit/rollback covers
        # both sides of this call at once.
        apply_pulled(events)

        # (b) Deliver to every paired LAN device. THIS is the mechanism
        # that actually gets a cloud-originated event onto the LAN --
        # `origin_device_id=store.UPSTREAM_ORIGIN` marks these rows so (i)
        # `read_events_since`'s per-device exclusion never treats them as
        # belonging to any real device, so every paired device receives
        # them on its own next ordinary site-relay pull, and (ii)
        # `forward_up` above recognizes and excludes them, closing the
        # loop (see module docstring's "THE ECHO GUARD").
        store.append_events(conn, events, origin_device_id=store.UPSTREAM_ORIGIN)

        # (c) Advance this module's own downstream watermark.
        _advance_cloud_pull(conn, returned_cursor)
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()

    return len(events)


def _error_label(exc: BaseException) -> str:
    """Short, human-safe failure label for `run_once`'s result dict --
    mirrors `sync_service.py::SyncService._failure_reason` exactly, same
    signature and same reasoning: NEVER the raw `str(exc)`, because a
    `NetworkError` from `relay_client.py` embeds the full relay URL in its
    message, and this result dict is exactly the kind of thing that ends
    up in a log line or a diagnostics screen. A `SyncRelayClientError`
    subclass already carries the precise `reason_code` Owner returned (or
    one of this client's own transport-failure codes); anything else falls
    back to its exception class name."""
    return getattr(exc, "reason_code", None) or type(exc).__name__


def run_once(
    conn: sqlite3.Connection,
    cloud_client: _CloudRelayClient,
    *,
    apply_pulled: Callable[[list], None],
) -> dict:
    """The one entry point a forwarder tick (a timer, or a manual/CLI
    trigger) uses. Never lets a push or pull failure escape -- mirrors
    `sync_service.py::SyncService.run_once` in both structure and its own
    stated reasoning: push and pull are each wrapped in their OWN
    try/except, not one shared try/except around both, because a push
    failure (which can be a real, persistent rejection against one bad
    event -- not merely "offline") must never silently block this hub from
    receiving OTHER devices' updates via pull. Every paired LAN device
    downstream of this hub depends on `pull_down` continuing to run even
    while `forward_up` is stuck.

    Returns `{"forwarded": int, "pulled": int, "push_error": str | None,
    "pull_error": str | None}` -- never raises."""
    result = {"forwarded": 0, "pulled": 0, "push_error": None, "pull_error": None}

    try:
        result["forwarded"] = forward_up(conn, cloud_client)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
        result["push_error"] = _error_label(exc)

    try:
        result["pulled"] = pull_down(conn, cloud_client, apply_pulled=apply_pulled)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad, see docstring
        result["pull_error"] = _error_label(exc)

    return result
