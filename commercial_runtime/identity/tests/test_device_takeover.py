"""The guarded local takeover of the main-computer role (plan option (b)).

THIS FILE GOES WITH device_takeover.py: if the owner picks Owner-CC reset
(option a) both are deleted together, along with the marked block at the end of
device_routes.py.

WHY THESE ASSERTIONS. A takeover lets ONE till take the role away from the real
main, so the dangerous bugs are the ones that let it happen when it should not
(a live main overridden, a cashier with no password, a typo counted as a yes)
AND the ones that make the escape hatch useless (a dead main that can never be
replaced). Every deny test therefore has its allow twin, and every refusal is
asserted to have WRITTEN NOTHING (assignment row, flag, outbox) -- a 409 alone
cannot tell "the guard ran" from "something else refused" (ENGINEERING.md 1),
so each refusal test reaches a code only that guard produces.

Run (one file per process):
    python -m pytest commercial_runtime/identity/tests/test_device_takeover.py -q
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from commercial_runtime.identity import (
    company_rebind, device_context, device_registry, device_routes, device_takeover, mt_auth,
    onboarding_routes, registry_db,
)
from commercial_runtime.identity.device_registry import (
    apply_admin_assignment, apply_identity_device_schema, claim_admin_device_with_assignment,
    get_assignment, upsert_local_device,
)
from commercial_runtime.identity.device_routes import device_bp
from commercial_runtime.identity.device_sync_schema import apply_device_sync_schema
from commercial_runtime.identity.device_takeover import (
    TAKEOVER_CONFIRM_PHRASE, TakeoverRefused, check_takeover_allowed, confirm_phrase_ok,
    take_over_main,
)
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.licensing_contracts import flask_guard
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRecord

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)
CO = "company-1"


def _ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()


# ── the typed phrase ─────────────────────────────────────────────────────────

def test_the_phrase_must_be_exact_after_trimming_the_ends():
    assert confirm_phrase_ok("TAKE OVER") is True
    assert confirm_phrase_ok("  TAKE OVER \n") is True
    for bad in ("take over", "Take Over", "TAKE  OVER", "TAKEOVER", "TAKE OVER now", "", "   ", None, 5, ["TAKE OVER"]):
        assert confirm_phrase_ok(bad) is False, repr(bad)


def test_the_seven_day_rule_and_its_sentence_agree():
    """The message is a fixed literal (it is looked up whole in the locale
    files), so the number inside it cannot follow the constant by itself."""
    assert device_takeover.TAKEOVER_MIN_UNSEEN_DAYS == 7
    assert str(device_takeover.TAKEOVER_MIN_UNSEEN_DAYS) in device_takeover.TAKEOVER_SEEN_RECENTLY_MESSAGE
    assert TAKEOVER_CONFIRM_PHRASE in device_takeover.TAKEOVER_CONFIRM_MESSAGE


# ── the rule, on a bare registry ─────────────────────────────────────────────

@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(str(tmp_path / "registry.db"))
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE sync_outbox (id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT,"
              " event_type TEXT, payload TEXT, created_at TEXT)")
    apply_identity_device_schema(c)
    apply_device_sync_schema(c)
    c.commit()
    yield c
    c.close()


def _settle_me(conn):
    """upsert_local_device stamps 'me' with the REAL clock, but these tests judge
    silence against the fixed NOW. Since the M1 clock guard (2026-09-29) a
    takeover is refused when NOW is earlier than the newest stamp on record, so
    'me' must be dated just before NOW like a computer that was online a minute
    ago. This changes nothing the tests assert about the MAIN's silence."""
    stamp = (NOW - timedelta(minutes=1)).isoformat()
    conn.execute("UPDATE devices SET last_seen_at=?, first_seen_at=?, updated_at_utc=? WHERE id='me'",
                 (stamp, stamp, stamp))
    conn.commit()


def _shop(conn, *, main_seen, main_updated=None, assigned_at=None):
    """A shop whose main is 'boss' and whose other computer is 'me'. Times are
    written straight into the rows so the test controls what 'silent' means."""
    upsert_local_device(conn, "boss", CO, platform="WINDOWS")
    upsert_local_device(conn, "me", CO, platform="WINDOWS")
    claim_admin_device_with_assignment(conn, CO, "boss")
    conn.execute("UPDATE devices SET last_seen_at=?, first_seen_at=?, updated_at_utc=? WHERE id='boss'",
                 (main_seen, main_seen, main_updated or main_seen))
    conn.execute("UPDATE admin_device_assignment SET assigned_at_utc=? WHERE company_id=?",
                 (assigned_at or main_seen, CO))
    conn.commit()
    _settle_me(conn)


def _outbox(conn):
    return conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]


def _code(conn, **kw):
    with pytest.raises(TakeoverRefused) as ei:
        check_takeover_allowed(conn, CO, "me", now=NOW, **kw)
    return ei.value.code


def test_a_main_silent_for_seven_days_may_be_replaced_and_a_shorter_silence_may_not(conn):
    _shop(conn, main_seen=_ago(days=7, seconds=1))
    check_takeover_allowed(conn, CO, "me", now=NOW)                      # ALLOW half
    conn.execute("UPDATE devices SET last_seen_at=?, first_seen_at=?, updated_at_utc=? WHERE id='boss'",
                 (_ago(days=6, hours=23), _ago(days=6, hours=23), _ago(days=6, hours=23)))
    conn.execute("UPDATE admin_device_assignment SET assigned_at_utc=?", (_ago(days=6, hours=23),))
    conn.commit()
    assert _code(conn) == "TAKEOVER_MAIN_SEEN_RECENTLY"                  # DENY half


def test_exactly_seven_days_is_allowed(conn):
    _shop(conn, main_seen=_ago(days=7))
    check_takeover_allowed(conn, CO, "me", now=NOW)


def test_any_recent_sign_of_life_from_the_main_refuses(conn):
    """last_seen is old but the row was touched yesterday (a rename, an
    announce): the latest of every timestamp counts."""
    _shop(conn, main_seen=_ago(days=30), main_updated=_ago(days=1))
    assert _code(conn) == "TAKEOVER_MAIN_SEEN_RECENTLY"


def test_a_role_handed_over_recently_counts_as_seen(conn):
    _shop(conn, main_seen=_ago(days=30), assigned_at=_ago(days=2))
    assert _code(conn) == "TAKEOVER_MAIN_SEEN_RECENTLY"


def test_a_main_whose_clock_runs_ahead_is_seen_recently_not_long_silent(conn):
    """A main a few minutes ahead of us is 'heard just now' (a NEGATIVE gap must
    refuse like any recent sighting, never read as long silence). One 40 days
    ahead is refused earlier, by the M1 clock guard (our clock looks behind the
    records) -- still a refusal, with the more useful sentence."""
    _shop(conn, main_seen=(NOW + timedelta(minutes=5)).isoformat())
    assert _code(conn) == "TAKEOVER_MAIN_SEEN_RECENTLY"
    conn.execute("UPDATE devices SET last_seen_at=?, first_seen_at=?, updated_at_utc=? WHERE id='boss'",
                 ((NOW + timedelta(days=40)).isoformat(),) * 3)
    conn.commit()
    assert _code(conn) == "TAKEOVER_CLOCK_BEHIND"


def _forge_assignment(conn, device_id, at):
    """An assignment row naming `device_id`, written straight to the table: the
    apply path (rightly) refuses to store one naming a device of another
    company, but a poisoned or legacy registry can still hold one."""
    conn.execute("DELETE FROM admin_device_assignment WHERE company_id=?", (CO,))
    conn.execute("INSERT INTO admin_device_assignment (company_id, device_id, epoch, assigned_at_utc)"
                 " VALUES (?,?,3,?)", (CO, device_id, at))
    conn.commit()


def test_a_ghost_main_is_judged_by_the_assignment_time_alone(conn):
    """2026-09-29 review, ghost main. The assignment names a computer this one
    never heard of. It used to answer 'unknown' and refuse FOREVER, so a single
    orphaned or forged assignment made the shop unrecoverable. Now the
    assignment time is the only evidence: silent for 7 days means a takeover is
    allowed, 6 days 23 hours means it is not (the M5b boundary). The CLAIM door
    stays shut -- that is asserted in test_fix_round_devices.py."""
    upsert_local_device(conn, "me", CO)
    _settle_me(conn)
    apply_admin_assignment(conn, CO, {"device_id": "ghost", "epoch": 3,
                                      "assigned_at_utc": _ago(days=6, hours=23)}, now=NOW)
    conn.commit()
    assert _code(conn) == "TAKEOVER_MAIN_SEEN_RECENTLY"                   # DENY half, M5b
    conn.execute("UPDATE admin_device_assignment SET assigned_at_utc=?", (_ago(days=90),))
    conn.commit()
    check_takeover_allowed(conn, CO, "me", now=NOW)                       # ALLOW half


def test_a_main_row_that_belongs_to_another_shop_is_not_evidence(conn):
    """The assignment names a device id, but the row under that id is filed
    under a DIFFERENT company (a leftover, or a clash) and was heard from
    yesterday. Its stamps must not be read as 'our main is alive' -- that would
    let one foreign row veto a takeover forever. Only the assignment time (90
    days) counts, so the takeover is allowed. Guards the company check in
    main_last_heard_from: with it removed the foreign row's fresh stamp turns
    this into TAKEOVER_MAIN_SEEN_RECENTLY."""
    upsert_local_device(conn, "me", CO)
    _settle_me(conn)
    upsert_local_device(conn, "ghost", "some-other-company", platform="WINDOWS")
    conn.execute("UPDATE devices SET last_seen_at=?, first_seen_at=?, updated_at_utc=? WHERE id='ghost'",
                 (_ago(days=1), _ago(days=1), _ago(days=1)))
    _forge_assignment(conn, "ghost", _ago(days=90))
    check_takeover_allowed(conn, CO, "me", now=NOW)


def test_a_revoked_main_row_is_not_evidence_either(conn):
    """A removed computer's fresh stamps are not a sign the main is alive (a
    revoke of the main is refused everywhere now, so such a row arrived out of
    order or from a forger). Same shape as the foreign-shop row above."""
    _shop(conn, main_seen=_ago(days=90))
    conn.execute("UPDATE devices SET status='revoked', last_seen_at=?, updated_at_utc=? WHERE id='boss'",
                 (_ago(days=1), _ago(days=1)))
    conn.commit()
    check_takeover_allowed(conn, CO, "me", now=NOW)


def test_a_main_with_unreadable_timestamps_is_refused_not_assumed_dead(conn):
    _shop(conn, main_seen="not a date")
    conn.execute("UPDATE devices SET updated_at_utc='junk', first_seen_at='junk' WHERE id='boss'")
    conn.execute("UPDATE admin_device_assignment SET assigned_at_utc='junk'")
    conn.commit()
    assert _code(conn) == "TAKEOVER_MAIN_UNKNOWN"


def test_no_main_at_all_points_at_the_ordinary_claim(conn):
    upsert_local_device(conn, "me", CO)
    assert _code(conn) == "TAKEOVER_NO_MAIN"


def test_the_main_cannot_take_over_from_itself(conn):
    _shop(conn, main_seen=_ago(days=30))
    with pytest.raises(TakeoverRefused) as ei:
        check_takeover_allowed(conn, CO, "boss", now=NOW)
    assert ei.value.code == "TAKEOVER_ALREADY_MAIN"


def test_a_removed_computer_or_one_of_another_shop_cannot_take_over(conn):
    _shop(conn, main_seen=_ago(days=30))
    conn.execute("UPDATE devices SET status='revoked' WHERE id='me'")
    conn.commit()
    assert _code(conn) == "TAKEOVER_DEVICE_NOT_ACTIVE"
    upsert_local_device(conn, "stranger", "company-2")
    with pytest.raises(TakeoverRefused) as ei:
        check_takeover_allowed(conn, CO, "stranger", now=NOW)
    assert ei.value.code == "TAKEOVER_DEVICE_NOT_ACTIVE"


def test_take_over_moves_the_role_with_epoch_plus_one_and_one_event(conn):
    _shop(conn, main_seen=_ago(days=30))
    before_events = _outbox(conn)
    row = take_over_main(conn, CO, "me", by_user_uid="u-1", now=NOW)
    assert row["id"] == "me" and row["is_admin_device"] == 1
    a = get_assignment(conn, CO)
    assert (a["device_id"], a["epoch"], a["assigned_by_device_id"], a["assigned_by_user_uid"]) == (
        "me", 2, "me", "u-1")
    flagged = [r["id"] for r in conn.execute("SELECT id FROM devices WHERE is_admin_device=1")]
    assert flagged == ["me"]
    new = conn.execute("SELECT event_type, payload FROM sync_outbox WHERE entity_type='admin_device' "
                       "ORDER BY rowid DESC LIMIT 1").fetchone()
    assert _outbox(conn) == before_events + 1
    assert new["event_type"] == "update" and json.loads(new["payload"])["epoch"] == 2


def test_the_old_main_coming_back_loses_by_epoch(conn):
    """The whole recovery story: the old main's own (lower-epoch) claim,
    arriving later, cannot take the role back."""
    _shop(conn, main_seen=_ago(days=30))
    old = get_assignment(conn, CO)
    take_over_main(conn, CO, "me", now=NOW)
    assert apply_admin_assignment(conn, CO, {"device_id": "boss", "epoch": old["epoch"],
                                             "assigned_at_utc": old["assigned_at_utc"]}) is False
    assert get_assignment(conn, CO)["device_id"] == "me"


def test_a_refused_takeover_writes_nothing(conn):
    _shop(conn, main_seen=_ago(days=1))
    snap = (_outbox(conn), dict(get_assignment(conn, CO)),
            [r["id"] for r in conn.execute("SELECT id FROM devices WHERE is_admin_device=1")])
    with pytest.raises(TakeoverRefused):
        take_over_main(conn, CO, "me", now=NOW)
    assert not conn.in_transaction
    assert snap == (_outbox(conn), dict(get_assignment(conn, CO)),
                    [r["id"] for r in conn.execute("SELECT id FROM devices WHERE is_admin_device=1")])


# ── the route, on the real registry ──────────────────────────────────────────

OWNER_EMAIL = "owner@test.local"
OWNER_PW = "OwnerPW11"


@pytest.fixture(autouse=True)
def _reset_process_cache():
    device_context._cached_device = None
    yield
    device_context._cached_device = None


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(path))
    monkeypatch.setattr(registry_db, "_db_dir", str(tmp_path))
    registry_db.init_registry_db()
    return path


@pytest.fixture
def app(db_path, tmp_path, monkeypatch):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(onboarding_routes, "get_conn", _get_conn)
    monkeypatch.setattr(device_routes, "get_conn", _get_conn)
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))
    monkeypatch.setattr(flask_guard.LicenseStateRepository, "__init__", lambda self, db_path: None)
    monkeypatch.setattr(
        flask_guard.LicenseStateRepository, "load",
        lambda self: LicenseStateRecord(
            licensing_schema_version=1, product_code="AURA_TEST", platform="WINDOWS",
            current_state="ACTIVE_ONLINE"))
    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    flask_app.register_blueprint(device_bp)
    return flask_app


@pytest.fixture
def admin(app):
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': OWNER_EMAIL, 'password': OWNER_PW})
    assert r.status_code == 200, r.get_json()
    return client


def _db(db_path):
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def _company(db_path):
    c = _db(db_path)
    try:
        return c.execute("SELECT company_id FROM users WHERE LOWER(email)=?", (OWNER_EMAIL,)).fetchone()[0]
    finally:
        c.close()


def _shop_on_disk(db_path, *, main_seen, platform="WINDOWS"):
    """This computer ('me' = the real local device id) plus a main called
    'boss' last heard from at `main_seen`. Wipes whatever create-admin set up
    so the scenario is exactly what the test names."""
    co = _company(db_path)
    c = _db(db_path)
    try:
        c.execute("DELETE FROM admin_device_assignment")
        c.execute("DELETE FROM devices")
        c.execute("DELETE FROM sync_outbox WHERE entity_type IN ('device','admin_device')")
        me = device_context.local_device_uuid()
        upsert_local_device(c, me, co, platform=platform)
        upsert_local_device(c, "boss", co, platform="WINDOWS")
        claim_admin_device_with_assignment(c, co, "boss")
        c.execute("UPDATE devices SET last_seen_at=?, first_seen_at=?, updated_at_utc=? WHERE id='boss'",
                  (main_seen, main_seen, main_seen))
        c.execute("UPDATE admin_device_assignment SET assigned_at_utc=?", (main_seen,))
        c.commit()
    finally:
        c.close()
    device_context._cached_device = None
    return co, me


def _state(db_path):
    c = _db(db_path)
    try:
        a = get_assignment(c, _company(db_path))
        return (dict(a) if a else None,
                [r["id"] for r in c.execute("SELECT id FROM devices WHERE is_admin_device=1")],
                c.execute("SELECT COUNT(*) FROM sync_outbox WHERE entity_type='admin_device'").fetchone()[0])
    finally:
        c.close()


def _failed_count(db_path):
    c = _db(db_path)
    try:
        return c.execute("SELECT failed_login_count FROM users WHERE LOWER(email)=?",
                         (OWNER_EMAIL,)).fetchone()[0] or 0
    finally:
        c.close()


def _takeover_failures(db_path):
    c = _db(db_path)
    try:
        row = c.execute("SELECT SUM(failed_count) FROM takeover_lockout").fetchone()
        return row[0] or 0
    finally:
        c.close()


def _take(client, password=OWNER_PW, confirm=TAKEOVER_CONFIRM_PHRASE, **extra):
    return client.post("/api/devices/me/take-over",
                       json={"password": password, "confirm": confirm, **extra})


LONG_AGO = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
YESTERDAY = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()


def test_route_happy_path_moves_the_role_and_audits_it(admin, db_path):
    co, me = _shop_on_disk(db_path, main_seen=LONG_AGO)
    before_epoch = _state(db_path)[0]["epoch"]
    r = _take(admin)
    assert r.status_code == 200, r.get_json()
    a, flagged, events = _state(db_path)
    assert (a["device_id"], a["epoch"]) == (me, before_epoch + 1)
    assert flagged == [me] and r.get_json()["device"]["id"] == me
    assert events == 2                                  # the boss's claim + the takeover
    c = _db(db_path)
    try:
        row = c.execute("SELECT new_value_json FROM audit_logs WHERE action='TAKEOVER_MAIN_DEVICE'").fetchone()
    finally:
        c.close()
    assert json.loads(row[0]) == {"from_device_id": "boss", "to_device_id": me}


def test_route_refuses_a_live_main_and_writes_nothing(admin, db_path):
    _shop_on_disk(db_path, main_seen=YESTERDAY)
    before = _state(db_path)
    r = _take(admin)
    assert r.status_code == 409 and r.get_json()["code"] == "TAKEOVER_MAIN_SEEN_RECENTLY"
    assert _state(db_path) == before


def test_route_wrong_phrase_is_refused_before_the_password_is_even_tried(admin, db_path):
    _shop_on_disk(db_path, main_seen=LONG_AGO)
    before = _state(db_path)
    for bad in ("take over", "", None, "TAKE OVE"):
        r = _take(admin, confirm=bad)
        assert r.status_code == 400 and r.get_json()["code"] == "TAKEOVER_CONFIRM_REQUIRED", bad
    assert _failed_count(db_path) == 0          # a typo never burns a login attempt
    assert _state(db_path) == before


def test_route_wrong_password_is_refused_counted_and_writes_nothing(admin, db_path):
    _shop_on_disk(db_path, main_seen=LONG_AGO)
    before = _state(db_path)
    r = _take(admin, password="not-the-password")
    assert r.status_code == 403 and r.get_json()["code"] == "TAKEOVER_PASSWORD_INCORRECT"
    # M2 (2026-09-29): the takeover has its OWN counter. A wrong guess here must
    # NOT count against the owner's login (it used to, so a cashier at an
    # unlocked till could lock the owner out of signing in).
    assert _failed_count(db_path) == 0
    assert _takeover_failures(db_path) == 1
    assert _state(db_path) == before
    # A missing password is a miss too. Only TWO misses before the allow twin:
    # the third would (correctly) start the takeover lock, which the next test
    # covers. "" is exercised at unit level in test_fix_round_devices.py.
    assert _take(admin, password=None).get_json()["code"] == "TAKEOVER_PASSWORD_INCORRECT"
    assert _takeover_failures(db_path) == 2
    assert _state(db_path) == before
    # allow twin: the right password on the same shop goes through.
    assert _take(admin).status_code == 200


def test_route_repeated_wrong_passwords_lock_the_account_and_the_right_one_then_fails_too(admin, db_path):
    _shop_on_disk(db_path, main_seen=LONG_AGO)
    before = _state(db_path)
    for _ in range(device_takeover.TAKEOVER_FREE_ATTEMPTS):
        _take(admin, password="wrong")
    r = _take(admin, password=OWNER_PW)
    assert r.status_code == 429 and r.get_json()["code"] == "TAKEOVER_LOCKED"
    assert _state(db_path) == before
    assert _failed_count(db_path) == 0          # the owner can still sign in


def test_route_password_of_someone_else_than_the_signed_in_user_does_not_count(admin, db_path):
    """The owner's password is right, but the SESSION belongs to another
    account: consent must come from the person at the keyboard."""
    _shop_on_disk(db_path, main_seen=LONG_AGO)
    r = admin.post('/api/admin/employees', json={
        'name': 'Emp', 'email': 'emp@test.local', 'password': 'EmpPW1234', 'role': 'cashier'})
    assert r.status_code == 200, r.get_json()
    c = _db(db_path)
    try:
        emp_id = c.execute("SELECT id FROM users WHERE email='emp@test.local'").fetchone()[0]
    finally:
        c.close()
    before = _state(db_path)
    with admin.session_transaction() as s:
        s["mt_user_id"] = emp_id                 # still role admin, still owner@ e-mail
    res = _take(admin, password=OWNER_PW)
    assert res.status_code == 403 and res.get_json()["code"] == "TAKEOVER_PASSWORD_INCORRECT"
    assert _state(db_path) == before


def test_route_needs_a_company_admin(admin, db_path):
    _shop_on_disk(db_path, main_seen=LONG_AGO)
    before = _state(db_path)
    with admin.session_transaction() as s:
        s["mt_role"] = "manager"
    r = _take(admin)
    assert r.status_code == 403 and r.get_json()["code"] == "ADMIN_ROLE_REQUIRED"
    assert _state(db_path) == before
    with admin.session_transaction() as s:
        s["mt_role"] = "admin"
        s["is_demo_mode"] = True
    assert _take(admin).get_json()["code"] == "ADMIN_ROLE_REQUIRED"
    assert _state(db_path) == before


def test_route_refuses_a_non_windows_computer_and_a_clashing_one(admin, db_path):
    _, me = _shop_on_disk(db_path, main_seen=LONG_AGO, platform="ANDROID")
    before = _state(db_path)
    r = _take(admin)
    assert r.status_code == 409 and r.get_json()["code"] == "MAIN_MUST_BE_WINDOWS"
    assert _state(db_path) == before

    _shop_on_disk(db_path, main_seen=LONG_AGO)
    before = _state(db_path)
    c = _db(db_path)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS sync_apply_quarantine (entity_id TEXT, entity_type TEXT,"
                  " event_type TEXT, payload TEXT, reason TEXT, detail TEXT, quarantined_at TEXT,"
                  " PRIMARY KEY (entity_id, event_type))")
        c.execute("INSERT OR REPLACE INTO sync_apply_quarantine VALUES (?,'device','update','{}',"
                  "'device_identity_clash','','2026-01-01')", (me,))
        c.commit()
    finally:
        c.close()
    r2 = _take(admin)
    assert r2.status_code == 409 and r2.get_json()["code"] == "DEVICE_IDENTITY_CLASH"
    assert _state(db_path) == before


def test_route_has_no_target_it_only_ever_acts_on_this_computer(admin, db_path):
    co, me = _shop_on_disk(db_path, main_seen=LONG_AGO)
    c = _db(db_path)
    try:
        upsert_local_device(c, "other", co, platform="WINDOWS")
        c.commit()
    finally:
        c.close()
    r = _take(admin, device_id="other", target="other")
    assert r.status_code == 200
    assert _state(db_path)[1] == [me]


def test_route_no_main_yet_is_pointed_at_the_ordinary_claim(admin, db_path):
    co, me = _shop_on_disk(db_path, main_seen=LONG_AGO)
    c = _db(db_path)
    try:
        c.execute("DELETE FROM admin_device_assignment")
        c.execute("UPDATE devices SET is_admin_device=0")
        c.commit()
    finally:
        c.close()
    r = _take(admin)
    assert r.status_code == 409 and r.get_json()["code"] == "TAKEOVER_NO_MAIN"
    assert _state(db_path)[0] is None


# ── D9 (second fix round, 2026-09-30): joined, THEN founded a new shop ───────

def _audit_marks(db_path):
    c = _db(db_path)
    try:
        return [r[0] for r in c.execute(
            "SELECT action FROM audit_logs WHERE action IN ('JOINED_DEVICE_SEEDED','ADMIN_CREATED') "
            "ORDER BY rowid")]
    finally:
        c.close()


def test_d9_a_joined_device_that_then_founds_its_own_shop_can_claim_main(app, db_path):
    """Mutant: is_joined_device goes back to "a JOINED mark beats a founding
    mark" (or its ORDER BY rowid DESC is dropped). Real routes, real audit
    rows: this computer first joined a shop with a licence key (the seeder
    writes JOINED_DEVICE_SEEDED), then its owner chose "set up a new shop
    instead" -- POST create-admin writes ADMIN_CREATED AFTER it. That computer
    is a founder, and used to be refused for ever (JOINED_DEVICE_CANNOT_CLAIM)
    because the older mark outranked the newer one.
    DENY twin, opposite order: a founder whose device is later re-joined to
    another shop (JOINED mark written AFTER) is a joiner again and IS refused,
    so the fix did not simply open the claim to everyone."""
    c = _db(db_path)
    try:
        assert company_rebind.seed_company_settings_for_joining_device(c, "LIC-1") is True
        c.commit()
    finally:
        c.close()
    assert _audit_marks(db_path) == ["JOINED_DEVICE_SEEDED"]

    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': OWNER_EMAIL, 'password': OWNER_PW})
    assert r.status_code == 200, r.get_json()
    assert _audit_marks(db_path) == ["JOINED_DEVICE_SEEDED", "ADMIN_CREATED"]   # joined FIRST, founded LATER

    co, me = _shop_on_disk(db_path, main_seen=LONG_AGO)
    c = _db(db_path)
    try:
        c.execute("DELETE FROM admin_device_assignment")
        c.execute("UPDATE devices SET is_admin_device=0")
        c.commit()
    finally:
        c.close()
    ok = client.post("/api/devices/me/claim-admin")
    assert ok.status_code == 200, ok.get_json()
    a, flagged, _ = _state(db_path)
    assert a["device_id"] == me and flagged == [me]

    # DENY twin: now a JOINED mark lands AFTER the founding one.
    c = _db(db_path)
    try:
        c.execute("DELETE FROM admin_device_assignment")
        c.execute("UPDATE devices SET is_admin_device=0")
        c.execute("INSERT INTO audit_logs (id, company_id, action, entity_type, entity_id) "
                  "VALUES ('later-join', ?, 'JOINED_DEVICE_SEEDED', 'company_settings', 'later-join')", (co,))
        c.commit()
    finally:
        c.close()
    device_context._cached_device = None
    refused = client.post("/api/devices/me/claim-admin")
    assert refused.status_code == 409
    assert refused.get_json()["code"] == "JOINED_DEVICE_CANNOT_CLAIM"
    assert _state(db_path)[0] is None
