"""Staff help assistant (spec feature O2): "how do I do X in Aura Owner?".

Public-purpose (brain.STAFF_HELP): the questions are how-to questions about our
own screens and sales rules, not shop data, so the free Gemini tier is allowed
-- which is exactly why the UI tells the person not to paste customer phone
numbers or personal data (the free tier may use prompts for training).

The system prompt (knowledge files + rules) is built once and never varies
(prompts.staff_help_system); the screen the person is on goes in the USER
message, so provider-side prompt caching keeps working. The per-staff-member
daily cap (R4) is reserved before any AI call.
"""
from __future__ import annotations

from app.ai import brain, budget, prompts

MAX_QUESTION_CHARS = 1000
MAX_HISTORY_TURNS = 6
MAX_HISTORY_CONTENT_CHARS = 2000
MAX_PAGE_CHARS = 200
HELP_MAX_TOKENS = 900


class HelpRequestError(ValueError):
    """The request body was not what the help panel sends (route -> 400)."""


def validate(payload) -> dict:
    if not isinstance(payload, dict):
        raise HelpRequestError("expected a JSON object")
    question = payload.get("question")
    if not isinstance(question, str) or not (1 <= len(question.strip()) <= MAX_QUESTION_CHARS):
        raise HelpRequestError(f"question must be 1 to {MAX_QUESTION_CHARS} characters")
    history_raw = payload.get("history", [])
    if not isinstance(history_raw, list) or len(history_raw) > MAX_HISTORY_TURNS:
        raise HelpRequestError(f"history holds at most {MAX_HISTORY_TURNS} turns")
    history = []
    for turn in history_raw:
        if (
            not isinstance(turn, dict)
            or turn.get("role") not in ("user", "assistant")
            or not isinstance(turn.get("content"), str)
            or not (1 <= len(turn["content"]) <= MAX_HISTORY_CONTENT_CHARS)
        ):
            raise HelpRequestError("each history turn needs a role and content")
        history.append({"role": turn["role"], "content": turn["content"]})
    page = payload.get("page", "")
    if not isinstance(page, str) or len(page) > MAX_PAGE_CHARS:
        raise HelpRequestError("page must be a short string")
    return {"question": question.strip(), "history": history, "page": page.strip()}


def ask(payload, staff_id, config) -> str:
    """The reply text. Raises HelpRequestError (bad request) or AIError."""
    clean = validate(payload)
    brain.preflight(brain.STAFF_HELP, config)
    budget.reserve_staff_help(config, staff_id)
    screen = f"[The person is on the screen: {clean['page']}]\n" if clean["page"] else ""
    result = brain.run(
        brain.STAFF_HELP,
        system=prompts.staff_help_system(),
        messages=clean["history"] + [{"role": "user", "content": f"{screen}{clean['question']}"}],
        max_tokens=HELP_MAX_TOKENS,
        json_schema=None,
        config=config,
    )
    return result.text.strip()
