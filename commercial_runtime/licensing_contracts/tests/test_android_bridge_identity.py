import json
from datetime import datetime, timezone

import pytest

from commercial_runtime.licensing_contracts.android_bridge_identity import AndroidBridgeDeviceIdentityProvider
from commercial_runtime.licensing_contracts.device_identity import DeviceIdentityError, LocalStateCorruptError


@pytest.fixture
def licensing_dir(tmp_path):
    d = tmp_path / "licensing"
    d.mkdir()
    return d


@pytest.fixture
def provider(licensing_dir):
    return AndroidBridgeDeviceIdentityProvider(licensing_dir)


def _write_kotlin_files(licensing_dir, *, fingerprint="abc123", status="ACTIVE", owner_installation_id=None):
    (licensing_dir / "device_public_key.txt").write_text("dGVzdC1wdWJsaWMta2V5", encoding="utf-8")
    meta = {
        "algorithm": "ed25519",
        "publicKeyFingerprint": fingerprint,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "ownerInstallationId": owner_installation_id,
    }
    (licensing_dir / "device_key_meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_no_key_initially(provider):
    assert provider.has_key() is False


def test_has_key_true_once_kotlin_files_exist(provider, licensing_dir):
    _write_kotlin_files(licensing_dir)
    assert provider.has_key() is True


def test_reads_public_key_written_by_kotlin(provider, licensing_dir):
    _write_kotlin_files(licensing_dir)
    assert provider.get_public_key_b64() == "dGVzdC1wdWJsaWMta2V5"


def test_reads_metadata_written_by_kotlin_camel_case(provider, licensing_dir):
    _write_kotlin_files(licensing_dir, fingerprint="fp-1", owner_installation_id="inst-9")
    meta = provider.get_metadata()
    assert meta.algorithm == "ed25519"
    assert meta.public_key_fingerprint == "fp-1"
    assert meta.status == "ACTIVE"
    assert meta.owner_installation_id == "inst-9"


def test_get_public_key_missing_is_local_state_corrupt(provider):
    with pytest.raises(LocalStateCorruptError):
        provider.get_public_key_b64()


def test_get_metadata_missing_is_local_state_corrupt(provider):
    with pytest.raises(LocalStateCorruptError):
        provider.get_metadata()


def test_generate_new_key_raises_kotlin_owns_it(provider):
    with pytest.raises(DeviceIdentityError):
        provider.generate_new_key()


def test_sign_raises_kotlin_owns_it(provider):
    with pytest.raises(DeviceIdentityError):
        provider.sign(b"anything")


def test_mark_reset_pending_updates_status_in_place(provider, licensing_dir):
    _write_kotlin_files(licensing_dir, fingerprint="fp-2")
    provider.mark_reset_pending()
    meta = provider.get_metadata()
    assert meta.status == "RESET_PENDING"
    assert meta.public_key_fingerprint == "fp-2"  # unrelated fields preserved


def test_destroy_key_removes_all_three_files(provider, licensing_dir):
    _write_kotlin_files(licensing_dir)
    (licensing_dir / "device_key.enc").write_bytes(b"opaque-wrapped-blob")
    provider.destroy_key()
    assert not (licensing_dir / "device_public_key.txt").exists()
    assert not (licensing_dir / "device_key_meta.json").exists()
    assert not (licensing_dir / "device_key.enc").exists()
    assert provider.has_key() is False


def test_destroy_key_is_safe_when_nothing_exists(provider):
    provider.destroy_key()  # must not raise
