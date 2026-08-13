"""Phase 9.5D Milestone 5 -- Quote domain service tests."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import make_staff


def _seed_sales_employee(app, email):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=["SALES"])
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id,
                "employee_number": f"EMP-{email[:6].upper()}",
                "full_name": f"Sales {email}",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        return staff_id, profile.id


def _seed_customer(app, staff_id):
    from app.customers.services import create_customer

    with app.app_context():
        customer = create_customer({"legal_name": "Quote Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, *, plan_code="QUOTE_TEST_PLAN", price=Decimal("99.00"), currency="USD"):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Quote Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Quote Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, currency, date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def test_create_quote_generates_number_and_defaults(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quotea@example.com")
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.commercial_sales.quotes import create_quote

        quote = create_quote(
            {"customer_id": customer_id, "currency": "usd"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id
        )
        assert quote.status == "DRAFT"
        assert quote.currency == "USD"
        assert quote.quote_number.startswith("Q-")
        assert quote.total == Decimal("0.00")
        assert quote.version == 1


def test_add_line_recomputes_totals(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quoteb@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, plan_code="QB_PLAN")
    with app.app_context():
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        line = add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=2, actor_staff_user_id=staff_id)

        assert line.line_total == Decimal("198.00")
        assert quote.subtotal == Decimal("198.00")
        assert quote.total == Decimal("198.00")
        assert quote.version == 2


def test_add_line_to_non_draft_quote_rejected(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quotec@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, plan_code="QC_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)

        with pytest.raises(CommercialSalesError) as exc:
            add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        assert exc.value.code == "INVALID_QUOTE_TRANSITION"


def test_submit_empty_quote_rejected(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quoted@example.com")
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import create_quote, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        with pytest.raises(CommercialSalesError) as exc:
            submit_quote(quote, actor_staff_user_id=staff_id)
        assert exc.value.code == "EMPTY_DOCUMENT"


def test_full_accept_lifecycle(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quotee@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, plan_code="QE_PLAN")
    with app.app_context():
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)
        assert quote.status == "SENT"

        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
        assert quote.status == "ACCEPTED"
        assert quote.accepted_at is not None


def test_reject_requires_reason(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quotef@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, plan_code="QF_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)

        with pytest.raises(CommercialSalesError) as exc:
            record_customer_decision(quote, accepted=False, actor_staff_user_id=staff_id)
        assert exc.value.code == "REASON_REQUIRED"


def test_accepted_quote_cannot_be_cancelled(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quoteg@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, plan_code="QG_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, cancel_quote, create_quote, record_customer_decision, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote, actor_staff_user_id=staff_id)
        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)

        with pytest.raises(CommercialSalesError) as exc:
            cancel_quote(quote, reason="changed mind", actor_staff_user_id=staff_id)
        assert exc.value.code == "INVALID_QUOTE_TRANSITION"


def test_stale_version_rejected(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quoteh@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, plan_code="QH_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)

        with pytest.raises(CommercialSalesError) as exc:
            submit_quote(quote, actor_staff_user_id=staff_id, expected_version=quote.version - 1)
        assert exc.value.code == "STALE_VERSION"


def test_currency_mismatch_between_quote_and_catalog_item_rejected(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quotei@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, plan_code="QI_PLAN", currency="EUR")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        with pytest.raises(CommercialSalesError) as exc:
            add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        assert exc.value.code == "CURRENCY_MISMATCH"


def test_inactive_catalog_item_rejected(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quotej@example.com")
    customer_id = _seed_customer(app, staff_id)
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        with pytest.raises(CommercialSalesError) as exc:
            add_quote_line(quote, plan_id=uuid.uuid4(), addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        assert exc.value.code == "CATALOG_ITEM_INACTIVE"


def test_expire_stale_quotes_only_affects_sent_not_accepted(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "quotek@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, plan_code="QK_PLAN")
    with app.app_context():
        from app.commercial_sales.quotes import add_quote_line, create_quote, expire_stale_quotes, record_customer_decision, submit_quote

        # One reference date drives BOTH the backdated valid_until values and
        # expire_stale_quotes()'s as_of.
        #
        # The bug this closes: the test backdated valid_until against LOCAL
        # date.today() but let expire_stale_quotes() default as_of to UTC
        # utcnow().date() (quotes.py:354). East of UTC those disagree for the
        # first N hours of each local day -- at UTC+3, 00:00-03:00 local -- so
        # valid_until == as_of, the `valid_until < as_of` filter is false, the
        # quote never expires, and both `count == 1` and the EXPIRED assertion
        # fail. Deterministic within that window, not a race.
        #
        # expire_stale_quotes() already accepts as_of for exactly this reason,
        # so pinning it is more surgical than freezing the process clock.
        today = date.today()

        quote_sent = create_quote(
            {"customer_id": customer_id, "currency": "USD", "valid_until": today - timedelta(days=1)},
            actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id,
        )
        add_quote_line(quote_sent, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote_sent, actor_staff_user_id=staff_id)

        # Accepted while still valid -- an already-expired quote cannot
        # legitimately be accepted in the first place (correct behavior,
        # covered separately). valid_until is backdated *after*
        # acceptance to simulate time passing since a real acceptance.
        quote_accepted = create_quote(
            {"customer_id": customer_id, "currency": "USD"},
            actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id,
        )
        add_quote_line(quote_accepted, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)
        submit_quote(quote_accepted, actor_staff_user_id=staff_id)
        record_customer_decision(quote_accepted, accepted=True, actor_staff_user_id=staff_id)
        quote_accepted.valid_until = today - timedelta(days=1)

        count = expire_stale_quotes(as_of=today)
        assert count == 1
        assert quote_sent.status == "EXPIRED"
        assert quote_accepted.status == "ACCEPTED"  # never silently expires
