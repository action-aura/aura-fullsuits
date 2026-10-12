"""OWNER-SEC item 2 (review D SEC-03): the commercial-ops UI routes that
release/replace a device slot must demand recent MFA exactly like their twin
licensing_admin.replace_device. Both directions: no recent auth -> bounced to
reauth and the slot is untouched; recent auth -> the slot is freed."""
from __future__ import annotations

import pytest

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_license, make_staff


def _active_installation(app, staff_id):
    from app.extensions import db_session
    from app.installations.services import register_installation
    from app.licensing.services import transition_license
    from app.models.catalog import Platform
    from app.models.licensing import License

    with app.app_context():
        lic_id, _ = make_license(app, staff_id, device_limit=2)
        lic = db_session.get(License, lic_id)
        transition_license(lic, "ACTIVE", staff_id)
        platform = db_session.query(Platform).filter_by(platform_code="WINDOWS").first()
        inst = register_installation(
            {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": lic.product_id,
             "platform_id": platform.id, "installation_label": "dev-x"},
            staff_id,
        )
        inst.status = "ACTIVE"
        db_session.commit()
        return str(inst.id)


def _status(app, installation_id):
    from app.extensions import db_session
    from app.models.installations import Installation

    with app.app_context():
        db_session.expire_all()
        return db_session.get(Installation, installation_id).status


@pytest.mark.parametrize("action,final", [("release", "DEACTIVATED"), ("replace", "REPLACED")])
def test_slot_action_requires_recent_auth_and_works_with_it(app, client, seeded, action, final):
    admin_id = make_staff(app, "slot-admin@example.com", super_admin=True, mfa=True)
    inst_id = _active_installation(app, admin_id)
    url = f"/commercial-ops/ui/installations/{inst_id}/{action}"

    # DENY: a session with no recent MFA is sent to reauth; nothing changes.
    force_login(client, app, admin_id)
    csrf = get_csrf(client.get("/auth/reauth").get_data(as_text=True))
    resp = client.post(url, data={"csrf_token": csrf, "reason": "test reason"})
    assert resp.status_code == 302
    assert "/auth/reauth" in resp.headers["Location"]
    assert _status(app, inst_id) == "ACTIVE"

    # ALLOW: a real MFA-verified session performs it.
    c2 = app.test_client()
    login_and_verify_mfa(c2, "slot-admin@example.com")
    csrf = get_csrf(c2.get("/auth/reauth").get_data(as_text=True))
    resp = c2.post(url, data={"csrf_token": csrf, "reason": "test reason"})
    assert resp.status_code == 302
    assert "/auth/reauth" not in resp.headers["Location"]
    assert _status(app, inst_id) == final
