"""AUDIT-033 / S-AUTH fix round -- the verifier's registry-stream attacks, ported.

These are the attack tests the adversarial verifier (V) wrote against
fafaf7cc, taken over VERBATIM (names, bodies, assertions) except for the one
deliberately corrected setup noted at `test_V9_replaying_...` below. Every test
asserts the SECURE outcome, so a failing test means the attack works. They were
RED on fafaf7cc (the crew's progress.md quotes each failure) and are GREEN on the
fixed tree.

They use only helpers that existed at fafaf7cc (`Shop`, `world.new_device`,
`world.sign`, `install_main`, `admin_claim_event`, the `LocalSigner`
constructor), so the same file runs against both trees. Tests that need
helpers added by the fix live in test_sync_authority_fixround.py.

Run (ONE FILE PER PYTEST PROCESS):
    pytest commercial_runtime/sync/tests/test_sync_authority_attacks.py -q
"""
from __future__ import annotations

import base64
import uuid

import pytest

from commercial_runtime.sync import authority
from commercial_runtime.sync.tests._signing import install_main, admin_claim_event
from commercial_runtime.sync.tests.test_sync_authority_rules import Shop, COMPANY, OWNER, CASHIER


@pytest.fixture
def shop(tmp_path, monkeypatch):
    return Shop(tmp_path, monkeypatch)


def impersonator(attacker, as_dev):
    """The insider's OWN key and OWN Owner-signed lease, but `_auth.dev` naming
    somebody else. Needs nothing the insider does not hold."""
    key = attacker.key

    def sign_fn(data: bytes) -> str:
        return base64.b64encode(key.sign(data)).decode("ascii")

    return authority.LocalSigner(licence=attacker.signer.licence, inst=attacker.inst, dev=as_dev,
                                 pk_b64=attacker.signer.pk_b64, lease=attacker.signer.lease,
                                 sign_fn=sign_fn)


def apply_with(shop, ev, local_signer):
    ev = authority.sign_event(ev, local_signer)
    conn = shop.get_conn()
    try:
        r = shop.svc._apply_event(conn, ev, local_company_id=COMPANY)
        conn.commit()
        return r
    finally:
        conn.close()


def _record_main_installation(shop):
    """The receiver ALREADY KNOWS the main's real installation id and key
    fingerprint from the registry (devices.owner_installation_id /
    device_fingerprint) -- the binding code never consults them."""
    conn = shop.get_conn()
    conn.execute("UPDATE devices SET owner_installation_id=?, device_fingerprint=? WHERE id=?",
                 (shop.M.inst, shop.M.fingerprint, shop.M.dev))
    conn.commit()
    conn.close()


# ── V1: first-binding race -- the insider speaks AS THE MAIN ─────────────────

def test_V1_insider_cannot_speak_as_the_main_by_winning_the_first_binding(shop):
    """Upgrade window / fresh joiner / LAN-hub ordering: the receiver has not yet
    verified any signed event from the main (sync_signers holds only itself).
    The insider signs with ITS OWN key+lease but `_auth.dev` = the main's id."""
    _record_main_installation(shop)
    forged = impersonator(shop.A, shop.M.dev)
    ev = shop.user_event(OWNER, password_hash="ATTACKER-HASH")
    applied = apply_with(shop, ev, forged)
    assert shop.user(OWNER)["password_hash"] == f"HASH-{OWNER}", \
        f"owner password replaced by an insider posing as the main (applied={applied}, parked={shop.parked()})"


def test_V1b_after_the_race_the_real_main_is_locked_out(shop):
    forged = impersonator(shop.A, shop.M.dev)
    apply_with(shop, shop.user_event(CASHIER, display_name="x"), forged)   # any event binds
    ok = shop.apply(shop.user_event(OWNER, password_hash="REAL-MAIN-CHANGE"), shop.M)
    assert ok is True, f"the real main's change was refused: parked={shop.parked()}"


def test_V1c_insider_posing_as_main_hands_main_to_itself(shop):
    forged = impersonator(shop.A, shop.M.dev)
    ev = admin_claim_event(shop.A.dev, epoch=2)
    ev["payload"]["assigned_by_device_id"] = shop.M.dev
    apply_with(shop, ev, forged)
    assert shop.main() == (shop.M.dev, 1), f"main moved to {shop.main()} (parked={shop.parked()})"


# ── V2: own-echo exemption when the receiver has no signer of its own ────────

def test_V2_own_echo_exemption_is_not_granted_to_a_stranger(shop):
    """A receiver whose own signer cannot be built (e.g. a lease that names an
    older key, DPAPI unreadable) but which still has a licence, a trust store and
    a local device id. It never self-binds, so the insider binds the RECEIVER's
    own id and every rule is skipped as an 'own echo'."""
    env = shop.world.env(shop.R)
    shop.svc._authority_env_provider = lambda: authority.AuthorityEnv(
        trust_store=env.trust_store, own_licence=env.own_licence, signer=None, local_dev=shop.R.dev)
    forged = impersonator(shop.A, shop.R.dev)
    apply_with(shop, shop.user_event(OWNER, password_hash="ATTACKER-HASH"), forged)
    assert shop.user(OWNER)["password_hash"] == f"HASH-{OWNER}", "rules skipped as an own echo"


# ── V3: a device with TWO admin rows (placeholder + real owner) ───────────────

def test_V3_the_real_owner_on_a_two_admin_device_is_protected(shop):
    """The shipped join scenario: this device minted its own first-run admin
    (OWNER, seeded first) and the main then introduced the shop owner's real
    account. `_local_admin_uid` picks ONE admin (LIMIT 1); the other is treated
    as an ordinary user."""
    real = {"entity_type": "user", "entity_id": "real-owner", "event_type": "create", "payload": {
        "uid": "real-owner", "employee_id": "ADMIN-0007", "email": "real@x.com", "role": "admin",
        "status": "active", "require_password_change": 0, "language": "en", "password_hash": "REAL",
        "pin_hash": "R", "row_version": 1, "updated_at_utc": "2026-02-01T00:00:00+00:00",
        "session_version": 1}}
    assert shop.apply(real, shop.M) is True
    demote = {"entity_type": "user", "entity_id": "real-owner", "event_type": "update", "payload": dict(
        real["payload"], role="cashier", password_hash="ATTACKER-HASH", row_version=2)}
    shop.apply(demote, shop.A)
    row = shop.user("real-owner")
    assert (row["role"], row["password_hash"]) == ("admin", "REAL"), \
        f"real owner demoted / password replaced by a non-main: {row['role']}, {row['password_hash']}"


# ── V4: a 'harmless' owner edit carries an arbitrary row_version ──────────────

def test_V4_a_harmless_owner_edit_cannot_freeze_the_owners_credentials(shop):
    """Any member may send an owner event that leaves the critical fields as they
    are -- but its row_version is unconstrained. Sent with a huge version, every
    later legitimate change from the main (version local+1) loses the applier's
    `excluded.row_version > users.row_version` gate on this receiver."""
    harmless = shop.user_event(OWNER, display_name="x", row_version=10 ** 12)
    assert shop.apply(harmless, shop.A) is True          # allowed by design
    # The main changes the owner's password (it had not pulled the insider's
    # event yet, or refused it because ITS row already differs): local+1 = 2.
    legit = shop.user_event(OWNER, password_hash="NEW-AFTER-LEAK", row_version=2)
    shop.apply(legit, shop.M)
    assert shop.user(OWNER)["password_hash"] == "NEW-AFTER-LEAK", \
        "the main's owner password change was silently dropped on this receiver"


def test_V4b_the_harmless_bump_also_deletes_a_parked_legit_owner_change(shop):
    conn = shop.get_conn()
    conn.execute("DELETE FROM admin_device_assignment")
    conn.commit()
    conn.close()
    legit = shop.world.sign(shop.user_event(OWNER, password_hash="FROM-MAIN"), shop.M)
    assert shop.apply(legit) is False
    assert shop.parked("user") == ["main_unknown"]
    # insider: harmless owner edit, higher version (no main needed: nothing critical changes)
    assert shop.apply(shop.user_event(OWNER, display_name="x", row_version=50), shop.A) is True
    conn = shop.get_conn()
    install_main(conn, COMPANY, shop.M.dev)
    conn.close()
    shop.pull()
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN", \
        f"parked legit change lost (parked now={shop.parked()})"


# ── V5: quarantine upsert key ignores entity_type / signature ─────────────────

def test_V5_an_unsigned_copy_cannot_overwrite_a_parked_signed_event(shop):
    conn = shop.get_conn()
    conn.execute("DELETE FROM admin_device_assignment")
    conn.commit()
    conn.close()
    legit = shop.world.sign(shop.user_event(OWNER, password_hash="FROM-MAIN"), shop.M)
    assert shop.apply(legit) is False
    junk = {"entity_type": "user", "entity_id": OWNER, "event_type": "update",
            "payload": {"uid": OWNER, "junk": True}}
    shop.apply(junk)                                       # unsigned -> parks, same key
    conn = shop.get_conn()
    install_main(conn, COMPANY, shop.M.dev)
    conn.close()
    shop.pull()
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN", \
        f"the parked, signed main event was overwritten by an unsigned copy (parked={shop.parked()})"


def test_V6_a_forgery_signal_cannot_be_rewritten_into_a_supersedable_reason(shop):
    forged = shop.user_event(OWNER, password_hash="ATTACKER-HASH")
    assert shop.apply(forged, shop.A) is False
    assert shop.parked("user") == ["owner_change_needs_main"]
    shop.apply({"entity_type": "user", "entity_id": OWNER, "event_type": "update",
                "payload": {"uid": OWNER}})                # unsigned, same (entity_id, event_type)
    assert shop.parked("user") == ["owner_change_needs_main"], \
        f"evidence rewritten: {shop.parked('user')}"


# ── V7: first writer of a not-yet-known uid ──────────────────────────────────

def test_V7_insider_cannot_pre_create_the_real_owners_uid_on_a_joining_device(shop):
    """A joining device with its own placeholder admin (OWNER here) has not yet
    received the shop owner's account. The insider creates that uid first, as a
    cashier with its own password and a huge row_version; the main's real
    owner row then loses the version gate for ever."""
    squat = {"entity_type": "user", "entity_id": "real-owner", "event_type": "create", "payload": {
        "uid": "real-owner", "employee_id": "EMP-0777", "email": "real@x.com", "role": "cashier",
        "status": "active", "require_password_change": 0, "language": "en",
        "password_hash": "ATTACKER-HASH", "pin_hash": "A", "row_version": 10 ** 9,
        "updated_at_utc": "2026-02-01T00:00:00+00:00", "session_version": 1}}
    shop.apply(squat, shop.A)
    real = {"entity_type": "user", "entity_id": "real-owner", "event_type": "create", "payload": dict(
        squat["payload"], employee_id="ADMIN-0007", role="admin", password_hash="REAL", row_version=1)}
    shop.apply(real, shop.M)
    row = shop.user("real-owner")
    assert (row["role"], row["password_hash"]) == ("admin", "REAL"), \
        f"real owner arrives as {row['role']} with password {row['password_hash']}"


# ── V8: role spelling ────────────────────────────────────────────────────────

def test_V8_a_differently_spelled_admin_role_is_judged_as_admin(shop):
    """normalize_role() (strip+lower) is what capability code reads; the rule
    compares the raw string to 'admin'."""
    ev = shop.user_event(CASHIER, role="Admin")
    shop.apply(ev, shop.A)
    assert shop.user(CASHIER)["role"] == "cashier", "role 'Admin' minted by a non-main"


# ── V9: "the main is alive" evidence can be replayed ─────────────────────────

def _old_main_signed_event(shop):
    """Any authority event the main signed in the past and the insider received:
    here the main's harmless edit of an ordinary user, captured off the relay."""
    return shop.world.sign(shop.user_event(CASHIER, display_name="old edit by main"), shop.M)


def test_V9_control_takeover_allowed_after_real_silence(shop):
    from datetime import datetime, timedelta, timezone
    from commercial_runtime.sync.tests.test_sync_authority_rules import _takeover
    shop.set_last_seen(shop.M, datetime.now(timezone.utc) - timedelta(days=8))
    assert shop.apply(_takeover(shop.B.dev), shop.B) is True


def test_V9_replaying_an_old_main_event_cannot_fake_that_the_main_is_alive(shop):
    """The main has been gone 8 days (stolen / dead). The owner wants till B to
    take over. The insider replays one OLD main-signed event; the receiver
    refreshes the main's last-seen on its own clock and refuses the takeover --
    for as long as the insider keeps replaying.

    CORRECTED SETUP (the only deviation from V's file; the fix-round plan, section
    6). V's original replays an event THIS RECEIVER NEVER SAW, signed under a
    lease issued that very moment. That is indistinguishable from a genuine
    fresh main event (fresh Owner lease + unseen signature), so no rule can pass
    it while still accepting real liveness: V's original stays RED on the fixed
    tree on purpose. A REPLAY is an event the receiver already counted: the
    original delivery happens first, then the main goes quiet, then the same
    signed event is replayed. That is what this test does."""
    from datetime import datetime, timedelta, timezone
    from commercial_runtime.sync.tests.test_sync_authority_rules import _takeover
    old = _old_main_signed_event(shop)
    shop.apply(old)                                   # the original delivery
    shop.set_last_seen(shop.M, datetime.now(timezone.utc) - timedelta(days=8))
    shop.apply(old)                                   # replay, no re-signing
    assert shop.apply(_takeover(shop.B.dev), shop.B) is True, \
        f"takeover refused after a replay (parked={shop.parked()})"


def test_V9b_a_parked_main_event_retried_each_pull_does_not_keep_the_main_alive(shop):
    """No insider at all: one main-signed event is parked for an ordinary reason
    (a permission for a user this receiver does not have yet). Every pull retries
    it, re-verifies the signature and refreshes the main's last-seen."""
    from datetime import datetime, timedelta, timezone
    from commercial_runtime.sync.tests.test_sync_authority_rules import _takeover
    perm = {"entity_type": "user_permission", "entity_id": "p-1", "event_type": "update",
            "payload": {"user_uid": "not-here-yet", "subsystem": "retail.sell", "access_level": "full"}}
    assert shop.apply(perm, shop.M) is False
    print("parked:", shop.parked())
    shop.set_last_seen(shop.M, datetime.now(timezone.utc) - timedelta(days=8))
    shop.pull()                                        # an ordinary pull: retries the parked row
    assert shop.apply(_takeover(shop.B.dev), shop.B) is True, \
        f"a retried old event kept a dead main alive (parked={shop.parked()})"


# ═══ fix-round additions (HEAD-compatible: same helpers as the ports above) ════
# Each pins a part of the fix that V's own tests do not reach. They are RED on
# fafaf7cc for the reason in their docstrings and GREEN on the fixed tree.

def _drop_main(shop):
    conn = shop.get_conn()
    conn.execute("DELETE FROM admin_device_assignment")
    conn.commit()
    conn.close()


def _put_main(shop):
    conn = shop.get_conn()
    install_main(conn, COMPANY, shop.M.dev)
    conn.close()


def test_a_non_mains_harmless_owner_edit_never_writes_the_employee_code(shop):
    """Finding #3 + the employee_id note. The owner's employee code is a per-device
    label, so a non-main's edit that carries a different one is not 'critical' --
    but the harmless path must not WRITE it either: the name lands, the code does
    not. (RED at fafaf7cc: employee_id was critical, so the edit parked.)"""
    ev = shop.user_event(OWNER, employee_id="ADMIN-0099", display_name="Renamed By A")
    assert shop.apply(ev, shop.A) is True
    row = shop.user(OWNER)
    assert row["display_name"] == "Renamed By A"
    assert row["employee_id"] == "ADMIN-0001", f"employee code written by a non-main: {row['employee_id']}"
    assert row["row_version"] == 1, "a non-main moved the owner row's version"


def test_V5b_an_attested_insiders_signed_junk_cannot_displace_a_parked_legit_change(shop):
    """Finding #7. The main's owner change is parked `main_unknown`; an insider
    (a verified member) parks a signed event of its own for the same uid. With the
    old (entity_id, event_type) key the insider's row REPLACED the legitimate one
    and the main's change was lost for good."""
    _drop_main(shop)
    legit = shop.world.sign(shop.user_event(OWNER, password_hash="FROM-MAIN"), shop.M)
    assert shop.apply(legit) is False
    junk = shop.user_event(OWNER, password_hash="ATTACKER-HASH", row_version=9)
    shop.apply(junk, shop.A)
    _put_main(shop)
    shop.pull()
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN", \
        f"the legitimate parked change was displaced (parked={shop.parked()})"


def test_V6b_a_forged_parked_event_stays_evidence_and_never_clears_by_a_later_main_event(shop):
    """Finding #8. A forged owner change is parked `owner_change_needs_main` (a
    forgery signal). Deleting the assignment makes the next pull re-park it for a
    DIFFERENT reason (`main_unknown`, which a later signed event may clear) -- the
    stored evidence must not be rewritten into it."""
    forged = shop.user_event(OWNER, password_hash="ATTACKER-HASH", row_version=9)
    assert shop.apply(forged, shop.A) is False
    assert shop.parked("user") == ["owner_change_needs_main"]
    _drop_main(shop)
    shop.pull()                                           # the retry re-parks it: main is unknown now
    assert shop.parked("user") == ["owner_change_needs_main"], \
        f"evidence rewritten to {shop.parked('user')}"
    _put_main(shop)
    assert shop.apply(shop.user_event(OWNER, display_name="main edit", row_version=20), shop.M) is True
    assert shop.parked("user") == ["owner_change_needs_main"], \
        f"the forgery signal was cleared by a later main event: {shop.parked('user')}"
    assert shop.user(OWNER)["password_hash"] == f"HASH-{OWNER}"


def test_two_signed_events_for_one_entity_each_keep_their_own_parked_row(shop):
    """Finding #7: the retry deletes by the FULL key. Two signed events for one
    owner, both parked while the main is unknown; the main's resolves and the
    insider's (a higher version, so no supersede) stays parked."""
    _drop_main(shop)
    legit = shop.world.sign(shop.user_event(OWNER, display_name="from main", password_hash="FROM-MAIN"), shop.M)
    shop.apply(legit)
    shop.apply(shop.user_event(OWNER, password_hash="ATTACKER-HASH", row_version=7), shop.A)
    assert len(shop.parked("user")) == 2, f"expected two rows, got {shop.parked('user')}"
    _put_main(shop)
    shop.pull()
    assert shop.user(OWNER)["password_hash"] == "FROM-MAIN"
    assert shop.parked("user") == ["owner_change_needs_main"], shop.parked("user")


def test_own_echo_exemption_needs_this_devices_own_signer(shop):
    """Finding #5, the other half of V2. The receiver's env carries a local device
    id but NO signer. An event signed by that very identity (so verification and
    binding are fine) that changes the owner's password -- R is not the main --
    must be judged by the rules, not waved through as 'my own echo'. (At fafaf7cc
    `local_dev == signer.dev` alone exempted it and the password changed.)"""
    env = shop.world.env(shop.R)
    shop.svc._authority_env_provider = lambda: authority.AuthorityEnv(
        trust_store=env.trust_store, own_licence=env.own_licence, signer=None, local_dev=shop.R.dev)
    ev = shop.user_event(OWNER, password_hash="ECHO-HASH")
    assert shop.apply(ev, shop.R) is False
    assert shop.parked("user") == ["owner_change_needs_main"]
    assert shop.user(OWNER)["password_hash"] == f"HASH-{OWNER}"


def test_a_main_event_under_a_20_day_old_lease_does_not_prove_the_main_is_alive(shop):
    """Finding #6, the lease-age window. The main's last-seen is 8 days old. An
    UNSEEN event signed by the main's own key but under a lease issued 20 days ago
    is not evidence of life (a replayed or stashed lease), so a takeover by B is
    still allowed afterwards."""
    from datetime import datetime, timedelta, timezone
    from commercial_runtime.sync.tests.test_sync_authority_rules import _takeover
    stale_main = shop.world.new_device(shop.M.dev, inst=shop.M.inst, key=shop.M.key,
                                       issued_at=datetime.now(timezone.utc) - timedelta(days=20))
    shop.set_last_seen(shop.M, datetime.now(timezone.utc) - timedelta(days=8))
    ev = shop.user_event(CASHIER, display_name="edit under an old lease")
    assert shop.apply(ev, stale_main) is True
    assert shop.apply(_takeover(shop.B.dev), shop.B) is True, \
        f"an old-lease event kept a dead main alive (parked={shop.parked()})"


def test_a_parked_main_event_retried_after_the_licence_arrives_does_not_prove_life(shop):
    """Finding #6, retry isolation. The receiver has no licence of its own yet, so
    a main-signed event parks `no_local_licence` (a state of THIS device, nothing
    to do with the main). The licence arrives, the next pull's retry verifies and
    applies it: that retry is not proof the main is alive, so a takeover (main
    silent for 8 days) is still allowed."""
    from datetime import datetime, timedelta, timezone
    from commercial_runtime.sync.tests.test_sync_authority_rules import _takeover
    good_env = shop.svc._authority_env_provider
    env = good_env()
    shop.svc._authority_env_provider = lambda: authority.AuthorityEnv(
        trust_store=env.trust_store, own_licence=None, signer=env.signer, local_dev=env.local_dev)
    ev = shop.world.sign(shop.user_event(CASHIER, display_name="edit by main"), shop.M)
    assert shop.apply(ev) is False
    assert shop.parked("user") == ["no_local_licence"]
    shop.set_last_seen(shop.M, datetime.now(timezone.utc) - timedelta(days=8))
    shop.svc._authority_env_provider = good_env          # the licence arrives
    shop.pull()                                           # the retry applies it
    assert shop.user(CASHIER)["display_name"] == "edit by main"
    assert shop.apply(_takeover(shop.B.dev), shop.B) is True, \
        f"a retried event refreshed the main's last-seen (parked={shop.parked()})"
