"""Phase 9.5D Milestone 6 -- CommercialApproval service tests, including
integration with the Quote domain's automatic exception-detection."""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.conftest import make_staff


_employee_number_counter = iter(range(1, 100000))


def _seed_sales_employee(app, email):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=["SALES"])
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id,
                # Real bug found in this test helper: f"EMP-{email[:6].upper()}"
                # collided for any two prefixes sharing a 6-char stem (e.g.
                # "unrelaudit_a"/"unrelaudit_b" both truncate to "UNRELA"),
                # a real UniqueViolation on employee_number caught by the
                # test suite itself. Fixed with a monotonic counter instead
                # of a truncated email.
                "employee_number": f"EMP-{next(_employee_number_counter):05d}",
                "full_name": f"Sales {email}",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        return staff_id, profile.id


def _seed_customer(app, staff_id):
    from app.customers.services import create_customer

    with app.app_context():
        customer = create_customer({"legal_name": "Approval Test Customer Co"}, actor_staff_user_id=staff_id)
        return customer.id


def _seed_plan(app, plan_code, price=Decimal("100.00")):
    from app.catalog.services import add_plan_price, create_plan, seed_canonical_catalog
    from app.extensions import db_session
    from app.models.catalog import Product

    with app.app_context():
        seed_canonical_catalog()
        product = Product(product_code=f"PROD_{plan_code}", name="Approval Test Product", is_active=True, is_sellable=True)
        db_session.add(product)
        db_session.flush()
        plan = create_plan(
            {
                "plan_code": plan_code,
                "product_id": product.id,
                "name": "Approval Test Plan",
                "billing_model": "MONTHLY",
                "effective_date": date.today() - timedelta(days=1),
            },
            actor_staff_user_id=None,
        )
        add_plan_price(plan, price, "USD", date.today() - timedelta(days=1), actor_staff_user_id=None)
        return plan.id


def test_normal_line_creates_no_approval(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "appa@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "APA_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import unresolved_approvals_for_targets
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        line = add_quote_line(quote, plan_id=plan_id, addon_id=None, quantity=1, actor_staff_user_id=staff_id)

        assert unresolved_approvals_for_targets("QUOTE_LINE", [line.id]) == []


def test_price_override_line_creates_pending_approval(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "appb@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "APB_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import unresolved_approvals_for_targets
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="loyal customer", actor_staff_user_id=staff_id,
        )

        pending = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])
        assert len(pending) == 1
        assert pending[0].reason_code == "PRICE_OVERRIDE"
        assert pending[0].status == "PENDING"


def test_accept_blocked_while_approval_pending(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "appc@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "APC_PLAN")
    with app.app_context():
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="discount", actor_staff_user_id=staff_id,
        )
        submit_quote(quote, actor_staff_user_id=staff_id)

        with pytest.raises(CommercialSalesError) as exc:
            record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_id)
        assert exc.value.code == "APPROVAL_REQUIRED"


def test_accept_succeeds_after_approval_granted(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "appd_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "appd_b@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "APD_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import decide_approval, unresolved_approvals_for_targets
        from app.commercial_sales.quotes import add_quote_line, create_quote, record_customer_decision, submit_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="discount", actor_staff_user_id=staff_a,
        )
        submit_quote(quote, actor_staff_user_id=staff_a)

        approval = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])[0]
        decide_approval(
            approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b
        )

        record_customer_decision(quote, accepted=True, actor_staff_user_id=staff_a)
        assert quote.status == "ACCEPTED"


def test_self_approval_forbidden(app, seeded):
    staff_id, profile_id = _seed_sales_employee(app, "appe@example.com")
    customer_id = _seed_customer(app, staff_id)
    plan_id = _seed_plan(app, "APE_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import decide_approval, unresolved_approvals_for_targets
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_id, actor_staff_user_id=staff_id)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("0.00"),
            override_reason="free trial", actor_staff_user_id=staff_id,
        )
        approval = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])[0]
        assert approval.reason_code == "ZERO_PRICE_LINE"

        with pytest.raises(CommercialSalesError) as exc:
            decide_approval(
                approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_id
            )
        assert exc.value.code == "SELF_APPROVAL_FORBIDDEN"


def test_reject_requires_reason(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "appf_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "appf_b@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "APF_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import decide_approval, unresolved_approvals_for_targets
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="discount", actor_staff_user_id=staff_a,
        )
        approval = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])[0]

        with pytest.raises(CommercialSalesError) as exc:
            decide_approval(
                approval, approved=False, decision_reason=None, decided_by_staff_user_id=staff_b
            )
        assert exc.value.code == "REASON_REQUIRED"


class TestApprovalValidityBoundToCommercialSubstance:
    """Verifies that approval validity is bound to the exact commercial
    values being approved -- a deterministic content fingerprint
    (compute_line_commercial_fingerprint), not the QuoteLine.version
    counter alone. QuoteLine.version is only a valid staleness signal if
    every approval-relevant mutation reliably bumps it; the fingerprint
    is authoritative because it's computed directly from the material
    values (product/plan, quantity, price, discount, currency) and can
    never miss a change regardless of which code path made it."""

    def _setup_pending_approval(self, app, email_prefix, plan_code):
        staff_a, profile_a = _seed_sales_employee(app, f"{email_prefix}_a@example.com")
        staff_b, _ = _seed_sales_employee(app, f"{email_prefix}_b@example.com")
        customer_id = _seed_customer(app, staff_a)
        plan_id = _seed_plan(app, plan_code)
        from app.commercial_sales.approvals import unresolved_approvals_for_targets
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="discount", actor_staff_user_id=staff_a,
        )
        approval = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])[0]
        return staff_a, staff_b, quote, line, approval

    def test_material_change_quantity_invalidates(self, app, seeded):
        """No update_quote_line() exists yet in this milestone's scope, so
        this simulates a future line edit directly (mutating the live
        QuoteLine row exactly as such a function would) -- proving the
        fingerprint check catches a real material change regardless."""
        with app.app_context():
            from app.commercial_sales.approvals import decide_approval
            from app.commercial_sales.errors import CommercialSalesError

            staff_a, staff_b, quote, line, approval = self._setup_pending_approval(app, "matqty", "MATQTY_PLAN")
            line.quantity = 2

            with pytest.raises(CommercialSalesError) as exc:
                decide_approval(approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b)
            assert exc.value.code == "APPROVAL_STALE"

    def test_material_change_override_price_invalidates(self, app, seeded):
        with app.app_context():
            from app.commercial_sales.approvals import decide_approval
            from app.commercial_sales.errors import CommercialSalesError

            staff_a, staff_b, quote, line, approval = self._setup_pending_approval(app, "matprice", "MATPRICE_PLAN")
            line.overridden_unit_price = Decimal("45.00")  # a different proposed price than what was approved

            with pytest.raises(CommercialSalesError) as exc:
                decide_approval(approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b)
            assert exc.value.code == "APPROVAL_STALE"

    def test_material_change_discount_invalidates(self, app, seeded):
        with app.app_context():
            from app.commercial_sales.approvals import decide_approval
            from app.commercial_sales.errors import CommercialSalesError

            staff_a, staff_b, quote, line, approval = self._setup_pending_approval(app, "matdisc", "MATDISC_PLAN")
            line.discount_amount = Decimal("5.00")

            with pytest.raises(CommercialSalesError) as exc:
                decide_approval(approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b)
            assert exc.value.code == "APPROVAL_STALE"

    def test_material_change_plan_invalidates(self, app, seeded):
        """Changing which product/plan the line refers to entirely --
        the most material change possible."""
        # Seeded before _setup_pending_approval, not after: _seed_plan()
        # opens its own nested app_context internally, and its teardown
        # detaches any ORM objects already loaded in the outer context's
        # session (a real DetachedInstanceError caught by this test suite
        # itself on the first attempt) -- so any extra seeding must happen
        # before quote/line/approval are created, never after.
        other_plan_id = _seed_plan(app, "MATPLAN_OTHER_PLAN", price=Decimal("200.00"))
        with app.app_context():
            from app.commercial_sales.approvals import decide_approval
            from app.commercial_sales.errors import CommercialSalesError

            staff_a, staff_b, quote, line, approval = self._setup_pending_approval(app, "matplan", "MATPLAN_PLAN")
            line.plan_id = other_plan_id

            with pytest.raises(CommercialSalesError) as exc:
                decide_approval(approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b)
            assert exc.value.code == "APPROVAL_STALE"

    def test_deleted_line_treated_as_stale_not_missing(self, app, seeded):
        """A line deleted after its approval was requested must not be
        silently treated as 'nothing to check' -- the approval becomes
        undecidable (STALE), not auto-approved by the absence of a
        target."""
        with app.app_context():
            from app.commercial_sales.approvals import decide_approval
            from app.commercial_sales.errors import CommercialSalesError
            from app.extensions import db_session

            staff_a, staff_b, quote, line, approval = self._setup_pending_approval(app, "matdel", "MATDEL_PLAN")
            db_session.delete(line)
            db_session.flush()

            with pytest.raises(CommercialSalesError) as exc:
                decide_approval(approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b)
            assert exc.value.code == "APPROVAL_STALE"

    def test_unrelated_sibling_line_added_does_not_invalidate(self, app, seeded):
        """The exact real bug this fingerprint design replaces: adding an
        unrelated sibling line used to bump Quote.version, which the
        original (wrong) implementation compared against -- incorrectly
        invalidating a still-accurate approval for a DIFFERENT line."""
        other_plan_id = _seed_plan(app, "UNRELSIB_OTHER_PLAN")
        with app.app_context():
            from app.commercial_sales.approvals import decide_approval
            from app.commercial_sales.quotes import add_quote_line

            staff_a, staff_b, quote, line, approval = self._setup_pending_approval(app, "unrelsib", "UNRELSIB_PLAN")
            add_quote_line(quote, plan_id=other_plan_id, addon_id=None, quantity=3, actor_staff_user_id=staff_a)

            decided = decide_approval(approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b)
            assert decided.status == "APPROVED"

    def test_unrelated_quote_submit_does_not_invalidate(self, app, seeded):
        """The original real bug reproduced directly: submit_quote() bumps
        Quote.version, which must never invalidate a pending approval for
        one of its lines."""
        with app.app_context():
            from app.commercial_sales.approvals import decide_approval
            from app.commercial_sales.quotes import submit_quote

            staff_a, staff_b, quote, line, approval = self._setup_pending_approval(app, "unrelsub", "UNRELSUB_PLAN")
            submit_quote(quote, actor_staff_user_id=staff_a)

            decided = decide_approval(approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b)
            assert decided.status == "APPROVED"

    def test_unrelated_audit_writes_do_not_invalidate(self, app, seeded):
        """Writing unrelated audit log entries referencing this exact line
        must never affect its approval's validity -- the fingerprint has
        no dependency on the audit chain at all."""
        with app.app_context():
            from app.audit.services import record as audit_record
            from app.commercial_sales.approvals import decide_approval

            staff_a, staff_b, quote, line, approval = self._setup_pending_approval(app, "unrelaudit", "UNRELAUDIT_PLAN")
            for _ in range(3):
                audit_record(
                    actor_staff_user_id=staff_a, actor_role_snapshot=None, action_code="QUOTE_LINE_VIEWED",
                    entity_type="quote_line", entity_public_id=str(line.id),
                )

            decided = decide_approval(approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b)
            assert decided.status == "APPROVED"


def test_double_decision_rejected(app, seeded):
    staff_a, profile_a = _seed_sales_employee(app, "apph_a@example.com")
    staff_b, _ = _seed_sales_employee(app, "apph_b@example.com")
    customer_id = _seed_customer(app, staff_a)
    plan_id = _seed_plan(app, "APH_PLAN")
    with app.app_context():
        from app.commercial_sales.approvals import decide_approval, unresolved_approvals_for_targets
        from app.commercial_sales.errors import CommercialSalesError
        from app.commercial_sales.quotes import add_quote_line, create_quote

        quote = create_quote({"customer_id": customer_id, "currency": "USD"}, actor_employee_profile_id=profile_a, actor_staff_user_id=staff_a)
        line = add_quote_line(
            quote, plan_id=plan_id, addon_id=None, quantity=1, override_unit_price=Decimal("50.00"),
            override_reason="discount", actor_staff_user_id=staff_a,
        )
        approval = unresolved_approvals_for_targets("QUOTE_LINE", [line.id])[0]
        decide_approval(
            approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b
        )

        with pytest.raises(CommercialSalesError) as exc:
            decide_approval(
                approval, approved=True, decision_reason=None, decided_by_staff_user_id=staff_b
            )
        assert exc.value.code == "INVALID_APPROVAL_TRANSITION"
