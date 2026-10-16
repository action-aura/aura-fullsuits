"""SYNC-BOOT Phase 3: the bootstrap MAILBOX (`/api/sync/v1/bootstrap/...`).

WHAT THIS IS. A device whose pull was refused with RESYNC_REQUIRED cannot be
replayed history Owner has already pruned. It asks a peer that HOLDS the shop's
data for a consistent copy; Owner is only the mailbox between them, and cannot
read what it carries (the producer encrypts to a single-use key the requester
made -- commercial_runtime/sync/bootstrap/crypto.py). The lifecycle lives on
`SyncBootstrapRequest` (models/sync.py); this module is every transition:

  request   requester   -> OPEN      (one open row per (installation, stream))
  claim     a peer      -> CLAIMED   (first claim wins, 30-minute lease)
  chunk     the claimer             (encrypted bytes onto the VM's disk)
  complete  the claimer -> READY     (every chunk present, counted, hashed)
  download  requester               (one chunk per call)
  ack       requester   -> DELIVERED (the bytes are DELETED now)
  fulfil    a peer      -> FULFILLED (the authority half: see below)
  status    requester               (poll)

THE TWO HALVES. Business data travels as the encrypted copy. AUTHORITY data
(users, permissions, till numbers, who is main) is never copied: the copy has it
scrubbed, and the MAIN computer re-announces it, signed, as ordinary events. The
ORDER matters and is enforced here: the re-announce events must land AFTER the
copy's `as_of`, because the copy has no authority rows to fall back on and the
requester resumes pulling from `as_of`. So `fulfil` for a retail request is
refused until the copy is READY (`TOO_EARLY`); events pushed after that have a
seq above any `as_of` the copy could name. A registry-stream request has no
copy: it is OPEN until the main's re-announce, which lands above the
`fast_forward_to` recorded when it was made.

SECURITY MODEL. Every route goes through the same `_authenticate` as push and
pull (own nonce scope, `sync_bootstrap`); the licence comes from the VERIFIED
installation, never the body, and EVERY lookup filters on it -- a request id from
another licence is "not found", not "forbidden". Role checks: only the requester
may download/ack/status, only the claim holder may upload/complete. `fulfil`
trusts a licence member's word that the authority half was done: it is a status
hint only, the signed authority events themselves are what receiving devices
verify. Chunks are opaque ciphertext; Owner stores them, hashes them (so a file
altered on disk is caught before decryption) and caps their total size.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import os
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import current_app, jsonify, request
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.extensions import db_session
from app.models.sync import BOOTSTRAP_OPEN_STATUSES, SyncBootstrapRequest, SyncDeviceCursor, SyncEvent
from app.sync.routes import SyncAuthError, _authenticate, _error, _license_pruned_through, bp

logger = logging.getLogger(__name__)

STREAMS = ("retail", "registry")
REASONS = ("join", "resync", "offline", "restore")
#: A request nobody finishes within this is EXPIRED and its bytes removed.
REQUEST_TTL = timedelta(hours=72)
#: A producer holds the request this long; after it, the next claim wins.
CLAIM_LEASE = timedelta(minutes=30)
#: One chunk is 4 MiB of plaintext + a 16-byte GCM tag (crypto.CHUNK_SIZE); a little headroom.
MAX_CHUNK_BYTES = 4 * 1024 * 1024 + 1024
#: Hard ceiling on chunks per request (the byte cap bites first; this bounds the file-name space).
MAX_CHUNKS = 4096
#: How many open requests one pull response carries.
MAX_LISTED = 20
_B64_KEY_MAX = 64


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _base_dir() -> Path:
    return Path(current_app.config["SYNC_BOOTSTRAP_DIRECTORY"])


def _blob_dir(row: SyncBootstrapRequest) -> Path:
    # blob_ref is "<request uuid>", generated here -- never anything a client sent.
    return _base_dir() / str(row.id)


def _remove_blob(row_id) -> None:
    shutil.rmtree(_base_dir() / str(row_id), ignore_errors=True)


def _chunk_path(row: SyncBootstrapRequest, index: int) -> Path:
    return _blob_dir(row) / f"{index:06d}.chunk"


def _reply(reason_code: str, status: int = 409, **extra):
    return jsonify({"reason_code": reason_code, **extra}), status


def _as_utc(value):
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


def _view(row: SyncBootstrapRequest) -> dict:
    manifest = dict(row.manifest or {})
    return {
        "request_id": str(row.id),
        "stream": row.stream,
        "status": row.status,
        "reason": row.reason,
        "min_as_of": row.min_as_of,
        "fast_forward_to": row.fast_forward_to,
        "expires_at": row.expires_at.isoformat(),
        "claim_expires_at": row.claim_expires_at.isoformat() if row.claim_expires_at else None,
        "authority_fulfilled": manifest.get("authority_fulfilled") is True,
        # Public transport facts only (ephemeral public key, salt, counts, hashes).
        "manifest": {k: v for k, v in manifest.items() if k != "authority_fulfilled"},
    }


def _claim_view(row: SyncBootstrapRequest) -> dict:
    """What the producer gets back from a claim: the request plus the requester's
    PUBLIC key -- the one thing it needs to encrypt the copy to. (Public by
    construction; it is not in `_view` because nobody else needs it.)"""
    return {**_view(row), "enc_pubkey": row.enc_pubkey}


def _authenticate_or_error(body):
    try:
        return _authenticate(body or {}, nonce_scope="sync_bootstrap"), None
    except SyncAuthError as exc:
        return None, _error(exc.reason_code)


def _load(body, license_id) -> SyncBootstrapRequest | None:
    """The request named by the body, ONLY if it belongs to this licence."""
    try:
        rid = uuid.UUID(str(body.get("request_id")))
    except (ValueError, TypeError, AttributeError):
        return None
    return db_session.execute(
        select(SyncBootstrapRequest)
        .where(SyncBootstrapRequest.id == rid, SyncBootstrapRequest.license_id == license_id)
        .with_for_update()
    ).scalar_one_or_none()


def _expire_if_due(row: SyncBootstrapRequest) -> bool:
    """Lazily expires an open row past its 72 h; True = it is now EXPIRED."""
    if row.status in BOOTSTRAP_OPEN_STATUSES and _as_utc(row.expires_at) < _now():
        row.status = "EXPIRED"
        _remove_blob(row.id)
        return True
    return False


def _lease_lapsed(row: SyncBootstrapRequest) -> bool:
    return row.status == "CLAIMED" and (row.claim_expires_at is None or _as_utc(row.claim_expires_at) < _now())


def _licence_max_seq(license_id) -> int:
    return int(db_session.execute(
        select(func.coalesce(func.max(SyncEvent.seq), 0)).where(SyncEvent.license_id == license_id)).scalar_one())


def _valid_pubkey(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > _B64_KEY_MAX:
        return False
    try:
        return len(base64.b64decode(value, validate=True)) == 32
    except (binascii.Error, ValueError):
        return False


def _open_row_for(installation_id, stream) -> SyncBootstrapRequest | None:
    return db_session.execute(
        select(SyncBootstrapRequest).where(
            SyncBootstrapRequest.requester_installation_id == installation_id,
            SyncBootstrapRequest.stream == stream,
            SyncBootstrapRequest.status.in_(BOOTSTRAP_OPEN_STATUSES),
        ).with_for_update()
    ).scalar_one_or_none()


def open_or_create_request(
    installation, license_id, *, stream: str, reason: str, enc_pubkey: str,
    schema_version=None, app_version=None,
) -> SyncBootstrapRequest:
    """The one open request of (installation, stream), creating it if there is none.
    A retail request with a DIFFERENT key supersedes the old one (the device lost
    the private half -- e.g. a restart before staging -- so the old copy could
    never be opened). Caller commits."""
    existing = _open_row_for(installation.id, stream)
    if existing is not None and _expire_if_due(existing):
        existing = None
    if existing is not None:
        if stream == "retail" and enc_pubkey and existing.enc_pubkey != enc_pubkey:
            existing.status = "FAILED"
            _remove_blob(existing.id)
            db_session.flush()
        else:
            return existing
    if stream == "retail":
        own_top = int(db_session.execute(
            select(func.coalesce(func.max(SyncEvent.seq), 0)).where(
                SyncEvent.license_id == license_id, SyncEvent.device_id == installation.id)).scalar_one())
        fast_forward = None
    else:
        own_top = 0
        fast_forward = _licence_max_seq(license_id)
    row = SyncBootstrapRequest(
        license_id=license_id, requester_installation_id=installation.id, stream=stream, reason=reason,
        status="OPEN", min_as_of=own_top, fast_forward_to=fast_forward,
        requester_schema_version=schema_version, requester_app_version=app_version,
        enc_pubkey=enc_pubkey, expires_at=_now() + REQUEST_TTL, manifest={})
    db_session.add(row)
    db_session.flush()
    return row


def record_registry_request(installation, license_id) -> None:
    """Called by pull() when a REGISTRY pull is refused with RESYNC_REQUIRED: the
    main computer is told (through `bootstrap_requests`) that this device is
    waiting for a fresh signed re-announce. Never raises into the pull."""
    try:
        open_or_create_request(
            installation, license_id, stream="registry", reason="resync", enc_pubkey="")
        db_session.commit()
    except Exception:  # noqa: BLE001 -- the 409 answer matters more than the note
        db_session.rollback()
        logger.warning("Could not record a registry bootstrap request.", exc_info=True)


def pending_for(installation, license_id) -> list:
    """The open requests a producer-capable puller can act on, for the pull
    response. Retail OPEN = a peer builds the copy; retail READY/DELIVERED and
    registry OPEN = the MAIN re-announces. The device decides which it is."""
    rows = db_session.execute(
        select(SyncBootstrapRequest).where(
            SyncBootstrapRequest.license_id == license_id,
            SyncBootstrapRequest.requester_installation_id != installation.id,
            SyncBootstrapRequest.status.in_(BOOTSTRAP_OPEN_STATUSES),
            SyncBootstrapRequest.expires_at > _now(),
        ).order_by(SyncBootstrapRequest.created_at).limit(MAX_LISTED * 2)
    ).scalars().all()
    out = []
    for r in rows:
        fulfilled = (r.manifest or {}).get("authority_fulfilled") is True
        status = "OPEN" if _lease_lapsed(r) else r.status
        wants_peer = r.stream == "retail" and status == "OPEN"
        wants_main = not fulfilled and (
            (r.stream == "registry" and status == "OPEN")
            or (r.stream == "retail" and status in ("READY", "DELIVERED")))
        if not (wants_peer or wants_main):
            continue
        out.append({
            "request_id": str(r.id), "requester": str(r.requester_installation_id), "stream": r.stream,
            "status": status, "requested_at": r.created_at.isoformat(), "min_as_of": r.min_as_of,
            "requester_schema_version": r.requester_schema_version,
        })
        if len(out) >= MAX_LISTED:
            break
    return out


# ── routes ──────────────────────────────────────────────────────────────────

@bp.route("/bootstrap/request", methods=["POST"])
def bootstrap_request():
    body = request.get_json(silent=True)
    auth, err = _authenticate_or_error(body)
    if err:
        return err
    installation, license_id = auth
    stream = body.get("stream")
    if stream not in STREAMS:
        return _error("INVALID_STREAM")
    reason = body.get("reason") if body.get("reason") in REASONS else "join"
    enc_pubkey = body.get("enc_pubkey") or ""
    # A retail copy is encrypted to this key; without it there is nothing to
    # encrypt to and the producer could only send plaintext. A registry request
    # carries no copy, so it needs none.
    if stream == "retail" and not _valid_pubkey(enc_pubkey):
        return _error("INVALID_ENC_PUBKEY")
    schema_version = body.get("schema_version")
    if schema_version is not None and (not isinstance(schema_version, int) or isinstance(schema_version, bool)):
        return _error("INVALID_SCHEMA_VERSION")
    app_version = body.get("app_version")
    app_version = app_version[:64] if isinstance(app_version, str) else None
    try:
        row = open_or_create_request(
            installation, license_id, stream=stream, reason=reason, enc_pubkey=enc_pubkey if stream == "retail" else "",
            schema_version=schema_version, app_version=app_version)
        db_session.commit()
    except IntegrityError:
        # Two simultaneous requests from one device: the partial unique index
        # let exactly one in. The loser reads the winner's row.
        db_session.rollback()
        row = _open_row_for(installation.id, stream)
        if row is None:
            return _error("INTERNAL_ERROR", 500)
    return jsonify(_view(row)), 200


@bp.route("/bootstrap/status", methods=["POST"])
def bootstrap_status():
    body = request.get_json(silent=True)
    auth, err = _authenticate_or_error(body)
    if err:
        return err
    installation, license_id = auth
    if body.get("request_id") is not None:
        row = _load(body, license_id)
    else:
        row = db_session.execute(
            select(SyncBootstrapRequest).where(
                SyncBootstrapRequest.requester_installation_id == installation.id,
                SyncBootstrapRequest.stream == body.get("stream"),
            ).order_by(SyncBootstrapRequest.created_at.desc()).limit(1)
        ).scalar_one_or_none()
    if row is None or row.requester_installation_id != installation.id:
        return _reply("BOOTSTRAP_NOT_FOUND", 404)
    if _expire_if_due(row):
        db_session.commit()
    return jsonify(_view(row)), 200


@bp.route("/bootstrap/claim", methods=["POST"])
def bootstrap_claim():
    body = request.get_json(silent=True)
    auth, err = _authenticate_or_error(body)
    if err:
        return err
    installation, license_id = auth
    row = _load(body, license_id)
    if row is None:
        return _reply("BOOTSTRAP_NOT_FOUND", 404)
    if _expire_if_due(row):
        db_session.commit()
        return _reply("BOOTSTRAP_EXPIRED")
    if row.requester_installation_id == installation.id:
        return _reply("SELF_CLAIM", 400)
    if row.stream != "retail":
        return _reply("NOT_CLAIMABLE")
    schema_version = body.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        return _error("INVALID_SCHEMA_VERSION")
    if row.requester_schema_version is not None and schema_version > row.requester_schema_version:
        # The requester can migrate a copy forward but cannot read a newer one.
        return _reply("SCHEMA_NEWER")
    if row.status == "CLAIMED" and row.producer_installation_id == installation.id and not _lease_lapsed(row):
        return jsonify(_claim_view(row)), 200  # the same producer asking again: idempotent
    # A producer must itself have pulled the shop at least up to the requester's
    # own events, or its copy would be missing them.
    producer_cursor = db_session.get(SyncDeviceCursor, (installation.id, "retail"))
    if producer_cursor is None or producer_cursor.last_acked_seq < row.min_as_of:
        return _reply("PRODUCER_BEHIND")
    now = _now()
    # THE RACE GUARD. Two producers can both read the row as OPEN; only an
    # UPDATE that still sees it OPEN (or its lease lapsed) in the same statement
    # can win. Rowcount 1 = this call won; 0 = someone else did first.
    won = db_session.execute(
        update(SyncBootstrapRequest)
        .where(
            SyncBootstrapRequest.id == row.id,
            or_(SyncBootstrapRequest.status == "OPEN",
                and_(SyncBootstrapRequest.status == "CLAIMED", SyncBootstrapRequest.claim_expires_at < now)))
        .values(status="CLAIMED", producer_installation_id=installation.id,
                claim_expires_at=now + CLAIM_LEASE, blob_ref=str(row.id))
    ).rowcount
    if won != 1:
        db_session.rollback()
        return _reply("ALREADY_CLAIMED")
    _remove_blob(row.id)                       # leftovers of a lapsed earlier claim
    _blob_dir(row).mkdir(parents=True, exist_ok=True)
    db_session.commit()
    db_session.refresh(row)
    return jsonify(_claim_view(row)), 200


def _claim_holder_row(body, installation, license_id):
    """The request, if `installation` holds its live claim. -> (row, error response)."""
    row = _load(body, license_id)
    if row is None:
        return None, _reply("BOOTSTRAP_NOT_FOUND", 404)
    if _expire_if_due(row):
        db_session.commit()
        return None, _reply("BOOTSTRAP_EXPIRED")
    if row.status != "CLAIMED" or row.producer_installation_id != installation.id:
        return None, _reply("NOT_CLAIM_HOLDER", 403)
    if _lease_lapsed(row):
        return None, _reply("CLAIM_EXPIRED")
    return row, None


def _dir_bytes(directory: Path, skip: Path | None = None) -> int:
    total = 0
    if directory.is_dir():
        for p in directory.iterdir():
            if p.is_file() and p != skip:
                total += p.stat().st_size
    return total


@bp.route("/bootstrap/chunk", methods=["POST"])
def bootstrap_chunk_up():
    body = request.get_json(silent=True)
    auth, err = _authenticate_or_error(body)
    if err:
        return err
    installation, license_id = auth
    row, bad = _claim_holder_row(body, installation, license_id)
    if bad:
        return bad
    index = body.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < MAX_CHUNKS):
        return _error("INVALID_CHUNK_INDEX")
    try:
        raw = base64.b64decode(str(body.get("data") or ""), validate=True)
    except (binascii.Error, ValueError):
        return _error("INVALID_CHUNK")
    if not raw or len(raw) > MAX_CHUNK_BYTES:
        return _error("INVALID_CHUNK")
    target = _chunk_path(row, index)
    if _dir_bytes(_blob_dir(row), skip=target) + len(raw) > current_app.config["SYNC_BOOTSTRAP_MAX_BYTES"]:
        return _reply("TOO_LARGE", 413)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, target)                    # a half-written chunk is never visible
    return jsonify({"stored": index, "bytes": len(raw)}), 200


def _clean_encryption(value, chunk_count: int):
    """Only the known PUBLIC fields, with the shapes the requester's decryptor
    expects; anything else is dropped rather than stored."""
    if not isinstance(value, dict):
        return None
    out = {"format": value.get("format"), "ephemeral_pub": value.get("ephemeral_pub"),
           "salt": value.get("salt"), "chunk_count": value.get("chunk_count"),
           "plaintext_size": value.get("plaintext_size")}
    if not all(isinstance(out[k], str) and 0 < len(out[k]) <= 128 for k in ("format", "ephemeral_pub", "salt")):
        return None
    if out["chunk_count"] != chunk_count or not isinstance(out["plaintext_size"], int) or isinstance(out["plaintext_size"], bool):
        return None
    return out


@bp.route("/bootstrap/complete", methods=["POST"])
def bootstrap_complete():
    body = request.get_json(silent=True)
    auth, err = _authenticate_or_error(body)
    if err:
        return err
    installation, license_id = auth
    row, bad = _claim_holder_row(body, installation, license_id)
    if bad:
        return bad
    count = body.get("chunk_count")
    as_of = body.get("as_of")
    if not isinstance(count, int) or isinstance(count, bool) or not (1 <= count <= MAX_CHUNKS):
        return _error("INVALID_CHUNK_COUNT")
    if not isinstance(as_of, int) or isinstance(as_of, bool) or as_of < 0:
        return _error("INVALID_AS_OF")
    encryption = _clean_encryption(body.get("encryption"), count)
    if encryption is None:
        return _error("INVALID_ENCRYPTION")
    names = sorted(p.name for p in _blob_dir(row).glob("*.chunk")) if _blob_dir(row).is_dir() else []
    if names != [f"{i:06d}.chunk" for i in range(count)]:
        return _reply("CHUNKS_INCOMPLETE")
    pruned_through = _license_pruned_through(license_id)
    if as_of < row.min_as_of or as_of < pruned_through:
        # Either the copy does not contain events the requester itself pushed, or
        # it is itself BELOW what Owner has pruned -- the requester would resume
        # from `as_of`, be told RESYNC_REQUIRED again and ask again, forever (a
        # producer that is itself stuck behind the prune holds exactly such a copy).
        # Back to OPEN with the bytes removed, so a better-placed peer can try.
        row.status = "OPEN"
        row.producer_installation_id = None
        row.claim_expires_at = None
        _remove_blob(row.id)
        db_session.commit()
        return _reply("AS_OF_TOO_OLD", min_as_of=max(row.min_as_of, pruned_through))
    hashes, total = [], 0
    for i in range(count):
        data = _chunk_path(row, i).read_bytes()
        hashes.append(hashlib.sha256(data).hexdigest())
        total += len(data)
    row.manifest = {**(row.manifest or {}), "encryption": encryption, "as_of": as_of,
                    "chunk_count": count, "chunk_sha256": hashes, "total_bytes": total}
    row.status = "READY"
    db_session.commit()
    return jsonify(_view(row)), 200


@bp.route("/bootstrap/download", methods=["POST"])
def bootstrap_chunk_down():
    body = request.get_json(silent=True)
    auth, err = _authenticate_or_error(body)
    if err:
        return err
    installation, license_id = auth
    row = _load(body, license_id)
    if row is None or row.requester_installation_id != installation.id:
        return _reply("BOOTSTRAP_NOT_FOUND", 404)
    if _expire_if_due(row):
        db_session.commit()
        return _reply("BOOTSTRAP_EXPIRED")
    if row.status != "READY":
        return _reply("NOT_READY")
    index = body.get("index")
    count = (row.manifest or {}).get("chunk_count")
    if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < (count or 0)):
        return _error("INVALID_CHUNK_INDEX")
    path = _chunk_path(row, index)
    if not path.is_file():
        return _reply("BLOB_MISSING", 410)
    data = path.read_bytes()
    return jsonify({
        "index": index, "data": base64.b64encode(data).decode("ascii"),
        "sha256": hashlib.sha256(data).hexdigest(),
    }), 200


def _settle(row: SyncBootstrapRequest) -> None:
    """DELIVERED -> FULFILLED once the authority half is also done."""
    if row.status == "DELIVERED" and (row.manifest or {}).get("authority_fulfilled") is True:
        row.status = "FULFILLED"


@bp.route("/bootstrap/ack", methods=["POST"])
def bootstrap_ack():
    body = request.get_json(silent=True)
    auth, err = _authenticate_or_error(body)
    if err:
        return err
    installation, license_id = auth
    row = _load(body, license_id)
    if row is None or row.requester_installation_id != installation.id:
        return _reply("BOOTSTRAP_NOT_FOUND", 404)
    if row.status in ("DELIVERED", "FULFILLED"):
        return jsonify(_view(row)), 200         # a retried ack
    if row.status != "READY":
        return _reply("NOT_READY")
    # THE POINT OF THE ACK: the shop's data stops sitting on Owner's disk the
    # moment the one device that needed it says it has it.
    _remove_blob(row.id)
    row.status = "DELIVERED"
    _settle(row)
    db_session.commit()
    return jsonify(_view(row)), 200


@bp.route("/bootstrap/fulfil", methods=["POST"])
def bootstrap_fulfil():
    body = request.get_json(silent=True)
    auth, err = _authenticate_or_error(body)
    if err:
        return err
    installation, license_id = auth
    row = _load(body, license_id)
    if row is None:
        return _reply("BOOTSTRAP_NOT_FOUND", 404)
    if _expire_if_due(row):
        db_session.commit()
        return _reply("BOOTSTRAP_EXPIRED")
    if row.requester_installation_id == installation.id:
        return _reply("SELF_FULFIL", 400)
    if row.status in ("FULFILLED",):
        return jsonify(_view(row)), 200
    if row.stream == "registry":
        if row.status != "OPEN":
            return _reply("NOT_OPEN")
        row.manifest = {**(row.manifest or {}), "authority_fulfilled": True}
        row.status = "FULFILLED"
    else:
        # The ordering rule in the module docstring: re-announce events must land
        # after the copy's as_of, so the authority half cannot be reported done
        # before the copy exists.
        if row.status not in ("READY", "DELIVERED"):
            return _reply("TOO_EARLY")
        row.manifest = {**(row.manifest or {}), "authority_fulfilled": True}
        _settle(row)
    db_session.commit()
    return jsonify(_view(row)), 200


# ── housekeeping (called by `flask commercial housekeeping`) ─────────────────

def sweep(*, apply: bool) -> dict:
    """Expire requests nobody finished, put lapsed claims back to OPEN, and
    delete every blob directory no live request owns. Dry run counts only.
    -> {"expired": n, "leases_reset": n, "blobs_removed": n}"""
    now = _now()
    expired = db_session.execute(
        select(SyncBootstrapRequest).where(
            SyncBootstrapRequest.status.in_(BOOTSTRAP_OPEN_STATUSES), SyncBootstrapRequest.expires_at < now)
    ).scalars().all()
    lapsed = db_session.execute(
        select(SyncBootstrapRequest).where(
            SyncBootstrapRequest.status == "CLAIMED", SyncBootstrapRequest.claim_expires_at < now,
            SyncBootstrapRequest.expires_at >= now)
    ).scalars().all()
    expired_ids = {r.id for r in expired}
    if apply:
        for r in expired:
            r.status = "EXPIRED"
        for r in lapsed:
            r.status = "OPEN"
            r.producer_installation_id = None
            r.claim_expires_at = None
        db_session.flush()
    # Directories whose request is not CLAIMED/READY any more (expired, delivered,
    # failed, or no row at all) hold bytes nobody may read again.
    keep = {str(i) for i in db_session.execute(
        select(SyncBootstrapRequest.id).where(SyncBootstrapRequest.status.in_(("CLAIMED", "READY")))).scalars().all()}
    # Subtracted in a dry run too: the dry-run count must predict what --apply
    # will delete (the rows are only flipped when applying, the bytes go either way).
    keep -= {str(i) for i in expired_ids}
    keep -= {str(r.id) for r in lapsed}
    removed = 0
    base = _base_dir()
    if base.is_dir():
        for entry in base.iterdir():
            if entry.is_dir() and entry.name not in keep:
                removed += 1
                if apply:
                    shutil.rmtree(entry, ignore_errors=True)
    if apply:
        db_session.commit()
    return {"expired": len(expired), "leases_reset": len(lapsed), "blobs_removed": removed}
