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


def test_seed_defines_the_branch_limit_entitlement(app, seeded):
    """The 2026-09-05 price list sells a branch as a paid add-on -- enforced
    entirely by the retail till reading this entitlement off the stored
    assertion (see retail_api.py's _branch_limit())."""
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import EntitlementDefinition

        row = db_session.query(EntitlementDefinition).filter_by(entitlement_code="max_branches").first()
        assert row is not None
        assert row.value_type == "integer"


# -- launch-readiness W0.3 Part A: seed_canonical_plans --------------------
# Without a seeded plan, a brand-new Owner deployment cannot issue a single
# licence (`owner_plans` stays empty after seed-catalog, and issuance is
# always against a plan_id). See owner/app/catalog/services.py's own
# docstring for why this is a separate command from seed-catalog.

def test_seed_plans_produces_issuable_license_end_to_end(app, seeded):
    """Acceptance test: seed-catalog (via `seeded`) + seed-plans is enough
    for issue_license_direct to hand back a real, working key -- no manual
    UI step first."""
    staff_id = make_staff(app, "seedplan-issuer@example.com")
    with app.app_context():
        import uuid

        from app.catalog.services import seed_canonical_plans
        from app.extensions import db_session
        from app.licensing.issuance import issue_license_direct
        from app.models.catalog import Plan

        seed_canonical_plans()
        plan = db_session.query(Plan).filter_by(plan_code="AURA_RETAIL_STANDARD").first()
        assert plan is not None

        result = issue_license_direct(
            new_customer_legal_name="Seed Plan Test Co",
            plan_id=plan.id,
            idempotency_key=str(uuid.uuid4()),
            actor_staff_user_id=staff_id,
            license_pepper=app.config["LICENSE_PEPPER"],
        )
        assert result["full_key"] is not None
        assert result["full_key"].startswith("AURA-")
        assert result["whatsapp_message"] is not None
        assert result["full_key"] in result["whatsapp_message"]


def test_seed_plans_is_idempotent(app, seeded):
    with app.app_context():
        from app.catalog.services import seed_canonical_plans

        first = seed_canonical_plans()
        assert first["plans"] == 2  # AURA_RETAIL_STANDARD + AURA_CLINIC_STANDARD
        assert first["prices"] == 2

        second = seed_canonical_plans()
        assert second == {"plans": 0, "prices": 0}  # nothing new created on a second run


def test_seed_plans_requires_catalog_seeded_first(app):
    # Deliberately no `seeded` fixture -- an empty, un-seeded database is
    # exactly what a truly fresh Owner deployment looks like before anyone
    # has run `flask seed-catalog`.
    with app.app_context():
        from app.catalog.services import seed_canonical_plans

        try:
            seed_canonical_plans()
            assert False, "should have raised"
        except ValueError as exc:
            assert "seed-catalog" in str(exc)
