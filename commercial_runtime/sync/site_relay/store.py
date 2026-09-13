"""Raw-sqlite3 DAO for the LAN site relay's local tables (retail schema v30,
`products/retail/backend/database/schema.py`'s `_migrate_add_site_relay`;
ROADMAP.md's "2026-09-14 - retail schema v30 CLAIMED for the LAN site relay"
entry). Port of the storage half of `owner/app/sync/routes.py` onto SQLite --
that file is SQLAlchemy/Postgres, this one is stdlib `sqlite3` with hand-
written SQL, so this is a REWRITE of the same guarantees, not a transliteration.
See `docs/launch-readiness/lan-restaurant-design.md` sec3 ("Mechanics of the
site relay") for why this table set exists and sec5 for what authenticates
against it (`replay.py`, this package's sibling module).

CONNECTION CONTRACT, matching `commercial_runtime/sync/sync_service.py`'s own
convention exactly: every function here takes an ALREADY-OPEN
`sqlite3.Connection` as its first argument and never opens or closes one
itself -- the caller (a future push/pull route handler, or a test) owns the
connection's lifetime. Every function also assumes the connection's
`row_factory` is already `sqlite3.Row` (again matching `sync_service.py`,
which makes the identical assumption in `read_outbox` et al.) -- setting it
is the CALLER's job, not this module's, since a module that silently
reconfigured a connection handed to it by someone else could surprise other
code sharing that same connection.

TRANSACTION CONTRACT: no function in this module ever calls `conn.commit()`
or `conn.rollback()` -- the caller decides when a unit of work is durable,
exactly like every function in `sync_service.py` (`push_once`/`pull_once`
commit once, after everything in a batch has been written, never per-row).
The ONE exception in this whole package is `replay.consume_nonce` (a sibling
module, not this one) -- see that function's own docstring for why burning a
nonce must be durable independent of whatever the rest of the request does
next. Nothing in THIS module needs that exception: every table here is
either idempotent-by-id (`site_sync_events`, dedup on the UNIQUE `id`) or a
plain upsert with no burn-once semantics (`site_device_cursors`,
`site_paired_devices`), so there is no equivalent "must survive a later
rollback" requirement anywhere in this file.

DELIBERATELY NO PER-LICENCE SCOPING ANYWHERE IN THIS MODULE. Every query
here is unscoped by any `license_id`/`company_id` column, unlike Owner's
`owner/app/sync/routes.py`, whose every query is scoped by the verified
Installation's `license_id` (see that file's `_lock_license_stream` and
`pull()` for why: one Postgres database serves MANY licences at once). This
hub is a single retail install's own SQLite file serving exactly ONE
licence, because the hub IS the till, and a till is one install with one
database -- there is structurally only ever one licence's data in this
database to begin with, retail or otherwise. Do NOT add a `license_id`
column to any of these tables "for symmetry" with Owner's schema if this
module is ever revisited: it would be a column with exactly one distinct
value forever, adding a WHERE clause to every query for a distinction that
cannot occur here, and inviting exactly the kind of multi-tenant assumption
that does not hold for an embedded per-till relay.

NOT PART OF THIS MODULE: `site_forward_cursor` (the hub-to-cloud forwarder's
own watermark) and `site_roster` (the cached Owner-signed device roster).
Both tables are claimed by the same v30 migration this module's tables come
from, but neither has a DAO function here -- the forwarder and the roster
fetch/verify/enforce logic are separate, not-yet-built pieces of the R-LAN
wave (see `lan-restaurant-design.md` sec6 and sec5 respectively) and were
out of scope for this task.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Iterable, Optional

# Mirrors `owner/app/sync/routes.py::pull`'s own `.limit(500)` -- the same
# per-pull row cap, so a LAN device pulling from this hub sees the same
# batching behaviour it would see pulling from Owner's cloud relay. Not
# imported from Owner (a separately deployed service this client can never
# import at runtime -- see `sync_service.py`'s own `_PUSH_CHUNK_SIZE`
# comment for the identical reasoning about why this is a separate literal,
# not a shared import).
PULL_LIMIT = 500

# Mirrors `owner/app/sync/routes.py`'s own `_MAX_PUSH_BATCH = 200`. This
# module does not itself enforce the cap (no function here takes a whole
# "batch" and rejects it outright -- `append_events` just inserts whatever
# list it is given) because batch-size REJECTION is a route-handler
# concern, exactly like Owner's own `push()` checks `len(events) >
# _MAX_PUSH_BATCH` before ever calling its storage layer. This constant is
# exported from here so the (not-yet-built) site relay push handler has one
# canonical value to import rather than re-declaring its own copy that
# could drift from this module's.
MAX_PUSH_BATCH = 200

# Mirrors Owner's `_ENTITY_TYPE_MAX_LEN = 64` (`owner/app/sync/routes.py`),
# which itself matches `SyncEvent.entity_type`'s Postgres `String(64)`
# column. `site_sync_events.entity_type` is a bare SQLite `TEXT` with no
# engine-enforced length cap, but this DAO enforces the SAME limit at the
# application layer so an event this hub accepts is never wider than what
# Owner's cloud relay is willing to accept when the hub's forwarder later
# pushes it upstream -- accepting something HERE that would only be
# rejected THERE would be a silently un-forwardable event.
ENTITY_TYPE_MAX_LEN = 64

# Matches Owner's `_ALLOWED_EVENT_TYPES = ("create", "update", "delete")`
# exactly (`owner/app/sync/routes.py`) -- the same three verbs, because this
# hub relays the identical wire contract Owner's cloud relay speaks, and a
# LAN device's event stream is forwarded upstream verbatim (see this
# module's own docstring on `UPSTREAM_ORIGIN` and
# `lan-restaurant-design.md` sec6).
_ALLOWED_EVENT_TYPES = ("create", "update", "delete")

# `origin_device_id` stamped on a `site_sync_events` row that this hub
# pulled DOWN from Owner's cloud relay, rather than one accepted directly
# from a paired LAN device's own push. This is what lets
# `read_events_since`'s `origin_device_id != ?` exclusion (see that
# function's own docstring) correctly hand a cloud-forwarded event to EVERY
# paired LAN device -- none of them "originated" it, so none of them are
# excluded from receiving it. A real `installation_id` would incorrectly
# exclude that one device if it happened to match; a fixed sentinel that no
# genuine installation_id can ever collide with (installation ids are
# UUIDs; this is not one) avoids that entirely. Not yet written by any code
# in THIS module -- see the module docstring's "NOT PART OF THIS MODULE"
# paragraph: the forwarder that will write rows with this origin is a
# separate, not-yet-built piece. Defined here so the forwarder (whenever it
# is built) and this module's own `read_events_since` share exactly one
# spelling of the sentinel, rather than each hardcoding their own string
# that could drift apart.
UPSTREAM_ORIGIN = "__upstream__"

_REQUIRED_EVENT_KEYS = ("id", "entity_type", "entity_id", "event_type", "payload", "created_at")


class InvalidEventError(ValueError):
    """Raised by `validate_event` (and, by extension, by `append_events`,
    which calls it per event -- see that function's own docstring for why)
    for any malformed item in a push batch. `ValueError` subclass, not a
    bare `Exception`, so a caller that already catches `ValueError` broadly
    around this DAO's boundary (the common Python idiom for "reject bad
    input") catches this without needing to know this module's own
    exception hierarchy."""


def validate_event(raw) -> dict:
    """Validates and normalizes one item of a push batch, returning a clean
    dict of exactly the required fields on success. Raises
    `InvalidEventError` on any malformed field.

    Ported from `owner/app/sync/routes.py::_build_event`, with ONE
    deliberate loosening, called out here rather than silently matched:
    Owner requires `id` and `entity_id` to parse as `uuid.UUID` (`SyncEvent`
    is a Postgres table whose `id`/`entity_id` columns are genuinely typed
    `uuid`). This DAO's `site_sync_events.id`/`entity_id` are plain SQLite
    `TEXT` -- there is no column-level UUID type to satisfy, and this event
    log's readers (this hub's own paired LAN devices, and eventually the
    forwarder relaying rows upstream) never assume the id parses as a UUID
    either. Tightening this check to Owner's UUID requirement would REJECT
    event shapes this SQLite log can legitimately carry for no correctness
    reason on this side of the wire -- do not "fix" this by adding a
    `uuid.UUID(...)` parse here; that would be re-imposing a Postgres
    column constraint on a table that was never given one.

    Every other check below mirrors `_build_event` exactly: all six
    required keys present; `entity_type` a non-empty string within
    `ENTITY_TYPE_MAX_LEN`; `event_type` one of `_ALLOWED_EVENT_TYPES`;
    `payload` a dict. `id`/`entity_id`/`created_at` are required to be
    non-empty strings (this log stores all three as TEXT, and an empty or
    non-string value could never round-trip through `read_events_since` or
    a future forwarder push meaningfully) -- looser than Owner's UUID/
    parsed-timestamp checks for `id`/`entity_id`/`created_at` respectively,
    for the identical "no column-level type to satisfy" reasoning as the
    UUID relaxation above, but not so loose that a non-string sneaks
    through into a TEXT column and produces a confusing type error three
    layers away from where it was actually accepted.
    """
    if not isinstance(raw, dict):
        raise InvalidEventError("event must be an object")

    missing = [key for key in _REQUIRED_EVENT_KEYS if key not in raw]
    if missing:
        raise InvalidEventError(f"missing required field(s): {', '.join(missing)}")

    event_id = raw["id"]
    entity_type = raw["entity_type"]
    entity_id = raw["entity_id"]
    event_type = raw["event_type"]
    payload = raw["payload"]
    created_at = raw["created_at"]

    if not isinstance(event_id, str) or not event_id:
        raise InvalidEventError("id must be a non-empty string")
    if not isinstance(entity_type, str) or not (1 <= len(entity_type) <= ENTITY_TYPE_MAX_LEN):
        raise InvalidEventError(
            f"entity_type must be a string of length 1..{ENTITY_TYPE_MAX_LEN}"
        )
    if not isinstance(entity_id, str) or not entity_id:
        raise InvalidEventError("entity_id must be a non-empty string")
    if event_type not in _ALLOWED_EVENT_TYPES:
        raise InvalidEventError(f"event_type must be one of {_ALLOWED_EVENT_TYPES}")
    if not isinstance(payload, dict):
        raise InvalidEventError("payload must be an object")
    if not isinstance(created_at, str) or not created_at:
        raise InvalidEventError("created_at must be a non-empty string")

    return {
        "id": event_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "event_type": event_type,
        "payload": payload,
        "created_at": created_at,
    }


def append_events(conn: sqlite3.Connection, events: Iterable[dict], *, origin_device_id: str) -> int:
    """Validates and stores each event in `events`, deduplicated on the
    UNIQUE `id` column, and returns the number ACTUALLY inserted (never
    `len(events)`). `payload` is JSON-encoded for storage; `created_at` is
    stored VERBATIM exactly as the origin device supplied it and is NEVER
    rewritten to this hub's own local time (this hub is not the authority
    on when an event happened -- the originating device is).

    Each event is passed through `validate_event` first (raising
    `InvalidEventError` immediately on the first malformed one, aborting
    the whole call) -- this DAO never writes an unvalidated event to the
    log. This is a NARROWER contract than Owner's `_store_events`, which
    quarantines a single bad event into `owner_sync_quarantine` and
    continues the rest of the batch (see that function's own docstring for
    the outage it exists to prevent: one poison row wedging a shop's sync
    forever). This module does not replicate quarantine, deliberately: v30
    claims no `site_sync_quarantine` table (see ROADMAP.md's v30 entry --
    only the six tables listed there), and inventing one was out of scope
    for this task. A caller that wants Owner's per-event-skip behaviour
    must validate and filter events itself before calling this function;
    left as a named gap for whoever builds the push route handler, not
    silently absorbed here.

    DEDUP MECHANISM -- read this before "simplifying" it into `executemany`:
    each event is inserted with its OWN single-row `INSERT OR IGNORE`
    statement, and `cursor.rowcount` (which for a single-row INSERT is
    reliably 0 if the row already existed / was ignored, or 1 if it was
    actually inserted) is accumulated across the loop. This is NOT done
    with `executemany`, and that is deliberate, not an oversight:
    `executemany`'s `rowcount` with `INSERT OR IGNORE` is not a reliable
    per-statement count of actual insertions across sqlite3/SQLite
    versions -- it can reflect the number of rows ATTEMPTED rather than the
    number that actually landed, which would silently overcount every
    retried push (the exact case dedup exists to handle correctly) and
    make `append_events`'s return value lie about what actually happened.
    `conn.total_changes` (a connection-wide cumulative counter) was also
    considered and rejected for the same class of reason: it is
    connection-global, so it would be wrong the moment this connection is
    shared with ANY other concurrent write on the same request (which the
    connection contract in this module's docstring does not rule out). One
    execute() per row, with that row's own `cursor.rowcount`, is the only
    approach that is correct regardless of batch size or what else touches
    the connection.

    This IS the load-bearing dedup guarantee for the whole site relay: a
    retried push after a dropped response, an event that reached this log
    by two different paths (e.g. once directly from a device, once
    forwarded back down from the cloud after this hub itself pushed it
    up), and a crash mid-batch on a PREVIOUS attempt must all collapse to
    a clean no-op on retry -- never a duplicate row, never a raised error
    for "already exists". `INSERT OR IGNORE` keyed on `site_sync_events`'s
    `UNIQUE(id)` constraint is what makes that true; see the mutation-proof
    note below for what breaks if this becomes `INSERT OR REPLACE` instead.

    MUTATION-PROOF WARNING (do not "fix" this by inspection): `INSERT OR
    REPLACE` also avoids raising on a duplicate `id`, and looks
    superficially equivalent -- it is not. `OR REPLACE` DELETES the
    existing row and inserts a new one in its place, which reassigns a
    NEW, higher `seq` (AUTOINCREMENT) to an event that was already
    delivered to some paired devices under its OLD `seq`. Any device that
    had already pulled past the old `seq` would never see the row again at
    its new one, and any device that had NOT yet pulled it would receive
    it a second time as if it were new. `OR IGNORE` leaves the existing
    row -- and its `seq` -- completely untouched, which is the only
    behaviour a monotone, once-assigned seq space can tolerate.
    """
    inserted = 0
    for raw in events:
        event = validate_event(raw)
        cur = conn.execute(
            "INSERT OR IGNORE INTO site_sync_events "
            "(id, entity_type, entity_id, event_type, payload, created_at, origin_device_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event["id"], event["entity_type"], event["entity_id"], event["event_type"],
                json.dumps(event["payload"]), event["created_at"], origin_device_id,
            ),
        )
        if cur.rowcount:
            inserted += 1
    return inserted


def read_events_since(conn: sqlite3.Connection, *, since: int, exclude_device_id: str,
                       limit: int = PULL_LIMIT) -> list:
    """Returns events with `seq > since`, EXCLUDING `exclude_device_id`'s
    own rows, oldest-first, capped at `limit`. Mirrors
    `owner/app/sync/routes.py::pull`'s own query shape (`.where(SyncEvent.seq
    > since).where(SyncEvent.device_id != installation.id).order_by(
    SyncEvent.seq).limit(500)`) minus the `license_id` scoping this module
    deliberately never has (see module docstring).

    `origin_device_id != ?` IS THE SELF-EXCLUSION GUARANTEE: without it, a
    device would receive its own just-pushed events back on its very next
    pull, apply them again against rows it already holds, and -- depending
    on what `apply_pull_result`'s idempotent-upsert semantics do with a
    device re-applying its OWN write -- at best waste a round trip, at
    worst manufacture a spurious `sync_conflicts` entry comparing a row
    against itself. Owner's cloud relay enforces the identical exclusion
    for the identical reason; see the mutation-proof note in this
    package's test file for what breaks when this clause is dropped.

    Returns dicts shaped exactly like the cloud relay's own pull response
    items (`id, entity_type, entity_id, event_type, payload, created_at,
    seq`) -- `payload` is decoded back from its stored JSON text into a
    dict here, the mirror image of `append_events`'s `json.dumps` on the
    way in.

    Assumes `conn.row_factory is sqlite3.Row` (this module's stated
    convention, see module docstring) so `r["col"]` below is valid; a
    connection with the default tuple row factory would raise `TypeError`
    on the first indexed access."""
    rows = conn.execute(
        "SELECT seq, id, entity_type, entity_id, event_type, payload, created_at "
        "FROM site_sync_events "
        "WHERE seq > ? AND origin_device_id != ? "
        "ORDER BY seq "
        "LIMIT ?",
        (since, exclude_device_id, limit),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "event_type": row["event_type"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
            "seq": row["seq"],
        }
        for row in rows
    ]


def max_event_seq(conn: sqlite3.Connection) -> int:
    """The current highest `seq` in `site_sync_events`, or 0 if the log is
    empty. Used by `resolve_pull_cursor`'s zero-rows clamp below -- see that
    function's own docstring for why an unbounded `since` must never be
    trusted past this value."""
    row = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM site_sync_events").fetchone()
    return row[0]


def resolve_pull_cursor(conn: sqlite3.Connection, *, since: int, rows: list) -> int:
    """Given the rows a pull just returned (possibly empty) and the
    client-supplied `since` it was asked to pull from, returns the cursor
    value the caller should both RETURN to the client and PERSIST as that
    device's acked position.

    Ports `owner/app/sync/routes.py::pull`'s clamping logic verbatim in
    BEHAVIOUR (see that function's own long comment, sec"lines 600-660" of
    this task's plan) -- reproduced here in full because getting this wrong
    is the kind of defect that only shows up as a device going silently,
    permanently blind, long after the code that caused it shipped:

    If `rows` is non-empty, the cursor is simply the last row's `seq` -- a
    real value from a row that genuinely exists, nothing to bound.

    If `rows` is EMPTY, `since` would otherwise become the returned/
    persisted cursor VERBATIM -- and `since` is untrusted, client-supplied
    input with no upper bound (a client cannot know this hub's true max
    seq in advance, so validating it against a fixed ceiling would be
    wrong for every caller). Persisting an absurd `since` unmodified
    creates a device on record as having acknowledged events that DO NOT
    EXIST YET -- not hypothetical: a restored-from-backup client resending
    a stale cursor, a version-skewed client, or a plain integer bug in a
    future caller could all produce it, and nothing about a validly-formed
    pull request can be rejected by authentication for carrying one.

    The damage lands on events written AFTERWARDS: `site_device_cursors`
    is this hub's local analogue of Owner's `SyncDeviceCursor`, and any
    future site-log pruning built on top of it (mirroring
    `owner/app/pruning.py`'s MIN-over-active-devices watermark) would treat
    a bogus high cursor as "this device has already seen everything up to
    here" -- pre-acknowledging the entire FUTURE of the stream, so a new
    event becomes prunable the instant it is written, deleted before the
    over-claiming device ever legitimately reaches it. And because a
    cursor-advance operation correctly never regresses an already-higher
    value (see `advance_device_cursor`'s own docstring), a bogus high-water
    mark can never be walked back down through the ordinary protocol --
    unrecoverable without manual intervention.

    So on the zero-rows path, the cursor is clamped to `min(since,
    max_event_seq(conn))`: server truth a device cannot legitimately claim
    to have exceeded, since it cannot have acknowledged an event that was
    never written. `max_event_seq` COALESCEs to 0 for an empty log, so a
    fresh hub with no events at all clamps any `since` down to 0.

    The clamped value is what is BOTH returned to the client AND what the
    caller should pass to `advance_device_cursor` -- returning the raw,
    over-large `since` back to the client would leave it permanently
    blind (it would keep asking from a point the stream will never reach),
    while handing back the true high-water mark lets a misbehaving client
    self-heal on its very next pull. A well-behaved client is completely
    unaffected: for it, `since` is already <= the true max, so this clamp
    changes nothing.
    """
    if rows:
        return rows[-1]["seq"]
    return min(since, max_event_seq(conn))


def advance_device_cursor(conn: sqlite3.Connection, installation_id: str, seq: int) -> None:
    """Upserts `site_device_cursors` for `installation_id`, never letting an
    already-higher persisted `last_seq` regress -- the SQLite analogue of
    Owner's `_advance_device_cursor` GREATEST-style guard, done in one
    upsert statement via `MAX(last_seq, excluded.last_seq)` rather than
    Owner's read-then-compare-in-Python approach (simpler here because
    there is no separate ORM object identity to preserve; the atomic upsert
    is strictly stronger against concurrent callers than a
    read-compare-write ever was, though SQLite's own single-writer
    serialization would have made that difference moot in practice either
    way).

    This guard matters for exactly the reason Owner's docstring states:
    nonce/timestamp replay protection stops a CAPTURED request from being
    replayed, but does not stop a genuinely fresh, validly-signed pull
    request that happens to carry a LOWER `seq` than one already recorded
    (a client bug, a restored-from-backup client resending an old cursor,
    two requests reordered in flight) from landing after a higher one
    already did. Silently regressing the stored watermark in that case
    would let a later pruning pass believe events between the lower and
    higher values are still needed forever -- never letting the persisted
    value move backward closes that off entirely; the caller can always
    still catch up by pulling forward again on its next attempt."""
    conn.execute(
        "INSERT INTO site_device_cursors (installation_id, last_seq, updated_at) "
        "VALUES (?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(installation_id) DO UPDATE SET "
        "last_seq = MAX(site_device_cursors.last_seq, excluded.last_seq), "
        "updated_at = CURRENT_TIMESTAMP",
        (installation_id, seq),
    )


def read_device_cursor(conn: sqlite3.Connection, installation_id: str) -> int:
    """Returns `installation_id`'s currently persisted `last_seq`, or 0 if
    this device has never been recorded (mirrors `sync_service.py::
    read_cursor`'s own "no row yet means 0" default for `sync_cursor`)."""
    row = conn.execute(
        "SELECT last_seq FROM site_device_cursors WHERE installation_id = ?",
        (installation_id,),
    ).fetchone()
    return row["last_seq"] if row is not None else 0


def lookup_paired_device(conn: sqlite3.Connection, installation_id: str) -> Optional[dict]:
    """Returns the paired-device row for `installation_id`, or `None` if no
    such device has ever been paired to this hub.

    DELIBERATELY returns `revoked_at` rather than filtering revoked devices
    out of the result -- a caller (the future authentication path this DAO
    supports) needs to distinguish THREE outcomes, not two: "never paired
    at all" (this function returns `None`), "paired and currently
    authorized" (`revoked_at` is `None`), and "paired but locally revoked"
    (`revoked_at` is set). Each of the first and third cases should return
    a DIFFERENT reason code to a rejected caller (an unknown device versus
    a device the shop owner explicitly kicked off), and a caller cannot
    recover that distinction from a bare `None` if this function collapsed
    both non-authorized cases to the same "not found" result. Silently
    filtering revoked rows out here would erase exactly the information
    the local-revoke feature exists to provide (per
    `lan-restaurant-design.md` sec5: "the hub's own Settings can revoke a
    paired device immediately... because the realistic urgent case -- a
    fired employee's tablet -- is standing in the restaurant")."""
    row = conn.execute(
        "SELECT installation_id, device_public_key, label, paired_at, revoked_at "
        "FROM site_paired_devices WHERE installation_id = ?",
        (installation_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "installation_id": row["installation_id"],
        "device_public_key": row["device_public_key"],
        "label": row["label"],
        "paired_at": row["paired_at"],
        "revoked_at": row["revoked_at"],
    }


def pair_device(conn: sqlite3.Connection, installation_id: str, *, device_public_key: str,
                label: Optional[str] = None) -> None:
    """Upserts `installation_id` into `site_paired_devices` with the given
    public key/label. Re-pairing an installation_id that was previously
    revoked CLEARS `revoked_at` -- pairing is the explicit, owner-initiated
    "trust this device" action (the QR-scan flow in
    `lan-restaurant-design.md` sec4), so an operator who deliberately
    re-pairs a device that had been revoked is deliberately re-authorizing
    it; leaving the old `revoked_at` in place after a fresh, explicit
    re-pair would make the pairing screen lie about what it just did."""
    conn.execute(
        "INSERT INTO site_paired_devices "
        "(installation_id, device_public_key, label, paired_at, revoked_at) "
        "VALUES (?, ?, ?, CURRENT_TIMESTAMP, NULL) "
        "ON CONFLICT(installation_id) DO UPDATE SET "
        "device_public_key = excluded.device_public_key, "
        "label = excluded.label, "
        "revoked_at = NULL",
        (installation_id, device_public_key, label),
    )


def revoke_device(conn: sqlite3.Connection, installation_id: str) -> None:
    """Stamps `revoked_at` on `installation_id`'s paired-device row (a
    no-op if the id was never paired at all -- `UPDATE ... WHERE` simply
    matches zero rows rather than raising). This is the LOCAL, immediate
    revoke `lan-restaurant-design.md` sec5 describes as the remedy for
    suspension latency: the Owner-signed roster only refreshes when this
    hub next reaches the internet, but a shop owner can cut a device off
    from the LAN relay right now, from this hub's own Settings, with no
    connectivity required at all."""
    conn.execute(
        "UPDATE site_paired_devices SET revoked_at = CURRENT_TIMESTAMP WHERE installation_id = ?",
        (installation_id,),
    )
