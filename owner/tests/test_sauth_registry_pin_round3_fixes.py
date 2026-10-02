"""AUDIT-033 / review V4o G1-G5 (round-3 fix crew F): allow AND deny halves.

G1/G2  the history guard reads Owner's RECEIPT time (SyncEvent.created_at),
       never the pushing device's client_created_at.
G3     the staff transfer can also ASSIGN an id nobody holds.
G4     the accountable actor must be ACTIVE staff; its role is audited.
G5     every write of a pin is serialised per id (advisory lock) and written
       with conditional UPDATEs.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select, text

from tests.conftest import (
    TEST_DB_URL, build_activation_body, build_checkin_body, make_device_keypair, make_license, make_staff,
)

RA = "11111111-1111-4111-8111-111111111111"
RB = "22222222-2222-4222-8222-222222222222"
RC = "33333333-3333-4333-8333-333333333333"


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


def _holders(app, rid):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        db_session.expire_all()
        return sorted(str(i) for i in db_session.execute(
            select(Installation.id).where(Installation.registry_device_id == rid)).scalars().all())


def _events(app, iid, event_type):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import ActivationEvent

        db_session.expire_all()
        return [dict(e.safe_metadata) for e in db_session.execute(select(ActivationEvent).where(
            ActivationEvent.installation_id == uuid.UUID(iid), ActivationEvent.event_type == event_type)).scalars().all()]


def _audit(app, iid, action):
    with app.app_context():
        from app.extensions import db_session
        from app.models.audit import AuditLog

        db_session.expire_all()
        return db_session.execute(select(AuditLog).where(
            AuditLog.entity_public_id == iid, AuditLog.action_code == action)).scalars().all()


def _set_created(app, iid, when):
    with app.app_context():
        from app.extensions import db_session
        from app.models.installations import Installation

        db_session.get(Installation, uuid.UUID(iid)).created_at = when
        db_session.commit()


def _seed_event(app, license_id, rid, *, received_at, client_at):
    """A registry `device` row in the relay log with its RECEIPT time and the
    pushing device's own (possibly lying) time set independently."""
    with app.app_context():
        from app.extensions import db_session
        from app.models.sync import SyncEvent

        db_session.add(SyncEvent(
            id=uuid.uuid4(), license_id=license_id, device_id=uuid.uuid4(), entity_type="device",
            entity_id=uuid.UUID(rid), event_type="create", payload={},
            client_created_at=client_at, created_at=received_at))
        db_session.commit()


def _transfer(app, rid, to_iid, actor, reason="staff: this is the real computer"):
    with app.app_context():
        from app.extensions import db_session
        from app.licensing_service.registry_pin import transfer_registry_device_id

        transfer_registry_device_id(db_session, registry_device_id=rid, to_installation_id=uuid.UUID(to_iid),
                                    actor_staff_user_id=actor, reason=reason)


def _code(app, rid, to_iid, actor, reason="why"):
    from app.licensing_service.registry_pin import RegistryTransferError

    with pytest.raises(RegistryTransferError) as ei:
        _transfer(app, rid, to_iid, actor, reason)
    return ei.value.code


@pytest.fixture()
def shop(app, client, seeded, signing_key):
    actor = make_staff(app, f"ff-{uuid.uuid4().hex[:6]}@example.com")
    license_id, full_key = make_license(app, actor, device_limit=6)
    return {"actor": actor, "license_id": license_id, "full_key": full_key}


def _replacement(app, shop):
    """A second licence on the SAME chain as the shop's (A.replaced_by -> B), with
    A left ACTIVE so installations on BOTH licences can still check in. (A real
    replace_license() marks A replaced, which would stop A's computers checking
    in, and a race between two licences needs both live.) Returns (id, key)."""
    from app.extensions import db_session
    from app.models.licensing import License

    new_id, new_key = make_license(app, shop["actor"], device_limit=6)
    with app.app_context():
        db_session.get(License, shop["license_id"]).replaced_by_license_id = new_id
        db_session.commit()
    return new_id, new_key


# -- G1/G2: server receipt time ------------------------------------------------

def test_g1_history_received_before_the_install_blocks_even_if_the_client_dated_it_in_the_future(app, client, shop):
    """Deny half: the pusher's clock says 2099, Owner received it 2 days ago."""
    _seed_event(app, shop["license_id"], RA, received_at=datetime.now(timezone.utc) - timedelta(days=2),
                client_at=datetime(2099, 1, 1, tzinfo=timezone.utc))
    resp = _activate(client, make_device_keypair(), shop["full_key"], "g1-new", registry_device_id=RA)
    assert "registry_device_id" not in _payload(resp)
    assert len(_events(app, _iid(resp), "REGISTRY_DEVICE_ID_HISTORY_CONFLICT")) == 1


def test_g2_history_received_after_the_install_does_not_block_even_if_the_client_dated_it_in_2001(app, client, shop):
    """Allow half: the pusher's clock says 2001, Owner received it just now."""
    key = make_device_keypair()
    iid = _iid(_activate(client, key, shop["full_key"], "g2-main"))
    _set_created(app, iid, datetime.now(timezone.utc) - timedelta(days=1))
    _seed_event(app, shop["license_id"], RA, received_at=datetime.now(timezone.utc),
                client_at=datetime(2001, 1, 1, tzinfo=timezone.utc))
    assert _payload(_checkin(client, key, iid, registry_device_id=RA))["registry_device_id"] == RA


# -- G3: assign ------------------------------------------------------------------

def _history_victim(app, client, shop, rid=RC):
    """A computer the history guard refused: its row was relayed before it activated."""
    _seed_event(app, shop["license_id"], rid, received_at=datetime.now(timezone.utc) - timedelta(days=3),
                client_at=datetime.now(timezone.utc) - timedelta(days=3))
    key = make_device_keypair()
    iid = _iid(_activate(client, key, shop["full_key"], "g3-victim", registry_device_id=rid))
    assert _pin(app, iid) is None
    return key, iid


def test_g3_assign_pins_an_id_nobody_holds_and_audits_it_with_from_null(app, client, shop):
    key, iid = _history_victim(app, client, shop)
    _transfer(app, RC, iid, shop["actor"], "  till activated after the hub relayed its row  ")
    assert _pin(app, iid) == RC and _holders(app, RC) == [iid]
    (meta,) = _events(app, iid, "REGISTRY_ID_TRANSFERRED")
    assert meta == {"registry_device_id": RC, "from_installation": None, "to_installation": iid,
                    "reason": "till activated after the hub relayed its row"}
    (row,) = _audit(app, iid, "REGISTRY_ID_TRANSFERRED")
    assert row.actor_staff_user_id == shop["actor"] and row.reason == "till activated after the hub relayed its row"
    assert _payload(_checkin(client, key, iid, registry_device_id=RC))["registry_device_id"] == RC
    # the id is now reserved for it like any other pin: nobody else can take it
    other = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g3-other", registry_device_id=RC))
    assert _pin(app, other) is None and _holders(app, RC) == [iid]


def test_g3_assign_refusals_change_nothing(app, client, shop):
    _, iid = _history_victim(app, client, shop)
    # reason is still required
    assert _code(app, RC, iid, shop["actor"], "  ") == "REASON_REQUIRED"
    # target pinned to a DIFFERENT id: refused even though nobody holds RC
    pinned = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g3-pinned", registry_device_id=RB))
    assert _code(app, RC, pinned, shop["actor"]) == "TARGET_PINNED_TO_A_DIFFERENT_ID"
    # held on a DIFFERENT licence chain: not assignable here
    _, other_key = make_license(app, shop["actor"], device_limit=2)
    foreign = _iid(_activate(client, make_device_keypair(), other_key, "g3-foreign", registry_device_id=RA))
    assert _code(app, RA, iid, shop["actor"]) == "HOLDER_ON_A_DIFFERENT_LICENCE_CHAIN"
    assert _code(app, RC, str(uuid.uuid4()), shop["actor"]) == "TARGET_NOT_FOUND"
    assert _pin(app, iid) is None and _pin(app, pinned) == RB and _pin(app, foreign) == RA
    assert _events(app, iid, "REGISTRY_ID_TRANSFERRED") == [] and _audit(app, iid, "REGISTRY_ID_TRANSFERRED") == []


# -- G4: actor ---------------------------------------------------------------------

def _disable(app, staff_id, *, via):
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        s = db_session.get(StaffUser, staff_id)
        if via == "is_active":
            s.is_active = False
        else:
            s.disabled_at = datetime.now(timezone.utc)
        db_session.commit()


@pytest.mark.parametrize("via", ["is_active", "disabled_at"])
def test_g4_service_refuses_a_disabled_actor_and_an_unknown_actor(app, client, shop, via):
    a_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g4-a", registry_device_id=RA))
    b_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g4-b"))
    ghost = make_staff(app, f"ff-off-{uuid.uuid4().hex[:6]}@example.com")
    _disable(app, ghost, via=via)
    assert _code(app, RA, b_iid, ghost) == "ACTOR_NOT_ACTIVE_STAFF"
    assert _code(app, RA, b_iid, uuid.uuid4()) == "ACTOR_NOT_ACTIVE_STAFF"
    assert _pin(app, a_iid) == RA and _pin(app, b_iid) is None
    _transfer(app, RA, b_iid, shop["actor"])          # allow half: an active actor works
    assert _pin(app, b_iid) == RA


@pytest.mark.parametrize("roles,super_admin,expected", [
    (["SALES"], False, "SALES"), ([], True, "super_admin"), ([], False, "no-role"),
])
def test_g4_audit_row_records_the_actors_role_snapshot(app, client, shop, roles, super_admin, expected):
    actor = make_staff(app, f"ff-role-{uuid.uuid4().hex[:6]}@example.com", super_admin=super_admin, role_codes=roles)
    _iid(_activate(client, make_device_keypair(), shop["full_key"], "g4r-a", registry_device_id=RA))
    b_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g4r-b"))
    _transfer(app, RA, b_iid, actor)
    (row,) = _audit(app, b_iid, "REGISTRY_ID_TRANSFERRED")
    assert row.actor_role_snapshot == expected


def test_g4_cli_accepts_an_active_actor_for_an_assign(app, client, shop):
    _, iid = _history_victim(app, client, shop)
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        email = db_session.get(StaffUser, shop["actor"]).email
    res = app.test_cli_runner().invoke(args=["licensing", "transfer-registry-id", "--registry-device-id", RC,
                                             "--to", iid, "--actor", email, "--reason", "assign"])
    assert res.exit_code == 0, res.output
    assert _pin(app, iid) == RC


# -- G5: lock + conditional writes ----------------------------------------------------

def test_g5_lock_key_is_a_stable_signed_64_bit_hash_of_the_id():
    from app.licensing_service.registry_pin import registry_pin_lock_key

    expected = int.from_bytes(hashlib.sha256(("aura-registry-pin:" + RA).encode()).digest()[:8], "big", signed=True)
    assert registry_pin_lock_key(RA) == expected == registry_pin_lock_key(RA)
    assert -2**63 <= expected < 2**63 and registry_pin_lock_key(RB) != expected


def _hold_lock(rid):
    """A separate connection holding the per-id lock; returns (conn, release)."""
    from app.licensing_service.registry_pin import registry_pin_lock_key

    engine = create_engine(TEST_DB_URL)
    conn = engine.connect()
    conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": registry_pin_lock_key(rid)})

    def release():
        conn.rollback()
        conn.close()
        engine.dispose()
    return release


def _run_blocked(fn):
    """Run fn in a thread; returns (thread, result list, error list)."""
    out, err = [], []

    def run():
        try:
            out.append(fn())
        except Exception as exc:  # noqa: BLE001 -- surfaced by the asserts
            err.append(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out, err


def test_g5_transfer_waits_for_the_per_id_lock(app, client, shop):
    a_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g5l-a", registry_device_id=RA))
    b_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g5l-b"))
    release = _hold_lock(RA)
    t, out, err = _run_blocked(lambda: _transfer(app, RA, b_iid, shop["actor"]))
    try:
        t.join(2.0)
        assert t.is_alive(), "the transfer did not wait for the per-id lock"
        assert _pin(app, b_iid) is None
    finally:
        release()
    t.join(20)
    assert not t.is_alive() and not err, err
    assert _pin(app, b_iid) == RA and _pin(app, a_iid) is None


def test_g5_the_automatic_pin_waits_for_the_per_id_lock(app, client, shop):
    b_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g5p-b"))
    b_key_holder = {}
    # an existing, unpinned installation checks in with the id while the lock is held
    key = make_device_keypair()
    iid = _iid(_activate(client, key, shop["full_key"], "g5p-c"))
    b_key_holder["k"] = key
    release = _hold_lock(RA)
    t, out, err = _run_blocked(lambda: _checkin(client, key, iid, registry_device_id=RA).status_code)
    try:
        t.join(2.0)
        assert t.is_alive(), "the automatic pin did not wait for the per-id lock"
        assert _pin(app, iid) is None
    finally:
        release()
    t.join(30)
    assert not t.is_alive() and not err, err
    assert out == [200] and _pin(app, iid) == RA and b_iid != iid


def _barrier_hook(monkeypatch, name, parties=2):
    """Make the first `parties` callers of registry_pin.<name> meet at a barrier
    (3 s timeout, so a correct lock that keeps the 2nd caller out merely times
    the 1st one out instead of deadlocking)."""
    from app.licensing_service import registry_pin

    real = getattr(registry_pin, name)
    barrier = threading.Barrier(parties)

    def hooked(*a, **k):
        try:
            barrier.wait(3)
        except threading.BrokenBarrierError:
            pass
        return real(*a, **k)

    monkeypatch.setattr(registry_pin, name, hooked)


def test_g5_two_concurrent_assigns_on_two_licences_of_one_chain_leave_one_holder(app, client, shop, monkeypatch):
    t1 = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g5c-1"))
    new_license, new_key = _replacement(app, shop)
    t2 = _iid(_activate(client, make_device_keypair(), new_key, "g5c-2"))
    _barrier_hook(monkeypatch, "licence_chain_ids")
    results = []

    def go(target):
        from app.extensions import db_session

        try:
            _transfer(app, RA, target, shop["actor"], f"concurrent {target[:4]}")
            results.append("ok")
        except Exception as exc:  # noqa: BLE001
            results.append(getattr(exc, "code", repr(exc)))
        finally:
            with app.app_context():
                db_session.remove()

    threads = [threading.Thread(target=go, args=(t,), daemon=True) for t in (t1, t2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads)
    holders = _holders(app, RA)
    assert len(holders) == 1, f"two holders for one id: {holders} ({results})"
    assert results.count("ok") == 2     # serialised: the second one MOVES it from the first


def test_g5_two_concurrent_automatic_pins_on_two_licences_of_one_chain_leave_one_holder(app, client, shop, monkeypatch):
    k1, k2 = make_device_keypair(), make_device_keypair()
    i1 = _iid(_activate(client, k1, shop["full_key"], "g5a-1"))
    _, new_key = _replacement(app, shop)
    i2 = _iid(_activate(client, k2, new_key, "g5a-2"))
    _barrier_hook(monkeypatch, "_first_history_at")
    codes = []

    def go(key, iid):
        from app.extensions import db_session

        try:
            codes.append(_checkin(client, key, iid, registry_device_id=RA).status_code)
        finally:
            with app.app_context():
                db_session.remove()

    threads = [threading.Thread(target=go, args=a, daemon=True) for a in ((k1, i1), (k2, i2))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not any(t.is_alive() for t in threads) and codes == [200, 200]
    assert len(_holders(app, RA)) == 1, _holders(app, RA)


def test_g5_transfer_refuses_when_the_holder_changes_under_it_and_rolls_back(app, client, shop, monkeypatch):
    """Conditional UPDATE on the HOLDER: the holder is cleared by another
    connection after it was read; the transfer must refuse, not pin blindly."""
    from app.licensing_service import registry_pin

    a_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g5h-a", registry_device_id=RA))
    b_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g5h-b"))
    real, fired = registry_pin.update, []

    def racing_update(*a, **k):
        if not fired:
            fired.append(1)
            engine = create_engine(TEST_DB_URL)
            with engine.connect() as other:
                other.execute(text("UPDATE owner_installations SET registry_device_id=NULL WHERE id=:i"), {"i": uuid.UUID(a_iid)})
                other.commit()
            engine.dispose()
        return real(*a, **k)

    monkeypatch.setattr(registry_pin, "update", racing_update)
    assert _code(app, RA, b_iid, shop["actor"]) == "CONCURRENT_PIN_CHANGE"
    monkeypatch.setattr(registry_pin, "update", real)
    assert fired and _pin(app, b_iid) is None
    assert _events(app, b_iid, "REGISTRY_ID_TRANSFERRED") == []


def test_g5_automatic_pin_never_overwrites_a_pin_that_landed_after_it_looked(app, client, shop, monkeypatch):
    """Conditional UPDATE on the automatic path: B's own pin (RB) lands on
    another connection after resolve() read B; RA must not overwrite it."""
    from app.extensions import db_session
    from app.licensing_service import registry_pin
    from app.models.installations import Installation

    b_iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g5u-b"))
    real, fired = registry_pin._holder, []

    def racing_holder(installation, rid):
        if not fired:
            fired.append(1)
            engine = create_engine(TEST_DB_URL)
            with engine.connect() as other:
                other.execute(text("UPDATE owner_installations SET registry_device_id=:r WHERE id=:i"),
                              {"r": RB, "i": uuid.UUID(b_iid)})
                other.commit()
            engine.dispose()
        return real(installation, rid)

    monkeypatch.setattr(registry_pin, "_holder", racing_holder)
    with app.app_context():
        inst = db_session.get(Installation, uuid.UUID(b_iid))
        outcome = registry_pin.resolve_registry_device_pin(installation=inst, requested=RA)
        db_session.rollback()
    assert fired and outcome.event_type == registry_pin.EVENT_CHANGE_REFUSED and outcome.attested_id == RB
    assert _pin(app, b_iid) == RB


def test_g5_resolve_itself_waits_for_the_per_id_lock(app, client, shop):
    """The rule-4 lock inside resolve_registry_device_pin (for direct callers; the
    HTTP paths also lock early, see the next two tests)."""
    from app.extensions import db_session
    from app.licensing_service import registry_pin
    from app.models.installations import Installation

    iid = _iid(_activate(client, make_device_keypair(), shop["full_key"], "g5r-a"))

    def call():
        with app.app_context():
            try:
                inst = db_session.get(Installation, uuid.UUID(iid))
                return registry_pin.resolve_registry_device_pin(installation=inst, requested=RA).event_type
            finally:
                db_session.rollback()
                db_session.remove()

    release = _hold_lock(RA)
    t, out, err = _run_blocked(call)
    try:
        t.join(2.0)
        assert t.is_alive(), "resolve_registry_device_pin did not wait for the per-id lock"
    finally:
        release()
    t.join(30)
    assert not t.is_alive() and not err and out == ["REGISTRY_DEVICE_ID_PINNED"], (out, err)


def _finishes_within(fn, seconds):
    t, out, err = _run_blocked(fn)
    t.join(seconds)
    return (not t.is_alive()), out, err


def test_g5_an_activation_waiting_for_the_id_lock_does_not_hold_the_licence_row(app, client, shop):
    """Lock ORDER (found by the concurrent-pin tests: a deadlock, HTTP 500). An
    activation takes the licence row FOR UPDATE; if it waits for the per-id lock
    AFTER that, every other activation/check-in on the licence queues behind it
    and two paths can deadlock. Hold the id lock, start an activation claiming the
    id, then a check-in of another computer on the SAME licence must still finish."""
    other_key = make_device_keypair()
    other = _iid(_activate(client, other_key, shop["full_key"], "g5o-other"))
    release = _hold_lock(RA)
    t, out, err = _run_blocked(lambda: _activate(app.test_client(), make_device_keypair(), shop["full_key"], "g5o-new",
                                                 registry_device_id=RA).status_code)
    try:
        t.join(2.0)
        assert t.is_alive(), "the activation did not wait for the per-id lock"
        done, got, errs = _finishes_within(lambda: _checkin(app.test_client(), other_key, other).status_code, 10)
        assert done and got == [200] and not errs, "a waiting activation blocked the licence for everyone else"
    finally:
        release()
    t.join(30)
    assert not t.is_alive() and not err and out == [200], (out, err)


def test_g5_a_check_in_waiting_for_the_id_lock_holds_nothing_the_licence_needs(app, client, shop):
    """Same property for the check-in path: while it waits for the id lock it must
    not yet reference the licence row, or an activation (FOR UPDATE) queues behind
    it."""
    claimer_key = make_device_keypair()
    claimer = _iid(_activate(client, claimer_key, shop["full_key"], "g5q-claimer"))
    release = _hold_lock(RA)
    t, out, err = _run_blocked(lambda: _checkin(app.test_client(), claimer_key, claimer, registry_device_id=RA).status_code)
    try:
        t.join(2.0)
        assert t.is_alive(), "the check-in did not wait for the per-id lock"
        done, got, errs = _finishes_within(
            lambda: _activate(app.test_client(), make_device_keypair(), shop["full_key"], "g5q-new").status_code, 10)
        assert done and got == [200] and not errs, "a waiting check-in blocked an activation on the same licence"
    finally:
        release()
    t.join(30)
    assert not t.is_alive() and not err and out == [200], (out, err)
