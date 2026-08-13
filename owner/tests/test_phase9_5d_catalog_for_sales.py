"""Phase 9.5D Milestone 4 -- sales-facing catalog read service tests."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest


def _seed_product_and_plan(app, *, product_code="TEST_PRODUCT", plan_code="TEST_PLAN", effective_date=None, retirement_date=None):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    seed_canonical_catalog()
    product = Product(product_code=product_code, name="Test Product", is_active=True, is_sellable=True)
    db_session.add(product)
    db_session.flush()
    plan = create_plan(
        {
            "plan_code": plan_code,
            "product_id": product.id,
            "name": "Test Plan",
            "billing_model": "MONTHLY",
            "effective_date": effective_date or (date.today() - timedelta(days=1)),
            "retirement_date": retirement_date,
        },
        actor_staff_user_id=None,
    )
    add_plan_price(plan, Decimal("49.99"), "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
    return product, plan


def test_active_plan_with_current_price_is_sellable(app, seeded):
    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale

        _, plan = _seed_product_and_plan(app)
        result = describe_plan_for_sale(plan.id)

        assert result is not None
        assert result["plan_code"] == "TEST_PLAN"
        assert result["unit_price"] == Decimal("49.99")
        assert result["currency"] == "USD"
        assert result["price_version_id"]


def test_plan_with_no_current_price_not_sellable(app, seeded):
    with app.app_context():
        from app.catalog.services import create_plan
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale
        from app.extensions import db_session
        from app.models.catalog import Product

        db_session.add(Product(product_code="NO_PRICE_PROD", name="No Price", is_active=True, is_sellable=True))
        db_session.flush()
        product = db_session.query(Product).filter_by(product_code="NO_PRICE_PROD").first()
        plan = create_plan(
            {
                "plan_code": "NO_PRICE_PLAN",
                "product_id": product.id,
                "name": "No Price Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        assert describe_plan_for_sale(plan.id) is None


def test_retired_plan_not_sellable(app, seeded):
    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale

        _, plan = _seed_product_and_plan(
            app, product_code="RETIRED_PROD", plan_code="RETIRED_PLAN", retirement_date=date.today() - timedelta(days=1)
        )
        assert describe_plan_for_sale(plan.id) is None


def test_not_yet_effective_plan_not_sellable(app, seeded):
    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale

        _, plan = _seed_product_and_plan(
            app, product_code="FUTURE_PROD", plan_code="FUTURE_PLAN", effective_date=date.today() + timedelta(days=30)
        )
        assert describe_plan_for_sale(plan.id) is None


def test_inactive_product_plan_not_sellable(app, seeded):
    with app.app_context():
        from app.catalog.services import add_plan_price, create_plan
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale
        from app.extensions import db_session
        from app.models.catalog import Product

        product = Product(product_code="INACTIVE_PROD", name="Inactive", is_active=False, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": "INACTIVE_PLAN",
                "product_id": product.id,
                "name": "Inactive Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, Decimal("10.00"), "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        assert describe_plan_for_sale(plan.id) is None


def test_nonexistent_plan_returns_none(app, seeded):
    import uuid

    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_plan_for_sale

        assert describe_plan_for_sale(uuid.uuid4()) is None


def test_addon_available_status_is_sellable(app, seeded):
    with app.app_context():
        from app.catalog.services import set_addon_availability
        from app.commercial_sales.catalog_for_sales import describe_addon_for_sale
        from app.extensions import db_session
        from app.models.catalog import Addon, Product

        product = Product(product_code="ADDON_PROD", name="Addon Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        addon = Addon(addon_code="TEST_ADDON", product_id=product.id, name="Test Addon", price=Decimal("5.00"), currency="USD")
        db_session.add(addon)
        db_session.flush()
        set_addon_availability(addon, "AVAILABLE", actor_staff_user_id=None)

        result = describe_addon_for_sale(addon.id)
        assert result is not None
        assert result["unit_price"] == Decimal("5.00")


def test_addon_draft_status_not_sellable(app, seeded):
    with app.app_context():
        from app.commercial_sales.catalog_for_sales import describe_addon_for_sale
        from app.extensions import db_session
        from app.models.catalog import Addon, Product

        product = Product(product_code="ADDON_DRAFT_PROD", name="Addon Draft Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        addon = Addon(addon_code="DRAFT_ADDON", product_id=product.id, name="Draft Addon", price=Decimal("5.00"), currency="USD")
        db_session.add(addon)
        db_session.flush()

        assert describe_addon_for_sale(addon.id) is None


class TestRequiresLineApproval:
    def test_normal_line_no_approval(self):
        from app.commercial_sales.catalog_for_sales import requires_line_approval

        assert requires_line_approval(
            unit_price=Decimal("100"), override_unit_price=None, discount_amount=Decimal("5"), line_gross=Decimal("100")
        ) is False

    def test_price_override_requires_approval(self):
        from app.commercial_sales.catalog_for_sales import requires_line_approval

        assert requires_line_approval(
            unit_price=Decimal("100"), override_unit_price=Decimal("80"), discount_amount=Decimal("0"), line_gross=Decimal("80")
        ) is True

    def test_zero_price_requires_approval(self):
        from app.commercial_sales.catalog_for_sales import requires_line_approval

        assert requires_line_approval(
            unit_price=Decimal("100"), override_unit_price=Decimal("0"), discount_amount=Decimal("0"), line_gross=Decimal("0")
        ) is True

    def test_discount_within_threshold_no_approval(self):
        from app.commercial_sales.catalog_for_sales import requires_line_approval

        assert requires_line_approval(
            unit_price=Decimal("100"), override_unit_price=None, discount_amount=Decimal("10"), line_gross=Decimal("100")
        ) is False

    def test_discount_exceeding_threshold_requires_approval(self):
        from app.commercial_sales.catalog_for_sales import requires_line_approval

        assert requires_line_approval(
            unit_price=Decimal("100"), override_unit_price=None, discount_amount=Decimal("10.01"), line_gross=Decimal("100")
        ) is True
