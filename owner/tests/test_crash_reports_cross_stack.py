"""Crash reports -- the REAL product sender against the REAL Owner route.

Why this file exists (same reason as test_release_update_cross_stack.py): each
half was built against a stand-in for the other. The product's tests use a fake
client; Owner's tests hand-build the request body. Both can be green while the
two sides disagree about the one thing that matters -- whether a batch the
product ACTUALLY builds (field names, timestamp format, canonical bytes of the
signature, the frames shape) is one Owner ACTUALLY accepts, and whether what
lands in Owner's database is free of customer data.

So this drives commercial_runtime's CrashStore -> capture -> CrashReportSender ->
LicensingClient through Owner's Flask app, with nothing faked except the network
hop (a requests-shaped adapter over the Flask test client).

PRIVACY is asserted on what Owner actually STORED: every column of every
owner_crash_reports row is serialised and searched for the seeded customer
name, the sale amount, the licence key and the home path.

Lives under owner/tests because it needs Owner's database fixtures; it imports
commercial_runtime from the repo root, which is the only coupling.
"""
from __future__ import annotations

import json as jsonlib
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

from flask import Flask
from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from commercial_runtime.crash_reports import capture  # noqa: E402
from commercial_runtime.crash_reports.sender import CrashReportSender  # noqa: E402
from commercial_runtime.crash_reports.store import CrashStore  # noqa: E402
from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig  # noqa: E402

from app.extensions import db_session  # noqa: E402
from app.models.crash_reports import CrashReport  # noqa: E402
from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff  # noqa: E402

BASE = "http://owner.test/api/licensing/v1"

CUSTOMER = "Ahmad Al-Khalidi"
AMOUNT = "12345.678"
LICENCE_KEY = "AURA-RETAIL-7K2M-QX9A-ZZPL"
HOME = r"C:\Users\bob smith\AppData\Roaming\Aura Retail\retail.db"


class _Response:
    def __init__(self, flask_response):
        self.status_code = flask_response.status_code
        self.headers = dict(flask_response.headers)
        self._r = flask_response

    def json(self):
        return self._r.get_json()


class _FlaskSession:
    """requests.Session-shaped: only .request() is used by LicensingClient."""

    def __init__(self, client):
        self._client = client
        self.calls = []

    def request(self, method, url, json=None, **_kwargs):
        assert url.startswith(BASE), url
        path = "/api/licensing/v1" + url[len(BASE):]
        self.calls.append((method, path, json))
        return _Response(self._client.open(path, method=method, data=jsonlib.dumps(json), content_type="application/json"))


class _Signer:
    def __init__(self, private_key):
        self._key = private_key

    def sign(self, canonical_bytes: bytes) -> str:
        import base64

        return base64.b64encode(self._key.sign(canonical_bytes)).decode("ascii")


def _activate(app, client):
    actor_id = make_staff(app, "xstack-crash@example.com")
    _license_id, full_key = make_license(app, actor_id, product_code="AURA_CLINIC")
    private_key = make_device_keypair()
    resp = client.post(
        "/api/licensing/v1/activations",
        data=jsonlib.dumps(build_activation_body(private_key, full_key=full_key, installation_id="xstack-crash-dev", product_code="AURA_CLINIC")),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["installation_id"], private_key


def _crash_on_the_till(store):
    """A real Flask app on the 'till' with capture installed and a route that
    raises with every kind of text a real bug puts in a message."""
    till = Flask(__name__)
    till.config["PROPAGATE_EXCEPTIONS"] = False

    @till.route("/api/sub/retail/sales/<int:sale_id>")
    def close_sale(sale_id):
        customer, total = CUSTOMER, AMOUNT           # locals: must not travel either
        raise ValueError("cannot close sale for '%s' total %s key %s file %s mail ahmad@shop.example"
                         % (customer, total, LICENCE_KEY, HOME))

    handle = capture.install(till, store, app_version="1.0.0-rc.8", platform="WINDOWS")
    try:
        assert till.test_client().get("/api/sub/retail/sales/987654").status_code == 500
        assert till.test_client().get("/api/sub/retail/sales/987655").status_code == 500
    finally:
        handle.remove()


def _sender(store, session, installation_id, private_key):
    return CrashReportSender(
        store,
        lambda: LicensingClient(LicensingClientConfig(base_url=BASE, max_retries=0), session=session),
        lambda: _Signer(private_key),
        lambda: SimpleNamespace(load=lambda: SimpleNamespace(owner_installation_id=installation_id)),
        "AURA_RETAIL", "1.0.0-rc.8", "WINDOWS",
    )


def _owner_rows(app):
    with app.app_context():
        return [
            {c.name: str(getattr(r, c.name)) for c in CrashReport.__table__.columns}
            for r in db_session.execute(select(CrashReport)).scalars().all()
        ]


def _mark_all_pending_again(store_path):
    """Simulates 'Owner stored the batch but the response was lost': the till
    still believes the rows are unsent."""
    conn = sqlite3.connect(str(store_path))
    conn.execute("UPDATE crash_outbox SET state = 'pending'")
    conn.commit()
    conn.close()


def test_a_real_crash_travels_from_the_till_to_owner_scrubbed_and_never_double_counts(app, client, seeded, signing_key, tmp_path):
    installation_id, private_key = _activate(app, client)
    store = CrashStore(tmp_path / "crash_reports.db")
    _crash_on_the_till(store)
    assert store.summary()["pending"] == 1, "two crashes of the same bug on the till are ONE row with count 2"
    assert store.all_rows()[0]["count"] == 2

    session = _FlaskSession(client)
    sender = _sender(store, session, installation_id, private_key)

    assert sender.run_once() == "SENT"
    assert [(m, p) for m, p, _ in session.calls] == [("POST", "/api/licensing/v1/crash-reports")], "the real client made exactly one call"

    rows = _owner_rows(app)
    assert len(rows) == 1
    row = rows[0]
    assert row["count"] == "2" and row["kind"] == "server" and row["exception_type"] == "ValueError"
    assert row["route"] == "/api/sub/retail/sales/<int:sale_id>"
    assert row["app_version"] == "1.0.0-rc.8" and row["platform"] == "WINDOWS" and row["product_code"] == "AURA_CLINIC"
    assert row["installation_id"] == installation_id
    # the scrubbed message is what Owner holds
    assert row["message"] == "cannot close sale for '…' total <n>.678 key <key> file <path> mail <email>"
    assert "close_sale" in row["frames"]

    # PRIVACY, on what Owner actually stored: every column of every row.
    everything = jsonlib.dumps(rows, ensure_ascii=False)
    for forbidden in (CUSTOMER, "Khalidi", AMOUNT, "12345", "987654", LICENCE_KEY, "7K2M", "ZZPL", "bob", "smith",
                      "AppData", "retail.db", "ahmad", "shop.example", str(tmp_path)):
        assert forbidden not in everything, "%r reached Owner's database" % forbidden
    # ... and on the wire body the real client posted
    wire = jsonlib.dumps(session.calls[0][2], ensure_ascii=False)
    for forbidden in (CUSTOMER, AMOUNT, LICENCE_KEY, "bob", "smith", "AppData"):
        assert forbidden not in wire, "%r was on the wire" % forbidden

    # The till marked it sent; nothing is left to send.
    assert store.summary()["pending"] == 0 and sender.run_once() == "NOTHING_PENDING"

    # The response was 'lost': the till sends the SAME running total again.
    _mark_all_pending_again(store.path)
    assert sender.run_once() == "SENT"
    assert len(session.calls) == 2, "the second send really went out"
    rows = _owner_rows(app)
    assert len(rows) == 1 and rows[0]["count"] == "2", "a re-sent batch must not double count"

    # A genuine third occurrence moves Owner's count to 3 (the running total), not to 2 + 3.
    _crash_on_the_till_once_more(store)
    assert sender.run_once() == "SENT"
    rows = _owner_rows(app)
    assert len(rows) == 1 and rows[0]["count"] == "3"


def _crash_on_the_till_once_more(store):
    till = Flask(__name__)
    till.config["PROPAGATE_EXCEPTIONS"] = False

    @till.route("/api/sub/retail/sales/<int:sale_id>")
    def close_sale(sale_id):
        customer, total = CUSTOMER, AMOUNT
        raise ValueError("cannot close sale for '%s' total %s key %s file %s mail ahmad@shop.example"
                         % (customer, total, LICENCE_KEY, HOME))

    handle = capture.install(till, store, app_version="1.0.0-rc.8", platform="WINDOWS")
    try:
        assert till.test_client().get("/api/sub/retail/sales/1").status_code == 500
    finally:
        handle.remove()
