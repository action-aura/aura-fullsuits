"""The AI router (app/ai/brain.py): the R1 privacy rule and the chain rules.

Every refusal below is paired with a call COUNT, not just an exception: "it
raised PRIVACY_REFUSED" cannot tell "the filter ran" from "the filter never
ran and the provider happened to fail" (ENGINEERING 1, failure shape 1).
"""
from __future__ import annotations

import pytest

from app.ai import brain
from app.ai.providers import AIError
from tests.ai_fakes import FakeProvider, error

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}


def _run(purpose, providers, config=None):
    return brain.run(
        purpose, system="S", messages=[{"role": "user", "content": "q"}], max_tokens=10, json_schema=None,
        config=config or {}, providers={p.name: p for p in providers},
    )


# -- R1: a private purpose never reaches a training provider ------------------
def test_r1_free_gemini_is_dropped_for_shop_analysis_even_when_configured_first():
    gemini = FakeProvider("gemini", trains_on_data=True)
    claude = FakeProvider("anthropic")
    result = _run(brain.SHOP_ANALYSIS, [gemini, claude], {"AI_SHOP_PROVIDERS": "gemini,anthropic"})
    assert result.provider == "anthropic"
    assert len(gemini.calls) == 0, "private data reached a provider that trains on it"
    assert len(claude.calls) == 1


def test_r1_only_a_training_provider_configured_refuses_and_calls_nothing():
    gemini = FakeProvider("gemini", trains_on_data=True)
    with pytest.raises(AIError) as exc:
        _run(brain.SHOP_ANALYSIS, [gemini], {"AI_SHOP_PROVIDERS": "gemini"})
    assert exc.value.code == "AI_PRIVACY_REFUSED"
    assert len(gemini.calls) == 0


def test_r1_allow_half_paid_gemini_is_allowed_for_shop_analysis():
    paid = FakeProvider("gemini", trains_on_data=False)
    assert _run(brain.SHOP_ANALYSIS, [paid], {"AI_SHOP_PROVIDERS": "gemini"}).provider == "gemini"
    assert len(paid.calls) == 1


@pytest.mark.parametrize("purpose,key", [(brain.LEADS, "AI_LEADS_PROVIDERS"), (brain.STAFF_HELP, "AI_HELP_PROVIDERS")])
def test_public_purposes_may_use_free_gemini(purpose, key):
    gemini = FakeProvider("gemini", trains_on_data=True)
    assert _run(purpose, [gemini], {key: "gemini"}).provider == "gemini"
    assert len(gemini.calls) == 1


def test_r1_holds_with_the_real_provider_classes_and_real_config():
    from app.ai.providers import build_providers

    cfg = {"GEMINI_API_KEY": "g", "AI_SHOP_PROVIDERS": "gemini", "GEMINI_PAID_TIER": False}
    with pytest.raises(AIError) as exc:
        brain.preflight(brain.SHOP_ANALYSIS, cfg, providers=build_providers(cfg))
    assert exc.value.code == "AI_PRIVACY_REFUSED"
    paid = {**cfg, "GEMINI_PAID_TIER": True}
    brain.preflight(brain.SHOP_ANALYSIS, paid, providers=build_providers(paid))  # does not raise


# -- chain: only rate limit / upstream fall through ---------------------------
@pytest.mark.parametrize("code", ["AI_RATE_LIMITED", "AI_UPSTREAM_ERROR"])
def test_rate_limit_and_upstream_fall_through_to_the_next_provider(code):
    first = FakeProvider("gemini", error=error(code))
    second = FakeProvider("anthropic")
    assert _run(brain.LEADS, [first, second]).provider == "anthropic"
    assert len(first.calls) == 1 and len(second.calls) == 1


@pytest.mark.parametrize("code", ["AI_BAD_OUTPUT", "AI_NOT_CONFIGURED", "AI_PRIVACY_REFUSED", "AI_BUDGET_EXHAUSTED"])
def test_other_failures_do_not_fall_through(code):
    first = FakeProvider("gemini", error=error(code))
    second = FakeProvider("anthropic")
    with pytest.raises(AIError) as exc:
        _run(brain.LEADS, [first, second])
    assert exc.value.code == code
    assert len(second.calls) == 0, "a non-transient failure must not be re-asked of another model"


def test_whole_chain_failing_raises_the_last_error():
    a = FakeProvider("gemini", error=error("AI_RATE_LIMITED"))
    b = FakeProvider("anthropic", error=error("AI_UPSTREAM_ERROR"))
    with pytest.raises(AIError) as exc:
        _run(brain.LEADS, [a, b])
    assert exc.value.code == "AI_UPSTREAM_ERROR"


# -- configuration ------------------------------------------------------------
def test_nothing_configured_is_not_configured():
    with pytest.raises(AIError) as exc:
        brain.run(brain.LEADS, system="S", messages=[], max_tokens=1, json_schema=None, config={}, providers={})
    assert exc.value.code == "AI_NOT_CONFIGURED"
    assert brain.is_available(brain.LEADS, {}, providers={}) is False


def test_chain_naming_only_unconfigured_providers_is_not_configured():
    claude = FakeProvider("anthropic")
    with pytest.raises(AIError) as exc:
        _run(brain.LEADS, [claude], {"AI_LEADS_PROVIDERS": "gemini"})
    assert exc.value.code == "AI_NOT_CONFIGURED" and len(claude.calls) == 0


def test_chain_parsing_and_defaults():
    assert brain.chain_names(brain.SHOP_ANALYSIS, {}) == ["anthropic"]
    assert brain.chain_names(brain.LEADS, {}) == ["gemini", "anthropic"]
    assert brain.chain_names(brain.STAFF_HELP, {"AI_HELP_PROVIDERS": " Anthropic , gemini,anthropic ,"}) == ["anthropic", "gemini"]


def test_is_available_applies_the_privacy_filter():
    free = FakeProvider("gemini", trains_on_data=True)
    cfg = {"AI_SHOP_PROVIDERS": "gemini", "AI_LEADS_PROVIDERS": "gemini"}
    assert brain.is_available(brain.SHOP_ANALYSIS, cfg, providers={"gemini": free}) is False
    assert brain.is_available(brain.LEADS, cfg, providers={"gemini": free}) is True


def test_unknown_purpose_is_a_programming_error():
    with pytest.raises(ValueError):
        brain.run("nope", system="S", messages=[], max_tokens=1, json_schema=None, config={}, providers={})


def test_chat_slice_starting_with_an_assistant_turn_is_trimmed_to_the_first_user_turn():
    # A "last N turns" slice can begin with the assistant; the Messages API
    # requires the user first (adversarial review 2026-10-31).
    msgs = [{"role": "assistant", "content": "a"}, {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}]
    assert brain.from_first_user_turn(msgs) == msgs[1:]
    assert brain.from_first_user_turn(msgs[1:]) == msgs[1:]
