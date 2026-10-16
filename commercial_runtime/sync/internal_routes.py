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

import base64
import hmac

from flask import Blueprint, jsonify, request

# F7 (BOOKS-WAVE1-PLAN.md decision F7; v2 finding 7): `/_internal/outbox`
# used to hand Kotlin the ENTIRE `sync_outbox` table in one HTTP response,
# unbounded -- a one-time historical backfill (this wave's own
# `general_ledger.backfill_legacy_journals`, roughly 2 events per historical
# sale) can put a shop's outbox at order 20k rows, meaning order 20MB in ONE
# response, on a phone, every 10-second tick until it drains. Both the
# default (when Kotlin's request carries no `limit` at all) and the hard cap
# (a `limit` above this is silently clamped down, never rejected) are this
# SAME number -- oldest-first paging (SyncService.read_outbox's own `ORDER
# BY rowid`) means asking for more than this never loses anything, it only
# ever changes how many TICKS a large backlog takes to fully drain.
#
# F7 KOTLIN FINDING (read android/aura-retail/.../sync/SyncCoordinator.kt's
# `pushOnce` before changing this default -- BOOKS-WAVE1-PLAN.md's own
# instruction): Kotlin's `pushOnce` reads this endpoint ONCE per tick, drains
# whatever it received (chunked further, client-side, to Owner's own
# `_MAX_PUSH_BATCH`), acks each chunk it genuinely pushed, and returns -- it
# does NOT loop back to re-read this endpoint within one tick. A capped page
# therefore drains a large backlog over SEVERAL ticks instead of entirely on
# the first one: strictly slower, never lossy (nothing is ever deleted here;
# `ack_outbox` only ever removes the ids Kotlin actually names, and the next
# tick's fresh SELECT naturally returns whatever is left). Kotlin's own
# comment on that call site states outright that "`SyncService.read_outbox`
# has no LIMIT" and treats the events it reads as "the true complete
# backlog" for `recordPending`, the health indicator `SyncStatusScreen`
# reads -- THAT is the one real, if minor, side effect of this default:
# while a backlog larger than this page size is still draining, the
# reported pending count under-reports the true backlog size (it reads
# this page's size, not the true total) until it catches up, tick by tick.
# No Kotlin change is made here (this wave's own files do not include
# android/**) -- reported as this wave's own F7 finding rather than left
# undiscovered, per BOOKS-WAVE1-PLAN.md's explicit instruction to STOP and
# report rather than "fix" Kotlin from this side.
_OUTBOX_DEFAULT_PAGE_LIMIT = 500
_OUTBOX_MAX_PAGE_LIMIT = 500



def _attach_tbs_for_kotlin(events, sync_service) -> None:
    """Add `auth_tbs_b64` to every authority event whose `_auth` is awaiting a
    signature (prepare-only signer). No signer -> nothing to add: the event goes
    out unsigned and receivers park it (read_outbox already logged that once)."""
    from commercial_runtime.sync import authority

    pending = [e for e in events if authority.is_authority_event(e)]
    if not pending:
        return
    signer = sync_service._authority_env().signer
    if signer is None:
        return
    for event in pending:
        auth = event["payload"].get(authority.AUTH_KEY)
        if not isinstance(auth, dict) or auth.get("sig"):
            continue
        tbs = authority.build_tbs(event, licence=signer.licence, inst=signer.inst, dev=signer.dev)
        event["auth_tbs_b64"] = base64.b64encode(tbs).decode("ascii")


def make_sync_internal_blueprint(
    *,
    sync_service,
    get_conn,
    state_repository,
    shared_secret: str,
    blueprint_name: str = "sync_internal",
    url_prefix: str = "/api/sync",
    bootstrap=None,
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
    # SYNC-BOOT Phase 3 (Android requester). `bootstrap` is the RETAIL stream's seam to the shared
    # bootstrap package (Kotlin builds and signs the Owner-mailbox calls itself, because Python never
    # holds the device key; it hands the DECRYPTED copy here to be verified and staged). Object with
    # schema_version(), stage(data: bytes, relay_url: str) -> dict (raises BootstrapError),
    # include_own_pending() -> bool, include_own_done(). None (the registry stream, every older caller)
    # registers none of the routes below and leaves /_internal/cursor byte-for-byte what it was.

    def _authorized() -> bool:
        provided = request.headers.get("X-Aura-Internal-Secret", "")
        return hmac.compare_digest(provided, shared_secret)

    def _forbidden():
        return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Unauthorized."}), 403

    def _installation_id():
        record = state_repository.load()
        return record.owner_installation_id if record else None

    def _outbox_page_limit() -> int:
        """F7: the effective page size for THIS request -- an absent or
        unparseable/non-positive `limit` query parameter falls back to
        `_OUTBOX_DEFAULT_PAGE_LIMIT`; anything supplied is clamped to
        `_OUTBOX_MAX_PAGE_LIMIT`, never rejected (a caller asking for more
        than the cap just gets the cap, not a 400)."""
        raw = request.args.get("limit")
        if raw is None:
            return _OUTBOX_DEFAULT_PAGE_LIMIT
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return _OUTBOX_DEFAULT_PAGE_LIMIT
        if value <= 0:
            return _OUTBOX_DEFAULT_PAGE_LIMIT
        return min(value, _OUTBOX_MAX_PAGE_LIMIT)

    @bp.route("/_internal/outbox", methods=["GET"])
    def internal_outbox():
        """Rows Kotlin is about to sign and push, plus the installation_id
        it must sign them under -- sourced from the SAME LicenseStateRepository
        licensing's own routes use (never a second/invented source), read
        fresh on every call so activation completing after process start is
        picked up on the very next tick with no restart required (same
        rationale as SyncService's own client_factory being rebuilt per
        attempt).

        F7 (BOOKS-WAVE1-PLAN.md decision F7; v2 finding 7): returns a
        BOUNDED, oldest-first PAGE of the outbox -- see this module's own
        `_OUTBOX_DEFAULT_PAGE_LIMIT`/`_OUTBOX_MAX_PAGE_LIMIT` comment for
        the full reasoning and the Kotlin-caller finding this change
        surfaces. An optional `?limit=N` query parameter narrows the page
        further (still capped at the same maximum); omitting it uses the
        default. Nothing here changes `SyncService.read_outbox`'s own
        default behaviour for any OTHER caller (`push_once()` on Windows,
        every pre-existing test) -- only THIS route ever passes an
        explicit `limit`."""
        if not _authorized():
            return _forbidden()
        installation_id = _installation_id()
        limit = _outbox_page_limit()
        conn = get_conn()
        try:
            with sync_service._lock:
                events = sync_service.read_outbox(conn, limit=limit)
        finally:
            conn.close()
        # AUDIT-033 / S-AUTH (D3, Android): Python never holds the Android
        # device key, so read_outbox left each authority event's `_auth`
        # WITHOUT a `sig`. Hand Kotlin the exact bytes to sign as a top-level
        # per-event `auth_tbs_b64` (never inside the payload: the relay keeps
        # only the known keys, and the field must not be signed or stored).
        # Kotlin signs these bytes as-is -- it must NOT re-canonicalise, because
        # Kotlin's number handling differs from Python's (int vs float) and a
        # re-rendered document would not verify. Non-authority events, and
        # authority events that already carry a sig, are left exactly as they
        # were.
        _attach_tbs_for_kotlin(events, sync_service)
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
        """`relay_url` is an OPTIONAL query parameter (retail schema v31,
        ROADMAP.md's 2026-09-14 "the sync cursor must know which relay it
        belongs to" entry): when Kotlin passes it, this calls
        SyncService.ensure_cursor_matches_relay BEFORE reading the cursor, so
        Android gets the same relay-binding protection `pull_once()` gives
        the Windows/Python pull loop, through this one seam, rather than a
        second Kotlin-side mechanism. When absent -- every caller today --
        this behaves exactly as before the parameter existed: no relay check,
        no write, no commit, just the read `read_cursor` has always done."""
        if not _authorized():
            return _forbidden()
        installation_id = _installation_id()
        relay_url = request.args.get("relay_url")
        conn = get_conn()
        try:
            with sync_service._lock:
                if relay_url is not None:
                    sync_service.ensure_cursor_matches_relay(conn, relay_url)
                    conn.commit()
                since = sync_service.read_cursor(conn)
        finally:
            conn.close()
        body = {"installation_id": installation_id, "since": since}
        if bootstrap is not None:
            # Asked for once, after a bootstrap install or a restore (see BootstrapHooks.include_own_once).
            body["include_own"] = bool(bootstrap.include_own_pending())
        return jsonify(body), 200

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

    if bootstrap is not None:
        @bp.route("/_internal/bootstrap/info", methods=["GET"])
        def internal_bootstrap_info():
            """What Kotlin needs to ask Owner for a copy: the schema this build runs."""
            if not _authorized():
                return _forbidden()
            return jsonify({"schema_version": int(bootstrap.schema_version())}), 200

        @bp.route("/_internal/bootstrap/stage", methods=["POST"])
        def internal_bootstrap_stage():
            """Kotlin downloaded and DECRYPTED a copy of the shop from Owner's mailbox; verify it (signed
            manifest, same shop, schema, no open drawer) and park it for the next start. The body is the
            zip itself. A refusal is a normal outcome and says why; nothing is staged then."""
            if not _authorized():
                return _forbidden()
            from commercial_runtime.sync.bootstrap.errors import BootstrapError

            data = request.get_data()
            if not data:
                return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Expected the copy as the body."}), 400
            try:
                record = bootstrap.stage(data, request.args.get("relay_url", ""))
            except BootstrapError as exc:
                return jsonify({"reason_code": exc.reason_code, "detail": exc.message}), 409
            return jsonify({"result": "SUCCESS", "as_of": record.get("as_of")}), 200

        @bp.route("/_internal/bootstrap/include-own-done", methods=["POST"])
        def internal_bootstrap_include_own_done():
            """Owner echoed include_own: the one-shot is spent."""
            if not _authorized():
                return _forbidden()
            bootstrap.include_own_done()
            return jsonify({"result": "SUCCESS"}), 200

    return bp
