"""Phase 9.5A Milestone 24 -- GPS/privacy structural tests and employee
lifecycle uniqueness. See docs/owner/phase9_5a/customer-location-contract.md
and mobile-location-privacy-design.md.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import make_staff

FORBIDDEN_TRACKING_MARKERS = ("background", "continuous", "always_on", "track_continuously", "periodic_location")


def _make_profile(app, staff_id, employee_number):
    from app.employees.services import create_employee_profile

    return create_employee_profile(
        {
            "staff_user_id": staff_id,
            "employee_number": employee_number,
            "full_name": f"Employee {employee_number}",
            "employment_start_date": date(2026, 1, 1),
        },
        actor_staff_user_id=staff_id,
    )


def test_customer_location_has_no_background_tracking_field(app):
    with app.app_context():
        from app.models.leads import CustomerLocation

        column_names = {c.name.lower() for c in CustomerLocation.__table__.columns}
        for marker in FORBIDDEN_TRACKING_MARKERS:
            assert not any(marker in name for name in column_names), f"unexpected tracking-shaped column matching '{marker}'"


def test_employee_presence_session_has_no_location_field(app):
    """Presence is deliberately online/offline signal only, never a location
    ping -- employee-presence-contract.md's explicit exclusion list."""
    with app.app_context():
        from app.models.employees import EmployeePresenceSession

        column_names = {c.name.lower() for c in EmployeePresenceSession.__table__.columns}
        for forbidden in ("latitude", "longitude", "location", "gps"):
            assert forbidden not in column_names


def test_licensing_service_module_has_no_location_reference(app):
    """No location data ever enters the /api/licensing/v1 traffic surface --
    the licensing_service package is untouched by Phase 9.5A."""
    import inspect

    with app.app_context():
        from app.licensing_service import activation, assertions, checkin, deactivation

        for module in (activation, assertions, checkin, deactivation):
            source = inspect.getsource(module)
            assert "latitude" not in source
            assert "longitude" not in source
            assert "CustomerLocation" not in source


def test_capture_location_accepts_approximate_network_accuracy(app, seeded):
    staff_id = make_staff(app, "priv1@example.com")
    with app.app_context():
        from app.leads.services import capture_location, create_lead

        profile = _make_profile(app, staff_id, "EMP-PRIV1")
        lead = create_lead({"organization_or_prospect_name": "Approx Co"}, actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id)

        location = capture_location(
            lead_id=lead.id, customer_id=None,
            fields={"source": "NETWORK", "latitude": Decimal("31.9539"), "longitude": Decimal("35.9106"), "accuracy_meters": Decimal("850.00")},
            actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
        )
        assert location.accuracy_meters == Decimal("850.00")
        assert location.verified is False  # never implied verified merely from a GPS/NETWORK source


def test_capture_location_writes_a_real_audit_row(app, seeded):
    staff_id = make_staff(app, "priv2@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.leads.services import capture_location, create_lead
        from app.models.audit import AuditLog

        profile = _make_profile(app, staff_id, "EMP-PRIV2")
        lead = create_lead({"organization_or_prospect_name": "Audited Loc Co"}, actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id)
        capture_location(
            lead_id=lead.id, customer_id=None, fields={"source": "MANUAL", "manual_address": "1 Test St"},
            actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
        )
        row = db_session.query(AuditLog).filter_by(action_code="CUSTOMER_LOCATION_CAPTURED", entity_public_id=str(lead.id)).first()
        assert row is not None
        assert row.actor_staff_user_id == staff_id


def test_employee_number_uniqueness_enforced_at_db_level(app, seeded):
    staff_a = make_staff(app, "priv3a@example.com")
    staff_b = make_staff(app, "priv3b@example.com")
    with app.app_context():
        from app.employees.services import create_employee_profile
        from app.extensions import db_session

        create_employee_profile(
            {"staff_user_id": staff_a, "employee_number": "DUP-001", "full_name": "First", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_a,
        )
        with pytest.raises(IntegrityError):
            create_employee_profile(
                {"staff_user_id": staff_b, "employee_number": "DUP-001", "full_name": "Second", "employment_start_date": date(2026, 1, 1)},
                actor_staff_user_id=staff_b,
            )
        db_session.rollback()
