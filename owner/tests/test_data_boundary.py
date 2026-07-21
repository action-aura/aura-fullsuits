"""Part W: forbidden business/medical data must be structurally impossible to
introduce into Aura Owner, not merely undocumented. This is the single test
file that most directly proves owner-data-boundary.md's claims."""
from __future__ import annotations

import pytest

FORBIDDEN_TERMS = (
    "patient", "diagnosis", "prescription", "medical_note", "clinical_note",
    "sale_line", "invoice_line", "inventory_quantity", "customer_purchase",
    "local_database", "card_number", "bank_account", "appointment",
)


def _all_owner_models():
    from app.models import Base

    return Base.metadata.sorted_tables


def test_no_model_column_matches_a_forbidden_term():
    for table in _all_owner_models():
        for column in table.columns:
            lowered = column.name.lower()
            for term in FORBIDDEN_TERMS:
                assert term not in lowered, f"{table.name}.{column.name} matches forbidden term '{term}'"


def test_no_model_table_name_matches_a_forbidden_term():
    for table in _all_owner_models():
        lowered = table.name.lower()
        for term in FORBIDDEN_TERMS:
            assert term not in lowered, f"table '{table.name}' matches forbidden term '{term}'"


def test_serializers_reject_forbidden_field_at_construction_time():
    from app.api.serializers import ForbiddenFieldError, _guard

    with pytest.raises(ForbiddenFieldError):
        _guard({"patient_name": "should never exist"})
    with pytest.raises(ForbiddenFieldError):
        _guard({"invoice_line_total": 100})
    with pytest.raises(ForbiddenFieldError):
        _guard({"card_number": "4111..."})

    # A legitimate Owner-domain payload passes through untouched.
    safe = _guard({"license_id": "abc", "status": "ACTIVE"})
    assert safe == {"license_id": "abc", "status": "ACTIVE"}


def test_license_check_serializer_output_is_a_fixed_allowlist(app, seeded):
    from tests.conftest import make_staff

    staff_id = make_staff(app, "db1@example.com")
    with app.app_context():
        from app.api.serializers import serialize_license_check_response
        from app.extensions import db_session
        from app.licensing.services import create_license
        from app.models.catalog import Plan, Product
        from app.models.customers import Customer
        from app.subscriptions.services import create_subscription

        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        plan = Plan(plan_code="DB1", product_id=product.id, name="DB1", billing_model="MONTHLY", currency="USD")
        customer = Customer(legal_name="Boundary Co")
        db_session.add_all([plan, customer])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, staff_id)
        license_row = create_license(
            {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
             "allowed_platforms": "WINDOWS", "device_limit": 1},
            staff_id,
        )
        payload = serialize_license_check_response(license_row, [])
        allowed_keys = {
            "license_id", "status", "valid_from", "valid_until", "allowed_platforms",
            "device_limit", "entitlements", "server_timestamp", "correlation_id",
        }
        assert set(payload.keys()) == allowed_keys


def test_no_import_path_from_owner_into_products_or_commercial_runtime():
    """Static proof, not just convention: no owner/app module imports anything
    from products.* or commercial_runtime.*."""
    import pathlib
    import re

    owner_app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders = []
    for py_file in owner_app_dir.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+(products|commercial_runtime)\b", text, re.MULTILINE):
            offenders.append(str(py_file))
    assert offenders == [], f"forbidden cross-package import found in: {offenders}"


def test_external_api_blueprint_not_registered_by_default():
    """The shared `app` fixture deliberately forces EXTERNAL_API_ENABLED=True
    in TestingConfig (Phase 6 needs it to exercise /api/licensing/v1/*) --
    that is a test-harness convenience, not the real default. This test
    proves the real invariant directly on the config classes actually used in
    development/production: the flag defaults to false on every one of them,
    and create_app() only imports/registers either external blueprint inside
    an `if app.config.get("EXTERNAL_API_ENABLED"):` guard (app/__init__.py) --
    so a real deployment with no override never has the route to reach."""
    from app.config import BaseConfig, DevelopmentConfig, ProductionConfig

    assert BaseConfig.EXTERNAL_API_ENABLED is False
    assert DevelopmentConfig.EXTERNAL_API_ENABLED is False
    assert ProductionConfig.EXTERNAL_API_ENABLED is False
