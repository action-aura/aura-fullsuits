"""Keep-selling rule, LAN half: a hub whose OWN subscription lapsed unpaid stops serving sync data.

The hub's till keeps selling (the listener is already running when the lapse begins -- see
retail_site_relay_config_test.py). What it must not do is keep acting as the shop's sync relay for
free: while the HOST's licence pauses sync, the three routes that MOVE shop data refuse with
reason code SUBSCRIPTION_LAPSED, and nothing is stored or served.

ORDER matters and is pinned here: the check runs AFTER authenticate() succeeds. An unauthenticated
caller (stranger, bad signature, ...) must still get the AUTH error, never SUBSCRIPTION_LAPSED --
otherwise an anonymous probe could learn that this hub's owner has not paid (auth.py documents the
same "do not become an unauthenticated oracle" ordering for the revoke check).

Routes of the hub, and what is gated:
  /push       GATED   moves events INTO the site log
  /pull       GATED   serves events AND advances the device's cursor row
  /bootstrap  GATED   hands out a full copy of the shop's database
  /pair       not gated: enrolment of a device by one-time code, moves no shop data
  /join       not gated: licence-proven enrolment, moves no shop data
  /identity   not gated: public hub identity only (no shop data)
  beacon / roster / admin: not part of this blueprint's data path (UDP announcement, signed
              roster, loopback-only admin) and carry no event data.

The hub forwarder (the hub's separate door to Owner's cloud) honours the same pause; tested below.

Run (one file per process):
    pytest commercial_runtime/sync/tests/test_site_relay_licence_pause.py -q
"""
import pytest
from flask import Flask

from commercial_runtime.sync.site_relay.forwarder_loop import ForwarderLoop
from commercial_runtime.sync.site_relay.routes import make_site_relay_blueprint
from commercial_runtime.sync.tests.test_site_relay_routes import (  # noqa: F401
    PULL_PATH,
    PUSH_PATH,
    Device,
    _make_event,
    _pair,
    _row_count,
    db_path,
    get_conn,
)

BOOTSTRAP_PATH = "/api/sync/v1/bootstrap"


class _Switch:
    def __init__(self, paused):
        self.paused = paused
        self.reads = 0

    def __call__(self):
        self.reads += 1
        return self.paused


class _Provider:
    def __init__(self):
        self.calls = 0

    def __call__(self, installation_id, schema_version):
        self.calls += 1
        raise RuntimeError("must not be reached while lapsed")


@pytest.fixture
def switch():
    return _Switch(True)


@pytest.fixture
def provider():
    return _Provider()


@pytest.fixture
def client(get_conn, switch, provider):
    app = Flask(__name__)
    app.register_blueprint(make_site_relay_blueprint(
        get_conn=get_conn, sync_paused=switch, bootstrap_provider=provider))
    app.testing = True
    return app.test_client()


def test_lapsed_host_refuses_an_authenticated_push_and_stores_nothing(client, get_conn, switch):
    device = Device()
    _pair(get_conn, device)

    resp = client.post(PUSH_PATH, json=device.sign_body({"events": [_make_event(), _make_event()]}))

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "SUBSCRIPTION_LAPSED"
    assert switch.reads > 0, "the pause callable was never consulted -- the gate did not run"
    assert _row_count(get_conn, "site_sync_events") == 0, "a refused push still wrote to the site log"


def test_lapsed_host_refuses_an_authenticated_pull_serves_nothing_and_keeps_cursors_still(client, get_conn):
    pusher, puller = Device(), Device()
    _pair(get_conn, pusher)
    _pair(get_conn, puller)
    # Seed the log directly through the store so there IS something that could be served.
    from commercial_runtime.sync.site_relay import store
    conn = get_conn()
    store.append_events(conn, [_make_event()], origin_device_id=pusher.installation_id)
    conn.commit()
    conn.close()
    cursors_before = _row_count(get_conn, "site_device_cursors")

    resp = client.post(PULL_PATH, json=puller.sign_body({"since": 0}))

    assert resp.status_code == 400
    body = resp.get_json()
    assert body["reason_code"] == "SUBSCRIPTION_LAPSED"
    assert "events" not in body, "a lapsed hub still served events"
    assert _row_count(get_conn, "site_device_cursors") == cursors_before, \
        "a refused pull still advanced the device's cursor row"


def test_lapsed_host_refuses_bootstrap_without_building_a_copy(client, get_conn, provider):
    device = Device()
    _pair(get_conn, device)

    resp = client.post(BOOTSTRAP_PATH, json=device.sign_body({"stream": "retail"}))

    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "SUBSCRIPTION_LAPSED"
    assert provider.calls == 0, "a lapsed hub still built a database copy for a peer"


def test_an_unauthenticated_caller_still_gets_the_auth_error_not_the_lapse(client, get_conn):
    """ORDER: the lapse check must come AFTER authenticate(). Otherwise any stranger could learn
    that this shop has not paid."""
    stranger = Device()  # never paired
    resp = client.post(PUSH_PATH, json=stranger.sign_body({"events": []}))
    assert resp.status_code == 400
    assert resp.get_json()["reason_code"] == "INSTALLATION_NOT_FOUND"
    resp = client.post(PULL_PATH, json=stranger.sign_body({"since": 0}))
    assert resp.get_json()["reason_code"] == "INSTALLATION_NOT_FOUND"
    # And a paired device with a BAD signature gets the signature error, not the lapse.
    device = Device()
    _pair(get_conn, device)
    body = device.sign_body({"events": []})
    body["signature"] = stranger.sign_body({"events": []})["signature"]
    resp = client.post(PUSH_PATH, json=body)
    assert resp.get_json()["reason_code"] == "INVALID_SIGNATURE"


def test_active_host_serves_and_renewal_serves_again_without_a_restart(client, get_conn, switch):
    """The allow half. Same app object, flipped callable: served again, nothing rebuilt."""
    device_a, device_b = Device(), Device()
    _pair(get_conn, device_a)
    _pair(get_conn, device_b)
    event = _make_event()

    assert client.post(PUSH_PATH, json=device_a.sign_body({"events": [event]})).status_code == 400
    assert _row_count(get_conn, "site_sync_events") == 0

    switch.paused = False  # renewal
    pushed = client.post(PUSH_PATH, json=device_a.sign_body({"events": [event]}))
    assert pushed.status_code == 200 and pushed.get_json() == {"stored": 1, "received": 1}
    pulled = client.post(PULL_PATH, json=device_b.sign_body({"since": 0}))
    assert pulled.status_code == 200
    assert [e["id"] for e in pulled.get_json()["events"]] == [event["id"]]

    switch.paused = True  # lapses again -> refused again, same instance
    assert client.post(PULL_PATH, json=device_b.sign_body({"since": 0})).get_json()["reason_code"] \
        == "SUBSCRIPTION_LAPSED"


def test_a_blueprint_without_the_callable_never_pauses(get_conn):
    """sync_paused=None (every existing caller / test) is exactly the old behaviour."""
    app = Flask(__name__)
    app.register_blueprint(make_site_relay_blueprint(get_conn=get_conn))
    app.testing = True
    device = Device()
    _pair(get_conn, device)
    resp = app.test_client().post(PUSH_PATH, json=device.sign_body({"events": [_make_event()]}))
    assert resp.status_code == 200


def test_a_raising_pause_callable_fails_toward_serving(get_conn):
    def boom():
        raise RuntimeError("licence store unreadable")

    app = Flask(__name__)
    app.register_blueprint(make_site_relay_blueprint(get_conn=get_conn, sync_paused=boom))
    app.testing = True
    device = Device()
    _pair(get_conn, device)
    resp = app.test_client().post(PUSH_PATH, json=device.sign_body({"events": [_make_event()]}))
    assert resp.status_code == 200


# ── the hub forwarder (hub -> Owner cloud) honours the same pause ────────────────

class _CountingCloudFactory:
    def __init__(self):
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return None  # NO_CLOUD once it IS consulted -- enough to prove it was reached


def test_a_paused_forwarder_tick_contacts_nothing_and_records_neither_success_nor_failure(get_conn):
    factory = _CountingCloudFactory()
    switch = _Switch(True)
    loop = ForwarderLoop(get_conn=get_conn, cloud_client_factory=factory, sync_paused=switch)

    result = loop.tick()

    assert switch.reads > 0, "the forwarder never consulted the pause -- the gate did not run"
    assert factory.calls == 0, "a paused forwarder still asked for a cloud client"
    assert result["state"] == "PAUSED_BY_LICENCE"
    snap = loop.snapshot()
    assert snap["consecutive_failures"] == 0 and snap["last_failure_at"] is None
    assert snap["last_success_at"] is None


def test_the_forwarder_resumes_after_renewal_on_the_same_instance(get_conn):
    factory = _CountingCloudFactory()
    switch = _Switch(True)
    loop = ForwarderLoop(get_conn=get_conn, cloud_client_factory=factory, sync_paused=switch)
    loop.tick()
    assert factory.calls == 0
    switch.paused = False
    result = loop.tick()
    assert factory.calls == 1
    assert result["state"] == "NO_CLOUD"


def test_a_forwarder_without_the_callable_or_with_a_raising_one_is_never_paused(get_conn):
    factory = _CountingCloudFactory()
    ForwarderLoop(get_conn=get_conn, cloud_client_factory=factory).tick()
    assert factory.calls == 1

    def boom():
        raise RuntimeError("unreadable")

    ForwarderLoop(get_conn=get_conn, cloud_client_factory=factory, sync_paused=boom).tick()
    assert factory.calls == 2
