"""Two-person rule on the LEGACY payment paths (Step 0, payment bug 2).

commercial_sales/payments.py::confirm_payment already refuses to let the
staff member who recorded a payment also confirm it
(SELF_CONFIRMATION_FORBIDDEN, tests/test_phase9_5d_payments.py). But it is
not the only way a PaymentRecord reaches CONFIRMED. The two storage
functions underneath it -- subscriptions/services.py::record_payment and
::correct_payment -- are ALSO reachable directly, through the older
subscriptions routes, and neither checked anything:

  A. POST /subscriptions/payments took `status` straight from the form, and
     its gate is payments.create -- which SALES holds (seed_data.py,
     Milestone 18). A salesperson could record a payment that was
     CONFIRMED from birth: no second person at all, verified_by NULL.
  B. POST /subscriptions/payments/<id>/correct with status=CONFIRMED by the
     person who recorded it (FINANCE holds payments.create AND
     payments.correct) left verified_by == recorded_by.
  C. The same route, used by the recorder to re-save an ALREADY confirmed
     payment (say, to edit its note), overwrote the real checker's
     verified_by with the recorder's own id.

Confirmed payments are what cash closing, the dashboards, the operational
reports and invoice allocation all count as real money (grep
`status == "CONFIRMED"` under app/), so each of these let one person turn a
claim of money into counted money alone.

The fix sits in the two storage functions themselves -- the only two
writers of PaymentRecord.status anywhere in app/ -- so every caller, old or
new, gets the rule; the route adds the payments.confirm check that
payments.correct alone must not stand in for.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import force_login, get_csrf, make_staff


def _make_subscription(app, staff_id):
    from app.extensions import db_session
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription

    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        plan = Plan(plan_code="TWOPERSON1", product_id=product.id, name="TWOPERSON1", billing_model="MONTHLY", currency="USD")
        customer = Customer(legal_name="Two Person Rule Co")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)
        return sub.id, customer.id


def _record_pending(app, staff_id, sub_id, customer_id, reference="TPR-1"):
    from app.subscriptions.services import record_payment

    with app.app_context():
        payment = record_payment(
            {
                "customer_id": customer_id, "subscription_id": sub_id, "amount": Decimal("75.00"),
                "currency": "USD", "payment_date": date.today(), "status": "PENDING", "reference": reference,
            },
            staff_id,
        )
        return payment.id


def _payment(app, payment_id):
    from app.extensions import db_session
    from app.models.subscriptions import PaymentRecord

    with app.app_context():
        db_session.expire_all()
        p = db_session.get(PaymentRecord, payment_id)
        return {
            "status": p.status, "verified_by": p.verified_by_staff_user_id,
            "recorded_by": p.recorded_by_staff_user_id, "history": len(p.correction_history),
        }


def _confirmed_count(app):
    from app.extensions import db_session
    from app.models.subscriptions import PaymentRecord

    with app.app_context():
        return db_session.query(PaymentRecord).filter_by(status="CONFIRMED").count()


# -- service layer: deny halves --------------------------------------------

def test_record_payment_refuses_to_create_an_already_confirmed_payment(app, seeded):
    """Path A at its root: recording is never confirming (Non-Negotiable
    Rule 4). No caller may create a PaymentRecord that is CONFIRMED from
    birth -- that is a confirmation with no checker at all."""
    staff_id = make_staff(app, "tpr-a@example.com", role_codes=["SALES"])
    sub_id, customer_id = _make_subscription(app, staff_id)
    with app.app_context():
        from app.subscriptions.services import PaymentSelfVerificationError, record_payment

        with pytest.raises(PaymentSelfVerificationError):
            record_payment(
                {
                    "customer_id": customer_id, "subscription_id": sub_id, "amount": Decimal("75.00"),
                    "currency": "USD", "payment_date": date.today(), "status": "CONFIRMED",
                },
                staff_id,
            )
    assert _confirmed_count(app) == 0


def test_correct_payment_refuses_the_recorder_confirming_their_own_payment(app, seeded):
    """Path B at its root."""
    staff_id = make_staff(app, "tpr-b@example.com", role_codes=["FINANCE"])
    sub_id, customer_id = _make_subscription(app, staff_id)
    payment_id = _record_pending(app, staff_id, sub_id, customer_id)
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord
        from app.subscriptions.services import PaymentSelfVerificationError, correct_payment

        payment = db_session.get(PaymentRecord, payment_id)
        with pytest.raises(PaymentSelfVerificationError):
            correct_payment(payment, "CONFIRMED", "I checked it myself", staff_id)
        db_session.rollback()

    after = _payment(app, payment_id)
    assert after["status"] == "PENDING"
    assert after["verified_by"] is None
    assert after["history"] == 0  # a refused correction writes no history row either


def test_recorder_cannot_resave_a_confirmed_payment_as_its_own_verifier(app, seeded):
    """Path C: B really confirmed it; A (the recorder) re-saving it as
    CONFIRMED must not replace B as the verifier."""
    staff_a = make_staff(app, "tpr-c-a@example.com", role_codes=["FINANCE"])
    staff_b = make_staff(app, "tpr-c-b@example.com", role_codes=["FINANCE"])
    sub_id, customer_id = _make_subscription(app, staff_a)
    payment_id = _record_pending(app, staff_a, sub_id, customer_id)
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord
        from app.subscriptions.services import PaymentSelfVerificationError, correct_payment

        payment = db_session.get(PaymentRecord, payment_id)
        correct_payment(payment, "CONFIRMED", "bank statement checked", staff_b)
        with pytest.raises(PaymentSelfVerificationError):
            correct_payment(payment, "CONFIRMED", "just editing the note", staff_a)
        db_session.rollback()

    after = _payment(app, payment_id)
    assert after["status"] == "CONFIRMED"
    assert after["verified_by"] == staff_b
    assert after["history"] == 1


# -- service layer: allow halves -------------------------------------------

def test_a_different_staff_member_can_confirm_through_correct_payment(app, seeded):
    staff_a = make_staff(app, "tpr-d-a@example.com", role_codes=["FINANCE"])
    staff_b = make_staff(app, "tpr-d-b@example.com", role_codes=["FINANCE"])
    sub_id, customer_id = _make_subscription(app, staff_a)
    payment_id = _record_pending(app, staff_a, sub_id, customer_id)
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord
        from app.subscriptions.services import correct_payment

        correct_payment(db_session.get(PaymentRecord, payment_id), "CONFIRMED", "bank statement checked", staff_b)

    after = _payment(app, payment_id)
    assert after["status"] == "CONFIRMED"
    assert after["verified_by"] == staff_b
    assert after["recorded_by"] == staff_a


def test_the_recorder_can_still_void_or_fail_their_own_payment(app, seeded):
    """The rule is about CONFIRMING. Withdrawing your own mistaken entry
    (VOIDED) or marking it FAILED never turns a claim into counted money, so
    it stays allowed -- refusing it would only push people to ask someone
    else to undo their typo."""
    staff_id = make_staff(app, "tpr-e@example.com", role_codes=["FINANCE"])
    sub_id, customer_id = _make_subscription(app, staff_id)
    payment_id = _record_pending(app, staff_id, sub_id, customer_id)
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord
        from app.subscriptions.services import correct_payment

        correct_payment(db_session.get(PaymentRecord, payment_id), "VOIDED", "entered twice by mistake", staff_id)

    assert _payment(app, payment_id)["status"] == "VOIDED"


def test_record_payment_still_creates_pending_and_failed_records(app, seeded):
    staff_id = make_staff(app, "tpr-f@example.com", role_codes=["SALES"])
    sub_id, customer_id = _make_subscription(app, staff_id)
    with app.app_context():
        from app.subscriptions.services import record_payment

        for status in ("PENDING", "FAILED"):
            p = record_payment(
                {
                    "customer_id": customer_id, "subscription_id": sub_id, "amount": Decimal("10.00"),
                    "currency": "USD", "payment_date": date.today(), "status": status,
                },
                staff_id,
            )
            assert p.status == status


# -- legacy routes -----------------------------------------------------------

def _csrf_for(client, sub_id):
    return get_csrf(client.get(f"/subscriptions/{sub_id}").get_data(as_text=True))


def test_legacy_record_route_refuses_a_born_confirmed_payment_from_sales(app, client, seeded):
    """Path A end to end, as the real role that could do it: SALES holds
    payments.create. The response is a clear 400, not a 500, and no
    CONFIRMED row exists afterwards."""
    staff_id = make_staff(app, "tpr-g@example.com", role_codes=["SALES"])
    sub_id, customer_id = _make_subscription(app, staff_id)
    force_login(client, app, staff_id)
    resp = client.post(
        "/subscriptions/payments",
        data={
            "csrf_token": _csrf_for(client, sub_id), "customer_id": str(customer_id), "subscription_id": str(sub_id),
            "amount": "75.00", "currency": "USD", "payment_date": date.today().isoformat(), "status": "CONFIRMED",
        },
    )
    assert resp.status_code == 400
    assert _confirmed_count(app) == 0


def test_legacy_record_route_still_records_a_pending_payment(app, client, seeded):
    staff_id = make_staff(app, "tpr-h@example.com", role_codes=["SALES"])
    sub_id, customer_id = _make_subscription(app, staff_id)
    force_login(client, app, staff_id)
    resp = client.post(
        "/subscriptions/payments",
        data={
            "csrf_token": _csrf_for(client, sub_id), "customer_id": str(customer_id), "subscription_id": str(sub_id),
            "amount": "75.00", "currency": "USD", "payment_date": date.today().isoformat(), "status": "PENDING",
        },
    )
    assert resp.status_code == 302
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        assert db_session.query(PaymentRecord).filter_by(status="PENDING", subscription_id=sub_id).count() == 1


@pytest.mark.parametrize(
    "bad_field",
    [
        {"amount": "seventy-five"},  # decimal.InvalidOperation is not a ValueError: it escaped as a 500
        {"amount": "NaN"},  # parses as a Decimal, and Postgres NUMERIC stores it: a NaN payment
        {"amount": "Infinity"},
        {"payment_date": None},  # date.fromisoformat(None) is a TypeError: a 500
        {"payment_date": "24/10/2026"},
    ],
    ids=["amount-words", "amount-nan", "amount-infinity", "date-missing", "date-wrong-format"],
)
def test_legacy_record_route_with_invalid_amount_or_date_is_a_400_not_a_crash(app, client, seeded, bad_field):
    """Fix round 2: the record route only handled the refusals record_payment
    raises. A mistyped amount or a missing/garbled date failed while
    building the record -- before record_payment ran -- and was a 500. Now a
    clear 400, and nothing is recorded."""
    staff_id = make_staff(app, "tpr-n@example.com", role_codes=["FINANCE"])
    sub_id, customer_id = _make_subscription(app, staff_id)
    force_login(client, app, staff_id)
    form = {
        "csrf_token": _csrf_for(client, sub_id), "customer_id": str(customer_id), "subscription_id": str(sub_id),
        "amount": "75.00", "currency": "USD", "payment_date": date.today().isoformat(), "status": "PENDING",
    }
    form.update(bad_field)
    form = {k: v for k, v in form.items() if v is not None}
    resp = client.post("/subscriptions/payments", data=form)
    assert resp.status_code == 400
    assert resp.get_json()["error"]
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        assert db_session.query(PaymentRecord).filter_by(subscription_id=sub_id).count() == 0


def test_record_form_no_longer_offers_confirmed(app, client, seeded):
    """The record form used to offer CONFIRMED as a starting status -- an
    option the service now refuses. Offering it would only produce errors."""
    staff_id = make_staff(app, "tpr-i@example.com", role_codes=["FINANCE"])
    sub_id, _ = _make_subscription(app, staff_id)
    force_login(client, app, staff_id)
    body = client.get(f"/subscriptions/{sub_id}").get_data(as_text=True)
    record_form = body.split('id="subscriptions-detail-status"', 1)[1].split("</select>", 1)[0]
    assert 'value="CONFIRMED"' not in record_form
    assert 'value="PENDING"' in record_form


def test_legacy_correct_route_refuses_self_confirmation(app, client, seeded):
    """Path B end to end: 403, payment untouched."""
    staff_id = make_staff(app, "tpr-j@example.com", role_codes=["FINANCE"])
    sub_id, customer_id = _make_subscription(app, staff_id)
    payment_id = _record_pending(app, staff_id, sub_id, customer_id)
    force_login(client, app, staff_id)
    resp = client.post(
        f"/subscriptions/payments/{payment_id}/correct",
        data={"csrf_token": _csrf_for(client, sub_id), "status": "CONFIRMED", "note": "mine, checked by me"},
    )
    assert resp.status_code == 403
    after = _payment(app, payment_id)
    assert after["status"] == "PENDING"
    assert after["verified_by"] is None


def test_legacy_correct_route_lets_a_different_finance_member_confirm(app, client, seeded):
    staff_a = make_staff(app, "tpr-k-a@example.com", role_codes=["FINANCE"])
    staff_b = make_staff(app, "tpr-k-b@example.com", role_codes=["FINANCE"])
    sub_id, customer_id = _make_subscription(app, staff_a)
    payment_id = _record_pending(app, staff_a, sub_id, customer_id)
    force_login(client, app, staff_b)
    resp = client.post(
        f"/subscriptions/payments/{payment_id}/correct",
        data={"csrf_token": _csrf_for(client, sub_id), "status": "CONFIRMED", "note": "bank statement checked"},
    )
    assert resp.status_code == 302
    after = _payment(app, payment_id)
    assert after["status"] == "CONFIRMED"
    assert after["verified_by"] == staff_b


def _make_corrector_without_confirm(app, email):
    """A custom role holding payments.correct but NOT payments.confirm. No
    seeded role has that shape today (FINANCE holds both), but roles are
    data an admin can create -- the route must not treat payments.correct as
    a side door around payments.confirm."""
    from app.extensions import db_session
    from app.models.staff import Permission, Role, RolePermission, StaffRoleAssignment

    staff_id = make_staff(app, email)
    with app.app_context():
        role = Role(code="PAYMENT_CLERK", name="Payment clerk", description="test-only", is_system_role=False)
        db_session.add(role)
        db_session.flush()
        for code in ("payments.correct", "payments.view", "subscriptions.view"):
            perm = db_session.query(Permission).filter_by(code=code).one()
            db_session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db_session.add(StaffRoleAssignment(staff_user_id=staff_id, role_id=role.id))
        db_session.commit()
    return staff_id


def test_correct_route_into_confirmed_needs_payments_confirm(app, client, seeded):
    recorder = make_staff(app, "tpr-l-rec@example.com", role_codes=["FINANCE"])
    clerk = _make_corrector_without_confirm(app, "tpr-l-clerk@example.com")
    sub_id, customer_id = _make_subscription(app, recorder)
    payment_id = _record_pending(app, recorder, sub_id, customer_id)
    force_login(client, app, clerk)
    csrf = _csrf_for(client, sub_id)

    # Deny half: a different person, but without the confirm authority.
    resp = client.post(
        f"/subscriptions/payments/{payment_id}/correct",
        data={"csrf_token": csrf, "status": "CONFIRMED", "note": "looks fine"},
    )
    assert resp.status_code == 403
    assert _payment(app, payment_id)["status"] == "PENDING"

    # Allow half: the same clerk can still make a non-confirming correction.
    resp = client.post(
        f"/subscriptions/payments/{payment_id}/correct",
        data={"csrf_token": csrf, "status": "FAILED", "note": "cheque bounced"},
    )
    assert resp.status_code == 302
    assert _payment(app, payment_id)["status"] == "FAILED"


def test_correct_route_with_an_invalid_status_is_a_400_not_a_crash(app, client, seeded):
    staff_a = make_staff(app, "tpr-m-a@example.com", role_codes=["FINANCE"])
    staff_b = make_staff(app, "tpr-m-b@example.com", role_codes=["FINANCE"])
    sub_id, customer_id = _make_subscription(app, staff_a)
    payment_id = _record_pending(app, staff_a, sub_id, customer_id)
    force_login(client, app, staff_b)
    resp = client.post(
        f"/subscriptions/payments/{payment_id}/correct",
        data={"csrf_token": _csrf_for(client, sub_id), "status": "NOT_A_STATUS", "note": "x"},
    )
    assert resp.status_code == 400
    assert _payment(app, payment_id)["status"] == "PENDING"
