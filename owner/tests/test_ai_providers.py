"""AI providers (app/ai/providers.py): request shape, output validation, error
mapping and key hygiene -- driven by an injected SDK client / HTTP transport,
never the network."""
from __future__ import annotations

import json

import pytest

from app.ai import providers as p
from app.ai.providers import AIError, AnthropicProvider, GeminiProvider
from app.ai.prompts import RANK_SCHEMA, REPORT_SCHEMA
from tests.ai_fakes import REPORT, FakeAnthropicClient, anthropic_response

KEY = "sk-ant-SECRET-KEY-123456"
GKEY = "AIzaSy-GEMINI-SECRET-9876"


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
def _anthropic(client):
    return AnthropicProvider(KEY, model="claude-haiku-5-5", client=client)


def test_anthropic_request_shape_and_structured_output():
    client = FakeAnthropicClient(anthropic_response(json.dumps(REPORT)))
    result = _anthropic(client).complete(
        system="SYS", messages=[{"role": "user", "content": "hi"}], max_tokens=2500, json_schema=REPORT_SCHEMA
    )
    req = client.requests[0]
    assert req["model"] == "claude-haiku-5-5"
    assert req["max_tokens"] == 2500 and req["system"] == "SYS"
    assert req["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": REPORT_SCHEMA}}
    # Sampling parameters and `thinking` are rejected by current Claude models.
    for forbidden in ("temperature", "top_p", "top_k", "thinking"):
        assert forbidden not in req
    assert result.data == REPORT and result.provider == "anthropic" and result.model == "claude-haiku-5-5"
    assert (result.input_tokens, result.output_tokens) == (5, 3)
    assert _anthropic(client).trains_on_data is False


def test_anthropic_plain_text_has_no_format():
    client = FakeAnthropicClient(anthropic_response("a plain answer"))
    result = _anthropic(client).complete(system="S", messages=[{"role": "user", "content": "q"}], max_tokens=100, json_schema=None)
    assert result.text == "a plain answer" and result.data is None
    assert client.requests[0]["output_config"] == {"effort": "low"}


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_anthropic_checks_stop_reason_before_reading_content(stop_reason):
    # Content is valid JSON here: a provider that skipped the stop_reason check
    # would happily return it.
    client = FakeAnthropicClient(anthropic_response(json.dumps(REPORT), stop_reason=stop_reason))
    with pytest.raises(AIError) as exc:
        _anthropic(client).complete(system="S", messages=[], max_tokens=10, json_schema=REPORT_SCHEMA)
    assert exc.value.code == "AI_BAD_OUTPUT"


def test_anthropic_schema_mismatch_and_non_json_are_bad_output():
    for text in ("not json at all", json.dumps({"headline": "only a headline"})):
        client = FakeAnthropicClient(anthropic_response(text))
        with pytest.raises(AIError) as exc:
            _anthropic(client).complete(system="S", messages=[], max_tokens=10, json_schema=REPORT_SCHEMA)
        assert exc.value.code == "AI_BAD_OUTPUT"


def test_anthropic_accepts_a_json_code_fence():
    client = FakeAnthropicClient(anthropic_response("```json\n" + json.dumps(REPORT) + "\n```"))
    assert _anthropic(client).complete(system="S", messages=[], max_tokens=10, json_schema=REPORT_SCHEMA).data == REPORT


class _Boom(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize("status,code", [(429, "AI_RATE_LIMITED"), (529, "AI_UPSTREAM_ERROR"), (500, "AI_UPSTREAM_ERROR"), (401, "AI_UPSTREAM_ERROR")])
def test_anthropic_error_mapping_never_leaks_the_key(status, code):
    # The SDK message here quotes the key, as a header echo would.
    client = FakeAnthropicClient(raises=_Boom(status, f"bad request with x-api-key: {KEY}"))
    with pytest.raises(AIError) as exc:
        _anthropic(client).complete(system="S", messages=[], max_tokens=10, json_schema=None)
    assert exc.value.code == code
    assert KEY not in str(exc.value) and KEY not in exc.value.detail
    assert exc.value.__cause__ is None and exc.value.__suppress_context__


def test_anthropic_real_sdk_exception_classes_map_correctly():
    import anthropic
    import httpx2 as httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    rate = anthropic.RateLimitError("slow down " + KEY, response=httpx.Response(429, request=request), body=None)
    with pytest.raises(AIError) as exc:
        _anthropic(FakeAnthropicClient(raises=rate)).complete(system="S", messages=[], max_tokens=10, json_schema=None)
    assert exc.value.code == "AI_RATE_LIMITED" and KEY not in exc.value.detail
    conn = anthropic.APIConnectionError(request=request)
    with pytest.raises(AIError) as exc:
        _anthropic(FakeAnthropicClient(raises=conn)).complete(system="S", messages=[], max_tokens=10, json_schema=None)
    assert exc.value.code == "AI_UPSTREAM_ERROR"


# ---------------------------------------------------------------------------
# Gemini
# ---------------------------------------------------------------------------
def _gemini_reply(text, *, finish="STOP", usage=None):
    return json.dumps({
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
        "usageMetadata": usage or {"promptTokenCount": 40, "candidatesTokenCount": 9, "thoughtsTokenCount": 4},
    }).encode()


class _Transport:
    def __init__(self, status=200, body=b"", raises=None):
        self.status, self.body, self.raises, self.calls = status, body, raises, []

    def __call__(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        if self.raises:
            raise self.raises
        return self.status, self.body


def _gemini(transport, **kw):
    return GeminiProvider(GKEY, model="gemini-3.8-flash", http_post=transport, **kw)


def test_gemini_request_shape_key_in_header_only():
    t = _Transport(body=_gemini_reply(json.dumps({"results": [{"idx": 0, "score": 80, "reason": "r", "opener": "o"}]})))
    result = _gemini(t).complete(
        system="SYS", messages=[{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}],
        max_tokens=900, json_schema=RANK_SCHEMA,
    )
    call = t.calls[0]
    assert call["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert call["headers"]["x-goog-api-key"] == GKEY
    assert GKEY not in call["url"], "the key must never ride in the URL"
    body = call["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "SYS"}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model"]
    gc = body["generationConfig"]
    assert gc["maxOutputTokens"] == 900 and gc["responseMimeType"] == "application/json"
    # Gemini's schema subset: our additionalProperties is stripped from the copy sent.
    assert "additionalProperties" not in json.dumps(gc["responseSchema"])
    assert gc["responseSchema"]["properties"]["results"]["items"]["required"] == ["idx", "score", "reason", "opener"]
    # Raw REST gets the Schema Type enum spelling (Google's own REST example).
    assert gc["responseSchema"]["type"] == "OBJECT"
    assert gc["responseSchema"]["properties"]["results"]["items"]["properties"]["reason"]["type"] == "STRING"
    assert result.data["results"][0]["score"] == 80
    assert (result.input_tokens, result.output_tokens) == (40, 13)  # thinking tokens count as output


def test_gemini_trains_on_data_follows_the_paid_tier_flag():
    assert _gemini(_Transport()).trains_on_data is True
    assert _gemini(_Transport(), paid_tier=True).trains_on_data is False


def test_gemini_status_mapping():
    with pytest.raises(AIError) as exc:
        _gemini(_Transport(status=429, body=b"{}")).complete(system="S", messages=[], max_tokens=1, json_schema=None)
    assert exc.value.code == "AI_RATE_LIMITED"
    for status in (500, 503, 403):
        with pytest.raises(AIError) as exc:
            _gemini(_Transport(status=status, body=b"{}")).complete(system="S", messages=[], max_tokens=1, json_schema=None)
        assert exc.value.code == "AI_UPSTREAM_ERROR"


def test_gemini_transport_failure_never_leaks_the_key():
    t = _Transport(raises=OSError(f"connection reset while sending header x-goog-api-key: {GKEY}"))
    with pytest.raises(AIError) as exc:
        _gemini(t).complete(system="S", messages=[], max_tokens=1, json_schema=None)
    assert exc.value.code == "AI_UPSTREAM_ERROR"
    assert GKEY not in str(exc.value) and GKEY not in exc.value.detail and "[REDACTED]" in exc.value.detail
    assert exc.value.__cause__ is None


def test_gemini_bad_outputs():
    cases = [
        _gemini_reply("x", finish="MAX_TOKENS"),
        _gemini_reply("   "),
        json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}).encode(),
        _gemini_reply("not json"),
        _gemini_reply(json.dumps({"results": "wrong type"})),
    ]
    for body in cases:
        with pytest.raises(AIError) as exc:
            _gemini(_Transport(body=body)).complete(system="S", messages=[], max_tokens=5, json_schema=RANK_SCHEMA)
        assert exc.value.code == "AI_BAD_OUTPUT", body


def test_gemini_malformed_envelope_is_upstream():
    with pytest.raises(AIError) as exc:
        _gemini(_Transport(body=b"<html>oops</html>")).complete(system="S", messages=[], max_tokens=5, json_schema=None)
    assert exc.value.code == "AI_UPSTREAM_ERROR"


# ---------------------------------------------------------------------------
# build_providers
# ---------------------------------------------------------------------------
def test_build_providers_only_returns_keyed_providers():
    assert p.build_providers({}) == {}
    assert p.build_providers({"ANTHROPIC_API_KEY": "  "}) == {}
    built = p.build_providers({"ANTHROPIC_API_KEY": KEY, "GEMINI_API_KEY": GKEY, "GEMINI_PAID_TIER": True,
                               "AI_ANTHROPIC_MODEL": "claude-haiku-5-5", "AI_GEMINI_MODEL": "gemini-3.8-flash"})
    assert set(built) == {"anthropic", "gemini"}
    assert built["gemini"].trains_on_data is False and built["anthropic"].trains_on_data is False
    assert p.build_providers({"GEMINI_API_KEY": GKEY})["gemini"].trains_on_data is True


def test_testing_config_pins_ai_keys_empty_and_strict_mode_knows_the_variables(app):
    from app.config import KNOWN_CONFIG_ENV_VARS

    assert app.config["ANTHROPIC_API_KEY"] == "" and app.config["GEMINI_API_KEY"] == ""
    assert app.config["GEMINI_PAID_TIER"] is False
    for name in (
        "OWNER_ANTHROPIC_API_KEY", "OWNER_GEMINI_API_KEY", "OWNER_GEMINI_PAID_TIER", "OWNER_AI_ANTHROPIC_MODEL",
        "OWNER_AI_GEMINI_MODEL", "OWNER_AI_TIMEOUT_SECONDS", "OWNER_AI_SHOP_PROVIDERS", "OWNER_AI_LEADS_PROVIDERS",
        "OWNER_AI_HELP_PROVIDERS", "OWNER_AI_SHOP_MONTHLY_CAP", "OWNER_AI_DAILY_CAP", "OWNER_AI_HELP_DAILY_CAP_PER_STAFF",
    ):
        assert name in KNOWN_CONFIG_ENV_VARS, name
