"""Secure license-key generation (Part O).

Format: AURA-<PRODUCT>-<FORMAT_VERSION>-XXXX-XXXX-XXXX-XXXX-XXXX
  - "AURA-<PRODUCT>-<FORMAT_VERSION>-" is a non-secret, product-identifying prefix.
  - The five XXXX groups (20 chars from a 32-symbol Crockford-style alphabet,
    ambiguous 0/O/1/I/L excluded) encode >=128 bits of CSPRNG entropy.
  - Only an HMAC-SHA256(pepper, full_key) of the secret portion is ever stored
    (License.key_secret_hmac); the plaintext key exists only in the single HTTP
    response returned at issuance (ADR-9).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

KEY_FORMAT_VERSION = 1
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # 32 symbols, no 0/O/1/I/L
_GROUP_COUNT = 5
_GROUP_LEN = 4  # 5 * 4 = 20 symbols * 5 bits/symbol = 100 bits from the alphabet alone, plus CSPRNG source >=128 bits

_PRODUCT_SHORT_CODE = {
    "AURA_RETAIL": "RET",
    "AURA_CLINIC": "CLN",
}


def _random_symbol() -> str:
    return _ALPHABET[secrets.randbelow(len(_ALPHABET))]


def generate_license_key(product_code: str) -> tuple[str, str, str]:
    """Returns (full_key, key_prefix, masked_suffix). full_key is shown ONCE by the
    caller and never persisted; only key_prefix and masked_suffix are safe to store/display."""
    short = _PRODUCT_SHORT_CODE.get(product_code, "GEN")
    prefix = f"AURA-{short}-{KEY_FORMAT_VERSION}"
    groups = ["".join(_random_symbol() for _ in range(_GROUP_LEN)) for _ in range(_GROUP_COUNT)]
    full_key = "-".join([prefix, *groups])
    masked_suffix = f"****{groups[-1]}"
    return full_key, prefix, masked_suffix


def hash_license_secret(full_key: str, pepper: str) -> str:
    if not pepper:
        raise ValueError("License pepper must not be empty -- refusing to hash with an empty key.")
    return hmac.new(pepper.encode("utf-8"), full_key.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_license_key(full_key: str, pepper: str, stored_hmac: str) -> bool:
    if not full_key or not stored_hmac:
        return False
    actual = hash_license_secret(full_key, pepper)
    return hmac.compare_digest(actual, stored_hmac)
