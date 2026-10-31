"""2026-10-31 (owner decision): a seller no longer holds the three "free"
buttons -- extend a shop (subscriptions.renew), un-suspend / change state
(subscriptions.update), extend a pilot (pilots.manage) -- nor, through
subscriptions.renew, add devices. Those moved to SALES_MANAGER.

Both directions are tested on purpose: the seller is refused, the manager is
allowed (a deny-only test would pass if the permission were deleted from
everyone), and `flask seed-rbac` must strip the old grants from a database
that already carries them (seed_rbac only ever added before)."""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_license, make_staff

FREE_BUTTONS = ("subscriptions.update", "subscriptions.renew", "pilots.manage")


def _sub_and_license(app, actor, *, status="ACTIVE"):
    from app.extensions import db_session
    from app.models.licensing import License
    from app.subscriptions.services import transition_subscription

    lic_id, _ = make_license(app, actor, device_limit=1)
    with app.app_context():
        lic = db_session.get(License, lic_id)
        sub = lic.subscription
        if status != "ACTIVE":
            transition_subscription(sub, status, actor)
        return str(sub.id), str(lic_id)


def _csrf(client, sub_id):
    return get_csrf(client.get(f"/subscriptions/{sub_id}").get_data(as_text=True))


def _sub_field(app, sub_id, field):
    from app.extensions import db_session
    from app.models.subscriptions import Subscription

    with app.app_context():
        db_session.expire_all()
        return getattr(db_session.get(Subscription, sub_id), field)


def _device_limit(app, lic_id):
    from app.extensions import db_session
    from app.models.licensing import License

    with app.app_context():
        db_session.expire_all()
        return db_session.get(License, lic_id).device_limit


def _perm_codes(app, role_code):
    from app.extensions import db_session
    from app.models.staff import Permission, Role, RolePermission

    with app.app_context():
        role = db_session.execute(select(Role).where(Role.code == role_code)).scalars().one()
        return {
            c for (c,) in db_session.execute(
                select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id == role.id)
            )
        }


# ---------------------------------------------------------------- the seller

def test_seller_is_refused_extend_renew_unsuspend_and_add_devices(app, client, seeded):
    admin = make_staff(app, "fb-admin@example.com", super_admin=True)
    make_staff(app, "fb-seller@example.com", role_codes=["SALES"], mfa=True)
    active_sub, lic = _sub_and_license(app, admin)
    suspended_sub, _ = _sub_and_license(app, admin, status="SUSPENDED")
    before_end = _sub_field(app, active_sub, "end_date")

    login_and_verify_mfa(client, "fb-seller@example.com")  # recent auth: only the permission can refuse
    csrf = _csrf(client, active_sub)

    renew = client.post(f"/subscriptions/{active_sub}/renew",
                        data={"csrf_token": csrf, "new_end_date": "2031-01-01", "reason": "free extension"})
    unsuspend = client.post(f"/subscriptions/{suspended_sub}/transition",
                            data={"csrf_token": csrf, "to_status": "ACTIVE", "reason": "free un-suspend"})
    add_dev = client.post(f"/licenses/{lic}/add-devices",
                          data={"csrf_token": csrf, "additional_devices": "3", "reason": "free devices"})
    pilot = client.post("/commercial-ops/ui/pilots", data={"csrf_token": csrf, "subscription_id": active_sub})
    ops_extend = client.post("/commercial-ops/renewals", json={"subscription_id": active_sub},
                             headers={"X-CSRFToken": csrf})

    assert [r.status_code for r in (renew, unsuspend, add_dev, pilot, ops_extend)] == [403] * 5
    # and nothing moved
    assert _sub_field(app, active_sub, "end_date") == before_end
    assert _sub_field(app, suspended_sub, "status") == "SUSPENDED"
    assert _device_limit(app, lic) == 1


def test_seller_role_holds_none_of_the_free_buttons_but_keeps_what_it_sells_with(seeded, app):
    codes = _perm_codes(app, "SALES")
    assert not (codes & set(FREE_BUTTONS))
    # the one-screen sale (licensing/issuance_routes.py) and the sales pipeline
    assert {"orders.create", "payments.create", "subscriptions.create", "subscriptions.view",
            "customers.create", "pilots.view"} <= codes


# ------------------------------------------------------------- the manager

def test_sales_manager_is_allowed_extend_unsuspend_and_add_devices(app, client, seeded):
    admin = make_staff(app, "fb-admin2@example.com", super_admin=True)
    make_staff(app, "fb-mgr@example.com", role_codes=["SALES_MANAGER"], mfa=True)
    active_sub, lic = _sub_and_license(app, admin)
    suspended_sub, _ = _sub_and_license(app, admin, status="SUSPENDED")

    login_and_verify_mfa(client, "fb-mgr@example.com")
    csrf = _csrf(client, active_sub)

    renew = client.post(f"/subscriptions/{active_sub}/renew",
                        data={"csrf_token": csrf, "new_end_date": "2031-01-01", "reason": "manager extension"})
    unsuspend = client.post(f"/subscriptions/{suspended_sub}/transition",
                            data={"csrf_token": csrf, "to_status": "ACTIVE", "reason": "manager un-suspend"})
    add_dev = client.post(f"/licenses/{lic}/add-devices",
                          data={"csrf_token": csrf, "additional_devices": "3", "reason": "manager devices",
                                "slot_kind": "PHONE"})  # the route now needs a kind (typed extras)

    assert (renew.status_code, unsuspend.status_code, add_dev.status_code) == (302, 302, 302)
    assert _sub_field(app, active_sub, "end_date") == date(2031, 1, 1)
    assert _sub_field(app, suspended_sub, "status") == "ACTIVE"
    assert _device_limit(app, lic) == 4


def test_sales_manager_holds_the_free_buttons_but_still_cannot_suspend_or_cancel(seeded, app):
    codes = _perm_codes(app, "SALES_MANAGER")
    assert set(FREE_BUTTONS) <= codes
    assert not ({"subscriptions.suspend", "subscriptions.cancel"} & codes)


# ------------------------------------------------- seed-rbac on an old database

def test_seed_rbac_removes_the_old_seller_grants_from_an_existing_database(app, seeded):
    """The live server was seeded with the old grants. Re-add them as that
    database had them, run the real seed_rbac(), and they must be gone -- while
    everything else SALES holds, and the manager's new grants, are untouched."""
    from app.extensions import db_session
    from app.models.staff import Permission, Role, RolePermission
    from app.staff.services import seed_rbac

    with app.app_context():
        sales = db_session.execute(select(Role).where(Role.code == "SALES")).scalars().one()
        for code in FREE_BUTTONS:
            perm = db_session.execute(select(Permission).where(Permission.code == code)).scalars().one()
            db_session.add(RolePermission(role_id=sales.id, permission_id=perm.id))
        db_session.commit()
    assert set(FREE_BUTTONS) <= _perm_codes(app, "SALES")  # precondition: old state really present

    kept_before = _perm_codes(app, "SALES") - set(FREE_BUTTONS)
    with app.app_context():
        seed_rbac()
        seed_rbac()  # idempotent: a second run changes nothing and does not fail

    assert not (_perm_codes(app, "SALES") & set(FREE_BUTTONS))
    assert _perm_codes(app, "SALES") == kept_before
    assert set(FREE_BUTTONS) <= _perm_codes(app, "SALES_MANAGER")


def test_revoked_list_never_contradicts_the_roles_it_revokes_from():
    from app.staff.seed_data import REVOKED_ROLE_PERMISSIONS, ROLES

    for role_code, revoked in REVOKED_ROLE_PERMISSIONS.items():
        wanted = ROLES[role_code]["permissions"]
        assert wanted == "*" or not (set(revoked) & set(wanted)), (role_code, revoked)
