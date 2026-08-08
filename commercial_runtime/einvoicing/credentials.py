"""JoFotara e-invoicing -- per-install client_id/client_secret, encrypted at
rest.

Credentials are NEVER stored in a database, NEVER logged, and NEVER synced
anywhere (in particular: never to the Owner Control Center -- see
docs/einvoicing/phase1/product-to-owner-data-boundary-einvoicing.md). They
live at <app_data_dir>/einvoicing/credentials.{dpapi,enc}, one file per
install, protecting them the same way this repo already protects the device
identity private key (Windows) / has no existing at-rest secret on Android
beyond app-sandbox + FDE.

Two backends, selected by platform:

  - WindowsDpapiSecretBox: reuses
    commercial_runtime/licensing_contracts/device_identity.py's
    _dpapi_protect_verified / _dpapi_unprotect verbatim -- does not
    reimplement DPAPI wrapping, and inherits that module's documented
    os.O_BINARY fix (Windows text-mode file writes silently corrupt binary
    blobs containing 0x0A bytes if opened without it -- a real bug that
    module already found and fixed once; not worth re-discovering here).

  - AppSecretDerivedSecretBox (Android and any non-Windows platform):
    AES-GCM via the `cryptography` package (already a Chaquopy dependency),
    key derived by HKDF-SHA256 from this installation's own
    commercial_runtime/security/app_secret.py secret, with a fixed
    info=b"aura-einvoicing-credentials-v1" so this key is cryptographically
    distinct from the Flask SECRET_KEY use of the same underlying secret.
    Honestly documented limitation: on Android this rests on the app-private
    storage sandbox plus device full-disk encryption, not hardware-backed
    Keystore wrapping -- see docs/einvoicing/phase1/credential-storage-design.md
    for the Phase 2 AndroidKeystore upgrade path.

Both backends write via tmp-file + os.replace (atomic) and chmod 0o600
best-effort, mirroring app_secret.py::_generate_and_persist.
"""
from __future__ import annotations

import abc
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


class CredentialsUnavailable(Exception):
    """Raised by load() when no credentials are stored, or when a stored
    blob exists but cannot be decrypted (corruption, or -- on the
    app-secret-derived backend -- the underlying secret.key having been
    regenerated after corruption of its own). Callers must surface
    're-enter your JoFotara credentials', never crash and never fall back to
    a default/plaintext value."""


@dataclass(frozen=True)
class EInvoiceCredentials:
    client_id: str
    client_secret: str

    def __repr__(self) -> str:  # defence-in-depth: even an accidental
        # repr() in a log line or traceback never reveals the secret.
        return "EInvoiceCredentials(client_id='***', client_secret='***')"

    __str__ = __repr__


class SecretBox(abc.ABC):
    @abc.abstractmethod
    def store(self, credentials: EInvoiceCredentials) -> None: ...

    @abc.abstractmethod
    def load(self) -> EInvoiceCredentials:
        """Raises CredentialsUnavailable if nothing is stored or the stored
        blob cannot be decrypted."""

    @abc.abstractmethod
    def exists(self) -> bool: ...

    @abc.abstractmethod
    def wipe(self) -> None: ...

    def describe(self) -> dict:
        """Safe metadata ONLY -- what routes.py::GET /settings serializes.
        Never returns the secret itself, and never raises -- an unreadable
        blob is reported as configured=True, readable=False rather than
        propagating CredentialsUnavailable to a status-check caller."""
        if not self.exists():
            return {'configured': False, 'client_id_last4': None, 'stored_at': None, 'backend': self.backend_name}
        try:
            creds = self.load()
        except CredentialsUnavailable:
            return {'configured': True, 'readable': False, 'client_id_last4': None, 'stored_at': None, 'backend': self.backend_name}
        return {
            'configured': True,
            'readable': True,
            'client_id_last4': creds.client_id[-4:] if len(creds.client_id) >= 4 else creds.client_id,
            'stored_at': self._stored_at(),
            'backend': self.backend_name,
        }

    def _stored_at(self) -> Optional[str]:
        try:
            return datetime.fromtimestamp(os.path.getmtime(self._path), tz=timezone.utc).isoformat()
        except OSError:
            return None

    @property
    @abc.abstractmethod
    def backend_name(self) -> str: ...

    @property
    @abc.abstractmethod
    def _path(self) -> str: ...


def _atomic_write(path: str, data: bytes) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp_path = path + '.tmp'
    binary_flag = getattr(os, 'O_BINARY', 0)  # required on Windows -- see module docstring
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | binary_flag, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp_path, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _serialize(credentials: EInvoiceCredentials) -> bytes:
    return json.dumps({'client_id': credentials.client_id, 'client_secret': credentials.client_secret}).encode('utf-8')


def _deserialize(raw: bytes) -> EInvoiceCredentials:
    payload = json.loads(raw.decode('utf-8'))
    return EInvoiceCredentials(client_id=payload['client_id'], client_secret=payload['client_secret'])


class WindowsDpapiSecretBox(SecretBox):
    backend_name = 'dpapi'

    def __init__(self, app_data_dir: str):
        self._app_data_dir = app_data_dir

    @property
    def _path(self) -> str:
        return os.path.join(self._app_data_dir, 'einvoicing', 'credentials.dpapi')

    def store(self, credentials: EInvoiceCredentials) -> None:
        from commercial_runtime.licensing_contracts.device_identity import _dpapi_protect_verified
        blob = _dpapi_protect_verified(_serialize(credentials))
        _atomic_write(self._path, blob)

    def load(self) -> EInvoiceCredentials:
        if not self.exists():
            raise CredentialsUnavailable("No e-invoicing credentials are stored for this installation.")
        from commercial_runtime.licensing_contracts.device_identity import _dpapi_unprotect
        try:
            with open(self._path, 'rb') as f:
                blob = f.read()
            raw = _dpapi_unprotect(blob)
            return _deserialize(raw)
        except Exception as exc:
            raise CredentialsUnavailable(f"Stored e-invoicing credentials could not be decrypted: {type(exc).__name__}") from exc

    def exists(self) -> bool:
        return os.path.exists(self._path)

    def wipe(self) -> None:
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass


class AppSecretDerivedSecretBox(SecretBox):
    backend_name = 'app-secret-aes-gcm'
    _HKDF_INFO = b'aura-einvoicing-credentials-v1'

    def __init__(self, app_data_dir: str):
        self._app_data_dir = app_data_dir

    @property
    def _path(self) -> str:
        return os.path.join(self._app_data_dir, 'einvoicing', 'credentials.enc')

    def _derive_key(self) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from commercial_runtime.security.app_secret import get_or_create_secret_key

        secret_hex = get_or_create_secret_key(self._app_data_dir)
        secret_bytes = bytes.fromhex(secret_hex)
        return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=self._HKDF_INFO).derive(secret_bytes)

    def store(self, credentials: EInvoiceCredentials) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        import secrets as _secrets

        key = self._derive_key()
        nonce = _secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(nonce, _serialize(credentials), associated_data=None)
        _atomic_write(self._path, nonce + ciphertext)

    def load(self) -> EInvoiceCredentials:
        if not self.exists():
            raise CredentialsUnavailable("No e-invoicing credentials are stored for this installation.")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            with open(self._path, 'rb') as f:
                blob = f.read()
            nonce, ciphertext = blob[:12], blob[12:]
            key = self._derive_key()
            raw = AESGCM(key).decrypt(nonce, ciphertext, associated_data=None)
            return _deserialize(raw)
        except Exception as exc:
            # Covers a corrupted blob AND the case where secret.key was
            # regenerated after its own corruption (app_secret.py's
            # documented fail-safe) -- either way, decryption fails and the
            # caller must ask the operator to re-enter credentials, never
            # crash and never silently fall back to plaintext.
            raise CredentialsUnavailable(f"Stored e-invoicing credentials could not be decrypted: {type(exc).__name__}") from exc

    def exists(self) -> bool:
        return os.path.exists(self._path)

    def wipe(self) -> None:
        try:
            os.remove(self._path)
        except FileNotFoundError:
            pass


def get_secret_box(app_data_dir: str, platform: str) -> SecretBox:
    if platform == 'WINDOWS':
        return WindowsDpapiSecretBox(app_data_dir)
    return AppSecretDerivedSecretBox(app_data_dir)
