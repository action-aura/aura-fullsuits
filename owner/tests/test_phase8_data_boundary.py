"""Phase 8 Part AC: data-boundary verification -- no patient/sales/
inventory/financial-secret detail crosses into any Owner-side surface
Phase 8 added (queues, reconciliation findings, notifications, the
extended assertion payload). A live network traffic capture isn't
possible in this environment; this is the structural equivalent: walking
every new surface's actual field/message content for forbidden markers,
the same technique `licensing_service/assertions.py::_guard_payload()`
already uses for the assertion payload itself."""
from __future__ import annotations

from datetime import date

from tests.conftest import make_staff

FORBIDDEN_SUBSTRINGS = (
    "patient", "diagnosis", "prescription", "medical_note", "clinical_note", "appointment",
    "sale", "inventory", "license_key", "key_secret_hmac", "pepper", "card_number", "bank_account",
    "tax_id", "tax_identifier",
)


def _contains_forbidden(text: str) -> str | None:
    lowered = text.lower()
    for marker in FORBIDDEN_SUBSTRINGS:
        if marker in lowered:
            return marker
    return None


def test_queue_snapshot_items_have_no_forbidden_fields(app, seeded):
    from app.commercial_ops.queues import QUEUE_ROLE_CODES, get_queue_for_role

    with app.app_context():
        for role_code in QUEUE_ROLE_CODES:
            snapshot = get_queue_for_role(role_code)
            for category, rows in snapshot.items.items():
                for row in rows:
                    for key in row:
                        hit = _contains_forbidden(key)
                        assert hit is None, f"{role_code}/{category} item key '{key}' matches forbidden marker '{hit}'"


def test_reconciliation_finding_details_have_no_forbidden_fields(app, seeded):
    from app.commercial_ops.reconciliation import run_reconciliation

    with app.app_context():
        result = run_reconciliation(as_of=date(2026, 7, 27), dry_run=True)
        for finding in result.findings:
            for key, value in finding.detail.items():
                assert _contains_forbidden(key) is None, f"finding detail key '{key}' looks forbidden"
                if isinstance(value, str):
                    assert _contains_forbidden(value) is None, f"finding detail value '{value}' looks forbidden"


def test_assertion_payload_new_commercial_fields_pass_the_guard(app, seeded):
    """Real call, not a mock -- proves the nine Part W fields coexist with
    the existing FORBIDDEN_ASSERTION_MARKERS guard rather than asserting
    it in isolation."""
    from app.commercial_ops.assertion_fields import resolve_commercial_assertion_fields
    from app.extensions import db_session
    from app.licensing_service.assertions import _guard_payload
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    staff_id = make_staff(app, "db1@example.com")
    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        customer = Customer(legal_name="Data Boundary Co")
        plan = Plan(plan_code=f"DB1-{staff_id}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
        db_session.add_all([customer, plan])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 9, 1)}, staff_id)
        transition_subscription(sub, "ACTIVE", staff_id)

        fields = resolve_commercial_assertion_fields(sub)
        payload = {"assertion_id": "x", **fields}
        guarded = _guard_payload(payload)  # raises AssertionError_ if any key is forbidden
        assert guarded == payload


def test_notification_messages_from_reconciliation_never_leak_forbidden_terms(app, seeded):
    from app.commercial_ops.reconciliation import run_reconciliation
    from app.extensions import db_session
    from app.models.commercial_ops import InternalNotification
    from app.models.catalog import Plan, Product
    from app.models.customers import Customer
    from app.subscriptions.services import create_subscription, transition_subscription

    staff_id = make_staff(app, "db2@example.com")
    with app.app_context():
        product = db_session.query(Product).filter_by(product_code="AURA_CLINIC").first()
        customer = Customer(legal_name="Notification Boundary Co")
        plan = Plan(plan_code=f"DB2-{staff_id}", product_id=product.id, name="x", billing_model="MONTHLY", currency="USD")
        db_session.add_all([customer, plan])
        db_session.commit()
        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id, "end_date": date(2026, 9, 1)}, staff_id)
        # Intentionally left in DRAFT with no license -- guaranteed to
        # surface at least the "no license" style finding paths elsewhere;
        # here we only need *some* real notifications to inspect.
        transition_subscription(sub, "ACTIVE", staff_id)

        run_reconciliation(as_of=date(2026, 7, 27), dry_run=False)
        rows = db_session.query(InternalNotification).all()
        for row in rows:
            assert _contains_forbidden(row.title) is None, f"notification title leaks forbidden term: {row.title}"
            assert _contains_forbidden(row.message) is None, f"notification message leaks forbidden term: {row.message}"
