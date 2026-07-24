import base64
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only.")

from commercial_runtime.licensing_contracts.device_identity import (
    DeviceIdentityError,
    LocalStateCorruptError,
    WindowsDpapiDeviceIdentityProvider,
    fingerprint_of,
    verify_signature,
)


@pytest.fixture
def provider(tmp_path):
    return WindowsDpapiDeviceIdentityProvider(tmp_path / "licensing")


def test_no_key_initially(provider):
    assert provider.has_key() is False


def test_generate_and_sign_roundtrip(provider):
    meta = provider.generate_new_key()
    assert meta.status == "ACTIVE"
    assert meta.algorithm == "ed25519"
    assert provider.has_key() is True

    pub_b64 = provider.get_public_key_b64()
    raw_pub = base64.b64decode(pub_b64)
    assert len(raw_pub) == 32
    assert meta.public_key_fingerprint == fingerprint_of(raw_pub)

    message = b'{"a":1}'
    sig_b64 = provider.sign(message)
    sig = base64.b64decode(sig_b64)
    assert verify_signature(raw_pub, message, sig) is True


def test_signature_does_not_verify_against_altered_message(provider):
    provider.generate_new_key()
    raw_pub = base64.b64decode(provider.get_public_key_b64())
    sig = base64.b64decode(provider.sign(b'{"a":1}'))
    assert verify_signature(raw_pub, b'{"a":2}', sig) is False


def test_private_key_file_is_not_plaintext_ed25519(provider):
    provider.generate_new_key()
    key_path = provider._key_path
    blob = key_path.read_bytes()
    # A DPAPI blob is never raw 32-byte key material and never contains the
    # word "PRIVATE KEY" the way a PEM would -- guards against ever
    # accidentally writing plaintext key material to disk.
    assert len(blob) != 32
    assert b"PRIVATE KEY" not in blob


def test_generate_new_key_refuses_to_overwrite_existing(provider):
    provider.generate_new_key()
    with pytest.raises(FileExistsError):
        provider.generate_new_key()


def test_missing_key_raises_not_silently_generates(provider):
    with pytest.raises(DeviceIdentityError):
        provider.sign(b"anything")


def test_corrupted_key_file_raises_local_state_corrupt(provider):
    provider.generate_new_key()
    provider._key_path.write_bytes(b"not a real dpapi blob")
    with pytest.raises(LocalStateCorruptError):
        provider.sign(b"anything")


def test_corrupted_metadata_raises_local_state_corrupt(provider):
    provider.generate_new_key()
    provider._meta_path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(LocalStateCorruptError):
        provider.get_metadata()


def test_mark_reset_pending_then_destroy(provider):
    provider.generate_new_key()
    provider.mark_reset_pending()
    assert provider.get_metadata().status == "RESET_PENDING"
    provider.destroy_key()
    assert provider.has_key() is False


def test_two_different_devices_produce_different_fingerprints(tmp_path):
    p1 = WindowsDpapiDeviceIdentityProvider(tmp_path / "d1")
    p2 = WindowsDpapiDeviceIdentityProvider(tmp_path / "d2")
    m1 = p1.generate_new_key()
    m2 = p2.generate_new_key()
    assert m1.public_key_fingerprint != m2.public_key_fingerprint
