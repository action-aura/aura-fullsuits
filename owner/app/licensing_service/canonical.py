"""Deterministic canonical serialization for signing/verification (Part C, ADR-6.4).

Extends the sorted-key, compact-separator pattern already trusted in
app/audit/services.py::_canonical -- same idea, shared here as the one
canonicalization primitive every Phase 6 signature-bearing operation uses.
"""
from __future__ import annotations

import json
import unicodedata
from typing import Any

MAX_CANONICAL_DEPTH = 16


class CanonicalizationError(ValueError):
    pass


def _normalize(value: Any, depth: int = 0) -> Any:
    if depth > MAX_CANONICAL_DEPTH:
        raise CanonicalizationError("Payload nesting exceeds maximum allowed depth.")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {str(k): _normalize(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v, depth + 1) for v in value]
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN/Infinity are not valid canonical numbers
            raise CanonicalizationError("Non-finite numeric value is not permitted in a canonical payload.")
        return value
    return value


def canonicalize(payload: dict) -> str:
    """Sorted keys, no insignificant whitespace, NFC-normalized strings,
    array order preserved (arrays are semantically ordered, unlike object
    keys). Rejects non-finite numbers and excessive nesting. This exact
    string is what gets signed and what verification re-derives -- never
    sign or verify framework-produced JSON ordering directly."""
    if not isinstance(payload, dict):
        raise CanonicalizationError("Canonical payload must be a JSON object at the top level.")
    normalized = _normalize(payload)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonicalize_bytes(payload: dict) -> bytes:
    return canonicalize(payload).encode("utf-8")
