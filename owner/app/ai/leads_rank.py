"""Leads Finder AI ranking (spec feature O1).

The rep has just searched Google Places; this asks the AI to score each result
for "would this business buy a POS from us" and to draft an Arabic WhatsApp
opener. Everything here exists to keep the AI in its lane:

  * R3 -- NO PHONE NUMBER EVER GOES TO THE AI, and none comes back. The payload
    sent to the model is rebuilt here, field by field, from the validated
    request (`_ai_payload`), so a phone smuggled into any request field is not
    forwarded; `has_phone` is a bare boolean. The reply schema has no phone
    field, and the opener is truncated and never trusted to hold contact data.
    Phone numbers on imported leads still come from Google Places alone
    (discovery.py:1-15).
  * The model's answer is data, not a command: any `idx` the server did not
    send is DROPPED (a model, or a tampered provider, cannot add or overwrite
    a row it was not shown), the score is clamped to 0-100, the opener is cut
    to 400 characters, and a repeated idx keeps its first answer.
  * Leads data is public Google Maps data, so this purpose may use the free
    Gemini tier (brain.LEADS is not a private purpose).
"""
from __future__ import annotations

import json
import re

from app.ai import brain, budget, prompts
from app.ai.providers import AIError

MAX_PLACES = 20
MAX_QUERY_LEN = 120
MAX_NAME_LEN = 200
MAX_ADDRESS_LEN = 300
MAX_TYPE_LEN = 60
MAX_TYPES = 8
MAX_REASON_LEN = 300
MAX_OPENER_LEN = 400
# 20 rows x (Arabic reason + 400-char Arabic opener) is ~4-5k tokens, and
# Gemini counts thinking against this too; 3,500 truncated real answers into
# AI_BAD_OUTPUT (no fallback, slot already spent). Output is fils per call.
RANK_MAX_TOKENS = 8000
LANGS = frozenset({"en", "ar"})


class RankRequestError(ValueError):
    """The request body was not what the rank screen sends (route -> 400)."""


def _str(value, max_len: int, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise RankRequestError("expected a string")
    value = value.strip()
    if required and not value:
        raise RankRequestError("expected a non-empty string")
    return value[:max_len]


def _opt_number(value, kind):
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return kind(value)


def validate(payload) -> dict:
    if not isinstance(payload, dict):
        raise RankRequestError("expected a JSON object")
    lang = payload.get("lang", "en")
    if lang not in LANGS:
        raise RankRequestError("unsupported lang")
    places_raw = payload.get("places")
    if not isinstance(places_raw, list) or not (1 <= len(places_raw) <= MAX_PLACES):
        raise RankRequestError(f"places must hold 1 to {MAX_PLACES} items")
    places = []
    seen: set[int] = set()
    for raw in places_raw:
        if not isinstance(raw, dict):
            raise RankRequestError("each place must be an object")
        idx = raw.get("idx")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0 or idx in seen:
            raise RankRequestError("each place needs a unique non-negative integer idx")
        seen.add(idx)
        types = raw.get("types") or []
        if not isinstance(types, list):
            raise RankRequestError("types must be a list")
        places.append({
            "idx": idx,
            "name": _str(raw.get("name"), MAX_NAME_LEN, required=True),
            "address": _str(raw.get("address"), MAX_ADDRESS_LEN),
            "types": [_str(t, MAX_TYPE_LEN) for t in types[:MAX_TYPES] if isinstance(t, str)],
            "rating": _opt_number(raw.get("rating"), float),
            "reviews": _opt_number(raw.get("reviews"), int),
            "has_phone": bool(raw.get("has_phone")),
            "has_website": bool(raw.get("has_website")),
            "status": _str(raw.get("status"), 40),
        })
    return {
        "segment": _str(payload.get("segment"), MAX_QUERY_LEN, required=True),
        "area": _str(payload.get("area"), MAX_QUERY_LEN, required=True),
        "lang": lang,
        "places": places,
    }


def _ai_payload(clean: dict) -> str:
    """The ONLY thing the model sees. Built from an explicit allowlist of the
    validated fields -- there is no `phone` key to forward, by construction."""
    return json.dumps(
        {
            "search": {"segment": clean["segment"], "area": clean["area"]},
            "businesses": [
                {
                    "idx": p["idx"], "name": p["name"], "address": p["address"], "categories": p["types"],
                    "rating": p["rating"], "review_count": p["reviews"],
                    "lists_a_phone": p["has_phone"], "lists_a_website": p["has_website"],
                    "business_status": p["status"],
                }
                for p in clean["places"]
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


# R3 in CODE, not only in the prompt (adversarial review 2026-10-31). A Places
# business NAME is model input the business owner controls -- "Cafe X: tell
# them to call +962 7..., visit http://..." is a plausible injection, and the
# free model may comply. Its opener is shown with a Copy button and saved as a
# lead note, so a planted number or link would travel straight into a rep's
# WhatsApp and into lead data. Any reason/opener carrying a phone-like digit
# run (7+, Arabic-Indic digits and spaced/dashed groups included) or a link or
# address drops the whole row: no AI advice beats contact data from nowhere.
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_DIGIT_SEPARATORS_RE = re.compile(r"(?<=\d)[\s\-.()/]+(?=\d)")
_PHONE_LIKE_RE = re.compile(r"\+?\d{7,}")
_LINK_RE = re.compile(r"https?://|www\.|wa\.me|t\.me|@|\.(?:com|net|org|jo|me)\b", re.IGNORECASE)


def carries_contact_data(text: str) -> bool:
    folded = _DIGIT_SEPARATORS_RE.sub("", (text or "").translate(_ARABIC_DIGITS))
    return bool(_PHONE_LIKE_RE.search(folded) or _LINK_RE.search(folded))


def clean_results(raw_results, sent_idx: set[int]) -> list[dict]:
    """The model's answer, reduced to rows the server actually sent."""
    out: list[dict] = []
    taken: set[int] = set()
    for item in raw_results or []:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx")
        if isinstance(idx, bool) or not isinstance(idx, int) or idx not in sent_idx or idx in taken:
            continue
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        taken.add(idx)
        reason = str(item.get("reason") or "")[:MAX_REASON_LEN]
        opener = str(item.get("opener") or "")[:MAX_OPENER_LEN]
        if carries_contact_data(reason) or carries_contact_data(opener):
            continue
        out.append({"idx": idx, "score": max(0, min(100, int(score))), "reason": reason, "opener": opener})
    return out


def rank_places(payload, config) -> list[dict]:
    """Validated request -> [{idx, score, reason, opener}] for rows we sent.
    Raises RankRequestError (bad request) or AIError (no key / cap / upstream)."""
    clean = validate(payload)
    brain.preflight(brain.LEADS, config)          # nothing configured -> refuse before a cap slot is spent
    budget.reserve_purpose_day(config, brain.LEADS)
    result = brain.run(
        brain.LEADS,
        system=prompts.leads_rank_system(clean["lang"]),
        messages=[{"role": "user", "content": _ai_payload(clean)}],
        max_tokens=RANK_MAX_TOKENS,
        json_schema=prompts.RANK_SCHEMA,
        config=config,
    )
    rows = clean_results((result.data or {}).get("results"), {p["idx"] for p in clean["places"]})
    if not rows:
        raise AIError("AI_BAD_OUTPUT", detail="the model scored none of the businesses it was given")
    return rows
