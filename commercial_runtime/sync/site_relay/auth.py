"""Authentication for the LAN site relay's push/pull routes (retail schema
v30, `docs/launch-readiness/lan-restaurant-design.md` sec3/sec5).

Port of `owner/app/sync/routes.py::_authenticate` onto this hub's own local
tables: Owner authenticates a device against its Postgres `Installation` +
`device_identity` roster; this module authenticates a device against
`site_paired_devices` (`commercial_runtime/sync/site_relay/store.py`'s
`lookup_paired_device`) -- the SAME verify-then-resolve shape, the SAME
ordering of operations, just a different roster. `replay.py` (this package's
sibling module) supplies the timestamp/nonce half; this module supplies the
signature/pairing half and glues both together into one `authenticate()`
entry point the push/pull route handlers call once each.

THE ORDER OF OPERATIONS IS THE WHOLE POINT, copied byte-for-byte in SEQUENCE
from `_authenticate` (not merely in spirit -- see that function's own
docstring, which itself cites `owner/app/licensing_service/checkin.py` as
the original source of this exact ordering):

  1. validate the required signed fields are present and non-empty
     (installation_id, timestamp, nonce, signature) -- INVALID_REQUEST.
  2. parse the timestamp -- INVALID_TIMESTAMP on a shape failure.
  3. validate timestamp freshness against the accepted skew window --
     propagates whatever reason code `replay.validate_timestamp` raises
     (INVALID_TIMESTAMP for a naive/non-tz-aware value, or
     TIMESTAMP_OUTSIDE_ALLOWED_WINDOW for a stale one).
  4. atomically BURN the nonce -- propagates NONCE_REUSED (or whatever else
     `replay.consume_nonce` raises). Done BEFORE the paired device is even
     looked up.
  5. look up the paired device by installation_id -- INSTALLATION_NOT_FOUND
     if this hub has never paired that installation_id at all.
  6. verify the Ed25519 signature over the canonicalized body (minus
     `signature` itself) against the PAIRED device's own stored public key
     -- INVALID_SIGNATURE on any mismatch, bad base64, or missing key.
  7. ONLY NOW check whether the device has been locally revoked --
     INSTALLATION_REVOKED if so.

Two of these orderings look like mistakes to a reader seeing this file cold,
and both are deliberate. Getting either one backwards silently reopens a
real security gap rather than merely failing a style check, so both are
spelled out here rather than left to be re-derived (or "fixed") later:

  * THE NONCE IS BURNED BEFORE THE SIGNATURE IS EVER CHECKED (step 4 before
    step 6). This looks backwards -- why spend the nonce store's one shot on
    a request you have not yet proven came from a legitimate key? Because
    burning AFTER verification would let an attacker replay a genuinely
    captured, validly-signed request an UNLIMITED number of times as long as
    each replay attempt happened to fail some LATER, unrelated check (a
    device that gets revoked moments after the original request, for
    instance) -- the nonce would still be sitting there, unburned, ready to
    be replayed again the next time that later check happens to pass. It
    would also turn the nonce store into a signature oracle: an attacker
    could probe whether a given (installation_id, timestamp, nonce) tuple is
    "fresh" without ever needing to hold the device's private key, just by
    watching whether the nonce gets burned. Burning FIRST means one captured
    request is spent exactly once, full stop, regardless of what happens to
    it afterwards -- this is exactly `owner/app/sync/routes.py::_authenticate`
    's own ordering, inherited from `checkin.py` before it.

  * THE REVOKE CHECK COMES AFTER SIGNATURE VERIFICATION, NOT BEFORE (step 7
    after step 6), mirroring Owner's own post-signature status gate
    (`_authenticate`'s comment on `installation.status`). Checking it
    earlier -- say, right after the device lookup at step 5 -- would turn
    this endpoint into an unauthenticated oracle: literally anyone on the
    shop's wifi, holding no key at all, could send a bare installation_id
    and learn from the response alone whether that installation_id has ever
    been paired, and separately whether it has been revoked, without ever
    proving they control the corresponding private key. Deferring the
    revoke check to AFTER a real signature has been verified costs nothing
    -- a genuine device that gets revoked is refused exactly one request
    later than it would be checked first, and `lan-restaurant-design.md`
    sec5's whole point for this column ("Local mitigation for the local
    threat": a fired employee's tablet, revoked immediately from this hub's
    own Settings, no connectivity required) is about IMMEDIATE remedy for a
    revoked device's OWN next request, not about hiding whether a device
    happens to be paired from an anonymous prober. Immediate, not
    unauthenticated.

REASON CODE FOR A LOCAL REVOKE: `INSTALLATION_REVOKED` is a NEW code, not
one of Owner's public reason codes -- checked against
`commercial_runtime/licensing_contracts/reason_codes.py` before minting it.
`DEVICE_KEY_REVOKED` is the closest existing entry there, but it names a
DIFFERENT situation: on the cloud side it means "this installation's device
key was rotated/reset and no ACTIVE key exists to verify against at all"
(`owner/app/sync/routes.py::_authenticate`'s `device_identity.
get_active_device_key(...) is None` branch) -- the signature check itself
cannot even be attempted. Here, the signature check DID succeed (the
requesting device still holds and correctly uses its own registered key);
what is being refused is that `site_paired_devices.revoked_at` has been
stamped by this hub's own operator, independently of anything Owner's
roster says (see `store.py::revoke_device`'s docstring: "the hub's own
Settings can revoke a paired device immediately... with no connectivity
required at all"). Reusing DEVICE_KEY_REVOKED for this would conflate "your
key is no longer registered" with "your key is fine but THIS hub kicked you
off locally" -- two situations a client-side caller may reasonably want to
react to differently (the former suggests re-pairing with a NEW key; the
latter means asking the shop owner to re-authorize the SAME device). None
of `INSTALLATION_SUSPENDED` / `INSTALLATION_DEACTIVATED` / `INSTALLATION_
REPLACED` fit either -- those are Owner's cloud-lifecycle states for an
Installation record that does not exist in this hub's purely-local pairing
table at all. So `INSTALLATION_REVOKED` is minted here, deliberately
distinct from all of the above, and is not part of `PUBLIC_REASON_CODES` or
`LOCAL_REASON_CODES` in `licensing_contracts/reason_codes.py` (that
module's own docstring scopes it to codes a PRODUCT can receive FROM
OWNER, or codes a licensing CLIENT can produce -- this is neither; it is
produced by, and only meaningful to, this hub's own LAN-facing relay).
"""
from __future__ import annotations

import base64
import binascii
import sqlite3

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.device_identity import verify_signature
from commercial_runtime.sync.site_relay import replay, store

# Same four fields Owner's `_REQUIRED_AUTH_FIELDS` requires -- see this
# module's docstring, step 1. Every push/pull request must carry all four,
# regardless of what else it carries (`events` for push, `since` for pull).
REQUIRED_AUTH_FIELDS = ("installation_id", "timestamp", "nonce", "signature")


class SiteAuthError(Exception):
    """Raised by `authenticate`; every route handler catches this and
    returns `{"reason_code": exc.reason_code}` with HTTP 400 -- matching
    Owner's `SyncAuthError` / `checkin.py`'s `CheckInRejected`, both of
    which use 400 (never 401/403) for every rejection reason, signature
    mismatches included. See `owner/app/sync/routes.py`'s own `SyncAuthError`
    docstring for the citation that confirms this against Owner's own test
    suite."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def authenticate(
    conn: sqlite3.Connection,
    body: dict,
    *,
    nonce_scope: str,
    skew_seconds: int = replay.DEFAULT_SKEW_SECONDS,
    nonce_ttl_seconds: int = replay.DEFAULT_NONCE_TTL_SECONDS,
    now=None,
) -> str:
    """Verify-then-resolve a push/pull request body against this hub's own
    paired-device roster. Returns the VERIFIED installation_id on success;
    raises `SiteAuthError` on any failure, at whichever step first rejects
    the request (see the module docstring for the full, deliberately-ordered
    sequence and why steps 4 and 7 are where they are, not where they might
    look like they should be).

    `nonce_scope` distinguishes push's nonces from pull's (mirrors Owner's
    own `"sync_push"` / `"sync_pull"` scoping in `_authenticate`'s callers)
    so a nonce burned verifying a push can never also burn -- and therefore
    block -- the same nonce VALUE arriving on a pull, or vice versa.

    `conn` must already have `row_factory` set to `sqlite3.Row` -- this
    function reads `store.lookup_paired_device`'s dict result by key, and
    `store.py`'s own module docstring states plainly that setting the row
    factory is the CALLER's job, never something a DAO/auth module
    reconfigures on a connection it did not open. `commercial_runtime/sync/
    internal_routes.py`'s `make_sync_internal_blueprint` makes the identical
    assumption about the `get_conn` it is handed, for the identical reason.

    `now` is the same injectable testing seam `replay.validate_timestamp`
    and `replay.consume_nonce` already expose (defaults to real UTC `now`;
    production callers never pass it) -- threaded through here rather than
    re-derived, so a caller that wants deterministic freshness/nonce-expiry
    behaviour in a test has exactly one place to inject a fixed clock.
    """
    # ── Step 1: shape -- every required field present and non-empty ────────
    if not isinstance(body, dict):
        raise SiteAuthError("INVALID_REQUEST")
    for field in REQUIRED_AUTH_FIELDS:
        if not body.get(field):
            raise SiteAuthError("INVALID_REQUEST")

    installation_id = str(body["installation_id"])

    # ── Step 2: parse the timestamp ─────────────────────────────────────────
    try:
        request_timestamp = replay.parse_request_timestamp(body["timestamp"])
    except (ValueError, TypeError, AttributeError):
        raise SiteAuthError("INVALID_TIMESTAMP")

    # ── Step 3: freshness -- propagate replay.py's own reason code verbatim ─
    try:
        replay.validate_timestamp(request_timestamp, skew_seconds, now=now)
    except replay.ReplayError as exc:
        raise SiteAuthError(exc.reason_code)

    # ── Step 4: burn the nonce BEFORE anything else is checked -- see the
    # module docstring's "THE NONCE IS BURNED BEFORE THE SIGNATURE IS EVER
    # CHECKED" section for why this is not a bug. `consume_nonce` commits its
    # own burn transaction independently (see its own docstring) -- that
    # commit survives even if a LATER step in this function raises.
    try:
        replay.consume_nonce(
            conn, str(body["nonce"]), scope=nonce_scope, ttl_seconds=nonce_ttl_seconds, now=now
        )
    except replay.ReplayError as exc:
        raise SiteAuthError(exc.reason_code)

    # ── Step 5: resolve the paired device -- never trust client-claimed
    # identity past this point without a lookup against this hub's own
    # roster. `license_id`-style "never from client input" reasoning does
    # not apply here (this hub is a single-licence install, see store.py's
    # own docstring), but the same "resolve from the VERIFIED record, never
    # from body input" discipline still governs everything below.
    device = store.lookup_paired_device(conn, installation_id)
    if device is None:
        raise SiteAuthError("INSTALLATION_NOT_FOUND")

    # ── Step 6: verify the Ed25519 signature over the canonicalized body
    # (minus `signature` itself) against the PAIRED device's OWN stored
    # public key -- never against a key the request itself supplies, which
    # would make the whole check circular (a forged request could carry a
    # forged key alongside a signature that matches only that forged key).
    signable = {k: v for k, v in body.items() if k != "signature"}
    canonical_bytes = canonicalize_bytes(signable)
    try:
        signature_bytes = base64.b64decode(body["signature"], validate=True)
        raw_public_key = base64.b64decode(device["device_public_key"], validate=True)
    except (TypeError, ValueError, binascii.Error):
        # Malformed base64 on either side (a corrupted signature, or -- in
        # principle -- a corrupted stored key) can never verify; treat it as
        # exactly what it is to the caller: a signature that does not check
        # out. Never a 500 -- this is untrusted client input, not a server
        # fault.
        raise SiteAuthError("INVALID_SIGNATURE")
    if not verify_signature(raw_public_key, canonical_bytes, signature_bytes):
        raise SiteAuthError("INVALID_SIGNATURE")

    # ── Step 7: ONLY NOW check the local revoke flag -- see the module
    # docstring's "THE REVOKE CHECK COMES AFTER SIGNATURE VERIFICATION"
    # section for why checking this any earlier would make the endpoint an
    # unauthenticated oracle over which installation_ids have been paired
    # and/or revoked.
    if device["revoked_at"] is not None:
        raise SiteAuthError("INSTALLATION_REVOKED")

    return installation_id
