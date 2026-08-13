"""Phase 9.5D Milestone 10 -- payment recording/confirmation orchestration
tests."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import make_staff

_employee_number_counter = iter(range(1, 100000))


def _seed_sales_employee(app, email, role_codes=None):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=role_codes or ["SALES"])
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id,
                "employee_number": f"EMP-{next(_employee_number_counter):05d}",
                "full_name": f"Staff {email}",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        return staff_id, profile.id


def _seed_customer(app, staff_id):
    from app.customers.services import create_customer

    with app.app_context():
        customer = create_customer({"legal_name": "Payment Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def test_submit_payment_creates_pending_record(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "paya@example.com")
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.commercial_sales.payments import submit_payment

        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("100.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_id,
        )
        assert payment.status == "PENDING"
        assert payment.recorded_by_staff_user_id == staff_id


def test_submit_payment_rejects_non_positive_amount(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "payb@example.com")
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.payments import submit_payment

        with pytest.raises(CommercialSalesError) as exc:
            submit_payment(
                customer_id=customer_id, amount=Decimal("0.00"), currency="USD", method="CASH",
                payment_date=date.today(), actor_staff_user_id=staff_id,
            )
        assert exc.value.code == "NEGATIVE_DOCUMENT_TOTAL"


def test_submit_payment_rejects_invalid_method(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "payc@example.com")
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.payments import submit_payment

        with pytest.raises(CommercialSalesError) as exc:
            submit_payment(
                customer_id=customer_id, amount=Decimal("100.00"), currency="USD", method="BITCOIN",
                payment_date=date.today(), actor_staff_user_id=staff_id,
            )
        assert exc.value.code == "INVALID_PAYMENT_METHOD"


def test_confirm_payment_by_different_staff_succeeds(app, seeded):
    staff_a, _ = _seed_sales_employee(app, "payd_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "payd_b@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment, submit_payment

        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("100.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_a,
        )
        confirmed = confirm_payment(payment, actor_staff_user_id=staff_b)
        assert confirmed.status == "CONFIRMED"
        assert confirmed.verified_by_staff_user_id == staff_b


def test_self_confirmation_forbidden(app, seeded):
    """Maker-checker: the employee who submitted a payment cannot also
    confirm it -- Non-Negotiable Rule 4's real teeth, enforced regardless
    of permission grant."""
    staff_id, _ = _seed_sales_employee(app, "paye@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.payments import confirm_payment, submit_payment

        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("100.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_id,
        )
        with pytest.raises(CommercialSalesError) as exc:
            confirm_payment(payment, actor_staff_user_id=staff_id)
        assert exc.value.code == "SELF_CONFIRMATION_FORBIDDEN"


def test_double_confirmation_rejected(app, seeded):
    staff_a, _ = _seed_sales_employee(app, "payf_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "payf_b@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.payments import confirm_payment, submit_payment

        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("100.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_a,
        )
        confirm_payment(payment, actor_staff_user_id=staff_b)

        with pytest.raises(CommercialSalesError) as exc:
            confirm_payment(payment, actor_staff_user_id=staff_b)
        assert exc.value.code == "PAYMENT_ALREADY_CONFIRMED"


def test_reject_payment_requires_reason(app, seeded):
    staff_a, _ = _seed_sales_employee(app, "payg_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "payg_b@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.payments import reject_payment, submit_payment

        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("100.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_a,
        )
        with pytest.raises(CommercialSalesError) as exc:
            reject_payment(payment, reason="", actor_staff_user_id=staff_b)
        assert exc.value.code == "REASON_REQUIRED"


def test_reject_payment_success(app, seeded):
    staff_a, _ = _seed_sales_employee(app, "payh_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "payh_b@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    with app.app_context():
        from app.commercial_sales.payments import reject_payment, submit_payment

        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("100.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_a,
        )
        rejected = reject_payment(payment, reason="Bounced cheque", actor_staff_user_id=staff_b)
        assert rejected.status == "FAILED"


def test_confirmation_creates_correction_history_row(app, seeded):
    """correct_payment() (existing, reused unchanged) already writes a
    PaymentCorrectionHistory row -- confirming this orchestration
    doesn't bypass that existing audit-trail mechanism."""
    staff_a, _ = _seed_sales_employee(app, "payi_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "payi_b@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment, submit_payment
        from app.extensions import db_session
        from app.models.commercial_ops import PaymentCorrectionHistory

        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("100.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_a,
        )
        confirm_payment(payment, actor_staff_user_id=staff_b)

        history = db_session.query(PaymentCorrectionHistory).filter_by(payment_record_id=payment.id).all()
        assert len(history) == 1
        assert history[0].new_status == "CONFIRMED"
