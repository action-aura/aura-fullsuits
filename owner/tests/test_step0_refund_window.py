"""Owner decision (2026-10-25): NO refunds after 30 days.

A refund is refused once 30 days have passed since the sale's money was
confirmed -- the same moment the 30-day commission hold ends, computed by the
same function with the same constant (ladder.HOLD_PERIOD), so "inside the
hold" and "still refundable" can never disagree. Refused at the service
layer, both when the refund is requested and when it is confirmed (paid
out), with a translated message.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from tests.test_step0_ladder import _ladder_sales, _refund, _rows, _sell, _world

UTC = timezone.utc


def _create(app, w, order_id, amount="250.00"):
    rows = _rows(app, order_id)
    with app.app_context():
        from app.commercial_sales.refunds import create_refund
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice

        return create_refund(db_session.get(CommercialInvoice, rows["invoice_id"]), amount=Decimal(amount),
                             reason="Customer changed their mind", payment_record_id=None,
                             actor_employee_profile_id=w["manager_profile"], actor_staff_user_id=w["manager"]).id


def _refunds(app):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialRefund

        return [r.status for r in db_session.execute(select(CommercialRefund)).scalars().all()]


def test_a_refund_29_days_23_hours_after_the_payment_is_allowed(app, seeded):
    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1", confirmed_at=datetime.now(UTC) - timedelta(days=29, hours=23))["order_id"]
    _refund(app, w, sale, "250.00")
    assert _refunds(app) == ["PAID"]


def test_a_refund_requested_30_days_and_a_minute_after_the_payment_is_refused(app, seeded):
    from app.commercial_sales.errors import CommercialSalesError

    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1", confirmed_at=datetime.now(UTC) - timedelta(days=30, minutes=1))["order_id"]
    with pytest.raises(CommercialSalesError) as exc:
        _create(app, w, sale)
    assert exc.value.code == "REFUND_WINDOW_CLOSED"
    assert _refunds(app) == []


def test_a_refund_requested_in_time_but_paid_out_after_the_30_days_is_refused(app, seeded):
    """Requested on day 29, approved, then the 30 days pass before it is paid
    out: confirm_refund refuses, and nothing moves -- the refund stays
    APPROVED, the invoice PAID, the licence without a stop."""
    from app.commercial_sales.errors import CommercialSalesError

    w = _world(app, rule="PERCENT")
    sale = _sell(app, w, "p1", confirmed_at=datetime.now(UTC) - timedelta(days=29))["order_id"]
    refund_id = _create(app, w, sale)
    rows = _rows(app, sale)
    with app.app_context():
        from app.commercial_sales.refunds import approve_refund
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialRefund

        approve_refund(db_session.get(CommercialRefund, refund_id), actor_staff_user_id=w["admin"])
        # two days pass: the payment was confirmed (and its sale finished) 31 days ago
        moved = datetime.now(UTC) - timedelta(days=31)
        db_session.execute(text("UPDATE owner_payment_correction_history SET created_at = :t WHERE new_status = 'CONFIRMED'"),
                           {"t": moved})
        db_session.execute(text("UPDATE owner_payment_allocations SET allocated_at = :t"), {"t": moved})
        db_session.commit()
    with app.app_context():
        from app.commercial_sales.refunds import confirm_refund
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice, CommercialRefund
        from app.models.licensing import LicenseScheduledStop

        with pytest.raises(CommercialSalesError) as exc:
            confirm_refund(db_session.get(CommercialRefund, refund_id), actor_staff_user_id=w["admin"])
        assert exc.value.code == "REFUND_WINDOW_CLOSED"
        db_session.rollback()
        assert db_session.get(CommercialRefund, refund_id).status == "APPROVED"
        assert db_session.get(CommercialInvoice, rows["invoice_id"]).status == "PAID"
        assert db_session.execute(select(LicenseScheduledStop)).scalars().all() == []


def test_the_refund_window_ends_exactly_when_the_commission_hold_ends(app, seeded):
    """Same function, same constant: for a counted ladder sale the last
    refundable moment IS its hold end."""
    w = _world(app)
    sale = _sell(app, w, "p1", confirmed_at=datetime.now(UTC) - timedelta(days=3, hours=5))["order_id"]
    [counted] = _ladder_sales(app, w["seller_profile"])
    with app.app_context():
        from app.commissions.ladder import refund_window_ends_at
        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice

        invoice = db_session.get(CommercialInvoice, _rows(app, sale)["invoice_id"])
        assert refund_window_ends_at(invoice) == counted["hold_ends_at"]


def test_the_refusal_is_translated(app, seeded):
    with app.test_request_context():
        from flask_babel import force_locale

        from app.i18n_labels import localize_commercial_sales_error

        with force_locale("ar"):
            arabic = localize_commercial_sales_error("REFUND_WINDOW_CLOSED")
        with force_locale("en"):
            english = localize_commercial_sales_error("REFUND_WINDOW_CLOSED")
    assert english == "Refunds are not possible once 30 days have passed since the payment was confirmed."
    assert arabic == "لا يمكن الاسترداد بعد مرور 30 يومًا على تأكيد الدفعة."
