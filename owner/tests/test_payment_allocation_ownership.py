"""Step 0 fix round 4 (2026-10-24) -- two money holes left open by 69608e4b.

  H1  a payment recorded FOR a sale could be allocated to a DIFFERENT
      invoice of the same customer (the invoice page's Allocate button, the
      API's /allocations), so the commission went to that invoice's creator.
      Two shapes: a one-screen sale's payment (linked to its order through
      the DIRECT_SALE_PAYMENT ledger row) and a quote-pipeline payment that
      names its invoice (PaymentRecord.commercial_invoice_id). The second is
      also a two-person-rule side door: the confirmer is checked against the
      NAMED invoice's seller, so the seller of another invoice could confirm
      the payment and then allocate it to their own invoice.
  H2  the legacy correct route (/subscriptions/payments/<id>/correct) could
      move a CONFIRMED payment to FAILED/VOIDED/REFUNDED/PENDING while a live
      allocation of it -- and the commission and licence that came from it
      -- stood. The company would then show a licence and a commission paid
      for money its own books say never arrived.

Every refusal test asserts what was (not) written, not just the code; every
guard has its allow half next to it.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import force_login, get_csrf, make_staff
from tests.test_issuance_sale import _confirm, _profile, _record, _world


# ---------------------------------------------------------------- helpers --

def _payment(payment_id):
    from app.extensions import db_session
    from app.models.subscriptions import PaymentRecord

    return db_session.get(PaymentRecord, payment_id)


def _invoice(invoice_id):
    from app.extensions import db_session
    from app.models.commercial_sales import CommercialInvoice

    return db_session.get(CommercialInvoice, invoice_id)


def _give_commission_plan(app, w, profile_id):
    with app.app_context():
        from app.commissions.management import (
            assign_employee_commission_plan,
            create_commission_plan,
            create_commission_rule_version,
        )

        plan = create_commission_plan({"plan_code": f"C_{uuid.uuid4().hex[:6]}", "name": "Others"}, w["admin"])
        create_commission_rule_version(
            plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=Decimal("10.0"),
            effective_from=date.today() - timedelta(days=1), actor_staff_user_id=w["admin"],
        )
        assign_employee_commission_plan(profile_id, plan, effective_from=date.today() - timedelta(days=1),
                                        actor_staff_user_id=w["admin"])


def _other_seller(app, w):
    """A second seller with their own 10% commission plan."""
    staff = make_staff(app, f"other-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES"], mfa=True)
    profile = _profile(app, staff, f"EMP-O-{uuid.uuid4().hex[:4]}", "Omar Other")
    _give_commission_plan(app, w, profile)
    return staff, profile


def _pipeline_invoice(app, w, customer_id, *, creator_staff, creator_profile):
    """The quote pipeline up to an ISSUED 100.00 JOD invoice for customer_id,
    created (and so credited for commission) by creator_profile."""
    with app.app_context():
        from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote

        quote = create_quote({"customer_id": customer_id, "currency": "JOD"},
                             actor_employee_profile_id=creator_profile, actor_staff_user_id=creator_staff)
        add_quote_line(quote, plan_id=w["plan"], addon_id=None, quantity=1, actor_staff_user_id=creator_staff)
        submit_quote(quote, actor_staff_user_id=creator_staff)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=creator_staff)
        order = create_order_from_quote(quote, actor_employee_profile_id=creator_profile,
                                        actor_staff_user_id=creator_staff, idempotency_key=str(uuid.uuid4()))
        confirm_order(order, actor_staff_user_id=w["admin"])
        invoice = create_invoice_from_order(order, actor_employee_profile_id=creator_profile,
                                            actor_staff_user_id=creator_staff, idempotency_key=str(uuid.uuid4()))
        issue_invoice(invoice, actor_staff_user_id=w["admin"])
        return invoice.id


def _sale_with_confirmed_payment(app, w):
    """A one-screen sale whose payment a manager confirmed on the payments
    screen (so it is CONFIRMED and not yet allocated anywhere)."""
    result, _key = _record(app, w)
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment

        confirm_payment(_payment(result["payment_id"]), actor_staff_user_id=w["manager"])
    return result


def _commission_total(app, profile_id) -> Decimal:
    with app.app_context():
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        return Decimal(db_session.execute(
            select(func.coalesce(func.sum(CommissionLedgerEntry.commission_amount), 0))
            .where(CommissionLedgerEntry.employee_profile_id == profile_id)
        ).scalar_one())


def _live_allocations(app, payment_id):
    """[(invoice_id, amount)] of the payment's non-reversed allocations."""
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import PaymentAllocation

        rows = db_session.execute(
            select(PaymentAllocation).where(
                PaymentAllocation.payment_record_id == payment_id, PaymentAllocation.reversed_at.is_(None)
            )
        ).scalars().all()
        return [(r.commercial_invoice_id, Decimal(r.allocated_amount)) for r in rows]


def _try_allocate(app, payment_id, invoice_id, actor, amount=Decimal("100.00")) -> str:
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment
        from app.commercial_sales.errors import CommercialSalesError

        try:
            allocate_payment(payment=_payment(payment_id), invoice=_invoice(invoice_id), amount=amount,
                             actor_staff_user_id=actor)
        except CommercialSalesError as exc:
            return exc.code
        return "allocated"


def _try_correct(app, payment_id, new_status, actor, note="bank says it bounced") -> str:
    with app.app_context():
        from app.subscriptions.services import correct_payment

        try:
            correct_payment(_payment(payment_id), new_status, note, actor)
        except ValueError as exc:
            return getattr(exc, "code", type(exc).__name__)
        return "corrected"


def _payment_state(app, payment_id):
    """(status, number of correction-history rows)."""
    with app.app_context():
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.commercial_ops import PaymentCorrectionHistory

        history = db_session.execute(
            select(func.count()).select_from(PaymentCorrectionHistory)
            .where(PaymentCorrectionHistory.payment_record_id == payment_id)
        ).scalar_one()
        return _payment(payment_id).status, history


def _sale_invoice_id(app, order_id):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice

        return db_session.execute(
            select(CommercialInvoice.id).where(CommercialInvoice.sales_order_id == order_id)
        ).scalar_one()


# --------------------------- H1: a sale's payment stays on its own invoice --

def test_one_screen_sale_payment_cannot_be_allocated_to_another_invoice(app, seeded):
    """RED on 69608e4b: the manager allocated the sale's payment to another
    seller's invoice of the same customer and that seller was credited the
    commission (10.00) for a sale they did not make."""
    w = _world(app)
    sale = _sale_with_confirmed_payment(app, w)
    other_staff, other_profile = _other_seller(app, w)
    other_invoice = _pipeline_invoice(app, w, sale["customer_id"], creator_staff=other_staff,
                                      creator_profile=other_profile)

    outcome = _try_allocate(app, sale["payment_id"], other_invoice, w["manager"])

    assert (outcome, _live_allocations(app, sale["payment_id"]), _commission_total(app, other_profile)) == (
        "PAYMENT_BELONGS_TO_ANOTHER_SALE", [], Decimal("0"))
    with app.app_context():
        assert _invoice(other_invoice).status == "ISSUED"


def test_one_screen_sale_still_finishes_on_its_own_invoice(app, seeded):
    """Allow half: the guard must let the sale's OWN invoice take the payment
    -- confirm_direct_sale allocates through the same allocate_payment --
    also after a redirect attempt was refused."""
    w = _world(app)
    sale = _sale_with_confirmed_payment(app, w)
    other_staff, other_profile = _other_seller(app, w)
    other_invoice = _pipeline_invoice(app, w, sale["customer_id"], creator_staff=other_staff,
                                      creator_profile=other_profile)
    assert _try_allocate(app, sale["payment_id"], other_invoice, w["manager"]) == "PAYMENT_BELONGS_TO_ANOTHER_SALE"

    finished = _confirm(app, sale["order_id"], w["manager"])

    assert finished["full_key"] and finished["full_key"].startswith("AURA-")
    own_invoice = _sale_invoice_id(app, sale["order_id"])
    assert _live_allocations(app, sale["payment_id"]) == [(own_invoice, Decimal("100.00"))]
    assert _commission_total(app, w["seller_profile"]) == Decimal("10.00")
    assert _commission_total(app, other_profile) == Decimal("0")


def test_invoice_page_allocate_button_refuses_a_sale_payment_in_arabic(app, client, seeded):
    """RED on 69608e4b: the invoice page's Allocate button allocated it (302
    back with no error). Now: back with the code, the page shows the
    translated message, nothing allocated."""
    w = _world(app)
    sale = _sale_with_confirmed_payment(app, w)
    other_staff, other_profile = _other_seller(app, w)
    other_invoice = _pipeline_invoice(app, w, sale["customer_id"], creator_staff=other_staff,
                                      creator_profile=other_profile)
    force_login(client, app, w["manager"])
    client.get("/locale/ar?next=/")
    page = client.get(f"/invoices/{other_invoice}")
    assert page.status_code == 200

    resp = client.post(f"/invoices/{other_invoice}/allocate", data={
        "csrf_token": get_csrf(page.get_data(as_text=True)),
        "payment_record_id": str(sale["payment_id"]), "amount": "100.00",
    })

    assert resp.status_code == 302
    assert "error=PAYMENT_BELONGS_TO_ANOTHER_SALE" in resp.headers["Location"]
    assert _live_allocations(app, sale["payment_id"]) == []
    shown = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "تخص هذه الدفعة عملية بيع أخرى" in shown


def test_pipeline_payment_named_for_one_invoice_cannot_go_to_another(app, seeded):
    """RED on 69608e4b, the quote-pipeline shape of H1 and its two-person
    side door: the seller S records a payment FOR invoice A; a manager who
    is not S confirms it (allowed -- they are not A's seller), then allocates
    it to invoice B that the MANAGER created, crediting themselves the
    commission for money they checked."""
    w = _world(app)
    _give_commission_plan(app, w, w["manager_profile"])
    with app.app_context():
        from app.customers.services import create_customer

        customer_id = create_customer({"legal_name": "Pipeline Shop"}, w["seller"]).id
    invoice_a = _pipeline_invoice(app, w, customer_id, creator_staff=w["seller"], creator_profile=w["seller_profile"])
    invoice_b = _pipeline_invoice(app, w, customer_id, creator_staff=w["manager"], creator_profile=w["manager_profile"])
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment, submit_payment

        payment = submit_payment(customer_id=customer_id, amount=Decimal("100.00"), currency="JOD",
                                 method="BANK_TRANSFER", payment_date=date.today(), commercial_invoice_id=invoice_a,
                                 reference="TRX-1", actor_staff_user_id=w["seller"])
        confirm_payment(payment, actor_staff_user_id=w["manager"])
        payment_id = payment.id

    outcome = _try_allocate(app, payment_id, invoice_b, w["manager"])

    assert (outcome, _live_allocations(app, payment_id), _commission_total(app, w["manager_profile"])) == (
        "PAYMENT_BELONGS_TO_ANOTHER_INVOICE", [], Decimal("0"))

    # Allow half: the invoice it was recorded for takes it, and S is paid.
    assert _try_allocate(app, payment_id, invoice_a, w["manager"]) == "allocated"
    assert _live_allocations(app, payment_id) == [(invoice_a, Decimal("100.00"))]
    assert _commission_total(app, w["seller_profile"]) == Decimal("10.00")


def test_payment_tied_to_no_invoice_or_sale_can_still_go_to_any_invoice_of_its_customer(app, seeded):
    """Allow half: a customer payment recorded against no invoice and no
    sale (one transfer for several invoices, say) is still allocated by
    the manager wherever it belongs -- the guard keys on the payment's own
    link, it does not forbid allocation in general."""
    w = _world(app)
    with app.app_context():
        from app.customers.services import create_customer

        customer_id = create_customer({"legal_name": "Free Payment Shop"}, w["seller"]).id
    invoice = _pipeline_invoice(app, w, customer_id, creator_staff=w["seller"], creator_profile=w["seller_profile"])
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment, submit_payment

        payment = submit_payment(customer_id=customer_id, amount=Decimal("100.00"), currency="JOD",
                                 method="BANK_TRANSFER", payment_date=date.today(), reference="TRX-2",
                                 actor_staff_user_id=w["seller"])
        confirm_payment(payment, actor_staff_user_id=w["manager"])
        payment_id = payment.id

    assert _try_allocate(app, payment_id, invoice, w["manager"]) == "allocated"
    assert _live_allocations(app, payment_id) == [(invoice, Decimal("100.00"))]


# ------------------- H2: confirmed allocated money cannot be un-confirmed --

@pytest.mark.parametrize("new_status", ["FAILED", "VOIDED", "REFUNDED", "PENDING"])
def test_allocated_confirmed_payment_cannot_leave_confirmed(app, seeded, new_status):
    """RED on 69608e4b: a finished sale's payment (allocated, commission
    posted, licence issued) was set to new_status by the correct route's
    service; licence and commission stayed."""
    w = _world(app)
    sale, _key = _record(app, w)
    _confirm(app, sale["order_id"], w["manager"])
    corrector = make_staff(app, f"fin-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    before = _payment_state(app, sale["payment_id"])

    outcome = _try_correct(app, sale["payment_id"], new_status, corrector)

    assert (outcome, _payment_state(app, sale["payment_id"])) == ("PAYMENT_ALLOCATED", before)
    assert before[0] == "CONFIRMED"


def test_correct_route_refuses_unconfirming_allocated_money_in_arabic(app, client, seeded):
    """RED on 69608e4b: 302 and the payment FAILED, licence and commission
    left standing. Now 409 with the translated reason, payment untouched."""
    w = _world(app)
    sale, _key = _record(app, w)
    _confirm(app, sale["order_id"], w["manager"])
    before = _payment_state(app, sale["payment_id"])
    force_login(client, app, w["manager"])
    client.get("/locale/ar?next=/")
    page = client.get(f"/payments/{sale['payment_id']}")
    assert page.status_code == 200

    resp = client.post(f"/subscriptions/payments/{sale['payment_id']}/correct", data={
        "csrf_token": get_csrf(page.get_data(as_text=True)), "status": "FAILED", "note": "bounced",
    })

    assert resp.status_code == 409, resp.get_data(as_text=True)[:500]
    body = resp.get_json()
    assert body["error"] == "payment_allocated"
    assert "ألغِ التخصيص" in body["message"]
    assert _payment_state(app, sale["payment_id"]) == before
    assert _commission_total(app, w["seller_profile"]) == Decimal("10.00")


def test_confirmed_payment_with_no_allocation_can_still_be_corrected(app, seeded):
    """Allow half: a confirmed payment nobody allocated yet (a duplicate
    entry, say) can still be voided."""
    w = _world(app)
    sale = _sale_with_confirmed_payment(app, w)
    status_before, history_before = _payment_state(app, sale["payment_id"])
    assert status_before == "CONFIRMED"

    assert _try_correct(app, sale["payment_id"], "VOIDED", w["admin"], note="duplicate entry") == "corrected"
    assert _payment_state(app, sale["payment_id"]) == ("VOIDED", history_before + 1)


def test_reverse_the_allocation_then_the_payment_can_be_corrected(app, seeded):
    """Allow half: the path the refusal message names works -- reverse the
    allocation on the invoice page, then correct the payment."""
    w = _world(app)
    sale, _key = _record(app, w)
    _confirm(app, sale["order_id"], w["manager"])
    _status, history_before = _payment_state(app, sale["payment_id"])
    with app.app_context():
        from sqlalchemy import select

        from app.commercial_sales.allocation import reverse_allocation
        from app.extensions import db_session
        from app.models.commercial_sales import PaymentAllocation

        allocation = db_session.execute(
            select(PaymentAllocation).where(PaymentAllocation.payment_record_id == sale["payment_id"])
        ).scalars().one()
        reverse_allocation(allocation, reason="transfer was recalled by the bank", actor_staff_user_id=w["manager"])

    assert _try_correct(app, sale["payment_id"], "FAILED", w["admin"]) == "corrected"
    assert _payment_state(app, sale["payment_id"]) == ("FAILED", history_before + 1)


def test_allocated_confirmed_payment_can_still_have_its_note_corrected(app, seeded):
    """Allow half: the guard is about LEAVING CONFIRMED. Re-saving an
    allocated payment as CONFIRMED (to fix its note) by someone other than
    its recorder or seller still works."""
    w = _world(app)
    sale, _key = _record(app, w)
    _confirm(app, sale["order_id"], w["manager"])
    _status, history_before = _payment_state(app, sale["payment_id"])

    assert _try_correct(app, sale["payment_id"], "CONFIRMED", w["admin"], note="CliQ ref re-checked") == "corrected"
    assert _payment_state(app, sale["payment_id"]) == ("CONFIRMED", history_before + 1)
