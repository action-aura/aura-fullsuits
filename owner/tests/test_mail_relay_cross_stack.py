"""Mail relay -- the REAL product worker and client against the REAL Owner route.

Why this file exists (same reason as test_crash_reports_cross_stack.py): each
half was built against a stand-in for the other. The product's tests use a fake
client; Owner's tests hand-build the request body. Both can be green while the
two sides disagree about the one thing that matters -- whether a message the
product ACTUALLY builds (field names, canonical bytes of the signature, the
parameter shapes derived from a real outbox row) is one Owner ACTUALLY accepts,
renders and delivers.

So this drives commercial_runtime's email_outbox -> EmailOutboxWorker ->
RelayTransport -> LicensingClient through Owner's Flask app into
owner_email_outbox, then Owner's EmailWorker through a real socket SMTP server
that implements STARTTLS + AUTH, and reads what that server received. Nothing
is faked except the network hop between the two apps (a requests-shaped adapter
over the Flask test client).
"""
from __future__ import annotations

import base64
import json as jsonlib
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig  # noqa: E402
from commercial_runtime.notifications import settings as product_settings  # noqa: E402
from commercial_runtime.notifications import transport  # noqa: E402
from commercial_runtime.notifications.outbox import EmailOutboxRepository  # noqa: E402
from commercial_runtime.notifications.schema import apply_notifications_schema  # noqa: E402
from commercial_runtime.notifications.tests.fake_smtp_server import FakeSMTPServer, make_self_signed_contexts  # noqa: E402
from commercial_runtime.notifications.worker import EmailOutboxWorker  # noqa: E402

from app.extensions import db_session  # noqa: E402
from app.mail.worker import EmailWorker  # noqa: E402
from app.models.email_outbox import EmailOutbox  # noqa: E402
from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff  # noqa: E402

BASE = "http://owner.test/api/licensing/v1"
LINK = "http://127.0.0.1:5001/#verify-email/" + "b" * 32
RESET_LINK = "http://127.0.0.1:5001/#reset-password/" + "c" * 32
SMTP_USER = "owner-mailer@actionaura.test"
SMTP_PASSWORD = "cross-stack-fake-password"


class _Response:
    def __init__(self, flask_response):
        self.status_code = flask_response.status_code
        self.headers = dict(flask_response.headers)
        self._r = flask_response

    def json(self):
        return self._r.get_json()


class _FlaskSession:
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
        return base64.b64encode(self._key.sign(canonical_bytes)).decode("ascii")


def _activate(app, client):
    actor_id = make_staff(app, "xstack-mail@example.com")
    _license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()
    resp = client.post(
        "/api/licensing/v1/activations",
        data=jsonlib.dumps(build_activation_body(private_key, full_key=full_key, installation_id="xstack-mail-dev")),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return resp.get_json()["installation_id"], private_key


def test_a_real_till_message_travels_through_owner_to_the_smtp_server(app, client, seeded, signing_key, tmp_path):
    installation_id, private_key = _activate(app, client)

    # --- the till: a real outbox, a real worker, the real relay transport ---
    till_db = str(tmp_path / "till.db")
    conn = sqlite3.connect(till_db)
    apply_notifications_schema(conn)
    product_settings.set_setting(conn, "co-1", "enabled", "1")
    repo = EmailOutboxRepository(conn)
    repo.enqueue(company_id="co-1", email_type="verify_email", recipient="boss@shop.example",
                 subject="Verify your Aura Retail email address",
                 body_text=f"Confirm this is your email address:\n\n{LINK}\n\nThis link expires in 24 hours.")
    repo.enqueue(company_id="co-1", email_type="password_reset", recipient="cashier@shop.example",
                 subject="Reset your Aura Retail password", body_text=f"Reset your password:\n\n{RESET_LINK}\n")
    repo.enqueue(company_id="co-1", email_type="low_stock_alert", recipient="owner@shop.example", subject="Low stock alert: Widget",
                 body_text="Widget has dropped to 3 units on hand (reorder level 5).\n\nReview and accept.")
    repo.enqueue(company_id="co-1", email_type="shift_close_report", recipient="owner@shop.example",
                 subject="Shift closed", body_text="Cash session closed at Main.\n\nVariance:        0.000\nThis drawer balanced.")
    conn.commit()
    conn.close()

    session = _FlaskSession(client)
    relay = transport.RelayTransport(
        client_factory=lambda: LicensingClient(LicensingClientConfig(base_url=BASE, max_retries=0), session=session),
        signer_factory=lambda: _Signer(private_key),
        installation_id_provider=lambda: installation_id,
        app_version="1.0.0-rc.9", platform="WINDOWS",
    )

    def conn_factory():
        c = sqlite3.connect(till_db, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    till_worker = EmailOutboxWorker(conn_factory=conn_factory, company_id="co-1", rate_limit_seconds=0,
                                    transport_factory=lambda: transport.choose_transport(relay))
    result = till_worker.run_once()
    assert result["transport"] == "relay" and result["outcomes"] == {"sent": 4, "retry": 0, "failed_permanent": 0}, result
    assert [c[1] for c in session.calls] == ["/api/licensing/v1/mail"] * 4

    # --- Owner: four QUEUED rows rendered from Owner's own templates ---
    with app.app_context():
        rows = db_session.execute(select(EmailOutbox).order_by(EmailOutbox.created_at)).scalars().all()
        assert [(r.kind, r.to_address, r.status, r.source) for r in rows] == [
            ("verify_email", "boss@shop.example", "QUEUED", "relay"),
            ("password_reset", "cashier@shop.example", "QUEUED", "relay"),
            ("low_stock", "owner@shop.example", "QUEUED", "relay"),
            ("shift_close", "owner@shop.example", "QUEUED", "relay"),
        ]
        assert all(str(r.installation_id) == installation_id for r in rows)
        assert LINK in rows[0].body_text and RESET_LINK in rows[1].body_text
        assert "Widget: 3" in rows[2].body_text and "This drawer balanced." in rows[3].body_text
        assert SMTP_PASSWORD not in "".join(r.body_text + r.subject for r in rows)

    # --- Owner's worker -> a server that demands STARTTLS and AUTH ---
    server_ctx, client_ctx = make_self_signed_contexts(tmp_path)
    with FakeSMTPServer(tls_context=server_ctx, credentials=(SMTP_USER, SMTP_PASSWORD), require_tls=True) as server:
        app.config.update(SMTP_HOST=server.host, SMTP_PORT=str(server.port), SMTP_USER=SMTP_USER,
                          SMTP_PASSWORD=SMTP_PASSWORD, SMTP_FROM="noreply@actionaura.test", SMTP_USE_TLS=True)
        owner_result = EmailWorker(app, ssl_context=client_ctx, pacing_seconds=0).run_once()
    assert owner_result["outcomes"]["sent"] == 4, owner_result
    assert server.starttls_count >= 1 and all(a[2] for a in server.auth_attempts)
    recipients = [m["rcpt_to"][0] for m in server.received]
    assert sorted(r.split("<")[-1].rstrip(">") for r in recipients) == sorted(
        ["boss@shop.example", "cashier@shop.example", "owner@shop.example", "owner@shop.example"])
    assert all(m["tls"] and m["auth_user"] == SMTP_USER for m in server.received)
    verify_mail = next(m for m in server.received if "boss@shop.example" in m["rcpt_to"][0])
    assert "Verify your Aura Retail email address" in verify_mail["data"]
    with app.app_context():
        assert {r.status for r in db_session.execute(select(EmailOutbox)).scalars().all()} == {"SENT"}

    # the till row is SENT too, and a second tick sends nothing more (exactly once)
    assert till_worker.run_once()["claimed"] == 0
    assert len(session.calls) == 4
