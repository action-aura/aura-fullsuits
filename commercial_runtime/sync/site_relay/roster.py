"""The Owner-signed site roster: this hub's only source of NEGATIVE
authorization for LAN devices (retail schema v30, `docs/launch-readiness/
lan-restaurant-design.md` sec5, "Layer 3 -- authorization: the Owner-signed
site roster").

THE GAP THIS CLOSES: `commercial_runtime/sync/site_relay/auth.py` today
authorizes a device purely from this hub's own LOCAL `site_paired_devices`
table (`store.lookup_paired_device`). Pairing is a real, operator-witnessed
act -- but it is a HUB-LOCAL fact. Owner's own installation registry (where
"this licence suspended installation X" actually lives) never reaches this
hub at all, so a device Owner suspends keeps syncing on the LAN indefinitely.
This module gives the hub a cached, cryptographically-verified copy of
Owner's verdict on every installation on this licence, refreshed whenever
the hub is online, and lets `auth.py` consult it as a SECOND, independent
gate -- see that module's own docstring for exactly how and where it is
enforced, and why the enforcement rule deliberately narrows design sec5's
own wording.

THE ROSTER PAYLOAD SHAPE (the wire contract this module verifies and stores,
and the contract documented for Owner's side in this file's closing section):

    {
      "license_id": "<uuid>",
      "issued_at": "<iso8601 tz-aware>",
      "devices": [
        {"installation_id": "<uuid>", "device_public_key": "<base64>",
         "platform": "WINDOWS|ANDROID", "status": "ACTIVE|SUSPENDED|DEACTIVATED|REPLACED"}
      ]
    }

...delivered inside the EXACT same signed-envelope shape the licence
assertion already uses (`commercial_runtime/licensing_contracts/
assertion_verifier.py::verify_assertion`): `{"payload": {...above...},
"signing_key_id": "...", "algorithm": "ed25519", "signature": "<base64>"}`.
`verify_roster_envelope` below is a deliberate PORT of `verify_assertion`'s
own verify-then-resolve order onto this different payload shape -- same
steps, same sequence, same "never read a field before the signature that
protects it has been checked" discipline -- not a reinvention of it:

    1. envelope shape: `payload` / `signing_key_id` / `algorithm` /
       `signature` all present -- else `ROSTER_VERIFICATION_FAILED`.
    2. `algorithm` must be `"ed25519"` -- else `ROSTER_VERIFICATION_FAILED`
       (this package, like `assertion_verifier.py`, supports exactly one
       signing algorithm; there is no negotiation to perform).
    3. `trust_store.is_trusted(signing_key_id)` -- else `UNKNOWN_SIGNING_KEY`
       (reused verbatim from `licensing_contracts/reason_codes.py`'s
       `LOCAL_REASON_CODES`; the identical meaning applies here: this
       envelope claims to be signed by a key this installation has never
       been told to trust).
    4. canonicalize the PAYLOAD (`canonicalize_bytes`, the same
       canonicalization every other Owner-signed structure in this codebase
       uses) and verify the Ed25519 signature over it against the trusted
       key's public key -- else `ROSTER_VERIFICATION_FAILED`.
    5. ONLY NOW -- after the signature has verified -- read `license_id`,
       `issued_at`, and `devices` out of the payload and validate their
       shape. Reading any of these fields BEFORE step 4 succeeded would mean
       trusting attacker-controlled bytes before they have been proven to
       come from Owner at all; this is the exact discipline
       `verify_assertion`'s own docstring states ("never returns a
       partially-trusted result") and this function makes the same promise.

Devices are validated structurally (each entry is a dict carrying at least a
non-empty `installation_id` and a non-empty `status`), not against a fixed
enum of statuses -- `DENIED_ROSTER_STATUSES` below is the only place a
status string is ever compared for MEANING, and it is deliberately narrow
(see `auth.py`'s docstring for why absence from that set, including an
unrecognized status string, always means "not denied" rather than "assume
the worst").

REPLAY PROTECTION IS THE SUBTLE PART OF THIS FILE, so it gets its own
paragraph rather than a one-line comment. A roster is a SIGNED document with
no nonce of its own (it is not a single request/response like a push/pull
call -- it is a cached artifact, fetched during a check-in and re-used for
every LAN request afterwards until the next fetch). That means a validly
signed OLD roster never expires as far as signature verification is
concerned: an attacker who once captured a genuine roster from BEFORE a
device was suspended can replay that exact, still-validly-signed envelope to
this hub at any later time, and `verify_roster_envelope` alone would accept
it -- the signature is real, the key is trusted, nothing about the bytes
changed. The only defense available is comparing `issued_at` against
whatever roster is already cached and refusing to move backwards in time:
`store_roster` rejects any envelope whose `issued_at` is OLDER than the
currently-stored roster's `issued_at`, unconditionally, regardless of how
valid its signature is. This is why `store_roster` re-verifies internally
rather than trusting a caller's prior verification (see that function's own
docstring for the full reasoning) -- the replay guard and the signature
verification are two halves of one gate, and splitting them across a
caller/callee boundary would let a future caller satisfy one half without
the other.

THE OWNER ENDPOINT THIS NEEDS (not built here)
------------------------------------------------
Everything above is the HUB side: verify, cache, query. Nothing in this
repository yet ISSUES a roster -- that is Owner-side work, out of scope for
this task and this file set (see the task's own instruction not to touch
`owner/`), and is written down here, loudly, as the handover a collaborator
picks up next:

    * ROUTE SHAPE: a new Owner endpoint, e.g.
      `GET /api/licensing/site-roster?license_id=<uuid>`, returning exactly
      the envelope shape documented above -- `{"payload": {"license_id",
      "issued_at", "devices": [...]}, "signing_key_id", "algorithm":
      "ed25519", "signature"}` -- for the licence the caller's installation
      belongs to. `devices` must list EVERY installation currently on that
      licence, not just non-ACTIVE ones -- `auth.py`'s enforcement rule
      never denies by absence (see that module's docstring), so an
      incomplete list would silently under-report suspensions, never
      over-report them, but a complete list is still the honest contract to
      publish.
    * AUTHENTICATION: this endpoint must be authenticated the SAME WAY
      check-in already is -- a device-signed request (installation_id,
      timestamp, nonce, signature verified against that installation's own
      registered device key), never a bare `license_id` query parameter
      trusted on its own. A roster is exactly the kind of document an
      unauthenticated caller must not be able to fetch for an arbitrary
      licence: it lists every installation on that licence and its current
      status, which is itself licence-scoped operational information.
    * SIGNING KEY: the envelope must be signed with OWNER'S EXISTING
      assertion-signing key -- the same key (and the same rotation/
      continuity machinery, `OwnerTrustStore.admit_manifest`) the licence
      assertion itself uses, verified here against the SAME bundled trust
      anchor every install already carries (`trust_anchor_loader.py`). Do
      not mint a second, roster-specific signing key: this hub verifies
      rosters with the identical `OwnerTrustStore` instance it already
      maintains for assertions, and a second key would mean a second
      rotation/revocation lifecycle to build, ship, and keep in sync for no
      benefit -- the roster and the licence assertion already share exactly
      one issuer (Owner) and exactly one trust relationship (this
      installation trusts Owner's signing keys).
    * REGENERATION: the roster must be regenerated (a fresh `issued_at`, a
      fresh signature) on EVERY installation-status transition for that
      licence -- suspend, deactivate, replace, or reactivate -- not merely
      on a fixed schedule. `lan-restaurant-design.md` sec5's own "Staleness"
      paragraph already accepts that a hub's CACHE can lag until its next
      online refresh; that is a different, already-accepted latency from
      Owner failing to have produced an up-to-date roster to serve in the
      first place the moment the hub does ask.
    * THE PRUNING WARNING THAT MUST SHIP WITH IT (design sec6, item 2): a
      hub that relays LAN devices' events upstream is, from the CLOUD
      relay's point of view, one installation whose own `SyncDeviceCursor`
      advances while every LAN-attached device behind it never talks to the
      cloud relay directly at all -- their cloud-side cursors go stale
      forever. `owner/app/pruning.py`'s watermark is a MIN over every
      active device's cursor, so those permanently-stale LAN-device cursors
      would freeze cloud-side pruning for that licence FOREVER, not merely
      slow it down. Whoever builds the roster endpoint must ship the
      roster-aware pruning fix alongside it -- either exclude
      roster-listed, LAN-attached installations from the pruning MIN
      entirely, or let the hub's own cursor stand in for its whole flock --
      because shipping the roster endpoint WITHOUT this fix would be net
      negative: it would newly enable LAN operation (which is what makes an
      installation's cursor go stale in the first place) while leaving the
      cloud-side consequence of that unhandled. This is called out in
      design sec6 as "small but... not optional"; repeating it here so it is
      not merely a sentence in a design doc no one revisits once the hub
      side ships and looks, from this side of the fence, complete.
"""
from __future__ import annotations

import base64
import json
import sqlite3
from datetime import datetime
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from commercial_runtime.licensing_contracts.canonical import CanonicalizationError, canonicalize_bytes
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

# The three statuses that DENY LAN access when this hub's cached roster
# explicitly lists a device under one of them. "ACTIVE" and any status
# string this set does not name (including one this module has never heard
# of) are both NOT denied -- see `auth.py`'s docstring for why an unknown or
# absent status must never be treated as a denial. This set is exported (not
# a private module constant) specifically so `auth.py`'s enforcement gate
# imports and checks against THIS set, rather than re-declaring its own
# copy that could silently drift from what this module actually verifies
# and stores.
DENIED_ROSTER_STATUSES = frozenset({"SUSPENDED", "DEACTIVATED", "REPLACED"})


class RosterError(Exception):
    """Raised by `verify_roster_envelope` and, by extension, `store_roster`
    (which calls it internally -- see that function's own docstring). Every
    raise site names a `reason_code`, matching the shape of
    `AssertionVerificationError` (`licensing_contracts/assertion_verifier.py`)
    and `SiteAuthError` (`auth.py`) -- this package's established convention
    for "an error a caller needs to branch on by code, not just catch"."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def _require_aware_datetime(raw, *, field_name: str) -> datetime:
    """Parses `raw` as an ISO-8601 datetime and requires it to be
    timezone-aware, raising `RosterError("ROSTER_VERIFICATION_FAILED", ...)`
    otherwise. Mirrors `assertion_verifier.py`'s identical requirement on
    `not_before`/`expires_at`: a naive datetime cannot be safely compared
    against another clock without silently assuming a timezone, and this
    module compares `issued_at` values against each other for the replay
    guard below -- an ambiguous naive comparison there is exactly the kind
    of bug that would only surface as a roster silently failing to update,
    or worse, silently accepting a stale one."""
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError) as exc:
        raise RosterError(
            "ROSTER_VERIFICATION_FAILED", f"Roster {field_name} is not a valid ISO-8601 datetime: {exc}"
        ) from exc
    if parsed.tzinfo is None:
        raise RosterError("ROSTER_VERIFICATION_FAILED", f"Roster {field_name} must be timezone-aware.")
    return parsed


def _validate_devices(devices) -> None:
    """Validates `devices` is a list of dicts, each carrying at minimum a
    non-empty `installation_id` and a non-empty `status` -- the two fields
    `roster_device_status`/`auth.py`'s enforcement gate actually depend on.
    `device_public_key`/`platform` are part of the documented payload shape
    (and useful for a future roster-driven pairing check) but are NOT
    validated here: this module has exactly one consumer of `devices`
    today (the status lookup), and requiring fields no code here reads yet
    would reject a structurally-fine roster for a reason nothing downstream
    actually cares about."""
    if not isinstance(devices, list):
        raise RosterError("ROSTER_VERIFICATION_FAILED", "Roster payload's 'devices' must be a list.")
    for entry in devices:
        if not isinstance(entry, dict):
            raise RosterError("ROSTER_VERIFICATION_FAILED", "Each roster device entry must be an object.")
        installation_id = entry.get("installation_id")
        status = entry.get("status")
        if not isinstance(installation_id, str) or not installation_id:
            raise RosterError(
                "ROSTER_VERIFICATION_FAILED",
                "Each roster device entry must carry a non-empty 'installation_id'.",
            )
        if not isinstance(status, str) or not status:
            raise RosterError(
                "ROSTER_VERIFICATION_FAILED", "Each roster device entry must carry a non-empty 'status'."
            )


def verify_roster_envelope(envelope: dict, *, trust_store: OwnerTrustStore, expected_license_id: Optional[str] = None) -> dict:
    """Independently verifies a roster envelope and returns its PAYLOAD dict
    on success. Raises `RosterError` with a specific `reason_code` on any
    failure -- never returns a partially-trusted result (identical promise
    to `verify_assertion`'s own docstring).

    Mirrors `licensing_contracts/assertion_verifier.py::verify_assertion`'s
    verify-then-resolve order exactly (see this module's own docstring for
    the full numbered sequence): shape -> algorithm -> trusted key ->
    signature -> ONLY THEN read/validate the payload's own fields
    (`license_id`, `issued_at`, `devices`). Nothing below reads a payload
    field for any purpose -- comparison, storage, or return -- before the
    signature check has already succeeded.

    `expected_license_id`, when given, is checked against the verified
    payload's `license_id` -- a defensive cross-check for a caller that
    already knows which licence it belongs to (this hub is a single-licence
    install, per `store.py`'s own module docstring, so a production caller
    normally has this value on hand). `None` (the default) skips the check
    entirely, for a caller (such as a test, or a first-ever fetch before the
    licence id is otherwise known) that has no independent value to compare
    against.
    """
    try:
        payload = envelope["payload"]
        signing_key_id = envelope["signing_key_id"]
        algorithm = envelope["algorithm"]
        signature_b64 = envelope["signature"]
    except (KeyError, TypeError) as exc:
        raise RosterError("ROSTER_VERIFICATION_FAILED", f"Malformed roster envelope: {exc}") from exc

    if algorithm != "ed25519":
        raise RosterError("ROSTER_VERIFICATION_FAILED", f"Unsupported roster signing algorithm: {algorithm!r}.")

    if not isinstance(payload, dict):
        raise RosterError("ROSTER_VERIFICATION_FAILED", "Roster envelope 'payload' must be an object.")

    # ── trusted key? -- checked BEFORE any attempt to verify a signature
    # against it, matching `verify_assertion`'s own ordering: there is no
    # point spending a signature-verification attempt on a key this
    # installation was never told to trust in the first place, and doing so
    # first would make "was this key_id ever trusted" and "does the
    # signature verify" indistinguishable from the caller's point of view.
    if not trust_store.is_trusted(signing_key_id):
        raise RosterError("UNKNOWN_SIGNING_KEY", f"Roster signed by an untrusted key: {signing_key_id!r}.")
    public_key_b64 = trust_store.get_public_key_b64(signing_key_id)

    try:
        canonical_bytes = canonicalize_bytes(payload)
        signature = base64.b64decode(signature_b64)
        public_key_raw = base64.b64decode(public_key_b64)
    except (CanonicalizationError, ValueError) as exc:
        raise RosterError("ROSTER_VERIFICATION_FAILED", f"Malformed roster data: {exc}") from exc

    # ── THE signature check. Nothing above this line trusted a single byte
    # of `payload`'s CONTENTS (only its outermost shape, needed to even
    # attempt canonicalization) -- everything below this line is only ever
    # reached once that trust has actually been earned.
    try:
        Ed25519PublicKey.from_public_bytes(public_key_raw).verify(signature, canonical_bytes)
    except InvalidSignature as exc:
        raise RosterError("ROSTER_VERIFICATION_FAILED", "Roster signature is invalid.") from exc

    license_id = payload.get("license_id")
    if not isinstance(license_id, str) or not license_id:
        raise RosterError("ROSTER_VERIFICATION_FAILED", "Roster payload is missing a non-empty 'license_id'.")
    if expected_license_id is not None and license_id != expected_license_id:
        raise RosterError("ROSTER_LICENSE_MISMATCH", "Roster license_id does not match the expected licence.")

    # Validated (tz-aware, parseable) here so `store_roster`'s replay guard
    # can trust every roster it ever compares was already validated by this
    # same function -- see this module's docstring's "REPLAY PROTECTION"
    # paragraph for why that comparison is the one thing this whole file
    # exists to get right.
    _require_aware_datetime(payload.get("issued_at"), field_name="issued_at")

    _validate_devices(payload.get("devices"))

    return payload


def store_roster(conn: sqlite3.Connection, envelope: dict, *, trust_store: OwnerTrustStore) -> None:
    """Verifies `envelope` and, only if it verifies, writes it into the
    single `site_roster` row (schema v30's `_migrate_add_site_relay`,
    `id = 1`).

    RE-VERIFIES INTERNALLY -- DELIBERATE CHOICE, STATED HERE RATHER THAN
    LEFT IMPLICIT: the task that specified this function's shape allowed
    either of two designs -- have this function re-verify, or have it
    accept only an already-verified PAYLOAD (never a raw envelope) so a
    caller structurally cannot hand it anything else. This implementation
    takes the FIRST option: it takes the raw envelope and calls
    `verify_roster_envelope` on it itself, rather than trusting that
    whatever called this function already did so correctly. Chosen over the
    second option because Python has no way to make "this dict is a
    VERIFIED payload" a distinct, uncounterfeitable type from "this dict is
    an arbitrary payload-shaped dict" -- a caller could always construct or
    mutate a plain dict and pass it off as pre-verified, and nothing at the
    type level would catch that. Re-verifying here means the ONE guarantee
    this function makes ("nothing lands in `site_roster` that was not
    actually signed by a trusted key") holds no matter how this function is
    ever called in the future, including by a caller that gets the
    order of operations wrong. The signature-verification cost this pays
    for that guarantee is one Ed25519 verify per roster fetch -- a rare
    event (once per check-in cycle, not once per push/pull) -- not a
    per-request cost.

    THE REPLAY GUARD -- REJECTS A ROSTER OLDER THAN THE ONE ALREADY CACHED.
    See this module's own docstring for the full "why" (a validly-signed
    roster never expires on its own, so a captured old one is a genuine
    replay vector against exactly the suspension this file exists to
    enforce). The comparison is STRICT: a NEW roster whose `issued_at`
    equals the currently-cached one's is accepted (a harmless re-store of
    the same or an identically-timestamped roster, not a rollback), while
    anything strictly OLDER is refused with `RosterError("ROSTER_STALE",
    ...)` and NOTHING is written -- the existing cached roster is left
    completely untouched, exactly as it was before this call.

    Follows `store.py`'s stated transaction contract: never calls
    `conn.commit()` or `conn.rollback()` itself -- the caller decides when
    this write (and whatever else it is grouped with) becomes durable.
    Also follows `store.py`'s row_factory contract: `conn.row_factory` must
    already be `sqlite3.Row` before this is called (this function reads the
    existing row, if any, by column name).
    """
    payload = verify_roster_envelope(envelope, trust_store=trust_store)
    new_issued_at = _require_aware_datetime(payload["issued_at"], field_name="issued_at")

    existing = conn.execute("SELECT issued_at FROM site_roster WHERE id = 1").fetchone()
    if existing is not None:
        existing_issued_at = _require_aware_datetime(existing["issued_at"], field_name="stored issued_at")
        if new_issued_at < existing_issued_at:
            # MUTATION-PROOF NOTE (see test_site_relay_roster.py): removing
            # this check entirely lets an attacker who once captured a
            # validly-signed, now-stale roster replay it to UN-suspend a
            # device whose real, current roster status is SUSPENDED --  the
            # signature alone can never catch this, because the replayed
            # envelope's signature is completely genuine.
            raise RosterError(
                "ROSTER_STALE",
                "Refusing to store a roster older than the one already cached (possible replay).",
            )

    conn.execute(
        "INSERT INTO site_roster (id, roster_json, signature, signing_key_id, issued_at, verified_at) "
        "VALUES (1, ?, ?, ?, ?, CURRENT_TIMESTAMP) "
        "ON CONFLICT(id) DO UPDATE SET "
        "roster_json = excluded.roster_json, "
        "signature = excluded.signature, "
        "signing_key_id = excluded.signing_key_id, "
        "issued_at = excluded.issued_at, "
        "verified_at = CURRENT_TIMESTAMP",
        (json.dumps(payload), envelope["signature"], envelope["signing_key_id"], payload["issued_at"]),
    )


def load_roster(conn: sqlite3.Connection) -> Optional[dict]:
    """Returns the currently cached roster PAYLOAD (`license_id`,
    `issued_at`, `devices`), or `None` if this hub has never successfully
    stored one. Deliberately returns the payload, not the storage row --
    signature/signing_key_id are `store_roster`'s own re-verification
    concern, not something a caller consulting "what does the roster
    currently say" needs to see again (the payload was already proven
    authentic at store time; this hub does not re-verify on every read,
    matching the design's own "cached so the hub can authorize LAN requests
    ... rather than requiring a live Owner round trip per request" intent
    for `roster_json`, schema.py's `_migrate_add_site_relay` docstring).

    Assumes `conn.row_factory is sqlite3.Row`, matching every other
    function in this package (see `store.py`'s module docstring)."""
    row = conn.execute("SELECT roster_json FROM site_roster WHERE id = 1").fetchone()
    if row is None:
        return None
    return json.loads(row["roster_json"])


def roster_device_status(conn: sqlite3.Connection, installation_id: str) -> Optional[str]:
    """Returns the cached roster's status string for `installation_id`, or
    `None` if EITHER no roster is cached at all OR the cached roster does
    not mention this installation_id. Collapsing both cases to the same
    `None` is deliberate, not a loss of information anyone downstream
    needs: `auth.py`'s enforcement gate treats "no roster" and "roster
    exists but is silent about this device" identically (see that module's
    docstring -- both mean "do not deny," and neither is a case the gate
    needs to tell apart from the other)."""
    roster_payload = load_roster(conn)
    if roster_payload is None:
        return None
    for device in roster_payload.get("devices", []):
        if device.get("installation_id") == installation_id:
            return device.get("status")
    return None
