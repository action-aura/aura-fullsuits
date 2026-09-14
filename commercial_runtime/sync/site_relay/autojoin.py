"""Automatic discovery-and-join for the LAN site relay -- the DESKTOP half.

THE OWNER'S RULING THIS IMPLEMENTS (`join.py`'s own docstring carries the
full mechanism this module drives): LAN sync must be FULLY AUTOMATIC -- no
QR code, no pasted payload, no IP address, no port number, nothing anyone
ever has to type or look at. A second till on the shop wifi must find the
hub and join it BY ITSELF. Desktop tills are the common case here, not the
minor half: this module is the client-side orchestration that ties together
three already-built, separately-owned pieces --

  1. `beacon.py`             -- the hub's signed UDP broadcast (where to look)
  2. `pinned_transport.py`   -- SPKI-pinned TLS (who we are actually talking to)
  3. `assertion_verifier.py` -- Owner-signed licence proof (who gets to belong)

-- into the one function a caller (a background timer, eventually wired up
by whoever owns `listener.py`/`app.py`; not this file) needs:
`discover_and_join`.

THE SHOP BOUNDARY. Per the owner's ruling, the shared LICENCE -- and nothing
else -- decides which shop a device belongs to. This REPLACES the
operator-issued pairing code (`pairing.py`'s `/pair`, still available as a
fallback): two devices belong together **iff their Owner-signed assertions
name the same `license_public_id`**, checkable by both sides with zero
Owner contact (the same offline-verifiable design every other Owner-signed
artefact in this codebase already uses -- `roster.py`'s roster envelope is
the closest sibling, and `join.py`'s `verify_membership` is the SERVER-side
mirror of the check this module performs on the CLIENT side against a hub
it does not yet trust).

A BEACON IS A POINTER, NOT A CREDENTIAL -- read this before touching the
listening step below. `beacon.py`'s own module docstring says it plainly:
"an attacker replaying it can only redirect devices to a host that must
then present the pinned key, which it cannot" (for an ALREADY-PAIRED
device) -- and for a device that has never paired at all, which is exactly
this module's job, the same principle applies even more strongly, because
there is nothing about "location-finding" that a signature could protect
here that the steps below do not already protect independently. Concretely:
`beacon.py`'s own `parse_beacon`/`listen_for_beacon` are written for the
OTHER use case -- a device that already learned the hub's device key AT
PAIRING and is merely re-discovering its current address ("the caller
already knows which hub key it trusts, learned once at pairing" --
`parse_beacon`'s own docstring). A device running THIS module has never
paired with anything yet and has no such key to check against -- the
signature-verified entry points cannot be called at all here, because the
one argument they require unconditionally (`expected_device_public_key`) is
exactly the thing autojoin does not yet have. So the default listener below
(`_default_beacon_listener`) reuses beacon.py's WIRE FORMAT -- its port
(`BEACON_PORT`) and its size cap (`BEACON_MAX_DATAGRAM_BYTES`), so the two
modules can never silently drift apart on what a beacon datagram looks like
-- and extracts only the POINTER fields (`installation_id`, `url`,
`spki_pin`) from it, deliberately WITHOUT attempting the signature check
`parse_beacon` performs, because that check cannot be performed here and
because nothing downstream depends on it having been performed: the pinned
TLS handshake (step 2) only ever succeeds against a peer that actually holds
the private key matching whatever `spki_pin` was advertised -- so a forged
beacon can redirect this device to a dead end, or to some OTHER hub's real
listener, but never to something that can forge its way past step 3 below.
**Never describe the code below as "verifying the beacon" -- it does not,
on purpose, and could not usefully in this position anyway.** The verified
ASSERTION is what decides trust, not the beacon.

THE FLOW, end to end -- every step gating the next, exactly mirroring
`join.py`'s own server-side `verify_membership` (read that module alongside
this one; it is the closest sibling and this module deliberately does not
re-invent any of its reasoning):

  0. This device must already be activated -- holding its OWN Owner-signed
     assertion locally (`LicenseStateRecord.assertion_envelope_json`). An
     unactivated device has no licence and therefore no shop to prove
     membership of; `discover_and_join` does NOTHING (no beacon listen, no
     network call of any kind) and returns `NOT_ACTIVATED`.
  1. Listen for a hub beacon (`beacon.py`'s wire format, unauthenticated --
     see above). No beacon heard inside `listen_timeout` -> `NO_HUB_FOUND`.
  2. If the beacon's `url` already equals this device's OWN persisted relay
     (`LicenseStateRecord.sync_relay_base_url` -- see
     `products/retail/backend/config.py::_discover_persisted_sync_relay_url`,
     the existing seam this module writes to and never invents a second
     one for), this device is already using this hub -- `ALREADY_JOINED`,
     a pure no-op: no HTTP call of any kind. This is what makes calling
     this function repeatedly on a timer safe and cheap.
  3. Fetch `GET {url}/api/sync/v1/identity` over a `pinned_session(spki_pin)`
     -- the SPKI pin the beacon advertised, so this connection can only ever
     succeed against whatever host actually holds the matching TLS private
     key. Any failure to reach it, a non-200, or an unparsable/malformed
     response body -> `FAILED`.
  4. Independently verify the returned assertion envelope with
     `assertion_verifier.verify_assertion` against the bundled trust anchor
     (`trust_store`) -- untrusted-signing-key, tampered-payload, and expiry
     are all `verify_assertion`'s own guarantees, reused rather than
     reimplemented (see `join.py`'s own "WHY verify_assertion IS CALLED"
     section for the identical reasoning applied on the server side).
     `expected_product_code`/`expected_platform` are read off the SAME
     envelope's own payload, deliberately self-referential rather than
     hardcoded -- `join.py`'s docstring explains at length why this is not a
     shortcut around a real check: this design's shop boundary is the
     licence alone, never product/platform, so making that comparison a
     trivial self-match is the documented absence of a check this design
     never wanted, not a corner cut here. `expected_installation_id`/
     `expected_device_key_fingerprint` are bound to the `installation_id`/
     `device_public_key` the SAME `/identity` response claims for itself --
     this is what stops a hub from presenting someone ELSE's genuine
     assertion alongside its own key (the identical "assertion is not
     secret" attack `join.py`'s own docstring names in detail). ANY
     verification failure -- untrusted key, expired, not-yet-valid,
     malformed, installation/device mismatch -> `FAILED`, logged at INFO
     with the reason code (never the envelope or key material -- see
     "NEVER LOG" below). Fail closed: no join on any doubt.
  5. THE SHOP BOUNDARY ITSELF: the verified assertion's `license_public_id`
     must equal THIS device's own (read from its own locally-verified
     assertion, `step 0`'s envelope). A different licence is a different
     shop, full stop -- `DIFFERENT_SHOP`, logged at INFO, and `/join` is
     NEVER called. Checked only after step 4 has already proven the
     envelope genuine -- reading `license_public_id` out of an unverified
     envelope first would mean branching on attacker-controlled bytes
     before they have been proven to come from Owner at all (`join.py`'s
     own docstring makes the identical point about ordering).
  6. `POST {url}/api/sync/v1/join` with THIS device's own
     `{installation_id, device_public_key, assertion}` -- the automatic
     equivalent of typing a pairing code, this device proving ITS OWN
     membership to the hub the same way the hub just proved its membership
     to us. A non-200 (or unreachable) response -> `FAILED`, and the relay
     URL is NOT persisted (see mutation-proof note in the test file: a
     hub that rejects the join must never be adopted as this device's
     relay).
  7. Only on a 200 from `/join`: persist `url` as this device's relay
     (`LicenseStateRepository.save`, `sync_relay_base_url` field -- see
     module docstring section "THE PERSISTENCE SEAM" below) and return
     `JOINED`.

NEVER LOG assertion contents or key material, anywhere in this module --
only reason codes, URLs, and boolean outcomes. An assertion envelope is not
itself secret (`join.py`'s own docstring), but there is no reason for this
module's logs to be the first place someone pastes one from.

THE PERSISTENCE SEAM. `products/retail/backend/config.py`'s
`_discover_persisted_sync_relay_url()` already reads
`LicenseStateRepository.load().sync_relay_base_url` as the fallback source
for `SYNC_RELAY_BASE_URL` on the NEXT process launch (Owner's own
activation-time value is the other source, `_resolve_effective_sync_relay_
base_url`'s precedence rule). This module writes to exactly that same
field, through the same `LicenseStateRepository`, rather than inventing a
second "where does this device's relay live" concept -- so a successful LAN
autojoin takes effect on this device's next restart exactly the same way an
Owner-provided relay URL already does, with no new config surface.

`signer` is the same minimal `sign(canonical_bytes) -> base64` / `get_public
_key_b64() -> base64` protocol every other caller in this package already
uses (`beacon.py`'s own `build_beacon` docstring cites the identical
contract) -- here only `get_public_key_b64()` is needed: `POST /join`'s body
carries this device's own public key so the hub can bind it to the assertion
presented alongside it (`routes.py`'s `/join` handler has no signature
requirement of its own; the assertion IS the proof).
"""
from __future__ import annotations

import base64
import binascii
import enum
import json
import logging
import socket
import time
from datetime import datetime
from typing import Callable, Optional

import requests

from commercial_runtime.licensing_contracts.assertion_verifier import (
    AssertionVerificationError,
    verify_assertion,
)
from commercial_runtime.licensing_contracts.device_identity import fingerprint_of
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore
from commercial_runtime.sync.site_relay import beacon
from commercial_runtime.sync.site_relay.pinned_transport import pinned_session

_log = logging.getLogger(__name__)

IDENTITY_PATH = "/api/sync/v1/identity"
JOIN_PATH = "/api/sync/v1/join"

# Per-request HTTP timeout for both the identity fetch and the join POST.
# Independent of `listen_timeout` (which bounds the UDP beacon wait) -- these
# are ordinary TLS requests against a host that just answered a beacon a
# moment ago, not a discovery wait.
REQUEST_TIMEOUT_SECONDS = 10.0


class AutoJoinResult(enum.Enum):
    JOINED = "JOINED"
    ALREADY_JOINED = "ALREADY_JOINED"
    NO_HUB_FOUND = "NO_HUB_FOUND"
    DIFFERENT_SHOP = "DIFFERENT_SHOP"
    NOT_ACTIVATED = "NOT_ACTIVATED"
    FAILED = "FAILED"


def _extract_beacon_pointer(datagram: bytes) -> Optional[dict]:
    """Decodes JUST the POINTER fields (`installation_id`, `url`,
    `spki_pin`) out of a raw beacon datagram -- deliberately NOT
    `beacon.parse_beacon`, which mandates a pre-known
    `expected_device_public_key` this module never has (see module
    docstring's "A BEACON IS A POINTER, NOT A CREDENTIAL" section for why
    that function cannot be reused unmodified here). Reuses beacon.py's own
    size cap (`BEACON_MAX_DATAGRAM_BYTES`) so the two modules can never
    silently disagree on the wire format's bounds -- enforced FIRST, before
    any parsing, for the identical DoS reasoning `parse_beacon`'s own
    docstring states (requirement 4).

    Returns `None` for anything that does not even shape up as a beacon
    (oversized, not JSON, not an object, missing/empty pointer fields) --
    the caller (`_default_beacon_listener`) treats that exactly like
    `beacon.listen_for_beacon` treats an invalid datagram: keep listening,
    never end the wait early on one junk packet.
    """
    if len(datagram) > beacon.BEACON_MAX_DATAGRAM_BYTES:
        return None
    try:
        body = json.loads(datagram.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None

    installation_id = body.get("installation_id")
    url = body.get("url")
    spki_pin = body.get("spki_pin")
    if not (isinstance(installation_id, str) and installation_id):
        return None
    if not (isinstance(url, str) and url):
        return None
    if not (isinstance(spki_pin, str) and spki_pin):
        return None
    return {"installation_id": installation_id, "url": url, "spki_pin": spki_pin}


def _default_beacon_listener(timeout_seconds: float) -> Optional[dict]:
    """Production default for `discover_and_join`'s `beacon_listener`
    parameter. Binds `beacon.BEACON_PORT` (matching `beacon.listen_for_
    beacon`'s own `SO_REUSEADDR` + bind-to-any-interface shape, and for the
    identical reason: this device must receive the hub's broadcast
    regardless of which local NIC it arrives on) and returns the FIRST
    datagram that parses as a beacon pointer (`_extract_beacon_pointer`), or
    `None` if `timeout_seconds` elapses with nothing valid heard -- mirroring
    `listen_for_beacon`'s own deadline-tracked loop shape (requirement 7 in
    that module's docstring: an invalid datagram must never end the wait
    early) even though the parsing underneath is deliberately different.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", beacon.BEACON_PORT))
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                datagram, _source = sock.recvfrom(65536)
            except socket.timeout:
                return None
            pointer = _extract_beacon_pointer(datagram)
            if pointer is not None:
                return pointer
            # Keep listening past a junk/invalid datagram -- see this
            # function's own docstring.
    finally:
        sock.close()


def _own_assertion_envelope(assertion_envelope_json: str) -> Optional[dict]:
    """Parses this device's OWN locally-stored assertion envelope back into
    a dict, or `None` if it is unreadable. Deliberately NOT re-verified
    against the trust store here -- this envelope was already independently
    verified (via `verify_assertion`) before ever being persisted, by
    whichever of `activation.py`/`checkin_scheduler.py` wrote it
    (`LicenseStateRecord.assertion_envelope_json`'s own docstring). Treating
    already-locally-trusted state as trusted again here is the same posture
    `products/retail/backend/config.py::_discover_persisted_sync_relay_url`
    already takes for this exact record."""
    try:
        envelope = json.loads(assertion_envelope_json)
    except ValueError:
        return None
    return envelope if isinstance(envelope, dict) else None


def discover_and_join(
    *,
    state_repository: LicenseStateRepository,
    signer,
    trust_store: OwnerTrustStore,
    trusted_now: datetime,
    session_factory: Callable[[str], "requests.Session"] = pinned_session,
    listen_timeout: float = 10.0,
    beacon_listener: Optional[Callable[[float], Optional[dict]]] = None,
) -> AutoJoinResult:
    """See module docstring for the full, numbered flow. Safe to call
    repeatedly on a timer (step 2's `ALREADY_JOINED` short-circuit is a pure
    no-op, no network call at all, for a hub this device already uses).

    Every collaborator is injectable so this can be tested with no sockets
    and no real licensing state:
      * `state_repository` -- anything exposing `.load() -> LicenseStateRecord
        | None` and `.save(record)`, matching `LicenseStateRepository`'s own
        interface.
      * `signer` -- anything exposing `.get_public_key_b64() -> str`,
        matching `DeviceIdentityProvider`'s own interface (see module
        docstring).
      * `trust_store` -- an `OwnerTrustStore` (or test double answering
        `is_trusted`/`get_public_key_b64`).
      * `trusted_now` -- the injectable clock parameter every real
        `verify_assertion` caller in this codebase already threads through
        rather than reading the wall clock internally (`activation.py`,
        `checkin_scheduler.py`, `join.py`'s own `POST /join` handler).
      * `session_factory` -- defaults to the real `pinned_session`; a test
        supplies a fake returning a fake `requests.Session`-shaped object.
      * `beacon_listener` -- defaults to `_default_beacon_listener`; a test
        supplies a fake returning a canned pointer dict (or `None`) with no
        socket involved at all.
    """
    own_record = state_repository.load()
    if own_record is None or not own_record.assertion_envelope_json:
        # An unactivated device has no licence and therefore no shop to
        # join -- do NOTHING (no beacon listen, no network call of any
        # kind), per this module's own non-negotiable.
        _log.info(
            "site relay autojoin: this device has not activated a licence yet "
            "(no local assertion on file) -- nothing to join. Doing nothing."
        )
        return AutoJoinResult.NOT_ACTIVATED

    own_assertion_envelope = _own_assertion_envelope(own_record.assertion_envelope_json)
    own_payload = own_assertion_envelope.get("payload") if own_assertion_envelope else None
    own_license_public_id = own_payload.get("license_public_id") if isinstance(own_payload, dict) else None
    if not isinstance(own_license_public_id, str) or not own_license_public_id:
        # Locally-stored state that does not even shape up as a usable
        # assertion is, for this device's purposes, indistinguishable from
        # never having activated at all -- there is no shop boundary to
        # prove membership of either way.
        _log.info(
            "site relay autojoin: local licence assertion is unreadable -- "
            "treating this device as not activated rather than guessing at "
            "a shop boundary. Doing nothing."
        )
        return AutoJoinResult.NOT_ACTIVATED

    listener = beacon_listener if beacon_listener is not None else _default_beacon_listener
    pointer = listener(listen_timeout)
    if pointer is None:
        _log.info("site relay autojoin: no hub beacon heard within %.1fs.", listen_timeout)
        return AutoJoinResult.NO_HUB_FOUND

    hub_url = pointer["url"]
    if own_record.sync_relay_base_url and own_record.sync_relay_base_url == hub_url:
        # Already using this hub as our relay -- a pure no-op, no network
        # call of any kind. This is what makes calling this function
        # repeatedly on a timer cheap and safe.
        return AutoJoinResult.ALREADY_JOINED

    session = session_factory(pointer["spki_pin"])

    try:
        identity_response = session.get(
            hub_url.rstrip("/") + IDENTITY_PATH, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.exceptions.RequestException:
        _log.info("site relay autojoin: could not reach the beacon-advertised hub at %s.", hub_url)
        return AutoJoinResult.FAILED

    if identity_response.status_code != 200:
        _log.info(
            "site relay autojoin: hub identity fetch at %s failed (HTTP %s).",
            hub_url, identity_response.status_code,
        )
        return AutoJoinResult.FAILED

    try:
        identity_body = identity_response.json()
    except ValueError:
        _log.info("site relay autojoin: hub identity response from %s was not valid JSON.", hub_url)
        return AutoJoinResult.FAILED
    if not isinstance(identity_body, dict):
        _log.info("site relay autojoin: hub identity response from %s was not an object.", hub_url)
        return AutoJoinResult.FAILED

    hub_installation_id = identity_body.get("installation_id")
    hub_device_public_key = identity_body.get("device_public_key")
    hub_assertion = identity_body.get("assertion")
    if not isinstance(hub_installation_id, str) or not hub_installation_id:
        return AutoJoinResult.FAILED
    if not isinstance(hub_device_public_key, str) or not hub_device_public_key:
        return AutoJoinResult.FAILED
    if not isinstance(hub_assertion, dict):
        return AutoJoinResult.FAILED

    # The device-key fingerprint of the key the hub ACTUALLY presented in
    # THIS response -- computed BEFORE the assertion is trusted for
    # anything, so it can be fed into verify_assertion's own comparison
    # rather than trusting anything the envelope itself claims about the
    # key. Mirrors `join.py::verify_membership`'s identical step, on the
    # other side of this same handshake.
    try:
        raw_hub_public_key = base64.b64decode(hub_device_public_key, validate=True)
    except (TypeError, ValueError, binascii.Error):
        return AutoJoinResult.FAILED
    if len(raw_hub_public_key) != 32:
        return AutoJoinResult.FAILED
    hub_fingerprint = fingerprint_of(raw_hub_public_key)

    # product_code/platform: read from the SAME envelope's own payload so
    # verify_assertion's mandatory comparison is a self-match and therefore
    # never decides membership -- see module docstring, step 4, and
    # `join.py`'s own "DELIBERATELY SELF-REFERENTIAL" section for the
    # identical reasoning applied on the server side of this same design.
    hub_raw_payload = hub_assertion.get("payload")
    expected_product_code = hub_raw_payload.get("product_code") if isinstance(hub_raw_payload, dict) else None
    expected_platform = hub_raw_payload.get("platform") if isinstance(hub_raw_payload, dict) else None

    try:
        verified = verify_assertion(
            hub_assertion,
            trust_store=trust_store,
            expected_product_code=expected_product_code,
            expected_platform=expected_platform,
            expected_installation_id=hub_installation_id,
            expected_device_key_fingerprint=hub_fingerprint,
            trusted_now=trusted_now,
        )
    except AssertionVerificationError as exc:
        # Fail closed on ANY doubt -- untrusted key, expired, not-yet-valid,
        # malformed, installation/device mismatch. Logged at INFO (this runs
        # unattended; a silent no-op would be undiagnosable) with the reason
        # code only -- never the envelope or key material.
        _log.info(
            "site relay autojoin: hub assertion from %s failed verification (%s) -- "
            "refusing to join, fail closed.", hub_url, exc.reason_code,
        )
        return AutoJoinResult.FAILED

    # THE SHOP BOUNDARY. Only reached once the hub's envelope has already
    # been proven genuine, current, and bound to the key it actually
    # presented (the verify_assertion call above) -- never read
    # license_public_id out of an unverified envelope before that (see
    # module docstring, step 5, and `join.py`'s identical ordering
    # argument).
    if verified.payload.get("license_public_id") != own_license_public_id:
        _log.info(
            "site relay autojoin: hub at %s belongs to a different licence -- "
            "different shop, refusing to join.", hub_url,
        )
        return AutoJoinResult.DIFFERENT_SHOP

    join_body = {
        "installation_id": own_record.owner_installation_id,
        "device_public_key": signer.get_public_key_b64(),
        "assertion": own_assertion_envelope,
    }
    try:
        join_response = session.post(
            hub_url.rstrip("/") + JOIN_PATH, json=join_body, timeout=REQUEST_TIMEOUT_SECONDS
        )
    except requests.exceptions.RequestException:
        _log.info("site relay autojoin: /join request to %s failed.", hub_url)
        return AutoJoinResult.FAILED

    if join_response.status_code != 200:
        # THE HUB REFUSED -- never adopt it as this device's relay. See the
        # mutation-proof note in this module's test file: persisting before
        # this check would strand this device pointed at a hub that never
        # actually admitted it.
        _log.info(
            "site relay autojoin: hub at %s refused /join (HTTP %s).",
            hub_url, join_response.status_code,
        )
        return AutoJoinResult.FAILED

    # Persist AFTER, and only after, a 200 from /join -- see the mutation-
    # proof note immediately above. Uses the SAME seam
    # products/retail/backend/config.py::_discover_persisted_sync_relay_url
    # already reads on the next launch -- see module docstring's "THE
    # PERSISTENCE SEAM" section for why this is not a second, invented
    # config surface.
    own_record.sync_relay_base_url = hub_url
    state_repository.save(own_record)
    _log.info("site relay autojoin: joined hub at %s.", hub_url)
    return AutoJoinResult.JOINED
