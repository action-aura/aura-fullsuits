"""Step 0 (2026-10-24) -- the one-screen issuance records the SALE.

Before (measured on 829c4cde): the one-screen issuance issued a licence at
once with no PaymentRecord, no SalesOrder and no sales owner, so a licence
sold that way was tied to no money and earned its seller no commission.

The owner's rules this file holds the code to (Aura Growth Plan, "Your
answers", 2026-10-24): sellers are registered employees and never take
cash; a manager -- not the seller, not whoever recorded it -- confirms each
payment; the licence key is issued only after that; commission goes to the
sale's seller, counted from the payment-confirmed date.

Every test asserts ROW COUNTS and WHO, not just status codes: a 400/403
cannot tell "refused before any write" from "refused after half the rows".
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff

PEPPER = "test-pepper"


# ------------------------------------------------------------------ setup --

def _profile(app, staff_id, number, name):
    with app.app_context():
        from app.employees.services import create_employee_profile

        profile = create_employee_profile(
            {"staff_user_id": staff_id, "employee_number": number, "full_name": name,
             "employment_start_date": date(2026, 1, 1), "employment_status": "ACTIVE"},
            actor_staff_user_id=staff_id,
        )
        return profile.id


def _world(app, *, plan_price=Decimal("100.00"), extra_device_price=Decimal("50.00"), billing_model="ONE_TIME",
           billing_interval_months=None, max_device_count=None, commission_rate=Decimal("10.0")):
    """A seller (SALES), a manager (FINANCE), a Super Admin, each with an
    ACTIVE employee profile; a sellable Aura Retail package priced in JOD;
    the seeded EXTRA_DEVICE add-on made sellable; a 10% commission plan
    assigned to the seller."""
    w = {}
    w["seller"] = make_staff(app, f"seller-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES"], mfa=True)
    w["manager"] = make_staff(app, f"manager-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    w["admin"] = make_staff(app, f"admin-{uuid.uuid4().hex[:6]}@example.com", super_admin=True, mfa=True)
    w["seller_profile"] = _profile(app, w["seller"], f"EMP-S-{uuid.uuid4().hex[:4]}", "Sami Seller")
    w["manager_profile"] = _profile(app, w["manager"], f"EMP-M-{uuid.uuid4().hex[:4]}", "Mona Manager")
    w["admin_profile"] = _profile(app, w["admin"], f"EMP-A-{uuid.uuid4().hex[:4]}", "Adam Admin")
    with app.app_context():
        from sqlalchemy import select

        from app.catalog.services import add_plan_price, create_plan
        from app.commissions.management import (
            assign_employee_commission_plan,
            create_commission_plan,
            create_commission_rule_version,
        )
        from app.extensions import db_session
        from app.models.catalog import Addon, Product

        product = db_session.execute(select(Product).where(Product.product_code == "AURA_RETAIL")).scalars().one()
        product.is_sellable = True
        product.is_active = True
        addon = db_session.execute(select(Addon).where(Addon.addon_code == "EXTRA_DEVICE")).scalars().one()
        if extra_device_price is not None:
            addon.availability_status = "AVAILABLE"
            addon.price = extra_device_price
            addon.currency = "JOD"
        db_session.commit()
        plan = create_plan(
            {"plan_code": f"SALE_{uuid.uuid4().hex[:6]}", "product_id": product.id, "name": "Aura Retail Yearly",
             "billing_model": billing_model, "billing_interval_months": billing_interval_months,
             "effective_date": date.today() - timedelta(days=1), "included_device_count": 2,
             "max_device_count": max_device_count, "currency": "JOD"},
            actor_staff_user_id=None,
        )
        add_plan_price(plan, plan_price, "JOD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        w["plan"] = plan.id

        commission_plan = create_commission_plan({"plan_code": f"C_{uuid.uuid4().hex[:6]}", "name": "Sellers"}, w["admin"])
        create_commission_rule_version(
            commission_plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=commission_rate,
            effective_from=date.today() - timedelta(days=1), actor_staff_user_id=w["admin"],
        )
        assign_employee_commission_plan(w["seller_profile"], commission_plan,
                                        effective_from=date.today() - timedelta(days=1), actor_staff_user_id=w["admin"])
    return w


def _codes(staff_id):
    from app.extensions import db_session
    from app.models.staff import StaffUser
    from app.security.rbac import get_staff_permission_codes

    return get_staff_permission_codes(db_session.get(StaffUser, staff_id))


def _record(app, w, *, actor=None, key=None, **overrides):
    """record_direct_sale as the seller by default; returns (result, key)."""
    actor = actor or w["seller"]
    key = key or str(uuid.uuid4())
    fields = dict(
        new_customer_legal_name="Corner Shop LLC", plan_id=w["plan"], extra_devices=0,
        amount=Decimal("100.00"), method="BANK_TRANSFER", reference="CLIQ-778899",
        payment_date=date.today(),
    )
    fields.update(overrides)
    with app.app_context():
        from app.commercial_sales.direct_sale import record_direct_sale

        result = record_direct_sale(idempotency_key=key, actor_staff_user_id=actor,
                                    actor_permission_codes=_codes(actor), **fields)
    return result, key


def _confirm(app, order_id, actor):
    with app.app_context():
        from app.commercial_sales.direct_sale import confirm_direct_sale

        return confirm_direct_sale(order_id, actor_staff_user_id=actor, license_pepper=PEPPER)


def _counts(app):
    with app.app_context():
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice, PaymentAllocation, SalesOrder
        from app.models.commissions import CommissionLedgerEntry
        from app.models.customers import Customer
        from app.models.licensing import License
        from app.models.subscriptions import PaymentRecord, Subscription

        def n(model):
            return db_session.execute(select(func.count()).select_from(model)).scalar_one()

        return {
            "customers": n(Customer), "orders": n(SalesOrder), "payments": n(PaymentRecord),
            "invoices": n(CommercialInvoice), "allocations": n(PaymentAllocation),
            "commissions": n(CommissionLedgerEntry), "subscriptions": n(Subscription), "licenses": n(License),
        }


RECORDED = {"customers": 1, "orders": 1, "payments": 1, "invoices": 0, "allocations": 0,
            "commissions": 0, "subscriptions": 0, "licenses": 0}
CONFIRMED = {"customers": 1, "orders": 1, "payments": 1, "invoices": 1, "allocations": 1,
             "commissions": 1, "subscriptions": 1, "licenses": 1}
NOTHING = {"customers": 0, "orders": 0, "payments": 0, "invoices": 0, "allocations": 0,
           "commissions": 0, "subscriptions": 0, "licenses": 0}


def _sale_form_post(client, **overrides):
    page = client.get("/licenses/issuance/new")
    assert page.status_code == 200
    csrf = get_csrf(page.get_data(as_text=True))
    data = {
        "csrf_token": csrf, "idempotency_key": str(uuid.uuid4()),
        "customer_id": "", "new_customer_legal_name": "Corner Shop LLC", "extra_devices": "0",
        "amount": "100.00", "method": "BANK_TRANSFER", "reference": "CLIQ-778899",
        "payment_date": date.today().isoformat(),
    }
    data.update(overrides)
    return client.post("/licenses/issuance/sale", data=data)


# ------------------------------------------- the gap, end to end over HTTP --

def test_one_screen_sale_records_pending_payment_and_seller_and_no_licence(app, client, seeded):
    """RED on 829c4cde (no sale form; the one screen issued a licence with no
    payment). A seller records the sale: one PENDING payment recorded by
    them, one DRAFT order credited to them, and NO licence yet."""
    w = _world(app)
    force_login(client, app, w["seller"])
    resp = _sale_form_post(client, plan_id=str(w["plan"]))
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    body = resp.get_data(as_text=True)
    assert "AURA-" not in body  # no key before a manager confirms the money
    assert _counts(app) == RECORDED
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder
        from app.models.customers import Customer
        from app.models.subscriptions import PaymentRecord

        payment = db_session.execute(select(PaymentRecord)).scalars().one()
        order = db_session.execute(select(SalesOrder)).scalars().one()
        customer = db_session.execute(select(Customer)).scalars().one()
        assert payment.status == "PENDING"
        assert payment.recorded_by_staff_user_id == w["seller"]
        assert payment.verified_by_staff_user_id is None
        assert (Decimal(payment.amount), payment.currency, payment.method, payment.reference) == (
            Decimal("100.00"), "JOD", "BANK_TRANSFER", "CLIQ-778899")
        assert order.status == "DRAFT"
        assert order.created_by_employee_profile_id == w["seller_profile"]
        assert order.total == Decimal("100.00")
        assert customer.assigned_sales_staff_id == w["seller"]
        assert order.order_number in body


def test_manager_confirms_then_exactly_one_licence_and_commission_for_the_seller(app, client, seeded):
    """RED on 829c4cde. The manager's one confirmation: payment CONFIRMED by
    them, invoice PAID and credited to the SELLER, one allocation, one
    commission entry for the seller (10% of 100.00), one licence whose key
    is shown once, subscription sales owner = the seller."""
    w = _world(app)
    result, _key = _record(app, w)
    login_and_verify_mfa(client, _email(app, w["manager"]))
    page = client.get("/licenses/issuance/sales")
    assert page.status_code == 200
    assert result["order_number"] in page.get_data(as_text=True)
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/licenses/issuance/sales/{result['order_id']}/confirm", data={"csrf_token": csrf})
    assert resp.status_code == 200, resp.get_data(as_text=True)[:2000]
    body = resp.get_data(as_text=True)
    assert "AURA-" in body
    assert 'data-copy-target="issuance-revealed-key"' in body
    assert _counts(app) == CONFIRMED
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import CommercialInvoice, SalesOrder
        from app.models.commissions import CommissionLedgerEntry
        from app.models.licensing import License
        from app.models.subscriptions import PaymentRecord, Subscription

        payment = db_session.execute(select(PaymentRecord)).scalars().one()
        invoice = db_session.execute(select(CommercialInvoice)).scalars().one()
        entry = db_session.execute(select(CommissionLedgerEntry)).scalars().one()
        subscription = db_session.execute(select(Subscription)).scalars().one()
        licence = db_session.execute(select(License)).scalars().one()
        order = db_session.execute(select(SalesOrder)).scalars().one()
        assert payment.status == "CONFIRMED" and payment.verified_by_staff_user_id == w["manager"]
        assert invoice.status == "PAID" and invoice.created_by_employee_profile_id == w["seller_profile"]
        assert entry.employee_profile_id == w["seller_profile"]
        assert entry.commission_amount == Decimal("10.00")
        assert entry.earned_at.date() == date.today()  # counted by the payment-confirmed date
        assert subscription.sales_owner_staff_user_id == w["seller"]
        assert subscription.sales_order_id == order.id and order.status == "FULFILLED"
        assert licence.status == "ISSUED" and licence.device_limit == 2


def _email(app, staff_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        return db_session.get(StaffUser, staff_id).email


# --------------------------------------------- the old immediate path --

def test_old_immediate_issue_without_a_reason_is_refused(app, client, seeded):
    """RED on 829c4cde: the Super Admin's old one-screen POST issued a paid
    package's licence with no payment and no seller. Now it needs a written
    no-charge reason; without one nothing is written."""
    w = _world(app)
    login_and_verify_mfa(client, _email(app, w["admin"]))
    page = client.get("/licenses/issuance/new")  # exists before and after Step 0
    csrf = get_csrf(page.get_data(as_text=True))
    assert csrf
    resp = client.post("/licenses/issuance", data={
        "csrf_token": csrf, "idempotency_key": str(uuid.uuid4()),
        "new_customer_legal_name": "Free Rider Co", "plan_id": str(w["plan"]), "extra_devices": "0",
    })
    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert "AURA-" not in body
    # Refused BY the reason check -- not by CSRF or anything else that 400s.
    assert "Write why this licence is issued without a payment" in body
    assert _counts(app) == NOTHING


def test_no_charge_licence_refused_to_a_non_super_admin_holding_licenses_issue(app, client, seeded):
    """RED on 829c4cde. licenses.issue is grantable; a custom role holding it
    must not hand out licences with no payment."""
    w = _world(app)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.staff import Permission, Role, RolePermission

        role = Role(code="KEY_ISSUER", name="Key issuer", description="custom", is_system_role=False)
        db_session.add(role)
        db_session.flush()
        for code in ("licenses.issue", "licenses.view"):
            perm = db_session.execute(select(Permission).where(Permission.code == code)).scalars().one()
            db_session.add(RolePermission(role_id=role.id, permission_id=perm.id))
        db_session.commit()
    issuer = make_staff(app, "issuer@example.com", role_codes=["KEY_ISSUER"], mfa=True)
    login_and_verify_mfa(client, "issuer@example.com")
    page = client.get("/licenses")  # a page this role may open, for the CSRF token
    assert page.status_code == 200
    csrf = get_csrf(page.get_data(as_text=True))
    assert csrf
    resp = client.post("/licenses/issuance", data={
        "csrf_token": csrf, "idempotency_key": str(uuid.uuid4()),
        "new_customer_legal_name": "Free Rider Co", "plan_id": str(w["plan"]), "extra_devices": "0",
        "no_charge_reason": "demo for a friend of the owner",
    })
    assert resp.status_code == 403
    assert _counts(app) == NOTHING
    with app.app_context():
        from app.licensing.issuance import LicenseIssuanceError, issue_no_charge_license

        with pytest.raises(LicenseIssuanceError):
            issue_no_charge_license(
                reason="demo for a friend of the owner", new_customer_legal_name="Free Rider Co",
                plan_id=w["plan"], idempotency_key=str(uuid.uuid4()), actor_staff_user_id=issuer,
                license_pepper=PEPPER,
            )
    assert _counts(app) == NOTHING


def test_no_charge_licence_with_reason_is_issued_and_audited_exactly_once(app, client, seeded):
    w = _world(app)
    login_and_verify_mfa(client, _email(app, w["admin"]))
    page = client.get("/licenses/issuance/no-charge")
    csrf = get_csrf(page.get_data(as_text=True))
    data = {
        "csrf_token": csrf, "idempotency_key": str(uuid.uuid4()),
        "new_customer_legal_name": "Demo Shop", "plan_id": str(w["plan"]), "extra_devices": "0",
        "no_charge_reason": "Showroom demo laptop",
    }
    first = client.post("/licenses/issuance", data=data)
    assert first.status_code == 200 and "AURA-" in first.get_data(as_text=True)
    again = client.post("/licenses/issuance", data=data)  # double submit
    assert again.status_code == 200 and "AURA-" not in again.get_data(as_text=True)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.audit import AuditLog
        from app.models.licensing import License

        licence = db_session.execute(select(License)).scalars().one()
        rows = db_session.execute(
            select(AuditLog).where(AuditLog.action_code == "LICENSE_ISSUED_NO_CHARGE")
        ).scalars().all()
        assert [(r.entity_public_id, r.reason, r.actor_staff_user_id) for r in rows] == [
            (str(licence.id), "Showroom demo laptop", w["admin"])]
    counts = _counts(app)
    assert (counts["licenses"], counts["payments"], counts["orders"], counts["commissions"]) == (1, 0, 0, 0)


def test_no_charge_reason_lost_to_a_crash_is_written_by_the_retry(app, seeded, monkeypatch):
    """The audit row comes after the issuance commit; a crash in between
    must not lose the reason: the retry (a replay) writes it, once."""
    w = _world(app)
    import app.licensing.issuance as issuance_module

    real = issuance_module._record_no_charge_reason
    calls = {"n": 0}

    def _crash_first(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash after the licence committed")
        return real(*args, **kwargs)

    monkeypatch.setattr(issuance_module, "_record_no_charge_reason", _crash_first)
    key = str(uuid.uuid4())
    kwargs = dict(reason="Replacement for a lost key", new_customer_legal_name="Lost Key Co", plan_id=w["plan"],
                  idempotency_key=key, actor_staff_user_id=w["admin"], license_pepper=PEPPER)
    with app.app_context():
        with pytest.raises(RuntimeError):
            issuance_module.issue_no_charge_license(**kwargs)
    with app.app_context():
        result = issuance_module.issue_no_charge_license(**kwargs)
        assert result["replayed"] is True
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.audit import AuditLog

        n = db_session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.action_code == "LICENSE_ISSUED_NO_CHARGE")
        ).scalar_one()
        assert n == 1


# ------------------------------------------------ the two-person rule --

def test_recorder_cannot_confirm_the_sale_they_recorded(app, seeded):
    """A Super Admin records a sale for the seller, then tries to confirm it."""
    w = _world(app)
    result, _ = _record(app, w, actor=w["admin"], sales_owner_employee_profile_id=w["seller_profile"])
    from app.commercial_sales.errors import CommercialSalesError

    with pytest.raises(CommercialSalesError) as exc:
        _confirm(app, result["order_id"], w["admin"])
    assert exc.value.code == "SELF_CONFIRMATION_FORBIDDEN"
    assert _counts(app) == RECORDED
    _assert_untouched(app)


def test_seller_cannot_confirm_a_sale_a_manager_recorded_for_them(app, seeded):
    """The admin records a sale whose seller is the FINANCE manager -- who
    holds every confirmation permission -- and that manager tries every
    confirmation door: the sale screen, confirm_payment, and the storage
    writer the legacy correct route calls."""
    w = _world(app)
    result, _ = _record(app, w, actor=w["admin"], sales_owner_employee_profile_id=w["manager_profile"])
    from app.commercial_sales.errors import CommercialSalesError
    from app.subscriptions.services import PaymentSelfVerificationError

    with pytest.raises(CommercialSalesError) as exc:
        _confirm(app, result["order_id"], w["manager"])  # the manager IS this sale's seller
    assert exc.value.code == "SELLER_CONFIRMATION_FORBIDDEN"
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord
        from app.subscriptions.services import correct_payment

        payment = db_session.get(PaymentRecord, result["payment_id"])
        with pytest.raises(CommercialSalesError) as exc2:
            confirm_payment(payment, actor_staff_user_id=w["manager"])
        assert exc2.value.code == "SELLER_CONFIRMATION_FORBIDDEN"
        with pytest.raises(PaymentSelfVerificationError):
            correct_payment(payment, "CONFIRMED", "seller via legacy route", w["manager"])
    assert _counts(app) == RECORDED
    _assert_untouched(app)


def _assert_untouched(app):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder
        from app.models.subscriptions import PaymentRecord

        payment = db_session.execute(select(PaymentRecord)).scalars().one()
        order = db_session.execute(select(SalesOrder)).scalars().one()
        assert (payment.status, payment.verified_by_staff_user_id, order.status) == ("PENDING", None, "DRAFT")


def test_seller_cannot_finish_a_sale_whose_payment_was_confirmed_elsewhere(app, seeded):
    """The manager confirms the payment from the payments screen; the seller
    (with manager rights here) must still not be the one who finishes the
    sale and reveals the key."""
    w = _world(app)
    result, _ = _record(app, w, actor=w["admin"], sales_owner_employee_profile_id=w["manager_profile"])
    third = make_staff(app, "third@example.com", role_codes=["FINANCE"], mfa=True)
    with app.app_context():
        from app.commercial_sales.payments import confirm_payment
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        confirm_payment(db_session.get(PaymentRecord, result["payment_id"]), actor_staff_user_id=third)
    from app.commercial_sales.errors import CommercialSalesError

    with pytest.raises(CommercialSalesError) as exc:
        _confirm(app, result["order_id"], w["manager"])
    assert exc.value.code == "SELLER_CONFIRMATION_FORBIDDEN"
    with pytest.raises(CommercialSalesError) as exc2:
        _confirm(app, result["order_id"], w["admin"])  # the recorder
    assert exc2.value.code == "SELF_CONFIRMATION_FORBIDDEN"
    counts = _counts(app)
    assert (counts["licenses"], counts["invoices"], counts["commissions"]) == (0, 0, 0)
    # ...and the third person may finish it: the allow half.
    done = _confirm(app, result["order_id"], third)
    assert done["full_key"] is not None
    # This sale's seller (the manager) has no commission plan assigned, so
    # the ledger rightly posts nothing (ledger.py: no rule -> no entry).
    assert _counts(app) == {**CONFIRMED, "commissions": 0}


def test_a_different_manager_may_confirm_a_sale_recorded_by_another_manager(app, seeded):
    """The allow half of the recorder/seller checks: recorded by the admin
    for the seller, confirmed by the FINANCE manager -> works."""
    w = _world(app)
    result, _ = _record(app, w, actor=w["admin"], sales_owner_employee_profile_id=w["seller_profile"])
    done = _confirm(app, result["order_id"], w["manager"])
    assert done["full_key"] is not None and done["full_key"].startswith("AURA-")
    assert _counts(app) == CONFIRMED


def test_sales_role_cannot_confirm_or_list_over_http(app, client, seeded):
    w = _world(app)
    result, _ = _record(app, w)
    login_and_verify_mfa(client, _email(app, w["seller"]))
    assert client.get("/licenses/issuance/sales").status_code == 403
    page = client.get("/licenses/issuance/new")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/licenses/issuance/sales/{result['order_id']}/confirm", data={"csrf_token": csrf})
    assert resp.status_code == 403
    assert _counts(app) == RECORDED


def test_finance_cannot_open_the_sale_form(app, client, seeded):
    """FINANCE confirms money; it does not record sales (no orders.create)."""
    w = _world(app)
    force_login(client, app, w["manager"])
    assert client.get("/licenses/issuance/new").status_code == 403


def test_finance_cannot_post_a_sale_either(app, client, seeded):
    """The POST's own gate, not just the form's: record_direct_sale checks no
    permission itself (it trusts the route), and FINANCE holds orders.approve,
    so without the route gate it could record -- and pick the seller of -- a
    sale it then confirms as 'a different person'."""
    w = _world(app)
    login_and_verify_mfa(client, _email(app, w["manager"]))
    page = client.get("/licenses/issuance/sales")  # a page FINANCE may open, for the CSRF token
    assert page.status_code == 200
    csrf = get_csrf(page.get_data(as_text=True))
    assert csrf
    resp = client.post("/licenses/issuance/sale", data={
        "csrf_token": csrf, "idempotency_key": str(uuid.uuid4()), "customer_id": "",
        "new_customer_legal_name": "Corner Shop LLC", "plan_id": str(w["plan"]), "extra_devices": "0",
        "sales_owner_employee_profile_id": str(w["seller_profile"]),
        "amount": "100.00", "method": "BANK_TRANSFER", "reference": "CLIQ-778899",
        "payment_date": date.today().isoformat(),
    })
    assert resp.status_code == 403
    assert _counts(app) == NOTHING


def test_sales_role_cannot_reject_a_sale_over_http(app, client, seeded):
    """Rejecting cancels the order: a seller must not be able to cancel a
    sale (theirs or anyone's) -- that is the manager's call."""
    w = _world(app)
    result, _ = _record(app, w)
    login_and_verify_mfa(client, _email(app, w["seller"]))
    page = client.get("/licenses/issuance/new")
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/licenses/issuance/sales/{result['order_id']}/reject",
                       data={"csrf_token": csrf, "reason": "seller cancels it"})
    assert resp.status_code == 403
    assert _counts(app) == RECORDED
    _assert_untouched(app)


def test_confirm_without_recent_mfa_goes_to_reauth_and_issues_nothing(app, client, seeded):
    """Confirming reveals the key, so it needs a recent MFA check (Part F),
    the same as the no-charge path."""
    w = _world(app)
    result, _ = _record(app, w)
    login_and_verify_mfa(client, _email(app, w["manager"]))
    page = client.get("/licenses/issuance/sales")
    csrf = get_csrf(page.get_data(as_text=True))
    with app.app_context():
        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffSession

        session_row = db_session.query(StaffSession).order_by(StaffSession.created_at.desc()).first()
        session_row.mfa_verified_at = utcnow() - timedelta(seconds=app.config["RECENT_AUTH_WINDOW_SECONDS"] + 5)
        db_session.commit()
    resp = client.post(f"/licenses/issuance/sales/{result['order_id']}/confirm", data={"csrf_token": csrf})
    assert resp.status_code == 302
    assert "reauth" in resp.headers["Location"]
    assert _counts(app) == RECORDED
    _assert_untouched(app)


# --------------------------------------------- who the sale is credited to --

def test_sales_role_cannot_credit_the_sale_to_another_seller(app, seeded):
    w = _world(app)
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError):
        _record(app, w, sales_owner_employee_profile_id=w["manager_profile"])
    assert _counts(app) == NOTHING
    # allow half: naming themselves is fine
    result, _ = _record(app, w, sales_owner_employee_profile_id=w["seller_profile"])
    assert result["replayed"] is False and _counts(app) == RECORDED


def test_sales_role_cannot_sell_to_another_sellers_customer(app, seeded):
    w = _world(app)
    with app.app_context():
        from app.customers.services import create_customer

        theirs = create_customer({"legal_name": "Someone Else's Shop"}, w["manager"]).id
        mine = create_customer({"legal_name": "My Own Shop"}, w["seller"]).id
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError):
        _record(app, w, customer_id=theirs, new_customer_legal_name=None)
    counts = _counts(app)
    assert (counts["orders"], counts["payments"]) == (0, 0)
    result, _ = _record(app, w, customer_id=mine, new_customer_legal_name=None)
    assert result["customer_id"] == mine


def test_manager_recording_must_name_an_active_seller(app, seeded):
    w = _world(app)
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError):
        _record(app, w, actor=w["admin"])  # no seller picked
    with app.app_context():
        from app.employees.services import update_employee_profile
        from app.extensions import db_session
        from app.models.employees import EmployeeProfile

        update_employee_profile(db_session.get(EmployeeProfile, w["seller_profile"]),
                                {"employment_status": "SUSPENDED"}, w["admin"])
    with pytest.raises(DirectSaleError):
        _record(app, w, actor=w["admin"], sales_owner_employee_profile_id=w["seller_profile"])
    assert _counts(app) == NOTHING


# ------------------------------------------------------- the money rules --

@pytest.mark.parametrize("overrides", [
    {"amount": Decimal("99.99")},                       # not the full price
    {"amount": Decimal("100.01")},
    {"method": "CASH"},                                 # sellers never take cash
    {"method": "CHEQUE"},
    {"reference": "   "},                               # proof required
    {"payment_date": date.today() + timedelta(days=1)},  # money from the future
    {"payment_date": None},
    {"extra_devices": -1},
], ids=["short", "over", "cash", "cheque", "no-reference", "future-date", "no-date", "negative-devices"])
def test_money_rules_refuse_before_any_write(app, seeded, overrides):
    w = _world(app)
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError):
        _record(app, w, **overrides)
    assert _counts(app) == NOTHING


def test_full_price_today_by_transfer_is_accepted(app, seeded):
    """The allow half of the money rules above (yesterday's date too)."""
    w = _world(app)
    result, _ = _record(app, w, payment_date=date.today() - timedelta(days=1))
    assert result["total"] == Decimal("100.00") and _counts(app) == RECORDED


def test_zero_price_package_is_not_a_sale(app, seeded):
    w = _world(app, plan_price=Decimal("0.00"))
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError, match="no-charge"):
        _record(app, w, amount=Decimal("0.01"))
    assert _counts(app) == NOTHING


def test_extra_devices_are_priced_from_the_catalog_and_reach_the_licence(app, seeded):
    w = _world(app)
    with pytest.raises(Exception):
        _record(app, w, extra_devices=2)  # 100.00 is no longer the full price
    result, _ = _record(app, w, extra_devices=2, amount=Decimal("200.00"))
    assert result["total"] == Decimal("200.00")
    done = _confirm(app, result["order_id"], w["manager"])
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        licence = db_session.get(License, done["license_id"])
        subscription = db_session.get(Subscription, licence.subscription_id)
        assert (licence.device_limit, subscription.device_allowance) == (4, 4)


def test_extra_devices_refused_while_the_add_on_is_not_on_sale(app, seeded):
    w = _world(app, extra_device_price=None)  # EXTRA_DEVICE stays PLANNED, unpriced
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError, match="Extra devices"):
        _record(app, w, extra_devices=1, amount=Decimal("150.00"))
    assert _counts(app) == NOTHING


def test_annual_package_sold_this_way_gets_a_term(app, seeded):
    """Parity with the old one-screen path (resolve_license_term): before,
    fulfillment set no term at all, so a yearly licence sold through the
    pipeline never expired."""
    w = _world(app, billing_model="ANNUAL", billing_interval_months=12)
    result, _ = _record(app, w)
    done = _confirm(app, result["order_id"], w["manager"])
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        licence = db_session.get(License, done["license_id"])
        subscription = db_session.get(Subscription, licence.subscription_id)
        assert subscription.start_date == date.today() and subscription.end_date is not None
        assert subscription.end_date > date.today() + timedelta(days=360)
        assert (licence.valid_from, licence.valid_until) == (subscription.start_date, subscription.end_date)


# ------------------------------------------------------- exactly once --

def test_same_form_twice_is_one_sale(app, seeded):
    w = _world(app)
    first, key = _record(app, w)
    second, _ = _record(app, w, key=key)
    assert second["replayed"] is True
    assert (second["order_id"], second["payment_id"]) == (first["order_id"], first["payment_id"])
    assert _counts(app) == RECORDED


@pytest.mark.parametrize("change", [
    {"amount": Decimal("100.00"), "reference": "CLIQ-OTHER"},
    {"new_customer_legal_name": "Another Shop"},
    {"extra_devices": 1, "amount": Decimal("150.00")},
], ids=["reference", "customer", "devices"])
def test_same_key_different_sale_is_refused(app, seeded, change):
    w = _world(app)
    _first, key = _record(app, w)
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError, match="different sale"):
        _record(app, w, key=key, **change)
    assert _counts(app) == RECORDED


def test_concurrent_submits_of_one_form_record_one_sale(app, seeded, monkeypatch):
    """Five callers, one key, all held in the window before the order is
    written (0.75s sleep in document numbering -- same technique as
    test_license_issuance.py). One customer, one order, one payment; four
    clean replays, no crash."""
    w = _world(app)
    import app.commercial_sales.direct_sale as direct_sale_module

    real = direct_sale_module.allocate_document_number

    def _slow(*args, **kwargs):
        time.sleep(0.75)
        return real(*args, **kwargs)

    monkeypatch.setattr(direct_sale_module, "allocate_document_number", _slow)
    key = str(uuid.uuid4())
    results, errors = [], []
    lock = threading.Lock()

    def worker():
        try:
            r, _ = _record(app, w, key=key)
            with lock:
                results.append(r)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            from app.extensions import db_session as scoped

            scoped.remove()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)
    assert errors == [], "; ".join(f"{type(e).__name__}: {e}" for e in errors)
    assert _counts(app) == RECORDED
    assert sum(1 for r in results if r["replayed"]) == 4
    assert len({r["order_id"] for r in results}) == 1


def test_crash_between_order_and_payment_resumes_on_resubmit(app, seeded, monkeypatch):
    w = _world(app)
    import app.commercial_sales.direct_sale as direct_sale_module

    real = direct_sale_module.record_payment
    calls = {"n": 0}

    def _fails_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash before the payment was written")
        return real(*args, **kwargs)

    monkeypatch.setattr(direct_sale_module, "record_payment", _fails_once)
    key = str(uuid.uuid4())
    with pytest.raises(RuntimeError):
        _record(app, w, key=key)
    counts = _counts(app)
    assert (counts["customers"], counts["orders"], counts["payments"]) == (1, 1, 0)
    result, _ = _record(app, w, key=key)
    assert result["replayed"] is False
    assert _counts(app) == RECORDED


def test_confirming_twice_is_one_licence_one_commission(app, seeded):
    w = _world(app)
    result, _ = _record(app, w)
    first = _confirm(app, result["order_id"], w["manager"])
    second = _confirm(app, result["order_id"], w["manager"])
    assert first["full_key"] is not None
    assert second["replayed"] is True and second["full_key"] is None
    assert second["license_id"] == first["license_id"]
    assert _counts(app) == CONFIRMED


def test_concurrent_confirmations_issue_one_licence(app, seeded, monkeypatch):
    """Two managers pressing Confirm at once: one licence, one invoice, one
    allocation, one commission entry; exactly one key revealed."""
    w = _world(app)
    other = make_staff(app, "other-manager@example.com", role_codes=["FINANCE"], mfa=True)
    result, _ = _record(app, w)
    import app.commercial_sales.direct_sale as direct_sale_module

    real = direct_sale_module.confirm_payment

    def _slow(*args, **kwargs):
        time.sleep(0.75)
        return real(*args, **kwargs)

    monkeypatch.setattr(direct_sale_module, "confirm_payment", _slow)
    results, errors = [], []
    lock = threading.Lock()

    def worker(actor):
        try:
            r = _confirm(app, result["order_id"], actor)
            with lock:
                results.append(r)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            from app.extensions import db_session as scoped

            scoped.remove()

    threads = [threading.Thread(target=worker, args=(a,)) for a in (w["manager"], other, w["manager"])]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert errors == [], "; ".join(f"{type(e).__name__}: {e}" for e in errors)
    assert _counts(app) == CONFIRMED
    assert sum(1 for r in results if r["full_key"] is not None) == 1


# ------------------------------------------------------------- reject --

def test_rejected_sale_never_gets_a_licence(app, seeded):
    w = _world(app)
    result, _ = _record(app, w)
    with app.app_context():
        from app.commercial_sales.direct_sale import reject_direct_sale

        reject_direct_sale(result["order_id"], reason="No transfer reached the account", actor_staff_user_id=w["manager"])
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError):
        _confirm(app, result["order_id"], w["manager"])
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder
        from app.models.subscriptions import PaymentRecord

        assert db_session.get(PaymentRecord, result["payment_id"]).status == "FAILED"
        assert db_session.get(SalesOrder, result["order_id"]).status == "CANCELLED"
    counts = _counts(app)
    assert (counts["licenses"], counts["commissions"], counts["invoices"]) == (0, 0, 0)


def test_sale_whose_payment_was_rejected_elsewhere_cannot_be_confirmed(app, seeded):
    """The payment is rejected from the payments screen (not through this
    sale), so the order is still DRAFT. Confirming must stop on the FAILED
    payment BEFORE any write -- not confirm the order and issue an invoice
    first and only then fail at allocation. (Added after mutation M29
    survived: the reject test above is stopped by the CANCELLED order check
    and never reaches the payment-status check.)"""
    w = _world(app)
    result, _ = _record(app, w)
    with app.app_context():
        from app.commercial_sales.payments import reject_payment
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        reject_payment(db_session.get(PaymentRecord, result["payment_id"]), reason="Bounced transfer",
                       actor_staff_user_id=w["manager"])
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError, match="FAILED"):
        _confirm(app, result["order_id"], w["admin"])
    assert _counts(app) == RECORDED
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder

        assert db_session.get(SalesOrder, result["order_id"]).status == "DRAFT"


# ------------------------------------- fulfillment credits the right seller --

def test_pipeline_fulfillment_names_the_seller_not_the_confirmer(app, seeded):
    """RED on 829c4cde: fulfill_order wrote the FULFILLING actor (the
    manager) as the subscription's sales owner."""
    w = _world(app)
    result, _ = _record(app, w)
    done = _confirm(app, result["order_id"], w["manager"])
    with app.app_context():
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        subscription = db_session.get(Subscription, db_session.get(License, done["license_id"]).subscription_id)
        assert subscription.sales_owner_staff_user_id == w["seller"]
        assert subscription.sales_owner_staff_user_id != w["manager"]


def test_sale_form_renders_seller_and_payment_fields(app, client, seeded):
    """RED on 829c4cde: the one screen had no payment or seller fields."""
    w = _world(app)
    force_login(client, app, w["admin"])
    body = client.get("/licenses/issuance/new").get_data(as_text=True)
    for field in ('name="amount"', 'name="reference"', 'name="payment_date"', 'name="method"',
                  'name="sales_owner_employee_profile_id"', 'action="/licenses/issuance/sale"'):
        assert field in body, field
    assert 'value="CASH"' not in body


# ---------- the reused pipeline itself (quote path, no direct_sale code) --
#
# These drive the EXISTING quote -> order -> invoice -> payment ->
# allocation -> fulfill_order services only, so they measure fulfillment.py
# on its own, before and after (RED on 829c4cde for the reason each names).

def _pipeline_fulfil(app, w, *, extra_devices=0):
    with app.app_context():
        from sqlalchemy import select

        from app.commercial_sales.allocation import allocate_payment
        from app.commercial_sales.fulfillment import fulfill_order
        from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
        from app.commercial_sales.payments import confirm_payment, submit_payment
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote
        from app.customers.services import create_customer
        from app.extensions import db_session
        from app.models.catalog import Addon

        customer = create_customer({"legal_name": "Pipeline Shop"}, w["seller"])
        quote = create_quote({"customer_id": customer.id, "currency": "JOD"},
                             actor_employee_profile_id=w["seller_profile"], actor_staff_user_id=w["seller"])
        add_quote_line(quote, plan_id=w["plan"], addon_id=None, quantity=1, actor_staff_user_id=w["seller"])
        if extra_devices:
            addon = db_session.execute(select(Addon).where(Addon.addon_code == "EXTRA_DEVICE")).scalars().one()
            add_quote_line(quote, plan_id=None, addon_id=addon.id, quantity=extra_devices, sort_order=1,
                           actor_staff_user_id=w["seller"])
        submit_quote(quote, actor_staff_user_id=w["seller"])
        record_customer_decision(quote, accepted=True, actor_staff_user_id=w["seller"])
        order = create_order_from_quote(quote, actor_employee_profile_id=w["seller_profile"],
                                        actor_staff_user_id=w["seller"], idempotency_key=str(uuid.uuid4()))
        confirm_order(order, actor_staff_user_id=w["manager"])
        invoice = create_invoice_from_order(order, actor_employee_profile_id=w["seller_profile"],
                                            actor_staff_user_id=w["seller"], idempotency_key=str(uuid.uuid4()))
        issue_invoice(invoice, actor_staff_user_id=w["manager"])
        payment = submit_payment(customer_id=customer.id, amount=invoice.total, currency="JOD", method="BANK_TRANSFER",
                                 payment_date=date.today(), reference="T-1", actor_staff_user_id=w["seller"])
        confirm_payment(payment, actor_staff_user_id=w["manager"])
        allocate_payment(payment=payment, invoice=invoice, amount=invoice.total, actor_staff_user_id=w["manager"])
        result = fulfill_order(order, actor_staff_user_id=w["manager"], license_pepper=PEPPER,
                               idempotency_key=str(uuid.uuid4()))
        return result["subscription_id"], result["license_id"]


def _sub_and_licence(app, subscription_id, license_id):
    from app.extensions import db_session
    from app.models.licensing import License
    from app.models.subscriptions import Subscription

    return db_session.get(Subscription, subscription_id), db_session.get(License, license_id)


def test_pipeline_fulfilment_credits_the_order_creator_as_sales_owner(app, seeded):
    """RED on 829c4cde: sales_owner_staff_user_id was the fulfilling manager."""
    w = _world(app)
    sub_id, lic_id = _pipeline_fulfil(app, w)
    with app.app_context():
        subscription, _ = _sub_and_licence(app, sub_id, lic_id)
        assert subscription.sales_owner_staff_user_id == w["seller"]


def test_pipeline_fulfilment_gives_the_licence_its_paid_extra_devices(app, seeded):
    """RED on 829c4cde: device_limit was plan.included_device_count (2) even
    with 3 extra devices on the paid order."""
    w = _world(app)
    sub_id, lic_id = _pipeline_fulfil(app, w, extra_devices=3)
    with app.app_context():
        subscription, licence = _sub_and_licence(app, sub_id, lic_id)
        assert (licence.device_limit, subscription.device_allowance) == (5, 5)


def test_pipeline_fulfilment_sets_the_term_of_a_yearly_package(app, seeded):
    """RED on 829c4cde: no start/end date, so an ANNUAL licence never expired."""
    w = _world(app, billing_model="ANNUAL", billing_interval_months=12)
    sub_id, lic_id = _pipeline_fulfil(app, w)
    with app.app_context():
        subscription, licence = _sub_and_licence(app, sub_id, lic_id)
        assert subscription.start_date == date.today()
        assert subscription.end_date is not None and subscription.end_date > date.today() + timedelta(days=360)
        assert licence.valid_until == subscription.end_date


def test_pipeline_fulfilment_refuses_extra_devices_beyond_the_package_maximum(app, seeded):
    w = _world(app, max_device_count=3)
    from app.commercial_sales.errors import CommercialSalesError

    with pytest.raises(CommercialSalesError) as exc:
        _pipeline_fulfil(app, w, extra_devices=2)  # 2 + 2 = 4 > 3
    assert exc.value.code == "FULFILLMENT_NOT_ELIGIBLE"
    assert _counts(app)["licenses"] == 0


def test_crash_between_allocation_and_licence_resumes_without_a_second_allocation(app, seeded, monkeypatch):
    """Confirmation crashes after the money was allocated (commission posted)
    but before the licence: pressing Confirm again must finish the SAME
    sale -- one allocation, one commission entry, one licence."""
    w = _world(app)
    result, _ = _record(app, w)
    import app.commercial_sales.direct_sale as direct_sale_module

    real = direct_sale_module.fulfill_order
    calls = {"n": 0}

    def _fails_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash before fulfilment")
        return real(*args, **kwargs)

    monkeypatch.setattr(direct_sale_module, "fulfill_order", _fails_once)
    with pytest.raises(RuntimeError):
        _confirm(app, result["order_id"], w["manager"])
    counts = _counts(app)
    assert (counts["allocations"], counts["commissions"], counts["licenses"]) == (1, 1, 0)
    done = _confirm(app, result["order_id"], w["manager"])
    assert done["full_key"] is not None
    assert _counts(app) == CONFIRMED


def test_package_retired_since_the_sale_is_refused_before_the_money_is_confirmed(app, seeded):
    w = _world(app)
    result, _ = _record(app, w)
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import Plan

        db_session.get(Plan, w["plan"]).retirement_date = date.today()
        db_session.commit()
    from app.commercial_sales.direct_sale import DirectSaleError

    with pytest.raises(DirectSaleError, match="no longer sellable"):
        _confirm(app, result["order_id"], w["manager"])
    assert _counts(app) == RECORDED
    _assert_untouched(app)
