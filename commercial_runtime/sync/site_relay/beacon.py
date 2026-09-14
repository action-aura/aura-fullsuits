"""The hub's LAN address-changed problem, solved by a signed UDP broadcast.

See `docs/launch-readiness/lan-restaurant-design.md` sec4 ("B. Discovery and
pairing: QR to trust, beacon to find") -- specifically the "Address changes
(router reboot, new DHCP lease)" bullet and the "Why not mDNS/Bonjour" bullet,
which together are this module's specification. QR pairing (a separate,
sibling module, not built by this file) establishes which hub a device
trusts, once, by KEY. This module keeps that trust valid when the hub's
ADDRESS moves underneath it -- a router reboot handing the till a new DHCP
lease is the single most common way a shop's LAN sync silently goes dark, and
the symptom on every paired tablet is not an error, it is nothing: sync just
stops advancing, with no obvious cause to point a non-technical owner at.

WHAT THE BEACON IS, IN THE DESIGN DOC'S OWN WORDS: "the hub broadcasts a
small UDP beacon every few seconds on the local subnet: {installation_id,
current URL, SPKI pin, hub wall-clock time, Ed25519 signature by the hub's
device key}. Paired devices verify the signature against the hub key they
learned at pairing (or from the roster) and update the stored URL." That is
this module end to end: `build_beacon`/`BeaconBroadcaster` on the hub side,
`parse_beacon`/`listen_for_beacon` on the paired-device side.

WHY THIS IS SAFE EVEN THOUGH IT IS AN UNAUTHENTICATED UDP BROADCAST, again in
the design doc's own words: "The beacon carries no secrets; an attacker
replaying it can only redirect devices to a host that must then present the
pinned key, which it cannot." Concretely: the beacon only updates the
REMEMBERED ADDRESS a paired device dials next -- it never grants trust by
itself. `pinned_transport.py`'s SPKI check runs on every connection that
address is ever used for, independent of the beacon, so redirecting a device
to an attacker's host (by spoofing or replaying a beacon) achieves nothing:
the attacker's TLS handshake presents the attacker's key, the pin check
fails, and the connection is refused before a single byte of real sync
traffic moves. The beacon's own signature exists to stop something NARROWER
but still real: an unsigned or tampered `url` field would let anyone on the
wifi redirect every till in the shop to a host of their choosing merely by
broadcasting UDP packets, wasting every device's retry budget against a dead
or hostile address instead of the real hub -- an availability attack, not a
trust bypass, but one that is cheap to close here, so it is closed.

WHAT THE BEACON LEAKS, STATED PLAINLY (design sec5, residual risk #3): "UDP
beacon reveals that an Aura hub exists on the LAN (metadata only)." Nothing
else: `installation_id`, the URL and the SPKI pin are not secrets by design
-- the SPKI pin is a public-key fingerprint already handed out openly on the
pairing QR (sec4), and the URL is exactly what a device on the SAME wifi
would trivially discover the moment it made its first (pinned, and therefore
still safe) connection anyway.

REQUIREMENTS THAT ARE NOT NEGOTIABLE, each argued again at its point of
enforcement below rather than only here:

  1. The signed payload is canonicalized with the SAME `canonicalize_bytes`
     the sync protocol itself signs with (`licensing_contracts/canonical.py`)
     -- never a second, beacon-specific serialization. Two serializers for
     "the same conceptual signed payload" drift from each other over time by
     construction (a field re-ordered, a number formatted differently), and
     the failure mode is a signature that silently never verifies -- not a
     loud bug, a beacon that quietly never updates anyone's address.
  2. The signature covers the WHOLE payload, `url` and `timestamp` included.
     `url` is the entire point of the datagram; leaving it unsigned would let
     anyone on the wifi redirect every till by forging that one field over a
     signature that still verifies.
  3. `parse_beacon` verifies the signature BEFORE it trusts anything else
     about the datagram, including before checking whether it is even fresh.
     Freshness bounds how long a captured-and-replayed beacon stays useful;
     it authenticates nothing by itself.
  4. The size cap is enforced BEFORE any parsing is attempted. A UDP
     listener that runs `json.loads` on whatever bytes arrive, of whatever
     length, before checking anything, is a textbook denial-of-service
     surface on a port every device on the shop wifi can reach.
  5. `base_url_fn` is a CALLABLE, evaluated fresh on every broadcast, never a
     string captured once. The hub's own address changing is the entire
     reason this module exists; a broadcaster that captured the URL once at
     construction would broadcast a stale address forever -- exactly the
     failure this feature exists to cure, self-inflicted.
  6. Every send is wrapped so a broken interface, a network stack that
     refuses broadcast, or any other transient error never kills the
     broadcast thread -- the interface may come back, and a dead thread
     cannot notice when it does.
  7. `listen_for_beacon` keeps listening past an invalid datagram instead of
     returning on it. Anything else on the LAN -- a chatty printer, a
     phone's own broadcast discovery, an attacker -- can put one junk UDP
     packet on this port; a listener that gives up on the first one denies
     service to the real beacon for the rest of its timeout window.

Standard library only: `socket` for the broadcast/listen, `json` (via
`canonicalize_bytes`) for the wire format, `threading` for the hub-side
background loop. No new dependency.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.device_identity import verify_signature

_log = logging.getLogger(__name__)

# The design doc does not pin a specific port; this one is chosen once, here,
# so the hub and every paired device agree on it with no configuration of
# their own. Outside the well-known/registered range (0-1023, which would
# need elevated privileges on most platforms anyway) and outside the common
# ephemeral-port range most OSes hand out to outbound sockets, to keep
# accidental collisions unlikely on a shop LAN that already carries printers,
# other POS peripherals and ordinary consumer devices.
BEACON_PORT = 45455

# Matches design sec4's "every few seconds": frequent enough that a device
# which just lost the hub (a router reboot handing out a new DHCP lease)
# re-finds it within a handful of seconds, not so frequent that five paired
# devices' worth of hub broadcasts become noticeable LAN chatter on their
# own.
BEACON_INTERVAL_SECONDS = 5.0

# How long a signed beacon stays acceptable after it was minted. This is NOT
# authentication -- see `parse_beacon`'s step 5 comment -- it only bounds how
# long a captured beacon stays replayable, the same role a skew window plays
# in `replay.py`, just wider: a beacon is a low-value redirect hint (see the
# module docstring's "WHY THIS IS SAFE" section above), not a signed
# transaction, so there is no security reason to make this tight, and a tight
# window would only reintroduce the same offline-clock-drift problem
# `replay.py`'s own 5-minute skew was deliberately widened to avoid.
BEACON_MAX_AGE_SECONDS = 120.0

# Enforced FIRST, before a single byte is handed to `json.loads` -- see the
# module docstring's requirement 4. A real beacon (four short strings plus
# one ~88-character base64 signature) is a few hundred bytes at most; 1024
# leaves comfortable room for a long LAN URL without opening the door to a
# UDP listener that will run a JSON parser over an attacker-chosen payload of
# unbounded size.
BEACON_MAX_DATAGRAM_BYTES = 1024

# Every field the signed payload -- and therefore every field required for a
# beacon to be considered at all -- must carry. `url` and `timestamp` sit on
# equal footing with the others deliberately: see requirement 2 above and its
# enforcement at the signature-verification step in `parse_beacon` below.
# This is the beacon's analogue of `auth.REQUIRED_AUTH_FIELDS` -- same shape,
# smaller set (a beacon carries no nonce; see `parse_beacon`'s docstring,
# step 5, for why one is not needed here: a beacon is a low-value redirect
# hint that a pinned TLS handshake must still validate afterward, not a
# transaction that must never be replayed at all).
REQUIRED_BEACON_FIELDS = ("installation_id", "url", "spki_pin", "timestamp", "signature")


class BeaconError(Exception):
    """Raised by `parse_beacon`. Carries a `reason_code` -- the same shape as
    this package's `auth.SiteAuthError` and `replay.ReplayError` -- so a
    caller (`listen_for_beacon`, or a future pairing/status UI) can branch on
    WHY a beacon was rejected without parsing exception text.

    These codes are minted fresh for THIS module, not borrowed from
    `licensing_contracts/reason_codes.py`: that registry's own docstring
    scopes it to codes a product can receive FROM Owner, or codes a
    licensing client can produce -- a beacon is neither. Listed here instead:

        BEACON_TOO_LARGE      -- rejected by size, before any parsing at all.
        MALFORMED_BEACON      -- not valid UTF-8 JSON, or not a JSON object.
        INVALID_BEACON        -- valid JSON, but missing/empty a required
                                 field, or an unparseable timestamp.
        INVALID_SIGNATURE     -- the signature does not verify against the
                                 caller-supplied device public key (including
                                 either side's base64 being unparseable).
        BEACON_STALE          -- the signature is valid, but the timestamp
                                 falls outside `max_age_seconds`.
        INSTALLATION_MISMATCH -- signature and freshness both check out, but
                                 `expected_installation_id` was given and
                                 does not match.
    """

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def build_beacon(*, installation_id: str, base_url: str, spki_pin: str,
                  signer, now: Optional[datetime] = None) -> bytes:
    """Builds one signed beacon datagram, ready to hand to `socket.sendto`.

    `signer` is the same minimal protocol `SyncRelayClient` and
    `WindowsDpapiDeviceIdentityProvider` already expose to the rest of this
    codebase: an object with a `sign(canonical_bytes: bytes) -> str` method
    returning base64 (see `device_identity.DeviceIdentityProvider.sign`, and
    `test_site_relay_end_to_end.py`'s `_Ed25519Signer` for the exact contract
    this module is written against). Reusing that shape rather than inventing
    a beacon-specific one means the hub's existing device key -- the same key
    that signs every push/pull request -- signs the beacon too, with no new
    key material to generate, store, or ever rotate separately.

    `now` is the same injectable testing seam every other timestamp-freshness
    function in this package exposes (`replay.validate_timestamp`,
    `auth.authenticate`) -- defaults to real UTC now; production callers
    never pass it.
    """
    moment = now or datetime.now(timezone.utc)
    # Requirement 2: `url` and `timestamp` are signed fields, not passengers
    # riding alongside a signature that does not cover them.
    # Requirement 1: `canonicalize_bytes`, not a bespoke serialization --
    # the exact function the push/pull wire protocol signs with.
    payload = {
        "installation_id": str(installation_id),
        "url": str(base_url),
        "spki_pin": str(spki_pin),
        "timestamp": moment.isoformat(),
    }
    signature = signer.sign(canonicalize_bytes(payload))
    full_body = dict(payload, signature=signature)
    # The wire encoding reuses `canonicalize_bytes` too, deliberately -- not
    # because the TRANSMITTED copy needs to be signature-stable (only the
    # signed subset above does), but because canonical JSON is also the most
    # compact correct encoding available (sorted keys, no insignificant
    # whitespace), which matters directly against `BEACON_MAX_DATAGRAM_
    # BYTES`, and because it means this module never touches `json.dumps`
    # with a second, different set of options anywhere.
    return canonicalize_bytes(full_body)


def parse_beacon(datagram: bytes, *, expected_device_public_key: str,
                  expected_installation_id: Optional[str] = None,
                  now: Optional[datetime] = None,
                  max_age_seconds: float = BEACON_MAX_AGE_SECONDS) -> dict:
    """Verifies and decodes one beacon datagram, or raises `BeaconError`.

    ORDER OF OPERATIONS, and why it is exactly this order (requirement 3):

      1. size cap -- before a single byte is handed to a parser (requirement
         4: this is the DoS-surface concern, and it dominates every other
         ordering question here, so it goes first even though "check the
         length" looks like the least interesting line in this function).
      2. JSON parse -- can raise on garbage input; caught explicitly so a
         malformed datagram becomes a `BeaconError`, never an uncaught
         exception that would take a caller's listen loop down with it (see
         `listen_for_beacon`'s own requirement to survive junk packets).
      3. required fields present and non-empty.
      4. SIGNATURE VERIFICATION -- before anything below trusts a single
         field's VALUE for anything, including the timestamp used by the
         very next check. This is the same discipline `auth.authenticate`
         documents at length for the push/pull path: an unverified field is
         not yet a fact, it is an unauthenticated claim, and a freshness
         check run against an unproven claim proves nothing.
      5. timestamp freshness -- exists to bound REPLAY, not to AUTHENTICATE:
         it runs only after step 4 has already established the beacon really
         was signed by the expected key at some point; all this step adds is
         "...and recently". A beacon has no per-datagram nonce (unlike the
         push/pull protocol's `auth.py`) because it does not need one: it is
         a low-value redirect hint that a pinned TLS handshake must still
         validate independently afterward (see the module docstring's "WHY
         THIS IS SAFE" section) -- there is nothing here for a nonce to
         protect that the pin check does not already protect on its own.
      6. `expected_installation_id` match, only when the caller supplied one
         -- last, because it is the least security-relevant check of the
         six: the signature already proves which KEY spoke; this is a caller
         convenience for "and it claims to be the installation I think it
         is" (useful once a hub has been re-promoted or re-paired), not an
         independent trust boundary.
    """
    # -- Step 1: size cap, enforced before any parsing whatsoever ----------
    if len(datagram) > BEACON_MAX_DATAGRAM_BYTES:
        raise BeaconError("BEACON_TOO_LARGE")

    # -- Step 2: JSON parse --------------------------------------------------
    try:
        body = json.loads(datagram.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BeaconError("MALFORMED_BEACON")
    if not isinstance(body, dict):
        raise BeaconError("MALFORMED_BEACON")

    # -- Step 3: required fields present and non-empty -----------------------
    for field in REQUIRED_BEACON_FIELDS:
        if not body.get(field):
            raise BeaconError("INVALID_BEACON")

    # -- Step 4: verify the signature over the canonicalized body (minus
    # `signature` itself) BEFORE trusting any field's value -- see this
    # function's docstring. Same canonicalize-then-verify shape as
    # `auth.authenticate`'s step 6, against the caller-supplied device public
    # key: the beacon carries no roster of its own to resolve a key from --
    # the caller already knows which hub key it trusts, learned once at
    # pairing.
    signable = {k: v for k, v in body.items() if k != "signature"}
    canonical_bytes = canonicalize_bytes(signable)
    try:
        signature_bytes = base64.b64decode(body["signature"], validate=True)
        raw_public_key = base64.b64decode(expected_device_public_key, validate=True)
    except (TypeError, ValueError, binascii.Error):
        # Malformed base64 on either side can never verify; treat it as
        # exactly what it is to the caller -- a signature that does not
        # check out -- rather than letting a decode error escape as an
        # uncaught exception.
        raise BeaconError("INVALID_SIGNATURE")
    if not verify_signature(raw_public_key, canonical_bytes, signature_bytes):
        raise BeaconError("INVALID_SIGNATURE")

    # -- Step 5: freshness -- bounds REPLAY, does not AUTHENTICATE. See this
    # function's docstring, step 5, for the full reasoning; the check itself
    # is symmetric (a beacon claiming to be from the future is rejected too),
    # matching `replay.validate_timestamp`'s own `abs(delta)` shape, since a
    # hub whose clock has drifted AHEAD is just as much a hygiene concern as
    # one that has drifted behind.
    try:
        beacon_time = datetime.fromisoformat(str(body["timestamp"]).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise BeaconError("INVALID_BEACON")
    if beacon_time.tzinfo is None:
        # A naive datetime has no defined offset from UTC and cannot be
        # safely compared against `moment` (always tz-aware UTC here)
        # without silently assuming one -- reject outright rather than
        # guess, exactly `replay.validate_timestamp`'s own posture.
        raise BeaconError("INVALID_BEACON")
    moment = now or datetime.now(timezone.utc)
    age_seconds = (moment - beacon_time).total_seconds()
    if abs(age_seconds) > max_age_seconds:
        raise BeaconError("BEACON_STALE")

    # -- Step 6: caller-supplied installation_id match, when given ----------
    if expected_installation_id is not None and str(body["installation_id"]) != str(expected_installation_id):
        raise BeaconError("INSTALLATION_MISMATCH")

    return {
        "installation_id": str(body["installation_id"]),
        "url": str(body["url"]),
        "spki_pin": str(body["spki_pin"]),
        "timestamp": beacon_time,
    }


class BeaconBroadcaster:
    """Hub side: broadcasts a fresh, signed beacon on a background daemon
    thread every `interval_seconds`, until `stop()`.

    `base_url_fn` is a CALLABLE -- see the module docstring's requirement 5.
    It is read fresh on every single broadcast (`self._base_url_fn()` inside
    `_run` below, never cached onto an attribute), because the hub's own
    address changing out from under a captured string is the entire reason
    this class exists: a broadcaster built by closing over a `base_url`
    string at construction time would broadcast that one, frozen address
    forever, silently reintroducing the exact failure this module exists to
    cure the moment the hub's own IP actually changes -- which is precisely
    when a caller most needs the broadcast to reflect reality.
    """

    def __init__(self, *, installation_id: str, base_url_fn: Callable[[], str],
                 spki_pin: str, signer, port: int = BEACON_PORT,
                 interval_seconds: float = BEACON_INTERVAL_SECONDS):
        self._installation_id = installation_id
        self._base_url_fn = base_url_fn
        self._spki_pin = spki_pin
        self._signer = signer
        self._port = port
        self._interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Idempotent: calling `start()` on an already-running broadcaster is
        a no-op rather than a second thread -- matches this codebase's
        general posture on background loops that must not be started twice
        (see `listener.py`'s own guard on `_site_relay_server`, and
        `app.py::init_app`'s comment on the same hazard)."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="aura-site-relay-beacon", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_seconds + 1.0)
            self._thread = None

    def _run(self) -> None:
        # One socket for the broadcaster's whole lifetime -- see the
        # requirement 6 comment at the send site below for why a send
        # failure must never tear this socket down and force a reconnect:
        # the interface coming back is exactly the case this loop exists to
        # recover from, and it can only do that by staying alive to notice.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            while not self._stop_event.is_set():
                try:
                    # Requirement 5: called fresh every single iteration.
                    datagram = build_beacon(
                        installation_id=self._installation_id,
                        base_url=self._base_url_fn(),
                        spki_pin=self._spki_pin,
                        signer=self._signer,
                    )
                    sock.sendto(datagram, ("255.255.255.255", self._port))
                except Exception:
                    # Requirement 6: a down interface, a network stack that
                    # refuses broadcast, or any other transient failure must
                    # never kill this loop -- the interface may come back on
                    # the NEXT tick, and a dead thread would never notice
                    # when it does. Logged at debug, not error: this fires
                    # innocuously on any machine without a working broadcast
                    # route (some VMs, some locked-down corporate networks)
                    # on every single tick, and per-send error-level logging
                    # on a 5-second cadence is exactly the log spam
                    # requirement 6 rules out.
                    _log.debug("site relay beacon send failed", exc_info=True)
                self._stop_event.wait(self._interval_seconds)
        finally:
            sock.close()


def listen_for_beacon(*, expected_device_public_key: str, timeout_seconds: float,
                       port: int = BEACON_PORT,
                       expected_installation_id: Optional[str] = None) -> Optional[dict]:
    """Blocks for up to `timeout_seconds` total, returning the first VALID
    beacon received, or `None` if the deadline passes with nothing valid.

    Requirement 7, the load-bearing property this function's whole design
    exists to satisfy: an invalid datagram does NOT end the wait. The
    deadline is tracked in wall-clock terms up front (`deadline`, below) and
    each socket recv is given only the time remaining, so a burst of junk
    packets can consume the timeout window (a real, if minor, availability
    concern already named in the module docstring) but can never make this
    function return EARLY with the wrong answer -- which is exactly the
    property this module's own test suite pins directly (send junk, then a
    real beacon; the real beacon must still come back).

    `SO_REUSEADDR` + binding `('', port)` (not a specific interface) matches
    how a paired device needs to receive the hub's broadcast regardless of
    which local NIC it arrives on, and lets a second call bind again
    immediately after a prior one closes rather than waiting out TIME_WAIT --
    the same reasoning `listener.py`'s `_ThreadingWSGIServer.allow_reuse_
    address` documents for the TLS listener.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", port))
    try:
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                # A generous buffer, well past BEACON_MAX_DATAGRAM_BYTES: the
                # size cap this module enforces lives inside `parse_beacon`,
                # applied to the datagram's actual length -- not here, as
                # "how much of an oversized packet the socket layer happens
                # to capture". An over-cap datagram still reaches
                # `parse_beacon` whole and is rejected there, on its own
                # terms, rather than silently truncated a layer below it.
                datagram, _source = sock.recvfrom(65536)
            except socket.timeout:
                return None
            try:
                return parse_beacon(
                    datagram,
                    expected_device_public_key=expected_device_public_key,
                    expected_installation_id=expected_installation_id,
                )
            except BeaconError:
                # Requirement 7: keep listening. Returning (or raising) here
                # would let one junk packet from anything else on the LAN --
                # a chatty printer, a phone's own broadcast discovery, an
                # attacker -- deny service to the real beacon for the rest of
                # this call's timeout window.
                _log.debug("discarding invalid beacon datagram, continuing to listen")
                continue
    finally:
        sock.close()
