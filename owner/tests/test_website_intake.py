"""Website demo-request intake (POST /api/intake/website-lead).

Every guard is proven by the row it did or did not write, not only by the
status code: default-off 404, both directions of the secret check, the rate
limit (which counts only AUTHENTICATED requests, so an unauthenticated flood
cannot burn the real caller's budget), idempotency, validation, and the
system actor that can never log in."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select

from tests.conftest import force_login, make_staff

SECRET = "w" * 48
URL = "/api/intake/website-lead"


def _enable(app, secret=SECRET):
    app.config["WEBSITE_INTAKE_SECRET"] = secret


def _seed_actor(app):
    with app.app_context():
        from app.leads.website_intake import seed_website_intake_actor
        return seed_website_intake_actor()


def _body(**over):
    body = {"lead_id": "7", "name": "Sami Haddad", "phone": "+962790000000", "business_type": "shop",
            "business_name": "Haddad Market", "branches": "2-3", "message": "Need a demo for two branches", "lang": "ar"}
    body.update(over)
    return body


def _post(client, body, *, auth=None):
    return client.post(URL, json=body, headers={"Authorization": f"Bearer {SECRET}" if auth is None else auth})


def _count(app, model):
    with app.app_context():
        from app.extensions import db_session
        return db_session.execute(select(func.count()).select_from(model)).scalar_one()


def _ready(app):
    _enable(app)
    return _seed_actor(app)


def test_unset_secret_answers_404_like_a_missing_route(app, client):
    from app.models.leads import Lead
    from app.models.licensing_service import RateLimitCounter

    _enable(app, "")
    _seed_actor(app)
    hit = _post(client, _body(), auth="Bearer ")
    miss = client.post("/api/intake/nope", json=_body(), headers={"Authorization": "Bearer "})
    assert hit.status_code == 404
    assert miss.status_code == 404
    assert hit.get_data() == miss.get_data()
    assert _count(app, Lead) == 0
    assert _count(app, RateLimitCounter) == 0  # nothing ran before the guard


@pytest.mark.parametrize("auth", [f"Bearer {SECRET}", f"bearer {SECRET}"])
def test_correct_secret_creates_the_website_lead_and_its_note(app, client, auth):
    from app.extensions import db_session
    from app.models.commercial_sales import CommercialOperationsIdempotencyKey
    from app.models.leads import Lead, LeadNote

    actor = _ready(app)
    res = _post(client, _body(), auth=auth)  # CSRF is on in testing: success proves the exemption
    assert res.status_code == 201
    data = res.get_json()
    assert data["ok"] is True and data["created"] is True and data["lead_id"]
    with app.app_context():
        lead = db_session.get(Lead, uuid.UUID(data["lead_id"]))
        assert lead.source == "WEBSITE"
        assert lead.status == "NEW"
        assert lead.organization_or_prospect_name == "Haddad Market"
        assert lead.primary_contact_name == "Sami Haddad"
        assert lead.phone == "+962790000000"
        assert str(lead.created_by_employee_profile_id) == actor["employee_profile_id"]
        notes = db_session.execute(select(LeadNote)).scalars().all()
        assert len(notes) == 1
        assert str(notes[0].author_employee_profile_id) == actor["employee_profile_id"]
        for fragment in (
            "Website demo request #7", "Business type: shop", "Branches: 2-3", "Language: ar",
            "Need a demo for two branches",
        ):
            assert fragment in notes[0].body
        key = db_session.execute(
            select(CommercialOperationsIdempotencyKey).where(
                CommercialOperationsIdempotencyKey.idempotency_key == "website:7"
            )
        ).scalars().first()
        assert key is not None


@pytest.mark.parametrize(
    "auth",
    ["Bearer wrong", f"Bearer {SECRET}x", f"Bearer {SECRET[:-1]}", f"Basic {SECRET}", SECRET, "", "Bearer ",
     f"Token {SECRET}"],
)
def test_wrong_or_malformed_credentials_are_401_and_write_nothing(app, client, auth):
    from app.models.leads import Lead

    _ready(app)
    res = _post(client, _body(), auth=auth)
    assert res.status_code == 401
    assert res.headers.get("WWW-Authenticate")
    assert _count(app, Lead) == 0


def test_missing_authorization_header_is_401(app, client):
    from app.models.leads import Lead

    _ready(app)
    res = client.post(URL, json=_body())
    assert res.status_code == 401
    assert _count(app, Lead) == 0


def test_org_name_falls_back_to_contact_name(app, client):
    from app.extensions import db_session
    from app.models.leads import Lead

    _ready(app)
    res = _post(client, _body(business_name=""))
    assert res.status_code == 201
    with app.app_context():
        lead = db_session.get(Lead, uuid.UUID(res.get_json()["lead_id"]))
        assert lead.organization_or_prospect_name == "Sami Haddad"


def test_replaying_the_same_lead_id_returns_the_same_lead_and_adds_nothing(app, client):
    from app.models.commercial_sales import CommercialOperationsIdempotencyKey
    from app.models.leads import Lead, LeadNote, LeadStatusHistory

    _ready(app)
    first = _post(client, _body())
    second = _post(client, _body())
    third = _post(client, _body(message="a different message"))
    assert first.status_code == 201
    assert second.status_code == 200 and second.get_json()["created"] is False
    assert third.status_code == 200 and third.get_json()["created"] is False
    assert second.get_json()["lead_id"] == first.get_json()["lead_id"] == third.get_json()["lead_id"]
    assert _count(app, Lead) == 1
    assert _count(app, LeadNote) == 1
    assert _count(app, LeadStatusHistory) == 1
    assert _count(app, CommercialOperationsIdempotencyKey) == 1


def test_different_lead_ids_create_different_leads(app, client):
    from app.models.leads import Lead

    _ready(app)
    a = _post(client, _body(lead_id="7"))
    b = _post(client, _body(lead_id="8"))
    assert a.status_code == b.status_code == 201
    assert a.get_json()["lead_id"] != b.get_json()["lead_id"]
    assert _count(app, Lead) == 2


def test_concurrent_duplicate_is_answered_as_a_replay(app, client, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app.leads import services
    from app.models.leads import Lead, LeadNote

    _ready(app)

    def _racing(*args, **kwargs):
        services.create_lead(*args, **kwargs)
        raise IntegrityError("dup", None, Exception("dup"))

    monkeypatch.setattr("app.leads.website_intake.create_lead", _racing)
    res = _post(client, _body())
    assert res.status_code == 200
    assert res.get_json()["created"] is False
    assert _count(app, Lead) == 1
    assert _count(app, LeadNote) == 0  # the loser path never reaches the note


@pytest.mark.parametrize(
    "over,field",
    [
        ({"name": ""}, "name"),
        ({"phone": "1" * 33}, "phone"),
        ({"business_type": "spaceship"}, "business_type"),
        ({"branches": "9"}, "branches"),
        ({"lead_id": "../x"}, "lead_id"),
        ({"lead_id": True}, "lead_id"),
        ({"message": "x" * 2001}, "message"),
        ({"lang": "fr"}, "lang"),
        ({"name": 5}, "name"),
        ({"phone": "07900abc"}, "phone"),  # F7: letters are not a phone number
    ],
)
def test_invalid_payloads_are_rejected_and_write_nothing(app, client, over, field):
    from app.models.leads import Lead

    _ready(app)
    res = _post(client, _body(**over))
    assert res.status_code == 422
    assert res.get_json()["field"] == field
    assert _count(app, Lead) == 0


def test_non_object_bodies_are_400_invalid_json_and_write_nothing(app, client):
    from app.models.leads import Lead

    _ready(app)
    headers = {"Authorization": f"Bearer {SECRET}"}
    bad = client.post(URL, data="{bad", content_type="application/json", headers=headers)
    lst = client.post(URL, json=[1], headers=headers)
    txt = client.post(URL, data=json.dumps(_body()), content_type="text/plain", headers=headers)
    for res in (bad, lst, txt):
        assert res.status_code == 400
        assert res.get_json()["error"] == "invalid_json"
    assert _count(app, Lead) == 0


def test_unseeded_actor_is_503_and_writes_nothing(app, client):
    from app.models.leads import Lead

    _enable(app)
    res = _post(client, _body())
    assert res.status_code == 503
    assert res.get_json()["error"] == "INTAKE_NOT_SEEDED"
    assert _count(app, Lead) == 0


def test_rate_limit_counts_authenticated_requests_and_the_31st_is_429(app, client, monkeypatch):
    from app.licensing_service import ratelimit
    from app.models.leads import Lead

    class _Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 10, 21, 12, 0, 5, tzinfo=dt.timezone.utc)

    monkeypatch.setattr(ratelimit, "datetime", _Frozen)
    _ready(app)
    # Changed 2026-10-21 by design (the limit now runs AFTER the bearer check):
    # the budget is spent by 30 AUTHENTICATED posts (one create, 29 replays),
    # not by wrong-secret ones. This still goes red if the limit call is removed
    # (the 31st would be 200, not 429). What it no longer proves -- that wrong
    # secrets burn budget -- is now the opposite guarantee, pinned by
    # test_unauthenticated_flood_cannot_burn_the_real_callers_budget.
    statuses = [_post(client, _body()).status_code for _ in range(30)]
    assert statuses == [201] + [200] * 29
    res = _post(client, _body())  # budget spent
    assert res.status_code == 429
    assert res.headers["Retry-After"] == "55"
    assert _count(app, Lead) == 1


def test_unauthenticated_flood_cannot_burn_the_real_callers_budget(app, client, monkeypatch):
    # F3: the caller is a Cloudflare egress IP shared with other traffic, so
    # wrong-bearer requests from that address must never consume its budget.
    from app.licensing_service import ratelimit
    from app.models.leads import Lead
    from app.models.licensing_service import RateLimitCounter

    class _Frozen(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 10, 21, 12, 0, 5, tzinfo=dt.timezone.utc)

    monkeypatch.setattr(ratelimit, "datetime", _Frozen)
    _ready(app)
    statuses = [_post(client, _body(), auth="Bearer wrong").status_code for _ in range(40)]
    assert statuses == [401] * 40
    assert _count(app, RateLimitCounter) == 0
    res = _post(client, _body())
    assert res.status_code == 201
    assert _count(app, Lead) == 1


@pytest.mark.parametrize("secret_set", [True, False])
def test_get_is_404_whether_or_not_the_secret_is_set(app, client, secret_set):
    # F9: the route must not reveal itself with a 405 to a probing GET.
    _enable(app, SECRET if secret_set else "")
    res = client.get(URL, headers={"Authorization": f"Bearer {SECRET}"})
    assert res.status_code == 404


@pytest.mark.parametrize("weak", ["tiny-9Zq7", "changeme" + "x" * 40, "placeholder-" + "x" * 40])
def test_a_weak_secret_turns_intake_off_instead_of_refusing_to_boot(app, client, weak):
    # Changed 2026-10-21 by design: an optional feature's bad secret disables the
    # feature (404 + one startup WARNING) instead of refusing boot. The route must
    # not accept the weak value as a bearer.
    from app.leads.intake_routes import warn_if_intake_secret_unusable
    from app.models.leads import Lead

    _enable(app, weak)
    _seed_actor(app)
    res = _post(client, _body(), auth=f"Bearer {weak}")
    assert res.status_code == 404
    assert _count(app, Lead) == 0
    import logging

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    # Attached to the module's own logger: the app's JSON log setup does not
    # propagate to the root logger, so caplog cannot see these records.
    logger = logging.getLogger("app.leads.intake_routes")
    handler = _Collect(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        warn_if_intake_secret_unusable(app)
    finally:
        logger.removeHandler(handler)
    logged = " ".join(r.getMessage() for r in records)
    assert "OWNER_WEBSITE_INTAKE_SECRET" in logged
    assert weak not in logged


def test_seed_command_is_skipped_while_intake_is_off(app):
    from app.extensions import db_session
    from app.leads.website_intake import WEBSITE_INTAKE_EMAIL
    from app.models.staff import StaffUser

    _enable(app, "")
    result = app.test_cli_runner().invoke(args=["seed-website-intake"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output.strip().splitlines()[-1]) == {"skipped": "website intake off"}
    with app.app_context():
        assert db_session.execute(select(StaffUser).where(StaffUser.email == WEBSITE_INTAKE_EMAIL)).first() is None


def test_unicode_invisibles_are_removed_and_a_lone_surrogate_does_not_500(app, client):
    # F7. Each of these used to survive into the database / the lead page.
    from app.extensions import db_session
    from app.models.leads import Lead, LeadNote

    _ready(app)
    res = client.post(
        URL,
        data=json.dumps(_body(name="Sa\u2028mi", message="line one\nli\u202ene two\ud800 end")),
        content_type="application/json",
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert res.status_code == 201
    with app.app_context():
        lead = db_session.execute(select(Lead)).scalars().one()
        assert lead.primary_contact_name == "Sami"
        body = db_session.execute(select(LeadNote)).scalars().one().body
        assert "\u202e" not in body and "\u2028" not in body and "\ud800" not in body
        assert "line one\nline two end" in body  # the newline is kept in the message


def test_the_intake_note_is_added_once_even_if_another_delivery_already_wrote_it(app, monkeypatch):
    # F5: simulate the race by writing the actor's note between create_lead and
    # the note step, exactly as a concurrent delivery would.
    from app.leads import services
    from app.leads.website_intake import ingest_website_lead, parse_website_lead_payload
    from app.models.leads import LeadNote

    actor = _ready(app)

    def _other_delivery_wins_the_note(fields, profile_id, staff_user_id, **kwargs):
        lead = services.create_lead(fields, profile_id, staff_user_id, **kwargs)
        services.add_lead_note(lead, "already written by the other delivery", profile_id, staff_user_id)
        return lead

    monkeypatch.setattr("app.leads.website_intake.create_lead", _other_delivery_wins_the_note)
    with app.app_context():
        ingest_website_lead(parse_website_lead_payload(_body()))
    assert _count(app, LeadNote) == 1
    assert actor["employee_profile_id"]


def _staff_with_profile(app, email):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=["SALES"])
    with app.app_context():
        create_employee_profile(
            {"staff_user_id": staff_id, "employee_number": "E-" + email[:4], "full_name": "Sales",
             "employment_start_date": date(2026, 1, 1), "employment_status": "ACTIVE"},
            actor_staff_user_id=staff_id,
        )
    return staff_id


def test_staff_cannot_supply_a_website_idempotency_key_on_the_form(app, client, seeded):
    # F6: "website:7" belongs to the website intake; a typed one would alias lead #7.
    import re

    from app.models.leads import Lead

    staff_id = _staff_with_profile(app, "form-sales@example.com")
    force_login(client, app, staff_id)
    page = client.get("/leads/new")
    csrf = re.search(r'name="csrf_token" value="([^"]*)"', page.get_data(as_text=True)).group(1)
    data = {"csrf_token": csrf, "organization_or_prospect_name": "Typed", "phone": "1"}
    bad = client.post("/leads", data={**data, "idempotency_key": "WebSite:7"})
    assert bad.status_code == 400
    assert _count(app, Lead) == 0
    ok = client.post("/leads", data={**data, "idempotency_key": "my-own-key"})  # allow-half
    assert ok.status_code == 302
    assert _count(app, Lead) == 1


def test_staff_cannot_supply_a_website_idempotency_key_on_the_api(app, client, seeded):
    import re

    from app.models.leads import Lead

    staff_id = _staff_with_profile(app, "api-sales@example.com")
    force_login(client, app, staff_id)
    page = client.get("/leads/new")
    csrf = re.search(r'name="csrf_token" value="([^"]*)"', page.get_data(as_text=True)).group(1)
    headers = {"X-CSRFToken": csrf}
    api = "/api/operations/v1/leads"
    body = {"organization_or_prospect_name": "Typed", "phone": "1"}
    bad = client.post(api, json={**body, "idempotency_key": "website:7"}, headers=headers)
    assert bad.status_code == 400
    assert _count(app, Lead) == 0
    ok = client.post(api, json={**body, "idempotency_key": "my-own-key"}, headers=headers)  # allow-half
    assert ok.status_code == 201
    assert _count(app, Lead) == 1


def test_html_in_the_message_is_escaped_on_the_lead_page(app, client, seeded):
    from app.employees.services import create_employee_profile
    from app.leads.website_intake import ingest_website_lead, parse_website_lead_payload

    _seed_actor(app)
    with app.app_context():
        lead, _created = ingest_website_lead(
            parse_website_lead_payload(_body(message="<script>alert(1)</script>"))
        )
        lead_id = lead.id
    viewer = make_staff(app, "intake-viewer@example.com", role_codes=["VIEWER"])
    with app.app_context():
        create_employee_profile(
            {
                "staff_user_id": viewer,
                "employee_number": "E-VIEW",
                "full_name": "Viewer",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=viewer,
        )
    force_login(client, app, viewer)
    res = client.get(f"/leads/{lead_id}")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


def test_seed_is_idempotent_and_the_account_can_never_log_in(app, client, seeded):
    from app.auth.services import authenticate
    from app.extensions import db_session
    from app.leads.website_intake import WEBSITE_INTAKE_EMAIL
    from app.models.employees import EmployeeProfile
    from app.models.staff import StaffUser
    from app.security.rbac import get_staff_permission_codes

    # Changed 2026-10-21 by design: the seed command is a no-op unless intake is
    # opted in (secret set), so this test opts in first.
    _enable(app)
    runner = app.test_cli_runner()
    first = json.loads(runner.invoke(args=["seed-website-intake"]).output.strip().splitlines()[-1])
    second = json.loads(runner.invoke(args=["seed-website-intake"]).output.strip().splitlines()[-1])
    assert first["created"] is True
    assert second["created"] is False and second["repaired"] is False
    with app.app_context():
        rows = db_session.execute(select(StaffUser).where(StaffUser.email == WEBSITE_INTAKE_EMAIL)).scalars().all()
        assert len(rows) == 1
        staff = rows[0]
        assert staff.is_active is False
        assert staff.disabled_at is not None
        assert staff.is_super_admin is False
        profile = db_session.execute(
            select(EmployeeProfile).where(EmployeeProfile.staff_user_id == staff.id)
        ).scalars().one()
        assert profile.employment_status == "ARCHIVED"
        user, _reason = authenticate(WEBSITE_INTAKE_EMAIL, "Sup3r-Str0ng-Pass!", "127.0.0.1", 5, 900)
        assert user is None
        assert get_staff_permission_codes(staff) == set()


def test_seed_repairs_a_hand_reenabled_account(app):
    from app.extensions import db_session
    from app.leads.website_intake import (
        WEBSITE_INTAKE_EMAIL,
        WEBSITE_INTAKE_UNUSABLE_PASSWORD_HASH,
        seed_website_intake_actor,
    )
    from app.models.staff import StaffUser

    _seed_actor(app)
    with app.app_context():
        staff = db_session.execute(select(StaffUser).where(StaffUser.email == WEBSITE_INTAKE_EMAIL)).scalars().one()
        before = staff.session_version
        staff.is_active = True
        staff.password_hash = "x"
        db_session.commit()
        result = seed_website_intake_actor()
        db_session.expire_all()
        staff = db_session.execute(select(StaffUser).where(StaffUser.email == WEBSITE_INTAKE_EMAIL)).scalars().one()
        assert result["repaired"] is True
        assert staff.is_active is False
        assert staff.password_hash == WEBSITE_INTAKE_UNUSABLE_PASSWORD_HASH
        assert staff.session_version == before + 1
