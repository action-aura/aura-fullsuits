"""AUDIT-033 S-AUTH fix round (verifier V finding #1) -- end to end, with the REAL Owner.

A real Owner Flask app on a real localhost port (werkzeug), real Postgres, the
real `commercial_runtime` LicensingClient and real Ed25519 device keys. The leases
the shop computers hold here are the ones Owner actually signed, and a receiver
(the real `SyncService` apply path, real `authority` rules, a tmp registry) judges
events signed with them. This proves real Owner leases flow into the same verify
path production uses -- the Python product harness cannot run Owner, so the unit
tests elsewhere model Owner's pin with test leases; this is where the model meets
the real thing.

Story (one licence, two computers):
  1. A activates the OLD way (no registry id): its lease names nothing. Then A
     checks in claiming RA: Owner pins it, the lease names RA.
  2. B (an insider) activates claiming A's id RA: licensing still SUCCEEDS, the
     lease names nothing, and Owner records a REGISTRY_DEVICE_ID_CLASH. B then
     checks in with its own id RB and is pinned to that.
  3. The receiver, with A installed as the shop's main:
       - B signs an owner password change as RA (its own key, its own REAL lease)
         -> parked `device_id_not_attested`, the password is untouched;
       - B's pre-pin lease (no id) cannot speak at all;
       - A (the real main) changes the owner password -> applied;
       - B, as itself (RB), edits an ordinary user -> applied (the allow half).
  4. Owner replaces A's device key: A's next check-in still carries RA.

What this does not prove: Android hardware, and the production relay's own
ordering. See docs/owner/sauth/registry-device-id-contract.md.
"""
from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

from tests.conftest import make_staff
from tests.test_phase8v_scenario_live_server import (  # noqa: F401 -- live_owner_server is a fixture
    RealDeviceSigner, _bootstrap_trust_store, _make_license_and_key, _make_windows_client_stack,
    live_owner_server,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

RA = str(uuid.uuid4())
RB = str(uuid.uuid4())


def _activate(client, signer, full_key, label, registry_device_id=None):
    kwargs = {"registry_device_id": registry_device_id} if registry_device_id else {}
    return client.activate(
        product_code="AURA_CLINIC", platform="WINDOWS", app_version="1.0.0-e2e", release_channel=None,
        installation_id=label, device_public_key_b64=signer.get_public_key_b64(), license_key=full_key,
        idempotency_key=str(uuid.uuid4()), signer=signer, **kwargs,
    )


def _checkin(client, signer, installation_id, registry_device_id=None):
    kwargs = {"registry_device_id": registry_device_id} if registry_device_id else {}
    return client.check_in(installation_id=installation_id, signer=signer, **kwargs)


def _lease_of(response):
    assert response["result"] == "SUCCESS", response
    return response["signed_assertion"]


def _computer(signer, installation_id, dev, lease):
    """A shop computer as the receiver tests model it: its registry id, installation,
    and a LocalSigner that signs with the device key and carries the REAL lease."""
    from commercial_runtime.sync import authority

    local = authority.LocalSigner(
        licence=lease["payload"]["license_public_id"], inst=installation_id, dev=dev,
        pk_b64=signer.get_public_key_b64(), lease=lease, sign_fn=signer.sign,
    )
    return SimpleNamespace(dev=dev, inst=installation_id, signer=local, fingerprint=signer.fingerprint())


def test_real_owner_leases_drive_the_shop_authority_rules(app, client, seeded, live_owner_server, tmp_path, monkeypatch):
    from app.extensions import db_session
    from app.models.installations import ActivationEvent
    from sqlalchemy import select

    from commercial_runtime.sync import authority
    from commercial_runtime.sync.tests._signing import install_main
    from commercial_runtime.sync.tests.test_sync_authority_rules import CASHIER, COMPANY, OWNER, Shop

    staff_id = make_staff(app, "pin-e2e@example.com")
    _sub_id, full_key = _make_license_and_key(app, staff_id, end_date="2027-12-31", device_limit=3)
    trust_store = _bootstrap_trust_store(app, tmp_path)
    win_client, _repo, _events = _make_windows_client_stack(tmp_path, live_owner_server)

    # ── 1. A: old-style activation (no id), then pinned by a check-in ────────
    a_dev = RealDeviceSigner()
    a_act = _activate(win_client, a_dev, full_key, "e2e-a")
    a_inst = a_act["installation_id"]
    a_lease_old = _lease_of(a_act)
    assert "registry_device_id" not in a_lease_old["payload"], "old-client compat: no id sent -> none returned"
    a_lease = _lease_of(_checkin(win_client, a_dev, a_inst, RA))
    assert a_lease["payload"]["registry_device_id"] == RA
    licence_id = a_lease["payload"]["license_public_id"]

    # ── 2. B: claims A's id at activation; licensing still works, nothing attested ──
    b_dev = RealDeviceSigner()
    b_act = _activate(win_client, b_dev, full_key, "e2e-b", registry_device_id=RA)
    b_inst = b_act["installation_id"]
    b_lease_unpinned = _lease_of(b_act)
    assert "registry_device_id" not in b_lease_unpinned["payload"], "a clash must not be attested"
    with app.app_context():
        clash = db_session.execute(
            select(ActivationEvent).where(
                ActivationEvent.installation_id == uuid.UUID(b_inst), ActivationEvent.event_type == "REGISTRY_DEVICE_ID_CLASH")
        ).scalars().all()
        assert len(clash) == 1 and clash[0].safe_metadata["requested"] == RA
    b_lease = _lease_of(_checkin(win_client, b_dev, b_inst, RB))
    assert b_lease["payload"]["registry_device_id"] == RB

    # ── 3. The receiver judges events signed with those REAL leases ─────────
    shop = Shop(tmp_path, monkeypatch)
    shop.svc._authority_env_provider = lambda: authority.AuthorityEnv(
        trust_store=trust_store, own_licence=str(licence_id), signer=None, local_dev=None)
    conn = shop.get_conn()
    install_main(conn, COMPANY, RA)
    conn.close()

    computer_a = _computer(a_dev, a_inst, RA, a_lease)
    computer_b = _computer(b_dev, b_inst, RB, b_lease)
    b_posing_as_a = _computer(b_dev, b_inst, RA, b_lease)          # B's key and REAL lease, claiming A's id
    b_before_pin = _computer(b_dev, b_inst, RB, b_lease_unpinned)  # B's lease from before it was pinned

    owner_hash_before = shop.user(OWNER)["password_hash"]
    forged = shop.user_event(OWNER, password_hash="ATTACKER-HASH")
    assert shop.apply(forged, b_posing_as_a) is False
    assert shop.parked("user") == ["device_id_not_attested"], shop.parked()
    assert shop.user(OWNER)["password_hash"] == owner_hash_before, "the insider replaced the owner's password"

    assert shop.apply(shop.user_event(CASHIER, role="manager"), b_before_pin) is False
    assert shop.parked("user")[-1] == "device_id_not_attested", "an un-pinned lease must not be able to speak"
    assert shop.user(CASHIER)["role"] == "cashier"

    # allow half: the real main, and a pinned member as itself
    legit = shop.user_event(OWNER, password_hash="REAL-MAIN-CHANGE")
    assert shop.apply(legit, computer_a) is True, f"the real main was refused: {shop.parked()}"
    assert shop.user(OWNER)["password_hash"] == "REAL-MAIN-CHANGE"
    assert shop.apply(shop.user_event(CASHIER, role="manager"), computer_b) is True, shop.parked()
    assert shop.user(CASHIER)["role"] == "manager"

    # ── 4. Key replacement keeps the pin: A's next lease still names RA ───────
    new_key = RealDeviceSigner()
    with app.app_context():
        from app.licensing_service import device_identity

        current = device_identity.get_active_device_key(uuid.UUID(a_inst))
        device_identity.replace_device_key(current, "ed25519", new_key.get_public_key_b64())
    after_rotation = _lease_of(_checkin(win_client, new_key, a_inst, RA))
    assert after_rotation["payload"]["registry_device_id"] == RA
