"""Owner-editable retention (months) for unconverted website leads.

The nightly purge used to be hard-wired to 730 days. The owner now sets the
months in the Control Center (System page). These tests prove, in both
directions: the stored value is what the CLI uses (and an explicit flag still
wins), and the save route is refused for a role without system.restore with the
stored value left UNCHANGED (asserted on the row, not just the status code)."""
from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import select

from tests.conftest import get_csrf, login_and_verify_mfa, make_staff
from tests.test_website_lead_retention import _exists, _make_lead, _seed_actor


def _store(app, value):
    from app.extensions import db_session
    from app.leads.website_retention import RETENTION_SETTING_KEY
    from app.models.audit import SystemSetting

    with app.app_context():
        db_session.add(SystemSetting(key=RETENTION_SETTING_KEY, value=value))
        db_session.commit()


def _stored(app):
    from app.extensions import db_session
    from app.leads.website_retention import RETENTION_SETTING_KEY
    from app.models.audit import SystemSetting

    with app.app_context():
        row = db_session.execute(select(SystemSetting).where(SystemSetting.key == RETENTION_SETTING_KEY)).scalars().first()
        return None if row is None else row.value


def _get(app):
    from app.extensions import db_session
    from app.leads.website_retention import get_retention_months

    with app.app_context():
        return get_retention_months(db_session)


def test_default_is_24_with_no_row(app):
    assert _get(app) == 24


def test_returns_stored_value(app):
    _store(app, {"months": 7})
    assert _get(app) == 7


@pytest.mark.parametrize("bad", [{"months": 0}, {"months": 61}, {"months": "12"}, {"months": 1.5},
                                 {"months": True}, {"other": 3}, {}])
def test_malformed_or_out_of_range_falls_back_to_24(app, bad):
    _store(app, bad)
    assert _get(app) == 24


def test_months_to_days_matches_todays_constant():
    from app.leads.website_retention import WEBSITE_LEAD_RETENTION_DAYS, months_to_days
    assert months_to_days(24) == WEBSITE_LEAD_RETENTION_DAYS == 730
    assert months_to_days(1) == 30 and months_to_days(60) == 1825


@pytest.mark.parametrize("bad", [0, 61, -1, "12", 1.5, None, True])
def test_set_rejects_invalid(app, bad):
    from app.extensions import db_session
    from app.leads.website_retention import set_retention_months

    staff_id = make_staff(app, "ret-set@example.com")
    with app.app_context():
        from app.models.staff import StaffUser
        staff = db_session.get(StaffUser, staff_id)
        with pytest.raises(ValueError):
            set_retention_months(db_session, bad, staff)
    assert _stored(app) is None


@pytest.mark.parametrize("ok", [1, 60])
def test_set_accepts_bounds(app, ok):
    from app.extensions import db_session
    from app.leads.website_retention import set_retention_months

    staff_id = make_staff(app, "ret-set2@example.com")
    with app.app_context():
        from app.models.staff import StaffUser
        staff = db_session.get(StaffUser, staff_id)
        assert set_retention_months(db_session, ok, staff) == 24
        db_session.commit()
    assert _stored(app) == {"months": ok}


def _run(app, *args):
    result = app.test_cli_runner().invoke(args=["leads", "purge-website", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.output[result.output.index("{"):])


def test_cli_without_flag_uses_the_stored_setting(app):
    _seed_actor(app)
    lead = _make_lead(app, "dormant40", age_days=40)
    # Default 24 months: a 40-day-old lead is far inside the window, so kept.
    kept = _run(app, "--apply")
    assert kept["retention_source"] == "setting" and kept["retention_months"] == 24
    assert kept["deleted"] == 0 and _exists(app, lead)
    # 1 month (30 days): the same lead is now dormant long enough and is deleted.
    _store(app, {"months": 1})
    gone = _run(app, "--apply")
    assert gone["retention_source"] == "setting" and gone["retention_months"] == 1
    assert gone["older_than_days"] == 30 and gone["deleted"] == 1
    assert not _exists(app, lead)


def test_explicit_flag_beats_the_setting(app):
    _seed_actor(app)
    lead = _make_lead(app, "dormant40", age_days=40)
    _store(app, {"months": 1})  # would delete it
    out = _run(app, "--older-than-days", "100", "--apply")
    assert out["retention_source"] == "flag" and out["retention_months"] is None
    assert out["older_than_days"] == 100 and out["deleted"] == 0
    assert _exists(app, lead)


# -- route ---------------------------------------------------------------

def _csrf(client):
    return get_csrf(client.get("/system/backups").get_data(as_text=True))


def _admin(app, client):
    make_staff(app, "ret-admin@example.com", super_admin=True, mfa=True)
    login_and_verify_mfa(client, "ret-admin@example.com")


def test_get_page_shows_current_value(app, seeded, client):
    _store(app, {"months": 13})
    _admin(app, client)
    html = client.get("/system/backups").get_data(as_text=True)
    assert "Website demo requests" in html
    assert re.search(r'name="months"[^>]*value="13"', html)


def test_allowed_role_saves_and_audit_row_has_before_after(app, seeded, client):
    from app.extensions import db_session
    from app.models.audit import AuditLog

    _store(app, {"months": 18})
    _admin(app, client)
    resp = client.post("/system/website-lead-retention", data={"csrf_token": _csrf(client), "months": "6"})
    assert resp.status_code == 302
    assert _stored(app) == {"months": 6}
    with app.app_context():
        row = db_session.execute(
            select(AuditLog).where(AuditLog.action_code == "WEBSITE_LEAD_RETENTION_CHANGED")
        ).scalars().one()
        assert row.before_state_redacted == {"months": 18}
        assert row.after_state_redacted == {"months": 6}
        assert row.actor_staff_user_id is not None


def test_out_of_range_post_changes_nothing(app, seeded, client):
    _admin(app, client)
    for bad in ("0", "61", "abc", "1.5", ""):
        client.post("/system/website-lead-retention", data={"csrf_token": _csrf(client), "months": bad})
    assert _stored(app) is None


def test_role_without_permission_is_refused_and_setting_unchanged(app, seeded, client):
    _store(app, {"months": 24})
    # Fresh MFA so require_recent_auth passes: the ONLY thing left to refuse
    # this user is the permission check (SALES holds no system.* permission).
    make_staff(app, "ret-sales@example.com", role_codes=["SALES"], mfa=True)
    login_and_verify_mfa(client, "ret-sales@example.com")
    resp = client.post("/system/website-lead-retention", data={"csrf_token": _csrf_any(client), "months": "1"})
    assert resp.status_code == 403
    assert _stored(app) == {"months": 24}


def _csrf_any(client):
    # SALES cannot open /system/backups (system.view), so take a token from a page it can.
    return get_csrf(client.get("/").get_data(as_text=True)) or get_csrf(client.get("/auth/reauth").get_data(as_text=True))
