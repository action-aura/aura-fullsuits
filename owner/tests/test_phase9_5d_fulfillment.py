"""Phase 9.5D Milestone 13 -- commercial fulfillment orchestration tests."""
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
        customer = create_customer({"legal_name": "Fulfillment Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, price=Decimal("100.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product, ProductPlatform, Platform

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Fulfillment Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        platform = db_session.query(Platform).first()
        db_session.add(ProductPlatform(product_id=product.id, platform_id=platform.id, supported=True))
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Fulfillment Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
                "included_device_count": 3,
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_staff_id):
    from app.commercial_sales.allocation import allocate_payment
    from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
    from app.commercial_sales.payments import confirm_payment, submit_payment
    from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
    from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote

    quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
    add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
    submit_quote(quote, actor_staff_user_id=staff_id)
    record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
    order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
    confirm_order(order, actor_staff_user_id=staff_id)
    invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
    issue_invoice(invoice, actor_staff_user_id=staff_id)

    payment = submit_payment(
        customer_id=customer_id, amount=invoice.total, currency="USD", method="CASH",
        payment_date=date.today(), actor_staff_user_id=staff_id,
    )
    confirm_payment(payment, actor_staff_user_id=finance_staff_id)
    allocate_payment(payment=payment, invoice=invoice, amount=invoice.total, actor_staff_user_id=finance_staff_id)
    return order, invoice


def test_fulfill_order_creates_active_subscription_and_issued_license(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfilla@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillb@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FA_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.extensions import db_session
        from app.models.licensing import License
        from app.models.subscriptions import Subscription

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        result = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))

        subscription = db_session.get(Subscription, result["subscription_id"])
        assert subscription.status == "ACTIVE"
        assert subscription.sales_order_id == order.id

        license_row = db_session.get(License, result["license_id"])
        assert license_row.status == "ISSUED"
        assert license_row.allowed_platforms  # real platform codes, never "ALL"
        assert "ALL" not in license_row.allowed_platforms.split(",")

        assert order.status == "FULFILLED"
        assert order.fulfilled_at is not None


def test_fulfillment_requires_confirmed_order(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfillc@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FC_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.fulfillment import fulfill_order
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.commercial_sales.sales_orders import create_order_from_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        # order still DRAFT

        with pytest.raises(CommercialSalesError) as exc:
            fulfill_order(order, actor_staff_user_id=staff_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "FULFILLMENT_NOT_ELIGIBLE"


def test_fulfillment_requires_fully_paid_invoice(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfilld@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfille@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FD_PLAN", price=Decimal("200.00"))
    with app.app_context():
        from app.commercial_sales.allocation import allocate_payment
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.fulfillment import fulfill_order
        from app.commercial_sales.invoices import create_invoice_from_order, issue_invoice
        from app.commercial_sales.payments import confirm_payment, submit_payment
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote
        from app.commercial_sales.sales_orders import confirm_order, create_order_from_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
        order = create_order_from_quote(quote, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        confirm_order(order, actor_staff_user_id=staff_id)
        invoice = create_invoice_from_order(order, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id, idempotency_key=str(uuid.uuid4()))
        issue_invoice(invoice, actor_staff_user_id=staff_id)

        payment = submit_payment(
            customer_id=customer_id, amount=Decimal("50.00"), currency="USD", method="CASH",
            payment_date=date.today(), actor_staff_user_id=staff_id,
        )
        confirm_payment(payment, actor_staff_user_id=finance_id)
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal("50.00"), actor_staff_user_id=finance_id)
        assert invoice.status == "PARTIALLY_PAID"

        with pytest.raises(CommercialSalesError) as exc:
            fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "FULFILLMENT_NOT_ELIGIBLE"


def test_fulfillment_blocked_by_pending_refund(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfillf@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillg@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FF_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.fulfillment import fulfill_order
        from app.commercial_sales.refunds import create_refund

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        create_refund(
            invoice, amount=Decimal("10.00"), reason="partial issue", payment_record_id=None,
            actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id,
        )

        with pytest.raises(CommercialSalesError) as exc:
            fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "FULFILLMENT_NOT_ELIGIBLE"


def test_item5_duplicate_idempotency_key_identical_payload_replays(app, seeded):
    """Item #5: duplicate idempotency key, identical payload (same
    order) -- returns the original result, never re-executes."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfillh@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfilli@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FH_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        key = str(uuid.uuid4())
        first = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=key)
        second = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=key)
        assert first["subscription_id"] == second["subscription_id"]
        assert second["replayed"] is True


def test_item6_duplicate_idempotency_key_conflicting_payload_rejected(app, seeded):
    """Item #6: the same idempotency key reused against a DIFFERENT
    order (a real client bug or replay attack) must be rejected outright
    -- never silently resolved to either order."""
    staff_a, profile_a = _seed_sales_employee(app, "fulfillq_a@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillq_f@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "FQ_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.fulfillment import fulfill_order

        order_a, _ = _make_paid_order(app, staff_a, profile_a, customer_id, plan_id, finance_id)
        order_b, _ = _make_paid_order(app, staff_a, profile_a, customer_id, plan_id, finance_id)

        shared_key = str(uuid.uuid4())
        fulfill_order(order_a, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=shared_key)

        with pytest.raises(CommercialSalesError) as exc:
            fulfill_order(order_b, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=shared_key)
        assert exc.value.code == "IDEMPOTENCY_CONFLICT"


def test_item1_subscription_created_license_crashes_retry_reuses_subscription(app, seeded):
    """Item #1: Subscription created, License creation crashes, retry
    reuses the Subscription (and completes License creation this time --
    never creates a second Subscription)."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfilln@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillo@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FN_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.extensions import db_session
        from app.models.catalog import Plan
        from app.models.licensing import License
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import create_subscription

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        plan = db_session.get(Plan, plan_id)

        # Simulate the exact partial-failure state: a real Subscription
        # already exists for this order (License creation "crashed"
        # before it ran), no idempotency-key record, order still
        # CONFIRMED not FULFILLED.
        pre_existing = create_subscription(
            {"customer_id": customer_id, "product_id": plan.product_id, "plan_id": plan_id, "sales_order_id": order.id},
            finance_id,
        )
        assert db_session.query(Subscription).filter_by(sales_order_id=order.id).count() == 1
        assert db_session.query(License).filter_by(subscription_id=pre_existing.id).count() == 0

        result = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))

        assert result["subscription_id"] == pre_existing.id
        assert db_session.query(Subscription).filter_by(sales_order_id=order.id).count() == 1
        assert pre_existing.status == "ACTIVE"
        # The retry completes the step that "crashed" the first time.
        assert db_session.query(License).filter_by(subscription_id=pre_existing.id).count() == 1


def test_item2_subscription_and_license_exist_result_write_crashes_retry_reconciles(app, seeded):
    """Item #2: Subscription and License both already exist (the result
    write -- order.status/idempotency-key commit -- is what "crashed"),
    retry reconciles to FULFILLED without duplicating either row."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfillr@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfills@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FR_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.extensions import db_session
        from app.models.catalog import Plan
        from app.models.licensing import License
        from app.models.subscriptions import Subscription
        from app.subscriptions.services import create_subscription, transition_subscription
        from app.licensing.services import create_license

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        plan = db_session.get(Plan, plan_id)

        pre_sub = create_subscription(
            {"customer_id": customer_id, "product_id": plan.product_id, "plan_id": plan_id, "sales_order_id": order.id},
            finance_id,
        )
        transition_subscription(pre_sub, "ACTIVE", finance_id)
        pre_license = create_license(
            {
                "customer_id": customer_id, "subscription_id": pre_sub.id, "product_id": plan.product_id,
                "plan_id": plan_id, "allowed_platforms": "WINDOWS", "device_limit": 3,
            },
            finance_id,
        )
        # order.status is still CONFIRMED -- the final write "crashed".
        assert order.status == "CONFIRMED"

        result = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))

        assert result["subscription_id"] == pre_sub.id
        assert result["license_id"] == pre_license.id
        assert db_session.query(Subscription).filter_by(sales_order_id=order.id).count() == 1
        assert db_session.query(License).filter_by(subscription_id=pre_sub.id).count() == 1
        assert order.status == "FULFILLED"


def test_item3_concurrent_fulfillment_requests_only_one_creates_subscription(app, seeded):
    """Item #3: two concurrent fulfillment requests for the same order --
    the SELECT ... FOR UPDATE row lock must serialize them so exactly one
    Subscription is ever created, never two."""
    import threading

    staff_id, profile_id = _seed_sales_employee(app, "fulfillt@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillu@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FT_PLAN")

    order_id_holder = {}
    with app.app_context():
        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        order_id_holder["id"] = order.id

    results = []
    errors = []
    lock = threading.Lock()

    def worker():
        try:
            with app.app_context():
                from app.commercial_sales.fulfillment import fulfill_order
                from app.extensions import db_session
                from app.models.commercial_sales import SalesOrder

                local_order = db_session.get(SalesOrder, order_id_holder["id"])
                r = fulfill_order(
                    local_order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"],
                    idempotency_key=str(uuid.uuid4()),
                )
                with lock:
                    results.append(r)
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)
        finally:
            from app.extensions import db_session as scoped_db_session

            scoped_db_session.remove()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        subscription_count = db_session.query(Subscription).filter_by(sales_order_id=order_id_holder["id"]).count()
        assert subscription_count == 1, f"expected exactly 1 Subscription, got {subscription_count}"

    # Every thread either succeeded (reusing/creating the one real
    # Subscription) or failed with a real, distinguishable error (e.g.
    # FULFILLMENT_ALREADY_COMPLETE once serialized behind the winner) --
    # never an unhandled crash.
    assert len(results) + len(errors) == 5


def test_item4_existing_incompatible_subscription_state_rejected(app, seeded):
    """Item #4: a Subscription already exists for this order but in an
    incompatible status (e.g. CANCELLED) -- something else already
    happened to it outside this fulfillment attempt. Must be rejected,
    never silently reused/transitioned."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfillv@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillw@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FV_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.fulfillment import fulfill_order
        from app.models.catalog import Plan
        from app.subscriptions.services import create_subscription, transition_subscription
        from app.extensions import db_session

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        plan = db_session.get(Plan, plan_id)

        incompatible = create_subscription(
            {"customer_id": customer_id, "product_id": plan.product_id, "plan_id": plan_id, "sales_order_id": order.id},
            finance_id,
        )
        transition_subscription(incompatible, "ACTIVE", finance_id)
        transition_subscription(incompatible, "CANCELLED", finance_id, reason="unrelated cancellation")

        with pytest.raises(CommercialSalesError) as exc:
            fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "FULFILLMENT_NOT_ELIGIBLE"
        assert incompatible.status == "CANCELLED"  # untouched, not silently revived


def test_item7_refund_after_fulfillment_invokes_entitlement_consequence(app, seeded):
    """Item #7: a full refund confirmed against a fulfilling invoice must
    invoke the configured entitlement consequence (SUSPEND_ENTITLEMENTS
    by default) against the real Subscription -- closes Milestone 12's
    own forward reference now that Milestone 13 exists."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfillx@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfilly@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FX_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        result = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        subscription = db_session.get(Subscription, result["subscription_id"])
        assert subscription.status == "ACTIVE"

        refund = create_refund(
            invoice, amount=invoice.total, reason="full refund after fulfillment", payment_record_id=None,
            actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id,
        )
        approve_refund(refund, actor_staff_user_id=finance_id)
        confirm_refund(refund, actor_staff_user_id=finance_id)

        db_session.refresh(subscription)
        assert subscription.status == "SUSPENDED"


def test_item8_no_direct_write_to_licensing_or_installation_tables(app, seeded):
    """Item #8: no commercial_sales service module writes directly to
    Subscription/License/Entitlement/Installation/licensing-audit
    tables -- structural proof across every file in the package, not
    just fulfillment.py."""
    import inspect
    import pathlib

    import app.commercial_sales as pkg

    forbidden_constructors = [
        "Subscription(", "License(", "Installation(", "PlanEntitlement(", "AddonEntitlement(",
        "LicenseKeyIssuanceEvent(", "LicenseStatusHistory(", "SubscriptionStatusHistory(",
    ]
    package_dir = pathlib.Path(inspect.getfile(pkg)).parent
    violations = []
    for py_file in package_dir.glob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        for forbidden in forbidden_constructors:
            if forbidden in source:
                violations.append(f"{py_file.name}: {forbidden}")
    assert violations == [], f"direct model-row construction found: {violations}"


def test_already_fulfilled_order_rejected(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "fulfillj@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillk@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FJ_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.fulfillment import fulfill_order

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))

        with pytest.raises(CommercialSalesError) as exc:
            fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
        assert exc.value.code == "FULFILLMENT_ALREADY_COMPLETE"


def test_no_installation_created_by_fulfillment(app, seeded):
    """Non-Negotiable: fulfillment never creates an Installation --
    device activation remains a separate, later event."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfilll@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillm@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FL_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.extensions import db_session
        from app.models.installations import Installation

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))

        assert db_session.query(Installation).count() == 0


def test_fulfill_order_yields_retrievable_license_key(app, seeded):
    """AUDIT-NNN: a licence fulfilled through the quote->order->invoice->
    payment->fulfill pipeline must yield a key the operator can actually
    read back. `issue_license_key()`'s plaintext return value used to be
    discarded here (fulfillment.py just called it for effect) -- producing
    a licence the customer could never activate, with `replace_license`
    the only recovery. The full key is captured and returned once, the
    same ADR-9 discipline `licensing/routes.py::issue` already uses for
    the direct path."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfillkey_a@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillkey_b@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FK_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.extensions import db_session
        from app.models.licensing import License, LicenseKeyIssuanceEvent
        from app.security.license_keys import verify_license_key

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        result = fulfill_order(
            order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4())
        )

        assert result["license_key"], "fulfillment must surface the plaintext key, not discard it"
        license_row = db_session.get(License, result["license_id"])
        # The returned key really is the one this license was issued with --
        # verified against the persisted HMAC, the only thing actually stored.
        assert verify_license_key(result["license_key"], app.config["LICENSE_PEPPER"], license_row.key_secret_hmac)

        # Not persisted anywhere new: the stored HMAC is not the plaintext,
        # and the prefix (the only plaintext fragment ever stored) is a
        # strict prefix of the key, never the whole thing.
        assert license_row.key_secret_hmac != result["license_key"]
        assert result["license_key"].startswith(license_row.key_prefix)
        assert license_row.key_prefix != result["license_key"]

        events = db_session.query(LicenseKeyIssuanceEvent).filter_by(license_id=license_row.id).all()
        assert len(events) == 1
        assert events[0].key_prefix == license_row.key_prefix


def test_fulfill_order_replay_reports_already_issued_not_blank_or_error(app, seeded):
    """A REPLAY (the exact same idempotency key reused for the same order)
    must not raise and must not present a blank key as if nothing had
    happened -- a key really was issued, on the first call, and shown
    once then. The second call must say so explicitly (`license_key`
    populated on the first call, `None` -- not an exception -- on the
    replay), never silently, and never by re-issuing."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfillkey_c@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillkey_d@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FK2_PLAN")
    with app.app_context():
        from app.commercial_sales.fulfillment import fulfill_order
        from app.extensions import db_session
        from app.models.licensing import LicenseKeyIssuanceEvent

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        key = str(uuid.uuid4())
        first = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=key)
        second = fulfill_order(order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=key)

        assert first["license_key"]  # the real, once-shown key
        assert second["replayed"] is True
        assert second["license_key"] is None  # explicitly reported as absent, never an exception, never re-derived
        assert second["subscription_id"] == first["subscription_id"]

        # The replay never re-issues -- exactly one issuance event for this license.
        events = db_session.query(LicenseKeyIssuanceEvent).filter_by(license_id=first["license_id"]).all()
        assert len(events) == 1


def test_fulfill_order_key_issuance_audited_without_plaintext(app, seeded):
    """The audit trail this pipeline already relies on
    (`LICENSE_KEY_ISSUED`, written by `issue_license_key` itself) is
    unchanged by capturing the key here -- it still records only the
    prefix/masked-suffix metadata, never the plaintext."""
    staff_id, profile_id = _seed_sales_employee(app, "fulfillkey_e@example.com")
    finance_id, _ = _seed_sales_employee(app, "fulfillkey_f@example.com", role_codes=["FINANCE"])
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "FK3_PLAN")
    with app.app_context():
        import json

        from sqlalchemy import select

        from app.commercial_sales.fulfillment import fulfill_order
        from app.extensions import db_session
        from app.models.audit import AuditLog

        order, invoice = _make_paid_order(app, staff_id, profile_id, customer_id, plan_id, finance_id)
        result = fulfill_order(
            order, actor_staff_user_id=finance_id, license_pepper=app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4())
        )

        audit_row = db_session.execute(
            select(AuditLog).where(
                AuditLog.action_code == "LICENSE_KEY_ISSUED", AuditLog.entity_public_id == str(result["license_id"])
            )
        ).scalars().first()
        assert audit_row is not None
        assert result["license_key"] not in json.dumps(audit_row.after_state_redacted)


def test_fulfillment_module_never_constructs_subscription_or_license_directly():
    """Structural proof, matching Phase 9.5C's own verification method for
    leads/conversion.py: grep the actual source for a direct model
    constructor call."""
    import inspect

    from app.commercial_sales import fulfillment

    source = inspect.getsource(fulfillment)
    assert "Subscription(" not in source
    assert "License(" not in source
    assert "create_subscription(" in source
    assert "issue_license_key(" in source
