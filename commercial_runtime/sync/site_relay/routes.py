"""LAN-facing Flask routes for the site relay's push/pull wire contract
(retail schema v30, `docs/launch-readiness/lan-restaurant-design.md` sec3):
a second, LAN-facing implementation of Owner's `/api/sync/v1/push|pull`
contract, hosted by the main till's own backend so paired devices on one
shop's wifi converge with no internet at all. Port of
`owner/app/sync/routes.py`'s `push`/`pull` view functions onto raw
`sqlite3` + this hub's own `auth.py`/`store.py` -- the WIRE SHAPE (request
fields, response fields, reason codes, HTTP status codes) is deliberately
identical to Owner's, so `commercial_runtime/sync/relay_client.py` (the
client `SyncRelayClient`, written against Owner's contract) works against
THIS hub with zero changes -- same signed-body shape, same `since`-inside-
the-body rule, same GET+POST `/pull` acceptance, same
`{"reason_code": "..."}` 400 shape for every rejection.

Follows `commercial_runtime/sync/internal_routes.py`'s `make_*_blueprint(*,
...)` factory pattern: a factory function that closes over its dependencies
(here, `get_conn`) and returns a `Blueprint`, rather than a module-level
Blueprint wired to module-level globals -- the same reason
`make_sync_internal_blueprint` is a factory (so Android can register it
twice, once per `SyncService`/database): a caller here may eventually want
more than one site-relay instance in a process, or simply wants to inject a
test double for `get_conn` without any module-level state to reset between
tests.

CONNECTION CONTRACT (matches `store.py`'s own stated convention exactly):
`get_conn` is a zero-argument callable returning an OPEN `sqlite3.Connection`
whose `row_factory` is ALREADY `sqlite3.Row` -- setting it is the caller's
job, not this module's (see `store.py`'s module docstring, and `auth.py`'s
`authenticate` docstring, which makes the identical assumption). Each route
below opens exactly one connection per request and closes it in a `finally`,
the same shape `make_sync_internal_blueprint`'s routes use.

ONE DELIBERATE DIVERGENCE FROM OWNER, DOCUMENTED HERE RATHER THAN DISCOVERED
LATER -- Owner's `push()` QUARANTINES a single malformed event
(`_store_events` + the `owner_sync_quarantine` table) and lets the rest of
the batch through; this route does NOT, because retail schema v30 claims no
`site_sync_quarantine` table (see `products/retail/backend/database/
schema.py`'s `_migrate_add_site_relay` docstring: "Six new, self-contained
tables" -- a quarantine table was not one of them, and inventing a SEVENTH
table was out of scope for this task). Instead: EVERY event in a push batch
is validated with `store.validate_event` BEFORE any of them are inserted,
and if ANY event in the batch is malformed, the WHOLE batch is rejected with
`{"reason_code": "INVALID_EVENT", "event_index": <n>}` -- naming the
offending position so the failure is diagnosable rather than a mystery, but
rejecting the entire batch rather than silently dropping just the bad row.

This fails CLOSED: nothing is ever silently dropped, and no business data
that reaches this route is ever lost without an explicit, loud rejection.
The honest trade against Owner's per-row quarantine is availability: a
single poison event -- one bad row a client's own outbox keeps retrying --
blocks that ENTIRE batch, and therefore that device's entire outbox, until
a human looks at it, for as long as the device keeps resending the same
batch unchanged. Owner did not start with quarantine either -- its own
`InvalidEventError` docstring records that the FIRST version of that route
had this exact whole-batch-rejection behaviour, and quarantine was built
specifically because "one malformed row stops that shop syncing forever"
became a real, not theoretical, outage once its allowlist widened across
two clients on different release cadences. The identical pressure will
eventually apply here as more device/version combinations push to this
hub. THE EVENTUAL FIX IS THE SAME ONE OWNER ALREADY BUILT: a
`site_sync_quarantine` table (mirroring `owner_sync_quarantine`'s shape --
raw payload, origin device, batch position, rejection reason) and a schema
bump to claim it, at which point this route's push handler should quarantine
per-event exactly like `_store_events` does, instead of rejecting the whole
batch. Left as a named, written-down gap rather than silently deferred.
"""
from __future__ import annotations

import base64
import binascii
import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from commercial_runtime.sync.site_relay import auth as site_auth
from commercial_runtime.sync.site_relay import join as site_join
from commercial_runtime.sync.site_relay import pairing as site_pairing
from commercial_runtime.sync.site_relay import replay, store

logger = logging.getLogger(__name__)


def _error(reason_code: str, http_status: int = 400, **extra) -> tuple:
    """Every auth/validation refusal in this module is `{"reason_code":
    ...}` at HTTP 400 -- matching Owner's own `_error` helper and this
    codebase's stated convention (see `auth.py`'s `SiteAuthError` docstring
    for the citation). `http_status` is overridable only for the one
    genuine-server-fault path (`INTERNAL_ERROR`, 500) -- callers below never
    pass anything else. `**extra` lets `push()` attach `event_index` to an
    `INVALID_EVENT` rejection without a second, bespoke response-shaping
    helper."""
    body = {"reason_code": reason_code}
    body.update(extra)
    return jsonify(body), http_status


def make_site_relay_blueprint(
    *,
    get_conn,
    blueprint_name: str = "site_relay",
    url_prefix: str = "/api/sync/v1",
    skew_seconds: int = replay.DEFAULT_SKEW_SECONDS,
    nonce_ttl_seconds: int = replay.DEFAULT_NONCE_TTL_SECONDS,
    pairing_codes: "site_pairing.PairingCodeStore | None" = None,
    hub_identity_provider=None,
    license_public_id_provider=None,
    trust_store_provider=None,
) -> Blueprint:
    """`blueprint_name`/`url_prefix` default to exactly Owner's own route
    prefix (`/api/sync/v1`) so `SyncRelayClient`'s hardcoded `/api/sync/v1/
    push` and `/api/sync/v1/pull` paths reach this blueprint unmodified
    when a caller points the client's `base_url` at this hub instead of at
    Owner -- the whole point of this route set being wire-compatible with
    the cloud relay. Overridable only so a future caller registering more
    than one instance in the same process (unlikely for this single-hub
    design, but the pattern costs nothing to support -- see
    `make_sync_internal_blueprint`'s identical parameterization) is not
    forced to collide on either the blueprint name or the URL prefix.

    `skew_seconds`/`nonce_ttl_seconds` default to `replay.py`'s own module
    constants (see that module's docstring for why 5 minutes / 30 minutes,
    and the invariant relating them) rather than being re-declared here --
    overridable purely as a testing seam (a test wanting a razor-thin skew
    window to exercise `TIMESTAMP_OUTSIDE_ALLOWED_WINDOW` without sleeping)
    ; production callers never pass either.

    `pairing_codes` is a `pairing.PairingCodeStore` instance (or `None`,
    the default) backing the `/pair` route below. `None` means pairing is
    NOT AVAILABLE on this blueprint instance at all -- `/pair` refuses
    every request it receives with `PAIRING_NOT_AVAILABLE` rather than
    ever touching a pairing code or the database, so an install that never
    turns pairing on never exposes the endpoint's actual behaviour (see
    that route's own docstring for why this matters: the route is
    otherwise unauthenticated by anything except the pairing code itself,
    so a caller must opt in explicitly by constructing and passing a
    store, never get one implicitly).

    `hub_identity_provider` is a zero-argument callable returning this hub's
    own identity dict (`join.hub_identity(...)`'s shape), or `None` (the
    default) -- backs `GET /identity` below. `None` means this hub has never
    activated and has no assertion to show yet: a normal, transient state
    (see that route's own docstring), not a misconfiguration, so it is not
    treated the same way `license_public_id_provider`/`trust_store_provider`
    missing is treated for `/join`.

    `license_public_id_provider`/`trust_store_provider` are zero-argument
    callables returning THIS hub's own licence's `license_public_id` and its
    `trust_store.OwnerTrustStore` instance respectively, or `None` (the
    default) for either -- back `POST /join` below, the automatic,
    licence-proven equivalent of `/pair` (`commercial_runtime/sync/
    site_relay/join.py`'s module docstring has the full design). With EITHER
    missing, `/join` refuses every request with `JOIN_UNAVAILABLE` before
    touching the request body or the database at all -- fail closed, exactly
    like `pairing_codes=None` already does for `/pair` above: an install
    that has not wired up automatic joining must not expose that endpoint's
    real behaviour to anything probing it.
    """
    bp = Blueprint(blueprint_name, __name__, url_prefix=url_prefix)

    @bp.route("/push", methods=["POST"])
    def push():
        body = request.get_json(silent=True)
        conn = get_conn()
        try:
            try:
                installation_id = site_auth.authenticate(
                    conn, body or {}, nonce_scope="sync_push",
                    skew_seconds=skew_seconds, nonce_ttl_seconds=nonce_ttl_seconds,
                )
            except site_auth.SiteAuthError as exc:
                return _error(exc.reason_code, 400)

            events = body.get("events") if isinstance(body, dict) else None
            if events is None or not isinstance(events, list) or len(events) > store.MAX_PUSH_BATCH:
                return _error("INVALID_BATCH", 400)

            # Validate the WHOLE batch BEFORE inserting ANY of it -- see the
            # module docstring's "ONE DELIBERATE DIVERGENCE FROM OWNER"
            # section. `store.append_events` itself validates-then-inserts
            # PER EVENT internally (raising on the first malformed one it
            # reaches, having already issued -- uncommitted -- INSERTs for
            # every valid event before it in the loop); calling it directly
            # on an unvalidated batch would make whether anything gets
            # inserted before the failure is discovered depend on WHERE in
            # the batch the bad event happens to sit, which is precisely the
            # "insert before validating" defect this file's test suite
            # mutation-proves against. Pre-validating the entire batch here,
            # with `store.append_events` never even called unless every
            # event already passed, is what makes the fail-closed guarantee
            # ("nothing from a rejected batch is ever written") independent
            # of event ORDER rather than an accident of how far the loop got
            # before raising.
            for index, raw_event in enumerate(events):
                try:
                    store.validate_event(raw_event)
                except store.InvalidEventError:
                    return _error("INVALID_EVENT", 400, event_index=index)

            try:
                stored = store.append_events(conn, events, origin_device_id=installation_id)
                conn.commit()
            except Exception:
                conn.rollback()
                # Never log the request body here -- it is untrusted
                # business data (product names, customer records, sale
                # totals), and Owner's own push()/pull() handlers make the
                # identical "request body not logged" promise for the
                # identical reason. The traceback alone is enough to debug a
                # genuine server-side fault; the body is not needed and
                # would be a real data-handling regression to include.
                logger.exception("Internal failure during site relay push (request body not logged).")
                return _error("INTERNAL_ERROR", 500)

            return jsonify({"stored": stored, "received": len(events)}), 200
        finally:
            conn.close()

    @bp.route("/pull", methods=["GET", "POST"])
    def pull():
        # GET with a signed JSON body, POST also accepted -- identical
        # acceptance and identical reasoning to `owner/app/sync/routes.py::
        # pull`: Android's own site-relay client (mirroring its existing
        # cloud-relay client) issues GET-with-body, while a frozen Windows
        # PyInstaller build of this codebase's OWN desktop client
        # (`SyncRelayClient.pull`, see that method's docstring) reproducibly
        # gets an EMPTY response body on GET-with-body specifically -- a
        # non-frozen interpreter hitting the identical route with the
        # identical request succeeds, isolating the defect to the frozen
        # build's networking stack, not to this route or the sync protocol
        # itself. POST was added there as a working alternative rather than
        # chasing the frozen-build defect itself; accepting both here (never
        # just one) is what lets EITHER client keep working against this hub
        # with no per-platform branching in this route.
        body = request.get_json(silent=True)
        conn = get_conn()
        try:
            try:
                installation_id = site_auth.authenticate(
                    conn, body or {}, nonce_scope="sync_pull",
                    skew_seconds=skew_seconds, nonce_ttl_seconds=nonce_ttl_seconds,
                )
            except site_auth.SiteAuthError as exc:
                return _error(exc.reason_code, 400)

            # `since` lives INSIDE the signed body -- deliberately never a
            # `?since=` query parameter, which `request.args` is never even
            # consulted for below. A free, unsigned query parameter would
            # let a captured, otherwise-legitimate pull request be replayed
            # (the nonce/timestamp on the SIGNED portion would still need to
            # be fresh for that specific replay window, but an attacker
            # controlling the URL of an intercepted-and-immediately-
            # forwarded request could still tack on a DIFFERENT `?since=`
            # before it reaches this hub) and walk more of this hub's event
            # history than the original signer actually asked for and
            # signed off on. Folding `since` into the signed body means any
            # tampering with it is caught by the signature check in
            # `authenticate()` above, exactly like every other field in the
            # request -- see `owner/app/sync/routes.py`'s module docstring
            # for the identical reasoning on the cloud side, and
            # `relay_client.py::pull`'s own comment on why the client sends
            # it this way in the first place.
            try:
                since = int(body["since"])
                if since < 0:
                    raise ValueError("since must be non-negative")
            except (KeyError, TypeError, ValueError):
                return _error("INVALID_SINCE", 400)

            try:
                rows = store.read_events_since(conn, since=since, exclude_device_id=installation_id)
                cursor = store.resolve_pull_cursor(conn, since=since, rows=rows)
                # Advanced even when `rows` is empty -- a device that pulls
                # and catches itself up to a `since` with nothing new to
                # receive still needs that fact recorded, or any future
                # site-log pruning built on `site_device_cursors` (mirroring
                # `owner/app/pruning.py`'s MIN-over-active-devices watermark)
                # can never consider events up to that point safe to delete.
                # See `store.advance_device_cursor`'s own docstring for the
                # never-regresses guard this relies on.
                store.advance_device_cursor(conn, installation_id, cursor)
                conn.commit()
            except Exception:
                conn.rollback()
                logger.exception("Internal failure during site relay pull.")
                return _error("INTERNAL_ERROR", 500)

            return jsonify({"events": rows, "cursor": cursor}), 200
        finally:
            conn.close()

    @bp.route("/pair", methods=["POST"])
    def pair():
        """Redeems a pairing code minted by `pairing.PairingCodeStore.issue`
        (design doc sec4: the operator taps "Connect a device" on the hub,
        which shows a QR encoding `pairing.pairing_payload(...)`; the
        joining device scans it and POSTs the result here) and, on success,
        adds/updates the device in `site_paired_devices` so it can
        immediately start signed push/pull traffic against this hub.

        TLS DOES NOT PROTECT THIS ENDPOINT, AND THAT IS THE ONE THING ABOUT
        IT THAT MUST BE UNDERSTOOD BEFORE ANYONE EDITS IT. Every other route
        in this blueprint authenticates the CALLER via an Ed25519 signature
        checked against a key this hub already has on file for them
        (`auth.authenticate`) -- but a device hitting THIS route is not
        paired yet, so it has no entry here to verify a signature against
        at all; requiring one would make first pairing impossible. The SPKI
        pin (`tls_identity.py`) authenticates the SERVER to the client, not
        the client to the server -- anyone who can route to this port can
        open a connection and POST to it, pin or no pin. The pairing code
        IS the authentication: short-lived, single-use, high-entropy, and
        only in existence while an operator is actively pairing a device
        (see `pairing.py`'s module docstring for why each of those
        properties holds). Rate limiting is absent here and inherits the
        cloud relay's documented deferred-gap status (design doc sec5,
        "Residual risks").

        Order of operations, each step gating the next:
          1. `pairing_codes` is `None` (pairing not configured on this
             blueprint instance at all) -> `PAIRING_NOT_AVAILABLE`, before
             touching the request body or the database at all -- an install
             that never enables pairing must not expose this endpoint's
             actual behaviour to anything probing it.
          2. Body shape: `pairing_code`, `installation_id`,
             `device_public_key` all present as non-empty strings (`label`
             optional, string or absent) -> else `INVALID_REQUEST`.
          3. `device_public_key` must be valid base64 decoding to EXACTLY
             32 bytes (a raw Ed25519 public key, matching the shape every
             `Device.public_key_b64` in this package's tests produces) ->
             else `INVALID_REQUEST`. Checked and rejected HERE, before the
             code is even consumed, deliberately: a malformed key accepted
             now would sit in `site_paired_devices` and surface only as a
             mystifying `INVALID_SIGNATURE` on that device's very first
             real push/pull attempt, with nothing at that point pointing
             back at pairing as the actual cause.
          4. `pairing_codes.consume(pairing_code)` -> propagates whichever
             `PairingError.reason_code` it raises. CONSUMED BEFORE ANYTHING
             IS WRITTEN, so a request that fails here (bad/expired/reused
             code) can never leave a half-paired device behind -- the code
             and the database write either both happen or neither does.
          5. `store.pair_device(conn, installation_id,
             device_public_key=..., label=...)`, then commit.

        RE-PAIRING AN installation_id THAT ALREADY EXISTS SUCCEEDS AND
        REPLACES THE STORED KEY, with no special-casing needed in this
        route at all: `store.pair_device` is already an upsert
        (`ON CONFLICT(installation_id) DO UPDATE SET device_public_key =
        excluded.device_public_key, ...`), so calling it again for a known
        installation_id simply overwrites the old key rather than raising.
        This is correct, not merely tolerated -- a device that was wiped
        and re-activated re-pairs with a brand-new key, which is normal,
        not an attack; it still costs a fresh, operator-issued pairing code
        to get there, so nothing about this route makes re-pairing any
        easier to trigger than pairing a device for the first time.

        RE-PAIRING A REVOKED installation_id ALSO CLEARS THE REVOKE, again
        with no special-casing needed here: `store.pair_device`'s own
        upsert unconditionally sets `revoked_at = NULL`. This is the
        deliberate, intended behaviour, not a gap -- an operator physically
        issuing a fresh pairing code and walking a device through pairing
        again is an explicit, in-person act of re-authorization (see
        `store.pair_device`'s own docstring: "an operator who deliberately
        re-pairs a device that had been revoked is deliberately
        re-authorizing it").
        """
        if pairing_codes is None:
            return _error("PAIRING_NOT_AVAILABLE", 400)

        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _error("INVALID_REQUEST", 400)

        pairing_code = body.get("pairing_code")
        installation_id = body.get("installation_id")
        device_public_key = body.get("device_public_key")
        label = body.get("label")

        for value in (pairing_code, installation_id, device_public_key):
            if not isinstance(value, str) or not value:
                return _error("INVALID_REQUEST", 400)
        if label is not None and not isinstance(label, str):
            return _error("INVALID_REQUEST", 400)

        # Step 3: a raw Ed25519 public key is always exactly 32 bytes --
        # reject anything else (wrong length, or not valid base64 at all)
        # before the pairing code is spent on a request that could never
        # have produced a usable device.
        try:
            raw_public_key = base64.b64decode(device_public_key, validate=True)
        except (TypeError, ValueError, binascii.Error):
            return _error("INVALID_REQUEST", 400)
        if len(raw_public_key) != 32:
            return _error("INVALID_REQUEST", 400)

        # Step 4: consume BEFORE writing anything -- see the route
        # docstring's "CONSUMED BEFORE ANYTHING IS WRITTEN" paragraph.
        try:
            pairing_codes.consume(pairing_code)
        except site_pairing.PairingError as exc:
            return _error(exc.reason_code, 400)

        conn = get_conn()
        try:
            store.pair_device(
                conn, installation_id, device_public_key=device_public_key, label=label,
            )
            conn.commit()
        finally:
            conn.close()

        return jsonify({"paired": True, "installation_id": installation_id}), 200

    @bp.route("/identity", methods=["GET"])
    def identity():
        """UNAUTHENTICATED, DELIBERATELY -- this is the bootstrap step of
        the automatic-join flow (`join.py`'s module docstring), and the
        caller has nothing to authenticate itself with yet: a device that
        just discovered this hub on the LAN (`beacon.py`, a separate
        concern) has no shared secret, no pairing, nothing. It calls this
        route FIRST, purely to learn who this hub claims to be, before it
        ever considers calling `POST /join`.

        THIS IS SAFE BECAUSE EVERYTHING RETURNED HERE IS ALREADY PUBLIC.
        `installation_id` is not a secret (Owner already knows it and
        assigned it). `device_public_key` is, by definition, a PUBLIC key.
        And the `assertion` envelope is an Owner-signed document whose own
        payload is worthless to anyone who does not also hold the PRIVATE
        key it names (see `join.py`'s "THE DEVICE-KEY-FINGERPRINT CHECK"
        section) -- exposing it here grants a caller no capability it did
        not already have simply by being on the same LAN as a device that
        already holds one. This route GRANTS NOTHING; it lets a device
        being asked to trust this hub decide whether to, exactly the same
        way `POST /join` (below) lets this hub decide whether to trust the
        device asking to join it.

        `hub_identity_provider() is None` means this hub has never
        activated and has no assertion to show at all -- a normal,
        transient state (a fresh install before its first activation), not
        an error condition, hence `IDENTITY_UNAVAILABLE` rather than
        something implying misconfiguration.
        """
        if hub_identity_provider is None:
            return _error("IDENTITY_UNAVAILABLE", 400)
        return jsonify(hub_identity_provider()), 200

    @bp.route("/join", methods=["POST"])
    def join():
        """The automatic equivalent of `/pair` above: mutual licence proof
        (`join.verify_membership`) instead of an operator-issued code. See
        `commercial_runtime/sync/site_relay/join.py`'s module docstring for
        the full mechanism and why each check is load-bearing.

        Order of operations, each step gating the next -- mirrors `/pair`'s
        own documented shape immediately above, with one deliberate
        addition (step 4) that `/pair` has no equivalent of at all:

          1. `license_public_id_provider`/`trust_store_provider` -- if
             EITHER is `None` (not wired up on this blueprint instance at
             all) -> `JOIN_UNAVAILABLE`, before touching the request body or
             the database. Fail closed: an install that never configured
             automatic joining must never pair a device this way, no matter
             what it presents.
          2. Body shape: `installation_id`/`device_public_key` non-empty
             strings, `assertion` an object, optional `label` a string or
             absent -> else `INVALID_REQUEST`.
          3. `device_public_key` must be valid base64 decoding to EXACTLY 32
             bytes (a raw Ed25519 public key) -> else `INVALID_REQUEST`,
             checked here BEFORE any verification is attempted, the same
             reasoning `/pair`'s own step 3 gives for checking this early.
          4. `join.verify_membership(...)` against THIS HUB'S OWN
             `license_public_id_provider()` -> propagates whichever
             `JoinError.reason_code` it raises (`UNKNOWN_SIGNING_KEY`,
             `LICENSE_MISMATCH`, `ASSERTION_INSTALLATION_MISMATCH`,
             `ASSERTION_DEVICE_MISMATCH`, `ASSERTION_EXPIRED`, etc. -- see
             `join.py`'s own docstring for the full, numbered check
             sequence this single call enforces).
          5. ONLY NOW -- after membership on the correct licence has
             actually been PROVEN -- check whether THIS installation_id was
             previously revoked LOCALLY at this hub
             (`store.lookup_paired_device(...)["revoked_at"]`). THIS IS THE
             ONE PLACE THIS ROUTE DELIBERATELY DIFFERS FROM `/pair`, AND THE
             REASON IS THE WHOLE POINT OF THIS STEP EXISTING: a `/pair`
             re-join requires an OPERATOR to issue a fresh code, and that
             act of issuing a fresh code IS the operator's own, in-person
             consent to re-authorize a previously-revoked device (see
             `/pair`'s own docstring, "RE-PAIRING A REVOKED installation_id
             ALSO CLEARS THE REVOKE"). `/join` has NO operator in the loop
             at all -- it runs automatically, the moment two devices on a
             LAN discover each other and share a licence. Without this
             check, a device the shop owner deliberately revoked at the
             till (design's own "fired employee's tablet" scenario, `auth.
             py`'s module docstring) would silently re-admit ITSELF on its
             very next automatic discovery sweep, because it still holds a
             perfectly valid, same-licence assertion -- undoing the exact
             local-revoke control this package was built to provide, with
             no human ever asked and no way for an operator to stop it
             short of physically taking the device off the network.
             Refused with `INSTALLATION_REVOKED` (the SAME locally-minted
             reason code `auth.py`'s step 7 already uses for the identical
             situation on push/pull, not a new one -- see that module's own
             docstring for why it is not one of Owner's public codes).
          6. `store.pair_device(conn, installation_id, device_public_key=...,
             label=...)`, then commit -- EXACTLY what `/pair` does on
             success, including the identical upsert-and-clear-revoke
             semantics for an installation_id that was already paired (see
             `store.pair_device`'s own docstring): a device re-joining after
             a wipe-and-reactivate, or simply re-discovered after a network
             blip, re-joins cleanly with its current key.

        Checked and rejected in this order so a request that fails at any
        step can never leave a half-joined device behind, and so an
        install that has not opted into automatic joining never reveals
        this endpoint's real behaviour to anything probing it -- the exact
        same reasoning `/pair`'s own docstring gives for its own ordering.
        """
        if license_public_id_provider is None or trust_store_provider is None:
            return _error("JOIN_UNAVAILABLE", 400)

        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return _error("INVALID_REQUEST", 400)

        installation_id = body.get("installation_id")
        device_public_key = body.get("device_public_key")
        assertion = body.get("assertion")
        label = body.get("label")

        for value in (installation_id, device_public_key):
            if not isinstance(value, str) or not value:
                return _error("INVALID_REQUEST", 400)
        if not isinstance(assertion, dict):
            return _error("INVALID_REQUEST", 400)
        if label is not None and not isinstance(label, str):
            return _error("INVALID_REQUEST", 400)

        # Step 3: a raw Ed25519 public key is always exactly 32 bytes --
        # reject anything else before verify_membership is even attempted,
        # mirroring /pair's own identical, identically-reasoned check.
        try:
            raw_public_key = base64.b64decode(device_public_key, validate=True)
        except (TypeError, ValueError, binascii.Error):
            return _error("INVALID_REQUEST", 400)
        if len(raw_public_key) != 32:
            return _error("INVALID_REQUEST", 400)

        # Step 4: prove membership on THIS hub's own licence. `trusted_now`
        # is resolved HERE, once, at the top of this check -- see join.py's
        # module docstring for why this is the same idiom verify_assertion's
        # own real production callers already use (activation.py,
        # checkin_scheduler.py: trusted_now=datetime.now(timezone.utc)
        # resolved at the call site, never reached for internally).
        try:
            # Return value intentionally discarded beyond the verification
            # itself -- this route's only job on success is to admit the
            # device; nothing the verified payload carries (entitlements,
            # license_status, ...) is this hub's business to inspect or
            # persist.
            site_join.verify_membership(
                assertion,
                trust_store=trust_store_provider(),
                expected_license_public_id=license_public_id_provider(),
                claimed_installation_id=installation_id,
                claimed_device_public_key=device_public_key,
                trusted_now=datetime.now(timezone.utc),
            )
        except site_join.JoinError as exc:
            return _error(exc.reason_code, 400)

        conn = get_conn()
        try:
            # Step 5: THE DELIBERATE ASYMMETRY WITH /pair -- see this
            # route's own docstring, step 5, for the full reasoning. Checked
            # only now, AFTER membership has already been cryptographically
            # proven, mirroring auth.py's own "prove identity before
            # checking local status" ordering (that module's step 6 before
            # its step 7) rather than turning this into an unauthenticated
            # oracle over which installation_ids have ever been paired here.
            existing = store.lookup_paired_device(conn, installation_id)
            if existing is not None and existing["revoked_at"] is not None:
                return _error("INSTALLATION_REVOKED", 400)

            # Step 6: identical to /pair's own final step, including the
            # identical upsert/clear-revoke semantics -- see store.pair_
            # device's own docstring.
            store.pair_device(
                conn, installation_id, device_public_key=device_public_key, label=label,
            )
            conn.commit()
        finally:
            conn.close()

        return jsonify({"joined": True, "installation_id": installation_id}), 200

    return bp
