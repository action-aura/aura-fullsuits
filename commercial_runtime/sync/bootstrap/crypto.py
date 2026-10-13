"""End-to-end encryption of a bootstrap copy while it sits in Owner's mailbox.

WHY. Owner relays the copy but must not be able to read it: it is a whole shop's
sales, customers and staff rows. So the REQUESTER makes a single-use X25519 key
pair and sends only the public half with its request; the PRODUCER makes its own
ephemeral pair, derives a shared secret with the requester's public key, runs it
through HKDF-SHA256 and encrypts the zip in 4 MB chunks with AES-256-GCM. Owner
stores ciphertext and public values; only the requester's private key (never
sent anywhere, never reused) can open it.

WHAT THE CHUNK BINDING PREVENTS. Every chunk's AAD (additional authenticated
data) carries the request id, the chunk's index AND the total chunk count:
  * a chunk swapped into another position fails (index),
  * a chunk lifted from another request's copy fails (request id),
  * a truncated or extended copy fails (total) -- without the total, an attacker
    holding the mailbox could drop the last chunks and every remaining chunk would
    still authenticate, silently handing the device a short database.
The nonce is the chunk index (the key is single-use per request, so an index is a
unique nonce under it). `decrypt_chunk` refuses any chunk it cannot authenticate;
there is no "best effort" path.

The signed manifest INSIDE the zip (producer.py) still has to verify after
decryption: encryption says "nobody but the requester read this"; the signature
says "a member of this shop made it". Both are needed, neither replaces the other.
"""
from __future__ import annotations

import base64
import os
import struct
from dataclasses import dataclass
from typing import Iterator

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from commercial_runtime.sync.bootstrap import errors
from commercial_runtime.sync.bootstrap.errors import BootstrapError

CHUNK_SIZE = 4 * 1024 * 1024
FORMAT = "aura-bootstrap-enc/1"
_HKDF_INFO = b"aura-bootstrap-enc/1 key"
_SALT_BYTES = 32
TAG_BYTES = 16


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str, what: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise BootstrapError(errors.BAD_PACKAGE, f"{what} is not valid base64") from exc


@dataclass(frozen=True)
class RequesterKeys:
    """The requester's single-use pair. `private_b64` is what the device keeps on
    disk beside its pending request; `public_b64` is what goes to Owner."""
    private_b64: str
    public_b64: str


def new_requester_keys() -> RequesterKeys:
    priv = X25519PrivateKey.generate()
    return RequesterKeys(
        private_b64=_b64(priv.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())),
        public_b64=_b64(priv.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
    )


def _aad(request_id: str, index: int, total: int) -> bytes:
    return FORMAT.encode("ascii") + b"|" + str(request_id).encode("ascii") + b"|" + struct.pack(">II", index, total)


def _nonce(index: int) -> bytes:
    return b"\x00\x00\x00\x00" + struct.pack(">Q", index)


def _derive(shared: bytes, salt: bytes, request_id: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=_HKDF_INFO + b"|" + str(request_id).encode("ascii")).derive(shared)


def _public_from(b64: str, what: str) -> X25519PublicKey:
    raw = _unb64(b64, what)
    if len(raw) != 32:
        raise BootstrapError(errors.BAD_PACKAGE, f"{what} is not a 32-byte X25519 key")
    return X25519PublicKey.from_public_bytes(raw)


@dataclass(frozen=True)
class EncryptionParams:
    """The PUBLIC facts the requester needs to decrypt; Owner stores them in the
    request's manifest. Nothing here is secret."""
    ephemeral_pub_b64: str
    salt_b64: str
    chunk_count: int
    plaintext_size: int

    def as_dict(self) -> dict:
        return {"format": FORMAT, "ephemeral_pub": self.ephemeral_pub_b64, "salt": self.salt_b64,
                "chunk_count": self.chunk_count, "plaintext_size": self.plaintext_size}


def chunk_count_for(size: int) -> int:
    return max(1, -(-size // CHUNK_SIZE))


def encrypt_package(data: bytes, requester_pub_b64: str, request_id: str) -> tuple[EncryptionParams, Iterator[bytes]]:
    """Encrypt `data` for the holder of `requester_pub_b64`. Returns the public
    params and a generator of ciphertext chunks (index order). Chunks are made
    lazily so a large shop copy is never held twice in memory."""
    requester_pub = _public_from(requester_pub_b64, "the requester key")
    eph = X25519PrivateKey.generate()
    salt = os.urandom(_SALT_BYTES)
    key = _derive(eph.exchange(requester_pub), salt, request_id)
    total = chunk_count_for(len(data))
    params = EncryptionParams(
        ephemeral_pub_b64=_b64(eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)),
        salt_b64=_b64(salt), chunk_count=total, plaintext_size=len(data))
    aes = AESGCM(key)
    size = CHUNK_SIZE

    def chunks() -> Iterator[bytes]:
        for i in range(total):
            piece = data[i * size:(i + 1) * size]
            yield aes.encrypt(_nonce(i), piece, _aad(request_id, i, total))
    return params, chunks()


class ChunkDecryptor:
    """Holds the derived key for one request; opens chunks one at a time."""

    def __init__(self, requester_private_b64: str, params: dict, request_id: str):
        try:
            priv = X25519PrivateKey.from_private_bytes(_unb64(requester_private_b64, "the requester private key"))
        except ValueError as exc:
            raise BootstrapError(errors.BAD_PACKAGE, "the requester private key is malformed") from exc
        if params.get("format") != FORMAT:
            raise BootstrapError(errors.BAD_PACKAGE, "the copy uses an encryption format this build does not know")
        eph = _public_from(str(params.get("ephemeral_pub", "")), "the producer key")
        salt = _unb64(str(params.get("salt", "")), "the salt")
        total = params.get("chunk_count")
        if not isinstance(total, int) or isinstance(total, bool) or total < 1:
            raise BootstrapError(errors.BAD_PACKAGE, "the copy names no valid chunk count")
        self._aes = AESGCM(_derive(priv.exchange(eph), salt, request_id))
        self._request_id = str(request_id)
        self.total = total

    def decrypt_chunk(self, index: int, blob: bytes) -> bytes:
        if not (0 <= index < self.total):
            raise BootstrapError(errors.BAD_PACKAGE, f"chunk {index} is outside 0..{self.total - 1}")
        try:
            return self._aes.decrypt(_nonce(index), blob, _aad(self._request_id, index, self.total))
        except InvalidTag as exc:
            raise BootstrapError(
                errors.CHECKSUM_MISMATCH,
                f"chunk {index} failed authentication (wrong key, tampered, or from another copy)") from exc


def decrypt_package(chunks: list, requester_private_b64: str, params: dict, request_id: str) -> bytes:
    """Convenience: decrypt every chunk (in index order) and join them."""
    dec = ChunkDecryptor(requester_private_b64, params, request_id)
    if len(chunks) != dec.total:
        raise BootstrapError(errors.BAD_PACKAGE, f"expected {dec.total} chunks, got {len(chunks)}")
    out = b"".join(dec.decrypt_chunk(i, c) for i, c in enumerate(chunks))
    size = params.get("plaintext_size")
    if isinstance(size, int) and len(out) != size:
        raise BootstrapError(errors.CHECKSUM_MISMATCH, "the decrypted size does not match the declared size")
    return out
