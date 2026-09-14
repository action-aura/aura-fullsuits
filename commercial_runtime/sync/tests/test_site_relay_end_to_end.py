"""Two devices converging through a real LAN hub, over real TLS, with no
mocks anywhere in the path.

Every other site-relay test file exercises one layer against test doubles:
`test_site_relay_store.py` drives the DAO directly, `test_site_relay_routes.py`
drives the blueprint through Flask's test client (no socket, no TLS), and
`test_site_relay_pinning.py` proves the pin against a bare `http.server`. Each
is the right shape for what it guards, and all three can pass while the
assembled thing does not work at all -- the layers have never met.

So this file assembles the actual product:

    a real sqlite database, migrated by the real `_migrate_add_site_relay`
      -> the real Flask blueprint from `routes.py`
      -> the real threaded TLS listener from `listener.py`, on a real socket
      -> the real SPKI-pinned `requests` transport from `pinned_transport.py`
      -> the real `SyncRelayClient` from `commercial_runtime/sync/`, the same
         class the shipped desktop till uses against Owner's cloud relay,
         with its real Ed25519 signing, real canonicalization, real nonces

and makes two simulated devices converge through it. This repo's
ENGINEERING.md is blunt about why this file has to exist: "agent-written work
passes its own tests and is broken anyway... Reading code finds nothing.
Running it finds everything."

THE CLAIM THIS FILE IS HERE TO SUBSTANTIATE is the one the whole R-LAN design
rests on (`docs/launch-readiness/lan-restaurant-design.md` §3): a device joins
the LAN by CONFIGURATION ONLY -- point `SyncRelayClient` at the hub instead of
at Owner, and everything else (outbox, cursor, signing, idempotent apply) is
untouched. If that claim is false, the design's effort estimate is wrong and
the client needs real work. `test_the_shipped_client_reaches_the_hub_unmodified`
is the assertion that it is true: the client is constructed exactly as
`products/retail/backend/app.py::_build_sync_client` constructs it, with only
the base URL and the session differing.

ON `verify_tls=False` BELOW -- it is not a shortcut and it is not weaker than
the alternative. The hub's certificate is self-signed and its IP changes with
every DHCP lease, so ordinary chain-and-hostname verification cannot succeed
and must not be what we depend on. The pinned adapter replaces it with a
STRICTER check: the presented leaf's SubjectPublicKeyInfo must equal the exact
key learned out-of-band at pairing, or the connection raises. That is design
§5's "Layer 1 -- transport" verbatim: "clients pin the public key (SPKI) ...
never a 'trust any cert' mode". `test_a_wrong_pin_cannot_reach_the_hub` is
what stops that sentence from being decorative -- without it, nothing in this
suite would notice if the pinning silently degraded to trust-anything, which
is strictly worse than plain HTTP because it still looks encrypted.

Run:
    pytest commercial_runtime/sync/tests/test_site_relay_end_to_end.py -v
"""
from __future__ import annotations

import base64
import shutil
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SUITE_ROOT = Path(__file__).resolve().parents[3]
RETAIL_BACKEND = SUITE_ROOT / "products" / "retail" / "backend"
for _p in (str(SUITE_ROOT), str(RETAIL_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from commercial_runtime.sync.relay_client import RelayRejected, SyncRelayClient  # noqa: E402
from commercial_runtime.sync.site_relay import listener, store  # noqa: E402
from commercial_runtime.sync.site_relay.pinned_transport import (  # noqa: E402
    SpkiPinMismatch,
    pinned_session,
)

DESK = "11111111-1111-4111-8111-111111111111"
TABLET = "22222222-2222-4222-8222-222222222222"


class _Ed25519Signer:
    """The `DeviceSigner` protocol `SyncRelayClient` expects
    (`relay_client.py`: `def sign(self, canonical_bytes: bytes) -> str`).

    Real Ed25519 over the real canonical bytes, returning base64 -- the same
    contract `WindowsDpapiDeviceIdentityProvider.sign` fulfils on a shipped
    till. Only the key STORAGE differs (an in-memory key here, DPAPI there),
    which is the one part of the device-identity stack that is irrelevant to
    whether the relay verifies a signature correctly."""

    def __init__(self, private_key: Ed25519PrivateKey):
        self._private_key = private_key

    def sign(self, canonical_bytes: bytes) -> str:
        return base64.b64encode(self._private_key.sign(canonical_bytes)).decode("ascii")

    def public_key_b64(self) -> str:
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        raw = self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")


def _event(entity_type="product", event_type="create", **payload):
    """One event in the exact shape the cloud relay's `_build_event` accepts,
    because the hub speaks the identical wire contract -- that identity is the
    whole point of the port and is what lets the unmodified client talk to
    both."""
    return {
        "id": str(uuid.uuid4()),
        "entity_type": entity_type,
        "entity_id": str(uuid.uuid4()),
        "event_type": event_type,
        "payload": payload or {"name": "Widget"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture()
def hub():
    """A running hub: migrated database, TLS listener on an ephemeral port,
    both devices paired.

    `get_conn` opens a FRESH connection per call rather than sharing one.
    That is mandatory, not stylistic: the listener is threaded
    (`_ThreadingWSGIServer`), and a sqlite3 connection may not be used from a
    thread other than the one that created it. Sharing one would raise
    ProgrammingError under exactly the concurrent load this listener exists to
    serve -- and never under a single-threaded test, which is how that bug
    ships."""
    tmp = Path(tempfile.mkdtemp(prefix="aura_site_relay_e2e_"))
    db_path = tmp / "retail.db"

    from database.schema import _migrate_add_site_relay

    bootstrap = sqlite3.connect(db_path)
    bootstrap.row_factory = sqlite3.Row
    _migrate_add_site_relay(bootstrap)
    bootstrap.commit()

    desk_signer = _Ed25519Signer(Ed25519PrivateKey.generate())
    tablet_signer = _Ed25519Signer(Ed25519PrivateKey.generate())
    store.pair_device(bootstrap, DESK, device_public_key=desk_signer.public_key_b64(), label="Main till")
    store.pair_device(bootstrap, TABLET, device_public_key=tablet_signer.public_key_b64(), label="Tablet 1")
    bootstrap.commit()
    bootstrap.close()

    def get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    hub = listener.start_site_relay(
        get_conn=get_conn, host="127.0.0.1", port=0, identity_dir=tmp)
    pin = hub.pin
    base_url = f"https://127.0.0.1:{hub.port}"

    try:
        yield {
            "base_url": base_url, "pin": pin, "db_path": db_path, "get_conn": get_conn,
            "desk": desk_signer, "tablet": tablet_signer,
        }
    finally:
        hub.server.shutdown()
        hub.server.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


def _client(hub, installation_id, signer, *, pin=None):
    """Constructed exactly as `app.py::_build_sync_client` builds the shipped
    one, except for `base_url` (the hub instead of Owner) and `session` (the
    pinned transport instead of a default one). Nothing else differs -- that
    is the design claim under test."""
    return SyncRelayClient(
        hub["base_url"],
        signer,
        installation_id,
        timeout_seconds=10.0,
        verify_tls=False,  # the SPKI pin replaces chain verification -- see module docstring
        session=pinned_session(pin or hub["pin"]),
        max_retries=1,
    )


def test_the_shipped_client_reaches_the_hub_unmodified(hub):
    """The load-bearing claim of design §3: joining the LAN is configuration,
    not code. A real `SyncRelayClient` -- signing, canonicalizing and nonce-ing
    exactly as it does against Owner -- is accepted by the hub."""
    desk = _client(hub, DESK, hub["desk"])
    result = desk.push([_event(), _event()])
    assert result["stored"] == 2
    assert result["received"] == 2


def test_two_devices_converge_through_the_hub(hub):
    """The actual product behaviour a shop buys: what one till rings up, the
    other device sees -- with no internet anywhere in this test."""
    desk = _client(hub, DESK, hub["desk"])
    tablet = _client(hub, TABLET, hub["tablet"])

    sent = [_event(name="Coffee"), _event(name="Tea"), _event(name="Cake")]
    assert desk.push(sent)["stored"] == 3

    received = tablet.pull(since=0)
    assert [e["id"] for e in received["events"]] == [e["id"] for e in sent]
    assert received["cursor"] == 3

    # ...and back the other way, proving the hub is a relay rather than a
    # one-directional drain.
    reply = _event(name="Receipt printer setting")
    assert tablet.push([reply])["stored"] == 1
    assert [e["id"] for e in desk.pull(since=0)["events"]] == [reply["id"]]


def test_a_device_never_receives_its_own_events_back(hub):
    """Self-exclusion end to end. Without it a device would re-apply its own
    writes on every pull, and its cursor would advance over an ever-growing
    echo of itself."""
    desk = _client(hub, DESK, hub["desk"])
    desk.push([_event(), _event()])

    assert desk.pull(since=0)["events"] == []


def test_a_replayed_request_is_refused_over_the_real_transport(hub):
    """Replay protection surviving the whole stack, not just the unit test.

    The captured body is replayed by hand rather than through the client,
    because the client mints a fresh nonce per call and so cannot express a
    replay -- which is exactly the attack: someone who is NOT our client,
    resending bytes they captured on the shop's wifi."""
    desk = _client(hub, DESK, hub["desk"])
    body = desk._signed_body({"events": [_event()]})

    session = pinned_session(hub["pin"])
    url = hub["base_url"] + "/api/sync/v1/push"
    first = session.post(url, json=body, verify=False, timeout=10)
    second = session.post(url, json=body, verify=False, timeout=10)

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["reason_code"] == "NONCE_REUSED"


def test_an_unpaired_device_is_refused(hub):
    """A laptop that joins the cafe wifi holds no pairing, so it is refused
    even though it can reach the port and complete the TLS handshake."""
    stranger = _client(hub, str(uuid.uuid4()), _Ed25519Signer(Ed25519PrivateKey.generate()))

    with pytest.raises(RelayRejected) as excinfo:
        stranger.push([_event()])
    assert excinfo.value.reason_code == "INSTALLATION_NOT_FOUND"


def test_a_locally_revoked_device_is_refused_immediately(hub):
    """Design §5's answer to suspension latency: the fired employee's tablet
    is standing in the restaurant now, and the hub can refuse it without
    waiting for an Owner roster refresh that needs internet it may not have
    for days."""
    tablet = _client(hub, TABLET, hub["tablet"])
    assert tablet.push([_event()])["stored"] == 1

    conn = hub["get_conn"]()
    store.revoke_device(conn, TABLET)
    conn.commit()
    conn.close()

    with pytest.raises(RelayRejected) as excinfo:
        tablet.push([_event()])
    assert excinfo.value.reason_code == "INSTALLATION_REVOKED"


def test_a_wrong_pin_cannot_reach_the_hub(hub):
    """The transport half of the security story, and the test that stops
    design §5's "never a 'trust any cert' mode" from being decorative.

    A client holding the wrong key must fail to connect at all -- not fall
    back, not warn, not succeed. If pinning ever silently degraded to
    trust-anything, every other test in this file would still pass, because
    they all use the correct pin."""
    other_pin = base64.b64encode(b"\x00" * 32).decode("ascii")
    wrong = _client(hub, DESK, hub["desk"], pin=other_pin)

    # Catching broad `Exception` here is deliberate and is NOT the assertion:
    # requests/urllib3 wrap a connection-time failure in their own types, so
    # naming SpkiPinMismatch in the `raises` clause would pin the wrapper
    # rather than the cause. The real assertion is the chain walk below, which
    # fails the test if SpkiPinMismatch is not genuinely the reason.
    with pytest.raises(Exception) as excinfo:
        wrong.push([_event()])
    # Assert on the TYPE somewhere in the chain, never on message text: a
    # pin failure that surfaces only as a generic retryable transport error
    # is a real defect (a caller would retry past it), so the specific
    # exception must survive requests'/urllib3's wrapping layers.
    chain, seen = excinfo.value, []
    while chain is not None and chain not in seen:
        seen.append(chain)
        if isinstance(chain, SpkiPinMismatch):
            break
        chain = chain.__cause__ or chain.__context__
    else:
        pytest.fail(f"SpkiPinMismatch not found in the exception chain: {seen!r}")


def test_nothing_the_hub_rejected_was_written(hub):
    """Fail-closed, checked against the database rather than the response.

    A relay that answers 400 and stores the row anyway would pass every
    status-code assertion in this suite -- so the row count is what is
    asserted here."""
    desk = _client(hub, DESK, hub["desk"])
    desk.push([_event()])

    malformed = [_event(), {"id": str(uuid.uuid4()), "entity_type": "product"}]  # missing keys
    with pytest.raises(RelayRejected) as excinfo:
        desk.push(malformed)
    assert excinfo.value.reason_code == "INVALID_EVENT"

    conn = hub["get_conn"]()
    remaining = conn.execute("SELECT COUNT(*) FROM site_sync_events").fetchone()[0]
    conn.close()
    assert remaining == 1, "a rejected batch must leave nothing behind"
