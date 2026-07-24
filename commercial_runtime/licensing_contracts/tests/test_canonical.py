import pytest

from commercial_runtime.licensing_contracts.canonical import (
    CanonicalizationError,
    canonicalize,
    canonicalize_bytes,
)


def test_sorted_keys():
    assert canonicalize({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_compact_separators_no_whitespace():
    out = canonicalize({"a": [1, 2, 3], "b": {"c": 1}})
    assert " " not in out


def test_nfc_normalization_makes_equivalent_strings_identical():
    # "é" as a single codepoint vs "e" + combining acute accent.
    composed = {"name": "café"}
    decomposed = {"name": "café"}
    assert canonicalize(composed) == canonicalize(decomposed)


def test_array_order_preserved():
    assert canonicalize({"a": [3, 1, 2]}) == '{"a":[3,1,2]}'


def test_null_preserved_not_stripped():
    assert canonicalize({"a": None}) == '{"a":null}'


def test_rejects_nan():
    with pytest.raises(CanonicalizationError):
        canonicalize({"a": float("nan")})


def test_rejects_infinity():
    with pytest.raises(CanonicalizationError):
        canonicalize({"a": float("inf")})


def test_rejects_non_dict_top_level():
    with pytest.raises(CanonicalizationError):
        canonicalize([1, 2, 3])  # type: ignore[arg-type]


def test_rejects_excessive_nesting():
    payload = {}
    cursor = payload
    for _ in range(20):
        cursor["next"] = {}
        cursor = cursor["next"]
    with pytest.raises(CanonicalizationError):
        canonicalize(payload)


def test_canonicalize_bytes_is_utf8_of_canonicalize():
    payload = {"a": "é"}
    assert canonicalize_bytes(payload) == canonicalize(payload).encode("utf-8")


def test_matches_owner_known_vector():
    # Cross-checked by hand against owner/app/licensing_service/canonical.py's
    # own doctest-equivalent behavior (Phase 6) -- same algorithm, same
    # output, for the exact shape an activation request body takes.
    payload = {
        "contract_version": "v1",
        "product_code": "AURA_RETAIL",
        "nonce": "abc123",
        "timestamp": "2026-07-22T10:00:00+00:00",
    }
    expected = (
        '{"contract_version":"v1","nonce":"abc123",'
        '"product_code":"AURA_RETAIL","timestamp":"2026-07-22T10:00:00+00:00"}'
    )
    assert canonicalize(payload) == expected
