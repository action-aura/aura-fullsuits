"""Step 0 (2026-10-24) -- the owner's three packages, sold by "Sell a licence".

The owner's decisions (Aura Growth Plan, "Your answers" + the 2026-10-24
additions) this file holds the code to:
  - prices are PER BRANCH; each package includes 2 devices of ANY kind (2026-10-31;
    it was "1 desktop + 1 phone");
  - Package 1: 250 JOD the first year (100 a year + 150 first-time), then
    100 JOD a year -- and the renewal stays 100 however many extra devices;
  - Package 2: 35 JOD a month, 12-month contract; Package 3: 20 JOD a
    month, 24-month contract;
  - extra devices are sold TYPED (2026-10-31, replacing the 2026-10-24 "pair"): an
    extra phone (Android only) and an extra desktop (Windows only), 50 JOD once each,
    on every package, never on the renewal.

Measured on 7571c3df before this change: the sale screen charged base_price
only (P1 = 100, never 250), had no idea of extra devices (it sold the Retail-only
EXTRA_DEVICE add-on one device at a time), no plan or subscription carried a
contract term, and the catalog's own price writer dropped the setup fee.

Like test_issuance_sale.py, every test asserts ROW COUNTS and AMOUNTS, not
just status codes.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import force_login, get_csrf, jordan_today, login_and_verify_mfa
from tests.test_issuance_sale import NOTHING, PEPPER, _codes, _counts, _email, _profile

JOD = "JOD"


# ------------------------------------------------------------------ setup --

def _make_plan(db_session, product, *, code, name, billing_model, interval, base, setup, contract=None,
               max_devices=None):
    from app.models.catalog import Plan, PlanPrice

    plan = Plan(
        plan_code=code, product_id=product.id, name=name, lifecycle_status="AVAILABLE",
        billing_model=billing_model, billing_interval_months=interval, currency=JOD,
        included_device_count=2, max_device_count=max_devices, effective_date=date.today() - timedelta(days=1),
    )
    # Set only where the column exists, so this world can be built on the
    # code BEFORE this change too (the RED run) -- the tests then fail on
    # behaviour, not on a TypeError in setup.
    if hasattr(Plan, "contract_term_months"):
        plan.contract_term_months = contract
    db_session.add(plan)
    db_session.flush()
    db_session.add(PlanPrice(plan_id=plan.id, base_price=base, setup_fee=setup, currency=JOD,
                             effective_from=date.today() - timedelta(days=1)))
    db_session.flush()
    return plan.id


def _world(app, *, phone_price=Decimal("50.00"), desktop_price=Decimal("50.00"), single_device_on_sale=False,
           p1_max_devices=None, commission_rate=Decimal("10.0")):
    """A seller (SALES), a manager (FINANCE), a Super Admin with profiles; a
    10% commission plan for the seller; Aura Retail sellable with the owner's
    three packages written straight into the catalog (the seed command has
    its own test); the extra PHONE and extra DESKTOP add-ons priced 50 JOD and AVAILABLE."""
    from tests.conftest import make_staff

    w = {}
    w["seller"] = make_staff(app, f"seller-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES"], mfa=True)
    w["manager"] = make_staff(app, f"manager-{uuid.uuid4().hex[:6]}@example.com", role_codes=["FINANCE"], mfa=True)
    w["admin"] = make_staff(app, f"admin-{uuid.uuid4().hex[:6]}@example.com", super_admin=True, mfa=True)
    w["seller_profile"] = _profile(app, w["seller"], f"EMP-S-{uuid.uuid4().hex[:4]}", "Sami Seller")
    w["manager_profile"] = _profile(app, w["manager"], f"EMP-M-{uuid.uuid4().hex[:4]}", "Mona Manager")
    with app.app_context():
        from sqlalchemy import select

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
        w["p1"] = _make_plan(db_session, product, code="T_P1", name="Aura Retail Package 1", billing_model="ANNUAL",
                             interval=12, base=Decimal("100.00"), setup=Decimal("150.00"), max_devices=p1_max_devices)
        w["p2"] = _make_plan(db_session, product, code="T_P2", name="Aura Retail Package 2", billing_model="MONTHLY",
                             interval=1, base=Decimal("35.00"), setup=Decimal("0"), contract=12)
        w["p3"] = _make_plan(db_session, product, code="T_P3", name="Aura Retail Package 3", billing_model="MONTHLY",
                             interval=1, base=Decimal("20.00"), setup=Decimal("0"), contract=24)
        for code, name, price in (("EXTRA_PHONE", "Extra phone (Android)", phone_price),
                                  ("EXTRA_DESKTOP", "Extra desktop (Windows)", desktop_price)):
            addon = db_session.execute(select(Addon).where(Addon.addon_code == code)).scalars().first()
            if addon is None:  # the code before this change never seeded it
                addon = Addon(addon_code=code, product_id=product.id, name=name, availability_status="PLANNED")
                db_session.add(addon)
            if price is not None:
                addon.price = price
                addon.currency = JOD
                addon.availability_status = "AVAILABLE"
        if single_device_on_sale:
            # The rehearsal catalogue (scripts/ops/owner_rehearsal_pricing.py)
            # made the single EXTRA_DEVICE add-on AVAILABLE at 50 JOD.
            single = db_session.execute(select(Addon).where(Addon.addon_code == "EXTRA_DEVICE")).scalars().one()
            single.price = Decimal("50.00")
            single.currency = JOD
            single.availability_status = "AVAILABLE"
        db_session.commit()

        commission_plan = create_commission_plan({"plan_code": f"C_{uuid.uuid4().hex[:6]}", "name": "Sellers"}, w["admin"])
        create_commission_rule_version(
            commission_plan, rule_type="PERCENTAGE_OF_PAYMENT", rate_percentage=commission_rate,
            effective_from=date.today() - timedelta(days=1), actor_staff_user_id=w["admin"],
        )
        assign_employee_commission_plan(w["seller_profile"], commission_plan,
                                        effective_from=date.today() - timedelta(days=1), actor_staff_user_id=w["admin"])
    return w


def _post_sale(client, app, w, plan_key, *, amount, phones=None, desktops=None, pairs=None, key=None, **extra):
    """The seller's own form, over HTTP -- what the seller really does."""
    force_login(client, app, w["seller"])
    page = client.get("/licenses/issuance/new")
    assert page.status_code == 200
    data = {
        "csrf_token": get_csrf(page.get_data(as_text=True)), "idempotency_key": key or str(uuid.uuid4()),
        "customer_id": "", "new_customer_legal_name": f"Shop {uuid.uuid4().hex[:6]}", "plan_id": str(w[plan_key]),
        # CASH: these tests are about package prices and extra devices; a transfer would need a slip
        # photo since 2026-10-31 (rule tested in test_rehearsal_owner_fixes.py), which none of them is about.
        "amount": str(amount), "method": "CASH", "reference": f"CLIQ-{uuid.uuid4().hex[:6]}",
        "payment_date": date.today().isoformat(),
    }
    if phones is not None:
        data["extra_phones"] = str(phones)
    if desktops is not None:
        data["extra_desktops"] = str(desktops)
    if pairs is not None:  # the retired field: a page loaded before 2026-10-31 still posts it
        data["extra_device_pairs"] = str(pairs)
    data.update(extra)
    return client.post("/licenses/issuance/sale", data=data)


def _confirm(app, order_id, actor):
    with app.app_context():
        from app.commercial_sales.direct_sale import confirm_direct_sale

        return confirm_direct_sale(order_id, actor_staff_user_id=actor, license_pepper=PEPPER)


def _only_order(app):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder

        return db_session.execute(select(SalesOrder)).scalars().one().id


def _order_lines(app, order_id):
    """[(kind, quantity, unit_price, line_total)] -- kind = 'PLAN' or the add-on's code."""
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import Addon
        from app.models.commercial_sales import SalesOrderLine

        rows = db_session.execute(
            select(SalesOrderLine).where(SalesOrderLine.sales_order_id == order_id).order_by(SalesOrderLine.sort_order)
        ).scalars().all()
        out = []
        for line in rows:
            kind = "PLAN" if line.plan_id is not None else db_session.get(Addon, line.addon_id).addon_code
            out.append((kind, line.quantity, Decimal(line.unit_price), Decimal(line.line_total)))
        return out


def _finished(app, order_id):
    """What the manager's confirmation produced for this order."""
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import Addon
        from app.models.commercial_sales import CommercialInvoice, CommercialInvoiceItem, SalesOrder
        from app.models.commissions import CommissionLedgerEntry
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        order = db_session.get(SalesOrder, order_id)
        sub = db_session.execute(select(Subscription).where(Subscription.sales_order_id == order_id)).scalars().one()
        lic = db_session.execute(select(License).where(License.subscription_id == sub.id)).scalars().one()
        inv = db_session.execute(select(CommercialInvoice).where(CommercialInvoice.sales_order_id == order_id)).scalars().one()
        items = db_session.execute(
            select(CommercialInvoiceItem).where(CommercialInvoiceItem.commercial_invoice_id == inv.id)
        ).scalars().all()
        def _items(code):
            return [(i.quantity, Decimal(i.line_total)) for i in items
                    if i.addon_id is not None and db_session.get(Addon, i.addon_id).addon_code == code]

        phone_items, desktop_items = _items("EXTRA_PHONE"), _items("EXTRA_DESKTOP")
        entry = db_session.execute(
            select(CommissionLedgerEntry).where(CommissionLedgerEntry.source_commercial_invoice_id == inv.id)
        ).scalars().one()
        return {
            "order_status": order.status, "invoice_total": Decimal(inv.total), "invoice_status": inv.status,
            "device_limit": lic.device_limit, "device_allowance": sub.device_allowance,
            "valid_from": lic.valid_from, "valid_until": lic.valid_until, "end_date": sub.end_date,
            "contract_term_months": getattr(sub, "contract_term_months", "NO COLUMN"),
            "contract_ends_on": getattr(sub, "contract_ends_on", "NO COLUMN"),
            "phone_invoice_items": phone_items, "desktop_invoice_items": desktop_items,
            "extra_desktops": lic.extra_desktop_slots, "extra_phones": lic.extra_phone_slots,
            "invoice_subtotal": Decimal(inv.subtotal), "invoice_tax": Decimal(inv.tax_total),
            "commission": Decimal(entry.commission_amount),
        }


def _months(n):
    from app.commercial_ops.renewal_dates import add_interval

    return add_interval(jordan_today(), months=n)  # licence terms start on Jordan's day


# --------------------------------------------- Package 1: 250, then 100 --

def test_p1_alone_charges_250_and_issues_a_one_year_two_device_licence(app, client, seeded):
    """RED on 7571c3df: the screen priced P1 at base_price only and refused
    250 as "not the full price 100.00". 250 = 100 + the 150 first-time fee."""
    w = _world(app)
    resp = _post_sale(client, app, w, "p1", amount="250.00")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]
    order_id = _only_order(app)
    assert _order_lines(app, order_id) == [("PLAN", 1, Decimal("250.00"), Decimal("250.00"))]
    _confirm(app, order_id, w["manager"])
    done = _finished(app, order_id)
    assert done["order_status"] == "FULFILLED" and done["invoice_status"] == "PAID"
    assert done["invoice_total"] == Decimal("250.00")
    assert done["device_limit"] == 2 and done["device_allowance"] == 2      # 2 devices of any kind
    assert (done["extra_desktops"], done["extra_phones"]) == (0, 0)
    assert (done["valid_from"], done["valid_until"]) == (jordan_today(), _months(12))
    assert (done["contract_term_months"], done["contract_ends_on"]) == (None, None)  # P1 has no contract
    assert done["phone_invoice_items"] == [] and done["desktop_invoice_items"] == []
    assert done["commission"] == Decimal("25.00")  # commission behaviour unchanged: 10% of what was paid


def test_p1_with_one_phone_charges_300_and_the_licence_gets_one_more_device(app, client, seeded):
    """RED on 7571c3df: no extras field existed (posted extras were ignored, the
    sale was priced 100 and the licence had 2 devices). Typed since 2026-10-31:
    the phone line is EXTRA_PHONE, quantity = phones, and the licence remembers it."""
    w = _world(app)
    resp = _post_sale(client, app, w, "p1", amount="300.00", phones=1)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]
    order_id = _only_order(app)
    assert _order_lines(app, order_id) == [
        ("PLAN", 1, Decimal("250.00"), Decimal("250.00")),
        ("EXTRA_PHONE", 1, Decimal("50.00"), Decimal("50.00")),   # quantity = phones
    ]
    _confirm(app, order_id, w["manager"])
    done = _finished(app, order_id)
    assert done["invoice_total"] == Decimal("300.00")
    assert done["device_limit"] == 3 and done["device_allowance"] == 3   # 2 included + 1 phone
    assert (done["extra_desktops"], done["extra_phones"]) == (0, 1)
    # The phone line stays identifiable on the invoice the commission ledger
    # reads, so the ladder can leave it out.
    assert done["phone_invoice_items"] == [(1, Decimal("50.00"))]


def test_two_desktops_are_two_extra_devices(app, client, seeded):
    w = _world(app)
    resp = _post_sale(client, app, w, "p1", amount="350.00", desktops=2)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]
    order_id = _only_order(app)
    _confirm(app, order_id, w["manager"])
    done = _finished(app, order_id)
    assert (done["device_limit"], done["extra_desktops"], done["extra_phones"]) == (4, 2, 0)


@pytest.mark.parametrize("amount,phones", [
    ("100.00", None),    # base price alone: the 150 first-time fee is owed
    ("150.00", None),    # setup fee alone
    ("300.00", None),    # a phone's money without the phone
    ("250.00", 1),       # a phone without its money
    ("299.99", 1),
])
def test_wrong_amount_for_the_package_is_refused_and_writes_nothing(app, client, seeded, amount, phones):
    """Both directions of the full-price rule with the new prices. RED on
    7571c3df for 100.00 (accepted there: base price only)."""
    w = _world(app)
    resp = _post_sale(client, app, w, "p1", amount=amount, phones=phones)
    assert resp.status_code == 400
    assert _counts(app) == NOTHING


def test_renewal_price_is_the_yearly_price_whatever_the_extra_devices(app, client, seeded):
    """The owner: the 100 JOD renewal never grows with extra devices, and the
    150 first-time fee is never charged again."""
    w = _world(app)
    resp = _post_sale(client, app, w, "p1", amount="400.00", phones=2, desktops=1)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]
    order_id = _only_order(app)
    _confirm(app, order_id, w["manager"])
    with app.app_context():
        from sqlalchemy import select

        from app.commercial_sales.packages import renewal_price
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        sub = db_session.execute(select(Subscription).where(Subscription.sales_order_id == order_id)).scalars().one()
        assert sub.device_allowance == 5
        assert renewal_price(sub) == (Decimal("100.00"), JOD)


def test_catalog_states_first_sale_and_renewal_prices(app, seeded):
    w = _world(app)
    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale

        p1 = describe_plan_for_sale(w["p1"])
        p2 = describe_plan_for_sale(w["p2"])
        assert (p1["first_sale_unit_price"], p1["renewal_unit_price"]) == (Decimal("250.00"), Decimal("100.00"))
        assert (p2["first_sale_unit_price"], p2["renewal_unit_price"]) == (Decimal("35.00"), Decimal("35.00"))
        assert p2["contract_term_months"] == 12 and p1["contract_term_months"] is None


# ----------------------------------- Packages 2 and 3: the first month --

def test_p2_records_the_first_month_and_stores_the_12_month_contract(app, client, seeded):
    """RED on 7571c3df: no contract term existed anywhere."""
    w = _world(app)
    resp = _post_sale(client, app, w, "p2", amount="35.00")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]
    order_id = _only_order(app)
    _confirm(app, order_id, w["manager"])
    done = _finished(app, order_id)
    assert done["invoice_total"] == Decimal("35.00") and done["device_limit"] == 2
    # The licence runs through the PAID period (one month); the contract is
    # stored on the subscription for the later months and the ladder.
    assert (done["valid_from"], done["valid_until"], done["end_date"]) == (jordan_today(), _months(1), _months(1))
    assert (done["contract_term_months"], done["contract_ends_on"]) == (12, _months(12))


def test_p2_with_a_phone_is_85_first_month(app, client, seeded):
    w = _world(app)
    assert _post_sale(client, app, w, "p2", amount="35.00", phones=1).status_code == 400
    assert _counts(app) == NOTHING
    resp = _post_sale(client, app, w, "p2", amount="85.00", phones=1)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]
    order_id = _only_order(app)
    _confirm(app, order_id, w["manager"])
    done = _finished(app, order_id)
    assert done["invoice_total"] == Decimal("85.00") and done["device_limit"] == 3
    assert done["contract_term_months"] == 12


def test_p3_is_20_a_month_on_a_24_month_contract(app, client, seeded):
    w = _world(app)
    assert _post_sale(client, app, w, "p3", amount="35.00").status_code == 400
    resp = _post_sale(client, app, w, "p3", amount="20.00")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]
    order_id = _only_order(app)
    _confirm(app, order_id, w["manager"])
    done = _finished(app, order_id)
    assert done["invoice_total"] == Decimal("20.00") and done["device_limit"] == 2
    assert done["valid_until"] == _months(1)
    assert (done["contract_term_months"], done["contract_ends_on"]) == (24, _months(24))


# -------------------------------------- typed extras, never singles or pairs --

def test_extras_beyond_the_package_maximum_are_refused(app, client, seeded):
    """Max 4 devices: 1 desktop + 1 phone (2 + 2) sells, one more is refused
    before any write. RED on 7571c3df (the extras were ignored)."""
    w = _world(app, p1_max_devices=4)
    assert _post_sale(client, app, w, "p1", amount="400.00", phones=2, desktops=1).status_code == 400
    assert _counts(app) == NOTHING
    resp = _post_sale(client, app, w, "p1", amount="350.00", phones=1, desktops=1)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]


def test_a_phone_or_desktop_is_never_sold_as_a_single_device(app, client, seeded):
    """The single EXTRA_DEVICE add-on on sale at 50 (as in the rehearsal
    catalogue) must not stand in for a typed extra: with no phone/desktop add-on on
    sale, each is refused; and the old single-device field adds nothing."""
    w = _world(app, phone_price=None, desktop_price=None, single_device_on_sale=True)
    assert _post_sale(client, app, w, "p1", amount="300.00", phones=1).status_code == 400
    assert _post_sale(client, app, w, "p1", amount="300.00", desktops=1).status_code == 400
    assert _counts(app) == NOTHING
    resp = _post_sale(client, app, w, "p1", amount="250.00", extra_devices="1")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]
    assert _order_lines(app, _only_order(app)) == [("PLAN", 1, Decimal("250.00"), Decimal("250.00"))]


@pytest.mark.parametrize("field", ["phones", "desktops"])
@pytest.mark.parametrize("count", ["-1", "two", "1.5"])
def test_bad_extra_counts_are_refused(app, client, seeded, field, count):
    w = _world(app)
    assert _post_sale(client, app, w, "p1", amount="250.00", **{field: count}).status_code == 400
    assert _counts(app) == NOTHING


def test_same_form_with_a_different_extras_count_is_a_different_sale(app, client, seeded):
    w = _world(app)
    key = str(uuid.uuid4())
    with app.app_context():
        from app.commercial_sales.direct_sale import DirectSaleError, record_direct_sale

        fields = dict(new_customer_legal_name="Corner Shop", plan_id=w["p1"], method="BANK_TRANSFER",
                      reference="CLIQ-1", payment_date=date.today(), idempotency_key=key,
                      actor_staff_user_id=w["seller"], actor_permission_codes=_codes(w["seller"]))
        record_direct_sale(extra_phones=1, amount=Decimal("300.00"), **fields)
        with pytest.raises(DirectSaleError) as exc:
            record_direct_sale(extra_phones=2, amount=Decimal("350.00"), **fields)
        assert exc.value.code == "KEY_USED_FOR_DIFFERENT_SALE"
        replay = record_direct_sale(extra_phones=1, amount=Decimal("300.00"), **fields)
        assert replay["replayed"] is True and replay["total"] == Decimal("300.00")


def test_resubmit_after_a_crash_with_a_different_extras_count_is_refused(app, seeded, monkeypatch):
    """The order (1 phone, 300) committed, then the process died before the
    payment. A resubmit of the same form key asking for 2 phones -- with the
    300 that the stored order totals -- must not finish the 1-phone sale.
    The amount check alone cannot catch this (300 IS the stored total); only
    the extras comparison can (mutation M20 survived the test above, whose
    second call differs in amount too)."""
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
    with app.app_context():
        from app.commercial_sales.direct_sale import DirectSaleError, record_direct_sale

        fields = dict(new_customer_legal_name="Corner Shop", plan_id=w["p1"], method="BANK_TRANSFER",
                      reference="CLIQ-1", payment_date=date.today(), idempotency_key=key,
                      actor_staff_user_id=w["seller"], actor_permission_codes=_codes(w["seller"]))
        with pytest.raises(RuntimeError):
            record_direct_sale(extra_phones=1, amount=Decimal("300.00"), **fields)
        with pytest.raises(DirectSaleError) as exc:
            record_direct_sale(extra_phones=2, amount=Decimal("300.00"), **fields)
        assert exc.value.code == "KEY_USED_FOR_DIFFERENT_SALE"
    assert _counts(app)["payments"] == 0
    with app.app_context():  # the allow half: the same form finishes its own sale
        from app.commercial_sales.direct_sale import record_direct_sale

        result = record_direct_sale(extra_phones=1, amount=Decimal("300.00"), **fields)
        assert result["replayed"] is False and result["total"] == Decimal("300.00")
    assert _counts(app)["payments"] == 1


def test_waiting_list_names_the_phones_and_desktops(app, client, seeded):
    w = _world(app)
    assert _post_sale(client, app, w, "p1", amount="400.00", phones=1, desktops=2).status_code == 200
    manager = app.test_client()  # a fresh browser: the seller is signed in on `client`
    login_and_verify_mfa(manager, _email(app, w["manager"]))
    body = manager.get("/licenses/issuance/sales").get_data(as_text=True)
    assert "+ 1 extra phone" in body and "+ 2 extra desktops" in body


def test_sale_form_shows_each_package_first_payment_then_and_contract(app, client, seeded):
    w = _world(app)
    force_login(client, app, w["seller"])
    body = client.get("/licenses/issuance/new").get_data(as_text=True)
    assert 'name="extra_phones"' in body and 'name="extra_desktops"' in body
    assert 'name="extra_device_pairs"' not in body and 'name="extra_devices"' not in body
    assert "first payment 250.00 JOD, then 100.00 JOD a year" in body
    assert "first payment 35.00 JOD, then 35.00 JOD a month; 12-month contract" in body
    assert "first payment 20.00 JOD, then 20.00 JOD a month; 24-month contract" in body
    assert "extra phone 50.00 JOD, extra desktop 50.00 JOD" in body


def test_sale_form_and_waiting_list_speak_arabic_for_the_new_words(app, client, seeded):
    w = _world(app)
    force_login(client, app, w["seller"])
    client.get("/locale/ar?next=/")
    body = client.get("/licenses/issuance/new").get_data(as_text=True)
    for arabic in ("الدفعة الأولى", "ثم", "سنويًا", "شهريًا", "عقد لمدة 12 شهرًا", "عقد لمدة 24 شهرًا",
                   "هاتف إضافي", "جهاز مكتبي إضافي", "هواتف إضافية (أندرويد فقط؛ تُدفع مرة واحدة ولا تدخل في التجديد)",
                   "أجهزة مكتبية إضافية (ويندوز فقط؛ تُدفع مرة واحدة ولا تدخل في التجديد)"):
        assert arabic in body, arabic
    for english in ("first payment", "-month contract", "extra phone ", "extra desktop "):
        assert english not in body, english
    resp = _post_sale(client, app, w, "p1", amount="300.00", phones="two")
    assert resp.status_code == 400
    assert "يجب أن يكون عدد الهواتف الإضافية وعدد الأجهزة المكتبية الإضافية عددًا صحيحًا." in resp.get_data(as_text=True)


# ----------------------------------- the quote path prices it the same way --

def test_quote_prices_a_package_at_its_first_sale_price(app, seeded):
    """RED on 7571c3df: a quote line for P1 was 100.00 -- the quote pipeline
    and the sale screen would have priced one package two ways."""
    w = _world(app)
    with app.app_context():
        from app.commercial_sales.quotes import add_quote_line, create_quote
        from app.customers.services import create_customer

        customer = create_customer({"legal_name": "Quote Shop"}, w["seller"])
        quote = create_quote({"customer_id": customer.id, "currency": JOD},
                             actor_employee_profile_id=w["seller_profile"], actor_staff_user_id=w["seller"])
        line = add_quote_line(quote, plan_id=w["p1"], addon_id=None, quantity=1, actor_staff_user_id=w["seller"])
        assert Decimal(line.unit_price) == Decimal("250.00") and Decimal(quote.total) == Decimal("250.00")
        p2_quote = create_quote({"customer_id": customer.id, "currency": JOD},
                                actor_employee_profile_id=w["seller_profile"], actor_staff_user_id=w["seller"])
        p2_line = add_quote_line(p2_quote, plan_id=w["p2"], addon_id=None, quantity=1, actor_staff_user_id=w["seller"])
        assert Decimal(p2_line.unit_price) == Decimal("35.00")


# ------------------------------- the catalog keeps the first-time fee --

def test_a_new_price_keeps_the_setup_fee_unless_one_is_given(app, seeded):
    """RED on 7571c3df: add_plan_price (the catalog screen's only price
    writer) wrote setup_fee=0 on every new price row, so raising P1's yearly
    price from the screen would silently drop the 150 first-time fee."""
    w = _world(app)
    with app.app_context():
        from app.catalog.services import add_plan_price
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale
        from app.extensions import db_session
        from app.models.catalog import Plan

        plan = db_session.get(Plan, w["p1"])
        add_plan_price(plan, Decimal("110.00"), JOD, date.today(), actor_staff_user_id=w["admin"])
        item = describe_plan_for_sale(w["p1"])
        assert (item["unit_price"], item["setup_fee"]) == (Decimal("110.00"), Decimal("150.00"))
        add_plan_price(plan, Decimal("110.00"), JOD, date.today() + timedelta(days=1), actor_staff_user_id=w["admin"],
                       setup_fee=Decimal("0"))
        item = describe_plan_for_sale(w["p1"])
        assert item["setup_fee"] == Decimal("0")


def test_catalog_price_form_records_the_setup_fee(app, client, seeded):
    w = _world(app)
    login_and_verify_mfa(client, _email(app, w["admin"]))
    page = client.get(f"/catalog/plans/{w['p1']}")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert 'name="setup_fee"' in html and "150.00" in html
    resp = client.post(f"/catalog/plans/{w['p1']}/prices", data={
        "csrf_token": get_csrf(html), "base_price": "120.00", "setup_fee": "130.00", "currency": JOD,
        "effective_from": date.today().isoformat(),
    })
    assert resp.status_code == 302
    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale

        item = describe_plan_for_sale(w["p1"])
        assert (item["unit_price"], item["setup_fee"], item["first_sale_unit_price"]) == (
            Decimal("120.00"), Decimal("130.00"), Decimal("250.00"))


# --------------------------------------------------- the seed command --

def _seed_packages(app, *args):
    result = app.test_cli_runner().invoke(args=["seed-packages", *args])
    assert result.exit_code == 0, result.output
    return result.output


def _retail_packages(app):
    with app.app_context():
        from sqlalchemy import select

        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale
        from app.extensions import db_session
        from app.models.catalog import Plan

        out = {}
        for code in ("AURA_RETAIL_P1_YEARLY", "AURA_RETAIL_P2_MONTHLY_12", "AURA_RETAIL_P3_MONTHLY_24"):
            plan = db_session.execute(select(Plan).where(Plan.plan_code == code)).scalars().one()
            item = describe_plan_for_sale(plan.id)
            out[code] = (plan.billing_model, plan.billing_interval_months, plan.contract_term_months,
                         plan.included_device_count, item["unit_price"], item["setup_fee"], item["currency"])
        return out


def test_seed_packages_puts_the_owners_three_packages_and_the_typed_extras_on_sale(app, seeded):
    _seed_packages(app)
    assert _retail_packages(app) == {
        "AURA_RETAIL_P1_YEARLY": ("ANNUAL", 12, None, 2, Decimal("100.00"), Decimal("150.00"), JOD),
        "AURA_RETAIL_P2_MONTHLY_12": ("MONTHLY", 1, 12, 2, Decimal("35.00"), Decimal("100.00"), JOD),
        "AURA_RETAIL_P3_MONTHLY_24": ("MONTHLY", 1, 24, 2, Decimal("25.00"), Decimal("100.00"), JOD),
    }
    with app.app_context():
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.catalog import Addon, Plan, PlanPrice

        for code in ("EXTRA_PHONE", "EXTRA_DESKTOP"):
            extra = db_session.execute(select(Addon).where(Addon.addon_code == code)).scalars().one()
            assert (extra.availability_status, Decimal(extra.price), extra.currency) == ("AVAILABLE", Decimal("50.00"), JOD)
        for code in ("EXTRA_DEVICE_PAIR", "EXTRA_BRANCH"):  # no longer sold (owner, 2026-10-31)
            retired = db_session.execute(select(Addon).where(Addon.addon_code == code)).scalars().one()
            assert retired.availability_status == "RETIRED", code
        counts = (db_session.execute(select(func.count()).select_from(Plan)).scalar_one(),
                  db_session.execute(select(func.count()).select_from(PlanPrice)).scalar_one())
    _seed_packages(app)  # idempotent: nothing new, nothing changed
    with app.app_context():
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.catalog import Plan, PlanPrice

        assert (db_session.execute(select(func.count()).select_from(Plan)).scalar_one(),
                db_session.execute(select(func.count()).select_from(PlanPrice)).scalar_one()) == counts


def test_seed_packages_rerun_moves_the_2026_10_24_monthly_prices_to_the_new_list(app, seeded):
    """Owner, 2026-10-26: P3 went 20 -> 25 and both monthly packages gained
    the 100 activation. A database seeded with the OLD list is updated by a
    re-run, with the old price kept as history and no plan or price row
    duplicated; a price someone else changed (next test) is left alone."""
    _seed_packages(app)
    with app.app_context():
        from datetime import timedelta

        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import Plan, PlanPrice

        for code, (base, setup) in (("AURA_RETAIL_P2_MONTHLY_12", ("35.00", "0.00")),
                                    ("AURA_RETAIL_P3_MONTHLY_24", ("20.00", "0.00"))):
            plan = db_session.execute(select(Plan).where(Plan.plan_code == code)).scalars().one()
            price = db_session.execute(select(PlanPrice).where(PlanPrice.plan_id == plan.id)).scalars().one()
            price.base_price, price.setup_fee = Decimal(base), Decimal(setup)
            price.effective_from = date.today() - timedelta(days=2)  # seeded two days ago
        db_session.commit()
    assert _retail_packages(app)["AURA_RETAIL_P3_MONTHLY_24"][4:6] == (Decimal("20.00"), Decimal("0.00"))
    output = _seed_packages(app)
    assert _retail_packages(app)["AURA_RETAIL_P3_MONTHLY_24"][4:6] == (Decimal("25.00"), Decimal("100.00"))
    assert _retail_packages(app)["AURA_RETAIL_P2_MONTHLY_12"][4:6] == (Decimal("35.00"), Decimal("100.00"))
    assert "prices_updated" in output
    with app.app_context():
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.catalog import Plan, PlanPrice

        plan = db_session.execute(select(Plan).where(Plan.plan_code == "AURA_RETAIL_P3_MONTHLY_24")).scalars().one()
        rows = db_session.execute(select(PlanPrice).where(PlanPrice.plan_id == plan.id)
                                  .order_by(PlanPrice.effective_from)).scalars().all()
        assert [(Decimal(r.base_price), r.effective_until is None) for r in rows] == [
            (Decimal("20.00"), False), (Decimal("25.00"), True)]
        assert db_session.execute(select(func.count()).select_from(Plan).where(
            Plan.plan_code == "AURA_RETAIL_P3_MONTHLY_24")).scalar_one() == 1
    _seed_packages(app)  # and again: nothing more changes
    with app.app_context():
        from sqlalchemy import func, select

        from app.extensions import db_session
        from app.models.catalog import PlanPrice

        assert db_session.execute(select(func.count()).select_from(PlanPrice).where(
            PlanPrice.plan_id == plan.id)).scalar_one() == 2


def test_seed_packages_never_overwrites_a_price_someone_changed(app, seeded):
    _seed_packages(app)
    with app.app_context():
        from sqlalchemy import select

        from app.catalog.services import add_plan_price
        from app.extensions import db_session
        from app.models.catalog import Plan

        plan = db_session.execute(select(Plan).where(Plan.plan_code == "AURA_RETAIL_P2_MONTHLY_12")).scalars().one()
        add_plan_price(plan, Decimal("39.00"), JOD, date.today(), actor_staff_user_id=None)
    output = _seed_packages(app)
    assert _retail_packages(app)["AURA_RETAIL_P2_MONTHLY_12"][4] == Decimal("39.00")
    assert "AURA_RETAIL_P2_MONTHLY_12" in output and "39.00" in output  # reported, not silently kept


def test_seed_packages_can_retire_the_other_retail_plans_but_not_one_with_an_open_sale(app, client, seeded):
    w = _world(app)  # T_P1/T_P2/T_P3: other sellable Retail plans
    assert _post_sale(client, app, w, "p2", amount="35.00").status_code == 200   # an open sale on T_P2
    output = _seed_packages(app)
    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale

        assert describe_plan_for_sale(w["p1"]) is not None  # reported only without the flag
    assert "T_P1" in output
    output = _seed_packages(app, "--retire-other-retail-plans")
    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale

        assert describe_plan_for_sale(w["p1"]) is None and describe_plan_for_sale(w["p3"]) is None
        assert describe_plan_for_sale(w["p2"]) is not None  # its open sale must still be confirmable
    assert "T_P2" in output


# ------------------------------- end to end on the seeded catalog --

def test_seeded_p1_with_a_phone_and_seeded_p3_sell_end_to_end(app, client, seeded):
    w = _world(app)
    _seed_packages(app)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.catalog import Plan

        w["sp1"] = db_session.execute(select(Plan).where(Plan.plan_code == "AURA_RETAIL_P1_YEARLY")).scalars().one().id
        w["sp3"] = db_session.execute(select(Plan).where(Plan.plan_code == "AURA_RETAIL_P3_MONTHLY_24")).scalars().one().id
    assert _post_sale(client, app, w, "sp1", amount="300.00", phones=1).status_code == 200
    assert _post_sale(client, app, w, "sp3", amount="125.00").status_code == 200  # 25 a month + 100 activation
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder

        orders = db_session.execute(select(SalesOrder).order_by(SalesOrder.created_at)).scalars().all()
        ids = [o.id for o in orders]
    manager = app.test_client()  # a fresh browser: the seller is signed in on `client`
    login_and_verify_mfa(manager, _email(app, w["manager"]))
    page = manager.get("/licenses/issuance/sales")
    for order_id in ids:
        resp = manager.post(f"/licenses/issuance/sales/{order_id}/confirm",
                           data={"csrf_token": get_csrf(page.get_data(as_text=True))})
        assert resp.status_code == 200 and "AURA-" in resp.get_data(as_text=True)
    p1, p3 = _finished(app, ids[0]), _finished(app, ids[1])
    assert (p1["invoice_total"], p1["device_limit"], p1["valid_until"], p1["contract_term_months"]) == (
        Decimal("300.00"), 3, _months(12), None)
    assert (p3["invoice_total"], p3["device_limit"], p3["valid_until"], p3["contract_term_months"],
            p3["contract_ends_on"]) == (Decimal("125.00"), 2, _months(1), 24, _months(24))  # 25 a month + 100 activation
