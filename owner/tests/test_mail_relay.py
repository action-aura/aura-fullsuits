"""Mail relay (POST /api/licensing/v1/mail) -- Owner side.

Real HTTP-level tests in the style of test_crash_reports.py: real activation,
real device signature, real rows. Why the assertions look the way they do
(ENGINEERING 1): a 400/429 cannot tell "the guard refused it" from "the route
never ran", so every refusal is paired with a positive control (the same route
ACCEPTED a valid request in the same test) and asserts the outbox is still empty
of the refused data; and the caps tests count ROWS, because "429 on the 51st"
would pass whether or not the 51st request also queued a message.
"""
from __future__ import annotations

import json
import uuid as uuid_mod
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.extensions import db_session
from app.mail import relay_limits as lim
from app.models.email_outbox import EmailOutbox
from app.models.licensing import License
from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff, sign_body

URL = "/api/licensing/v1/mail"
LINK = "http://127.0.0.1:5001/#verify-email/" + "a" * 32


def _activate(app, client, label="mail-dev", email="mail@example.com"):
    actor_id = make_staff(app, email)
    license_id, full_key = make_license(app, actor_id)
    private_key = make_device_keypair()
    resp = client.post(
        "/api/licensing/v1/activations",
        data=json.dumps(build_activation_body(private_key, full_key=full_key, installation_id=label)),
        content_type="application/json",
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return license_id, resp.get_json()["installation_id"], private_key


def _body(private_key, installation_id, kind="verify_email", params=None, **overrides):
    if params is None:
        params = {"email": "boss@shop.example", "link": LINK}
    body = {
        "contract_version": "v1", "request_id": str(uuid_mod.uuid4()), "correlation_id": str(uuid_mod.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(), "nonce": uuid_mod.uuid4().hex,
        "installation_id": installation_id, "app_version": "1.0.0", "platform": "WINDOWS",
        "kind": kind, "lang": "en", "params": params,
    }
    body.update(overrides)
    return sign_body(private_key, body)


def _send(client, body):
    return client.post(URL, data=json.dumps(body), content_type="application/json")


def _rows(app):
    with app.app_context():
        return db_session.execute(select(EmailOutbox).order_by(EmailOutbox.created_at)).scalars().all()


def _row_count(app):
    return len(_rows(app))


def _seed(app, installation_id, n, *, kind="low_stock", age=timedelta(minutes=10), to_prefix="seed"):
    with app.app_context():
        now = datetime.now(timezone.utc)
        for i in range(n):
            db_session.add(EmailOutbox(
                kind=kind, to_address=f"{to_prefix}{i}@shop.example", subject="s", body_text="b", status="QUEUED",
                attempt_count=0, source="relay", installation_id=uuid_mod.UUID(installation_id), created_at=now - age,
            ))
        db_session.commit()


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------
def test_a_valid_verify_email_is_queued_and_rendered_from_the_owner_template(app, client, seeded, signing_key):
    _lic, inst, key = _activate(app, client)
    resp = _send(client, _body(key, inst))
    assert resp.status_code == 202, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["result"] == "QUEUED"
    rows = _rows(app)
    assert len(rows) == 1
    row = rows[0]
    assert str(row.id) == data["message_id"]
    assert (row.status, row.source, row.kind, row.to_address) == ("QUEUED", "relay", "verify_email", "boss@shop.example")
    assert str(row.installation_id) == inst
    # Subject and body came from Owner's own template, not from the request.
    assert row.subject == "Verify your Aura Retail email address"
    assert LINK in row.body_text and "do not reply" in row.body_text and "support@actionaura.me" in row.body_text
    assert "<a href=" in row.body_html and "تأكيد" not in row.subject
    # English first by default, Arabic section below in the same mail.
    assert row.body_text.index("Confirm this is your email") < row.body_text.index("أكّد")


def test_arabic_first_when_lang_is_ar(app, client, seeded, signing_key):
    _lic, inst, key = _activate(app, client)
    assert _send(client, _body(key, inst, lang="ar")).status_code == 202
    row = _rows(app)[0]
    assert row.subject.startswith("تأكيد")
    assert row.body_text.index("أكّد") < row.body_text.index("Confirm this is your email")


@pytest.mark.parametrize("kind,params,lang", [
    ("password_reset", {"email": "a@b.example", "link": LINK}, "en"),
    ("low_stock", {"to": "a@b.example", "items": [{"name": "Widget <b>", "qty": 3}, {"name": "Gadget", "qty": "2.5"}]}, "en"),
    ("shift_close", {"to": "a@b.example", "lines": ["Cash session closed.", "Variance: 0.000"]}, "ar"),
    ("report_summary", {"to": "a@b.example", "lines": ["Revenue: JD 1.000"]}, "en"),
])
def test_every_kind_renders_and_goes_to_the_named_recipient(app, client, seeded, signing_key, kind, params, lang):
    _lic, inst, key = _activate(app, client)
    resp = _send(client, _body(key, inst, kind=kind, params=params, lang=lang))
    assert resp.status_code == 202, resp.get_data(as_text=True)
    row = _rows(app)[0]
    assert row.to_address == "a@b.example" and row.kind == kind
    assert "Sent by Aura Retail - do not reply" in row.body_text   # fixed footer, no caller text
    if kind == "low_stock":
        assert "Widget &lt;b&gt;" in row.body_html    # HTML-escaped in the HTML part
        assert "<b>" not in row.body_html             # a product name cannot inject markup


# ---------------------------------------------------------------------------
# refusals (each: positive control + nothing queued for the refused request)
# ---------------------------------------------------------------------------
def _refused(app, client, key, inst, status=400, **kw):
    assert _send(client, _body(key, inst)).status_code == 202          # positive control
    before = _row_count(app)
    resp = _send(client, _body(key, inst, **kw))
    assert resp.status_code == status, resp.get_data(as_text=True)
    assert _row_count(app) == before, "a refused request must queue nothing"
    return resp


@pytest.mark.parametrize("overrides", [
    {"kind": "free_text"},                                                  # unknown kind
    {"subject": "Win a prize"},                                             # raw subject
    {"body": "hello"},                                                      # raw body
    {"body_html": "<p>x</p>"},
    {"from": "ceo@actionaura.me"},
    {"bcc": "x@y.example"},
    {"params": {"email": "a@b.example", "link": LINK, "subject": "own subject"}},   # raw subject inside params
    {"params": {"email": "a@b.example", "link": LINK, "body": "own body"}},
    {"params": {"email": "a@b.example"}},                                   # missing link
    {"params": {"email": "a@b.example", "link": "javascript:alert(1)"}},    # link scheme
    {"params": {"email": "a@b.example", "link": "ftp://x.example/a"}},
    {"params": {"email": "a@b.example", "link": "data:text/html;base64,AAAA"}},
    {"params": {"email": "a@b.example", "link": "http://x.example/ a"}},     # whitespace in link
    {"params": {"email": "a@b.example", "link": 'http://x.example/"onmouseover="x'}},
    {"params": {"email": "a@b.example", "link": "http://x.example/" + "a" * 500}},   # over 500
    {"params": {"email": "a@b.example\nBcc: victim@x.example", "link": LINK}},        # header injection
    {"params": {"email": "not-an-email", "link": LINK}},
    {"lang": "fr"},
    {"shop_name": "Ahmad & Sons"},    # removed 2026-10-10: free text in a mail from OUR address
    {"params": {"email": "a@b.example", "link": "https://evil.example/reset/abc"}},      # off-box host
    {"params": {"email": "a@b.example", "link": "https://aura.example@evil.example/x"}},  # userinfo trick
    {"params": {"email": "a@b.example", "link": "http://0.0.0.0:5001/x"}},               # unspecified
    {"params": {"email": "a@b.example", "link": "http://8.8.8.8:5001/x"}},               # public IP
    {"params": {"email": "a@b.example", "link": "http://127.0.0.1@evil.example/x"}},
    {"params": {"email": "a@b.example", "link": "http://MYSHOP-PC:5001/x"}},             # hostname form
])
def test_invalid_requests_are_refused_and_queue_nothing(app, client, seeded, signing_key, overrides):
    _lic, inst, key = _activate(app, client)
    _refused(app, client, key, inst, **overrides)


@pytest.mark.parametrize("link", [
    "http://127.0.0.1:5001/#verify-email/" + "a" * 32,
    "http://192.168.1.20:5001/#reset-password/" + "b" * 32,
    "http://10.0.0.5/#x/1",
    "http://localhost:5001/#verify-email/" + "c" * 32,
    "https://[::1]:5001/#x/1",
    "http://169.254.10.10:5001/#x/1",
])
def test_links_to_the_shops_own_app_are_accepted(app, client, seeded, signing_key, link):
    _lic, inst, key = _activate(app, client)
    resp = _send(client, _body(key, inst, params={"email": "a@b.example", "link": link}))
    assert resp.status_code == 202, resp.get_data(as_text=True)
    assert link in _rows(app)[0].body_text


def test_sixth_identity_mail_to_one_address_is_refused_across_installations(app, client, seeded, signing_key):
    _l1, inst1, key1 = _activate(app, client, label="mail-dev-1", email="m1@example.com")
    _l2, inst2, key2 = _activate(app, client, label="mail-dev-2", email="m2@example.com")
    # Five already sent to the victim by installation 1 (seeded as aged rows so
    # the 2-minute same-kind rule cannot be what refuses the next one).
    with app.app_context():
        now = datetime.now(timezone.utc)
        for _ in range(lim.IDENTITY_PER_RECIPIENT_PER_DAY):
            db_session.add(EmailOutbox(
                kind="password_reset", to_address="Victim@Shop.example", subject="s", body_text="b", status="QUEUED",
                attempt_count=0, source="relay", installation_id=uuid_mod.UUID(inst1), created_at=now - timedelta(minutes=30),
            ))
        db_session.commit()
    n = _row_count(app)
    # A DIFFERENT installation (its own per-installation counters are at zero) is refused for that address...
    over = _send(client, _body(key2, inst2, params={"email": "victim@shop.example", "link": LINK}))
    assert over.status_code == 429 and over.get_json()["reason_code"] == "MAIL_LIMIT"
    assert _row_count(app) == n
    # ...while another address is unaffected (positive control: the route did run and accept).
    assert _send(client, _body(key2, inst2, params={"email": "someone.else@shop.example", "link": LINK})).status_code == 202
    assert _row_count(app) == n + 1


def test_over_long_parameters_are_refused(app, client, seeded, signing_key):
    _lic, inst, key = _activate(app, client)
    ok = {"to": "a@b.example", "items": [{"name": "N" * 120, "qty": 1}]}
    assert _send(client, _body(key, inst, kind="low_stock", params=ok)).status_code == 202
    n = _row_count(app)
    bad_params = [
        {"to": "a@b.example", "items": [{"name": "N" * 121, "qty": 1}]},
        {"to": "a@b.example", "items": [{"name": "x", "qty": "9" * 33}]},
        {"to": "a@b.example", "items": [{"name": "x", "qty": 1}] * 51},
        {"to": "a@b.example", "items": []},
        {"to": "a@b.example", "items": [{"name": "x\nBcc: y", "qty": 1}]},
        {"to": "a@b.example", "items": [{"name": "x", "qty": True}]},
    ]
    for p in bad_params:
        assert _send(client, _body(key, inst, kind="low_stock", params=p)).status_code == 400
    assert _send(client, _body(key, inst, kind="shift_close", params={"to": "a@b.example", "lines": ["L" * 201]})).status_code == 400
    assert _send(client, _body(key, inst, kind="shift_close", params={"to": "a@b.example", "lines": ["x"] * 41})).status_code == 400
    assert _send(client, _body(key, inst, kind="report_summary", params={"to": "a@b.example", "lines": ["x"] * 61})).status_code == 400
    assert _row_count(app) == n


def test_bad_signature_and_replayed_nonce_are_refused(app, client, seeded, signing_key):
    _lic, inst, key = _activate(app, client)
    good = _body(key, inst)
    assert _send(client, good).status_code == 202
    assert _row_count(app) == 1
    # the very same signed request again: replay
    replay = _send(client, good)
    assert replay.status_code == 400 and replay.get_json()["reason_code"] == "NONCE_REUSED"
    # a valid body whose content was changed after signing
    tampered = _body(key, inst)
    tampered["params"] = {"email": "attacker@evil.example", "link": LINK}
    resp = _send(client, tampered)
    assert resp.status_code == 400 and resp.get_json()["reason_code"] == "INVALID_SIGNATURE"
    # signed by some other device key
    other = _body(make_device_keypair(), inst)
    assert _send(client, other).get_json()["reason_code"] == "INVALID_SIGNATURE"
    assert _row_count(app) == 1


def test_a_suspended_licence_cannot_use_the_relay(app, client, seeded, signing_key):
    license_id, inst, key = _activate(app, client)
    assert _send(client, _body(key, inst)).status_code == 202
    with app.app_context():
        db_session.get(License, license_id).status = "SUSPENDED"
        db_session.commit()
    resp = _send(client, _body(key, inst, params={"email": "other@shop.example", "link": LINK}))
    assert resp.status_code == 400
    assert _row_count(app) == 1


# ---------------------------------------------------------------------------
# caps -- per installation, never global
# ---------------------------------------------------------------------------
def test_the_51st_message_in_24h_is_refused_with_429_and_nothing_is_queued(app, client, seeded, signing_key):
    _lic, inst, key = _activate(app, client)
    _seed(app, inst, lim.TOTAL_PER_DAY - 1)
    ok = _send(client, _body(key, inst, kind="report_summary", params={"to": "x@shop.example", "lines": ["a"]}))
    assert ok.status_code == 202                                   # the 50th goes through
    assert _row_count(app) == lim.TOTAL_PER_DAY
    over = _send(client, _body(key, inst, kind="report_summary", params={"to": "y@shop.example", "lines": ["a"]}))
    assert over.status_code == 429 and over.get_json()["reason_code"] == "MAIL_LIMIT"
    assert _row_count(app) == lim.TOTAL_PER_DAY


def test_messages_older_than_24h_do_not_count(app, client, seeded, signing_key):
    _lic, inst, key = _activate(app, client)
    _seed(app, inst, lim.TOTAL_PER_DAY, age=timedelta(hours=25))
    assert _send(client, _body(key, inst)).status_code == 202


def test_the_11th_identity_mail_in_24h_is_refused_but_shop_mail_still_flows(app, client, seeded, signing_key):
    _lic, inst, key = _activate(app, client)
    _seed(app, inst, lim.IDENTITY_PER_DAY - 1, kind="verify_email")
    ok = _send(client, _body(key, inst, kind="password_reset", params={"email": "t10@shop.example", "link": LINK}))
    assert ok.status_code == 202                                   # the 10th identity mail
    n = _row_count(app)
    over = _send(client, _body(key, inst, kind="verify_email", params={"email": "t11@shop.example", "link": LINK}))
    assert over.status_code == 429 and over.get_json()["reason_code"] == "MAIL_LIMIT"
    assert _row_count(app) == n
    # the identity cap is not a total cap: a shop alert is still accepted
    assert _send(client, _body(key, inst, kind="report_summary", params={"to": "ok@shop.example", "lines": ["a"]})).status_code == 202


def test_same_kind_and_recipient_within_two_minutes_is_refused(app, client, seeded, signing_key):
    _lic, inst, key = _activate(app, client)
    assert _send(client, _body(key, inst)).status_code == 202
    again = _send(client, _body(key, inst))
    assert again.status_code == 429 and again.get_json()["reason_code"] == "MAIL_LIMIT"
    assert _row_count(app) == 1
    # a different recipient, or a different kind to the same recipient, is fine
    assert _send(client, _body(key, inst, params={"email": "else@shop.example", "link": LINK})).status_code == 202
    assert _send(client, _body(key, inst, kind="password_reset")).status_code == 202
    # recipient match ignores case
    assert _send(client, _body(key, inst, params={"email": "ELSE@shop.example", "link": LINK})).status_code == 429


def test_same_recipient_after_two_minutes_is_accepted(app, client, seeded, signing_key):
    _lic, inst, key = _activate(app, client)
    _seed(app, inst, 1, kind="verify_email", age=timedelta(seconds=lim.SAME_KIND_TO_SECONDS + 30), to_prefix="boss")
    with app.app_context():
        row = db_session.execute(select(EmailOutbox)).scalars().first()
        row.to_address = "boss@shop.example"
        db_session.commit()
    assert _send(client, _body(key, inst)).status_code == 202


def test_the_caps_count_per_installation_a_second_installation_is_unaffected(app, client, seeded, signing_key):
    _lic_a, inst_a, key_a = _activate(app, client, label="mail-a", email="a@example.com")
    _lic_b, inst_b, key_b = _activate(app, client, label="mail-b", email="b@example.com")
    assert inst_a != inst_b
    _seed(app, inst_a, lim.TOTAL_PER_DAY)
    assert _send(client, _body(key_a, inst_a)).status_code == 429          # A is at its cap...
    assert _send(client, _body(key_b, inst_b)).status_code == 202          # ...B is not
    # identity cap is per installation too
    _seed(app, inst_a, 0)
    with app.app_context():
        for row in db_session.execute(select(EmailOutbox)).scalars().all():
            if str(row.installation_id) == inst_a:
                db_session.delete(row)
        db_session.commit()
    _seed(app, inst_a, lim.IDENTITY_PER_DAY, kind="verify_email")
    assert _send(client, _body(key_a, inst_a, params={"email": "z@shop.example", "link": LINK})).status_code == 429
    assert _send(client, _body(key_b, inst_b, params={"email": "z@shop.example", "link": LINK})).status_code == 202


# ---------------------------------------------------------------------------
# migration
# ---------------------------------------------------------------------------
def test_the_migration_is_one_revision_on_top_of_the_crash_reports_head():
    import os

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(owner_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(owner_dir, "migrations"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == ["b5e1c8a27d94"]
    assert script.get_revision("b5e1c8a27d94").down_revision == "a3d9e6c14b72"


def test_the_table_has_the_planned_columns(app):
    from sqlalchemy import inspect

    with app.app_context():
        cols = {c["name"] for c in inspect(db_session.get_bind()).get_columns("owner_email_outbox")}
    assert cols == {
        "id", "kind", "to_address", "subject", "body_text", "body_html", "status", "attempt_count", "next_attempt_at",
        "lease_expires_at", "last_error", "source", "installation_id", "created_at", "sent_at",
    }
