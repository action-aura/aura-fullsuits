"""POST /api/licensing/v1/ai/shop-analysis -- real HTTP, real activation, real
device signature, real counters; only the AI provider is a fake.

Assertion style (ENGINEERING 1): a refusal is paired with a positive control
(the same route ACCEPTED a valid request in the same test) and with a COUNT --
how many times the provider was called, how many cap slots exist -- because a
400/429 alone cannot tell "the guard refused" from "the route never ran".
"""
from __future__ import annotations

import json
import uuid as uuid_mod
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.extensions import db_session
from tests.ai_fakes import REPORT, FakeProvider, error, patch_providers
from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff, sign_body

URL = "/api/licensing/v1/ai/shop-analysis"
SNAPSHOT = {"currency": "JOD", "summary": {"revenue": 1200.0}, "top_products": [{"name": "Olive oil", "units": 40}]}


def _activate(app, client, label="ai-dev", email="ai@example.com"):
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


def _body(private_key, installation_id, kind="report", **overrides):
    body = {
        "contract_version": "v1", "request_id": str(uuid_mod.uuid4()), "correlation_id": str(uuid_mod.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(), "nonce": uuid_mod.uuid4().hex,
        "installation_id": installation_id, "app_version": "1.0.0", "platform": "WINDOWS",
        "kind": kind, "lang": "en", "snapshot": SNAPSHOT,
    }
    if kind == "chat":
        body["question"] = "Why did sales drop on Tuesday?"
    body.update(overrides)
    return sign_body(private_key, body)


def _send(client, body):
    return client.post(URL, data=json.dumps(body), content_type="application/json")


def _slots(app, like):
    with app.app_context():
        return int(db_session.execute(
            text("SELECT COALESCE(SUM(count), 0) FROM owner_rate_limit_counters WHERE bucket_key LIKE :p"), {"p": like}
        ).scalar_one())


@pytest.fixture()
def claude(monkeypatch):
    fake = FakeProvider("anthropic", data=REPORT, text="Tuesday was a public holiday in the snapshot period.")
    patch_providers(monkeypatch, fake)
    return fake


def test_report_is_accepted_and_the_licence_comes_from_the_installation(app, client, seeded, signing_key, claude):
    lic, inst, key = _activate(app, client)
    resp = _send(client, _body(key, inst, lang="ar"))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["result"] == "ACCEPTED" and data["kind"] == "report"
    assert data["report"] == REPORT and data["model"] == "fake-model"
    assert data["usage"] == {"used": 1, "cap": 120}
    # metered against the licence the INSTALLATION belongs to
    assert _slots(app, f"ai:shop:{lic}:%") == 1
    call = claude.calls[0]
    assert "Arabic" in call["system"] and call["json_schema"] is not None and call["max_tokens"] == 2500
    assert "Olive oil" in call["messages"][0]["content"]


def test_chat_returns_a_reply_and_passes_history(app, client, seeded, signing_key, claude):
    _lic, inst, key = _activate(app, client)
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    resp = _send(client, _body(key, inst, kind="chat", history=history))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["reply"].startswith("Tuesday")
    messages = claude.calls[0]["messages"]
    assert messages[:2] == history and "Why did sales drop" in messages[2]["content"] and "Olive oil" in messages[2]["content"]
    assert claude.calls[0]["json_schema"] is None and claude.calls[0]["max_tokens"] == 1200


# -- validation happens BEFORE the nonce is consumed ---------------------------
@pytest.mark.parametrize("overrides", [
    {"kind": "dance"},
    {"lang": "fr"},
    {"snapshot": "not an object"},
    {"snapshot": {}},
    {"snapshot": {"a": {"b": {"c": {"d": {"e": 1}}}}}},   # 5 levels
    {"kind": "chat", "question": ""},                       # chat with no question
    {"kind": "chat", "question": "x" * 1001},
    {"kind": "chat", "question": "q", "history": [{"role": "user", "content": "x"}] * 11},
    {"kind": "chat", "question": "q", "history": [{"role": "system", "content": "x"}]},
    {"snapshot": {"blob": "x" * 62000}},                    # > 60 KB, < the blueprint's 64 KB
])
def test_invalid_requests_are_400_and_never_burn_the_nonce(app, client, seeded, signing_key, claude, overrides):
    _lic, inst, key = _activate(app, client)
    nonce = uuid_mod.uuid4().hex
    bad = _send(client, _body(key, inst, nonce=nonce, **overrides))
    assert bad.status_code == 400 and bad.get_json()["reason_code"] == "INVALID_REQUEST", bad.get_data(as_text=True)[:200]
    assert len(claude.calls) == 0
    # Positive control: the SAME nonce is still unspent, so a valid request using it succeeds.
    kind_overrides = {"kind": overrides.get("kind")} if overrides.get("kind") == "chat" else {}
    good = _send(client, _body(key, inst, nonce=nonce, **kind_overrides))
    assert good.status_code == 200, good.get_data(as_text=True)[:300]


def test_a_nonce_cannot_be_replayed(app, client, seeded, signing_key, claude):
    _lic, inst, key = _activate(app, client)
    body = _body(key, inst)
    assert _send(client, body).status_code == 200
    again = _send(client, body)
    assert again.status_code >= 400 and again.get_json()["reason_code"] == "NONCE_REUSED"
    assert len(claude.calls) == 1


# -- authentication failure consumes no cap slot --------------------------------
def test_bad_signature_is_refused_and_takes_no_cap_slot(app, client, seeded, signing_key, claude):
    lic, inst, key = _activate(app, client)
    forged = _body(key, inst)
    forged["signature"] = forged["signature"][:-4] + ("AAAA" if not forged["signature"].endswith("AAAA") else "BBBB")
    resp = _send(client, forged)
    assert resp.status_code >= 400 and resp.get_json()["reason_code"] == "INVALID_SIGNATURE"
    assert len(claude.calls) == 0
    assert _slots(app, "ai:%") == 0, "an unauthenticated request must not consume any AI cap slot"
    # positive control: an authentic request does consume exactly one
    assert _send(client, _body(key, inst)).status_code == 200
    assert _slots(app, f"ai:shop:{lic}:%") == 1


def test_unknown_installation_is_refused_and_takes_no_cap_slot(app, client, seeded, signing_key, claude):
    _activate(app, client)
    stranger = make_device_keypair()
    resp = _send(client, _body(stranger, str(uuid_mod.uuid4())))
    assert resp.status_code >= 400 and len(claude.calls) == 0 and _slots(app, "ai:%") == 0


# -- caps -------------------------------------------------------------------------
def test_monthly_cap_refuses_at_the_cap_before_any_ai_call(app, client, seeded, signing_key, claude):
    app.config["AI_SHOP_MONTHLY_CAP"] = "2"
    _lic, inst, key = _activate(app, client)
    assert _send(client, _body(key, inst)).status_code == 200
    assert _send(client, _body(key, inst)).status_code == 200
    third = _send(client, _body(key, inst))
    assert third.status_code == 429 and third.get_json()["reason_code"] == "AI_BUDGET_EXHAUSTED"
    assert len(claude.calls) == 2, "the refused request reached the AI"


def test_daily_server_cap_refuses_the_next_request(app, client, seeded, signing_key, claude):
    app.config["AI_DAILY_CAP"] = "1"
    _lic, inst, key = _activate(app, client)
    assert _send(client, _body(key, inst)).status_code == 200
    resp = _send(client, _body(key, inst))
    assert resp.status_code == 429 and resp.get_json()["reason_code"] == "AI_BUDGET_EXHAUSTED"
    assert len(claude.calls) == 1


# -- configuration / privacy / upstream ----------------------------------------------
def test_not_configured_is_503_and_takes_no_cap_slot(app, client, seeded, signing_key, monkeypatch):
    patch_providers(monkeypatch)  # no provider has a key
    _lic, inst, key = _activate(app, client)
    resp = _send(client, _body(key, inst))
    assert resp.status_code == 503 and resp.get_json()["reason_code"] == "AI_NOT_CONFIGURED"
    assert _slots(app, "ai:%") == 0


def test_free_gemini_is_never_used_for_shop_data_even_if_that_is_all_there_is(app, client, seeded, signing_key, monkeypatch):
    free = FakeProvider("gemini", trains_on_data=True, data=REPORT)
    patch_providers(monkeypatch, free)
    app.config["AI_SHOP_PROVIDERS"] = "gemini"
    _lic, inst, key = _activate(app, client)
    resp = _send(client, _body(key, inst))
    assert resp.status_code == 503 and resp.get_json()["reason_code"] == "AI_PRIVACY_REFUSED"
    assert len(free.calls) == 0, "a shop's sales snapshot reached a provider that trains on its input"
    assert _slots(app, "ai:%") == 0


@pytest.mark.parametrize("code", ["AI_RATE_LIMITED", "AI_UPSTREAM_ERROR", "AI_BAD_OUTPUT"])
def test_upstream_trouble_is_503_upstream_error(app, client, seeded, signing_key, monkeypatch, code):
    patch_providers(monkeypatch, FakeProvider("anthropic", error=error(code, "detail with sk-ant-SECRET")))
    _lic, inst, key = _activate(app, client)
    resp = _send(client, _body(key, inst))
    assert resp.status_code == 503 and resp.get_json()["reason_code"] == "AI_UPSTREAM_ERROR"
    assert "SECRET" not in resp.get_data(as_text=True), "provider detail must never reach the till"


def test_request_body_is_never_logged(app, client, seeded, signing_key, claude, caplog):
    _lic, inst, key = _activate(app, client)
    body = _body(key, inst, snapshot={**SNAPSHOT, "marker": "SNAPSHOT-MARKER-XYZ"})
    with caplog.at_level("DEBUG"):
        assert _send(client, body).status_code == 200
    assert "SNAPSHOT-MARKER-XYZ" not in caplog.text
