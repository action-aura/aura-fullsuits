"""Phase 9.5E -- Expense lifecycle + approval/segregation-of-duties tests.
One test per Non-Negotiable rule in
docs/owner/phase9_5e/expense-approval-and-segregation-contract.md, matching
the discipline established by test_phase9_5d_commission_ledger.py."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import make_staff

_employee_number_counter = iter(range(1, 100000))


def _seed_employee(app, email, role_codes):
    from app.employees.services import create_employee_profile

    staff_id = make_staff(app, email, role_codes=role_codes)
    with app.app_context():
        profile = create_employee_profile(
            {
                "staff_user_id": staff_id,
                "employee_number": f"EMP-{next(_employee_number_counter):05d}",
                "full_name": f"Employee {email}",
                "employment_start_date": date(2026, 1, 1),
            },
            actor_staff_user_id=staff_id,
        )
        from app.employees.services import activate_employee
        activate_employee(profile, actor_staff_user_id=staff_id)
        return staff_id, profile.id


def _seed_category(app, code="TRAVEL"):
    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory

    with app.app_context():
        cat = ExpenseCategory(category_code=code, name=code.title(), is_active=True)
        db_session.add(cat)
        db_session.commit()
        return cat.id


def _seed_external_payee(app, creator_staff_id):
    from app.expenses.payees import create_payee

    with app.app_context():
        payee = create_payee(
            payee_type="EXTERNAL", display_name="Acme Vendor Co", employee_profile_id=None,
            external_contact_reference="vendor@acme.example", created_by_staff_user_id=creator_staff_id,
        )
        return payee.id


def _seed_employee_payee(app, creator_staff_id, beneficiary_employee_profile_id):
    from app.expenses.payees import create_payee

    with app.app_context():
        payee = create_payee(
            payee_type="EMPLOYEE", display_name="Reimbursement", employee_profile_id=beneficiary_employee_profile_id,
            external_contact_reference=None, created_by_staff_user_id=creator_staff_id,
        )
        return payee.id


def _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id, amount=Decimal("100.00")):
    from app.expenses.lifecycle import create_expense, submit_expense
    from app.extensions import db_session
    from app.models.expenses import Expense

    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=amount, currency="USD",
            expense_date=date(2026, 8, 1), description="Client dinner", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=requester_profile_id,
        )
        submit_expense(expense, actor_staff_user_id=requester_staff_id)
        expense_id = expense.id
    return expense_id


def test_requester_cannot_approve_own_expense(app, seeded):
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.errors import ExpenseError
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session

    staff_id, profile_id = _seed_employee(app, "req1@example.com", ["SALES", "FINANCE"])
    category_id = _seed_category(app, "TRAVEL1")
    payee_id = _seed_external_payee(app, staff_id)
    expense_id = _create_and_submit(app, staff_id, profile_id, category_id, payee_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, staff_id)
        with pytest.raises(ExpenseError) as exc:
            decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by)
        assert exc.value.code == "SELF_APPROVAL_FORBIDDEN"


def test_requester_with_super_admin_cannot_approve_own_expense(app, seeded):
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.errors import ExpenseError
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session
    from app.employees.services import create_employee_profile, activate_employee

    staff_id = make_staff(app, "superreq@example.com", super_admin=True)
    with app.app_context():
        profile = create_employee_profile(
            {"staff_user_id": staff_id, "employee_number": f"EMP-{next(_employee_number_counter):05d}",
             "full_name": "Super Admin Requester", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_id,
        )
        activate_employee(profile, actor_staff_user_id=staff_id)
        profile_id = profile.id

    category_id = _seed_category(app, "TRAVEL2")
    payee_id = _seed_external_payee(app, staff_id)
    expense_id = _create_and_submit(app, staff_id, profile_id, category_id, payee_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, staff_id)
        with pytest.raises(ExpenseError) as exc:
            decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by)
        assert exc.value.code == "SELF_APPROVAL_FORBIDDEN"


def test_employee_beneficiary_cannot_approve(app, seeded):
    """Rule 3: even though the beneficiary employee is a DIFFERENT person
    from the requester, they still cannot approve -- they have a financial
    stake in the outcome."""
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.errors import ExpenseError
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req2@example.com", ["SALES"])
    beneficiary_staff_id, beneficiary_profile_id = _seed_employee(app, "benef@example.com", ["FINANCE"])
    category_id = _seed_category(app, "TRAVEL3")
    payee_id = _seed_employee_payee(app, requester_staff_id, beneficiary_profile_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        assert expense.beneficiary_employee_profile_id == beneficiary_profile_id
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, beneficiary_staff_id)
        with pytest.raises(ExpenseError) as exc:
            decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by)
        assert exc.value.code == "BENEFICIARY_APPROVAL_FORBIDDEN"


def test_external_payee_creator_not_automatically_conflicted(app, seeded):
    """Rule 3: 'For external payees, no conflict exists merely because the
    approver created or manages the payee record.'"""
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req3@example.com", ["SALES"])
    approver_staff_id, approver_profile_id = _seed_employee(app, "appr3@example.com", ["FINANCE"])
    category_id = _seed_category(app, "TRAVEL4")
    # The approver themself created the external payee record.
    payee_id = _seed_external_payee(app, approver_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, approver_staff_id)
        result = decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by)
        assert result.status == "APPROVED"


def test_suspended_approver_rejected(app, seeded):
    from app.employees.services import suspend_employee
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.errors import ExpenseError
    from app.models.employees import EmployeeProfile
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req4@example.com", ["SALES"])
    approver_staff_id, approver_profile_id = _seed_employee(app, "appr4@example.com", ["FINANCE"])
    category_id = _seed_category(app, "TRAVEL5")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    with app.app_context():
        profile = db_session.get(EmployeeProfile, approver_profile_id)
        suspend_employee(profile, "policy violation", actor_staff_user_id=requester_staff_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, approver_staff_id)
        with pytest.raises(ExpenseError) as exc:
            decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by)
        # suspend_employee() cascades to StaffUser.is_active=False (Phase 9.5B),
        # so the account-inactive check -- checked first, correctly -- fires
        # before the employment_status check is ever reached. The block is
        # what matters; APPROVER_SUSPENDED_OR_TERMINATED remains real
        # defense-in-depth for a hypothetical desync where is_active stays
        # true but employment_status doesn't (not reachable via any current
        # service-layer path).
        assert exc.value.code == "APPROVER_INELIGIBLE"


def test_terminated_approver_rejected(app, seeded):
    from app.employees.services import terminate_employee
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.errors import ExpenseError
    from app.models.employees import EmployeeProfile
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req5@example.com", ["SALES"])
    approver_staff_id, approver_profile_id = _seed_employee(app, "appr5@example.com", ["FINANCE"])
    category_id = _seed_category(app, "TRAVEL6")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    with app.app_context():
        profile = db_session.get(EmployeeProfile, approver_profile_id)
        terminate_employee(profile, "resigned", actor_staff_user_id=requester_staff_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, approver_staff_id)
        with pytest.raises(ExpenseError) as exc:
            decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by)
        # Same reasoning as test_suspended_approver_rejected above --
        # terminate_employee() also cascades to StaffUser.is_active=False.
        assert exc.value.code == "APPROVER_INELIGIBLE"


def test_approver_without_employee_profile_rejected(app, seeded):
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.errors import ExpenseError
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req6@example.com", ["SALES"])
    category_id = _seed_category(app, "TRAVEL7")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    approver_staff_id = make_staff(app, "noprofile@example.com", role_codes=["FINANCE"])

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, approver_staff_id)
        with pytest.raises(ExpenseError) as exc:
            decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by)
        assert exc.value.code == "APPROVER_MISSING_EMPLOYEE_PROFILE"


def test_stale_fingerprint_rejected(app, seeded):
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.errors import ExpenseError
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req7@example.com", ["SALES"])
    approver_staff_id, approver_profile_id = _seed_employee(app, "appr7@example.com", ["FINANCE"])
    category_id = _seed_category(app, "TRAVEL8")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        expense.description = "Materially different business purpose"
        db_session.commit()

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, approver_staff_id)
        with pytest.raises(ExpenseError) as exc:
            decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by)
        assert exc.value.code == "APPROVAL_STALE"


def test_material_amount_change_invalidates_approval(app, seeded):
    from app.expenses.fingerprint import current_fingerprint_for_expense
    from app.expenses.approvals import pending_approval_for_expense
    from app.models.expenses import Expense
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req8@example.com", ["SALES"])
    category_id = _seed_category(app, "TRAVEL9")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        original_fingerprint = approval.fingerprint
        expense.amount = Decimal("999.00")
        db_session.commit()
        assert current_fingerprint_for_expense(expense) != original_fingerprint


def test_view_and_audit_actions_do_not_invalidate_approval(app, seeded):
    from app.audit.services import record as audit_record
    from app.expenses.fingerprint import current_fingerprint_for_expense
    from app.expenses.approvals import pending_approval_for_expense
    from app.models.expenses import Expense
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req9@example.com", ["SALES"])
    category_id = _seed_category(app, "TRAVEL10")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        original_fingerprint = approval.fingerprint
        audit_record(
            actor_staff_user_id=requester_staff_id, actor_role_snapshot=None,
            action_code="EXPENSE_VIEWED", entity_type="expense", entity_public_id=str(expense.id),
        )
        assert current_fingerprint_for_expense(expense) == original_fingerprint


def test_approved_amount_above_requested_rejected(app, seeded):
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.errors import ExpenseError
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req10@example.com", ["SALES"])
    approver_staff_id, approver_profile_id = _seed_employee(app, "appr10@example.com", ["FINANCE"])
    category_id = _seed_category(app, "TRAVEL11")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id, amount=Decimal("100.00"))

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, approver_staff_id)
        with pytest.raises(ExpenseError) as exc:
            decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by, approved_amount=Decimal("150.00"))
        assert exc.value.code == "APPROVED_AMOUNT_EXCEEDS_REQUESTED"


def test_approved_lower_amount_works(app, seeded):
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req11@example.com", ["SALES"])
    approver_staff_id, approver_profile_id = _seed_employee(app, "appr11@example.com", ["FINANCE"])
    category_id = _seed_category(app, "TRAVEL12")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id, amount=Decimal("100.00"))

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, approver_staff_id)
        result = decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by, approved_amount=Decimal("80.00"))
        assert result.status == "APPROVED"
        assert result.approved_amount == Decimal("80.00")
        assert expense.status == "APPROVED"
        assert expense.approved_amount == Decimal("80.00")


def test_returned_expense_resubmission_creates_new_approval_cycle(app, seeded):
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.lifecycle import revise_expense, submit_expense
    from app.models.expenses import Expense, ExpenseApproval
    from app.models.staff import StaffUser
    from app.extensions import db_session
    from sqlalchemy import select

    requester_staff_id, requester_profile_id = _seed_employee(app, "req12@example.com", ["SALES"])
    approver_staff_id, approver_profile_id = _seed_employee(app, "appr12@example.com", ["FINANCE"])
    category_id = _seed_category(app, "TRAVEL13")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    old_approval_id = None
    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        old_approval_id = approval.id
        decided_by = db_session.get(StaffUser, approver_staff_id)
        decide_expense_approval(approval, expense, decision="RETURNED", decided_by=decided_by, decision_reason="need receipt")
        assert expense.status == "RETURNED"

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        revise_expense(expense, actor_staff_user_id=requester_staff_id, description="Client dinner (with receipt)")
        submit_expense(expense, actor_staff_user_id=requester_staff_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        assert expense.status == "SUBMITTED"
        new_approval = pending_approval_for_expense(expense)
        assert new_approval.id != old_approval_id
        old = db_session.get(ExpenseApproval, old_approval_id)
        assert old.status == "RETURNED"


def test_old_approval_cannot_be_reused_after_resubmission(app, seeded):
    """The old (RETURNED) ExpenseApproval row is immutable/terminal --
    attempting to decide it again must fail the transition check."""
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.errors import ExpenseError
    from app.expenses.lifecycle import revise_expense, submit_expense
    from app.models.expenses import Expense, ExpenseApproval
    from app.models.staff import StaffUser
    from app.extensions import db_session

    requester_staff_id, requester_profile_id = _seed_employee(app, "req13@example.com", ["SALES"])
    approver_staff_id, approver_profile_id = _seed_employee(app, "appr13@example.com", ["FINANCE"])
    category_id = _seed_category(app, "TRAVEL14")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    old_approval_id = None
    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        old_approval_id = approval.id
        decided_by = db_session.get(StaffUser, approver_staff_id)
        decide_expense_approval(approval, expense, decision="RETURNED", decided_by=decided_by, decision_reason="need receipt")

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        revise_expense(expense, actor_staff_user_id=requester_staff_id, description="revised")
        submit_expense(expense, actor_staff_user_id=requester_staff_id)

    with app.app_context():
        old_approval = db_session.get(ExpenseApproval, old_approval_id)
        expense = db_session.get(Expense, expense_id)
        decided_by = db_session.get(StaffUser, approver_staff_id)
        with pytest.raises(ExpenseError) as exc:
            decide_expense_approval(old_approval, expense, decision="APPROVED", decided_by=decided_by)
        assert exc.value.code == "INVALID_EXPENSE_APPROVAL_TRANSITION"


def test_approval_never_creates_payment_or_sets_paid(app, seeded):
    """Rule 1: approval only raises approved_amount / flips to APPROVED --
    it must never touch ExpensePayment or PAID status."""
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.models.expenses import Expense, ExpensePayment
    from app.models.staff import StaffUser
    from app.extensions import db_session
    from sqlalchemy import select

    requester_staff_id, requester_profile_id = _seed_employee(app, "req14@example.com", ["SALES"])
    approver_staff_id, approver_profile_id = _seed_employee(app, "appr14@example.com", ["FINANCE"])
    category_id = _seed_category(app, "TRAVEL15")
    payee_id = _seed_external_payee(app, requester_staff_id)
    expense_id = _create_and_submit(app, requester_staff_id, requester_profile_id, category_id, payee_id)

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, approver_staff_id)
        decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by)

        assert expense.status == "APPROVED"
        payments = db_session.execute(select(ExpensePayment).where(ExpensePayment.expense_id == expense.id)).scalars().all()
        assert payments == []
