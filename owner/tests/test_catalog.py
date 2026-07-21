from __future__ import annotations

from datetime import date

from tests.conftest import force_login, make_staff


def test_seed_is_idempotent(app, seeded):
    with app.app_context():
        from app.catalog.services import seed_canonical_catalog

        result_again = seed_canonical_catalog()
    assert all(v == 0 for v in result_again.values())  # nothing new created on a second run


def test_product_platform_mapping_seeded(app, seeded):
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import Product, ProductPlatform

        retail = db_session.query(Product).filter_by(product_code="AURA_RETAIL").first()
        mappings = db_session.query(ProductPlatform).filter_by(product_id=retail.id).count()
        assert mappings == 2  # WINDOWS + ANDROID


def test_price_history_never_overwritten(app, client, seeded):
    staff_id = make_staff(app, "f@example.com", role_codes=["FINANCE"])
    force_login(client, app, staff_id)
    with app.app_context():
        from app.extensions import db_session
        from app.catalog.services import add_plan_price
        from app.models.catalog import Plan, Product

        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        plan = Plan(plan_code="P1", product_id=product.id, name="P1", billing_model="MONTHLY", currency="USD")
        db_session.add(plan)
        db_session.commit()

        add_plan_price(plan, 100, "USD", date(2026, 1, 1), staff_id)
        add_plan_price(plan, 120, "USD", date(2026, 6, 1), staff_id)

        db_session.refresh(plan)
        prices = sorted(plan.prices, key=lambda p: p.effective_from)
        assert len(prices) == 2
        assert prices[0].base_price == 100
        assert prices[0].effective_until == date(2026, 6, 1)  # closed out, not deleted
        assert prices[1].base_price == 120
        assert prices[1].effective_until is None  # current


def test_addon_availability_cannot_be_set_to_invalid_status(app, client, seeded):
    with app.app_context():
        from app.catalog.services import set_addon_availability
        from app.extensions import db_session
        from app.models.catalog import Addon

        addon = db_session.query(Addon).first()
        try:
            set_addon_availability(addon, "NOT_A_REAL_STATUS", None)
            assert False, "should have raised"
        except ValueError:
            pass


def test_unbuilt_addons_are_not_marked_available_by_seed(app, seeded):
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import Addon

        assert db_session.query(Addon).filter_by(availability_status="AVAILABLE").count() == 0
