"""Tests for `commercial_runtime.sync.site_relay.beacon` -- the signed UDP
broadcast a paired device uses to re-find the hub after its address changes
(a router reboot handing out a new DHCP lease is the common real-world
trigger). Task spec: `docs/launch-readiness/lan-restaurant-design.md` sec4,
specifically the "Address changes (router reboot, new DHCP lease)" bullet
and the "Why not mDNS/Bonjour" bullet.

Real Ed25519 keys throughout (`cryptography`), never a fake signature check
-- a beacon whose entire security property IS its signature is exactly the
wrong place to mock signing. `_Ed25519Signer` below is deliberately the same
shape as `test_site_relay_end_to_end.py`'s signer of the same name (real
Ed25519 over real canonical bytes, base64 out) -- the `DeviceSigner`-shaped
contract `build_beacon`, `SyncRelayClient` and
`WindowsDpapiDeviceIdentityProvider.sign` all agree on, so a beacon signed in
this test suite is signed exactly the way a shipped hub signs one.

Most of this suite (tests 1-8) exercises `build_beacon`/`parse_beacon`
directly -- no sockets needed to prove a signature verifies, a tamper is
caught, or a stale/oversized/malformed datagram is rejected. The last two
(tests 9-10) go over a REAL UDP socket on loopback, because "does the
listener survive a junk packet from something else on the LAN" is a claim
about actual socket behaviour (recv-then-loop, not recv-then-return) that a
direct call to `parse_beacon` cannot exercise on its own.

MUTATION PROOFS (see this task's own report for the verbatim before/after
output): three specific protections are proven load-bearing by temporarily
breaking the exact thing they guard and watching the corresponding test go
RED, then restoring it and watching it go GREEN again --

    1. `test_tampering_with_the_url_after_signing_is_rejected` -- proven by
       temporarily excluding `url` from both the signed payload
       (`build_beacon`) and the verified payload (`parse_beacon`'s
       `signable`), consistently on both sides (so tests 1/2 stay green and
       only the URL-tamper protection itself is removed).
    2. `test_a_datagram_over_the_size_cap_is_rejected_without_parsing` --
       proven by temporarily removing `parse_beacon`'s size-cap check.
    3. `test_an_invalid_datagram_does_not_stop_the_listener` -- proven by
       temporarily changing `listen_for_beacon`'s `except BeaconError:
       continue` to `except BeaconError: return None`.

Run:
    /c/Users/MSI/Desktop/aura-fullsuits/.venv/Scripts/python.exe -m pytest \\
        commercial_runtime/sync/tests/test_site_relay_beacon.py -q --color=no
"""
from __future__ import annotations

import base64
import json
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from commercial_runtime.sync.site_relay import beacon
from commercial_runtime.sync.site_relay.beacon import (
    BEACON_MAX_AGE_SECONDS,
    BEACON_MAX_DATAGRAM_BYTES,
    REQUIRED_BEACON_FIELDS,
    BeaconBroadcaster,
    BeaconError,
    build_beacon,
    listen_for_beacon,
    parse_beacon,
)

HUB_URL = "https://10.0.0.5:8743"
HUB_PIN = "3q2+7w=="  # a plausible-looking base64 SPKI pin; its exact bytes are never inspected by this suite


class _Ed25519Signer:
    """The `DeviceSigner` protocol `build_beacon` (and, elsewhere,
    `SyncRelayClient`) expects: `sign(canonical_bytes: bytes) -> str`
    (base64). Same shape as `test_site_relay_end_to_end.py`'s signer of the
    same name -- real Ed25519 over real canonical bytes, the one part of the
    device-identity stack that actually matters for whether a signature
    verifies. Only the key STORAGE differs (in-memory here, DPAPI on a
    shipped till), which is irrelevant to this suite."""

    def __init__(self, private_key: Ed25519PrivateKey):
        self._private_key = private_key

    def sign(self, canonical_bytes: bytes) -> str:
        return base64.b64encode(self._private_key.sign(canonical_bytes)).decode("ascii")

    def public_key_b64(self) -> str:
        raw = self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")


def _signer() -> _Ed25519Signer:
    return _Ed25519Signer(Ed25519PrivateKey.generate())


def _free_udp_port() -> int:
    """Binds an ephemeral UDP port, reads it back, and releases it
    immediately -- so tests 9/10 below use a real, available port instead of
    the real `BEACON_PORT`, and parallel test runs (this repo's CI shards
    tests across processes -- see ROADMAP.md/CLAUDE.md's CI notes) cannot
    collide on one fixed port number. The tiny window between releasing the
    probe socket and the caller binding the same port is the standard,
    accepted risk of this technique -- the alternative (holding the socket
    open and having `listen_for_beacon` reuse the same fd) is not possible
    here, since `listen_for_beacon` opens and binds its own socket
    internally rather than accepting one."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]
    finally:
        probe.close()


class _RecordingSocket:
    """A fake in place of a real `socket.socket`, for
    `test_base_url_fn_is_re_read_on_every_broadcast` below. Records every
    datagram `BeaconBroadcaster._run` hands to `sendto` instead of actually
    putting them on a wire -- this test's whole point is proving the
    CALLABLE is re-invoked per tick, which needs no real network at all, and
    a real broadcast send can legitimately fail or be filtered in a sandboxed
    CI network namespace for reasons that have nothing to do with what this
    test checks."""

    def __init__(self):
        self.sent: list[bytes] = []

    def setsockopt(self, *_args, **_kwargs) -> None:
        pass

    def sendto(self, data: bytes, _addr) -> None:
        self.sent.append(data)

    def close(self) -> None:
        pass


# ── 1. Round trip ────────────────────────────────────────────────────────────

def test_round_trip_returns_url_pin_and_installation_id():
    signer = _signer()
    datagram = build_beacon(
        installation_id="hub-1", base_url=HUB_URL, spki_pin=HUB_PIN, signer=signer,
    )

    result = parse_beacon(datagram, expected_device_public_key=signer.public_key_b64())

    assert result["installation_id"] == "hub-1"
    assert result["url"] == HUB_URL
    assert result["spki_pin"] == HUB_PIN


# ── 2. Wrong key ─────────────────────────────────────────────────────────────

def test_a_beacon_signed_by_a_different_key_is_rejected():
    real_signer = _signer()
    other_signer = _signer()
    datagram = build_beacon(
        installation_id="hub-1", base_url=HUB_URL, spki_pin=HUB_PIN, signer=real_signer,
    )

    with pytest.raises(BeaconError) as excinfo:
        parse_beacon(datagram, expected_device_public_key=other_signer.public_key_b64())
    assert excinfo.value.reason_code == "INVALID_SIGNATURE"


# ── 3. URL tampering -- the attack the signature exists to stop ─────────────

def test_tampering_with_the_url_after_signing_is_rejected():
    """Requirement 2's whole point: the URL is signed, so flipping it after
    the fact (keeping the original signature) must not verify. Mutation-
    proved: see the module docstring's "MUTATION PROOFS" section, item 1."""
    signer = _signer()
    datagram = build_beacon(
        installation_id="hub-1", base_url=HUB_URL, spki_pin=HUB_PIN, signer=signer,
    )

    body = json.loads(datagram.decode("utf-8"))
    body["url"] = "https://attacker.example:9999"
    tampered = json.dumps(body).encode("utf-8")

    with pytest.raises(BeaconError) as excinfo:
        parse_beacon(tampered, expected_device_public_key=signer.public_key_b64())
    assert excinfo.value.reason_code == "INVALID_SIGNATURE"


# ── 4. Staleness ─────────────────────────────────────────────────────────────

def test_a_beacon_older_than_max_age_is_rejected():
    signer = _signer()
    minted_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    datagram = build_beacon(
        installation_id="hub-1", base_url=HUB_URL, spki_pin=HUB_PIN, signer=signer, now=minted_at,
    )
    too_late = minted_at + timedelta(seconds=BEACON_MAX_AGE_SECONDS + 1)

    with pytest.raises(BeaconError) as excinfo:
        parse_beacon(datagram, expected_device_public_key=signer.public_key_b64(), now=too_late)
    assert excinfo.value.reason_code == "BEACON_STALE"


def test_a_beacon_still_within_max_age_is_accepted():
    """The other direction of test 4 -- a boundary this close is worth
    proving accepts, not just proving the far side rejects (ENGINEERING.md's
    "prove both directions of anything that both denies and allows")."""
    signer = _signer()
    minted_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    datagram = build_beacon(
        installation_id="hub-1", base_url=HUB_URL, spki_pin=HUB_PIN, signer=signer, now=minted_at,
    )
    just_in_time = minted_at + timedelta(seconds=BEACON_MAX_AGE_SECONDS - 1)

    result = parse_beacon(datagram, expected_device_public_key=signer.public_key_b64(), now=just_in_time)
    assert result["url"] == HUB_URL


# ── 5. Size cap, enforced before parsing ─────────────────────────────────────

def test_a_datagram_over_the_size_cap_is_rejected_without_parsing():
    """A LEGITIMATELY signed beacon that is simply too large -- not
    malformed JSON, not a bad signature. If the size cap were not enforced
    (or were enforced after signature verification), this exact datagram
    would otherwise verify successfully, which is what makes it a real test
    of the cap rather than of something else. Mutation-proved: see the
    module docstring's "MUTATION PROOFS" section, item 2."""
    signer = _signer()
    oversized_url = HUB_URL + "/" + ("x" * 2000)
    datagram = build_beacon(
        installation_id="hub-1", base_url=oversized_url, spki_pin=HUB_PIN, signer=signer,
    )
    assert len(datagram) > BEACON_MAX_DATAGRAM_BYTES

    with pytest.raises(BeaconError) as excinfo:
        parse_beacon(datagram, expected_device_public_key=signer.public_key_b64())
    assert excinfo.value.reason_code == "BEACON_TOO_LARGE"


# ── 6. Malformed JSON, and each required field missing ──────────────────────

def test_malformed_json_is_rejected_with_a_reason_code_not_an_exception():
    signer = _signer()
    with pytest.raises(BeaconError) as excinfo:
        parse_beacon(b"{this is not json", expected_device_public_key=signer.public_key_b64())
    assert excinfo.value.reason_code == "MALFORMED_BEACON"


def test_a_json_array_instead_of_an_object_is_rejected():
    signer = _signer()
    with pytest.raises(BeaconError) as excinfo:
        parse_beacon(b"[1, 2, 3]", expected_device_public_key=signer.public_key_b64())
    assert excinfo.value.reason_code == "MALFORMED_BEACON"


@pytest.mark.parametrize("missing_field", REQUIRED_BEACON_FIELDS)
def test_each_missing_required_field_is_rejected(missing_field):
    signer = _signer()
    datagram = build_beacon(
        installation_id="hub-1", base_url=HUB_URL, spki_pin=HUB_PIN, signer=signer,
    )
    body = json.loads(datagram.decode("utf-8"))
    del body[missing_field]
    mutated = json.dumps(body).encode("utf-8")

    with pytest.raises(BeaconError) as excinfo:
        parse_beacon(mutated, expected_device_public_key=signer.public_key_b64())
    assert excinfo.value.reason_code == "INVALID_BEACON"


# ── 7. installation_id mismatch ──────────────────────────────────────────────

def test_expected_installation_id_mismatch_is_rejected():
    signer = _signer()
    datagram = build_beacon(
        installation_id="hub-1", base_url=HUB_URL, spki_pin=HUB_PIN, signer=signer,
    )

    with pytest.raises(BeaconError) as excinfo:
        parse_beacon(
            datagram, expected_device_public_key=signer.public_key_b64(),
            expected_installation_id="a-different-hub",
        )
    assert excinfo.value.reason_code == "INSTALLATION_MISMATCH"


def test_expected_installation_id_match_is_accepted():
    """Allow-half of test 7 -- a correct id must not be refused."""
    signer = _signer()
    datagram = build_beacon(
        installation_id="hub-1", base_url=HUB_URL, spki_pin=HUB_PIN, signer=signer,
    )

    result = parse_beacon(
        datagram, expected_device_public_key=signer.public_key_b64(),
        expected_installation_id="hub-1",
    )
    assert result["installation_id"] == "hub-1"


# ── 8. base_url_fn is re-read on every broadcast, not captured once ─────────

def test_base_url_fn_is_re_read_on_every_broadcast(monkeypatch):
    """Requirement 5, exercised against the real `BeaconBroadcaster`, not
    just against `build_beacon` called twice by hand (which would prove
    nothing about whether the BROADCASTER re-reads the callable -- only that
    two different literal arguments produce two different beacons, which is
    true of any function). `socket.socket` is replaced with a recording fake
    so this test needs no real network and cannot be flaky in a sandboxed CI
    network namespace; the property under test -- was the callable invoked
    again on the second tick -- has nothing to do with whether a real UDP
    broadcast reaches anywhere."""
    signer = _signer()
    fake_socket = _RecordingSocket()
    monkeypatch.setattr(beacon.socket, "socket", lambda *a, **k: fake_socket)

    call_count = {"n": 0}

    def base_url_fn() -> str:
        call_count["n"] += 1
        return f"https://10.0.0.{call_count['n']}:8743"

    broadcaster = BeaconBroadcaster(
        installation_id="hub-1", base_url_fn=base_url_fn, spki_pin=HUB_PIN,
        signer=signer, interval_seconds=0.01,
    )
    broadcaster.start()
    try:
        deadline = time.monotonic() + 2.0
        while len(fake_socket.sent) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        broadcaster.stop()

    assert len(fake_socket.sent) >= 2, "broadcaster did not send at least two beacons in time"
    first = parse_beacon(fake_socket.sent[0], expected_device_public_key=signer.public_key_b64())
    second = parse_beacon(fake_socket.sent[1], expected_device_public_key=signer.public_key_b64())

    assert first["url"] == "https://10.0.0.1:8743"
    assert second["url"] == "https://10.0.0.2:8743"
    assert first["url"] != second["url"], "base_url_fn must be re-read per broadcast, not cached"


# ── 9. listen_for_beacon: timeout with nothing broadcasting ─────────────────

def test_listen_for_beacon_returns_none_on_timeout_when_nothing_broadcasts():
    port = _free_udp_port()
    signer = _signer()

    result = listen_for_beacon(
        expected_device_public_key=signer.public_key_b64(), timeout_seconds=0.3, port=port,
    )
    assert result is None


# ── 10. listen_for_beacon: an invalid datagram must not end the wait ────────

def test_an_invalid_datagram_does_not_stop_the_listener():
    """Requirement 7/the design's single most likely real-world failure: one
    junk UDP packet from anything else on the LAN must not deny service to
    the real beacon for the rest of the timeout window. Run over a REAL
    socket on loopback -- this is exactly the claim a direct call to
    `parse_beacon` cannot make on its own. Mutation-proved: see the module
    docstring's "MUTATION PROOFS" section, item 3."""
    port = _free_udp_port()
    signer = _signer()
    valid_datagram = build_beacon(
        installation_id="hub-1", base_url=HUB_URL, spki_pin=HUB_PIN, signer=signer,
    )

    outcome: dict = {}

    def _run_listener() -> None:
        outcome["result"] = listen_for_beacon(
            expected_device_public_key=signer.public_key_b64(), timeout_seconds=5.0, port=port,
        )

    listener_thread = threading.Thread(target=_run_listener, daemon=True)
    listener_thread.start()
    time.sleep(0.2)  # give the listener time to actually bind before we send anything

    sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sender.sendto(b"junk, not a beacon at all", ("127.0.0.1", port))
        time.sleep(0.1)
        sender.sendto(valid_datagram, ("127.0.0.1", port))
    finally:
        sender.close()

    listener_thread.join(timeout=6.0)
    assert not listener_thread.is_alive(), "listen_for_beacon did not return in time"
    result = outcome.get("result")
    assert result is not None, "the valid beacon sent after the junk packet was never returned"
    assert result["url"] == HUB_URL
