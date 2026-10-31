"""Staff helper: POST /assistant/ask and the floating Help button."""
from __future__ import annotations

import pytest

from tests.ai_fakes import FakeProvider, error, patch_providers
from tests.conftest import force_login, get_csrf, make_staff

URL = "/assistant/ask"
JSON = {"Accept": "application/json"}


def _csrf(client):
    # The profile menu's logout form carries a token on every signed-in page.
    return get_csrf(client.get("/").get_data(as_text=True))


def _post(client, payload, csrf=None, **kw):
    headers = {**JSON, **({"X-CSRFToken": csrf} if csrf is not None else {})}
    return client.post(URL, json=payload, headers=headers, **kw)


@pytest.fixture()
def helper(monkeypatch):
    fake = FakeProvider("gemini", trains_on_data=True, text="Open Leads in the sidebar.")
    patch_providers(monkeypatch, fake)
    return fake


@pytest.fixture()
def staff_client(app, client, seeded):
    force_login(client, app, make_staff(app, "help@example.com", role_codes=["SALES"]))
    return client


def test_answer_comes_back_and_the_page_goes_in_the_user_message(staff_client, helper):
    resp = _post(staff_client, {"question": "How do I add a lead?", "page": "/leads"}, _csrf(staff_client))
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json() == {"reply": "Open Leads in the sidebar."}
    call = helper.calls[0]
    assert call["json_schema"] is None
    assert "/leads" in call["messages"][-1]["content"] and "How do I add a lead?" in call["messages"][-1]["content"]
    assert "/leads" not in call["system"].split("REFERENCE MATERIAL:")[0]


def test_system_prompt_is_identical_across_requests_so_the_provider_can_cache_it(staff_client, helper):
    csrf = _csrf(staff_client)
    _post(staff_client, {"question": "one", "page": "/leads"}, csrf)
    _post(staff_client, {"question": "two", "page": "/customers", "history": [{"role": "user", "content": "one"}, {"role": "assistant", "content": "r"}]}, csrf)
    assert len(helper.calls) == 2 and helper.calls[0]["system"] == helper.calls[1]["system"]
    assert "owner_screens.md" in helper.calls[0]["system"] and "never" in helper.calls[0]["system"].lower()


def test_history_is_forwarded_before_the_new_question(staff_client, helper):
    hist = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    _post(staff_client, {"question": "q2", "history": hist}, _csrf(staff_client))
    assert helper.calls[0]["messages"][:2] == hist and helper.calls[0]["messages"][2]["content"].endswith("q2")


def test_requires_login(app, client, seeded, helper):
    csrf = get_csrf(client.get("/auth/login").get_data(as_text=True))
    resp = _post(client, {"question": "hello"}, csrf)
    assert resp.status_code == 401 and resp.get_json() == {"error": "authentication_required"}
    assert len(helper.calls) == 0


def test_requires_csrf(staff_client, helper):
    # Positive control first: with the token the same request is answered.
    assert _post(staff_client, {"question": "hello"}, _csrf(staff_client)).status_code == 200
    before = len(helper.calls)
    resp = _post(staff_client, {"question": "hello"}, csrf=None)
    assert resp.status_code == 400 and len(helper.calls) == before, "a request without the CSRF header reached the AI"


@pytest.mark.parametrize("payload", [
    {"question": ""}, {"question": "   "}, {"question": "x" * 1001}, {"question": 5}, {},
    {"question": "q", "history": [{"role": "user", "content": "x"}] * 8},
    {"question": "q", "history": [{"role": "system", "content": "x"}]},
    {"question": "q", "page": "x" * 201},
])
def test_bad_requests_are_400_and_cost_no_ai_call(staff_client, helper, payload):
    resp = _post(staff_client, payload, _csrf(staff_client))
    assert resp.status_code == 400 and resp.get_json()["error"] == "INVALID_REQUEST"
    assert len(helper.calls) == 0


def test_per_staff_daily_cap(app, staff_client, helper):
    app.config["AI_HELP_DAILY_CAP_PER_STAFF"] = "2"
    csrf = _csrf(staff_client)
    assert _post(staff_client, {"question": "a"}, csrf).status_code == 200
    assert _post(staff_client, {"question": "b"}, csrf).status_code == 200
    third = _post(staff_client, {"question": "c"}, csrf)
    assert third.status_code == 429 and third.get_json()["error"] == "AI_BUDGET_EXHAUSTED"
    assert len(helper.calls) == 2
    # Another staff member has their own allowance.
    other = make_staff(app, "other@example.com", role_codes=["SALES"])
    c2 = app.test_client()  # a second client = a second session
    force_login(c2, app, other)
    assert _post(c2, {"question": "d"}, _csrf(c2)).status_code == 200


def test_not_configured_is_503(staff_client, monkeypatch):
    patch_providers(monkeypatch)
    resp = _post(staff_client, {"question": "hi"}, _csrf(staff_client))
    assert resp.status_code == 503 and resp.get_json()["error"] == "AI_NOT_CONFIGURED"


def test_provider_detail_and_keys_never_reach_the_browser(staff_client, monkeypatch):
    patch_providers(monkeypatch, FakeProvider("gemini", error=error("AI_UPSTREAM_ERROR", "x-goog-api-key: AIzaSy-SECRET")))
    resp = _post(staff_client, {"question": "hi"}, _csrf(staff_client))
    assert resp.status_code == 502 and "SECRET" not in resp.get_data(as_text=True)


def test_help_button_is_present_only_when_a_provider_is_configured(app, staff_client, monkeypatch):
    patch_providers(monkeypatch)
    off = staff_client.get("/").get_data(as_text=True)
    assert "aura-ai-help" not in off and "ai-help.js" not in off
    patch_providers(monkeypatch, FakeProvider("gemini", trains_on_data=True))
    on = staff_client.get("/").get_data(as_text=True)
    assert 'id="aura-ai-help"' in on and 'data-ask-url="/assistant/ask"' in on and "js/ai-help.js" in on
    assert "Don&#39;t paste customer phone numbers or personal data" in on or "Don't paste customer phone numbers or personal data" in on


def test_help_button_never_renders_for_signed_out_visitors(client, seeded, monkeypatch):
    patch_providers(monkeypatch, FakeProvider("gemini", trains_on_data=True))
    assert "aura-ai-help" not in client.get("/auth/login").get_data(as_text=True)
