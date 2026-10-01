"""Signed AUTHORITY sync events (AUDIT-033 / S-AUTH).

THE HOLE THIS CLOSES. Any computer holding the shop licence could insert a
`sync_outbox` row and every till applied it: make itself admin, replace the
owner's `password_hash`, seize or revoke the main computer, forge till
allocations, release acks, branch aliases and numbering books. A receiver could
not tell who sent an event -- relays drop the origin, and every author field
inside a payload is a string the sender typed. Earlier waves raised the bar
(epoch checks, "author must be main" compared against a payload field) but, as
`SyncService._till_author_refusal` says in its own docstring, "a BAR-RAISER, NOT
AUTHENTICATION".

THE FIX. Every AUTHORITY event carries an Ed25519 signature made with the
sending device's EXISTING licensing key, inside a reserved `payload["_auth"]`
block (a reserved key INSIDE the payload is the only place metadata survives
both relays, quarantine and retry -- the same reason `_sender_schema_version`
lives there). The receiver:

  1. checks the signature over a canonical to-be-signed (tbs) document naming
     the signer's licence, installation and device id, the event's type/id/kind
     and its payload;
  2. checks the signer's Owner-signed lease (carried in `_auth.lease`) is genuine,
     names THE SAME licence as the receiver, and is bound to the signing key;
  3. derives the sender FROM THE SIGNATURE -- never from a payload field;
  4. applies a per-type authority rule (`authority_refusal`) saying who may send
     that kind of event (the main computer, the device itself, ...).

Anything that fails is PARKED in `sync_apply_quarantine` with a named reason
(visible in sync-review), never applied. Every rule is an EXTRA gate in front of
the existing appliers: epoch / row_version / quarantine checks all still run.

AUTHORITY TYPES. registry stream: user, user_permission, device, admin_device.
retail stream: till_allocation, doc_series, branch_alias, and `retail_setting`
ONLY when its key is a `till_release_ack:` confirmation (`is_authority_event`).
Everything else (sales, stock, products...) is untouched: unsigned and applied
exactly as before.

WHAT THE LEASE PROVES, AND WHAT IT DOES NOT. `verify_event` calls the normal
`verify_assertion` but with `trusted_now` pinned to the moment the lease was
issued. Expiry is ENTITLEMENT (may this shop still sell?); this module only
asks IDENTITY (was this key vouched for by Owner as a member of this licence?).
An event signed yesterday and pulled by a till that was offline for a week must
still verify, and a lease that lapsed since must not retroactively un-sign
history. Likewise a lapsed licence does not make a forger's lease valid -- the
Owner signature, the licence id and the key fingerprint are all still checked.

WHAT IS DELIBERATELY OUT OF SCOPE (recorded residuals, not built): per-USER
signatures (a non-main till can still edit non-owner staff and permissions, as
a delegated manager legitimately can today); an Owner-issued roster; every
`retail_setting` key other than `till_release_ack:`.

SIGNER BINDING lives in registry.db's `sync_signers` table (v10,
identity/sync_signers_schema.py): first binding wins in both directions, and
this receiver pre-binds ITSELF so nobody can claim its own device id.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Tuple, Union

from commercial_runtime.licensing_contracts.canonical import CanonicalizationError, canonicalize_bytes

logger = logging.getLogger(__name__)

#: Domain separator inside the signed document: a signature made for this
#: purpose can never be replayed as any other signed object (a licence request,
#: a roster), whatever bytes those happen to contain.
AUTH_CONTEXT = "aura-sync-authority/v1"
AUTH_KEY = "_auth"
_SENDER_VERSION_KEY = "_sender_schema_version"

#: Entity types that are authority events. `retail_setting` is in the set but
#: only counts when its key is a release acknowledgement -- see
#: `is_authority_event`. One constant, so the sender (who signs) and the
#: receiver (who demands a signature) can never disagree about the list.
AUTHORITY_ENTITY_TYPES = frozenset({
    "user", "user_permission", "device", "admin_device",
    "till_allocation", "doc_series", "branch_alias", "retail_setting",
})
RELEASE_ACK_PREFIX = "till_release_ack:"

#: A main computer that the receiver has not heard a verified event from for
#: this long (RECEIVER's clock) may be taken over. Device clocks are never
#: consulted: a forger controls those.
TAKEOVER_SILENCE_DAYS = 7

# ── refusal reasons (one place, so sync-review's sentence mapping can be checked
# against it). Verification:
REASON_UNSIGNED = "unsigned_authority_event"
REASON_BAD_SIGNATURE = "bad_signature"
REASON_FOREIGN_LICENCE = "foreign_licence"
REASON_UNTRUSTED_LEASE = "untrusted_lease"
REASON_BINDING_CLASH = "signer_binding_clash"
REASON_SIGNER_REVOKED = "signer_revoked"
REASON_NO_LOCAL_LICENCE = "no_local_licence"
# Authority rules:
REASON_NEEDS_MAIN = "needs_main_computer"
REASON_MAIN_UNKNOWN = "main_unknown"
REASON_ADMIN_CLAIM_MISMATCH = "admin_claim_signer_mismatch"
REASON_TAKEOVER_MISMATCH = "takeover_signer_mismatch"
REASON_TAKEOVER_NOT_SILENT = "takeover_main_not_silent"
REASON_DEVICE_CHANGE_NEEDS_MAIN = "device_change_needs_main"
REASON_OWNER_CHANGE_NEEDS_MAIN = "owner_change_needs_main"
REASON_SECOND_ADMIN = "second_admin_refused"
REASON_RELEASE_ACK_SENDER = "release_ack_wrong_sender"
REASON_ALIAS_ORIGIN = "alias_origin_mismatch"

ALL_REFUSAL_REASONS = frozenset({
    REASON_UNSIGNED, REASON_BAD_SIGNATURE, REASON_FOREIGN_LICENCE, REASON_UNTRUSTED_LEASE,
    REASON_BINDING_CLASH, REASON_SIGNER_REVOKED, REASON_NO_LOCAL_LICENCE,
    REASON_NEEDS_MAIN, REASON_MAIN_UNKNOWN, REASON_ADMIN_CLAIM_MISMATCH,
    REASON_TAKEOVER_MISMATCH, REASON_TAKEOVER_NOT_SILENT, REASON_DEVICE_CHANGE_NEEDS_MAIN,
    REASON_OWNER_CHANGE_NEEDS_MAIN, REASON_SECOND_ADMIN, REASON_RELEASE_ACK_SENDER,
    REASON_ALIAS_ORIGIN,
})


@dataclass(frozen=True)
class Refusal:
    """Why an authority event must be parked. `reason` is the stable
    machine/sync-review key; `detail` is for the operator."""
    reason: str
    detail: str


@dataclass(frozen=True)
class VerifiedSigner:
    """Who actually signed -- derived from the signature, never the payload."""
    dev: str
    inst: str
    fingerprint: str
    licence: str = ""


@dataclass(frozen=True, eq=False)
class LocalSigner:
    """This device's ability to sign. `sign_fn` is None in "prepare only" mode
    (Android: Python never holds the key, Kotlin signs the tbs bytes)."""
    licence: str
    inst: str
    dev: str
    pk_b64: str
    lease: dict
    sign_fn: Optional[Callable[[bytes], str]] = None


def is_authority_event(event) -> bool:
    """True when `event` is one of the kinds that must be signed."""
    if not isinstance(event, dict):
        return False
    entity_type = event.get("entity_type")
    if not isinstance(entity_type, str) or entity_type not in AUTHORITY_ENTITY_TYPES:
        return False
    if entity_type == "retail_setting":
        payload = event.get("payload")
        skey = payload.get("skey") if isinstance(payload, dict) else None
        return isinstance(skey, str) and skey.startswith(RELEASE_ACK_PREFIX)
    return True


def strip_auth(payload):
    """A copy of `payload` without `_auth`. Appliers receive this: nothing
    stores `_auth` in a business table."""
    if not isinstance(payload, dict):
        return payload
    return {k: v for k, v in payload.items() if k != AUTH_KEY}


def _integral_floats_as_ints(value):
    """`2.0` and `2` are the same JSON number to a relay that stores payloads as
    jsonb or re-serialises them, but the canonical text differs ("2.0" vs "2"),
    which would break a signature made on the other spelling. Signing and
    verifying both fold an integral float to an int, so a hop through any relay
    cannot change the bytes."""
    if isinstance(value, dict):
        return {k: _integral_floats_as_ints(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_integral_floats_as_ints(v) for v in value]
    if isinstance(value, float) and value == value and value not in (float("inf"), float("-inf"))             and value == int(value):
        return int(value)
    return value


def build_tbs(event: dict, *, licence: str, inst: str, dev: str) -> bytes:
    """The exact bytes that are signed. Deliberately EXCLUDES the event id and
    `created_at` (relays re-render both) and the two reserved payload keys
    (`_auth` is the signature itself; `_sender_schema_version` is stamped at
    read time, AFTER signing, and a skew guard must not break a signature)."""
    payload = event.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    body = _integral_floats_as_ints(
        {k: v for k, v in payload.items() if k not in (AUTH_KEY, _SENDER_VERSION_KEY)})
    return canonicalize_bytes({
        "ctx": AUTH_CONTEXT,
        "licence": licence,
        "inst": inst,
        "dev": dev,
        "entity_type": str(event.get("entity_type")),
        "entity_id": str(event.get("entity_id")).lower(),
        "event_type": str(event.get("event_type")),
        "payload": body,
    })


# ── signing side ─────────────────────────────────────────────────────────────

def prepare_unsigned_auth(event: dict, signer: LocalSigner) -> Tuple[dict, bytes]:
    """`(event_with__auth_but_no_sig, tbs_bytes)`. For the Android bridge:
    Python builds the `_auth` block and the bytes to sign, Kotlin signs them with
    the Keystore key and sets `payload._auth.sig`. Returns a NEW event; never
    mutates the argument."""
    payload = event.get("payload")
    base = dict(payload) if isinstance(payload, dict) else {}
    base.pop(AUTH_KEY, None)
    auth = {"v": 1, "inst": signer.inst, "dev": signer.dev, "pk": signer.pk_b64,
            "lease": signer.lease}
    base[AUTH_KEY] = auth
    new_event = dict(event, payload=base)
    return new_event, build_tbs(event, licence=signer.licence, inst=signer.inst, dev=signer.dev)


def _signature_is_ours(event: dict, signer: LocalSigner) -> bool:
    payload = event.get("payload")
    auth = payload.get(AUTH_KEY) if isinstance(payload, dict) else None
    if not isinstance(auth, dict) or not auth.get("sig"):
        return False
    if (auth.get("inst"), auth.get("dev"), auth.get("pk")) != (signer.inst, signer.dev, signer.pk_b64):
        return False
    try:
        from commercial_runtime.licensing_contracts.device_identity import verify_signature
        return bool(verify_signature(
            base64.b64decode(signer.pk_b64),
            build_tbs(event, licence=signer.licence, inst=signer.inst, dev=signer.dev),
            base64.b64decode(auth["sig"])))
    except (ValueError, binascii.Error, TypeError, CanonicalizationError):
        return False


def sign_event(event: dict, signer: LocalSigner) -> dict:
    """A NEW event dict carrying a verifying `_auth`. Idempotent: an event whose
    `_auth` already verifies for this signer comes back unchanged (an outbox row
    is re-read on every push attempt, and a LAN hub forwards what it received).
    In prepare-only mode (`signer.sign_fn is None`) the returned `_auth` has no
    `sig`; use `prepare_unsigned_auth` to also get the bytes to sign."""
    if _signature_is_ours(event, signer):
        return event
    prepared, tbs = prepare_unsigned_auth(event, signer)
    if signer.sign_fn is None:
        return prepared
    prepared["payload"][AUTH_KEY]["sig"] = signer.sign_fn(tbs)
    return prepared


def _read_json_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def local_signer_or_none(app_data_dir=None, *, prepare_only: Optional[bool] = None) -> Optional[LocalSigner]:
    """This install's signer, or None when it cannot sign: no device key, no
    Owner-signed lease, the lease names a DIFFERENT key (a stale lease from
    before a key reset), or no local device id yet.

    `prepare_only` (None = detect): True builds a signer whose `sign_fn` is None
    -- the Android shape, where Kotlin owns the private key. Detected from the
    key files: Kotlin writes `device_public_key.txt` and no DPAPI blob.

    Never raises: a till must keep syncing its non-authority events when it
    cannot sign. The caller decides what "no signer" means (the outbox sends the
    event unsigned, and the receiver parks it and says so)."""
    try:
        app = Path(app_data_dir or os.environ.get("AURA_APP_DATA") or "")
        if not str(app):
            return None
        lic_dir = app / "licensing"
        has_dpapi = (lic_dir / "device_key.dpapi").exists()
        has_android = (lic_dir / "device_public_key.txt").exists()
        if prepare_only is None:
            prepare_only = has_android and not has_dpapi
        if prepare_only:
            if not has_android:
                return None
            pk_b64 = (lic_dir / "device_public_key.txt").read_text(encoding="utf-8").strip()
            sign_fn = None
        else:
            if not has_dpapi:
                return None
            from commercial_runtime.licensing_contracts.device_identity import WindowsDpapiDeviceIdentityProvider
            provider = WindowsDpapiDeviceIdentityProvider(lic_dir)
            if not provider.has_key():
                return None
            pk_b64 = provider.get_public_key_b64()
            sign_fn = provider.sign
        lease_info = _local_lease(app)
        if lease_info is None:
            return None
        lease, licence, inst, lease_fp = lease_info
        from commercial_runtime.licensing_contracts.device_identity import fingerprint_of
        if lease_fp != fingerprint_of(base64.b64decode(pk_b64)):
            return None
        dev = _local_device_id(app)
        if not dev:
            return None
        return LocalSigner(licence=licence, inst=inst, dev=dev, pk_b64=pk_b64, lease=lease, sign_fn=sign_fn)
    except Exception as exc:  # noqa: BLE001 -- see docstring: never raise out of the sync path
        logger.warning("authority: could not build a local signer (%s: %s)", type(exc).__name__, exc)
        return None


def _local_lease(app: Path):
    """`(envelope, licence_id, installation_id, device_key_fingerprint)` from this
    install's stored lease, or None."""
    db = app / "database" / "subsystems" / "licensing.db"
    if not db.exists():
        return None
    from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
    record = LicenseStateRepository(db).load()
    if record is None or not record.assertion_envelope_json:
        return None
    envelope = json.loads(record.assertion_envelope_json)
    payload = envelope.get("payload") if isinstance(envelope, dict) else None
    if not isinstance(payload, dict):
        return None
    licence = payload.get("license_public_id")
    inst = payload.get("installation_public_id")
    fp = payload.get("device_key_fingerprint")
    if not (licence and inst and fp):
        return None
    return envelope, str(licence), str(inst), str(fp)


def _local_device_id(app: Path) -> Optional[str]:
    path = app / "device" / "local_device.json"
    raw = _read_json_file(path) if path.exists() else None
    value = raw.get("device_uuid") if isinstance(raw, dict) else None
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, eq=False)
class AuthorityEnv:
    """What a receiver needs to judge events, resolved fresh per apply batch."""
    trust_store: object
    own_licence: Optional[str]
    signer: Optional[LocalSigner]
    local_dev: Optional[str]


def default_authority_env(app_data_dir=None) -> AuthorityEnv:
    """Resolve the receiver's trust store, own licence, signer and device id from
    app data (`AURA_APP_DATA` unless given). Read per call, never cached at
    import time: the licence can be activated minutes after the process starts,
    and the trust store can be rotated (a stale in-memory copy would keep
    trusting a revoked key)."""
    app = Path(app_data_dir or os.environ.get("AURA_APP_DATA") or "")
    trust_store = None
    own_licence = None
    local_dev = None
    try:
        ts_path = app / "licensing" / "trust_store.json"
        if str(app) and ts_path.exists():
            from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore
            trust_store = OwnerTrustStore(ts_path)
        info = _local_lease(app) if str(app) else None
        own_licence = info[1] if info else None
        local_dev = _local_device_id(app) if str(app) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("authority: could not resolve the local trust context (%s: %s)",
                       type(exc).__name__, exc)
    return AuthorityEnv(trust_store=trust_store, own_licence=own_licence,
                        signer=local_signer_or_none(app if str(app) else None), local_dev=local_dev)


# ── verifying side ───────────────────────────────────────────────────────────

_LEASE_CACHE: dict = {}
_LEASE_CACHE_LOCK = threading.Lock()
_LEASE_CACHE_MAX = 512


def _parse_dt(value) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _verify_lease(lease, *, trust_store, inst: str, fingerprint: str) -> Union[dict, Refusal]:
    """Owner-signed identity check. Returns the verified lease payload."""
    from commercial_runtime.licensing_contracts.assertion_verifier import (
        AssertionVerificationError, verify_assertion)
    if not isinstance(lease, dict) or not isinstance(lease.get("payload"), dict):
        return Refusal(REASON_UNTRUSTED_LEASE, "the signer's lease is missing or malformed")
    payload = lease["payload"]
    signing_key_id = lease.get("signing_key_id")
    cache_key = (fingerprint, inst, str(lease.get("signature")), str(signing_key_id))
    if trust_store is not None and trust_store.is_trusted(signing_key_id):
        with _LEASE_CACHE_LOCK:
            cached = _LEASE_CACHE.get(cache_key)
        if cached is not None:
            return cached
    issued = _parse_dt(payload.get("issued_at"))
    not_before = _parse_dt(payload.get("not_before"))
    if issued is None or not_before is None:
        return Refusal(REASON_UNTRUSTED_LEASE, "the signer's lease has no usable issue date")
    # Identity, not entitlement: judge the lease as of the moment it was issued.
    pinned_now = max(issued, not_before) + timedelta(seconds=1)
    try:
        verify_assertion(
            lease,
            trust_store=trust_store,
            expected_product_code=payload.get("product_code"),
            expected_platform=payload.get("platform"),
            expected_installation_id=inst,
            expected_device_key_fingerprint=fingerprint,
            trusted_now=pinned_now,
        )
    except AssertionVerificationError as exc:
        return Refusal(REASON_UNTRUSTED_LEASE, f"the signer's lease was refused: {exc.reason_code}")
    except Exception as exc:  # noqa: BLE001 -- a malformed lease must park, never wedge the batch
        return Refusal(REASON_UNTRUSTED_LEASE, f"the signer's lease could not be checked: {type(exc).__name__}")
    with _LEASE_CACHE_LOCK:
        if len(_LEASE_CACHE) >= _LEASE_CACHE_MAX:
            _LEASE_CACHE.clear()
        _LEASE_CACHE[cache_key] = payload
    return payload


def verify_event(event: dict, *, trust_store, own_licence: Optional[str]) -> Union[VerifiedSigner, Refusal]:
    """Verify `event["payload"]["_auth"]`. A `VerifiedSigner` means: this exact
    event body was signed by the key named in `_auth`, that key is vouched for by
    Owner as installation `inst` of THIS licence. It says nothing about whether
    that signer is ALLOWED to send this kind of event -- `authority_refusal`."""
    payload = event.get("payload") if isinstance(event, dict) else None
    auth = payload.get(AUTH_KEY) if isinstance(payload, dict) else None
    if auth is None:
        return Refusal(REASON_UNSIGNED, "this authority event carries no signature")
    if not isinstance(auth, dict) or auth.get("v") != 1:
        return Refusal(REASON_BAD_SIGNATURE, "the signature block is malformed or from an unknown version")
    inst, dev, pk_b64, sig_b64 = auth.get("inst"), auth.get("dev"), auth.get("pk"), auth.get("sig")
    if not all(isinstance(x, str) and x for x in (inst, dev, pk_b64, sig_b64)):
        return Refusal(REASON_UNSIGNED if not sig_b64 else REASON_BAD_SIGNATURE,
                       "the signature block is incomplete")
    if trust_store is None:
        return Refusal(REASON_UNTRUSTED_LEASE, "this device has no Owner trust store to check the signer against")
    if not own_licence:
        return Refusal(REASON_NO_LOCAL_LICENCE, "this device has no licence of its own to compare the signer's against")
    try:
        raw_pk = base64.b64decode(pk_b64, validate=True)
        signature = base64.b64decode(sig_b64, validate=True)
    except (ValueError, binascii.Error):
        return Refusal(REASON_BAD_SIGNATURE, "the signature or key is not valid base64")
    if len(raw_pk) != 32:
        return Refusal(REASON_BAD_SIGNATURE, "the signer's public key is the wrong length")
    fingerprint = hashlib.sha256(raw_pk).hexdigest()

    lease_payload = _verify_lease(auth.get("lease"), trust_store=trust_store, inst=inst, fingerprint=fingerprint)
    if isinstance(lease_payload, Refusal):
        return lease_payload
    licence = lease_payload.get("license_public_id")
    if licence != own_licence:
        return Refusal(REASON_FOREIGN_LICENCE, "the signer belongs to a different licence")

    try:
        tbs = build_tbs(event, licence=str(licence), inst=inst, dev=dev)
    except CanonicalizationError as exc:
        return Refusal(REASON_BAD_SIGNATURE, f"the event cannot be canonicalised: {exc}")
    from commercial_runtime.licensing_contracts.device_identity import verify_signature
    if not verify_signature(raw_pk, tbs, signature):
        return Refusal(REASON_BAD_SIGNATURE, "the signature does not match the event")
    return VerifiedSigner(dev=dev, inst=inst, fingerprint=fingerprint, licence=str(licence))


# ── signer binding (registry v10) ────────────────────────────────────────────

def _utc_iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _ensure_signers_table(reg: sqlite3.Connection) -> None:
    # CREATE IF NOT EXISTS only: the real table comes from the v10 migration;
    # this exists for a hand-built registry (tests, a half-migrated file) so a
    # missing table can never turn into an exception out of the apply loop.
    from commercial_runtime.identity.sync_signers_schema import SYNC_SIGNERS_DDL
    reg.execute(SYNC_SIGNERS_DDL)


def bind_signer(reg: sqlite3.Connection, company_id: str, signer: VerifiedSigner,
                now: Optional[datetime] = None, *, refresh_seen: bool = True) -> Optional[Refusal]:
    """First binding wins, in both directions. A verified signer whose device id
    or installation is already bound to something else is refused; the same
    installation presenting a NEW key (Owner re-signed its lease after a key
    reset -- the lease verified) just updates the fingerprint. Refreshes
    `last_signed_seen_at_utc` on the RECEIVER's clock. No commit."""
    _ensure_signers_table(reg)
    stamp = _utc_iso(now)
    by_dev = reg.execute(
        "SELECT installation_id, key_fingerprint FROM sync_signers WHERE device_id=?", (signer.dev,)).fetchone()
    by_inst = reg.execute(
        "SELECT device_id FROM sync_signers WHERE installation_id=?", (signer.inst,)).fetchone()
    if by_dev is None and by_inst is None:
        reg.execute(
            "INSERT INTO sync_signers (device_id, company_id, installation_id, key_fingerprint, "
            "first_bound_at_utc, last_signed_seen_at_utc) VALUES (?,?,?,?,?,?)",
            (signer.dev, company_id, signer.inst, signer.fingerprint, stamp, stamp))
        return None
    if by_dev is not None and by_dev[0] != signer.inst:
        return Refusal(REASON_BINDING_CLASH,
                       f"device {signer.dev!r} is already bound to a different installation")
    if by_inst is not None and by_inst[0] != signer.dev:
        return Refusal(REASON_BINDING_CLASH,
                       f"installation {signer.inst!r} is already bound to a different device")
    if refresh_seen:
        reg.execute(
            "UPDATE sync_signers SET key_fingerprint=?, last_signed_seen_at_utc=? WHERE device_id=?",
            (signer.fingerprint, stamp, signer.dev))
    else:
        reg.execute("UPDATE sync_signers SET key_fingerprint=? WHERE device_id=?",
                    (signer.fingerprint, signer.dev))
    return None


def ensure_self_bound(reg: sqlite3.Connection, company_id: str, signer: LocalSigner,
                      now: Optional[datetime] = None) -> bool:
    """Pre-bind THIS device's own id to its own installation and key, so a peer
    can never get in first and claim it. True when bound (now or already).
    No commit."""
    own = VerifiedSigner(dev=signer.dev, inst=signer.inst,
                         fingerprint=hashlib.sha256(base64.b64decode(signer.pk_b64)).hexdigest(),
                         licence=signer.licence)
    # refresh_seen=False: this runs on every judged event and at boot; only a
    # VERIFIED event (including this device's own, echoed back) may say "alive".
    refusal = bind_signer(reg, company_id, own, now, refresh_seen=False)
    if refusal is not None:
        logger.error("authority: this device's own id cannot be bound: %s", refusal.detail)
        return False
    return True


# ── authority rules (D4) ─────────────────────────────────────────────────────

@dataclass
class RuleContext:
    """Everything `authority_refusal` may look at. `reg` is a registry.db
    connection (the assignment, users, devices, signer table all live there);
    `retail` is the retail.db connection on the retail stream, else None."""
    reg: sqlite3.Connection
    company_id: str
    local_dev: Optional[str] = None
    retail: Optional[sqlite3.Connection] = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _current_main(ctx: RuleContext) -> Optional[str]:
    from commercial_runtime.identity.device_registry import get_assignment
    assignment = get_assignment(ctx.reg, ctx.company_id)
    return str(assignment["device_id"]) if assignment else None


def _local_admin_uid(ctx: RuleContext) -> Optional[str]:
    """The owner: the one `users` row with role 'admin'."""
    try:
        row = ctx.reg.execute(
            "SELECT uid FROM users WHERE company_id=? AND role='admin' AND deleted_at_utc IS NULL LIMIT 1",
            (ctx.company_id,)).fetchone()
    except sqlite3.OperationalError:
        row = ctx.reg.execute(
            "SELECT uid FROM users WHERE company_id=? AND role='admin' LIMIT 1", (ctx.company_id,)).fetchone()
    return str(row[0]) if row and row[0] else None


def _main_is_silent(ctx: RuleContext, main_dev: str) -> bool:
    """True when the receiver has not recorded a verified event from `main_dev`
    for TAKEOVER_SILENCE_DAYS, by ITS OWN clock. Never heard -> silent (a till
    that just joined has no evidence the main is alive).

    When the receiver IS the main, the evidence is its own row: it is refreshed
    whenever the relay hands this device's own signed events back (they are
    verified like anyone's). A main that has been syncing recently keeps a fresh
    row and REFUSES a takeover; a main that was cut off for a week and has just
    come back has a stale one, so the takeover that happened in its absence is
    accepted and it steps down by itself -- which is the whole point of a
    takeover. `ensure_self_bound` never refreshes the row, so merely booting
    or judging events cannot make a returning main look alive."""
    row = ctx.reg.execute(
        "SELECT last_signed_seen_at_utc FROM sync_signers WHERE device_id=?", (main_dev,)).fetchone()
    if row is None:
        return True
    last = _parse_dt(row[0])
    if last is None:
        return True
    return ctx.now - last > timedelta(days=TAKEOVER_SILENCE_DAYS)


def _needs_main(ctx: RuleContext, signer: VerifiedSigner, reason: str, what: str) -> Optional[Refusal]:
    main = _current_main(ctx)
    if main is None:
        return Refusal(REASON_MAIN_UNKNOWN,
                       f"{what} needs the main computer, and this device does not know which one it is yet")
    if signer.dev != main:
        return Refusal(reason, f"{what} was sent by {signer.dev!r}, but the main computer is {main!r}")
    return None


def _admin_device_rule(p: dict, signer: VerifiedSigner, ctx: RuleContext) -> Optional[Refusal]:
    from commercial_runtime.identity.device_registry import get_assignment
    claimed = p.get("device_id")
    if not isinstance(claimed, str) or not claimed:
        return None   # nothing to judge: the applier parks a payload with no device as malformed
    local = get_assignment(ctx.reg, ctx.company_id)
    if local is None:
        # First claim: nobody holds main here yet, so the only thing to check is
        # that the signer is claiming it for ITSELF.
        if signer.dev != claimed:
            return Refusal(REASON_ADMIN_CLAIM_MISMATCH,
                           f"{signer.dev!r} signed a main-computer claim naming {claimed!r}")
        return None
    main = str(local["device_id"])
    if signer.dev == main:
        # Same-epoch re-announce (including a takeover assignment the new main
        # keeps re-stating) or a hand-over by the current main.
        return None
    if p.get("takeover") is True:
        if signer.dev != claimed:
            return Refusal(REASON_TAKEOVER_MISMATCH,
                           f"{signer.dev!r} signed a takeover naming {claimed!r}")
        if not _main_is_silent(ctx, main):
            return Refusal(REASON_TAKEOVER_NOT_SILENT,
                           f"the main computer {main!r} was heard from within the last "
                           f"{TAKEOVER_SILENCE_DAYS} days, so it cannot be taken over yet")
        return None
    return Refusal(REASON_NEEDS_MAIN,
                   f"a main-computer change was sent by {signer.dev!r}, but the main computer is {main!r}")


def _device_rule(p: dict, signer: VerifiedSigner, ctx: RuleContext) -> Optional[Refusal]:
    claimed = p.get("device_id")
    if not isinstance(claimed, str) or not claimed:
        return None   # nothing to judge: the applier parks a payload with no device as malformed
    if signer.dev != claimed:
        return _needs_main(ctx, signer, REASON_DEVICE_CHANGE_NEEDS_MAIN,
                           f"a change to computer {claimed!r}")
    # A device speaking about ITSELF: its own facts are always fine; the owner's
    # decisions about it (name, removal, reinstating) are not its to make.
    owner_change = False
    row = ctx.reg.execute(
        "SELECT status, device_label, admin_row_version FROM devices WHERE id=?", (claimed,)).fetchone()
    if p.get("reinstated_by_device_id"):
        owner_change = True
    elif row is not None:
        try:
            incoming_v = int(p.get("admin_row_version") or 1)
        except (TypeError, ValueError):
            incoming_v = 1
        if incoming_v > int(row[2] or 1):
            status_differs = (p.get("status") or "active") != (row[0] or "active")
            label_differs = bool(row[1]) and (p.get("device_label") or "") != row[1]
            owner_change = status_differs or label_differs
    if owner_change:
        main = _current_main(ctx)
        if main is not None and signer.dev == main:
            return None
        return _needs_main(ctx, signer, REASON_DEVICE_CHANGE_NEEDS_MAIN,
                           f"a name/status change to computer {claimed!r}")
    return None


def _user_rule(p: dict, signer: VerifiedSigner, ctx: RuleContext) -> Optional[Refusal]:
    uid = p.get("uid")
    role = p.get("role")
    admin_uid = _local_admin_uid(ctx)
    if role == "admin" and admin_uid is not None and uid != admin_uid:
        # Only the MAIN computer may introduce an owner account that differs from
        # the one already here. DEVIATION FROM THE WRITTEN PLAN ("refused always"),
        # forced by a shipped, test-pinned scenario: a joining device that minted
        # its own first-run admin must still receive the shop owner's real
        # account from the main computer (test_a_colliding_admin_code_from_a_
        # second_device_is_renumbered_not_parked). Refusing the main would leave
        # that device without its owner for ever. Everyone else is still refused,
        # and a forger cannot pose as the main: it is the SIGNATURE that says so.
        main = _current_main(ctx)
        if main is None:
            return Refusal(REASON_MAIN_UNKNOWN,
                           "a second administrator account needs the main computer, and this "
                           "device does not know which one it is yet")
        if signer.dev != main:
            return Refusal(REASON_SECOND_ADMIN,
                           "a second administrator account may only come from the main computer")
        return None
    if admin_uid is not None and uid == admin_uid:
        # The payload is the WHOLE synced row, so judge it against the LOCAL owner
        # row: an event that leaves every owner-critical field as it already is
        # (a display-name or language edit, a row_version bump) is harmless and any
        # verified member may send it. Only a change to a critical field -- who the
        # owner is, whether they can sign in, or how -- needs the main computer.
        if not _owner_critical_fields_changed(p, uid, ctx):
            return None
        return _needs_main(ctx, signer, REASON_OWNER_CHANGE_NEEDS_MAIN, "a change to the owner's account")
    if role == "admin":
        # An owner account this device does not have yet (fresh receiver): the
        # main computer introduces it.
        return _needs_main(ctx, signer, REASON_OWNER_CHANGE_NEEDS_MAIN, "a change to the owner's account")
    return None


#: Fields whose change is a change of WHO THE OWNER IS or HOW THEY SIGN IN
#: (planner amendment to D4, 2026-10-01). Everything else on a `user` row
#: (display_name, language, updated_at_utc, row_version, ...) is harmless.
OWNER_CRITICAL_FIELDS = ("role", "status", "password_hash", "pin_hash", "email",
                         "require_password_change", "session_version", "employee_id",
                         "branch_scope_uid", "role_preset")
#: Keys an OLD sender legitimately omits, in which case the applier KEEPS the
#: local value (`user_sync_apply_plan`: an absent role_preset means "unchanged").
#: `branch_scope_uid` is NOT here: the applier writes NULL for an absent key.
_ABSENT_MEANS_UNCHANGED = frozenset({"role_preset"})


def _owner_critical_fields_changed(p: dict, uid: str, ctx: RuleContext) -> bool:
    cur = ctx.reg.execute("SELECT * FROM users WHERE uid=? AND company_id=?", (uid, ctx.company_id))
    row = cur.fetchone()
    if row is None:
        return True
    local = dict(zip([d[0] for d in cur.description], tuple(row)))
    for key in OWNER_CRITICAL_FIELDS:
        if key not in local:
            continue   # a column this registry does not have cannot differ
        if key not in p and key in _ABSENT_MEANS_UNCHANGED:
            continue
        # An absent key the applier would WRITE as NULL is a change to NULL.
        incoming, stored = p.get(key), local.get(key)
        if key == "require_password_change":
            incoming, stored = (int(bool(incoming)) if incoming is not None else None,
                                int(bool(stored)) if stored is not None else None)
        if incoming != stored:
            return True
    return False


def _user_permission_rule(p: dict, signer: VerifiedSigner, ctx: RuleContext) -> Optional[Refusal]:
    admin_uid = _local_admin_uid(ctx)
    if admin_uid is not None and p.get("user_uid") == admin_uid:
        return _needs_main(ctx, signer, REASON_OWNER_CHANGE_NEEDS_MAIN, "a change to the owner's permissions")
    return None


def _till_allocation_rule(p: dict, signer: VerifiedSigner, ctx: RuleContext) -> Optional[Refusal]:
    """Main computer only -- with one pass-through. A NEW allocation naming an
    epoch ABOVE the one this receiver knows is a hand-over this device has not
    heard about yet: the real new main signs it, and until the registry stream
    delivers the assignment it is not (yet) the main HERE. Refusing it with
    `needs_main_computer` would hide the true reason, so it is passed to the
    applier, whose own `_till_author_refusal` parks every insert above the known
    epoch as `allocation_epoch_unknown` (re-verified on retry, and applied once
    the assignment arrives and the signer IS the main). Only for an id with no
    local row: an UPDATE of an existing allocation has no author check in the
    applier, so it never gets this pass."""
    refusal = _needs_main(ctx, signer, REASON_NEEDS_MAIN, "a till allocation")
    if refusal is None or refusal.reason == REASON_MAIN_UNKNOWN:
        return refusal
    from commercial_runtime.identity.device_registry import get_assignment
    assignment = get_assignment(ctx.reg, ctx.company_id)
    epoch = p.get("admin_epoch")
    if (assignment is not None and ctx.retail is not None and isinstance(epoch, int)
            and not isinstance(epoch, bool) and epoch > int(assignment["epoch"])):
        try:
            known = ctx.retail.execute(
                "SELECT 1 FROM till_allocations WHERE id=?", (str(p.get("id")),)).fetchone()
        except sqlite3.OperationalError:
            known = True   # cannot prove it is new: keep the refusal
        if known is None:
            return None
    return refusal


def _doc_series_rule(p: dict, signer: VerifiedSigner, ctx: RuleContext) -> Optional[Refusal]:
    """The main computer may write any numbering book. A till may write only a
    book that is, and stays, ITS OWN.

    DEVIATION FROM THE WRITTEN PLAN ("signer must be main"), forced by shipped
    behaviour (found when the two-process numbering suites went red): once the main
    hands a till its number, THAT TILL creates, reactivates and retires its own
    books (`retail_api._apply_till_books`, `allocator_terminal_uid` = its terminal)
    and the events travel from it. Refusing them would park every till's books on
    every other computer.

    What stays refused is the part that was ever dangerous: a till creating a book
    for somebody else, claiming a book the main made (claiming is main-only at the
    route), or taking over another till's book by sending an update that names
    itself as the allocator."""
    main = _current_main(ctx)
    if main is not None and signer.dev == main:
        return None
    own = p.get("allocator_terminal_uid") == signer.dev
    if own and ctx.retail is not None:
        try:
            row = ctx.retail.execute(
                "SELECT allocator_terminal_uid FROM doc_series WHERE id=?", (str(p.get("id")),)).fetchone()
        except sqlite3.OperationalError:
            return _needs_main(ctx, signer, REASON_NEEDS_MAIN, "a numbering-book event")
        if row is None or row[0] == signer.dev:
            return None   # a new book of its own, or its own book being updated
    return _needs_main(ctx, signer, REASON_NEEDS_MAIN, "a numbering-book event")


def _release_ack_rule(p: dict, signer: VerifiedSigner, ctx: RuleContext) -> Optional[Refusal]:
    alloc_id = str(p.get("skey", ""))[len(RELEASE_ACK_PREFIX):]
    main = _current_main(ctx)
    if main is not None and signer.dev == main:
        return None
    terminal = None
    if ctx.retail is not None:
        try:
            row = ctx.retail.execute(
                "SELECT terminal_uid FROM till_allocations WHERE id=?", (alloc_id,)).fetchone()
            terminal = str(row[0]) if row else None
        except sqlite3.OperationalError:
            terminal = None
    if terminal is not None and signer.dev == terminal:
        return None
    return Refusal(REASON_RELEASE_ACK_SENDER,
                   f"a release confirmation for allocation {alloc_id!r} came from {signer.dev!r}, "
                   "who is neither the till being released nor the main computer")


def authority_refusal(event: dict, signer: VerifiedSigner, ctx: RuleContext) -> Optional[Refusal]:
    """Is `signer` allowed to send THIS kind of authority event? None = yes.
    This is an extra gate in front of the existing appliers, never a
    replacement: epoch, row_version and quarantine checks still run after it.

      admin_device  first claim: the signer claims for itself; the current main
                    may re-announce or hand over; a takeover must be by the
                    claimant AND the main must have been silent 7 days
      device        a device may announce itself; its name/removal/reinstating
                    and any change to ANOTHER device need the main
      user          the owner's account (and any 'admin' role) needs the main; a
                    second admin is refused outright; everyone else is open to
                    any verified member
      user_permission  the owner's permissions need the main; others open
      till_allocation  main only
      doc_series    the main; a till only for a book that is its own
      retail_setting till_release_ack:<id>  the till being released, or the main
      branch_alias  the signer must be the alias's recorded origin
    """
    entity_type = event.get("entity_type")
    p = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    if entity_type == "admin_device":
        return _admin_device_rule(p, signer, ctx)
    if entity_type == "device":
        return _device_rule(p, signer, ctx)
    if entity_type == "user":
        return _user_rule(p, signer, ctx)
    if entity_type == "user_permission":
        return _user_permission_rule(p, signer, ctx)
    if entity_type == "till_allocation":
        return _till_allocation_rule(p, signer, ctx)
    if entity_type == "doc_series":
        return _doc_series_rule(p, signer, ctx)
    if entity_type == "retail_setting":
        return _release_ack_rule(p, signer, ctx)
    if entity_type == "branch_alias":
        if p.get("origin_terminal_uid") != signer.dev:
            return Refusal(REASON_ALIAS_ORIGIN,
                           f"a branch alias naming origin {p.get('origin_terminal_uid')!r} was sent by {signer.dev!r}")
        return None
    return None
