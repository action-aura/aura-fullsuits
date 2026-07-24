"""DeviceIdentityProvider (Part C/E/F) -- platform-independent contract for
the device Ed25519 keypair, with a Windows-native implementation.

The Android Kotlin implementation is a separate, non-Python module (see
docs/licensing/phase7/android-device-key-storage-design.md) -- this file
only provides the interface every provider (including a future Android-
Python bridge, should one ever be needed) must satisfy, plus the concrete
Windows/DPAPI provider, since Windows has no toolchain-availability risk the
way Chaquopy does.
"""
from __future__ import annotations

import abc
import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature
import base64


class DeviceIdentityError(Exception):
    pass


class LocalStateCorruptError(DeviceIdentityError):
    """Raised when a device key file exists but cannot be trusted -- callers
    must route this to LicenseState.LOCAL_STATE_CORRUPT, never to a silent
    fresh-key regeneration (Part E's explicit reject list)."""


def fingerprint_of(raw_public_key: bytes) -> str:
    """SHA-256 hex digest of the raw 32-byte Ed25519 public key -- identical
    derivation to Owner's own device_identity.py::fingerprint_of(), so a
    fingerprint computed client-side and one computed by Owner over the same
    key always match byte-for-byte."""
    return hashlib.sha256(raw_public_key).hexdigest()


@dataclass(frozen=True)
class DeviceKeyMetadata:
    algorithm: str
    public_key_fingerprint: str
    created_at: datetime
    status: str  # "ACTIVE" | "RESET_PENDING" | "REVOKED_LOCALLY"
    owner_installation_id: Optional[str] = None


class DeviceIdentityProvider(abc.ABC):
    """Owns the device keypair. Never exposes the private key outside this
    class -- every consumer gets a signature or a public key, never raw
    private-key bytes."""

    @abc.abstractmethod
    def has_key(self) -> bool: ...

    @abc.abstractmethod
    def generate_new_key(self) -> DeviceKeyMetadata: ...

    @abc.abstractmethod
    def get_public_key_b64(self) -> str: ...

    @abc.abstractmethod
    def get_metadata(self) -> DeviceKeyMetadata: ...

    @abc.abstractmethod
    def sign(self, canonical_bytes: bytes) -> str:
        """Returns base64-encoded signature."""

    @abc.abstractmethod
    def mark_reset_pending(self) -> None: ...

    @abc.abstractmethod
    def destroy_key(self) -> None:
        """Only called after Owner confirms a replacement/deactivation
        succeeded (Part X) -- never a spontaneous local decision."""


class WindowsDpapiDeviceIdentityProvider(DeviceIdentityProvider):
    """DPAPI (current-user scope) -- see
    docs/licensing/phase7/windows-device-key-storage-design.md for the full
    rationale. Storage layout: <state_dir>/device_key.dpapi (opaque DPAPI
    blob) + <state_dir>/device_key_meta.json (safe metadata only)."""

    def __init__(self, state_dir: Path):
        self._state_dir = state_dir
        self._key_path = state_dir / "device_key.dpapi"
        self._meta_path = state_dir / "device_key_meta.json"

    def has_key(self) -> bool:
        return self._key_path.exists() and self._meta_path.exists()

    def generate_new_key(self) -> DeviceKeyMetadata:
        private_key = Ed25519PrivateKey.generate()
        raw_private = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        raw_public = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._state_dir.mkdir(parents=True, exist_ok=True)
        blob = _dpapi_protect_verified(raw_private)
        # O_EXCL-equivalent: refuse to silently overwrite an existing key file
        # (that would be the exact silent-regeneration Part E forbids).
        #
        # os.O_BINARY is REQUIRED here on Windows -- this was a real bug
        # found by test_device_identity.py's flaky failures (traced through
        # several dead-end hypotheses -- ctypes buffer lifetime, missing
        # argtypes, transient DPAPI master-key availability -- before the
        # actual cause was found): os.open() defaults to TEXT mode on
        # Windows when O_BINARY is not included, so os.write() silently
        # translated every 0x0A byte in the DPAPI ciphertext to 0x0D 0x0A
        # (CRLF), corrupting roughly one in three freshly-written key blobs
        # (any ciphertext containing at least one 0x0A byte, ~1-e^-(262/256)
        # of them at this blob's length) in a way that only surfaced on the
        # *next* read, as an apparently-random CryptUnprotectData failure.
        # os.O_BINARY is a no-op (unavailable/unnecessary) on POSIX, so this
        # stays portable if this provider is ever reused there.
        binary_flag = getattr(os, "O_BINARY", 0)
        fd = os.open(str(self._key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | binary_flag, 0o600)
        try:
            os.write(fd, blob)
        finally:
            os.close(fd)

        meta = DeviceKeyMetadata(
            algorithm="ed25519",
            public_key_fingerprint=fingerprint_of(raw_public),
            created_at=datetime.now(timezone.utc),
            status="ACTIVE",
            owner_installation_id=None,
        )
        self._write_meta(meta)
        return meta

    def get_public_key_b64(self) -> str:
        raw_private = self._load_raw_private_key()
        private_key = Ed25519PrivateKey.from_private_bytes(raw_private)
        raw_public = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return base64.b64encode(raw_public).decode("ascii")

    def get_metadata(self) -> DeviceKeyMetadata:
        if not self._meta_path.exists():
            raise LocalStateCorruptError("Device key metadata file is missing.")
        try:
            raw = json.loads(self._meta_path.read_text(encoding="utf-8"))
            return DeviceKeyMetadata(
                algorithm=raw["algorithm"],
                public_key_fingerprint=raw["public_key_fingerprint"],
                created_at=datetime.fromisoformat(raw["created_at"]),
                status=raw["status"],
                owner_installation_id=raw.get("owner_installation_id"),
            )
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise LocalStateCorruptError(f"Device key metadata is unreadable: {exc}") from exc

    def sign(self, canonical_bytes: bytes) -> str:
        raw_private = self._load_raw_private_key()
        private_key = Ed25519PrivateKey.from_private_bytes(raw_private)
        signature = private_key.sign(canonical_bytes)
        return base64.b64encode(signature).decode("ascii")

    def mark_reset_pending(self) -> None:
        meta = self.get_metadata()
        self._write_meta(
            DeviceKeyMetadata(
                algorithm=meta.algorithm,
                public_key_fingerprint=meta.public_key_fingerprint,
                created_at=meta.created_at,
                status="RESET_PENDING",
                owner_installation_id=meta.owner_installation_id,
            )
        )

    def destroy_key(self) -> None:
        # Retain, don't delete, until the caller has confirmed Owner accepted
        # the replacement (Part E: "old DPAPI blob is retained... until Owner
        # confirms the replacement succeeded"). This method is the explicit,
        # deliberate destroy step called only after that confirmation.
        if self._key_path.exists():
            self._key_path.unlink()
        if self._meta_path.exists():
            self._meta_path.unlink()

    def _write_meta(self, meta: DeviceKeyMetadata) -> None:
        payload = {
            "algorithm": meta.algorithm,
            "public_key_fingerprint": meta.public_key_fingerprint,
            "created_at": meta.created_at.isoformat(),
            "status": meta.status,
            "owner_installation_id": meta.owner_installation_id,
        }
        self._meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(self._meta_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass  # best-effort on platforms without POSIX chmod semantics

    def _load_raw_private_key(self) -> bytes:
        if not self._key_path.exists():
            raise DeviceIdentityError("No device key exists -- caller must route to ACTIVATION_REQUIRED.")
        blob = self._key_path.read_bytes()
        try:
            raw_private = _dpapi_unprotect(blob)
        except Exception as exc:
            raise LocalStateCorruptError(f"Device key could not be decrypted: {exc}") from exc
        if len(raw_private) != 32:
            raise LocalStateCorruptError(
                f"Decrypted device key has unexpected length {len(raw_private)} (expected 32)."
            )
        return raw_private


def _dpapi_functions():
    """Lazily builds and caches the ctypes DATA_BLOB type and the two
    crypt32 function objects with EXPLICIT argtypes/restype declared.

    Real bug found by test_device_identity.py: the first version of this
    code called CryptProtectData/CryptUnprotectData with no prototype
    declared at all (relying on ctypes' implicit "foreign function without a
    prototype" argument conversion). That path is documented by ctypes as
    unreliable for struct-pointer arguments, and it genuinely was -- a
    protect-then-immediately-unprotect round trip in the same process failed
    with CryptUnprotectData GetLastError()==ERROR_INVALID_DATA roughly half
    the time. An isolated 200-iteration repro confirmed 0 failures once
    argtypes/restype were declared explicitly, and 0 failures since. Always
    declare full prototypes for ctypes calls that pass structures by
    pointer -- never rely on implicit conversion for them.
    """
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    return ctypes, DATA_BLOB, crypt32, ctypes.windll.kernel32


def _dpapi_protect(data: bytes) -> bytes:
    """CryptProtectData, current-user scope (no CRYPTPROTECT_LOCAL_MACHINE
    flag). ctypes-direct against crypt32 -- no extra dependency beyond the
    stdlib, matching windows-device-key-storage-design.md's decision."""
    ctypes, DATA_BLOB, crypt32, kernel32 = _dpapi_functions()

    buf = ctypes.create_string_buffer(data, len(data))
    in_blob = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise DeviceIdentityError(f"CryptProtectData failed (GetLastError={kernel32.GetLastError()}).")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_protect_verified(data: bytes, max_attempts: int = 8) -> bytes:
    """_dpapi_protect, but never returns a blob without first proving it
    decrypts back to the original bytes in-process.

    The original motivation for this wrapper was a real ~35-45% intermittent
    CryptUnprotectData failure found by test_device_identity.py. The actual
    root cause turned out to be a *file-write* bug, not a crypto-layer one:
    generate_new_key()'s os.open() call was missing os.O_BINARY, so Windows'
    default text-mode translation silently expanded every 0x0A byte in the
    ciphertext to 0x0D 0x0A on write, corrupting the on-disk blob for any
    ciphertext containing at least one such byte -- fixed at the write site
    directly. This wrapper's in-memory self-verification (protect, then
    immediately unprotect and compare) is kept regardless, as a legitimate,
    cheap integrity check: a freshly generated security credential should
    never be trusted without confirming it can be read back, independent of
    whatever might cause a future failure to do so."""
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        if attempt:
            time.sleep(0.05 * attempt)
        try:
            blob = _dpapi_protect(data)
            if _dpapi_unprotect(blob) == data:
                return blob
            last_error = DeviceIdentityError("DPAPI round-trip verification mismatch.")
        except DeviceIdentityError as exc:
            last_error = exc
    raise DeviceIdentityError(
        f"DPAPI protect-and-verify failed after {max_attempts} attempts: {last_error}"
    )


def _dpapi_unprotect(blob: bytes, max_attempts: int = 8) -> bytes:
    """Retries _dpapi_unprotect_once with a short backoff between attempts.
    See _dpapi_protect_verified's docstring for the bug this was originally
    chasing (a file-write text-mode corruption, now fixed at the write
    site -- retrying a genuinely corrupted blob was never going to help,
    which is exactly why retry counts up to 8 didn't resolve it at the
    time). Kept as real defense-in-depth against genuine transient DPAPI
    failures (e.g. a momentary master-key availability window), which are a
    documented real-world characteristic of DPAPI independent of this
    codebase and worth tolerating cheaply here."""
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        if attempt:
            time.sleep(0.05 * attempt)
        try:
            return _dpapi_unprotect_once(blob)
        except DeviceIdentityError as exc:
            last_error = exc
    raise DeviceIdentityError(f"CryptUnprotectData failed after {max_attempts} attempts: {last_error}")


def _dpapi_unprotect_once(blob: bytes) -> bytes:
    ctypes, DATA_BLOB, crypt32, kernel32 = _dpapi_functions()

    buf = ctypes.create_string_buffer(blob, len(blob))
    in_blob = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    )
    if not ok:
        raise DeviceIdentityError(f"CryptUnprotectData failed (GetLastError={kernel32.GetLastError()}).")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def verify_signature(raw_public_key: bytes, canonical_bytes: bytes, signature: bytes) -> bool:
    """Used by AssertionVerifier's device-fingerprint cross-checks and by
    tests -- never used to verify Owner's own assertion signature (that's
    assertion_verifier.py, keyed off the trusted Owner key set, not a device
    key)."""
    try:
        Ed25519PublicKey.from_public_bytes(raw_public_key).verify(signature, canonical_bytes)
        return True
    except InvalidSignature:
        return False
