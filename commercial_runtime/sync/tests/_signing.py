"""Test helper for AUDIT-033 / S-AUTH: a throwaway signing "world".

Authority events (user, device, admin_device, till_allocation, ...) are now
parked unless a real device signed them, so every fixture that used to push a
raw unsigned event needs a real signer. This module builds the same pieces a
real shop has, with nothing mocked on the verification side:

  * an OWNER key and an `OwnerTrustStore` trusting it (what `trust_anchor.json`
    seeds on a real install);
  * per-device Ed25519 keys, each with an Owner-SIGNED lease (a real
    `assertion` envelope that `verify_assertion` accepts) naming the shop's
    licence, a made-up installation id and the device key's fingerprint;
  * a `LocalSigner` per device that signs exactly the way `local_signer_or_none`
    does on a real till.

The receiver side is `world.env(receiver)`, an `authority.AuthorityEnv` handed
to `SyncService(authority_env_provider=...)`. There is no switch that turns
checking off: a world with a different Owner key, a different licence, or a
tampered event is refused by the same code as in production.

`world.install_main(reg_conn, company, device)` writes the main-computer
assignment into a registry the way the v9 backfill does, so rules that need the
main computer have one to compare with.
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore
from commercial_runtime.sync import authority

LICENCE = "lic-shop-1"
DEFAULT_SIGNER_DEV = "receiver-device"   # the identity install_legit_author_signing signs as


def _raw_pub(private_key) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def _policy():
    return {
        "check_in_interval_seconds": 86400,
        "retry_interval_seconds": 3600,
        "offline_grace_seconds": 1209600,
        "warning_start_seconds": 864000,
        "hard_expiry_behavior": "RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
        "clock_rollback_tolerance_seconds": 300,
        "assertion_refresh_threshold_seconds": 86400,
    }


def make_lease(owner_key, *, key_id="owner-1", licence=LICENCE, inst, fingerprint,
               issued_at: Optional[datetime] = None, ttl=timedelta(hours=24),
               registry_device_id: Optional[str] = None) -> dict:
    """A real Owner-signed assertion envelope (24h TTL like production).

    `registry_device_id` models Owner's PIN (AUDIT-033 fix round, verifier V
    finding #1): Owner records which registry device id belongs to this
    installation and signs it into the lease. The key is added only when given,
    exactly as Owner omits it for a client that never sent the id."""
    issued = issued_at or datetime.now(timezone.utc)
    payload = {
        "assertion_id": str(uuid.uuid4()),
        "issuer": "aura-owner",
        "product_code": "AURA_RETAIL",
        "license_public_id": licence,
        "installation_public_id": inst,
        "platform": "WINDOWS",
        "app_version_policy": "1.0.0",
        "release_channel": "rc",
        "issued_at": issued.isoformat(),
        "not_before": (issued - timedelta(minutes=5)).isoformat(),
        "expires_at": (issued + ttl).isoformat(),
        "license_status": "ACTIVE",
        "installation_status": "ACTIVE",
        "subscription_status": "ACTIVE",
        "allowed_device_count": 5,
        "device_key_fingerprint": fingerprint,
        "entitlements": {},
        "offline_policy": _policy(),
        "contract_version": "v1",
    }
    if registry_device_id is not None:
        payload["registry_device_id"] = registry_device_id
    sig = owner_key.sign(canonicalize_bytes(payload))
    return {"payload": payload, "signing_key_id": key_id, "algorithm": "ed25519",
            "assertion_version": 1, "signature": base64.b64encode(sig).decode("ascii")}


@dataclass
class TestDevice:
    """One shop computer: its registry device id, installation id, key and signer."""
    __test__ = False
    dev: str
    inst: str
    key: Ed25519PrivateKey
    signer: authority.LocalSigner

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_raw_pub(self.key)).hexdigest()


class SigningWorld:
    __test__ = False

    def __init__(self, tmp_path, licence: str = LICENCE):
        self.licence = licence
        self.owner_key = Ed25519PrivateKey.generate()
        self.trust_path = Path(tmp_path) / f"trust-{uuid.uuid4().hex[:6]}.json"
        self.trust_store = OwnerTrustStore(self.trust_path)
        self.trust_store.bootstrap_from_anchor({"keys": [{
            "key_id": "owner-1", "public_key": base64.b64encode(_raw_pub(self.owner_key)).decode("ascii"),
            "algorithm": "ed25519"}]})
        self.devices: dict = {}

    # ── devices ──────────────────────────────────────────────────────────
    def new_device(self, dev: Optional[str] = None, *, inst: Optional[str] = None,
                   licence: Optional[str] = None, owner_key=None, key=None,
                   issued_at: Optional[datetime] = None, lease_fingerprint: Optional[str] = None,
                   prepare_only: bool = False, attest: Union[bool, str] = True) -> TestDevice:
        """`attest` models Owner's pin of the registry device id in the lease:
        True -> the lease names `dev` (a normal, pinned computer); False -> the
        lease carries no id (an un-upgraded or not-yet-pinned computer); a str ->
        the lease names THAT id (a computer Owner pinned to someone else's id)."""
        dev = dev or str(uuid.uuid4())
        inst = inst or f"inst-{uuid.uuid4().hex[:10]}"
        key = key or Ed25519PrivateKey.generate()
        fp = hashlib.sha256(_raw_pub(key)).hexdigest()
        lease = make_lease(owner_key or self.owner_key, licence=licence or self.licence, inst=inst,
                           fingerprint=lease_fingerprint or fp, issued_at=issued_at,
                           registry_device_id=(dev if attest is True else (attest or None)
                                               if isinstance(attest, str) else None))

        def sign_fn(data: bytes) -> str:
            return base64.b64encode(key.sign(data)).decode("ascii")

        signer = authority.LocalSigner(
            licence=licence or self.licence, inst=inst, dev=dev,
            pk_b64=base64.b64encode(_raw_pub(key)).decode("ascii"), lease=lease,
            sign_fn=None if prepare_only else sign_fn)
        device = TestDevice(dev=dev, inst=inst, key=key, signer=signer)
        self.devices[dev] = device
        return device

    # ── the receiver's view ──────────────────────────────────────────────
    def env(self, receiver: Optional[TestDevice] = None, *, own_licence: Optional[str] = None,
            trust_store=None) -> authority.AuthorityEnv:
        return authority.AuthorityEnv(
            trust_store=trust_store if trust_store is not None else self.trust_store,
            own_licence=own_licence or self.licence,
            signer=receiver.signer if receiver is not None else None,
            local_dev=receiver.dev if receiver is not None else None)

    def provider(self, receiver: Optional[TestDevice] = None, **kw):
        env = self.env(receiver, **kw)
        return lambda: env

    # ── signing ──────────────────────────────────────────────────────────
    def sign(self, event: dict, device: TestDevice) -> dict:
        return authority.sign_event(event, device.signer)

    def sign_all(self, events, device: TestDevice) -> list:
        return [self.sign(e, device) if authority.is_authority_event(e) else e for e in events]


def install_main(reg_conn, company_id: str, device_id: str, epoch: int = 1,
                 assigned_at: str = "2026-01-01T00:00:00+00:00") -> None:
    """Write the main-computer assignment into a registry (no device row needed)."""
    reg_conn.execute(
        "INSERT OR REPLACE INTO admin_device_assignment (company_id, device_id, epoch, assigned_at_utc) "
        "VALUES (?,?,?,?)", (company_id, device_id, epoch, assigned_at))
    reg_conn.commit()


def admin_claim_event(device_id: str, epoch: int = 1, **extra) -> dict:
    payload = {"device_id": device_id, "epoch": epoch,
               "assigned_at_utc": "2026-01-01T00:00:00+00:00",
               "assigned_by_user_uid": None, "assigned_by_device_id": device_id}
    payload.update(extra)
    return {"entity_type": "admin_device", "entity_id": str(uuid.uuid4()),
            "event_type": "update", "payload": payload}


# ── legit-author signing for the OLDER apply tests ──────────────────────────
#
# Before S-AUTH, dozens of tests handed `SyncService._apply_event` a raw,
# unsigned `user` / `device` / `admin_device` / `till_allocation` event and
# asserted what the APPLIER did with it (epoch order, row_version gates,
# quarantine, rollback, ...). Those assertions are about the appliers, not about
# who sent the event, and they are unchanged. What changed is that the engine now
# refuses an unsigned authority event before the applier is reached.
#
# `install_legit_author_signing` keeps those tests meaning what they meant: right
# before `_apply_event` runs, an authority event that carries no `_auth` is
# signed by a REAL signer who is ALLOWED to send it (the current main computer
# when one is recorded, otherwise the device the payload names or a generic
# member). Verification, binding and the rule table all still run for real.
#
# WHAT THIS CANNOT CATCH (said plainly, per ENGINEERING.md section 2): it makes
# every legacy-test event "authorised", so those files can no longer detect an
# applier that is reached by an UNauthorised sender. That coverage lives in
# test_sync_authority*.py, which sign events as the wrong people on purpose and
# count the business rows.

def _registry_main(conn, company_id):
    try:
        row = conn.execute(
            "SELECT device_id FROM admin_device_assignment WHERE company_id=?", (company_id,)).fetchone()
    except Exception:  # noqa: BLE001 -- hand-built registry without the table
        return None
    return str(row[0]) if row else None


def install_legit_author_signing(monkeypatch, tmp_path, *, company_id=None, receiver_dev=DEFAULT_SIGNER_DEV,
                                 sign_pushes=True):
    """Patch `SyncService` for one test: a throwaway world as the receiver's
    trust context, and legit-author signing in front of `_apply_event`. Returns
    the world."""
    from commercial_runtime.sync import sync_service as ss

    world = SigningWorld(tmp_path)
    receiver = world.new_device(receiver_dev)
    env = world.env(receiver)
    if not sign_pushes:
        # `read_outbox` finds no signer, so pushed events stay unsigned and are
        # signed by the legit author at apply time instead (single-process tests
        # whose two "computers" share one registry).
        env = authority.AuthorityEnv(trust_store=env.trust_store, own_licence=env.own_licence,
                                     signer=None, local_dev=env.local_dev)
    original = ss.SyncService._apply_event

    def _author_dev(ev, reg, cid):
        etype, p = ev.get("entity_type"), ev.get("payload") or {}
        main = _registry_main(reg, cid) if reg is not None else None
        claimed = p.get("device_id") if isinstance(p.get("device_id"), str) else None
        if etype == "admin_device":
            if main is None or main == claimed or p.get("takeover") is True:
                return claimed or "member-device"
            return main
        if etype == "branch_alias":
            origin = p.get("origin_terminal_uid")
            return origin if isinstance(origin, str) and origin else "member-device"
        if etype == "till_allocation" and main is not None and reg is not None:
            # A hand-over: the NEW main authors allocations at an epoch above the
            # one this receiver knows. Sign those as their author, like a real
            # new main would; everything else as the current main.
            epoch, by = p.get("admin_epoch"), p.get("allocated_by_terminal_uid")
            row = reg.execute("SELECT epoch FROM admin_device_assignment WHERE company_id=?", (cid,)).fetchone()
            if (row is not None and isinstance(epoch, int) and epoch > int(row[0])
                    and isinstance(by, str) and by):
                return by
        if main is not None:
            return main
        if etype == "device" and claimed:
            return claimed
        return "member-device" if etype == "device" else "legacy-main-device"

    def wrapped(self, conn, ev, local_company_id=None, *args, **kwargs):
        if (isinstance(ev, dict) and authority.is_authority_event(ev)
                and isinstance(ev.get("payload"), dict) and "_auth" not in ev["payload"]
                and ev.get("entity_type") in self._handled_entity_types):
            cid = str(local_company_id or company_id or "")
            registry_stream = ev["entity_type"] in ("user", "user_permission", "device", "admin_device")
            if registry_stream:
                reg, own = conn, False
            else:
                from commercial_runtime.identity import registry_db
                try:
                    reg, own = registry_db.get_conn(), True
                except Exception:  # noqa: BLE001 -- a test that breaks the registry on purpose
                    return original(self, conn, ev, local_company_id, *args, **kwargs)
            try:
                dev = _author_dev(ev, reg, cid)
                if (ev["entity_type"] in ("till_allocation", "doc_series", "retail_setting", "user",
                                          "user_permission") and cid
                        and _registry_main(reg, cid) is None):
                    # A shop that syncs has a main computer; record one (at a
                    # sky-high epoch so `_till_author_refusal`'s epoch check, which
                    # these tests exercise on their own, is not disturbed).
                    reg.execute(
                        "INSERT OR IGNORE INTO admin_device_assignment (company_id, device_id, epoch, "
                        "assigned_at_utc) VALUES (?,?,?,?)",
                        (cid, dev, 2 ** 31 - 1, "2026-01-01T00:00:00+00:00"))
                    if own:
                        reg.commit()
            finally:
                if own:
                    reg.close()
            signer_dev = world.devices.get(dev) or world.new_device(dev, inst=f"inst-{dev}")
            ev = world.sign(ev, signer_dev)
        return original(self, conn, ev, local_company_id, *args, **kwargs)

    monkeypatch.setattr(ss.SyncService, "_apply_event", wrapped)
    monkeypatch.setattr(ss.SyncService, "_authority_env", lambda self: env)
    return world
