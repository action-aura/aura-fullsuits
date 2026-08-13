import logging
import sys

import pytest

from commercial_runtime.einvoicing.credentials import (
    AppSecretDerivedSecretBox,
    CredentialsUnavailable,
    EInvoiceCredentials,
    WindowsDpapiSecretBox,
    get_secret_box,
)

SECRET_MARKER = 'super-secret-client-secret-value-9f8e7d'

# Both real backends are exercised on Windows -- the dpapi path is not
# mocked there, it goes through real CryptProtectData. DPAPI has no Linux
# equivalent (AppSecretDerivedSecretBox is the intended cross-platform
# fallback, used on Android per test_get_secret_box_selects_app_secret_derived_on_android
# below), so the dpapi case is skipped rather than mocked on non-Windows CI
# runners -- mocking CryptProtectData would just test the mock, not DPAPI.
_windows_only = pytest.mark.skipif(
    sys.platform != 'win32',
    reason='DPAPI (CryptProtectData/CryptUnprotectData) is a Windows-only API',
)
BACKEND_FACTORIES = [
    pytest.param('dpapi', lambda tmp_path: WindowsDpapiSecretBox(str(tmp_path)), marks=_windows_only, id='dpapi'),
    pytest.param('app-secret-aes-gcm', lambda tmp_path: AppSecretDerivedSecretBox(str(tmp_path)), id='app-secret-aes-gcm'),
]


@pytest.mark.parametrize('name,factory', BACKEND_FACTORIES)
def test_round_trip(tmp_path, name, factory):
    box = factory(tmp_path)
    creds = EInvoiceCredentials(client_id='client-abc123', client_secret=SECRET_MARKER)
    assert box.exists() is False
    box.store(creds)
    assert box.exists() is True
    loaded = box.load()
    assert loaded.client_id == 'client-abc123'
    assert loaded.client_secret == SECRET_MARKER


@pytest.mark.parametrize('name,factory', BACKEND_FACTORIES)
def test_load_without_store_raises_unavailable(tmp_path, name, factory):
    box = factory(tmp_path)
    with pytest.raises(CredentialsUnavailable):
        box.load()


@pytest.mark.parametrize('name,factory', BACKEND_FACTORIES)
def test_wipe_removes_credentials(tmp_path, name, factory):
    box = factory(tmp_path)
    box.store(EInvoiceCredentials(client_id='c', client_secret=SECRET_MARKER))
    box.wipe()
    assert box.exists() is False
    with pytest.raises(CredentialsUnavailable):
        box.load()


def test_wipe_when_never_stored_does_not_raise(tmp_path):
    box = AppSecretDerivedSecretBox(str(tmp_path))
    box.wipe()  # must not raise


@pytest.mark.parametrize('name,factory', BACKEND_FACTORIES)
def test_describe_never_contains_the_secret(tmp_path, name, factory):
    box = factory(tmp_path)
    box.store(EInvoiceCredentials(client_id='client-abc123', client_secret=SECRET_MARKER))
    description = box.describe()
    assert SECRET_MARKER not in repr(description)
    assert description['configured'] is True
    assert description['client_id_last4'] == 'c123'
    assert description['backend'] == box.backend_name


def test_describe_when_nothing_stored():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        box = AppSecretDerivedSecretBox(d)
        description = box.describe()
        assert description == {'configured': False, 'client_id_last4': None, 'stored_at': None, 'backend': 'app-secret-aes-gcm'}


def test_describe_on_corrupted_blob_reports_unreadable_not_raise(tmp_path):
    box = AppSecretDerivedSecretBox(str(tmp_path))
    box.store(EInvoiceCredentials(client_id='client-abc123', client_secret=SECRET_MARKER))
    # Corrupt the on-disk blob directly.
    path = box._path
    with open(path, 'r+b') as f:
        f.seek(0)
        f.write(b'\x00' * 16)
    description = box.describe()
    assert description['configured'] is True
    assert description['readable'] is False


def test_corrupted_blob_raises_credentials_unavailable_on_load(tmp_path):
    box = AppSecretDerivedSecretBox(str(tmp_path))
    box.store(EInvoiceCredentials(client_id='client-abc123', client_secret=SECRET_MARKER))
    path = box._path
    with open(path, 'r+b') as f:
        f.seek(0)
        f.write(b'\x00' * 16)
    with pytest.raises(CredentialsUnavailable):
        box.load()


def test_repr_and_str_redact():
    creds = EInvoiceCredentials(client_id='client-abc123', client_secret=SECRET_MARKER)
    assert SECRET_MARKER not in repr(creds)
    assert SECRET_MARKER not in str(creds)
    assert '***' in repr(creds)


@pytest.mark.parametrize('name,factory', BACKEND_FACTORIES)
def test_no_secret_substring_in_logs_across_full_lifecycle(tmp_path, caplog, name, factory):
    caplog.set_level(logging.DEBUG)
    box = factory(tmp_path)
    creds = EInvoiceCredentials(client_id='client-abc123', client_secret=SECRET_MARKER)
    box.store(creds)
    box.load()
    box.describe()
    box.wipe()
    for record in caplog.records:
        assert SECRET_MARKER not in record.getMessage()


def test_get_secret_box_selects_dpapi_on_windows(tmp_path):
    box = get_secret_box(str(tmp_path), 'WINDOWS')
    assert isinstance(box, WindowsDpapiSecretBox)


def test_get_secret_box_selects_app_secret_derived_on_android(tmp_path):
    box = get_secret_box(str(tmp_path), 'ANDROID')
    assert isinstance(box, AppSecretDerivedSecretBox)


def test_store_is_atomic_no_tmp_file_left_behind(tmp_path):
    box = AppSecretDerivedSecretBox(str(tmp_path))
    box.store(EInvoiceCredentials(client_id='client-abc123', client_secret=SECRET_MARKER))
    tmp_leftover = tmp_path / 'einvoicing' / 'credentials.enc.tmp'
    assert not tmp_leftover.exists()
