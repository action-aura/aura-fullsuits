"""Multi-device sync relay: push/pull routes for the append-only
owner_sync_events log (multi-device-sync-foundation plan, Task 2).

Authentication mirrors owner/app/licensing_service/checkin.py's
device-signature-verification pattern, including the parts an earlier pass
of this file wrongly treated as skippable:

  1. validate the required signed fields are present (installation_id,
     timestamp, nonce, signature),
  2. validate timestamp freshness (replay.validate_timestamp),
  3. atomically consume the nonce (replay.consume_nonce) -- SAME order
     checkin.py uses (nonce is burned before the installation is even
     looked up, exactly like checkin.py:54-65),
  4. look up Installation, load its currently-ACTIVE device public key,
     verify the Ed25519 signature over the canonicalized body (minus
     `signature`),
  5. reject a suspended/deactivated/replaced installation the same way
     checkin.py does,
  6. resolve license_id from the VERIFIED installation's own relationship
     -- never from client input.

Post-review correction: an earlier version of this module had NO
nonce/timestamp/replay protection, reasoning that push's idempotency and
pull's read-only scoping made a bare-signature replay low-value. That
reasoning was wrong: `since` was a free, unsigned query parameter, so a
single captured pull request (proxy log, error-tracker capture, misrouted
intermediary) could be replayed forever with `since=0` to walk a license's
entire event history, up to 500 rows per call, without the device's
private key ever being needed again. Fixed by requiring `nonce`/`timestamp`
on every push/pull request (validated exactly like checkin.py does) and by
folding `since` into the signed body itself (no longer a free query
param) so a captured pull request can't be replayed with a different
`since` either. Downstream client tasks (5: desktop relay client, 9: mobile
Ktor transport) must send `since` inside the signed JSON body, not as a
`?since=` query string.

Phase 5 prerequisites #2/#3 (docs/launch-readiness/phase5-prerequisites.md
sections 2-3): pull() now persists each device's own cursor
(app/models/sync.py::SyncDeviceCursor) so pruning.py has a durable,
server-side answer to "how far has every device caught up", and
_store_events() quarantines (rather than 400s the whole batch on) a
single malformed event so one poison row from one client can never wedge
a shop's sync permanently -- see _store_events's own docstring for the
full reasoning.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func, select, text
from sqlalchemy.exc import DataError, IntegrityError

from app.extensions import db_session
from app.licensing_service import device_identity, replay
from app.licensing_service.canonical import canonicalize_bytes
from app.models.installations import Installation
from app.models.sync import SyncDeviceCursor, SyncEvent, SyncQuarantineEvent

bp = Blueprint("sync", __name__, url_prefix="/api/sync/v1")

# Abuse-resistance bound per the plan's "reject unreasonably large batches"
# requirement (full rate limiting is an explicitly deferred, documented gap
# -- see the plan's "Residual gaps" section).
_MAX_PUSH_BATCH = 200
_ALLOWED_EVENT_TYPES = ("create", "update", "delete")
_ENTITY_TYPE_MAX_LEN = 64  # matches SyncEvent.entity_type's String(64) column

_REQUIRED_AUTH_FIELDS = ("installation_id", "timestamp", "nonce", "signature")


class SyncAuthError(Exception):
    """Raised by _authenticate; every route handler catches this and returns
    a 400 with the reason_code -- matching checkin.py's CheckInRejected,
    whose default http_status is 400 for every rejection reason (bad
    signature included), not 401/403. Confirmed against
    owner/tests/test_phase6_checkin_protocol.py, which asserts
    resp.status_code == 400 for both an unknown installation and a
    signature mismatch."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class InvalidEventError(Exception):
    """Raised for a malformed event in a push batch.

    Pre-Phase-5-prerequisite-#3 behavior (superseded, kept here as history
    because a test used to assert exactly this): caught once by the route
    handler, which rolled back the ENTIRE batch and returned a clean 400
    INVALID_EVENT -- correct only while the allowlist was narrow and one
    bad row was rare. Phase 5 widens the allowlist to eight entity types
    written by two clients on different release cadences
    (phase5-prerequisites.md section 3), which makes "one malformed row
    stops that shop syncing forever" a real, not theoretical, outage: every
    retry hits the identical batch, fails on the identical row, rolls back.

    Current behavior: caught PER-EVENT inside _store_events (see its own
    docstring), which quarantines the offending row and continues the
    batch -- this exception no longer propagates out of _store_events for
    the malformed-input case. It is still caught at the route-handler level
    below purely as defense-in-depth (belt-and-braces against a future code
    path that raises it directly), never as the primary control anymore."""


def _error(reason_code: str, http_status: int = 400):
    return jsonify({"reason_code": reason_code}), http_status


def _authenticate(body: dict, *, nonce_scope: str) -> tuple[Installation, uuid.UUID]:
    """Verify-then-resolve, matching checkin.py's exact order of
    operations: validate shape, validate timestamp freshness, consume the
    nonce, THEN look up the Installation / verify the signature / check
    status / resolve license_id. license_id NEVER comes from body input.

    nonce_scope distinguishes push's nonces from pull's (mirrors checkin.py
    using a distinct scope per operation type, e.g. "checkin" vs
    "deactivation") so a nonce from one operation can never be replayed
    against the other.
    """
    if not isinstance(body, dict):
        raise SyncAuthError("INVALID_REQUEST")
    for field in _REQUIRED_AUTH_FIELDS:
        if not body.get(field):
            raise SyncAuthError("INVALID_REQUEST")

    try:
        installation_id = uuid.UUID(str(body["installation_id"]))
    except (ValueError, TypeError):
        raise SyncAuthError("INSTALLATION_NOT_FOUND")

    try:
        request_timestamp = replay.parse_request_timestamp(body["timestamp"])
    except (ValueError, TypeError, AttributeError):
        raise SyncAuthError("INVALID_TIMESTAMP")
    try:
        replay.validate_timestamp(request_timestamp, current_app.config["ACTIVATION_TIMESTAMP_SKEW_SECONDS"])
    except replay.ReplayError as exc:
        raise SyncAuthError(str(exc))
    try:
        replay.consume_nonce(str(body["nonce"]), scope=nonce_scope, ttl_seconds=current_app.config["NONCE_TTL_SECONDS"])
    except replay.ReplayError as exc:
        raise SyncAuthError(str(exc))

    installation = db_session.get(Installation, installation_id)
    if installation is None:
        raise SyncAuthError("INSTALLATION_NOT_FOUND")

    device_key = device_identity.get_active_device_key(installation.id)
    if device_key is None:
        raise SyncAuthError("DEVICE_KEY_REVOKED")

    public_key = device_identity.load_public_key_or_none(device_key.public_key)
    signable = {k: v for k, v in body.items() if k != "signature"}
    canonical_bytes = canonicalize_bytes(signable)
    signature = body.get("signature")
    if not isinstance(signature, str) or public_key is None or not device_identity.verify_signature(
        public_key, canonical_bytes, signature
    ):
        raise SyncAuthError("INVALID_SIGNATURE")

    # checkin.py additionally gates on installation.status after signature
    # verification (INSTALLATION_SUSPENDED/DEACTIVATED/REPLACED) -- required
    # here too: staff-initiated suspension/deactivation via
    # installations/services.py::transition_installation does NOT revoke the
    # device key (only the device-initiated deactivation flow in
    # deactivation.py does), so relying on "active device key" alone would
    # let an admin-suspended device keep syncing.
    if installation.status in ("SUSPENDED", "DEACTIVATED", "REPLACED"):
        raise SyncAuthError(f"INSTALLATION_{installation.status}")

    if installation.license_id is None:
        # Mirrors checkin.py's own handling of installation.license is None.
        raise SyncAuthError("INSTALLATION_NOT_FOUND")

    return installation, installation.license_id


def _build_event(raw) -> SyncEvent:
    """Validates and constructs a (not-yet-added) SyncEvent from one item of
    the push batch. Raises InvalidEventError on any malformed field --
    including bounds that would otherwise surface as a raw DataError at the
    DB layer (entity_type's length must fit the column before we ever
    attempt to flush it)."""
    if not isinstance(raw, dict):
        raise InvalidEventError("event must be an object")
    try:
        event_id = uuid.UUID(str(raw["id"]))
        entity_type = raw["entity_type"]
        entity_id = uuid.UUID(str(raw["entity_id"]))
        event_type = raw["event_type"]
        payload = raw["payload"]
        client_created_at = replay.parse_request_timestamp(raw["created_at"])
    except (KeyError, ValueError, TypeError, AttributeError):
        raise InvalidEventError("malformed event")

    if not isinstance(entity_type, str) or not (1 <= len(entity_type) <= _ENTITY_TYPE_MAX_LEN):
        raise InvalidEventError("entity_type")
    if event_type not in _ALLOWED_EVENT_TYPES:
        raise InvalidEventError("event_type")
    if not isinstance(payload, dict):
        raise InvalidEventError("payload")

    return SyncEvent(
        id=event_id,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        payload=payload,
        client_created_at=client_created_at,
    )


def _sanitize_nul_bytes(value):
    """Postgres cannot store a literal NUL byte (\\x00) in ANY text-based
    column -- json, jsonb, text, varchar -- it is a structural limitation
    of Postgres's own internal C-string storage, not a choice this code
    makes (confirmed against a live instance: `psycopg.errors.
    UntranslatableCharacter`, SQLSTATE 22021, raised identically whether
    the NUL lands in a plain text column or inside JSONB). Recursively
    replaces every literal NUL with its visible escaped form so the
    surrounding text is otherwise untouched. Only ever called from
    _quarantine_event's fallback path -- see its own docstring for why."""
    if isinstance(value, str):
        return value.replace("\x00", "\\u0000")
    if isinstance(value, dict):
        return {k: _sanitize_nul_bytes(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_nul_bytes(v) for v in value]
    return value


def _quarantine_event(raw, *, license_id, device_id, batch_index: int, reason: str) -> None:
    """Writes ONE rejected event to owner_sync_quarantine with its RAW
    payload verbatim (never the partially-parsed SyncEvent -- _build_event
    may have failed before ever constructing one) and does NOT re-raise --
    the caller is expected to `continue` its loop, which is the whole point
    (see _store_events's docstring).

    MUTATION-FOUND BUG this function used to have, fixed here rather than
    merely noted: when the REJECTION REASON is itself "Postgres refuses
    this payload" (the DataError branch in _store_events -- concretely, an
    embedded NUL byte), storing that SAME payload verbatim into
    raw_payload (also JSONB) fails for the IDENTICAL reason. Uncaught, that
    second DataError propagated out of this function, out of
    _store_events' own except block (which was already busy handling the
    first one), past push()'s `except (InvalidEventError, DataError)` --
    which matched it and returned 400 INVALID_EVENT, rolling back the
    WHOLE batch. That is precisely the wedged-shop outcome this entire
    prerequisite exists to prevent, just reached through quarantine's own
    write path instead of the original insert. A live NUL-byte push test
    caught this for real (not a hypothetical), which is why it is fixed
    here, not merely documented.

    The fix: attempt the verbatim write first, inside its own SAVEPOINT;
    if THAT specifically fails with a DataError, roll back to the
    savepoint (cheap, always available -- see below) and retry once with
    NUL bytes neutralized (_sanitize_nul_bytes), noting the substitution in
    the stored rejection_reason so an operator/replay knows the payload is
    not 100% byte-identical for this one specific reason. Any OTHER
    exception from either attempt still propagates -- quarantining itself
    failing for an unrelated reason is exactly the "genuinely unexpected"
    case push()'s outer `except Exception` exists for.

    Both attempts run inside their OWN begin_nested() (unlike the single
    top-level flush() this function used before) specifically so a failed
    first attempt leaves the surrounding per-event loop's outer transaction
    -- which may already hold one or more successfully-quarantined/stored
    events from earlier iterations -- fully intact, exactly like
    _store_events' own IntegrityError/DataError handling for real
    SyncEvent inserts. Still flushes (not just adds) before returning,
    for the same "must be durably attached to the outer transaction before
    the NEXT iteration's own SAVEPOINT can start" reason as before."""
    try:
        with db_session.begin_nested():
            db_session.add(SyncQuarantineEvent(
                license_id=license_id, device_id=device_id, batch_index=batch_index,
                raw_payload=raw, rejection_reason=reason,
            ))
            db_session.flush()
        return
    except DataError:
        pass  # fall through to the sanitized retry below

    with db_session.begin_nested():
        db_session.add(SyncQuarantineEvent(
            license_id=license_id, device_id=device_id, batch_index=batch_index,
            raw_payload=_sanitize_nul_bytes(raw),
            rejection_reason=(
                f"{reason} [NOTE: payload contained a character Postgres cannot store "
                "verbatim (likely a NUL byte); stored with that character escaped, not byte-identical]"
            ),
        ))
        db_session.flush()


def _store_events(events: list, license_id, device_id) -> int:
    """Stores each event, idempotent on id. Two layers of idempotency:
    a fast-path existence check (handles the common case: a genuine client
    retry after e.g. a dropped response), and a per-event SAVEPOINT that
    catches IntegrityError from a concurrent push of the exact same id
    losing the race between that check and this flush -- converting what
    would otherwise be an uncaught IntegrityError/500 into the same clean
    no-op outcome, never a duplicate row or a crash.

    Phase 5 prerequisite #3 (docs/launch-readiness/phase5-prerequisites.md
    section 3) changed what happens to a REJECTED event. Before: any single
    malformed or DB-rejected event raised InvalidEventError/DataError out
    to push(), which rolled back the ENTIRE batch and returned 400
    INVALID_EVENT -- so a client whose outbox retries the same batch (the
    only thing a client CAN do after a failed push) would hit the identical
    row and fail identically, forever. Now: a rejected event is written to
    owner_sync_quarantine (see _quarantine_event) with its raw payload,
    device, batch position and rejection reason, then SKIPPED -- the loop
    continues, every other event in the batch still applies, and the
    cursor the client advances to on its next pull moves past the bad
    event too (it was never assigned a `seq` at all, so there is no gap to
    "skip over" -- the surrounding good events simply get the next
    available seq values).

    This deliberately trades strict batch-level consistency for
    availability: skipping a bad event means ONE replayable row is
    temporarily missing from the ledger (visible and replayable from the
    Owner console, see quarantine_routes.py) rather than the WHOLE shop
    being unable to sync at all until an operator hand-edits production
    Postgres. That is the right trade for this specific ledger because
    owner_sync_events rows are append-only, independent business facts
    (a sale, a stock movement) with no cross-row dependency the skip could
    corrupt -- it would be the WRONG trade for data where skipping one row
    could silently change the meaning of the rows around it (e.g. a
    multi-leg financial transaction that must apply atomically or not at
    all). Two distinct rejection sources are quarantined, not just one:

      1. InvalidEventError from _build_event -- malformed BEFORE it ever
         reaches the database (bad UUID, oversized entity_type, unknown
         event_type, non-dict payload). Caught here, before any SAVEPOINT
         is even opened.
      2. DataError from the flush itself -- a payload that is valid JSON
         and passes _build_event's own checks but Postgres genuinely
         refuses at insert time (the concrete, real example: a NUL byte
         embedded in a JSONB string value, which Postgres's UTF8 encoding
         rejects outright). Caught inside the per-event SAVEPOINT, exactly
         alongside the existing IntegrityError handling.

    Any OTHER exception (i.e. not InvalidEventError, not IntegrityError,
    not DataError) still propagates all the way out to push()'s own
    `except Exception` -- a genuinely unexpected failure still rolls back
    the whole batch and returns 500, on purpose: quarantine is for known,
    named rejection reasons, never a catch-all that could paper over a
    real bug by silently discarding events it doesn't understand."""
    stored = 0
    for batch_index, raw in enumerate(events):
        try:
            event = _build_event(raw)
        except InvalidEventError as exc:
            _quarantine_event(raw, license_id=license_id, device_id=device_id, batch_index=batch_index, reason=str(exc))
            continue

        if db_session.get(SyncEvent, event.id) is not None:
            continue  # fast-path idempotent no-op: this id already exists

        event.license_id = license_id
        event.device_id = device_id
        try:
            with db_session.begin_nested():
                db_session.add(event)
                db_session.flush()
            stored += 1
        except IntegrityError:
            # begin_nested()'s context manager already issued ROLLBACK TO
            # SAVEPOINT before re-raising -- the outer transaction (and any
            # earlier events already flushed in this same loop) is still
            # intact. Lost a race with a concurrent push of this exact id;
            # same outcome as the fast-path no-op above, never a 500.
            pass
        except DataError as exc:
            # Same SAVEPOINT-rollback guarantee as IntegrityError above --
            # this event's failed insert is undone, everything before it
            # stays intact. exc.orig is the underlying psycopg error (a
            # concise driver message); falling back to str(exc) covers the
            # unlikely case a DataError arrives with no .orig at all.
            reason = str(exc.orig) if exc.orig is not None else str(exc)
            _quarantine_event(raw, license_id=license_id, device_id=device_id, batch_index=batch_index, reason=reason)
    return stored


def _lock_license_stream(license_id: uuid.UUID) -> None:
    """Acquires a transaction-scoped Postgres advisory lock keyed by
    license_id, closing the seq-visibility race documented as a
    deliberately-unfixed "Residual gap" in
    docs/superpowers/plans/2026-08-06-multi-device-sync-foundation.md until
    now (search that file for _lock_license_stream).

    THE RACE: owner_sync_events.seq is a Postgres IDENTITY column -- values
    are ASSIGNED at INSERT time but only become VISIBLE to other
    transactions at COMMIT time. Without this lock, two concurrent pushes
    for the SAME license could assign seq in one order (say 10 then 11) but
    commit in the other order (11 commits first, because the seq=10
    transaction is slower for any reason -- network, client, GC pause).
    A pull landing in that window sees seq=11 but not the not-yet-committed
    seq=10, and advances its cursor past 11. When seq=10 finally commits, it
    is permanently, silently invisible to that device -- its next pull is
    `WHERE seq > 11`, so seq=10 never surfaces. No error, no retry, no
    trace: silent per-device divergence.

    `pg_advisory_xact_lock` serializes the INSERT-through-COMMIT critical
    section of concurrent pushes for the SAME license, so for that
    license's stream, commit order can never diverge from seq-assignment
    order. It is transaction-scoped by definition -- it releases
    automatically on COMMIT or ROLLBACK of the enclosing transaction, no
    manual unlock required and no risk of a leaked lock if the request
    errors out partway through.

    WHY PER-LICENSE IS CORRECT -- NOT A GLOBAL LOCK (read this before you
    "simplify" it into one lock covering every license; that would be a
    regression, not a simplification):

    owner_sync_events.seq IS one single table-wide identity sequence shared
    across every license in the system -- that part is true. But a
    per-license lock is still fully sufficient for correctness, because
    pull() below is UNCONDITIONALLY scoped `.where(SyncEvent.license_id ==
    license_id)`, and that license_id comes ONLY from the verified
    Installation resolved inside _authenticate() -- never from
    client-supplied input (see _authenticate's own docstring/comment on
    this). A device can only ever ask "give me everything for MY license
    newer than my cursor"; no pull request can span license boundaries.

    That means: two DIFFERENT licenses' events can commit in any
    interleaved order relative to each other, and it is completely
    irrelevant to correctness -- no pull ever observes both streams
    together, so there is nothing for cross-license ordering to violate.
    Within any ONE license's own filtered stream, this per-license lock
    guarantees commit order matches seq order, which is the only ordering
    guarantee pull() actually depends on.

    A single global lock (one fixed key for all licenses) would additionally
    serialize every customer's push against every OTHER customer's push,
    for zero correctness benefit over the per-license version -- pure,
    actively harmful contention under real concurrent load across many
    licenses, not a simplification.

    CONTENTION: realistically a handful of devices per license (not
    thousands) push concurrently, so contention on any single license's
    lock is negligible -- worst case a push waits briefly for another push
    on the SAME license to finish, which is the correct, desired
    serialization, not a performance problem.

    KEY SPACE: hashtext() hashes license_id's text form across the full
    32-bit signed int4 range, implicitly widened to bigint for
    pg_advisory_xact_lock's single-bigint-argument form. Verified against a
    live Postgres 17 instance (not assumed from memory) before writing this:
    the single-bigint-argument form shares its lock namespace between
    session-level (pg_advisory_lock) and transaction-level
    (pg_advisory_xact_lock) callers using the SAME numeric key -- a session
    lock on key K genuinely blocks a transaction lock request for that same
    key K. (The two-integer-argument form, e.g. pg_advisory_xact_lock(a, b),
    was empirically confirmed to be a SEPARATE namespace from the
    single-bigint form even for equal values -- not used here, noted only so
    nobody "fixes" this comment by assuming otherwise.) The only other
    fixed-key, single-bigint-form advisory lock in this codebase is
    owner/tests/conftest.py's _TEST_SUITE_ADVISORY_LOCK_KEY (0x41757261 /
    1095583329, session-level, used to serialize whole pytest processes
    against each other -- unrelated purpose, but the SAME namespace this
    function's key lives in). See test_sync_ordering_concurrency.py's
    key-space assertion test, which checks this explicitly rather than
    leaving it assumed.
    """
    # NOTE: no `::text` cast here -- SQLAlchemy's text() parses a bare `::`
    # immediately after a bind name as colon-escaping syntax, not as
    # Postgres's cast operator, which silently drops the parameter
    # substitution (params ends up {} and the literal `:license_id::text`
    # reaches psycopg as a syntax error). str(license_id) below is already
    # Python text, so hashtext() receives a text argument without a cast.
    db_session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:license_id))"), {"license_id": str(license_id)})


def _advance_device_cursor(device_id: uuid.UUID, license_id: uuid.UUID, new_cursor: int) -> None:
    """Upserts SyncDeviceCursor for this device -- called ONLY from pull()
    (see SyncDeviceCursor's own docstring for why push() must never call
    this). GREATEST-style guard: never lets an already-higher persisted
    cursor regress, regardless of what value is passed in.

    This matters even though `since` is itself a client-supplied, signed
    value that ordinarily only increases: nonce/timestamp replay
    protection stops a captured request from being REPLAYED, but does not
    stop a genuinely fresh, validly-signed pull request that happens to
    carry a lower `since` than a previous one (e.g. a client bug, a
    restored-from-backup client resending an old cursor, or simply two
    requests reordered in flight) from arriving after a higher one already
    landed. Silently regressing the persisted watermark in that case would
    make pruning.py think events between the lower and higher values are
    still needed forever, or -- worse, if pruning already ran using the
    higher watermark -- would make a later prune run compute a watermark
    the DB no longer has full history for. Never letting the stored value
    move backward closes that off entirely; the caller (this device) can
    always still catch up by pulling forward again.

    This helper is deliberately a PURE upsert-with-GREATEST: it stores what
    it is given (never regressing) and makes no judgement about whether the
    value is plausible. Bounding an untrusted, client-supplied `since`
    against server truth is pull()'s job, at the point that untrusted value
    enters -- see the clamp in pull() and the reasoning there."""
    existing = db_session.get(SyncDeviceCursor, device_id)
    if existing is None:
        db_session.add(SyncDeviceCursor(installation_id=device_id, license_id=license_id, last_acked_seq=new_cursor))
    elif new_cursor > existing.last_acked_seq:
        existing.last_acked_seq = new_cursor
    # else: new_cursor <= existing.last_acked_seq -- no-op, by design.


@bp.route("/push", methods=["POST"])
def push():
    body = request.get_json(silent=True)
    try:
        installation, license_id = _authenticate(body or {}, nonce_scope="sync_push")
    except SyncAuthError as exc:
        return _error(exc.reason_code)

    events = body.get("events") if isinstance(body, dict) else None
    if events is None or not isinstance(events, list) or len(events) > _MAX_PUSH_BATCH:
        return _error("INVALID_BATCH")

    # Must be acquired inside the SAME transaction that will INSERT the
    # events below and COMMIT at the end of this handler -- see
    # _lock_license_stream's docstring for why per-license (not global) is
    # correct, and AUDIT note in the sync-foundation plan for the race this
    # closes. _authenticate() above already committed its own nonce-consumption
    # transaction (replay.consume_nonce), so this starts a fresh transaction
    # that naturally extends through _store_events and the commit() below.
    #
    # _store_events' quarantine writes (Phase 5 prerequisite #3) run inside
    # this SAME held-lock window, unavoidably -- they happen interleaved
    # with the real SyncEvent inserts the lock exists to protect, in the
    # same per-event loop, in the same transaction. This does not touch
    # `seq` at all (SyncQuarantineEvent has no seq column, no identity
    # sequence, no ordering claim whatsoever) and does not change WHEN the
    # lock is acquired or released, so it neither extends, weakens, nor
    # reorders the guarantee _lock_license_stream's own docstring documents
    # -- it is simply more work done during an interval the lock already
    # spans, touching a table the race that lock closes has no opinion
    # about.
    _lock_license_stream(license_id)

    try:
        stored = _store_events(events, license_id, installation.id)
    except (InvalidEventError, DataError):
        # Defense-in-depth only, as of Phase 5 prerequisite #3 -- see
        # InvalidEventError's own docstring. _store_events no longer raises
        # either of these for the malformed-input/DB-rejection cases it
        # used to (both are now quarantined-and-skipped inline); this
        # branch exists only to fail closed (a clean, understood 400
        # instead of an uncaught-exception 500) if some future code path
        # still raises one of them directly.
        db_session.rollback()
        return _error("INVALID_EVENT")
    except Exception:
        db_session.rollback()
        current_app.logger.exception("Internal failure during sync push (request body not logged).")
        return _error("INTERNAL_ERROR", 500)

    db_session.commit()
    return jsonify({"stored": stored, "received": len(events)}), 200


@bp.route("/pull", methods=["GET", "POST"])
def pull():
    # GET with a signed JSON body: no existing route in this codebase
    # authenticates a GET request (api_external/routes.py's only two GET
    # routes -- signing-keys, service-info -- are intentionally public, no
    # signature involved), so there is no established header-based signed-
    # GET convention to defer to here. This follows the same signed-JSON-
    # body shape push already uses, just over GET -- Flask's
    # request.get_json() does not restrict itself by HTTP method.
    #
    # POST also accepted (2026-08-12): the Windows desktop client's frozen
    # PyInstaller build reproducibly gets an empty response body on
    # GET-with-body specifically -- push (POST) and the same GET-with-body
    # call from a non-frozen interpreter both work fine against this exact
    # route, isolating it to something in the frozen build's networking
    # stack, not this route or the sync protocol. Android's existing
    # GET-with-body client (SyncRelayClient.kt's hand-rolled raw-socket
    # executeGetWithBody, needed because OkHttp itself forbids GET+body)
    # is unaffected and keeps using GET -- this is purely additive.
    #
    # `since` lives INSIDE the signed body (not a `?since=` query param) --
    # see the module docstring: a free query param would let a captured
    # request be replayed with a different `since` and walk more history
    # than the original request asked for.
    body = request.get_json(silent=True)
    try:
        installation, license_id = _authenticate(body or {}, nonce_scope="sync_pull")
    except SyncAuthError as exc:
        return _error(exc.reason_code)

    try:
        since = int(body["since"])
        if since < 0:
            raise ValueError("since must be non-negative")
    except (KeyError, TypeError, ValueError):
        return _error("INVALID_SINCE")

    try:
        rows = db_session.execute(
            select(SyncEvent)
            .where(SyncEvent.license_id == license_id)
            .where(SyncEvent.seq > since)
            .where(SyncEvent.device_id != installation.id)
            .order_by(SyncEvent.seq)
            .limit(500)
        ).scalars().all()
        if rows:
            # A real seq from a row that genuinely exists -- nothing to bound.
            cursor = rows[-1].seq
        else:
            # ZERO ROWS MATCHED, so `since` would otherwise become the cursor
            # VERBATIM -- and `since` is untrusted. It is validated above only
            # as a NON-NEGATIVE int, deliberately with no upper bound, because
            # a client genuinely cannot know the server's current max seq and
            # a fixed ceiling would be wrong for every license.
            #
            # Unbounded, though, an absurd `since` becomes an absurd persisted
            # last_acked_seq: a device on record as having acknowledged events
            # THAT DO NOT EXIST YET. This is not hypothetical -- a
            # restored-from-backup client, a version-skewed client, or a plain
            # integer bug all produce it, and the request is validly signed by
            # the device's own key, so no authentication check can reject it.
            # (Found by exercising this path against a live database, not by
            # review: a single pull with since=10**15 really did destroy a
            # license's entire event history.)
            #
            # The damage is to events written AFTERWARDS. pruning.py's
            # watermark is MIN(last_acked_seq) over active devices and deletes
            # `WHERE seq <= watermark`, so a bogus cursor pre-acknowledges the
            # whole FUTURE of the stream: every new event becomes instantly
            # prunable the moment it is written, deleted before the device
            # that over-claimed ever receives it. And because
            # _advance_device_cursor never regresses (correctly, for its own
            # reasons), the bogus high-water mark can never be walked back
            # down -- it is unrecoverable through the protocol.
            #
            # So `since` is bounded by the license's real MAX(seq): server
            # truth a device cannot legitimately exceed, since it cannot have
            # acknowledged an event that was never written. COALESCE to 0
            # covers a license with no events at all. This is a single indexed
            # lookup on ix_owner_sync_events_license_seq (license_id, seq),
            # and only on the zero-rows path -- a caught-up device's pull.
            #
            # The clamped value is also what is RETURNED, not just what is
            # stored. Returning the raw over-large `since` would leave that
            # client permanently blind: it would keep asking from a point the
            # stream never reaches and never receive another event again.
            # Handing back the true high-water mark lets it self-heal on its
            # next pull. Well-behaved clients are unaffected -- for them
            # `since` is already <= MAX(seq), so this changes nothing.
            max_seq = db_session.execute(
                select(func.coalesce(func.max(SyncEvent.seq), 0)).where(SyncEvent.license_id == license_id)
            ).scalar_one()
            cursor = min(since, max_seq)
        # Phase 5 prerequisite #2: persist this device's own claim of "how
        # far it has caught up" -- see _advance_device_cursor's docstring
        # and app/models/sync.py::SyncDeviceCursor's docstring for why this
        # exists at all and why it must happen even when `rows` is empty
        # (a device catching itself up to a seq with nothing new to
        # receive still needs its cursor recorded, or pruning.py can never
        # consider that seq safe to delete). Same transaction as the read
        # above, committed together right below -- if the commit fails for
        # any reason, the whole response falls through to the generic
        # except/500 below rather than ever reporting a cursor the server
        # didn't actually persist.
        _advance_device_cursor(installation.id, license_id, cursor)
        db_session.commit()
    except Exception:
        db_session.rollback()
        current_app.logger.exception("Internal failure during sync pull.")
        return _error("INTERNAL_ERROR", 500)

    return jsonify({
        "events": [{
            "id": str(r.id),
            "entity_type": r.entity_type,
            "entity_id": str(r.entity_id),
            "event_type": r.event_type,
            "payload": r.payload,
            "created_at": r.client_created_at.isoformat(),
            "seq": r.seq,
        } for r in rows],
        "cursor": cursor,
    }), 200
