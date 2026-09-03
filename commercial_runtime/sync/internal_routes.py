"""Android-only local sync HTTP surface (multi-device-sync-foundation,
Task 9/wiring). Mirrors `commercial_runtime/licensing_contracts/routes.py`'s
`_register_internal_sync_routes` pattern exactly: same
`X-Aura-Internal-Secret` shared-secret auth (proves a request came from
THIS app's own Kotlin process, not some other app sharing the loopback
port -- localhost binding alone already keeps other devices out), same
`/_internal/...` naming, same "raw response handed across the Kotlin/Python
boundary for the SAME reason" rationale.

Why this exists at all: on Android, `AndroidBridgeDeviceIdentityProvider.sign()`
always raises (Python on Android never holds the device's private key -- see
that class's own docstring). `SyncRelayClient`/`SyncService` (Task 5) were
built assuming a Python-side `signer.sign()` call works synchronously (true
on Windows via DPAPI) -- that assumption does not hold on Android. Kotlin's
`DeviceIdentity`/`OwnerClient`-equivalent (`SyncRelayClient.kt`) is the ONLY
thing on Android that ever makes a signed HTTP call to Owner's
`/api/sync/v1/push|pull` -- exactly mirroring how `OwnerClient.kt` is
already "the ONLY place on Android that ever makes a signed HTTP call to
Owner" for licensing. These routes are the narrow seam Kotlin uses to read
the outbox it's about to sign-and-push, acknowledge what it successfully
pushed, read the cursor it's about to sign-and-pull, and apply what it
successfully pulled -- never anything requiring the private key itself.

Registered only when `LICENSING_PLATFORM == 'ANDROID'` and an
`internal_shared_secret` was generated (see products/retail/backend/app.py) --
inert (not registered at all) on Windows, where `SyncService` drives its own
push/pull loop directly via `SyncRelayClient`'s Python signer.

Two-stream Android wiring (sync backend follow-up, closing the same gap
`REGISTRY_SYNC_ENTITY_TYPES` documents on Windows -- see its comment in
sync_service.py for the desktop half of this wound before it read
`user_permission`): `make_sync_internal_blueprint` is called TWICE on
Android, once per `SyncService` instance/database, using the
`blueprint_name`/`url_prefix` parameters below to avoid colliding with each
other on the same Flask app -- one over retail.db (`RETAIL_SYNC_ENTITY_TYPES`,
the original/default registration this module has always had), one over
registry.db (`REGISTRY_SYNC_ENTITY_TYPES`, `user`/`user_permission`). Without
the second registration, a `user` event reaching an Android install's Kotlin
pull loop had nowhere to be applied at all: the one existing instance is
gated to `RETAIL_SYNC_ENTITY_TYPES`, which excludes `user`, so `_apply_event`
silently dropped it (cursor still advancing past it, per that gate's own
comment) -- a cashier account created elsewhere simply never arrived on that
device, with no error anywhere to point at.
"""
from __future__ import annotations

import hmac

from flask import Blueprint, jsonify, request


def make_sync_internal_blueprint(
    *,
    sync_service,
    get_conn,
    state_repository,
    shared_secret: str,
    blueprint_name: str = "sync_internal",
    url_prefix: str = "/api/sync",
) -> Blueprint:
    """`blueprint_name`/`url_prefix` default to the ONLY values this
    function has ever used, so the existing (retail-stream) call site in
    products/retail/backend/app.py's ANDROID branch, and every existing test
    in test_internal_routes.py, are byte-for-byte unchanged -- neither
    passes these arguments at all.

    Parameterized (Android registry-stream wiring, sync backend follow-up)
    because Android now builds a SECOND internal blueprint alongside this
    one -- one per SyncService instance/database (retail.db, registry.db;
    see products/retail/backend/app.py's ANDROID branch) -- and Flask
    refuses to register two blueprints sharing a name, or two routes sharing
    a URL prefix, on the same app. Both parameters must be overridden
    together for the second instance: a distinct `blueprint_name` alone
    would still collide on `url_prefix`, and vice versa.
    """
    bp = Blueprint(blueprint_name, __name__, url_prefix=url_prefix)

    def _authorized() -> bool:
        provided = request.headers.get("X-Aura-Internal-Secret", "")
        return hmac.compare_digest(provided, shared_secret)

    def _forbidden():
        return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Unauthorized."}), 403

    def _installation_id():
        record = state_repository.load()
        return record.owner_installation_id if record else None

    @bp.route("/_internal/outbox", methods=["GET"])
    def internal_outbox():
        """Rows Kotlin is about to sign and push, plus the installation_id
        it must sign them under -- sourced from the SAME LicenseStateRepository
        licensing's own routes use (never a second/invented source), read
        fresh on every call so activation completing after process start is
        picked up on the very next tick with no restart required (same
        rationale as SyncService's own client_factory being rebuilt per
        attempt)."""
        if not _authorized():
            return _forbidden()
        installation_id = _installation_id()
        conn = get_conn()
        try:
            with sync_service._lock:
                events = sync_service.read_outbox(conn)
        finally:
            conn.close()
        return jsonify({"installation_id": installation_id, "events": events}), 200

    @bp.route("/_internal/outbox/ack", methods=["POST"])
    def internal_outbox_ack():
        """Deletes exactly the outbox rows Kotlin's push to Owner just
        genuinely succeeded for. Never called on a failed/rejected push --
        Kotlin's SyncCoordinator leaves the outbox untouched on failure,
        mirroring push_once()'s own all-or-nothing discipline."""
        if not _authorized():
            return _forbidden()
        body = request.get_json(silent=True) or {}
        ids = body.get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            return jsonify({"reason_code": "INVALID_REQUEST", "detail": "ids must be a list of strings."}), 400
        conn = get_conn()
        try:
            with sync_service._lock:
                sync_service.ack_outbox(conn, ids)
                conn.commit()
        finally:
            conn.close()
        return jsonify({"result": "SUCCESS"}), 200

    @bp.route("/_internal/cursor", methods=["GET"])
    def internal_cursor():
        if not _authorized():
            return _forbidden()
        installation_id = _installation_id()
        conn = get_conn()
        try:
            with sync_service._lock:
                since = sync_service.read_cursor(conn)
        finally:
            conn.close()
        return jsonify({"installation_id": installation_id, "since": since}), 200

    @bp.route("/_internal/pull-apply", methods=["POST"])
    def internal_pull_apply():
        """Applies a pull result Kotlin already fetched directly from Owner
        (the exact `{"events": [...], "cursor": N}` shape
        SyncRelayClient.pull() returns on desktop) and advances the local
        cursor -- reuses SyncService.apply_pull_result(), the SAME apply
        logic pull_once() uses on Windows, never a second re-derivation."""
        if not _authorized():
            return _forbidden()
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or "cursor" not in body:
            return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Expected an object with events/cursor."}), 400
        conn = get_conn()
        try:
            with sync_service._lock:
                try:
                    sync_service.apply_pull_result(conn, body)
                    conn.commit()
                except Exception:
                    # conn.close() with no prior commit() discards any
                    # uncommitted work -- a mid-apply failure leaves the
                    # cursor exactly where it was, matching pull_once()'s
                    # own all-or-nothing behavior (see its docstring).
                    return jsonify({"reason_code": "APPLY_FAILED", "detail": "Could not apply the pulled batch."}), 400
        finally:
            conn.close()
        return jsonify({"result": "SUCCESS"}), 200

    return bp
