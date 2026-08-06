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
"""
from __future__ import annotations

import uuid
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import DataError, IntegrityError

from app.extensions import db_session
from app.licensing_service import device_identity, replay
from app.licensing_service.canonical import canonicalize_bytes
from app.models.installations import Installation
from app.models.sync import SyncEvent

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
    """Raised for a malformed event in a push batch -- caught once by the
    route handler, which rolls back and returns a clean 400 rather than
    letting a KeyError/ValueError/DataError reach the client as a 500."""


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
        request_timestamp = datetime.fromisoformat(body["timestamp"])
    except (ValueError, TypeError):
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
        client_created_at = datetime.fromisoformat(raw["created_at"])
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


def _store_events(events: list, license_id, device_id) -> int:
    """Stores each event, idempotent on id. Two layers of idempotency:
    a fast-path existence check (handles the common case: a genuine client
    retry after e.g. a dropped response), and a per-event SAVEPOINT that
    catches IntegrityError from a concurrent push of the exact same id
    losing the race between that check and this flush -- converting what
    would otherwise be an uncaught IntegrityError/500 into the same clean
    no-op outcome, never a duplicate row or a crash. Raises InvalidEventError
    (malformed input) or DataError (unexpected DB-level rejection) up to the
    caller, which rolls back the whole batch and returns a clean 400."""
    stored = 0
    for raw in events:
        event = _build_event(raw)
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
    return stored


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

    try:
        stored = _store_events(events, license_id, installation.id)
    except (InvalidEventError, DataError):
        db_session.rollback()
        return _error("INVALID_EVENT")
    except Exception:
        db_session.rollback()
        current_app.logger.exception("Internal failure during sync push (request body not logged).")
        return _error("INTERNAL_ERROR", 500)

    db_session.commit()
    return jsonify({"stored": stored, "received": len(events)}), 200


@bp.route("/pull", methods=["GET"])
def pull():
    # GET with a signed JSON body: no existing route in this codebase
    # authenticates a GET request (api_external/routes.py's only two GET
    # routes -- signing-keys, service-info -- are intentionally public, no
    # signature involved), so there is no established header-based signed-
    # GET convention to defer to here. This follows the same signed-JSON-
    # body shape push already uses, just over GET -- Flask's
    # request.get_json() does not restrict itself by HTTP method.
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
        "cursor": rows[-1].seq if rows else since,
    }), 200
