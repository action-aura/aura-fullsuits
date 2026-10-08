"""The crash-report sender: when it stays quiet, what it sends, what it NEVER sends.

The privacy tests assert on the SERIALIZED payload (what would be on the wire),
not on the helper that builds it -- a scrubber that is correct but bypassed
would pass a unit test and still leak. Each of those tests also asserts a
positive control (the exception type and the scrubbed placeholders ARE in the
payload) so an empty payload cannot make "contains no customer name" pass.
"""
import json
import os
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

from commercial_runtime.crash_reports import capture, sender as sender_module
from commercial_runtime.crash_reports.sender import (
    CrashReportSender,
    build_crash_report_sender,
)
from commercial_runtime.crash_reports.store import CrashStore
from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig, NetworkError

CUSTOMER = "Ahmad Al-Khalidi"
AMOUNT = "12345.678"
LICENCE_KEY = "AURA-RETAIL-7K2M-QX9A-ZZPL"
HOME_WIN = r"C:\Users\bob smith\AppData\Roaming\Aura Retail\retail.db"
ALLOWED_FIELDS = {
    "fingerprint", "kind", "exception_type", "message", "frames", "route", "app_version", "platform",
    "first_seen_at", "last_seen_at", "count",
}


class FakeClient:
    def __init__(self, response=None, exc=None, on_send=None):
        self.calls = []
        self.response = {"result": "ACCEPTED", "accepted": 0} if response is None else response
        self.exc = exc
        self.on_send = on_send

    def send_crash_reports(self, **kwargs):
        self.calls.append(kwargs)
        if self.on_send:
            self.on_send(kwargs)
        if self.exc:
            raise self.exc
        return self.response


class Counting:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.value


def activated(installation_id="inst-1"):
    return lambda: SimpleNamespace(load=lambda: SimpleNamespace(owner_installation_id=installation_id))


@pytest.fixture
def store(tmp_path):
    return CrashStore(tmp_path / "crash.db")


def make_sender(store, client, *, state=None, **kw):
    clients = Counting(client)
    signers = Counting(object())
    s = CrashReportSender(store, clients, signers, state or activated(), "AURA_RETAIL", "1.2.3", "WINDOWS", **kw)
    return s, clients, signers


def seed(store, n=1, **over):
    for i in range(n):
        report = {
            "fingerprint": ("%032x" % (i + 1)), "kind": "server", "exception_type": "ValueError", "message": "boom %d" % i,
            "frames": [{"file": "x.py", "function": "f", "line": 1}], "route": "/r", "app_version": "1.2.3",
            "platform": "WINDOWS",
        }
        report.update(over)
        store.record(report)


# -- when it stays quiet ------------------------------------------------------
def test_with_no_owner_url_no_sender_is_built_and_the_client_factory_is_never_called(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(sender_module, "LicensingClient", lambda *a, **k: made.append(1))
    store = CrashStore(tmp_path / "crash.db")
    seed(store)
    got = build_crash_report_sender(
        store=store, app_data_dir=str(tmp_path), owner_base_url="", verify_tls=True, timeout_seconds=5,
        device_identity_factory=lambda d: object(), product_code="AURA_RETAIL", app_version="1.2.3",
        platform="WINDOWS")
    assert got is None
    assert made == [], "no Owner URL means no client is ever built"
    assert store.pending(10), "capture stays local: the row is still waiting"


def test_with_an_owner_url_but_no_activation_the_client_is_still_never_built(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(sender_module, "LicensingClient", lambda *a, **k: made.append(1))
    store = CrashStore(tmp_path / "crash.db")
    seed(store)
    built = build_crash_report_sender(
        store=store, app_data_dir=str(tmp_path), owner_base_url="https://owner.invalid/api/licensing/v1",
        verify_tls=True, timeout_seconds=5, device_identity_factory=lambda d: object(), product_code="AURA_RETAIL",
        app_version="1.2.3", platform="WINDOWS")
    assert built is not None
    assert built.run_once() == "NOT_ACTIVATED"
    assert made == []


def test_not_activated_sends_nothing(store):
    seed(store)
    s, clients, signers = make_sender(store, FakeClient(), state=activated(None))
    assert s.run_once() == "NOT_ACTIVATED"
    assert clients.calls == 0 and signers.calls == 0
    assert len(store.pending(10)) == 1


def test_a_licence_state_that_raises_counts_as_not_activated(store):
    seed(store)

    def boom():
        raise RuntimeError("db locked")

    s, clients, _ = make_sender(store, FakeClient(), state=lambda: SimpleNamespace(load=boom))
    assert s.run_once() == "NOT_ACTIVATED"
    assert clients.calls == 0


def test_nothing_pending_makes_no_call(store):
    s, clients, _ = make_sender(store, FakeClient())
    assert s.run_once() == "NOTHING_PENDING"
    assert clients.calls == 0


# -- success / failure --------------------------------------------------------
def test_accepted_marks_rows_sent_and_sends_at_most_twenty(store):
    seed(store, n=25)
    client = FakeClient()
    s, clients, signers = make_sender(store, client)
    assert s.run_once() == "SENT"
    assert clients.calls == 1 and signers.calls == 1
    call = client.calls[0]
    assert call["installation_id"] == "inst-1" and call["app_version"] == "1.2.3" and call["platform"] == "WINDOWS"
    assert len(call["reports"]) == 20
    summary = store.summary()
    assert summary["sent"] == 20 and summary["pending"] == 5
    sent = [r for r in store.all_rows() if r["state"] == "sent"]
    assert all(r["sent_count"] == r["count"] == 1 for r in sent)
    # the next run picks up the rest
    assert s.run_once() == "SENT"
    assert store.summary() == {**store.summary(), "pending": 0, "sent": 25}


def test_a_transport_failure_leaves_rows_pending_and_doubles_the_wait_up_to_six_hours(store):
    seed(store)
    client = FakeClient(exc=NetworkError("NETWORK_UNAVAILABLE", "down"))
    s, _, _ = make_sender(store, client, interval=1800)
    waits = []
    for _ in range(6):
        assert s.run_once() == "SEND_FAILED"
        waits.append(s.interval)
    assert len(client.calls) == 6, "the send must actually have been attempted each time"
    assert waits == [3600, 7200, 14400, 21600, 21600, 21600]
    assert len(store.pending(10)) == 1 and store.summary()["sent"] == 0


def test_an_owner_refusal_is_not_success(store):
    seed(store)
    client = FakeClient(response={"result": "FAILURE", "reason_code": "INVALID_SIGNATURE"})
    s, _, _ = make_sender(store, client, interval=1800)
    assert s.run_once() == "REFUSED"
    assert s.interval == 3600
    assert len(store.pending(10)) == 1, "a refused batch must stay pending"


def test_a_good_run_resets_the_wait(store):
    seed(store)
    client = FakeClient(exc=NetworkError("NETWORK_UNAVAILABLE", "down"))
    s, _, _ = make_sender(store, client, interval=1800)
    s.run_once()
    assert s.interval == 3600
    client.exc = None
    assert s.run_once() == "SENT"
    assert s.interval == 1800


def test_a_crash_that_repeats_while_the_batch_is_in_flight_stays_pending(store):
    seed(store)
    client = FakeClient(on_send=lambda kwargs: seed(store))      # same crash happens again mid-send
    s, _, _ = make_sender(store, client)
    assert s.run_once() == "SENT"
    row = store.all_rows()[0]
    assert row["count"] == 2 and row["state"] == "pending", "the newer total is still owed to Owner"
    assert client.calls[0]["reports"][0]["count"] == 1


def test_a_batch_is_split_to_stay_under_the_owner_body_limit(store):
    big_frames = [{"file": "f" * 190, "function": "g" * 190, "line": i} for i in range(30)]
    seed(store, n=20, frames=big_frames, message="m" * 300)
    client = FakeClient()
    s, _, _ = make_sender(store, client)
    s.run_once()
    sent_now = client.calls[0]["reports"]
    wire = json.dumps(sent_now, ensure_ascii=False).encode("utf-8")
    assert 0 < len(sent_now) < 20, "20 maximal reports cannot fit one request"
    # literal 40 KB: Owner refuses 64 KB bodies, and the envelope + signature need room
    assert len(wire) <= 40 * 1024
    # nothing is lost: repeated runs deliver every row
    for _ in range(25):
        if s.run_once() == "NOTHING_PENDING":
            break
    assert store.summary()["pending"] == 0 and store.summary()["sent"] == 20


def test_the_loop_runs_once_per_wait_and_stop_ends_it(store):
    seed(store)
    ran = threading.Event()
    client = FakeClient(on_send=lambda kwargs: ran.set())
    s, _, _ = make_sender(store, client, first_delay=0.01, interval=0.01)
    s.start()
    assert ran.wait(5), "the background loop never ran"
    s.stop()


# -- privacy: the serialized payload -----------------------------------------
def _crashing_app(store):
    app = Flask(__name__)
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.route("/api/sub/retail/sales/<int:sale_id>")
    def sale(sale_id):
        customer = CUSTOMER          # a local variable: must not travel
        total = AMOUNT
        raise ValueError("cannot close sale for '%s' total %s key %s file %s mail ahmad@shop.example" %
                         (customer, total, LICENCE_KEY, HOME_WIN))

    handle = capture.install(app, store, app_version="1.2.3", platform="WINDOWS")
    return app, handle


def test_a_real_crash_leaves_the_till_without_customer_amount_key_or_home_path(store):
    app, handle = _crashing_app(store)
    try:
        app.test_client().get("/api/sub/retail/sales/987654")
    finally:
        handle.remove()
    client = FakeClient()
    s, _, _ = make_sender(store, client)
    assert s.run_once() == "SENT"

    payload = client.calls[0]
    wire = json.dumps(payload["reports"], ensure_ascii=False)
    # positive controls: this really is the crash we caused
    assert '"exception_type": "ValueError"' in wire
    assert "<n>" in wire and "<key>" in wire and "<email>" in wire
    assert "/api/sub/retail/sales/<int:sale_id>" in wire
    for forbidden in (CUSTOMER, "Khalidi", AMOUNT, "12345", "987654", LICENCE_KEY, "7K2M", "ZZPL", "bob", "smith",
                      "AppData", "retail.db", "ahmad", "shop.example", os.path.expanduser("~")):
        assert forbidden not in wire, "%r leaked into the crash payload" % forbidden
    assert set(payload["reports"][0]) == ALLOWED_FIELDS


def test_a_poisoned_row_in_the_store_is_cleaned_again_before_it_is_sent(store):
    """The store should only hold scrubbed data -- but the sender does not trust
    that. A row with an unscrubbed message and an absolute-path frame (an older
    build's, or a hand-edited file) must still leave clean."""
    store.record({
        "fingerprint": "c" * 32, "kind": "server", "exception_type": "ValueError",
        "message": "owes %s to %s (%s)" % (AMOUNT, "ahmad@shop.example", HOME_WIN),
        "frames": [{"file": HOME_WIN, "function": "close_sale", "line": 5, "locals": {"customer": CUSTOMER}}],
        "route": "/r", "app_version": "1.2.3", "platform": "WINDOWS",
    })
    client = FakeClient()
    s, _, _ = make_sender(store, client)
    s.run_once()
    wire = json.dumps(client.calls[0]["reports"], ensure_ascii=False)
    assert "close_sale" in wire and "<email>" in wire                      # positive control
    for forbidden in (AMOUNT, "12345", "ahmad", "bob", "smith", "AppData", CUSTOMER, "locals"):
        assert forbidden not in wire, forbidden
    assert json.loads(wire)[0]["frames"] == [{"file": "retail.db", "function": "close_sale", "line": 5}]


def test_the_real_client_puts_only_the_documented_fields_on_the_wire_and_signs_them(store):
    seed(store, message="boom")
    posted = []

    class Session:
        def request(self, method, url, json=None, **kwargs):          # noqa: A002
            posted.append((method, url, json))
            return SimpleNamespace(status_code=200, headers={}, json=lambda: {"result": "ACCEPTED", "accepted": 1})

    signed = []

    class Signer:
        def sign(self, canonical_bytes):
            signed.append(canonical_bytes)
            return "sig"

    real = LicensingClient(LicensingClientConfig(base_url="https://owner.invalid/api/licensing/v1", max_retries=0),
                           session=Session())
    s = CrashReportSender(store, lambda: real, lambda: Signer(), activated(), "AURA_RETAIL", "1.2.3", "WINDOWS")
    assert s.run_once() == "SENT"
    (method, url, body), = posted
    assert method == "POST" and url == "https://owner.invalid/api/licensing/v1/crash-reports"
    assert set(body) == {"contract_version", "request_id", "correlation_id", "timestamp", "nonce", "installation_id",
                         "app_version", "platform", "reports", "signature"}
    assert body["installation_id"] == "inst-1" and body["signature"] == "sig"
    assert set(body["reports"][0]) == ALLOWED_FIELDS
    # the signature covers the body without the signature (what Owner re-derives)
    assert len(signed) == 1
    unsigned = {k: v for k, v in body.items() if k != "signature"}
    from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
    assert signed[0] == canonicalize_bytes(unsigned)
