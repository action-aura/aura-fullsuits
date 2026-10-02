# Credit: written by independent reviewer V4o (AUDIT-033 round-3 review). Copied
# VERBATIM by fix crew F (assertions and setup unchanged) as the RED -> GREEN target for
# gaps G1-G5. 6 tests were RED on a9e5dcb6; see test_sauth_registry_pin_round3_fixes.py
# for the allow/deny halves of each fix.
"""V4o -- independent round-3 review of AUDIT-033 finding #1 (Owner registry device id pin).

Every test asserts the SAFE outcome. A failure is a real gap unless its docstring
says "RESIDUAL" (then it pins the documented residual exactly, so it can never
silently get wider). Forged events go through the REAL push route
(/api/sync/v1/push) signed by a real active member installation: nothing is
hand-inserted that production would not accept, except where a docstring says
"seeded" (the main's own past history).

Groups:
  Q1  D-ADAPT judgement: what happens with NO synced history (residual shape).
  Q2  history guard: can a member forge client_created_at / entity types to veto
      the real main (DoS) or disable the guard for an accomplice?
  Q3  staff transfer: recovery for a HISTORY_CONFLICT victim, actor checks,
      lost update against a concurrent pin.
  Q4  licence-chain cycles terminate; migration 7c41 on a DB that already holds
      duplicate pins fails loudly AND atomically.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text

from tests.conftest import (
    TEST_DB_URL, build_activation_body, build_checkin_body, make_device_keypair, make_license, make_staff, sign_body,
)

RA = "11111111-1111-4111-8111-111111111111"   # the main's registry id
RB = "22222222-2222-4222-8222-222222222222"   # the member's own registry id
RC = "33333333-3333-4333-8333-333333333333"
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


# -- helpers -----------------------------------------------------------------

def _post(client, path, body):
    return client.post(path, data=json.dumps(body), content_type="application/json")


def _activate(client, key, full_key, label, **extra):
    return _post(client, "/api/licensing/v1/activations", build_activation_body(key, full_key=full_key, installation_id=label, **extra))


def _checkin(client, key, iid, **extra):
    return _post(client, "/api/licensing/v1/check-ins", build_checkin_body(key, installation_id=iid, **extra))


def _payload(resp):
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["signed_assertion"]["payload"]


def _iid(resp):
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["installation_id"]


def _pin(app, iid):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        db_session.expire_all()
        return db_session.get(Installation, uuid.UUID(iid)).registry_device_id


def _events(app, iid, event_type):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.installations import ActivationEvent

        return [dict(e.safe_metadata) for e in db_session.execute(select(ActivationEvent).where(
            ActivationEvent.installation_id == uuid.UUID(iid), ActivationEvent.event_type == event_type)).scalars().all()]


def _set_created(app, iid, when):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        db_session.get(Installation, uuid.UUID(iid)).created_at = when
        db_session.commit()


def _push(client, key, iid, events):
    body = sign_body(key, {"installation_id": iid, "timestamp": datetime.now(timezone.utc).isoformat(),
                           "nonce": uuid.uuid4().hex, "events": events})
    resp = _post(client, "/api/sync/v1/push", body)
    assert resp.status_code == 200, resp.get_json()
    return resp


def _ev(entity_type, *, entity_id=None, payload=None, created_at):
    return {"id": str(uuid.uuid4()), "entity_type": entity_type, "entity_id": str(entity_id or uuid.uuid4()),
            "event_type": "update", "payload": payload or {}, "created_at": created_at}


def _device_ev(rid, created_at):
    """What a real retail computer pushes for a registry device row
    (device_registry.py::queue_device_sync_event: entity_id = the device id)."""
    return _ev("device", entity_id=rid, payload={"device_id": rid}, created_at=created_at)


def _admin_ev(rid, created_at):
    """What a real retail computer pushes for the main assignment
    (device_registry.py::queue_admin_device_sync_event: fresh entity_id, payload.device_id)."""
    return _ev("admin_device", payload={"device_id": rid, "epoch": 1, "assigned_at_utc": created_at}, created_at=created_at)


def _transfer(app, rid, to_iid, actor, reason="staff: this is the real computer"):
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.registry_pin import transfer_registry_device_id

        transfer_registry_device_id(db_session, registry_device_id=rid, to_installation_id=uuid.UUID(to_iid),
                                    actor_staff_user_id=actor, reason=reason)


@pytest.fixture()
def shop(app, client, seeded, signing_key):
    actor = make_staff(app, f"v4o-{uuid.uuid4().hex[:6]}@example.com")
    license_id, full_key = make_license(app, actor, device_limit=6)
    return {"actor": actor, "license_id": license_id, "full_key": full_key}


def _main_and_member(app, client, shop, *, main_created=T0):
    """A pre-release-style main (activated, never pinned) and a post-release member
    that pinned ITS OWN id at activation (so it can never claim RA itself: rule 3)."""
    main_key, mem_key = make_device_keypair(), make_device_keypair()
    main_iid = _iid(_activate(client, main_key, shop["full_key"], "v4o-main"))
    _set_created(app, main_iid, main_created)
    mem_iid = _iid(_activate(client, mem_key, shop["full_key"], "v4o-mem", registry_device_id=RB))
    assert _pin(app, mem_iid) == RB and _pin(app, main_iid) is None
    return main_key, main_iid, mem_key, mem_iid


# -- Q1: D-ADAPT judgement, NO history ------------------------------------------

def test_q1_residual_no_history_new_install_wins_but_it_is_visible_and_recoverable(app, client, shop):
    """RESIDUAL (contract doc 'LAN-only shops ... no history'): with no synced
    history a brand-new installation still takes an unpinned main's id. Pin the
    residual's exact shape: the main's refusal is recorded naming the squatter,
    and the staff transfer gives the id back. If any of these change, the residual
    got wider (silent or unrecoverable)."""
    a_key, b_key = make_device_keypair(), make_device_keypair()
    a_iid = _iid(_activate(client, a_key, shop["full_key"], "q1-a"))
    _set_created(app, a_iid, T0)
    b_iid = _iid(_activate(client, b_key, shop["full_key"], "q1-b", registry_device_id=RA))
    assert _pin(app, b_iid) == RA                       # the residual itself, measured
    assert "registry_device_id" not in _payload(_checkin(client, a_key, a_iid, registry_device_id=RA))
    (clash,) = _events(app, a_iid, "REGISTRY_DEVICE_ID_CLASH")
    assert clash == {"requested": RA, "held_by_installation": b_iid}
    _transfer(app, RA, a_iid, shop["actor"])
    assert _payload(_checkin(client, a_key, a_iid, registry_device_id=RA))["registry_device_id"] == RA
    assert "registry_device_id" not in _payload(_checkin(client, b_key, b_iid, registry_device_id=RA))


def test_q1_real_main_history_shape_blocks_a_new_install(app, client, shop):
    """D-ADAPT realism check: history produced through the REAL push route by the
    main itself (its heartbeat `device` row, device_registry.py:1077) -- not a
    hand-inserted row -- is enough for rule 5 to stop a brand-new installation."""
    main_key = make_device_keypair()
    main_iid = _iid(_activate(client, main_key, shop["full_key"], "q1r-main"))
    _push(client, main_key, main_iid, [_device_ev(RA, (datetime.now(timezone.utc) - timedelta(days=2)).isoformat())])
    _set_created(app, main_iid, datetime.now(timezone.utc) - timedelta(days=3))
    b = _activate(client, make_device_keypair(), shop["full_key"], "q1r-b", registry_device_id=RA)
    assert "registry_device_id" not in _payload(b), "a brand-new installation was attested as the main"
    assert _payload(_checkin(client, main_key, main_iid, registry_device_id=RA))["registry_device_id"] == RA


# -- Q2: forging the history the guard reads ------------------------------------

@pytest.mark.parametrize("main_history", ["none", "device-row-only"])
def test_q2_a_member_cannot_veto_the_real_main_with_an_old_dated_admin_device_event(app, client, shop, main_history):
    """GAP candidate (DoS). client_created_at is chosen by the PUSHING device and
    Owner stores it unchecked (sync/routes.py::_build_event). A member that is
    already pinned to its own id (so it cannot steal) pushes one admin_device
    event naming the main's id, dated 2001. The real, unpinned main (activated
    2026-09-01) then checks in. Safe outcome: the main is attested."""
    main_key, main_iid, mem_key, mem_iid = _main_and_member(app, client, shop)
    if main_history == "device-row-only":
        # the main's own heartbeat row, AFTER its activation (legitimate history)
        _push(client, main_key, main_iid, [_device_ev(RA, (T0 + timedelta(hours=1)).isoformat())])
    _push(client, mem_key, mem_iid, [_admin_ev(RA, "2001-01-01T00:00:00+00:00")])
    got = _payload(_checkin(client, main_key, main_iid, registry_device_id=RA))
    assert got.get("registry_device_id") == RA, (
        f"a member vetoed the real main: {_events(app, main_iid, 'REGISTRY_DEVICE_ID_HISTORY_CONFLICT')}")


def test_q2_old_dated_forgery_is_ignored_once_the_mains_own_history_is_first(app, client, shop):
    """Allow half / what HOLDS: the guard takes the FIRST event by seq per kind, so a
    forgery pushed AFTER the main's own device AND admin_device events cannot veto it."""
    main_key, main_iid, mem_key, mem_iid = _main_and_member(app, client, shop)
    at = (T0 + timedelta(hours=1)).isoformat()
    _push(client, main_key, main_iid, [_device_ev(RA, at), _admin_ev(RA, at)])
    _push(client, mem_key, mem_iid, [_device_ev(RA, "2001-01-01T00:00:00+00:00"), _admin_ev(RA, "2001-01-01T00:00:00")])
    assert _payload(_checkin(client, main_key, main_iid, registry_device_id=RA))["registry_device_id"] == RA


@pytest.mark.parametrize("shape", [
    ("user", "payload"), ("user_permission", "payload"), ("sale", "payload"), ("device", "payload-only"),
], ids=lambda s: f"{s[0]}-{s[1]}")
def test_q2_other_entity_types_or_payload_only_mentions_do_not_veto(app, client, shop, shape):
    """HOLDS expected: only `device` entity_id and `admin_device` payload.device_id are read."""
    main_key, main_iid, mem_key, mem_iid = _main_and_member(app, client, shop)
    etype, _ = shape
    _push(client, mem_key, mem_iid, [_ev(etype, payload={"device_id": RA, "registry_device_id": RA},
                                         created_at="2001-01-01T00:00:00+00:00")])
    assert _payload(_checkin(client, main_key, main_iid, registry_device_id=RA))["registry_device_id"] == RA


@pytest.mark.parametrize("poison", [False, True], ids=["no-poison-control", "future-dated-poison-first"])
def test_q2_future_dated_forgery_pushed_first_cannot_switch_the_guard_off(app, client, shop, poison):
    """GAP candidate (guard bypass). Before the main's id first reaches Owner, a member
    (pinned to its own id, so it cannot claim RA itself) pushes events naming RA dated
    2099. Because the guard reads the FIRST event by seq, the main's real history that
    arrives later is ignored. An accomplice installation activated NOW -- 20 days after
    the main's real first synced event -- then claims RA. Safe outcome: refused
    (the contract says the race exists only for an installation OLDER than the id's
    first synced event)."""
    main_key, main_iid, mem_key, mem_iid = _main_and_member(app, client, shop,
                                                          main_created=datetime.now(timezone.utc) - timedelta(days=30))
    if poison:
        _push(client, mem_key, mem_iid, [_device_ev(RA, "2099-01-01T00:00:00+00:00"), _admin_ev(RA, "2099-01-01T00:00:00+00:00")])
    real = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    _push(client, main_key, main_iid, [_device_ev(RA, real), _admin_ev(RA, real)])
    acc = _activate(client, make_device_keypair(), shop["full_key"], "q2-acc", registry_device_id=RA)
    assert "registry_device_id" not in _payload(acc), "an accomplice newer than the main's history took the main's id"


# -- Q3: staff transfer ----------------------------------------------------------

def test_q3_a_history_conflict_victim_can_be_given_its_id_by_staff(app, client, shop):
    """GAP candidate (no recovery). The contract doc (Residual, 2nd bullet) says a
    legitimate computer refused by rule 5 -- its registry row was relayed by other
    computers before it activated with Owner -- is resolved by the staff transfer.
    Nobody holds the id in that case. Safe outcome: staff can pin it."""
    relay_key = make_device_keypair()
    relay_iid = _iid(_activate(client, relay_key, shop["full_key"], "q3-relay", registry_device_id=RB))
    # the LAN hub relays the new till's registry row 3 days before the till activates
    _push(client, relay_key, relay_iid, [_device_ev(RC, (datetime.now(timezone.utc) - timedelta(days=3)).isoformat())])
    till_key = make_device_keypair()
    till_iid = _iid(_activate(client, till_key, shop["full_key"], "q3-till", registry_device_id=RC))
    assert _pin(app, till_iid) is None and len(_events(app, till_iid, "REGISTRY_DEVICE_ID_HISTORY_CONFLICT")) == 1
    from app.licensing_service.registry_pin import RegistryTransferError

    try:
        _transfer(app, RC, till_iid, shop["actor"], "till activated after the hub relayed its row")
    except RegistryTransferError as exc:
        pytest.fail(f"staff have no way to pin a HISTORY_CONFLICT victim: transfer refused {exc.code}")
    assert _payload(_checkin(client, till_key, till_iid, registry_device_id=RC))["registry_device_id"] == RC


def test_q3_cli_refuses_a_disabled_staff_account_as_actor(app, client, shop):
    """GAP candidate (LOW, accountability). The audit row names --actor as the
    accountable person; a disabled account must not be usable."""
    a_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "q3c-a", registry_device_id=RA))
    b_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "q3c-b"))
    disabled = make_staff(app, f"v4o-off-{uuid.uuid4().hex[:6]}@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        s = db_session.get(StaffUser, disabled)
        s.is_active, s.disabled_at, s.disabled_reason = False, datetime.now(timezone.utc), "left the company"
        email = s.email
        db_session.commit()
    res = app.test_cli_runner().invoke(args=["licensing", "transfer-registry-id", "--registry-device-id", RA,
                                             "--to", b_iid, "--actor", email, "--reason", "x"])
    assert res.exit_code != 0, "a disabled staff account was accepted as the accountable actor"
    assert _pin(app, a_iid) == RA and _pin(app, b_iid) is None


def test_q3_transfer_never_silently_overwrites_a_pin_that_landed_concurrently(app, client, shop, monkeypatch):
    """GAP candidate (LOW, lost update). The transfer reads the target's pin, then
    later writes RA with no lock/version check. If the target's own check-in pins RB
    in between, RB is overwritten with no CHANGE_REFUSED trail. Safe outcome: the
    transfer refuses or the target keeps RB."""
    from app.licensing_service import registry_pin

    a_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "q3l-a", registry_device_id=RA))
    b_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "q3l-b"))
    real = registry_pin.licence_chain_ids
    fired = []

    def racing_chain(session, license_id):
        if not fired:   # first call inside transfer: AFTER target was read, BEFORE it is written
            fired.append(1)
            eng = session.get_bind()
            with eng.connect() as other:   # a concurrent request, its own connection + commit
                other.execute(text("UPDATE owner_installations SET registry_device_id=:r, registry_device_id_pinned_at=now() "
                                   "WHERE id=:i"), {"r": RB, "i": uuid.UUID(b_iid)})
                other.commit()
        return real(session, license_id)

    monkeypatch.setattr(registry_pin, "licence_chain_ids", racing_chain)
    try:
        _transfer(app, RA, b_iid, shop["actor"])
    except registry_pin.RegistryTransferError:
        pass
    monkeypatch.setattr(registry_pin, "licence_chain_ids", real)
    assert fired, "the race hook never ran"
    assert _pin(app, b_iid) == RB, "the transfer silently overwrote a pin that landed concurrently"


# -- Q4: chain cycles, migration on duplicates ----------------------------------

@pytest.mark.parametrize("shape", ["two-cycle", "self-loop"])
def test_q4_licence_chain_cycle_terminates(app, client, shop, shape):
    """HOLDS expected: A->B->A (or A->A) must not loop forever, on the check-in path too."""
    from app.extensions import db_session
    from app.licensing_service.registry_pin import licence_chain_ids
    from app.models.licensing import License

    b_license, _ = make_license(app, shop["actor"], device_limit=1)
    with app.app_context():
        a = db_session.get(License, shop["license_id"])
        if shape == "self-loop":
            a.replaced_by_license_id = a.id
            expected = {shop["license_id"]}
        else:
            a.replaced_by_license_id = b_license
            db_session.get(License, b_license).replaced_by_license_id = a.id
            expected = {shop["license_id"], b_license}
        db_session.commit()

    out, err = [], []

    def run():
        try:
            with app.app_context():
                out.append(licence_chain_ids(db_session, shop["license_id"]))
        except Exception as exc:  # noqa: BLE001 -- surfaced by the assert below
            err.append(repr(exc))

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(20)
    assert not t.is_alive(), "licence_chain_ids never returned on a cyclic chain"
    assert not err, err
    assert out == [expected]


PREV, NEW_REV = "5f2c8e1a9b37", "7c41d2e9a0b5"
SCRATCH = "aura_owner_v4o_dup_scratch"
IDX = "uq_owner_installations_license_registry_device"


def test_q4_migration_on_existing_duplicate_pins_fails_loudly_and_atomically():
    """The old index let a DEACTIVATED and an ACTIVE row share an id. Upgrading such
    a DB must fail loudly (never silently drop a row or a pin), and must not leave
    the table WITHOUT any unique index (drop_index ran first). Safe outcome:
    non-zero exit, version still PREV, old index intact, both rows intact."""
    from tests.test_sauth_registry_pin_migration import _alembic, _seed

    owner_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if not os.path.isfile(os.path.join(owner_dir, "alembic.ini")):
        owner_dir = os.path.dirname(owner_dir)
    base = TEST_DB_URL.rsplit("/", 1)[0]
    scratch_url, admin_url = f"{base}/{SCRATCH}", f"{base}/postgres"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {SCRATCH} OWNER aura_owner"))
    admin.dispose()
    try:
        assert _alembic(owner_dir, scratch_url, "upgrade", PREV).returncode == 0
        engine = create_engine(scratch_url)
        cache: dict = {}
        dup = str(uuid.uuid4())
        with engine.begin() as conn:
            lic = _seed(conn, "owner_licenses", cache)
            cache["owner_licenses"] = lic
            _seed(conn, "owner_installations", dict(cache), {"license_id": lic, "status": "DEACTIVATED", "registry_device_id": dup})
            _seed(conn, "owner_installations", dict(cache), {"license_id": lic, "status": "ACTIVE", "registry_device_id": dup})
        engine.dispose()
        up = _alembic(owner_dir, scratch_url, "upgrade", NEW_REV)
        engine = create_engine(scratch_url)
        with engine.connect() as conn:
            version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
            rows = conn.execute(text("SELECT status FROM owner_installations WHERE registry_device_id=:d"), {"d": dup}).scalars().all()
        idx = {i["name"]: i for i in inspect(engine).get_indexes("owner_installations")}.get(IDX)
        engine.dispose()
        assert up.returncode != 0, "upgrade silently succeeded over duplicate pins"
        assert "could not create unique index" in up.stderr or "UniqueViolation" in up.stderr, up.stderr[-800:]
        assert version == PREV, version
        assert sorted(rows) == ["ACTIVE", "DEACTIVATED"], rows
        assert idx is not None and idx["unique"], "failed upgrade left the table with NO unique index on the pin"
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)"))
        admin.dispose()
