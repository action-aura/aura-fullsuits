"""Replay protection for the LAN site relay: timestamp freshness + nonce burn.

Port of the timestamp-freshness and nonce-burn half of
`owner/app/licensing_service/replay.py` onto SQLite. Owner's version backs
its nonce store with a dedicated Postgres table
(`owner_security_nonce_records`, ADR-6.3: "PostgreSQL, not Redis"); this
module's nonce store is `site_sync_nonces` (retail schema v30,
`products/retail/backend/database/schema.py`'s `_migrate_add_site_relay`),
one hub-local table instead of a shared server-side one. The verification
LOGIC -- validate shape, validate timestamp freshness, THEN atomically burn
the nonce -- is unchanged; only the storage backend and the surrounding
transaction model (raw sqlite3, no ORM, no session) differ. See
`docs/launch-readiness/lan-restaurant-design.md` sec3/sec5 for why the site
relay exists at all and what it authenticates.

Reason codes are copied VERBATIM from Owner's `replay.py`, not re-invented,
so a future shared client-side error-message table (or a human reading logs
from both relays) never has to learn two different vocabularies for the
same two failures:

    * "INVALID_TIMESTAMP"              -- the timestamp has no tzinfo at all
      (Owner's `validate_timestamp`, the `request_timestamp.tzinfo is None`
      branch). A naive datetime cannot be safely compared against `now`
      (UTC) without silently assuming a timezone, so it is rejected outright
      rather than guessed at.
    * "TIMESTAMP_OUTSIDE_ALLOWED_WINDOW" -- the timestamp IS tz-aware but
      falls outside the accepted skew window either direction (too far in
      the past OR the future -- Owner's `abs(delta) > skew_seconds` check,
      reproduced identically below).
    * "NONCE_REUSED"                    -- the (scope, nonce) pair was
      already burned; Owner's `consume_nonce` raises this from the
      IntegrityError translation on its own UNIQUE(nonce, scope) constraint,
      and this module does the identical translation against
      `site_sync_nonces`'s composite PRIMARY KEY (scope, nonce), which is
      SQLite's equivalent of that same UNIQUE constraint.

THE ONE SECURITY INVARIANT THIS MODULE MUST NEVER VIOLATE:

    DEFAULT_NONCE_TTL_SECONDS must stay STRICTLY GREATER THAN twice
    DEFAULT_SKEW_SECONDS.

A nonce is forgotten (eligible for pruning) once `ttl_seconds` has elapsed
since it was seen. A request is still considered timestamp-fresh for up to
`skew_seconds` on EITHER side of "now" -- meaning a request timestamped up
to `skew_seconds` in the past is still accepted at the moment it arrives.
If the nonce TTL is not comfortably larger than twice the skew, there is a
window in which:

    1. A device sends a request timestamped `now - skew_seconds` (the
       oldest timestamp the freshness check will still accept) and its
       nonce is burned at time T.
    2. That nonce is pruned at T + ttl_seconds (it has been forgotten).
    3. An attacker who captured the original request replays it at any
       point up to T + skew_seconds + skew_seconds from the ORIGINAL
       timestamp (because the timestamp freshness check only cares about
       the gap between the timestamp and "now" at REPLAY time, and the
       replayed request's timestamp is unchanged -- so the replay is
       accepted as fresh right up until `original_timestamp + skew_seconds`
       measured from the replay's own "now").
    4. If ttl_seconds <= 2 * skew_seconds, step 2 (nonce forgotten) can
       happen BEFORE step 3's freshness window closes -- i.e. there exists
       a real moment at which the request is still timestamp-fresh AND its
       nonce has already been pruned from the store. A captured request
       replayed in that gap is accepted: fresh timestamp (nonce store never
       consulted for freshness), and the nonce store has no memory of it to
       reject it as reused. Replay protection is silently gone, with no
       error anywhere -- the request just succeeds a second time.

Requiring TTL > 2 * skew closes this precisely: the earliest a nonce can be
pruned (ttl_seconds after it was seen) is always strictly later than the
latest moment a replay of that SAME timestamp could still be accepted as
fresh (skew_seconds past the ORIGINAL timestamp, and the original timestamp
itself could have been up to skew_seconds old when it was first accepted --
hence the factor of 2, not 1). The module-level assertion at the bottom of
this file enforces the relationship at IMPORT time, so a future edit that
weakens either constant independently fails loudly and immediately, rather
than silently reopening this gap for someone to find in production months
later.

WHY THE SKEW IS GENEROUS HERE (5 minutes) AND NOT IN THE CLOUD RELAY
(seconds, per `ACTIVATION_TIMESTAMP_SKEW_SECONDS` in Owner's config): this
is a real LAN-offline failure mode, not a convenience. Per
`lan-restaurant-design.md` sec4, "Clock skew -- a real LAN-offline failure
mode": a waiter's tablet that has been offline for weeks has no NTP source
and drifts -- Android/Windows clocks both wander measurably over that
timescale with no internet time sync. A tight skew window tuned for an
always-online cloud client would start rejecting that tablet's requests as
INVALID_TIMESTAMP by month 2, in exactly the offline-first environment this
feature exists to serve. Widening the skew costs the design NOTHING on the
authenticity axis -- the nonce store (this module's whole other half) is
what actually kills replay; timestamp freshness only bounds how long a
captured request stays exploitable at all. A 5-minute window is generous
enough to absorb realistic uncorrected drift while still bounding a captured
request's shelf life to single-digit minutes, not indefinitely.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional


class ReplayError(Exception):
    """Raised by `validate_timestamp` and `consume_nonce`. Carries a
    `reason_code` string matching Owner's `replay.ReplayError` vocabulary
    (see module docstring) so a caller can return it to the client verbatim,
    exactly like `owner/app/sync/routes.py::_authenticate` does with
    `str(exc)` from its own `replay.ReplayError` catches."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


# ── The one invariant (see module docstring) ────────────────────────────────
#
# 5 minutes. Deliberately generous versus the cloud relay's own skew
# setting -- see the module docstring's "WHY THE SKEW IS GENEROUS HERE"
# section for the full reasoning (an offline-for-weeks tablet with no NTP,
# not a convenience).
DEFAULT_SKEW_SECONDS = 300

# 30 minutes. MUST stay > 2 * DEFAULT_SKEW_SECONDS -- see the module
# docstring's "THE ONE SECURITY INVARIANT" section. Do not shrink this (or
# grow DEFAULT_SKEW_SECONDS) without re-reading that section first; the
# assertion below exists so a violation is impossible to ship silently.
DEFAULT_NONCE_TTL_SECONDS = 1800

if DEFAULT_NONCE_TTL_SECONDS <= 2 * DEFAULT_SKEW_SECONDS:
    # A future edit that shrinks the TTL or grows the skew independently
    # would reopen the exact replay gap the module docstring's "THE ONE
    # SECURITY INVARIANT" section derives -- raising here, at import time,
    # means that edit fails the moment this module is imported (loudly, in
    # every test and every process boot) instead of shipping a silent
    # regression that only shows up as an unexplained successful replay in
    # the field, potentially months later.
    raise RuntimeError(
        "commercial_runtime.sync.site_relay.replay: DEFAULT_NONCE_TTL_SECONDS "
        f"({DEFAULT_NONCE_TTL_SECONDS}) must be strictly greater than twice "
        f"DEFAULT_SKEW_SECONDS ({DEFAULT_SKEW_SECONDS} * 2 = "
        f"{2 * DEFAULT_SKEW_SECONDS}). If the TTL does not comfortably "
        "outlive twice the accepted clock skew, a nonce can be pruned "
        "while a captured request carrying its timestamp is still inside "
        "its own freshness window -- silently reopening replay protection "
        "with no error anywhere. See this module's docstring, 'THE ONE "
        "SECURITY INVARIANT THIS MODULE MUST NEVER VIOLATE', for the full "
        "derivation before changing either constant."
    )


def parse_request_timestamp(raw) -> datetime:
    """Parses a client-submitted ISO-8601 timestamp. Identical to Owner's
    `replay.parse_request_timestamp`, including the same Python-3.10-vs-'Z'
    fix: `datetime.fromisoformat()` only accepts the 'Z' UTC designator from
    3.11 onward, but 'Z' is valid ISO-8601 and is what every non-Python
    client in this codebase's sync protocol emits (Android's Kotlin
    `Instant.toString()`). Normalizing 'Z' -> '+00:00' first makes this
    accept both forms rather than only whichever one the desktop client's
    own `datetime.isoformat()` happens to produce."""
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def validate_timestamp(request_timestamp: datetime, skew_seconds: int, *, now: Optional[datetime] = None) -> None:
    """Raises ReplayError if `request_timestamp` is not tz-aware, or falls
    outside `skew_seconds` of `now` in EITHER direction. Identical logic and
    identical reason codes to Owner's `replay.validate_timestamp` -- see the
    module docstring's reason-code list for why each one is worth keeping
    byte-for-byte the same as Owner's.

    `now` is an injectable seam (defaults to real UTC now) purely for
    deterministic testing -- production callers never pass it, exactly like
    every other `now=None`-defaulted parameter in this codebase's sync
    modules (e.g. `sync_service.py`'s freshness helpers)."""
    now = now or datetime.now(timezone.utc)
    if request_timestamp.tzinfo is None:
        # A naive datetime has no defined offset from UTC, so comparing it
        # against `now` (always tz-aware UTC here) would silently assume
        # one. Reject outright rather than guess -- exactly Owner's own
        # posture.
        raise ReplayError("INVALID_TIMESTAMP")
    delta = (now - request_timestamp).total_seconds()
    if abs(delta) > skew_seconds:
        raise ReplayError("TIMESTAMP_OUTSIDE_ALLOWED_WINDOW")


def consume_nonce(conn: sqlite3.Connection, nonce: str, *, scope: str, ttl_seconds: int,
                   now: Optional[datetime] = None) -> None:
    """Atomically claims `nonce` within `scope` against `site_sync_nonces`,
    or raises ReplayError("NONCE_REUSED") if it was already claimed.

    ATOMICITY: this is a bare INSERT into a table whose PRIMARY KEY is the
    composite (scope, nonce) -- there is no SELECT-then-INSERT here, and
    there must never be one. A SELECT-then-INSERT has a race window between
    the read and the write in which two concurrent requests carrying the
    SAME (scope, nonce) can both observe "not present yet" and both proceed
    to insert -- which is precisely the replay this whole module exists to
    prevent, reintroduced by the "obvious" implementation. The INSERT's own
    PRIMARY KEY constraint is what SQLite enforces atomically for us: a
    second concurrent INSERT for the identical (scope, nonce) fails with
    `sqlite3.IntegrityError` regardless of timing, because uniqueness is
    checked by the same engine that performs the write, not by a separate
    prior read this code half-trusts. This is the direct SQLite analogue of
    Owner's own `consume_nonce`, whose docstring makes the identical claim
    about its UNIQUE(nonce, scope) constraint in Postgres.

    PRUNE BEFORE INSERT, not after: expired rows for OTHER nonces are
    removed first so this call's own INSERT attempt is judged against only
    the nonces that are still genuinely live, and so the nonce store does
    not grow without bound across a hub's uptime (nothing else in this
    module's call graph prunes on a timer -- see `prune_nonces`'s own
    docstring for why THIS call site is the natural place to piggyback it,
    since every push/pull request calls consume_nonce exactly once).

    COMMITS ON ITS OWN -- the ONE function in this whole DAO (see
    `store.py`'s module docstring for the general "caller owns the
    transaction" rule) that does not leave committing to the caller.
    Mirrors Owner's `consume_nonce`, whose own docstring gives the reason
    directly: the nonce must be burned even if the REST of the request then
    fails for some unrelated reason (a bad signature discovered a step
    later, a database error in the handler that follows), because if the
    burn were rolled back along with everything else, the exact same
    request -- signature, timestamp, nonce and all -- becomes replayable
    again the instant the failed attempt unwinds. Burning must be permanent
    the moment it happens, independent of whatever the caller does next.

    Rolling back the PRUNE alongside a failed INSERT (the `except` branch
    below) is fine and deliberate, not an inconsistency: the pruned rows
    were already past their TTL and eligible for removal by construction,
    so losing that particular prune attempt on a NONCE_REUSED outcome only
    means those same expired rows get pruned again on some FUTURE call
    instead of this one -- a harmless deferral, never a correctness issue
    (a row that is not yet expired is never touched by prune_nonces at
    all, regardless of whether this transaction commits or rolls back)."""
    now = now or datetime.now(timezone.utc)
    prune_nonces(conn, ttl_seconds, now=now)
    try:
        conn.execute(
            "INSERT INTO site_sync_nonces (scope, nonce, seen_at) VALUES (?, ?, ?)",
            (scope, nonce, now.isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise ReplayError("NONCE_REUSED")


def prune_nonces(conn: sqlite3.Connection, ttl_seconds: int, *, now: Optional[datetime] = None) -> int:
    """Deletes every `site_sync_nonces` row older than `ttl_seconds`,
    returning the number of rows removed. Local port of Owner's
    `replay.purge_expired_nonces`, minus the ORM session plumbing.

    Deliberately does NOT commit (unlike `consume_nonce` above) -- this
    module's DAO-wide convention (see `store.py`'s module docstring) is
    that the CALLER owns the transaction, and `consume_nonce` is the one
    documented exception, not this function. `consume_nonce` calls this
    as a helper INSIDE its own transaction and commits (or rolls back)
    both together; a caller that invokes this directly (a scheduled
    housekeeping sweep, or a test) commits it explicitly, exactly like
    every other function in this package."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(seconds=ttl_seconds)
    cur = conn.execute("DELETE FROM site_sync_nonces WHERE seen_at < ?", (cutoff.isoformat(),))
    return cur.rowcount
