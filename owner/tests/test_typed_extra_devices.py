"""Typed extra devices (owner decision 2026-10-31): extra PHONES and extra DESKTOPS.

The rule: a package includes 2 devices of ANY kind; each bought extra phone lets one
MORE Android device in, each bought extra desktop one MORE Windows device -- never the
other way round. n = extra desktops, m = extra phones, pool = max(0, limit - n - m); a
new device is allowed iff

    other + max(0, windows - n) + max(0, android - m) <= pool        (counts include the asker)

With n = m = 0 that is exactly the old "total <= limit" check.

Every guard below was mutation-proven (the commit message lists each mutation and the
test that went red). Tests assert the REASON CODE and the ROW COUNTS, not only a status
code: a 400 cannot tell "the typed check ran and refused" from "something else refused".
"""
from __future__ import annotations

import json
import uuid
from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import build_activation_body, make_device_keypair, make_license, make_staff

ACTIVATIONS = "/api/licensing/v1/activations"


def _activate(client, full_key, platform, installation_id, private_key=None):
    key = private_key or make_device_keypair()
    body = build_activation_body(key, full_key=full_key, installation_id=installation_id, platform=platform)
    return client.post(ACTIVATIONS, data=json.dumps(body), content_type="application/json")


def _licence(app, name, *, limit, desktops=0, phones=0):
    staff_id = make_staff(app, f"{name}-{uuid.uuid4().hex[:6]}@example.com")
    license_id, full_key = make_license(
        app, staff_id, device_limit=limit, extra_desktop_slots=desktops, extra_phone_slots=phones
    )
    return staff_id, license_id, full_key


def _ok(resp):
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    assert resp.get_json()["result"] == "SUCCESS"


def _refused(resp, code):
    assert resp.status_code == 400, resp.get_data(as_text=True)[:500]
    assert resp.get_json()["reason_code"] == code


def _installations(app, license_id):
    with app.app_context():
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.installations import Installation

        return db_session.execute(
            select(func.count()).select_from(Installation).where(Installation.license_id == license_id)
        ).scalar_one()


# ----------------------------------------------------------- T9 the pure rule --

@pytest.mark.parametrize("counts,n,m,pool,expected", [
    # n = m = 0: exactly the old total check, whatever the kinds
    ({"ANDROID": 2}, 0, 0, 2, True),
    ({"ANDROID": 3}, 0, 0, 2, False),
    ({"WINDOWS": 1, "ANDROID": 1}, 0, 0, 2, True),
    ({"WINDOWS": 2, "ANDROID": 1}, 0, 0, 2, False),
    # one bought phone (limit 3, pool 2): a THIRD phone fits, a third desktop does not
    ({"ANDROID": 3}, 0, 1, 2, True),
    ({"WINDOWS": 3}, 0, 1, 2, False),
    ({"WINDOWS": 2, "ANDROID": 1}, 0, 1, 2, True),
    # one bought desktop (limit 3, pool 2): the mirror
    ({"WINDOWS": 3}, 1, 0, 2, True),
    ({"ANDROID": 3}, 1, 0, 2, False),
    # the typed slot is not interchangeable: with n=1 the second AND third phone use the pool
    ({"ANDROID": 2}, 1, 0, 2, True),
    ({"ANDROID": 3}, 1, 0, 2, False),
    # two typed extras of different kinds, nothing bought "any kind" beyond the included 2
    ({"WINDOWS": 3, "ANDROID": 3}, 1, 1, 2, False),
    ({"WINDOWS": 2, "ANDROID": 2}, 1, 1, 2, True),
    ({"WINDOWS": 3, "ANDROID": 1}, 1, 1, 2, True),
    # a platform that is neither: only the pool admits it
    ({"IOS": 2}, 1, 1, 2, True),
    ({"IOS": 3}, 1, 1, 2, False),
    ({"IOS": 1, "WINDOWS": 2}, 1, 0, 2, True),
    ({"IOS": 1, "WINDOWS": 3}, 1, 0, 2, False),
    # a pool that went negative is floored by the caller; zero pool admits only typed devices
    ({"WINDOWS": 1}, 1, 0, 0, True),
    ({"ANDROID": 1}, 1, 0, 0, False),
])
def test_fits_typed_slots_table(counts, n, m, pool, expected):
    from app.commercial_ops.device_slot_ops import fits_typed_slots

    assert fits_typed_slots(counts, extra_desktops=n, extra_phones=m, pool=pool) is expected


# ------------------------------------------------- activation over real HTTP --

def test_t1_a_plain_package_takes_any_two_devices_and_refuses_the_third(app, client, seeded, signing_key):
    """n = m = 0: two phones fit (the included 2 are ANY kind), the third is the OLD
    DEVICE_LIMIT_REACHED -- the typed code never appears on a licence without typed extras."""
    _staff, license_id, full_key = _licence(app, "t1", limit=2)
    _ok(_activate(client, full_key, "ANDROID", "t1-a"))
    _ok(_activate(client, full_key, "ANDROID", "t1-b"))
    _refused(_activate(client, full_key, "ANDROID", "t1-c"), "DEVICE_LIMIT_REACHED")
    assert _installations(app, license_id) == 2


def test_t2_a_bought_desktop_does_not_admit_a_third_phone(app, client, seeded, signing_key):
    """limit 3 with ONE extra DESKTOP: phones may only use the 2 any-kind slots, so a
    third phone is refused with the typed code -- while the desktop still fits (allow half)."""
    _staff, license_id, full_key = _licence(app, "t2", limit=3, desktops=1)
    _ok(_activate(client, full_key, "ANDROID", "t2-a"))
    _ok(_activate(client, full_key, "ANDROID", "t2-b"))
    _refused(_activate(client, full_key, "ANDROID", "t2-c"), "DEVICE_TYPE_LIMIT_REACHED")
    assert _installations(app, license_id) == 2  # the refused phone left no row
    _ok(_activate(client, full_key, "WINDOWS", "t2-d"))  # the bought desktop slot is usable
    assert _installations(app, license_id) == 3


def test_t3_a_bought_phone_admits_a_third_phone(app, client, seeded, signing_key):
    """The mirror, and the allow half of T2: limit 3 with ONE extra PHONE takes three phones."""
    _staff, license_id, full_key = _licence(app, "t3", limit=3, phones=1)
    for suffix in "abc":
        _ok(_activate(client, full_key, "ANDROID", f"t3-{suffix}"))
    assert _installations(app, license_id) == 3
    _refused(_activate(client, full_key, "ANDROID", "t3-d"), "DEVICE_LIMIT_REACHED")


def test_t4_the_total_check_stays_first_so_a_full_licence_still_says_device_limit(app, client, seeded, signing_key):
    """limit 3, one extra desktop, already 3 devices: a fourth phone is a FULL licence
    (DEVICE_LIMIT_REACHED), not a wrong-kind refusal. Pins the order of the two checks."""
    _staff, license_id, full_key = _licence(app, "t4", limit=3, desktops=1)
    _ok(_activate(client, full_key, "ANDROID", "t4-a"))
    _ok(_activate(client, full_key, "ANDROID", "t4-b"))
    _ok(_activate(client, full_key, "WINDOWS", "t4-c"))
    _refused(_activate(client, full_key, "ANDROID", "t4-d"), "DEVICE_LIMIT_REACHED")
    _refused(_activate(client, full_key, "WINDOWS", "t4-e"), "DEVICE_LIMIT_REACHED")


def test_t5_a_bought_phone_does_not_admit_a_third_desktop(app, client, seeded, signing_key):
    _staff, license_id, full_key = _licence(app, "t5", limit=3, phones=1)
    _ok(_activate(client, full_key, "WINDOWS", "t5-a"))
    _ok(_activate(client, full_key, "WINDOWS", "t5-b"))
    _refused(_activate(client, full_key, "WINDOWS", "t5-c"), "DEVICE_TYPE_LIMIT_REACHED")
    _ok(_activate(client, full_key, "ANDROID", "t5-d"))
    assert _installations(app, license_id) == 3


def test_t6_re_activation_of_a_device_already_in_is_never_blocked_by_the_typed_rule(app, client, seeded, signing_key):
    """Three phones in under one bought phone; the extra phone is then taken away (m -> 0)
    WITHOUT lowering the total (a stale-state case). The same installation and the same
    device key coming back must still get in: re-activation takes no new slot."""
    _staff, license_id, full_key = _licence(app, "t6", limit=3, phones=1)
    keys = {suffix: make_device_keypair() for suffix in "abc"}
    for suffix, key in keys.items():
        _ok(_activate(client, full_key, "ANDROID", f"t6-{suffix}", key))
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License

        db_session.get(License, license_id).extra_phone_slots = 0
        db_session.commit()
    _ok(_activate(client, full_key, "ANDROID", "t6-a", keys["a"]))           # same installation_id
    _ok(_activate(client, full_key, "ANDROID", "t6-fresh-id", keys["b"]))    # fresh id, same device key
    assert _installations(app, license_id) == 3
    # ...but a brand-new device is judged by the (now smaller) typed allowance
    _refused(_activate(client, full_key, "ANDROID", "t6-new"), "DEVICE_LIMIT_REACHED")


def test_t7_the_typed_code_reaches_the_app_unchanged(app, client, seeded, signing_key):
    """Public codes pass through; anything else is normalized to ACTIVATION_REJECTED
    (anti-enumeration). The app needs the real code to show the right message."""
    from app.licensing_service.reason_codes import PUBLIC_REASON_CODES

    assert "DEVICE_TYPE_LIMIT_REACHED" in PUBLIC_REASON_CODES
    _staff, _license_id, full_key = _licence(app, "t7", limit=3, desktops=1)
    _ok(_activate(client, full_key, "ANDROID", "t7-a"))
    _ok(_activate(client, full_key, "ANDROID", "t7-b"))
    refused = _activate(client, full_key, "ANDROID", "t7-c")
    assert refused.get_json()["reason_code"] == "DEVICE_TYPE_LIMIT_REACHED"
    assert refused.get_json()["result"] != "SUCCESS"


# --------------------------------------------- the held (manual approval) path --

def _held_world(app, staff_id, *, limit, desktops, phones, held_platform, others):
    """A licence with `others` ACTIVE installations (platform codes) and one installation
    held in PENDING_ACTIVATION on held_platform, plus its PendingActivation row."""
    from app.commercial_ops.activation_policy import create_pending_activation
    from app.extensions import db_session
    from app.installations.services import register_installation
    from app.models.catalog import Platform
    from tests.test_commercial_ops_activation_policy import _pending_installation

    installation, lic = _pending_installation(app, staff_id, device_limit=limit)
    lic.extra_desktop_slots, lic.extra_phone_slots = desktops, phones
    installation.platform_id = db_session.query(Platform).filter_by(platform_code=held_platform).one().id
    db_session.commit()
    for i, code in enumerate(others):
        other = register_installation(
            {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": installation.product_id,
             "platform_id": db_session.query(Platform).filter_by(platform_code=code).one().id,
             "installation_label": f"held-other-{i}"},
            staff_id,
        )
        other.status = "ACTIVE"
    db_session.commit()
    pending = create_pending_activation(
        installation=installation, license_id=lic.id, product_id=installation.product_id,
        platform_id=installation.platform_id, mode="MANUAL_APPROVAL",
    )
    return installation, pending


def test_t8_approving_a_held_activation_rechecks_the_kind(app, seeded):
    """The kind of the HELD device is counted back in. limit 3, one extra desktop, two phones
    already in: a held PHONE cannot be approved (typed code), a held DESKTOP can (allow half)."""
    staff_id = make_staff(app, "t8@example.com")
    with app.app_context():
        from app.commercial_ops.activation_policy import PendingActivationError, approve_pending_activation

        installation, pending = _held_world(
            app, staff_id, limit=3, desktops=1, phones=0, held_platform="ANDROID", others=["ANDROID", "ANDROID"]
        )
        with pytest.raises(PendingActivationError) as refused:
            approve_pending_activation(pending, staff_id)
        assert refused.value.code == "DEVICE_TYPE_LIMIT_REACHED"
        assert installation.status == "PENDING_ACTIVATION" and pending.status == "PENDING_REVIEW"


def test_t8b_approving_a_held_desktop_with_a_bought_desktop_works(app, seeded):
    staff_id = make_staff(app, "t8b@example.com")
    with app.app_context():
        from app.commercial_ops.activation_policy import approve_pending_activation

        installation, pending = _held_world(
            app, staff_id, limit=3, desktops=1, phones=0, held_platform="WINDOWS", others=["ANDROID", "ANDROID"]
        )
        approve_pending_activation(pending, staff_id)
        assert installation.status == "ACTIVE" and pending.status == "APPROVED"


# ------------------------------------------------- staff "add devices", typed --

def test_t19_add_devices_phone_raises_the_phone_column_and_the_total(app, seeded):
    staff_id = make_staff(app, "t19@example.com")
    license_id, _ = make_license(app, staff_id, device_limit=2)
    with app.app_context():
        from app.commercial_ops.device_slot_ops import DeviceSlotError, add_devices
        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.models.licensing import License

        lic = db_session.get(License, license_id)
        add_devices(lic, additional_devices=1, reason="bought a phone", actor_staff_user_id=staff_id, slot_kind="PHONE")
        db_session.refresh(lic)
        assert (lic.device_limit, lic.extra_phone_slots, lic.extra_desktop_slots) == (3, 1, 0)
        add_devices(lic, additional_devices=2, reason="bought desktops", actor_staff_user_id=staff_id, slot_kind="DESKTOP")
        db_session.refresh(lic)
        assert (lic.device_limit, lic.extra_phone_slots, lic.extra_desktop_slots) == (5, 1, 2)
        audit = db_session.query(AuditLog).filter_by(action_code="LICENSE_DEVICES_ADDED", entity_public_id=str(license_id)).all()
        assert [row.after_state_redacted["slot_kind"] for row in audit] == ["PHONE", "DESKTOP"]
        with pytest.raises(DeviceSlotError) as bad:
            add_devices(lic, additional_devices=1, reason="x", actor_staff_user_id=staff_id, slot_kind="TABLET")
        assert bad.value.code == "INVALID_SLOT_KIND"
        db_session.refresh(lic)
        assert lic.device_limit == 5  # the refused kind changed nothing


def test_t19b_the_add_devices_route_needs_a_kind(app, client, seeded):
    """Staff must say WHICH kind they add. A missing or unknown kind is a 400 that changes
    nothing; a real kind is accepted and raises that kind's column (both directions)."""
    from tests.conftest import get_csrf, login_and_verify_mfa

    admin = make_staff(app, "t19b-admin@example.com", super_admin=True)
    make_staff(app, "t19b-mgr@example.com", role_codes=["SALES_MANAGER"], mfa=True)
    license_id, _ = make_license(app, admin, device_limit=2)
    login_and_verify_mfa(client, "t19b-mgr@example.com")
    with app.app_context():
        from app.models.licensing import License  # noqa: F401
    from app.extensions import db_session
    from app.models.licensing import License as L

    def _state():
        with app.app_context():
            db_session.expire_all()
            lic = db_session.get(L, license_id)
            return lic.device_limit, lic.extra_desktop_slots, lic.extra_phone_slots, str(lic.subscription_id)

    sub_id = _state()[3]
    csrf = get_csrf(client.get(f"/subscriptions/{sub_id}").get_data(as_text=True))
    base = {"csrf_token": csrf, "additional_devices": "1", "reason": "bought one"}
    for bad in ({}, {"slot_kind": ""}, {"slot_kind": "TABLET"}, {"slot_kind": "phone"}):
        resp = client.post(f"/licenses/{license_id}/add-devices", data={**base, **bad})
        assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_slot_kind", bad
        assert _state()[:3] == (2, 0, 0)
    assert client.post(f"/licenses/{license_id}/add-devices", data={**base, "slot_kind": "PHONE"}).status_code == 302
    assert _state()[:3] == (3, 0, 1)
    assert client.post(f"/licenses/{license_id}/add-devices", data={**base, "slot_kind": "DESKTOP"}).status_code == 302
    assert _state()[:3] == (4, 1, 1)


# ----------------------------------------------------- licence replacement --

def test_t18_a_replacement_licence_keeps_the_typed_extras(app, seeded):
    staff_id = make_staff(app, "t18@example.com")
    license_id, _ = make_license(app, staff_id, device_limit=4, extra_desktop_slots=1, extra_phone_slots=1)
    with app.app_context():
        from app.extensions import db_session
        from app.licensing.services import replace_license, transition_license
        from app.models.licensing import License

        old = db_session.get(License, license_id)
        transition_license(old, "ACTIVE", staff_id)
        new = replace_license(old, staff_id)
        assert (new.device_limit, new.extra_desktop_slots, new.extra_phone_slots) == (4, 1, 1)


# ============================================================ T22 migration ==

def test_t22_migration_backfills_pairs_as_typed_extras_and_the_guards_bite():
    import importlib.util
    import os

    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import IntegrityError

    from tests.conftest import TEST_DB_URL
    from tests.test_sauth_registry_pin_migration import _alembic, _seed

    prev, new_rev = "d7e3a1b94c52", "a8f2c5d9e317"
    scratch = "aura_owner_typed_extras_scratch"
    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = TEST_DB_URL.rsplit("/", 1)[0]
    scratch_url, admin_url = f"{base}/{scratch}", f"{base}/postgres"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {scratch} OWNER aura_owner"))
    admin.dispose()
    try:
        assert _alembic(owner_dir, scratch_url, "upgrade", prev).returncode == 0
        engine = create_engine(scratch_url)
        cache: dict = {}
        with engine.begin() as conn:
            def seed(table, overrides=None):
                row_id = _seed(conn, table, cache, overrides)
                cache[table] = row_id
                return row_id

            seed("owner_plans", {"included_device_count": 2})
            order_id = seed("owner_sales_orders")
            pair_addon = _seed(conn, "owner_addons", cache, {"addon_code": "EXTRA_DEVICE_PAIR"})
            _seed(conn, "owner_sales_order_lines", cache, {"sales_order_id": order_id, "addon_id": pair_addon, "quantity": 2})
            seed("owner_subscriptions", {"sales_order_id": order_id})
            lic_pairs = seed("owner_licenses", {"device_limit": 6, "status": "ACTIVE"})
            # the same licence lost one pair to an applied refund and has another waiting
            refund_a = _seed(conn, "owner_commercial_refunds", cache)
            cache.pop("owner_commercial_refunds", None)
            refund_b = _seed(conn, "owner_commercial_refunds", cache)
            applied = _seed(conn, "owner_license_scheduled_stops", cache, {
                "license_id": lic_pairs, "source_refund_id": refund_a, "to_status": None, "devices_removed": 2,
                "applied_at": "2026-10-01T00:00:00+00:00", "reason": "r"})
            waiting = _seed(conn, "owner_license_scheduled_stops", cache, {
                "license_id": lic_pairs, "source_refund_id": refund_b, "to_status": None, "devices_removed": 2, "reason": "r"})
            # a licence with no pairs at all, and one whose limit was raised by staff without a sale
            cache.pop("owner_subscriptions", None)
            seed("owner_subscriptions")
            lic_plain = seed("owner_licenses", {"device_limit": 2, "status": "ACTIVE"})
            cache.pop("owner_subscriptions", None)
            seed("owner_subscriptions")
            lic_staff = seed("owner_licenses", {"device_limit": 5, "status": "ACTIVE"})
        engine.dispose()

        up = _alembic(owner_dir, scratch_url, "upgrade", new_rev)
        assert up.returncode == 0, up.stderr
        engine = create_engine(scratch_url)

        def typed(license_id):
            with engine.begin() as conn:
                return tuple(conn.execute(text(
                    "SELECT extra_desktop_slots, extra_phone_slots FROM owner_licenses WHERE id = :i"), {"i": license_id}).one())

        # 2 pairs bought - 1 pair already taken away = 1 desktop + 1 phone (limit 6 would allow 2)
        assert typed(lic_pairs) == (1, 1)
        assert typed(lic_plain) == (0, 0)
        assert typed(lic_staff) == (0, 0)   # staff-added devices stay any-kind, as they always were
        with engine.begin() as conn:
            rows = {str(r[0]): tuple(r[1:]) for r in conn.execute(text(
                "SELECT id, devices_removed, desktops_removed, phones_removed FROM owner_license_scheduled_stops"))}
        assert rows[str(waiting)] == (2, 1, 1) and rows[str(applied)] == (2, 1, 1)
        # the backfill is an absolute recomputation: running it again changes nothing
        spec = importlib.util.spec_from_file_location(
            "typed_extras_migration", os.path.join(owner_dir, "migrations", "versions", "a8f2c5d9e317_typed_extra_device_slots.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with engine.begin() as conn:
            conn.execute(text(module._BACKFILL_LICENCES))
            conn.execute(text(module._BACKFILL_STOPS))
        assert typed(lic_pairs) == (1, 1) and typed(lic_plain) == (0, 0)
        # deny: a negative count; allow: a real one
        for column in ("extra_desktop_slots", "extra_phone_slots"):
            with pytest.raises(IntegrityError):
                with engine.begin() as conn:
                    conn.execute(text(f"UPDATE owner_licenses SET {column} = -1 WHERE id = :i"), {"i": lic_plain})
            with engine.begin() as conn:
                conn.execute(text(f"UPDATE owner_licenses SET {column} = 2 WHERE id = :i"), {"i": lic_plain})
        engine.dispose()

        down = _alembic(owner_dir, scratch_url, "downgrade", prev)
        assert down.returncode == 0, down.stderr
        engine = create_engine(scratch_url)
        with engine.begin() as conn:
            columns = {r[0] for r in conn.execute(text(
                "SELECT column_name FROM information_schema.columns WHERE table_name IN "
                "('owner_licenses', 'owner_license_scheduled_stops')"))}
            assert not ({"extra_desktop_slots", "extra_phone_slots", "desktops_removed", "phones_removed"} & columns)
            assert conn.execute(text("SELECT device_limit FROM owner_licenses WHERE id = :i"), {"i": lic_pairs}).scalar_one() == 6
        engine.dispose()
    finally:
        admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)"))
        admin.dispose()
