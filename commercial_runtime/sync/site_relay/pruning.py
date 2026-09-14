"""Deleting site-log rows that every consumer has already taken.

The local peer of `owner/app/sync/pruning.py` -- read that file first; the
question is the same one ("what is safe to forget?") and getting it wrong has
the same two failure modes, one loud and one silent:

  * prune too EAGERLY and a device that had not yet caught up silently skips
    events forever. Its cursor is already past the deleted range, so it never
    asks for them again and nothing anywhere reports a gap. That is data loss
    with no error, which is the worst shape a bug can take in a money path.
  * prune too TIMIDLY and `site_sync_events` grows without bound on a till
    that runs for years. Slow, visible, recoverable -- annoying, not fatal.

Those costs are wildly asymmetric, so every judgement call in this module
resolves toward pruning LESS. Each one is commented where it is made.

WHO READS THE SITE LOG, because the safe watermark is exactly "the slowest of
them" and the list is not obvious:

  1. Every paired LAN device, via its ordinary site-relay pull. How far each
     has got is `site_device_cursors.last_seq`.
  2. The hub's own forwarder, which pushes locally-originated rows up to
     Owner's cloud relay. How far IT has got is
     `site_forward_cursor.forwarded_to_seq` (see `forwarder.py`).

Miss either and you delete rows the other still needed.

THE TRAP THIS MODULE EXISTS TO AVOID, named in the design doc itself
(`docs/launch-readiness/lan-restaurant-design.md` §6, item 2, about the CLOUD
side): a naive `MIN(last_seq)` over every cursor row freezes pruning forever
the moment one device stops syncing. A tablet that was dropped, sold, or
revoked keeps a stale cursor at seq 40 while the shop reaches seq 400,000, and
the MIN never advances again. So a REVOKED device is excluded here -- it will
never pull again by definition, and leaving it in the MIN would mean a shop
that fired one employee could never prune again.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

# Kept deliberately modest. Pruning is housekeeping that runs beside real till
# traffic on the same single-writer SQLite connection, so it takes the write
# lock; a huge DELETE would hold it long enough to be felt at the counter.
# Better to reclaim a bounded amount often than everything at once.
DEFAULT_PRUNE_BATCH = 5000


def _active_paired_installation_ids(conn: sqlite3.Connection) -> list:
    """Paired devices that are still entitled to pull.

    `revoked_at IS NULL` is the filter that keeps a dead device from freezing
    pruning forever -- see the module docstring. A revoked device is not
    coming back for those rows; if it is ever un-revoked (re-paired with a
    fresh operator-issued code) it re-pulls from its own cursor, and anything
    already pruned is equally gone for it on the cloud path too, so it is no
    worse off than a device that was simply offline for a long time."""
    rows = conn.execute(
        "SELECT installation_id FROM site_paired_devices WHERE revoked_at IS NULL"
    ).fetchall()
    return [r["installation_id"] if isinstance(r, sqlite3.Row) else r[0] for r in rows]


def safe_prune_watermark(conn: sqlite3.Connection) -> int:
    """The highest `seq` that every consumer has already taken.

    Returns 0 when nothing can safely be pruned, which is the correct answer
    far more often than it looks and is never an error.

    THE RULE, in the order the cases actually matter:

      * A paired, non-revoked device with NO cursor row has pulled nothing
        yet, so its effective position is 0 and NOTHING may be pruned. This is
        the case that would otherwise eat a tablet's entire history between it
        being paired and it first syncing -- the exact window in which a new
        device is most likely to be sitting on a shelf for a day before
        someone switches it on.
      * With paired devices present, the watermark is the MINIMUM of their
        cursors: the slowest device governs.
      * With NO active paired devices at all, no LAN consumer exists, so the
        forwarder alone governs. This is the ordinary single-till shop, and
        without this branch its log would grow forever despite having no
        peers to serve.
      * In every case the result is also capped by the forwarder's
        `forwarded_to_seq`. A row that has not yet reached Owner's cloud
        relay must never be deleted, whatever the LAN devices have done with
        it -- deleting it would lose it from the shop's history permanently,
        because the site log is the only place it exists until the forwarder
        succeeds.
    """
    forwarded_row = conn.execute(
        "SELECT forwarded_to_seq FROM site_forward_cursor WHERE id = 1"
    ).fetchone()
    # A missing row means the migration's seed is absent -- treat it as "the
    # forwarder has sent nothing", the conservative reading. Mirrors
    # forwarder.read_forward_cursor's own defensive fallback rather than
    # assuming a row that should exist does.
    forwarded_to = int(forwarded_row[0]) if forwarded_row is not None else 0

    installation_ids = _active_paired_installation_ids(conn)
    if not installation_ids:
        return max(0, forwarded_to)

    placeholders = ",".join("?" for _ in installation_ids)
    cursor_rows = conn.execute(
        f"SELECT installation_id, last_seq FROM site_device_cursors "
        f"WHERE installation_id IN ({placeholders})",
        installation_ids,
    ).fetchall()
    by_id = {
        (r["installation_id"] if isinstance(r, sqlite3.Row) else r[0]):
        int(r["last_seq"] if isinstance(r, sqlite3.Row) else r[1])
        for r in cursor_rows
    }

    slowest = None
    for installation_id in installation_ids:
        # .get(..., 0) is load-bearing, not defensive padding: a paired device
        # with no cursor row is at 0, and that must pin the whole watermark to
        # 0. Skipping such devices instead would prune away precisely the
        # history a brand-new device has not collected yet.
        position = by_id.get(installation_id, 0)
        slowest = position if slowest is None else min(slowest, position)

    return max(0, min(slowest, forwarded_to))


def prune_site_log(conn: sqlite3.Connection, *, batch=DEFAULT_PRUNE_BATCH,
                   watermark: Optional[int] = None) -> int:
    """Delete site-log rows at or below the safe watermark. Returns the count.

    Does NOT commit -- the caller owns the transaction, matching `store.py`'s
    convention throughout this package.

    `watermark` is injectable purely so a test can drive the deletion half
    without having to manufacture the cursor state that produces a given
    watermark; production always lets it compute.

    Pruning cannot renumber anything: `site_sync_events.seq` is
    `INTEGER PRIMARY KEY AUTOINCREMENT`, so SQLite will never reissue a
    deleted row's number to a later insert. That property is what makes
    pruning safe at all -- with a bare rowid alias, a device holding a cursor
    past a reused number would silently skip whatever event took that slot,
    which is exactly the failure `retail_site_relay_schema_test.py` pins.
    """
    if watermark is None:
        watermark = safe_prune_watermark(conn)
    if watermark <= 0:
        return 0

    cursor = conn.execute(
        "DELETE FROM site_sync_events WHERE seq IN ("
        "  SELECT seq FROM site_sync_events WHERE seq <= ? ORDER BY seq LIMIT ?"
        ")",
        (watermark, batch),
    )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def prune_nonces_and_log(conn: sqlite3.Connection, *, nonce_ttl_seconds,
                         batch=DEFAULT_PRUNE_BATCH, now=None) -> dict:
    """One housekeeping sweep: expired replay nonces plus the settled log.

    Both stores grow with traffic and neither has a natural ceiling, so they
    are swept together by whatever periodic job the hub runs -- one lock
    acquisition rather than two.

    Returns what it actually deleted, so a caller can log real numbers instead
    of "housekeeping ran", which is the difference between noticing that
    pruning has been stuck at zero for a month and not noticing."""
    from . import replay

    nonces = replay.prune_nonces(conn, nonce_ttl_seconds, now=now)
    events = prune_site_log(conn, batch=batch)
    return {"nonces_pruned": nonces, "events_pruned": events}
