from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from tests.conftest import make_staff


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


def test_valid_coordinates_pass():
    from app.leads.location import validate_location_fields
    from app.models.base import utcnow

    cleaned = validate_location_fields({"latitude": "31.9539", "longitude": "35.9106", "accuracy_meters": "15.5", "source": "GPS"}, now=utcnow())
    assert cleaned["latitude"] == pytest_approx_decimal("31.9539")
    assert cleaned["source"] == "GPS"


def pytest_approx_decimal(s):
    from decimal import Decimal

    return Decimal(s)


@pytest.mark.parametrize("lat", ["91", "-91", "1000"])
def test_latitude_out_of_bounds_rejected(lat):
    from app.leads.errors import LocationValidationError
    from app.leads.location import validate_location_fields
    from app.models.base import utcnow

    with pytest.raises(LocationValidationError):
        validate_location_fields({"latitude": lat}, now=utcnow())


@pytest.mark.parametrize("lon", ["181", "-181"])
def test_longitude_out_of_bounds_rejected(lon):
    from app.leads.errors import LocationValidationError
    from app.leads.location import validate_location_fields
    from app.models.base import utcnow

    with pytest.raises(LocationValidationError):
        validate_location_fields({"longitude": lon}, now=utcnow())


def test_nan_and_infinity_rejected():
    from app.leads.errors import LocationValidationError
    from app.leads.location import validate_location_fields
    from app.models.base import utcnow

    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(LocationValidationError):
            validate_location_fields({"latitude": bad}, now=utcnow())


def test_negative_accuracy_rejected():
    from app.leads.errors import LocationValidationError
    from app.leads.location import validate_location_fields
    from app.models.base import utcnow

    with pytest.raises(LocationValidationError):
        validate_location_fields({"accuracy_meters": "-1"}, now=utcnow())


def test_unknown_source_rejected():
    from app.leads.errors import LocationValidationError
    from app.leads.location import validate_location_fields
    from app.models.base import utcnow

    with pytest.raises(LocationValidationError):
        validate_location_fields({"source": "SATELLITE_LASER"}, now=utcnow())


def test_client_timestamp_too_far_rejected():
    from app.leads.errors import LocationValidationError
    from app.leads.location import validate_location_fields
    from app.models.base import utcnow

    with pytest.raises(LocationValidationError):
        validate_location_fields({"client_captured_at": utcnow() - timedelta(days=1)}, now=utcnow())


def test_manual_address_too_long_rejected():
    from app.leads.errors import LocationValidationError
    from app.leads.location import validate_location_fields
    from app.models.base import utcnow

    with pytest.raises(LocationValidationError):
        validate_location_fields({"manual_address": "x" * 2001}, now=utcnow())


def test_verify_location_requires_reason_and_records_actor_not_coordinates(app, seeded):
    from app.leads.errors import LocationValidationError
    from app.leads.location import verify_location
    from app.leads.services import capture_location, create_lead

    staff_id = make_staff(app, "loc1@example.com")
    with app.app_context():
        profile = _make_profile(app, staff_id, "EMP-LOC1")
        lead = create_lead({"organization_or_prospect_name": "Loc Co", "phone": "111"}, profile.id, staff_id)
        location = capture_location(
            lead_id=lead.id, customer_id=None,
            fields={"latitude": "31.95", "longitude": "35.91", "source": "GPS"},
            actor_employee_profile_id=profile.id, actor_staff_user_id=staff_id,
        )
        assert location.verified is False  # never defaults to verified

        with pytest.raises(LocationValidationError):
            verify_location(location, "", profile.id, staff_id)

        verify_location(location, "Confirmed via site visit photo.", profile.id, staff_id)
        assert location.verified is True
        assert location.verified_by_employee_profile_id == profile.id
        assert location.verification_reason == "Confirmed via site visit photo."
        assert location.verified_at is not None
