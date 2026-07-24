"""Deterministic canonical serialization for request signing (Part I).

Byte-for-byte port of owner/app/licensing_service/canonical.py -- the signer
and the verifier must derive the identical canonical string from the same
payload, so this is duplicated deliberately rather than imported (Owner and
the products are independent deployables; nothing here may import from
owner/). Any change to Owner's canonicalization rules must be mirrored here
by hand -- covered by the shared conformance fixtures in
commercial_runtime/licensing_contracts/tests/fixtures/canonical_vectors.json,
which both this module's tests and Owner's own test suite can be checked
against to catch drift.
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
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalizationError("Non-finite numeric value is not permitted in a canonical payload.")
        return value
    return value


def canonicalize(payload: dict) -> str:
    """Sorted keys, no insignificant whitespace, NFC-normalized strings,
    array order preserved. Rejects non-finite numbers and excessive nesting.
    This exact string is what gets signed and what verification re-derives."""
    if not isinstance(payload, dict):
        raise CanonicalizationError("Canonical payload must be a JSON object at the top level.")
    normalized = _normalize(payload)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonicalize_bytes(payload: dict) -> bytes:
    return canonicalize(payload).encode("utf-8")
