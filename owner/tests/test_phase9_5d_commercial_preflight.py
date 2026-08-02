"""Phase 9.5D Milestone 28 -- blocking commercial preflight tests.

_check_commercial_sales_domain_integrity() (per Milestone 1's own audit
plan: "Add _check_commercial_sales_domain_integrity following
_check_crm_domain_integrity's template") -- every condition here should
already be structurally impossible via the real service-layer invariants;
these tests prove the preflight check would actually catch a bypass (a
direct DB write bypassing every service-layer guard), matching
test_commercial_ops_preflight.py's own established pattern.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tests.conftest import make_staff


def test_commercial_sales_integrity_passes_by_default(app, seeded, signing_key):
    with app.app_context():
        from app.commercial_ops.preflight import run_preflight

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        commercial_checks = [c for c in result.checks if c.name.startswith(("no_invalid_quote", "no_invalid_sales_order", "no_invalid_commercial", "no_invalid_commission_ledger", "quote_has_customer", "no_over_allocated", "no_over_refunded"))]
        assert len(commercial_checks) == 9
        assert all(c.status == "OK" for c in commercial_checks), commercial_checks


def test_preflight_detects_invalid_quote_status_bypassing_the_service_layer(app, seeded, signing_key):
    """A direct DB write (bypassing every real transition guard) must be
    caught -- this is exactly the class of bug this check exists for."""
    staff_id = make_staff(app, "preflight-e2e@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.customers.services import create_customer
        from app.employees.services import create_employee_profile
        from app.extensions import db_session
        from app.models.commercial_sales import Quote

        profile = create_employee_profile(
            {"staff_user_id": staff_id, "employee_number": "EMP-PREFLIGHT", "full_name": "Preflight Test", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_id,
        )
        customer = create_customer({"legal_name": "Preflight Test Customer Co"}, actor_staff_user_id=staff_id)

        # Direct row construction, bypassing create_quote()'s own status="DRAFT"
        # default and every transition guard -- simulates a bug in a future
        # migration or an unreviewed direct DB write.
        quote = Quote(
            customer_id=customer.id, created_by_employee_profile_id=profile.id, status="NOT_A_REAL_STATUS",
            quote_number="PREFLIGHT-BYPASS-001", currency="USD", subtotal=Decimal("0"), discount_total=Decimal("0"),
            total=Decimal("0"), valid_until=date.today() + timedelta(days=30), version=1,
        )
        db_session.add(quote)
        db_session.commit()

        from app.commercial_ops.preflight import _check_commercial_sales_domain_integrity, PreflightCheck

        checks: list[PreflightCheck] = []
        ok = _check_commercial_sales_domain_integrity(checks)
        assert ok is False
        failed = [c for c in checks if c.name == "no_invalid_quote_status"]
        assert len(failed) == 1
        assert failed[0].status == "FAIL"
        assert "NOT_A_REAL_STATUS" in failed[0].detail

        # Clean up so this test doesn't poison later tests sharing the DB.
        db_session.delete(quote)
        db_session.commit()


def test_quote_with_neither_customer_nor_lead_is_rejected_at_the_db_level(app, seeded, signing_key):
    """The quote_has_customer_or_lead preflight check exists as a
    defense-in-depth backstop, but the real, primary guarantee is the DB's
    own ck_owner_quotes_at_least_one_of_customer_lead CHECK constraint
    (Milestone 7) -- proven here directly: an orphan row can never even be
    committed in the first place."""
    import pytest
    from sqlalchemy.exc import IntegrityError

    staff_id = make_staff(app, "preflight-orphan@example.com", role_codes=["SALES"])
    with app.app_context():
        from app.employees.services import create_employee_profile
        from app.extensions import db_session
        from app.models.commercial_sales import Quote

        profile = create_employee_profile(
            {"staff_user_id": staff_id, "employee_number": "EMP-PREFLIGHT2", "full_name": "Preflight Test 2", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_id,
        )
        quote = Quote(
            customer_id=None, lead_id=None, created_by_employee_profile_id=profile.id, status="DRAFT",
            quote_number="PREFLIGHT-ORPHAN-001", currency="USD", subtotal=Decimal("0"), discount_total=Decimal("0"),
            total=Decimal("0"), valid_until=date.today() + timedelta(days=30), version=1,
        )
        db_session.add(quote)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
