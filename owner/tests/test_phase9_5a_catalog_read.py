from __future__ import annotations

from datetime import date

from tests.conftest import make_staff


def test_read_active_catalog_includes_effective_plan_with_current_price(app, seeded):
    staff_id = make_staff(app, "cat1@example.com")
    with app.app_context():
        from app.catalog.services import add_plan_price, create_plan, read_active_catalog
        from app.extensions import db_session
        from app.models.catalog import Product
        from sqlalchemy import select

        product = db_session.execute(select(Product).where(Product.product_code == "AURA_CLINIC")).scalars().first()
        plan = create_plan(
            {
                "plan_code": "READ-ACTIVE-1", "product_id": product.id, "name": "Read Active Plan",
                "billing_model": "MONTHLY", "currency": "USD", "effective_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        add_plan_price(plan, base_price="199.00", currency="USD", effective_from=date(2026, 1, 1), actor_staff_user_id=staff_id)

        catalog = read_active_catalog(as_of=date(2026, 6, 1))
        entry = next((e for e in catalog if e["plan_code"] == "READ-ACTIVE-1"), None)
        assert entry is not None
        assert entry["current_price"] == {"base_price": "199.00", "currency": "USD"}


def test_read_active_catalog_excludes_retired_plan(app, seeded):
    staff_id = make_staff(app, "cat2@example.com")
    with app.app_context():
        from app.catalog.services import create_plan, read_active_catalog
        from app.extensions import db_session
        from app.models.catalog import Product
        from sqlalchemy import select

        product = db_session.execute(select(Product).where(Product.product_code == "AURA_CLINIC")).scalars().first()
        create_plan(
            {
                "plan_code": "RETIRED-1", "product_id": product.id, "name": "Retired Plan", "billing_model": "MONTHLY",
                "currency": "USD", "effective_date": date(2025, 1, 1), "retirement_date": date(2025, 12, 31),
            },
            actor_staff_user_id=staff_id,
        )

        catalog = read_active_catalog(as_of=date(2026, 6, 1))
        assert not any(e["plan_code"] == "RETIRED-1" for e in catalog)


def test_read_active_catalog_excludes_plan_with_no_effective_date(app, seeded):
    staff_id = make_staff(app, "cat3@example.com")
    with app.app_context():
        from app.catalog.services import create_plan, read_active_catalog
        from app.extensions import db_session
        from app.models.catalog import Product
        from sqlalchemy import select

        product = db_session.execute(select(Product).where(Product.product_code == "AURA_CLINIC")).scalars().first()
        create_plan(
            {"plan_code": "DRAFT-NO-DATE", "product_id": product.id, "name": "Draft Plan", "billing_model": "MONTHLY", "currency": "USD"},
            actor_staff_user_id=staff_id,
        )

        catalog = read_active_catalog(as_of=date(2026, 6, 1))
        assert not any(e["plan_code"] == "DRAFT-NO-DATE" for e in catalog)
