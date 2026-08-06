"""Multi-device sync relay: push/pull routes for the append-only
owner_sync_events log (multi-device-sync-foundation plan, Task 2).

Authentication mirrors owner/app/licensing_service/checkin.py's
device-signature-verification pattern as closely as this endpoint's shape
allows: look up Installation by installation_id, load its currently-ACTIVE
device public key, verify the Ed25519 signature over the canonicalized body
(minus `signature`), reject a suspended/deactivated/replaced installation
the same way checkin.py does, then resolve license_id from the VERIFIED
installation's own relationship -- never from client input.

Deliberate divergence from checkin.py, matching the plan's own scope
(see docs/superpowers/plans/2026-08-06-multi-device-sync-foundation.md,
Task 2's draft code and "Residual gaps" section): no nonce/timestamp replay
protection here. Push is idempotent on the client-generated event id, so a
replayed push is a pure no-op; pull is a read scoped to the verified
installation's own license, so a replayed pull discloses nothing beyond
what that device could already read. Full replay protection/rate limiting
on this relay is an explicitly-deferred, documented gap in the plan, not an
oversight.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.extensions import db_session
from app.licensing_service import device_identity
from app.licensing_service.canonical import canonicalize_bytes
from app.models.installations import Installation
from app.models.sync import SyncEvent

bp = Blueprint("sync", __name__, url_prefix="/api/sync/v1")

# Abuse-resistance bound per the plan's "reject unreasonably large batches"
# requirement (full rate limiting is an explicitly deferred, documented gap).
_MAX_PUSH_BATCH = 200
_ALLOWED_EVENT_TYPES = ("create", "update", "delete")


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


def _error(reason_code: str, http_status: int = 400):
    return jsonify({"reason_code": reason_code}), http_status


def _authenticate(body: dict) -> tuple[Installation, uuid.UUID]:
    """Verify-then-resolve, exactly checkin.py's order of operations:
    1) look up Installation, 2) load its ACTIVE device key, 3) verify the
    signature over the canonicalized body, 4) only then check installation
    status / resolve license_id. license_id NEVER comes from body input."""
    if not isinstance(body, dict):
        raise SyncAuthError("INVALID_REQUEST")

    installation_id_raw = body.get("installation_id")
    if not installation_id_raw:
        raise SyncAuthError("INSTALLATION_NOT_FOUND")
    try:
        installation_id = uuid.UUID(str(installation_id_raw))
    except (ValueError, TypeError):
        raise SyncAuthError("INSTALLATION_NOT_FOUND")

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


def _parse_event(raw: dict) -> SyncEvent | None:
    """Returns None (caller skips as idempotent no-op) if the event id is
    already stored; raises ValueError on any malformed event field."""
    event_id = uuid.UUID(str(raw["id"]))
    if db_session.get(SyncEvent, event_id) is not None:
        return None

    entity_type = raw["entity_type"]
    if not isinstance(entity_type, str) or not entity_type:
        raise ValueError("entity_type")
    entity_id = uuid.UUID(str(raw["entity_id"]))
    event_type = raw["event_type"]
    if event_type not in _ALLOWED_EVENT_TYPES:
        raise ValueError("event_type")
    payload = raw["payload"]
    if not isinstance(payload, dict):
        raise ValueError("payload")
    client_created_at = datetime.fromisoformat(raw["created_at"])

    return SyncEvent(
        id=event_id,
        entity_type=entity_type,
        entity_id=entity_id,
        event_type=event_type,
        payload=payload,
        client_created_at=client_created_at,
    )


@bp.route("/push", methods=["POST"])
def push():
    body = request.get_json(silent=True)
    try:
        installation, license_id = _authenticate(body or {})
    except SyncAuthError as exc:
        return _error(exc.reason_code)

    events = body.get("events") if isinstance(body, dict) else None
    if events is None or not isinstance(events, list) or len(events) > _MAX_PUSH_BATCH:
        return _error("INVALID_BATCH")

    stored = 0
    for raw in events:
        try:
            event = _parse_event(raw if isinstance(raw, dict) else {})
        except (KeyError, ValueError, TypeError):
            db_session.rollback()
            return _error("INVALID_EVENT")
        if event is None:
            continue  # idempotent no-op: this id already exists, no new seq
        event.license_id = license_id
        event.device_id = installation.id
        db_session.add(event)
        stored += 1

    db_session.commit()
    return jsonify({"stored": stored, "received": len(events)}), 200


@bp.route("/pull", methods=["GET"])
def pull():
    # GET with a signed JSON body: no existing route in this codebase
    # authenticates a GET request (api_external/routes.py's only two GET
    # routes -- signing-keys, service-info -- are intentionally public, no
    # signature involved), so there is no established header-based signed-
    # GET convention to defer to here. This follows the same signed-JSON-
    # body shape push already uses (and the shape Task 5/9's relay clients
    # are designed around), just over GET -- Flask's request.get_json()
    # does not restrict itself by HTTP method.
    body = request.get_json(silent=True)
    try:
        installation, license_id = _authenticate(body or {})
    except SyncAuthError as exc:
        return _error(exc.reason_code)

    try:
        since = int(request.args.get("since", "0"))
    except (TypeError, ValueError):
        return _error("INVALID_SINCE")

    rows = db_session.execute(
        select(SyncEvent)
        .where(SyncEvent.license_id == license_id)
        .where(SyncEvent.seq > since)
        .where(SyncEvent.device_id != installation.id)
        .order_by(SyncEvent.seq)
        .limit(500)
    ).scalars().all()

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
