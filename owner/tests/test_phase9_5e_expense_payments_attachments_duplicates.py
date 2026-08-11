"""Phase 9.5E -- ExpensePayment (partial/overpayment/idempotency/reversal),
attachment security, and duplicate-detection tests."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import make_staff

_employee_number_counter = iter(range(200000, 300000))


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


def _seed_category(app, code):
    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory
    with app.app_context():
        cat = ExpenseCategory(category_code=code, name=code.title(), is_active=True)
        db_session.add(cat)
        db_session.commit()
        return cat.id


def _seed_payee(app, creator_staff_id):
    from app.expenses.payees import create_payee
    with app.app_context():
        payee = create_payee(
            payee_type="EXTERNAL", display_name="Vendor Co", employee_profile_id=None,
            external_contact_reference="v@example.com", created_by_staff_user_id=creator_staff_id,
        )
        return payee.id


def _approved_expense(app, requester_staff_id, requester_profile_id, approver_staff_id, category_id, payee_id, amount=Decimal("100.00"), external_reference=None):
    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.lifecycle import create_expense, submit_expense
    from app.models.expenses import Expense
    from app.models.staff import StaffUser
    from app.extensions import db_session

    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=amount, currency="USD",
            expense_date=date(2026, 8, 1), description="Test expense", external_reference=external_reference,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=requester_profile_id,
        )
        submit_expense(expense, actor_staff_user_id=requester_staff_id)
        approval = pending_approval_for_expense(expense)
        decided_by = db_session.get(StaffUser, approver_staff_id)
        decide_expense_approval(approval, expense, decision="APPROVED", decided_by=decided_by)
        return expense.id


# --- Payments ---

def test_partial_then_full_payment_reaches_paid(app, seeded):
    from app.expenses.payments import outstanding_amount, record_expense_payment
    from app.models.expenses import Expense
    from app.extensions import db_session

    req_staff, req_profile = _seed_employee(app, "p1req@example.com", ["SALES"])
    appr_staff, appr_profile = _seed_employee(app, "p1appr@example.com", ["FINANCE"])
    category_id = _seed_category(app, "PAYCAT1")
    payee_id = _seed_payee(app, req_staff)
    expense_id = _approved_expense(app, req_staff, req_profile, appr_staff, category_id, payee_id, amount=Decimal("100.00"))

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        record_expense_payment(expense, amount=Decimal("40.00"), currency="USD", payment_method="CASH",
                                payment_reference=None, recorded_by_staff_user_id=appr_staff)
        assert expense.status == "PARTIALLY_PAID"
        assert outstanding_amount(expense) == Decimal("60.00")

        record_expense_payment(expense, amount=Decimal("60.00"), currency="USD", payment_method="CASH",
                                payment_reference=None, recorded_by_staff_user_id=appr_staff)
        assert expense.status == "PAID"
        assert outstanding_amount(expense) == Decimal("0.00")


def test_payment_cannot_exceed_outstanding(app, seeded):
    from app.expenses.payments import record_expense_payment
    from app.expenses.errors import ExpenseError
    from app.models.expenses import Expense
    from app.extensions import db_session

    req_staff, req_profile = _seed_employee(app, "p2req@example.com", ["SALES"])
    appr_staff, appr_profile = _seed_employee(app, "p2appr@example.com", ["FINANCE"])
    category_id = _seed_category(app, "PAYCAT2")
    payee_id = _seed_payee(app, req_staff)
    expense_id = _approved_expense(app, req_staff, req_profile, appr_staff, category_id, payee_id, amount=Decimal("100.00"))

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        with pytest.raises(ExpenseError) as exc:
            record_expense_payment(expense, amount=Decimal("150.00"), currency="USD", payment_method="CASH",
                                    payment_reference=None, recorded_by_staff_user_id=appr_staff)
        assert exc.value.code == "PAYMENT_EXCEEDS_OUTSTANDING"


def test_concurrent_payments_cannot_jointly_overpay(app, seeded):
    """Real concurrency proof, matching Phase 9.5D's own confirm_refund()
    race test pattern -- two threads each try to pay the full outstanding
    amount; only one may succeed."""
    import threading

    from app import create_app
    from app.expenses.payments import record_expense_payment
    from app.expenses.errors import ExpenseError
    from app.extensions import db_session
    from app.models.expenses import Expense

    req_staff, req_profile = _seed_employee(app, "p3req@example.com", ["SALES"])
    appr_staff, appr_profile = _seed_employee(app, "p3appr@example.com", ["FINANCE"])
    category_id = _seed_category(app, "PAYCAT3")
    payee_id = _seed_payee(app, req_staff)
    expense_id = _approved_expense(app, req_staff, req_profile, appr_staff, category_id, payee_id, amount=Decimal("100.00"))

    results = []

    def worker():
        thread_app = create_app("testing")
        with thread_app.app_context():
            from app.extensions import db_session as thread_db_session
            expense = thread_db_session.get(Expense, expense_id)
            try:
                record_expense_payment(expense, amount=Decimal("100.00"), currency="USD", payment_method="CASH",
                                        payment_reference=None, recorded_by_staff_user_id=appr_staff)
                results.append("ok")
            except ExpenseError:
                results.append("blocked")
            finally:
                thread_db_session.remove()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1
    assert results.count("blocked") == 3

    with app.app_context():
        from app.expenses.payments import _valid_paid_amount
        assert _valid_paid_amount(expense_id) == Decimal("100.00")


def test_payment_idempotency_key_returns_original(app, seeded):
    from app.expenses.payments import record_expense_payment
    from app.models.expenses import Expense
    from app.extensions import db_session

    req_staff, req_profile = _seed_employee(app, "p4req@example.com", ["SALES"])
    appr_staff, appr_profile = _seed_employee(app, "p4appr@example.com", ["FINANCE"])
    category_id = _seed_category(app, "PAYCAT4")
    payee_id = _seed_payee(app, req_staff)
    expense_id = _approved_expense(app, req_staff, req_profile, appr_staff, category_id, payee_id, amount=Decimal("100.00"))

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        p1 = record_expense_payment(expense, amount=Decimal("40.00"), currency="USD", payment_method="CASH",
                                     payment_reference=None, recorded_by_staff_user_id=appr_staff, idempotency_key="IDEM-1")
        p2 = record_expense_payment(expense, amount=Decimal("40.00"), currency="USD", payment_method="CASH",
                                     payment_reference=None, recorded_by_staff_user_id=appr_staff, idempotency_key="IDEM-1")
        assert p1.id == p2.id
        from app.expenses.payments import _valid_paid_amount
        assert _valid_paid_amount(expense_id) == Decimal("40.00")


def test_conflicting_idempotency_payload_rejected(app, seeded):
    from app.expenses.payments import record_expense_payment
    from app.expenses.errors import ExpenseError
    from app.models.expenses import Expense
    from app.extensions import db_session

    req_staff, req_profile = _seed_employee(app, "p5req@example.com", ["SALES"])
    appr_staff, appr_profile = _seed_employee(app, "p5appr@example.com", ["FINANCE"])
    category_id = _seed_category(app, "PAYCAT5")
    payee_id = _seed_payee(app, req_staff)
    expense_id = _approved_expense(app, req_staff, req_profile, appr_staff, category_id, payee_id, amount=Decimal("100.00"))

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        record_expense_payment(expense, amount=Decimal("40.00"), currency="USD", payment_method="CASH",
                                payment_reference=None, recorded_by_staff_user_id=appr_staff, idempotency_key="IDEM-2")
        with pytest.raises(ExpenseError) as exc:
            record_expense_payment(expense, amount=Decimal("50.00"), currency="USD", payment_method="CASH",
                                    payment_reference=None, recorded_by_staff_user_id=appr_staff, idempotency_key="IDEM-2")
        assert exc.value.code == "IDEMPOTENCY_CONFLICT"


def test_payment_reversal_is_append_only(app, seeded):
    from app.expenses.payments import record_expense_payment, reverse_expense_payment
    from app.models.expenses import Expense, ExpensePayment
    from app.extensions import db_session
    from sqlalchemy import select

    req_staff, req_profile = _seed_employee(app, "p6req@example.com", ["SALES"])
    appr_staff, appr_profile = _seed_employee(app, "p6appr@example.com", ["FINANCE"])
    category_id = _seed_category(app, "PAYCAT6")
    payee_id = _seed_payee(app, req_staff)
    expense_id = _approved_expense(app, req_staff, req_profile, appr_staff, category_id, payee_id, amount=Decimal("100.00"))

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        payment = record_expense_payment(expense, amount=Decimal("100.00"), currency="USD", payment_method="CASH",
                                          payment_reference=None, recorded_by_staff_user_id=appr_staff)
        assert expense.status == "PAID"
        reverse_expense_payment(payment, expense, actor_staff_user_id=appr_staff, reason="wrong amount recorded")

        rows = db_session.execute(select(ExpensePayment).where(ExpensePayment.expense_id == expense_id)).scalars().all()
        assert len(rows) == 2  # original (now REVERSED) + reversal row
        assert expense.status == "APPROVED"
        from app.expenses.payments import outstanding_amount
        assert outstanding_amount(expense) == Decimal("100.00")


# --- Attachments ---

def test_attachment_upload_rejects_content_mime_mismatch(app, seeded):
    from app.expenses.attachments import upload_attachment
    from app.expenses.errors import ExpenseError
    from app.expenses.lifecycle import create_expense
    from app.extensions import db_session

    req_staff, req_profile = _seed_employee(app, "a1req@example.com", ["SALES"])
    category_id = _seed_category(app, "ATTCAT1")
    payee_id = _seed_payee(app, req_staff)
    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("50.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="Receipt test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=req_profile,
        )
        with pytest.raises(ExpenseError) as exc:
            upload_attachment(expense, content=b"not a real pdf", original_filename="receipt.pdf",
                               declared_content_type="application/pdf", uploaded_by_employee_profile_id=req_profile)
        assert exc.value.code == "ATTACHMENT_CONTENT_MISMATCH"


def test_attachment_upload_rejects_disallowed_type(app, seeded):
    from app.expenses.attachments import upload_attachment
    from app.expenses.errors import ExpenseError
    from app.expenses.lifecycle import create_expense

    req_staff, req_profile = _seed_employee(app, "a2req@example.com", ["SALES"])
    category_id = _seed_category(app, "ATTCAT2")
    payee_id = _seed_payee(app, req_staff)
    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("50.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="Receipt test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=req_profile,
        )
        with pytest.raises(ExpenseError) as exc:
            upload_attachment(expense, content=b"<html><script>alert(1)</script></html>", original_filename="evil.html",
                               declared_content_type="text/html", uploaded_by_employee_profile_id=req_profile)
        assert exc.value.code == "ATTACHMENT_TYPE_NOT_ALLOWED"


def test_attachment_storage_key_traversal_rejected_on_read(app, seeded):
    from app.expenses.attachments import read_attachment_bytes
    from app.expenses.errors import ExpenseError
    from app.expenses.lifecycle import create_expense
    from app.models.expenses import ExpenseAttachment
    from app.models.base import utcnow
    from app.extensions import db_session

    req_staff, req_profile = _seed_employee(app, "a3req@example.com", ["SALES"])
    category_id = _seed_category(app, "ATTCAT3")
    payee_id = _seed_payee(app, req_staff)
    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("50.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="Receipt test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=req_profile,
        )
        malicious = ExpenseAttachment(
            expense_id=expense.id, storage_key="../../../../etc/passwd", original_filename="x",
            content_type="application/pdf", content_hash="deadbeef", size_bytes=4, status="ACTIVE",
            uploaded_by_employee_profile_id=req_profile, uploaded_at=utcnow(),
        )
        db_session.add(malicious)
        db_session.commit()
        with pytest.raises(ExpenseError) as exc:
            read_attachment_bytes(malicious)
        assert exc.value.code in ("INVALID_STORAGE_KEY", "ATTACHMENT_NOT_FOUND")


def test_attachment_upload_and_download_round_trip(app, seeded):
    from app.expenses.attachments import read_attachment_bytes, upload_attachment
    from app.expenses.lifecycle import create_expense

    req_staff, req_profile = _seed_employee(app, "a4req@example.com", ["SALES"])
    category_id = _seed_category(app, "ATTCAT4")
    payee_id = _seed_payee(app, req_staff)
    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("50.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="Receipt test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=req_profile,
        )
        real_pdf_bytes = b"%PDF-1.4\n%mock pdf content for testing\n"
        attachment = upload_attachment(expense, content=real_pdf_bytes, original_filename="../../evil<>.pdf",
                                        declared_content_type="application/pdf", uploaded_by_employee_profile_id=req_profile)
        assert "/" not in attachment.original_filename and ".." not in attachment.original_filename
        fetched = read_attachment_bytes(attachment)
        assert fetched == real_pdf_bytes


# --- Duplicate detection ---

def test_exact_external_reference_duplicate_detected(app, seeded):
    from app.expenses.duplicates import find_duplicate_signals
    from app.models.expenses import Expense
    from app.extensions import db_session

    req_staff, req_profile = _seed_employee(app, "d1req@example.com", ["SALES"])
    appr_staff, appr_profile = _seed_employee(app, "d1appr@example.com", ["FINANCE"])
    category_id = _seed_category(app, "DUPCAT1")
    payee_id = _seed_payee(app, req_staff)
    first_id = _approved_expense(app, req_staff, req_profile, appr_staff, category_id, payee_id, external_reference="INV-9001")

    from app.expenses.lifecycle import create_expense
    with app.app_context():
        second = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("100.00"), currency="USD",
            expense_date=date(2026, 8, 2), description="Same vendor invoice again", external_reference="INV-9001",
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=req_profile,
        )
        signals = find_duplicate_signals(second)
        assert any(s["signal_type"] == "EXACT_EXTERNAL_REFERENCE" and first_id in s["matched_expense_ids"] for s in signals)


def test_duplicate_override_requires_reason_and_does_not_approve(app, seeded):
    from app.expenses.duplicates import override_duplicate_warning
    from app.expenses.errors import ExpenseError
    from app.expenses.lifecycle import create_expense
    from app.extensions import db_session

    req_staff, req_profile = _seed_employee(app, "d2req@example.com", ["SALES"])
    category_id = _seed_category(app, "DUPCAT2")
    payee_id = _seed_payee(app, req_staff)
    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("50.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="dup test", external_reference="X",
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=req_profile,
        )
        with pytest.raises(ExpenseError) as exc:
            override_duplicate_warning(expense, actor_staff_user_id=req_staff, reason="", signals=[])
        assert exc.value.code == "DUPLICATE_OVERRIDE_REQUIRES_REASON"

        override_duplicate_warning(expense, actor_staff_user_id=req_staff, reason="verified with vendor, not a duplicate", signals=[])
        assert expense.status == "DRAFT"  # unchanged -- override never approves
