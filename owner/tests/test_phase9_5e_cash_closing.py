"""Phase 9.5E -- Daily Cash Closing service tests."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import make_staff

_employee_number_counter = iter(range(300000, 400000))


def _seed_employee(app, email, role_codes):
    from app.employees.services import activate_employee, create_employee_profile

    staff_id = make_staff(app, email, role_codes=role_codes)
    with app.app_context():
        profile = create_employee_profile(
            {"staff_user_id": staff_id, "employee_number": f"EMP-{next(_employee_number_counter):05d}",
             "full_name": f"Employee {email}", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_id,
        )
        activate_employee(profile, actor_staff_user_id=staff_id)
        return staff_id, profile.id


def test_expected_cash_is_server_calculated_from_opening_only_when_no_activity(app, seeded):
    from app.cash_closing.services import get_or_create_draft_closing

    staff_id, profile_id = _seed_employee(app, "cc1@example.com", ["FINANCE"])
    with app.app_context():
        closing = get_or_create_draft_closing(
            date(2026, 8, 1), "USD", prepared_by_staff_user_id=staff_id,
            opening_cash_override=Decimal("500.00"), opening_cash_override_reason="first closing for this scope",
        )
        assert closing.expected_closing_cash == Decimal("500.00")
        assert closing.confirmed_cash_collections == Decimal("0")


def test_opening_cash_override_requires_reason(app, seeded):
    from app.cash_closing.services import get_or_create_draft_closing
    from app.expenses.errors import ExpenseError

    staff_id, profile_id = _seed_employee(app, "cc2@example.com", ["FINANCE"])
    with app.app_context():
        with pytest.raises(ExpenseError) as exc:
            get_or_create_draft_closing(date(2026, 8, 1), "USD", prepared_by_staff_user_id=staff_id)
        assert exc.value.code == "OPENING_CASH_OVERRIDE_REQUIRES_REASON"


def test_second_closing_derives_opening_from_prior_approved_closing(app, seeded):
    from app.cash_closing.services import close_closing, decide_closing, get_or_create_draft_closing, submit_closing

    staff_id, profile_id = _seed_employee(app, "cc3prep@example.com", ["FINANCE"])
    approver_id, approver_profile = _seed_employee(app, "cc3appr@example.com", ["FINANCE"])
    with app.app_context():
        day1 = get_or_create_draft_closing(
            date(2026, 8, 1), "USD", prepared_by_staff_user_id=staff_id,
            opening_cash_override=Decimal("100.00"), opening_cash_override_reason="opening balance",
        )
        submit_closing(day1, actor_staff_user_id=staff_id, actual_counted_cash=Decimal("100.00"), variance_explanation=None)
        decide_closing(day1, decision="APPROVED", actor_staff_user_id=approver_id)
        close_closing(day1, actor_staff_user_id=approver_id)

        day2 = get_or_create_draft_closing(date(2026, 8, 2), "USD", prepared_by_staff_user_id=staff_id)
        assert day2.opening_cash == Decimal("100.00")
        assert day2.opening_cash_is_override is False


def test_one_active_closing_per_scope(app, seeded):
    from app.cash_closing.services import get_or_create_draft_closing

    staff_id, profile_id = _seed_employee(app, "cc4@example.com", ["FINANCE"])
    with app.app_context():
        first = get_or_create_draft_closing(
            date(2026, 8, 3), "USD", prepared_by_staff_user_id=staff_id,
            opening_cash_override=Decimal("0.00"), opening_cash_override_reason="test",
        )
        second = get_or_create_draft_closing(date(2026, 8, 3), "USD", prepared_by_staff_user_id=staff_id)
        assert first.id == second.id


def test_nonzero_variance_requires_explanation(app, seeded):
    from app.cash_closing.services import get_or_create_draft_closing, submit_closing
    from app.expenses.errors import ExpenseError

    staff_id, profile_id = _seed_employee(app, "cc5@example.com", ["FINANCE"])
    with app.app_context():
        closing = get_or_create_draft_closing(
            date(2026, 8, 4), "USD", prepared_by_staff_user_id=staff_id,
            opening_cash_override=Decimal("100.00"), opening_cash_override_reason="test",
        )
        with pytest.raises(ExpenseError) as exc:
            submit_closing(closing, actor_staff_user_id=staff_id, actual_counted_cash=Decimal("95.00"), variance_explanation=None)
        assert exc.value.code == "VARIANCE_EXPLANATION_REQUIRED"


def test_material_variance_routes_to_review_required(app, seeded):
    from app.cash_closing.services import get_or_create_draft_closing, submit_closing

    staff_id, profile_id = _seed_employee(app, "cc6@example.com", ["FINANCE"])
    with app.app_context():
        closing = get_or_create_draft_closing(
            date(2026, 8, 5), "USD", prepared_by_staff_user_id=staff_id,
            opening_cash_override=Decimal("100.00"), opening_cash_override_reason="test",
        )
        submit_closing(closing, actor_staff_user_id=staff_id, actual_counted_cash=Decimal("40.00"), variance_explanation="big discrepancy, investigating")
        assert closing.status == "REVIEW_REQUIRED"
        assert closing.variance == Decimal("-60.00")


def test_preparer_cannot_approve_own_closing(app, seeded):
    from app.cash_closing.services import decide_closing, get_or_create_draft_closing, submit_closing
    from app.expenses.errors import ExpenseError

    staff_id, profile_id = _seed_employee(app, "cc7@example.com", ["FINANCE"])
    with app.app_context():
        closing = get_or_create_draft_closing(
            date(2026, 8, 6), "USD", prepared_by_staff_user_id=staff_id,
            opening_cash_override=Decimal("100.00"), opening_cash_override_reason="test",
        )
        submit_closing(closing, actor_staff_user_id=staff_id, actual_counted_cash=Decimal("100.00"), variance_explanation=None)
        with pytest.raises(ExpenseError) as exc:
            decide_closing(closing, decision="APPROVED", actor_staff_user_id=staff_id)
        assert exc.value.code == "SELF_APPROVAL_FORBIDDEN_CLOSING"


def test_super_admin_preparer_still_cannot_self_approve(app, seeded):
    from app.cash_closing.services import decide_closing, get_or_create_draft_closing, submit_closing
    from app.expenses.errors import ExpenseError

    staff_id = make_staff(app, "cc8super@example.com", super_admin=True)
    with app.app_context():
        closing = get_or_create_draft_closing(
            date(2026, 8, 7), "USD", prepared_by_staff_user_id=staff_id,
            opening_cash_override=Decimal("100.00"), opening_cash_override_reason="test",
        )
        submit_closing(closing, actor_staff_user_id=staff_id, actual_counted_cash=Decimal("100.00"), variance_explanation=None)
        with pytest.raises(ExpenseError) as exc:
            decide_closing(closing, decision="APPROVED", actor_staff_user_id=staff_id)
        assert exc.value.code == "SELF_APPROVAL_FORBIDDEN_CLOSING"


def test_submitter_who_did_not_prepare_cannot_approve_the_closing(app, seeded):
    """AUDIT-032 regression: prepared_by_staff_user_id is set once at draft
    creation and never reassigned, but the (business_date, currency) scope
    is shared -- any cash_closing.prepare holder may submit a draft someone
    else created. The self-approval block must catch the actual submitter,
    not just the original (uninvolved) preparer."""
    from app.cash_closing.services import decide_closing, get_or_create_draft_closing, submit_closing
    from app.expenses.errors import ExpenseError

    alice_id, _ = _seed_employee(app, "cc11alice@example.com", ["FINANCE"])
    bob_id, _ = _seed_employee(app, "cc11bob@example.com", ["FINANCE"])
    with app.app_context():
        closing = get_or_create_draft_closing(
            date(2026, 8, 10), "USD", prepared_by_staff_user_id=alice_id,
            opening_cash_override=Decimal("100.00"), opening_cash_override_reason="test",
        )
        submit_closing(closing, actor_staff_user_id=bob_id, actual_counted_cash=Decimal("100.00"), variance_explanation=None)
        with pytest.raises(ExpenseError) as exc:
            decide_closing(closing, decision="APPROVED", actor_staff_user_id=bob_id)
        assert exc.value.code == "SELF_APPROVAL_FORBIDDEN_CLOSING"


def test_submitted_by_is_persisted_and_distinct_from_prepared_by(app, seeded):
    from app.cash_closing.services import get_or_create_draft_closing, submit_closing

    alice_id, _ = _seed_employee(app, "cc12alice@example.com", ["FINANCE"])
    bob_id, _ = _seed_employee(app, "cc12bob@example.com", ["FINANCE"])
    with app.app_context():
        closing = get_or_create_draft_closing(
            date(2026, 8, 11), "USD", prepared_by_staff_user_id=alice_id,
            opening_cash_override=Decimal("100.00"), opening_cash_override_reason="test",
        )
        submit_closing(closing, actor_staff_user_id=bob_id, actual_counted_cash=Decimal("100.00"), variance_explanation=None)
        assert closing.prepared_by_staff_user_id == alice_id
        assert closing.submitted_by_staff_user_id == bob_id


def test_third_party_approver_still_succeeds_after_submitter_block(app, seeded):
    """Guards against over-blocking: a real third party (neither preparer
    nor submitter) must still be able to approve."""
    from app.cash_closing.services import decide_closing, get_or_create_draft_closing, submit_closing

    alice_id, _ = _seed_employee(app, "cc13alice@example.com", ["FINANCE"])
    bob_id, _ = _seed_employee(app, "cc13bob@example.com", ["FINANCE"])
    carol_id, _ = _seed_employee(app, "cc13carol@example.com", ["FINANCE"])
    with app.app_context():
        closing = get_or_create_draft_closing(
            date(2026, 8, 12), "USD", prepared_by_staff_user_id=alice_id,
            opening_cash_override=Decimal("100.00"), opening_cash_override_reason="test",
        )
        submit_closing(closing, actor_staff_user_id=bob_id, actual_counted_cash=Decimal("100.00"), variance_explanation=None)
        decide_closing(closing, decision="APPROVED", actor_staff_user_id=carol_id)
        assert closing.status == "APPROVED"
        assert closing.approved_by_staff_user_id == carol_id


def test_approved_closing_immutable_and_reopen_requires_auth_and_reason(app, seeded):
    from app.cash_closing.services import close_closing, decide_closing, get_or_create_draft_closing, reopen_closing, submit_closing
    from app.expenses.errors import ExpenseError

    staff_id, profile_id = _seed_employee(app, "cc9prep@example.com", ["FINANCE"])
    approver_id, approver_profile = _seed_employee(app, "cc9appr@example.com", ["FINANCE"])
    with app.app_context():
        closing = get_or_create_draft_closing(
            date(2026, 8, 8), "USD", prepared_by_staff_user_id=staff_id,
            opening_cash_override=Decimal("100.00"), opening_cash_override_reason="test",
        )
        submit_closing(closing, actor_staff_user_id=staff_id, actual_counted_cash=Decimal("100.00"), variance_explanation=None)
        decide_closing(closing, decision="APPROVED", actor_staff_user_id=approver_id)
        close_closing(closing, actor_staff_user_id=approver_id)
        assert closing.status == "CLOSED"

        with pytest.raises(ExpenseError) as exc:
            reopen_closing(closing, actor_staff_user_id=approver_id, reason="", recent_auth_verified=True)
        assert exc.value.code == "REOPEN_REQUIRES_REASON"

        with pytest.raises(ExpenseError) as exc:
            reopen_closing(closing, actor_staff_user_id=approver_id, reason="found a late transaction", recent_auth_verified=False)
        assert exc.value.code == "REOPEN_REQUIRES_RECENT_AUTHENTICATION"

        reopen_closing(closing, actor_staff_user_id=approver_id, reason="found a late transaction", recent_auth_verified=True)
        assert closing.status == "REOPENED"
        assert closing.reopen_count == 1


def test_reopen_preserves_prior_approval_history(app, seeded):
    from app.cash_closing.services import close_closing, decide_closing, get_or_create_draft_closing, reopen_closing, submit_closing
    from app.models.cash_closing import CashClosingReopenEvent
    from app.extensions import db_session
    from sqlalchemy import select

    staff_id, profile_id = _seed_employee(app, "cc10prep@example.com", ["FINANCE"])
    approver_id, approver_profile = _seed_employee(app, "cc10appr@example.com", ["FINANCE"])
    with app.app_context():
        closing = get_or_create_draft_closing(
            date(2026, 8, 9), "USD", prepared_by_staff_user_id=staff_id,
            opening_cash_override=Decimal("200.00"), opening_cash_override_reason="test",
        )
        submit_closing(closing, actor_staff_user_id=staff_id, actual_counted_cash=Decimal("200.00"), variance_explanation=None)
        decide_closing(closing, decision="APPROVED", actor_staff_user_id=approver_id)
        original_approved_by = closing.approved_by_staff_user_id
        close_closing(closing, actor_staff_user_id=approver_id)

        reopen_closing(closing, actor_staff_user_id=approver_id, reason="late txn", recent_auth_verified=True)

        events = db_session.execute(
            select(CashClosingReopenEvent).where(CashClosingReopenEvent.cash_closing_id == closing.id)
        ).scalars().all()
        assert len(events) == 1
        assert events[0].prior_approved_by_staff_user_id == original_approved_by
        assert events[0].prior_status == "CLOSED"
