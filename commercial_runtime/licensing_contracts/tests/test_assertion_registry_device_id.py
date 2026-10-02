"""AUDIT-033 fix round (verifier V finding #1): the client allowlist accepts the
Owner-pinned `registry_device_id`, and stays strict about everything else.

Allow half: an Owner-signed envelope carrying the field verifies (without this
the allowlist rejects every post-fix lease, the Phase 8 Part W outage again).
Deny half: a different unknown field is still ASSERTION_FORBIDDEN_FIELD, so
adding one name did not turn the gate off.
"""
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commercial_runtime.licensing_contracts.assertion_verifier import AssertionVerificationError
from commercial_runtime.licensing_contracts.tests.test_assertion_verifier import (
    _envelope,
    _payload,
    _verify,
)
from commercial_runtime.licensing_contracts.tests.test_assertion_verifier import _b64_pub
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore


@pytest.fixture
def owner_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def trust_store(tmp_path, owner_key):
    s = OwnerTrustStore(tmp_path / "trust.json")
    s.bootstrap_from_anchor({"keys": [{"key_id": "owner-1", "public_key": _b64_pub(owner_key),
                                       "algorithm": "ed25519"}]})
    return s


def test_an_owner_signed_envelope_carrying_registry_device_id_verifies(owner_key, trust_store):
    dev = str(uuid.uuid4())
    result = _verify(_envelope(owner_key, "owner-1", _payload(registry_device_id=dev)), trust_store)
    assert result.payload["registry_device_id"] == dev


def test_a_different_unknown_field_is_still_refused(owner_key, trust_store):
    env = _envelope(owner_key, "owner-1", _payload(registry_device_ident="x"))
    with pytest.raises(AssertionVerificationError) as exc:
        _verify(env, trust_store)
    assert exc.value.reason_code == "ASSERTION_FORBIDDEN_FIELD"
