"""Owner's signed release descriptor must fail CLOSED.

Each rejection test asserts the specific error code, not just 'it raised':
a verifier that raises for the wrong reason (say, a typo) would still look red
for the right-sounding test name.
"""
import pytest

from commercial_runtime.updates.descriptor import ReleaseOffer, UpdateError, verify_release_descriptor
from commercial_runtime.updates.tests._fakes import (
    INSTALLATION_ID,
    OwnerKey,
    make_trust_store,
    release_payload,
)

REQUEST_ID = "req-1"
CURRENT = "1.0.0-rc.8"


@pytest.fixture
def key():
    return OwnerKey()


@pytest.fixture
def trust(tmp_path, key):
    return make_trust_store(tmp_path, key)


def verify(envelope, trust, **kw):
    args = dict(trust_store=trust, expected_installation_id=INSTALLATION_ID,
                expected_request_id=REQUEST_ID, current_version=CURRENT)
    args.update(kw)
    return verify_release_descriptor(envelope, **args)


def good(key, **kw):
    return key.envelope(release_payload(request_id=REQUEST_ID, **kw))


def test_a_valid_descriptor_is_accepted(key, trust):
    offer = verify(good(key, version="1.0.0-rc.9", checksum="b" * 64, size=2048), trust)
    assert isinstance(offer, ReleaseOffer)
    assert (offer.version, offer.checksum_sha256, offer.size_bytes) == ("1.0.0-rc.9", "b" * 64, 2048)
    assert offer.update_required is False and offer.below_min_supported is False


def test_update_required_and_below_min_supported_are_carried_through(key, trust):
    env = key.envelope(release_payload(request_id=REQUEST_ID, update_required=True, below_min_supported=True))
    offer = verify(env, trust)
    assert offer.update_required is True and offer.below_min_supported is True


def test_nothing_newer_returns_none_only_when_signed(key, trust):
    payload = release_payload(request_id=REQUEST_ID)
    payload.update(update_available=False, release=None)
    assert verify(key.envelope(payload), trust) is None


def test_an_unsigned_no_update_answer_is_still_refused(key, trust):
    # The signature is checked BEFORE anything in the body is acted on: an
    # attacker must not be able to say "all up to date" and silence the channel.
    payload = release_payload(request_id=REQUEST_ID)
    payload.update(update_available=False, release=None)
    env = key.envelope(payload)
    env["signature"] = OwnerKey().sign_b64(payload)  # signed by someone else
    with pytest.raises(UpdateError) as exc:
        verify(env, trust)
    assert exc.value.code == "DESCRIPTOR_UNTRUSTED"


def test_a_flipped_payload_byte_is_rejected(key, trust):
    env = good(key, checksum="c" * 64)
    env["payload"]["release"]["artifact_checksum_sha256"] = "d" * 64  # signature was over 'c'
    with pytest.raises(UpdateError) as exc:
        verify(env, trust)
    assert exc.value.code == "DESCRIPTOR_UNTRUSTED"


def test_a_flipped_size_is_rejected(key, trust):
    env = good(key, size=2048)
    env["payload"]["release"]["artifact_size_bytes"] = 2049
    with pytest.raises(UpdateError) as exc:
        verify(env, trust)
    assert exc.value.code == "DESCRIPTOR_UNTRUSTED"


def test_an_unknown_signing_key_is_rejected(tmp_path, key):
    stranger = OwnerKey(key_id="owner-unknown-key")
    trust = make_trust_store(tmp_path, key)  # trusts `key` only
    with pytest.raises(UpdateError) as exc:
        verify(stranger.envelope(release_payload(request_id=REQUEST_ID)), trust)
    assert exc.value.code == "DESCRIPTOR_UNTRUSTED"


def test_a_known_key_id_with_the_wrong_key_material_is_rejected(tmp_path, key):
    impostor = OwnerKey(key_id=key.key_id)  # same id, different key
    trust = make_trust_store(tmp_path, key)
    with pytest.raises(UpdateError) as exc:
        verify(impostor.envelope(release_payload(request_id=REQUEST_ID)), trust)
    assert exc.value.code == "DESCRIPTOR_UNTRUSTED"


def test_a_non_ed25519_algorithm_is_rejected(key, trust):
    env = key.envelope(release_payload(request_id=REQUEST_ID), algorithm="rsa")
    with pytest.raises(UpdateError) as exc:
        verify(env, trust)
    assert exc.value.code == "DESCRIPTOR_UNTRUSTED"


@pytest.mark.parametrize("broken", [None, "text", [], {}, {"payload": {}}, {"payload": {}, "signing_key_id": "x"}])
def test_malformed_envelopes_are_rejected(trust, broken):
    with pytest.raises(UpdateError) as exc:
        verify(broken, trust)
    assert exc.value.code == "DESCRIPTOR_UNTRUSTED"


def test_a_garbage_signature_encoding_is_rejected(key, trust):
    env = good(key)
    env["signature"] = "!!!not-base64!!!"
    with pytest.raises(UpdateError) as exc:
        verify(env, trust)
    assert exc.value.code == "DESCRIPTOR_UNTRUSTED"


def test_a_descriptor_for_another_installation_is_rejected(key, trust):
    env = key.envelope(release_payload(request_id=REQUEST_ID, installation_id="someone-else"))
    with pytest.raises(UpdateError) as exc:
        verify(env, trust)
    assert exc.value.code == "DESCRIPTOR_MISMATCH"


def test_a_descriptor_answering_another_request_is_rejected(key, trust):
    # Validly signed, correct installation -- but it is the answer to an
    # earlier poll. Replaying it must not be accepted as the current one.
    env = key.envelope(release_payload(request_id="an-older-request"))
    with pytest.raises(UpdateError) as exc:
        verify(env, trust)
    assert exc.value.code == "DESCRIPTOR_MISMATCH"


def test_a_downgrade_is_rejected(key, trust):
    with pytest.raises(UpdateError) as exc:
        verify(good(key, version="1.0.0-rc.7"), trust)
    assert exc.value.code == "DESCRIPTOR_INVALID"


def test_the_same_version_is_rejected(key, trust):
    with pytest.raises(UpdateError) as exc:
        verify(good(key, version=CURRENT), trust)
    assert exc.value.code == "DESCRIPTOR_INVALID"


def test_the_same_version_with_different_build_metadata_is_rejected(key, trust):
    with pytest.raises(UpdateError) as exc:
        verify(good(key, version=CURRENT + "+build.5"), trust)
    assert exc.value.code == "DESCRIPTOR_INVALID"


@pytest.mark.parametrize("version", ["../1.0.0", "1.0.0/x", "1.0.0 rc", "", "1.0", "1.0.0-rc.9\n", None, 5])
def test_unparseable_or_traversal_versions_are_rejected(key, trust, version):
    # These would otherwise reach the installer file name.
    with pytest.raises(UpdateError) as exc:
        verify(good(key, release_overrides={"version": version}), trust)
    assert exc.value.code == "DESCRIPTOR_INVALID"


@pytest.mark.parametrize("checksum", ["", "A" * 64, "a" * 63, "a" * 65, "g" * 64, None, 123, ("a" * 64) + "\n"])
def test_a_bad_checksum_is_rejected(key, trust, checksum):
    with pytest.raises(UpdateError) as exc:
        verify(good(key, checksum=checksum), trust)
    assert exc.value.code == "DESCRIPTOR_INVALID"


@pytest.mark.parametrize("size", [0, -1, 1_073_741_825, "1024", None, 1.5, True])
def test_a_size_outside_the_range_is_rejected(key, trust, size):
    with pytest.raises(UpdateError) as exc:
        verify(good(key, size=size), trust)
    assert exc.value.code == "DESCRIPTOR_INVALID"


def test_the_size_boundaries_are_accepted(key, trust):
    assert verify(good(key, size=1), trust).size_bytes == 1
    assert verify(good(key, size=1_073_741_824), trust).size_bytes == 1_073_741_824
