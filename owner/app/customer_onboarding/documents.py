"""Encrypted storage of a customer's ID / commercial-registration / payment-slip files.

Why this is separate from expenses/attachments.py: those are shop receipts read
by whoever may see the expense. These are identity documents of the customer's
owner -- personal data kept in the EU (the consent tick on the seller's form says
so) -- so they are encrypted at rest, readable only by a Sales Manager or Super
Admin (never by the seller who took the photo), every read is audited, and they
are purged on a schedule (retention below).

HOW A FILE IS STORED
  1. The type is decided from the bytes (JPEG / PNG / PDF magic numbers), never
     from the browser's say-so; anything else is refused. Max 10 MB.
  2. JPEG and PNG have their metadata stripped (EXIF carries the phone's GPS
     position and the device name; a customer's home address does not belong in a
     KYC file). Done by editing the file's own segments/chunks, with no image
     library: a PDF is stored as it is (a PDF cannot be cleaned safely here; the
     form only offers camera/photo inputs, a PDF is an upload someone chose).
  3. A fresh random 256-bit key (the DEK) encrypts the file with AES-256-GCM. The
     DEK is itself encrypted ("wrapped") with the key-encryption key (KEK) from
     OWNER_KYC_KEYS and stored in the file's header. Both use the document's id
     and the customer's id as AAD, so a blob copied to another document or
     customer fails to decrypt: an attacker with write access to the directory
     cannot swap one customer's ID for another's.
  4. The blob goes to OWNER_KYC_DIR (a docker named volume in production) under
     a server-made opaque name, written to a temp file and renamed, so a crash
     never leaves half a file under the final name.

KEY ROTATION: OWNER_KYC_KEYS is "kid:base64key[,kid2:base64key2]"; the FIRST is
used for new files, every listed key can still read old ones. Retire a key only
after no document names it (enc_key_id).

RETENTION (owner decisions, 2026-10-31): ID / commercial-registration files are
kept while the customer is a customer, plus one year after; if the trial never
converts they are purged 90 days after the trial ended; payment slips are kept 7
years. `purge_after` carries the date and purge_expired_documents() acts on it.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import struct
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import current_app
from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.customer_onboarding import CustomerDocument

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
SLIP_RETENTION_DAYS = 365 * 7
UNCONVERTED_TRIAL_PURGE_DAYS = 90
POST_CUSTOMER_RETENTION_DAYS = 365

_BLOB_MAGIC = b"AKYC1"
_NONCE_BYTES = 12
_WRAPPED_DEK_BYTES = 32 + 16  # AES-GCM ciphertext of a 32-byte key + 16-byte tag

_JPEG = b"\xff\xd8\xff"
_PNG = b"\x89PNG\r\n\x1a\n"
_PDF = b"%PDF-"

# PNG chunks that can carry personal data (EXIF, text with GPS/author, embedded
# colour profiles are harmless but pointless, so only the data-bearing ones go).
_PNG_STRIP_CHUNKS = {b"eXIf", b"tEXt", b"zTXt", b"iTXt", b"tIME"}


class DocumentError(ValueError):
    """A refusal with a stable code; the screens turn it into words. str() is the code."""

    def __init__(self, code: str, **params):
        self.code = code
        self.params = params
        super().__init__(code)


# ---------------------------------------------------------------- keys / dir

def _load_keys() -> dict[str, bytes]:
    raw = (current_app.config.get("KYC_KEYS") or "").strip()
    keys: dict[str, bytes] = {}
    for part in (p.strip() for p in raw.split(",") if p.strip()):
        kid, sep, b64 = part.partition(":")
        kid = kid.strip()
        if not sep or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", kid):
            raise DocumentError("KYC_KEYS_MALFORMED")
        try:
            key = base64.b64decode(b64.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DocumentError("KYC_KEYS_MALFORMED") from exc
        if len(key) != 32 or kid in keys:
            raise DocumentError("KYC_KEYS_MALFORMED")
        keys[kid] = key
    return keys


def active_key() -> tuple[str, bytes]:
    keys = _load_keys()
    if not keys:
        raise DocumentError("KYC_NOT_CONFIGURED")
    kid = next(iter(keys))  # dicts keep insertion order: the first listed key is the active one
    return kid, keys[kid]


def storage_is_ready() -> bool:
    """True when documents can be stored (a key is configured and well formed).
    Checked BEFORE the seller's form writes anything."""
    try:
        active_key()
    except DocumentError:
        return False
    return True


def _root() -> str:
    root = current_app.config["KYC_DIRECTORY"]
    os.makedirs(root, exist_ok=True)
    return root


# --------------------------------------------------------------- file checks

def detect_content_type(content: bytes) -> str:
    if content.startswith(_JPEG):
        return "image/jpeg"
    if content.startswith(_PNG):
        return "image/png"
    if content.startswith(_PDF):
        return "application/pdf"
    raise DocumentError("DOCUMENT_TYPE_NOT_ALLOWED")


def strip_jpeg_metadata(data: bytes) -> bytes:
    """Removes every APPn segment except JFIF (APP0) and the comment segment.
    Walks the marker segments up to start-of-scan (SOS); everything from SOS on
    is image data and is copied untouched. A file this cannot parse cleanly is
    refused rather than stored with its metadata intact."""
    if not data.startswith(_JPEG):
        raise DocumentError("DOCUMENT_CORRUPT")
    out = bytearray(data[:2])
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            raise DocumentError("DOCUMENT_CORRUPT")
        while pos < len(data) and data[pos] == 0xFF:  # fill bytes
            pos += 1
        if pos >= len(data):
            raise DocumentError("DOCUMENT_CORRUPT")
        marker = data[pos]
        pos += 1
        if marker == 0xDA:  # SOS: the rest is entropy-coded image data
            out += b"\xff\xda" + data[pos:]
            return bytes(out)
        if marker == 0xD9:  # EOI before any scan
            out += b"\xff\xd9"
            return bytes(out)
        if 0xD0 <= marker <= 0xD7 or marker in (0x01, 0x00):  # standalone markers
            out += bytes([0xFF, marker])
            continue
        if pos + 2 > len(data):
            raise DocumentError("DOCUMENT_CORRUPT")
        (length,) = struct.unpack(">H", data[pos:pos + 2])
        if length < 2 or pos + length > len(data):
            raise DocumentError("DOCUMENT_CORRUPT")
        segment = data[pos:pos + length]
        keep = not (0xE1 <= marker <= 0xEF)  # APP1..APP15: EXIF, XMP, ICC, vendor data
        if keep:
            out += bytes([0xFF, marker]) + segment
        pos += length
    raise DocumentError("DOCUMENT_CORRUPT")


def strip_png_metadata(data: bytes) -> bytes:
    if not data.startswith(_PNG):
        raise DocumentError("DOCUMENT_CORRUPT")
    out = bytearray(data[:8])
    pos = 8
    saw_end = False
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise DocumentError("DOCUMENT_CORRUPT")
        if ctype not in _PNG_STRIP_CHUNKS:
            out += data[pos:end]
        pos = end
        if ctype == b"IEND":
            saw_end = True
            break
    if not saw_end:
        raise DocumentError("DOCUMENT_CORRUPT")
    return bytes(out)


def clean_file(content: bytes) -> tuple[bytes, str]:
    """(bytes to store, content type). Raises DocumentError for an empty, too big,
    unknown-type or unparseable file."""
    if not content:
        raise DocumentError("DOCUMENT_EMPTY")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise DocumentError("DOCUMENT_TOO_LARGE", max_mb=MAX_DOCUMENT_BYTES // (1024 * 1024))
    content_type = detect_content_type(content)
    if content_type == "image/jpeg":
        return strip_jpeg_metadata(content), content_type
    if content_type == "image/png":
        return strip_png_metadata(content), content_type
    return content, content_type


# ------------------------------------------------------------- encrypt / read

def _aad(document_id, customer_id) -> bytes:
    return f"{document_id}|{customer_id}".encode("ascii")


def seal(plaintext: bytes, *, document_id, customer_id, kid: str, kek: bytes) -> bytes:
    dek = AESGCM.generate_key(bit_length=256)
    aad = _aad(document_id, customer_id)
    wrap_nonce, nonce = os.urandom(_NONCE_BYTES), os.urandom(_NONCE_BYTES)
    wrapped = AESGCM(kek).encrypt(wrap_nonce, dek, aad)
    body = AESGCM(dek).encrypt(nonce, plaintext, aad)
    return _BLOB_MAGIC + wrap_nonce + wrapped + nonce + body


def open_blob(blob: bytes, *, document_id, customer_id, kek: bytes) -> bytes:
    header = len(_BLOB_MAGIC) + _NONCE_BYTES + _WRAPPED_DEK_BYTES + _NONCE_BYTES
    if len(blob) <= header or not blob.startswith(_BLOB_MAGIC):
        raise DocumentError("DOCUMENT_UNREADABLE")
    pos = len(_BLOB_MAGIC)
    wrap_nonce = blob[pos:pos + _NONCE_BYTES]
    pos += _NONCE_BYTES
    wrapped = blob[pos:pos + _WRAPPED_DEK_BYTES]
    pos += _WRAPPED_DEK_BYTES
    nonce = blob[pos:pos + _NONCE_BYTES]
    body = blob[pos + _NONCE_BYTES:]
    aad = _aad(document_id, customer_id)
    try:
        dek = AESGCM(kek).decrypt(wrap_nonce, wrapped, aad)
        return AESGCM(dek).decrypt(nonce, body, aad)
    except InvalidTag as exc:
        raise DocumentError("DOCUMENT_UNREADABLE") from exc


def _purge_date_for(kind: str, now: datetime) -> datetime:
    if kind == "PAYMENT_SLIP":
        return now + timedelta(days=SLIP_RETENTION_DAYS)
    # ID / registration of a trial that has not converted: trial runs at most
    # 14 days before it starts + 7 days + a possible 7-day extension, then 90
    # days. Provisional until the trial actually starts (trial.py moves it) and
    # cleared on conversion (conversion keeps them for the customer's life + 1 year).
    return now + timedelta(days=14 + 7 + 7 + UNCONVERTED_TRIAL_PURGE_DAYS)


def store_document(
    *, customer_id, kind: str, content: bytes, uploaded_by_staff_user_id, payment_record_id=None, now: datetime | None = None
) -> CustomerDocument:
    """Cleans, encrypts, writes the blob and adds the row to db_session (the
    CALLER commits, so the row and the rest of its action are one transaction;
    if that commit fails the blob is an unreferenced orphan, never a row without
    a file -- discard_blob() removes it)."""
    cleaned, content_type = clean_file(content)
    kid, kek = active_key()
    now = now or datetime.now(timezone.utc)
    document_id = uuid.uuid4()
    blob = seal(cleaned, document_id=document_id, customer_id=customer_id, kid=kid, kek=kek)
    storage_key = f"{customer_id}/{document_id}.bin"
    final_path = os.path.join(_root(), *storage_key.split("/"))
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    tmp_path = final_path + ".part"
    with open(tmp_path, "wb") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, final_path)
    document = CustomerDocument(
        id=document_id, customer_id=customer_id, kind=kind, payment_record_id=payment_record_id,
        storage_key=storage_key, content_type=content_type, size_bytes=len(cleaned),
        sha256=hashlib.sha256(cleaned).hexdigest(), enc_key_id=kid,
        uploaded_by_staff_user_id=uploaded_by_staff_user_id, purge_after=_purge_date_for(kind, now),
    )
    db_session.add(document)
    return document


def path_for_key(storage_key: str) -> str:
    """The absolute path of a stored blob; refuses a key that escapes the storage root.
    Public because the seller ID photo (app/seller_id) stores its blobs in the same
    directory with the same crypto and must not grow a second copy of this check."""
    root = os.path.realpath(_root())
    candidate = os.path.realpath(os.path.join(root, *storage_key.split("/")))
    if os.path.commonpath([root, candidate]) != root:
        raise DocumentError("DOCUMENT_UNREADABLE")
    return candidate


def _path_for(document: CustomerDocument) -> str:
    return path_for_key(document.storage_key)


def key_for(kid: str) -> bytes | None:
    """The key-encryption key named `kid` (None when it is no longer configured)."""
    return _load_keys().get(kid)


def discard_blob(document: CustomerDocument) -> None:
    """Removes the file of a row that was never committed (a refused form's orphan)."""
    try:
        os.remove(_path_for(document))
    except (OSError, DocumentError):
        pass


def read_document(document: CustomerDocument) -> tuple[bytes, str]:
    """(plaintext, content type). The caller has ALREADY checked permission."""
    if document.purged_at is not None:
        raise DocumentError("DOCUMENT_PURGED")
    keys = _load_keys()
    kek = keys.get(document.enc_key_id)
    if kek is None:
        raise DocumentError("KYC_KEY_UNAVAILABLE")
    path = _path_for(document)
    if not os.path.isfile(path):
        raise DocumentError("DOCUMENT_UNREADABLE")
    with open(path, "rb") as handle:
        blob = handle.read()
    plaintext = open_blob(blob, document_id=document.id, customer_id=document.customer_id, kek=kek)
    if hashlib.sha256(plaintext).hexdigest() != document.sha256:
        raise DocumentError("DOCUMENT_UNREADABLE")
    return plaintext, document.content_type


def documents_of(customer_id, *, kinds=None) -> list[CustomerDocument]:
    stmt = select(CustomerDocument).where(CustomerDocument.customer_id == customer_id, CustomerDocument.purged_at.is_(None))
    if kinds:
        stmt = stmt.where(CustomerDocument.kind.in_(tuple(kinds)))
    return list(db_session.execute(stmt.order_by(CustomerDocument.created_at)).scalars().all())


def keep_for_customer_lifetime(customer_id) -> int:
    """A trial converted: the ID / registration files are now kept for as long as
    the customer is a customer (purge_after cleared)."""
    rows = [d for d in documents_of(customer_id, kinds=("NATIONAL_ID", "COMMERCIAL_REGISTRATION")) if d.purge_after is not None]
    for row in rows:
        row.purge_after = None
    return len(rows)


IDENTITY_KINDS = ("NATIONAL_ID", "COMMERCIAL_REGISTRATION")


def remove_blob(document: CustomerDocument) -> bool:
    """Deletes the encrypted file of a document. True when it is gone (or was already
    missing). The file holds its own wrapped key, so removing it is what makes the
    content unrecoverable. False = could not be removed (the caller records that)."""
    try:
        os.remove(_path_for(document))
    except FileNotFoundError:
        return True
    except (OSError, DocumentError):
        return False
    return True


def mark_identity_documents_purged(customer_id, *, now: datetime) -> list[CustomerDocument]:
    """Stamps purged_at on every live ID / registration row of the customer. The caller
    commits and THEN calls remove_blob() on each returned row, so a transaction that
    rolls back never destroys a file. Payment slips are never touched (7-year duty)."""
    rows = documents_of(customer_id, kinds=IDENTITY_KINDS)
    for row in rows:
        row.purged_at = now
    return rows


def purge_expired_documents(*, now: datetime | None = None, actor_staff_user_id=None) -> int:
    """Deletes the blob and marks the row purged for every document whose
    purge_after has passed. Idempotent (a purged row is skipped); the row itself
    stays as the audit trail that a file existed. Returns how many were purged."""
    now = now or datetime.now(timezone.utc)
    due = db_session.execute(
        select(CustomerDocument).where(
            CustomerDocument.purge_after.is_not(None), CustomerDocument.purge_after <= now, CustomerDocument.purged_at.is_(None)
        )
    ).scalars().all()
    for document in due:
        try:
            os.remove(_path_for(document))
        except FileNotFoundError:
            pass
        document.purged_at = now
    db_session.commit()
    for document in due:
        audit_record(
            actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code="CUSTOMER_DOCUMENT_PURGED",
            entity_type="customer_document", entity_public_id=str(document.id),
            after_state={"kind": document.kind, "customer_id": str(document.customer_id)},
        )
    return len(due)
