"""Shop analysis -- POST /api/licensing/v1/ai/shop-analysis (spec feature O3).

A till (opted in by the shop owner) sends a device-signed request carrying a
statistics snapshot; Owner meters it per licence, asks the AI, and returns a
structured report or a chat answer. The shape mirrors app/mail/relay.py, and
for the same reasons:

  * VALIDATION RUNS FIRST, before authentication, so a malformed request never
    burns a nonce (replay.consume_nonce is single-use) and a flood of garbage
    never touches the database.
  * Authentication is the shared authenticate_device_request (own nonce scope
    "ai_shop_analysis"), so timestamp / replay / signature / licence-state
    rules cannot drift between endpoints. THE LICENCE COMES FROM THE
    INSTALLATION ROW, never from the body: a body field naming a licence would
    let one shop spend another's monthly allowance.
  * An authentication failure consumes NO cap slot: caps are reserved only
    after the device is proven.
  * `brain.preflight` runs before the cap is reserved, so a request that could
    never be answered (no key, or only a training provider for this private
    purpose) does not eat the shop's allowance.
  * THE REQUEST BODY IS NEVER LOGGED. It is a shop's sales data; the only log
    line is the brain's own (purpose, provider, model, tokens, outcome).

The snapshot is deliberately NOT validated field by field: the till can add a
metric without an Owner release. What IS bounded: it must be a JSON object,
nested at most 4 levels, inside a 60 KB request. Customer names never arrive
here (rule R6 is enforced on the till, where the snapshot is built); the prompt
also tells the model never to ask for them.
"""
from __future__ import annotations

import json
import logging

from app.ai import brain, budget, prompts
from app.releases.distribution import DownloadAuthorizationRejected, authenticate_device_request

logger = logging.getLogger(__name__)

NONCE_SCOPE = "ai_shop_analysis"
KINDS = frozenset({"report", "chat"})
LANGS = frozenset({"en", "ar"})
MAX_BODY_BYTES = 60 * 1024
MAX_SNAPSHOT_DEPTH = 4
MAX_QUESTION_CHARS = 1000
MAX_HISTORY_ITEMS = 10
MAX_HISTORY_CONTENT_CHARS = 2000
REPORT_MAX_TOKENS = 2500
CHAT_MAX_TOKENS = 1200
_REQUIRED_FIELDS = ("installation_id", "timestamp", "nonce", "signature", "request_id", "kind", "snapshot")


def _invalid() -> DownloadAuthorizationRejected:
    return DownloadAuthorizationRejected("INVALID_REQUEST")


def _depth_exceeds(value, limit: int) -> bool:
    """True when `value` nests deeper than `limit` levels (a dict or list is one
    level; a scalar is zero). Iterative, so a 100,000-level body cannot blow
    the Python stack before being refused."""
    stack = [(value, 0)]
    while stack:
        node, depth = stack.pop()
        if isinstance(node, dict):
            children = node.values()
        elif isinstance(node, list):
            children = node
        else:
            continue
        depth += 1
        if depth > limit:
            return True
        stack.extend((child, depth) for child in children)
    return False


def _text(value, *, max_len: int) -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= max_len):
        raise _invalid()
    return value


def validate(body) -> dict:
    """Returns {kind, lang, question, history, snapshot}; raises INVALID_REQUEST."""
    if not isinstance(body, dict) or not all(k in body for k in _REQUIRED_FIELDS):
        raise _invalid()
    try:
        size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        raise _invalid() from None
    if size > MAX_BODY_BYTES:
        raise _invalid()
    kind = body["kind"]
    if kind not in KINDS:
        raise _invalid()
    lang = body.get("lang", "en")
    if lang not in LANGS:
        raise _invalid()
    snapshot = body["snapshot"]
    if not isinstance(snapshot, dict) or not snapshot or _depth_exceeds(snapshot, MAX_SNAPSHOT_DEPTH):
        raise _invalid()

    question = None
    history: list[dict] = []
    if kind == "chat":
        question = _text(body.get("question"), max_len=MAX_QUESTION_CHARS)
        raw_history = body.get("history", [])
        if not isinstance(raw_history, list) or len(raw_history) > MAX_HISTORY_ITEMS:
            raise _invalid()
        for turn in raw_history:
            if not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant"):
                raise _invalid()
            history.append(
                {"role": turn["role"], "content": _text(turn.get("content"), max_len=MAX_HISTORY_CONTENT_CHARS)}
            )
    return {"kind": kind, "lang": lang, "question": question, "history": history, "snapshot": snapshot}


def _messages(clean: dict) -> list[dict]:
    snapshot_text = json.dumps(clean["snapshot"], ensure_ascii=False, separators=(",", ":"))
    if clean["kind"] == "report":
        return [{"role": "user", "content": f"SNAPSHOT (JSON):\n{snapshot_text}"}]
    return clean["history"] + [
        {"role": "user", "content": f"SNAPSHOT (JSON):\n{snapshot_text}\n\nQUESTION:\n{clean['question']}"}
    ]


def accept_shop_analysis(body: dict, *, config: dict, app_config=None) -> dict:
    """The full 200 response body. Raises DownloadAuthorizationRejected for any
    shape/auth failure and AIError for any AI/cap failure (the route maps both
    to public reason codes).

    `config` is the licensing-service config dict (timestamp skew, nonce ttl);
    `app_config` is Flask's app.config, which carries the AI keys, chains and
    caps. They are separate because the former is a narrow dict by design."""
    from flask import current_app

    app_config = app_config if app_config is not None else current_app.config
    clean = validate(body)
    installation, license_row = authenticate_device_request(body, nonce_scope=NONCE_SCOPE, config=config)

    brain.preflight(brain.SHOP_ANALYSIS, app_config)
    usage = budget.reserve_shop_analysis(app_config, license_row.id)

    report = clean["kind"] == "report"
    result = brain.run(
        brain.SHOP_ANALYSIS,
        system=prompts.shop_report_system(clean["lang"]) if report else prompts.shop_chat_system(clean["lang"]),
        messages=_messages(clean),
        max_tokens=REPORT_MAX_TOKENS if report else CHAT_MAX_TOKENS,
        json_schema=prompts.REPORT_SCHEMA if report else None,
        config=app_config,
    )
    response = {"contract_version": "v1", "result": "ACCEPTED", "kind": clean["kind"], "model": result.model, "usage": usage}
    if report:
        response["report"] = result.data
    else:
        response["reply"] = result.text.strip()
    logger.info("ai shop analysis: installation=%s kind=%s ok", installation.id, clean["kind"])
    return response
