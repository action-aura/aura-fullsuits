"""The AI router: which provider may answer which kind of request.

Three purposes, one rule above all (hard rule R1): a PRIVATE purpose never
reaches a provider that trains on its data. That is enforced HERE, in code,
whatever the configuration says -- OWNER_AI_SHOP_PROVIDERS=gemini,anthropic
with the free Gemini key is a plausible operator mistake (it is what someone
would type to "save money"), and the answer to it is not a warning in a log
but a filter that makes the mistake unable to leak a shop's sales figures.
The filter runs BEFORE any provider is called, so a refused request sends
nothing anywhere.

Chain semantics (docs/superpowers/specs/2026-10-31-ai-brain-design.md):

  * Providers are tried in the configured order.
  * ONLY AI_RATE_LIMITED and AI_UPSTREAM_ERROR move on to the next provider.
    A bad-output / refusal / privacy failure is about this request or this
    answer; asking another model would pay twice for the same problem.
  * No configured provider in the chain -> AI_NOT_CONFIGURED (feature absent,
    R5). Providers present but all removed by the privacy filter ->
    AI_PRIVACY_REFUSED (a different, louder condition: the operator asked for
    something the router will not do).
  * One log line per provider attempt: purpose, provider, model, tokens,
    outcome. NEVER the prompt or the reply -- a shop snapshot or a staff
    question has no business in a log.

`providers` can be injected for tests; otherwise they are built from config
through `providers.build_providers` (looked up at call time so a test can
substitute it for the whole app).
"""
from __future__ import annotations

import logging

from app.ai import providers as providers_mod
from app.ai.providers import (
    AI_NOT_CONFIGURED,
    AI_PRIVACY_REFUSED,
    FALL_THROUGH_CODES,
    AIError,
    AIResult,
)

logger = logging.getLogger(__name__)

SHOP_ANALYSIS = "shop_analysis"
LEADS = "leads"
STAFF_HELP = "staff_help"

# Purposes whose data must never be exposed to a training provider (R1). Shop
# sales figures are the customer's business secret; lead search terms and
# how-to questions are not.
PRIVATE_PURPOSES = frozenset({SHOP_ANALYSIS})

# purpose -> (config key, default chain). Defaults mirror config.py; they are
# repeated here so a bare dict config (a test, a script) behaves identically.
_CHAIN_CONFIG = {
    SHOP_ANALYSIS: ("AI_SHOP_PROVIDERS", "anthropic"),
    LEADS: ("AI_LEADS_PROVIDERS", "gemini,anthropic"),
    STAFF_HELP: ("AI_HELP_PROVIDERS", "gemini,anthropic"),
}


def chain_names(purpose: str, config) -> list[str]:
    key, default = _CHAIN_CONFIG[purpose]
    raw = config.get(key)
    raw = default if raw is None else raw
    names: list[str] = []
    for part in str(raw).split(","):
        name = part.strip().lower()
        if name and name not in names:
            names.append(name)
    return names


def _eligible(purpose: str, config, available: dict) -> tuple[list, bool]:
    """(providers allowed to answer this purpose, any_configured_in_chain)."""
    configured = [available[n] for n in chain_names(purpose, config) if n in available]
    if purpose in PRIVATE_PURPOSES:
        # R1: the privacy filter. Applied to the list BEFORE any call.
        return [p for p in configured if not p.trains_on_data], bool(configured)
    return configured, bool(configured)


def is_available(purpose: str, config, *, providers: dict | None = None) -> bool:
    """True when `run(purpose, ...)` could possibly succeed -- what the UI asks
    to decide whether to show an AI button at all (R5)."""
    available = providers if providers is not None else providers_mod.build_providers(config)
    eligible, _any = _eligible(purpose, config, available)
    return bool(eligible)


def _resolve(purpose: str, config, providers: dict | None) -> list:
    """The providers that may answer `purpose`, or the AIError that says why
    none may. No provider is called here, which is what lets a caller refuse
    BEFORE it spends a cap slot (preflight)."""
    if purpose not in _CHAIN_CONFIG:
        raise ValueError(f"unknown AI purpose: {purpose!r}")
    available = providers if providers is not None else providers_mod.build_providers(config)
    eligible, any_configured = _eligible(purpose, config, available)
    if not any_configured:
        raise AIError(AI_NOT_CONFIGURED, detail=f"no AI provider is configured for {purpose}")
    if not eligible:
        raise AIError(AI_PRIVACY_REFUSED, detail=f"every configured provider for {purpose} may train on its data")
    return eligible


def preflight(purpose: str, config, *, providers: dict | None = None) -> None:
    """Raise exactly what run() would raise for configuration reasons
    (AI_NOT_CONFIGURED / AI_PRIVACY_REFUSED), without calling anything. Callers
    run it before reserving a spending-cap slot so a request that could never
    be answered does not eat the shop's allowance."""
    _resolve(purpose, config, providers)


def from_first_user_turn(messages: list[dict]) -> list[dict]:
    """Drop leading assistant turns. A client keeps the LAST N turns of a
    chat, and that slice can begin with the assistant's reply; the Messages
    API requires the first message to be the user's, so such a request would
    fail upstream and read as "AI busy" on every later turn of a long chat
    (adversarial review 2026-10-31). Only the head is trimmed; the question
    being asked is always the final user turn, so it is never dropped."""
    start = next((i for i, m in enumerate(messages) if m.get("role") == "user"), len(messages))
    return messages[start:]


def run(
    purpose: str,
    *,
    system: str,
    messages: list[dict],
    max_tokens: int,
    json_schema: dict | None,
    config,
    providers: dict | None = None,
) -> AIResult:
    eligible = _resolve(purpose, config, providers)
    messages = from_first_user_turn(messages)

    last_error: AIError | None = None
    for provider in eligible:
        try:
            result = provider.complete(
                system=system, messages=messages, max_tokens=max_tokens, json_schema=json_schema
            )
        except AIError as exc:
            logger.info(
                "ai: purpose=%s provider=%s model=%s outcome=%s", purpose, provider.name, provider.model, exc.code
            )
            if exc.code in FALL_THROUGH_CODES:
                last_error = exc
                continue
            raise
        logger.info(
            "ai: purpose=%s provider=%s model=%s in=%d out=%d outcome=ok",
            purpose, result.provider, result.model, result.input_tokens, result.output_tokens,
        )
        return result
    assert last_error is not None  # eligible was non-empty, so at least one error was recorded
    raise last_error
