"""Shared crypto helpers for scripts/backup_signing_keys.py and
scripts/restore_signing_keys.py -- the "make the Owner licensing signing
key impossible to lose silently" half of the retail-hardware-viewports
trust-anchor recovery task.

Kept as its own tiny, pure (no filesystem, no CLI) module for the same
reason trust_anchor_loader.py is kept separate from trust_store.py: the
"turn bytes into other bytes" concern stays independently testable and
independently reviewable from "walk a directory of PEM files" (backup) and
"write PEM files to a clean directory" (restore).

WHAT THIS DOES NOT DO: talk to Owner's database, import anything from
owner/**, or know about OWNER_SIGNING_KEY_DIRECTORY as a Flask config value
-- it operates purely on the PEM files themselves, which is all a backup/
restore of key MATERIAL ever needs. owner/app/licensing_service/signing.py
(which this script deliberately does not import, since another agent owns
owner/** while this was written) already defines the on-disk shape this
matches: PKCS8, unencrypted-at-rest-on-the-signing-host PEM, named
`{key_id}.pem`, `key_id` restricted to `^[A-Za-z0-9._-]{1,64}$`. That
restriction is re-validated here independently rather than trusted from a
backup file, because a backup manifest is the one artifact in this whole
flow that might travel through a less-trusted medium (a USB drive, cloud
storage, an email attachment) before restore reads it back -- the exact
same reasoning as signing.py's own `_safe_key_path`.

SECURITY MODEL. The private key bytes are the one thing that must never
leave this process in the clear once encrypted: every PEM is encrypted with
Fernet (authenticated symmetric encryption -- AES128-CBC + HMAC-SHA256, no
hand-rolled crypto) under a key derived from an operator passphrase via
scrypt (deliberately memory-hard, so a stolen backup file cannot be
brute-forced cheaply offline). Everything else recorded alongside the
ciphertext -- key_id, the derived public key, a signature over a fixed
canary message -- is already non-secret: Owner publishes exactly this
information in every trust-anchor and key-set manifest it ever emits. That
split is what makes the "real proof" restore check possible without ever
needing a live Owner or a live database: verify_restored_key() below proves
the RESTORED private key produced the SAME signature the ORIGINAL private
key produced at backup time, by checking it against the public key alone.
"""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# Mirrors owner/app/licensing_service/signing.py::_KEY_ID_RE exactly (kept
# as a literal copy, not an import, since this module must not depend on
# owner/** -- see module docstring). Any drift between the two is a real
# bug worth catching; commercial_runtime/licensing_contracts/tests covers
# that via a byte-for-byte comparison against the pattern string.
KEY_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Binds a canary signature to the specific key_id it was produced by, so a
# ciphertext entry swapped between two key_id slots in a tampered/corrupted
# manifest fails verification (wrong message) rather than merely producing
# "a valid-looking signature for the wrong key".
_CANARY_PREFIX = b"aura-signing-key-backup-canary-v1:"

# scrypt cost parameters. N=2**14 (16384) keeps a single derivation under
# ~150ms on ordinary hardware (this runs once per backup/restore, not in a
# hot path) while still being far more expensive to brute-force offline
# than a fast hash -- these are the library's own commonly-recommended
# interactive-use parameters, not invented here.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_SALT_LEN = 16
_SCRYPT_KEY_LEN = 32  # Fernet requires a 32-byte key


class SigningKeyBackupError(ValueError):
    """Raised for any backup/restore integrity failure -- a wrong
    passphrase, a corrupted archive, a restored key that does not match its
    recorded public key or canary signature. Never carries key bytes or the
    passphrase in its message."""


def new_salt() -> bytes:
    return os.urandom(_SCRYPT_SALT_LEN)


def derive_fernet_key(passphrase: str, salt: bytes) -> bytes:
    """passphrase + salt -> a 32-byte key, base64-urlsafe-encoded the way
    Fernet requires. Deterministic for the same (passphrase, salt) pair --
    restore must re-derive the identical key from the same salt recorded in
    the backup file, never generate a new one."""
    if not passphrase:
        raise SigningKeyBackupError("Refusing to derive a backup key from an empty passphrase.")
    kdf = Scrypt(salt=salt, length=_SCRYPT_KEY_LEN, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _canary_message(key_id: str) -> bytes:
    return _CANARY_PREFIX + key_id.encode("utf-8")


@dataclass(frozen=True)
class BackedUpKeyEntry:
    """One key's worth of a backup manifest. `public_key_b64` and
    `canary_signature_b64` are NOT secret -- they are exactly what a
    verifier needs and nothing more, matching trust_anchor.json's own
    "public key + signature, never the private half" shape. `ciphertext_b64`
    is the only field that needs the passphrase to mean anything."""

    key_id: str
    public_key_b64: str
    canary_signature_b64: str
    ciphertext_b64: str


def build_key_entry(key_id: str, pem_bytes: bytes, fernet_key: bytes) -> BackedUpKeyEntry:
    """Encrypts one PEM's raw bytes and records the non-secret proof
    material a later restore will check itself against. Raises
    SigningKeyBackupError if `pem_bytes` is not a loadable Ed25519 private
    key -- a backup must never silently include something that is not
    actually a usable key."""
    if not KEY_ID_RE.match(key_id):
        raise SigningKeyBackupError(f"Refusing unsafe signing key id: {key_id!r}")
    try:
        private_key = serialization.load_pem_private_key(pem_bytes, password=None)
    except Exception as exc:
        raise SigningKeyBackupError(f"{key_id}.pem is not a loadable private key: {exc}") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SigningKeyBackupError(f"{key_id}.pem is not an Ed25519 private key.")

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    signature = private_key.sign(_canary_message(key_id))
    ciphertext = Fernet(fernet_key).encrypt(pem_bytes)

    return BackedUpKeyEntry(
        key_id=key_id,
        public_key_b64=base64.b64encode(public_bytes).decode("ascii"),
        canary_signature_b64=base64.b64encode(signature).decode("ascii"),
        ciphertext_b64=base64.b64encode(ciphertext).decode("ascii"),
    )


def decrypt_and_verify_entry(entry: dict, fernet_key: bytes) -> bytes:
    """THE ACTUAL PROOF A RESTORE IS GOOD -- not "a file was written", not
    "the PEM parses", but: decrypt with the operator's passphrase, load the
    result as a real Ed25519 private key, derive ITS public key, and check
    that public key against BOTH the public key AND the canary signature
    recorded at export time. Ed25519 key derivation and signing are both
    deterministic, so this can only pass if the restored private key is the
    exact same key material the original export encrypted -- a single
    flipped bit anywhere in the ciphertext, a wrong passphrase, or a
    manifest entry that got shuffled between key_ids all fail here, loudly,
    every one of them proven by a real mutation in this task's test suite.

    Returns the decrypted PEM bytes on success. Raises SigningKeyBackupError
    (never a bare cryptography exception) on ANY failure -- wrong
    passphrase, corrupted ciphertext, or a restored key that does not match
    its recorded proof -- so a caller can tell "verification genuinely
    failed" from "some other bug", without ever needing to inspect key
    bytes to know which.
    """
    key_id = entry.get("key_id")
    if not isinstance(key_id, str) or not KEY_ID_RE.match(key_id):
        raise SigningKeyBackupError(f"Backup entry has an unsafe or missing key_id: {key_id!r}")

    try:
        ciphertext = base64.b64decode(entry["ciphertext_b64"])
    except Exception as exc:
        raise SigningKeyBackupError(f"{key_id}: ciphertext_b64 is not valid base64: {exc}") from exc

    try:
        pem_bytes = Fernet(fernet_key).decrypt(ciphertext)
    except InvalidToken as exc:
        raise SigningKeyBackupError(
            f"{key_id}: could not decrypt -- wrong passphrase, or the backup file is corrupted."
        ) from exc

    try:
        private_key = serialization.load_pem_private_key(pem_bytes, password=None)
    except Exception as exc:
        raise SigningKeyBackupError(f"{key_id}: decrypted bytes are not a loadable private key: {exc}") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise SigningKeyBackupError(f"{key_id}: decrypted key is not Ed25519.")

    restored_public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    recorded_public_bytes = base64.b64decode(entry["public_key_b64"])
    if restored_public_bytes != recorded_public_bytes:
        raise SigningKeyBackupError(
            f"{key_id}: restored key's public key does not match the one recorded at backup time -- "
            "this is NOT the same key that was backed up."
        )

    try:
        private_key.public_key().verify(
            base64.b64decode(entry["canary_signature_b64"]), _canary_message(key_id)
        )
    except Exception as exc:
        raise SigningKeyBackupError(
            f"{key_id}: restored key failed to verify its own backup-time canary signature: {exc}"
        ) from exc

    return pem_bytes
