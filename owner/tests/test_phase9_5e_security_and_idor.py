"""Phase 9.5E Milestone 20 -- complete security/IDOR matrix. Real, new
coverage beyond what M15/M16's route tests already proved: payment IDOR,
cash-closing immutability/late-transaction policy, report-snapshot access
control, attachment filename-attack resistance, audit-log redaction,
beneficiary-conflict via the real HTTP boundary, concurrent cash-closing
creation, and duplicate-review privacy redaction."""
from __future__ import annotations

import io
import threading
from datetime import date
from decimal import Decimal

import pytest

from tests.conftest import force_login, get_csrf, make_staff

_employee_number_counter = iter(range(900000, 1000000))


def _csrf(client):
    return get_csrf(client.get("/profile").get_data(as_text=True))


def _seed_active_employee(app, email, role_codes):
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


def _seed_category(app):
    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory
    import uuid

    with app.app_context():
        cat = ExpenseCategory(category_code=f"SEC-{uuid.uuid4().hex[:8]}", name="Security Test", is_active=True)
        db_session.add(cat)
        db_session.commit()
        return cat.id


def _seed_payee(app, creator_staff_id):
    from app.expenses.payees import create_payee
    with app.app_context():
        payee = create_payee(payee_type="EXTERNAL", display_name="V", employee_profile_id=None, external_contact_reference=None, created_by_staff_user_id=creator_staff_id)
        return payee.id


# --------------------------------------------------------------- Payment IDOR --

def test_expense_payment_record_not_leaked_to_unauthorized_peer(app, client, seeded):
    staff_a, profile_a = _seed_active_employee(app, "sec1a@example.com", ["SALES"])
    staff_fin, _ = _seed_active_employee(app, "sec1fin@example.com", ["FINANCE"])
    staff_b, _ = _seed_active_employee(app, "sec1b@example.com", ["SALES"])
    category_id = _seed_category(app)
    payee_id = _seed_payee(app, staff_fin)

    from app.expenses.approvals import decide_expense_approval, pending_approval_for_expense
    from app.expenses.lifecycle import create_expense, submit_expense
    from app.expenses.payments import record_expense_payment
    from app.extensions import db_session
    from app.models.staff import StaffUser

    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("50.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="payment idor test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=profile_a,
        )
        submit_expense(expense, actor_staff_user_id=staff_a)
        approval = pending_approval_for_expense(expense)
        fin_obj = db_session.get(StaffUser, staff_fin)
        decide_expense_approval(approval, expense, decision="APPROVED", decided_by=fin_obj, approved_amount=Decimal("50.00"))
        payment = record_expense_payment(expense, amount=Decimal("50.00"), currency="USD", payment_method="CASH", payment_reference=None, recorded_by_staff_user_id=staff_fin)
        expense_id, payment_id = expense.id, payment.id

    # Employee B (no relation to this expense, no expenses.pay) tries to
    # reverse a payment they have no business touching.
    force_login(client, app, staff_b)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/payments/{payment_id}/reverse", json={"reason": "unauthorized attempt"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 403  # SALES lacks expenses.pay entirely

    # Employee B cannot even view the parent expense to discover the payment exists.
    resp = client.get(f"/api/operations/v1/expenses/{expense_id}")
    assert resp.status_code == 404


# ---------------------------------------------------- Cash-closing immutability --

def _make_closed_closing(app, prep_staff, approver_staff, business_date, currency="USD"):
    from app.cash_closing.services import close_closing, decide_closing, get_or_create_draft_closing, submit_closing

    with app.app_context():
        closing = get_or_create_draft_closing(business_date, currency, prepared_by_staff_user_id=prep_staff, opening_cash_override=Decimal("0.00"), opening_cash_override_reason="test")
        submit_closing(closing, actor_staff_user_id=prep_staff, actual_counted_cash=Decimal("0.00"), variance_explanation=None)
        decide_closing(closing, decision="APPROVED", actor_staff_user_id=approver_staff)
        close_closing(closing, actor_staff_user_id=approver_staff)
        return closing.id


def test_approved_and_closed_cash_closing_cannot_be_resubmitted_or_redecided(app, client, seeded):
    staff_prep, _ = _seed_active_employee(app, "sec2prep@example.com", ["FINANCE"])
    staff_appr, _ = _seed_active_employee(app, "sec2appr@example.com", ["FINANCE"])
    closing_id = _make_closed_closing(app, staff_prep, staff_appr, date(2026, 8, 25))

    force_login(client, app, staff_prep)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/cash-closings/{closing_id}/submit", json={"actual_counted_cash": "999.00"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "INVALID_CASH_CLOSING_TRANSITION"

    force_login(client, app, staff_appr)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/cash-closings/{closing_id}/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "INVALID_CASH_CLOSING_TRANSITION"

    from app.extensions import db_session
    from app.models.cash_closing import CashClosing
    with app.app_context():
        closing = db_session.get(CashClosing, closing_id)
        assert closing.status == "CLOSED"
        assert closing.actual_counted_cash == Decimal("0.00")  # never silently rewritten


def test_late_transaction_requires_reopen_not_silent_rewrite(app, seeded):
    """REOPEN_REQUIRED_FOR_SAME_BUSINESS_DATE: a get_or_create_draft_closing()
    call for an already-CLOSED scope returns the existing, immutable row --
    it never silently creates a second draft or mutates the closed one."""
    from app.cash_closing.services import get_or_create_draft_closing

    staff_prep, _ = _seed_active_employee(app, "sec3prep@example.com", ["FINANCE"])
    staff_appr, _ = _seed_active_employee(app, "sec3appr@example.com", ["FINANCE"])
    closing_id = _make_closed_closing(app, staff_prep, staff_appr, date(2026, 8, 26))

    with app.app_context():
        same_scope = get_or_create_draft_closing(date(2026, 8, 26), "USD", prepared_by_staff_user_id=staff_prep)
        assert same_scope.id == closing_id
        assert same_scope.status == "CLOSED"


def test_concurrent_cash_closing_creation_for_same_scope_does_not_duplicate(app, seeded):
    """Real thread-based race proof, matching the exact pattern already
    proven for expense payments -- two threads racing to create a closing
    for the identical (business_date, currency) scope must end with exactly
    one row, never two."""
    from app import create_app

    staff_prep, _ = _seed_active_employee(app, "sec4prep@example.com", ["FINANCE"])
    business_date = date(2026, 8, 27)
    results = []

    errors = []

    def worker():
        thread_app = create_app("testing")
        with thread_app.app_context():
            from app.cash_closing.services import get_or_create_draft_closing
            from app.extensions import db_session as thread_db_session
            try:
                closing = get_or_create_draft_closing(business_date, "USD", prepared_by_staff_user_id=staff_prep, opening_cash_override=Decimal("10.00"), opening_cash_override_reason="race test")
                results.append(str(closing.id))
            except Exception as exc:  # noqa: BLE001 -- proving zero threads crash, whatever the exception type
                errors.append(repr(exc))
            finally:
                thread_db_session.remove()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"get_or_create_draft_closing() must gracefully fall back on a concurrent-insert race, not raise: {errors}"
    assert len(results) == 6
    assert len(set(results)) == 1, f"expected exactly one closing id across all racing threads, got {set(results)}"

    from app.extensions import db_session
    from app.models.cash_closing import CashClosing
    from sqlalchemy import select
    with app.app_context():
        rows = db_session.execute(select(CashClosing).where(CashClosing.business_date == business_date, CashClosing.currency == "USD")).scalars().all()
        assert len(rows) == 1


# --------------------------------------------------------- Report snapshot access --

def test_report_snapshot_list_and_generate_require_permission(app, client, seeded):
    staff = make_staff(app, "sec5@example.com", role_codes=["SALES"])
    force_login(client, app, staff)

    resp = client.get("/api/operations/v1/report-snapshots")
    assert resp.status_code == 403  # SALES has no report_snapshots.view

    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/report-snapshots/generate",
        json={"report_type": "DAILY_OPERATIONAL_SUMMARY", "period_start": "2026-08-01", "period_end": "2026-08-01", "currency": "USD"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 403


# ----------------------------------------------------------- Attachment attacks --

def test_attachment_filename_traversal_is_sanitized_never_used_as_path(app, seeded):
    from app.expenses.attachments import upload_attachment
    from app.expenses.lifecycle import create_expense

    staff, profile = _seed_active_employee(app, "sec6@example.com", ["SALES"])
    category_id = _seed_category(app)
    payee_id = _seed_payee(app, staff)

    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("5.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="attack test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=profile,
        )
        adversarial_names = [
            "../../../../windows/system32/drivers/etc/hosts.pdf",
            "..\\..\\..\\secrets.pdf",
            "/etc/passwd.pdf",
            "receipt\x00.exe.pdf",
            "receipt.pdf" + "A" * 500,
        ]
        for name in adversarial_names:
            attachment = upload_attachment(
                expense, content=b"%PDF-1.4\nreal\n", original_filename=name,
                declared_content_type="application/pdf", uploaded_by_employee_profile_id=profile,
            )
            assert "/" not in attachment.original_filename
            assert "\\" not in attachment.original_filename
            assert ".." not in attachment.original_filename
            assert "\x00" not in attachment.original_filename
            assert len(attachment.original_filename) <= 200
            # storage_key is always server-generated, never derived from the name.
            assert attachment.storage_key.split("/")[-1] != name


def test_attachment_svg_and_html_payload_rejected(app, seeded):
    from app.expenses.attachments import upload_attachment
    from app.expenses.errors import ExpenseError
    from app.expenses.lifecycle import create_expense

    staff, profile = _seed_active_employee(app, "sec7@example.com", ["SALES"])
    category_id = _seed_category(app)
    payee_id = _seed_payee(app, staff)

    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("5.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="svg test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=profile,
        )
        for content, declared_type in (
            (b"<svg onload='alert(1)'></svg>", "image/svg+xml"),
            (b"<html><script>alert(document.cookie)</script></html>", "text/html"),
            (b"MZ\x90\x00\x03\x00\x00\x00", "application/x-msdownload"),
        ):
            with pytest.raises(ExpenseError) as exc:
                upload_attachment(expense, content=content, original_filename="x", declared_content_type=declared_type, uploaded_by_employee_profile_id=profile)
            assert exc.value.code == "ATTACHMENT_TYPE_NOT_ALLOWED"


def test_attachment_disguised_as_pdf_via_extension_only_is_rejected(app, seeded):
    """A file literally named receipt.pdf whose real bytes are an HTML/script
    payload must be rejected by the magic-byte check -- the extension in
    the filename is never trusted."""
    from app.expenses.attachments import upload_attachment
    from app.expenses.errors import ExpenseError
    from app.expenses.lifecycle import create_expense

    staff, profile = _seed_active_employee(app, "sec8@example.com", ["SALES"])
    category_id = _seed_category(app)
    payee_id = _seed_payee(app, staff)

    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("5.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="disguise test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=profile,
        )
        with pytest.raises(ExpenseError) as exc:
            upload_attachment(
                expense, content=b"<script>alert(1)</script>", original_filename="receipt.pdf",
                declared_content_type="application/pdf", uploaded_by_employee_profile_id=profile,
            )
        assert exc.value.code == "ATTACHMENT_CONTENT_MISMATCH"


# ----------------------------------------------------------- Audit redaction --

def test_audit_log_never_contains_attachment_bytes_or_note_body(app, seeded):
    from app.expenses.attachments import upload_attachment
    from app.expenses.lifecycle import create_expense
    from app.management_notes.service import create_note
    from app.extensions import db_session
    from app.models.audit import AuditLog
    from sqlalchemy import select
    import json

    staff, profile = _seed_active_employee(app, "sec9@example.com", ["FINANCE"])
    category_id = _seed_category(app)
    payee_id = _seed_payee(app, staff)

    secret_body_marker = "SECRET-NOTE-BODY-MARKER-XYZ"
    secret_bytes_marker = b"SECRET-ATTACHMENT-BYTES-MARKER-XYZ"

    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal("5.00"), currency="USD",
            expense_date=date(2026, 8, 1), description="redaction test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=profile,
        )
        upload_attachment(
            expense, content=b"%PDF-1.4\n" + secret_bytes_marker, original_filename="r.pdf",
            declared_content_type="application/pdf", uploaded_by_employee_profile_id=profile,
        )
        create_note(title="t", body=secret_body_marker, category=None, priority="MEDIUM", visibility="MANAGEMENT_ONLY", assigned_employee_profile_id=None, created_by_staff_user_id=staff)

        rows = db_session.execute(select(AuditLog)).scalars().all()
        for row in rows:
            for blob in (row.after_state_redacted, row.before_state_redacted):
                if blob is None:
                    continue
                serialized = json.dumps(blob)
                assert secret_body_marker not in serialized, f"note body leaked into audit row {row.id}"
                assert secret_bytes_marker.decode("latin-1") not in serialized, f"attachment bytes leaked into audit row {row.id}"


# ------------------------------------------------- Beneficiary conflict via HTTP --

def test_beneficiary_conflict_rejected_via_http(app, client, seeded):
    staff_req, profile_req = _seed_active_employee(app, "sec10req@example.com", ["SALES"])
    staff_ben, profile_ben = _seed_active_employee(app, "sec10ben@example.com", ["FINANCE"])
    category_id = _seed_category(app)

    from app.expenses.payees import create_payee
    with app.app_context():
        payee = create_payee(payee_type="EMPLOYEE", display_name="Reimbursement", employee_profile_id=profile_ben, external_contact_reference=None, created_by_staff_user_id=staff_req)
        payee_id = payee.id

    force_login(client, app, staff_req)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": str(category_id), "payee_id": str(payee_id), "amount": "30.00", "currency": "USD",
              "expense_date": "2026-08-01", "description": "beneficiary conflict test", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf},
    )
    expense_id = resp.get_json()["id"]
    client.post(f"/api/operations/v1/expenses/{expense_id}/submit", json={}, headers={"X-CSRFToken": csrf})

    force_login(client, app, staff_ben)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/approval/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "BENEFICIARY_APPROVAL_FORBIDDEN"


# ------------------------------------------------------- Duplicate-review privacy --

def test_duplicate_signal_redacts_inaccessible_match_details_via_api(app, client, seeded):
    staff_a, profile_a = _seed_active_employee(app, "sec11a@example.com", ["SALES"])
    staff_b, profile_b = _seed_active_employee(app, "sec11b@example.com", ["SALES"])
    category_id = _seed_category(app)
    payee_id = _seed_payee(app, staff_a)

    force_login(client, app, staff_a)
    csrf = _csrf(client)
    client.post(
        "/api/operations/v1/expenses",
        json={"category_id": str(category_id), "payee_id": payee_id, "amount": "10.00", "currency": "USD",
              "expense_date": "2026-08-01", "description": "first", "payment_method": "CASH", "external_reference": "SEC-DUP-REF"},
        headers={"X-CSRFToken": csrf},
    )

    force_login(client, app, staff_b)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": str(category_id), "payee_id": payee_id, "amount": "10.00", "currency": "USD",
              "expense_date": "2026-08-02", "description": "second (peer's duplicate)", "payment_method": "CASH", "external_reference": "SEC-DUP-REF"},
        headers={"X-CSRFToken": csrf},
    )
    second_id = resp.get_json()["id"]

    resp = client.get(f"/api/operations/v1/expenses/{second_id}/duplicates")
    assert resp.status_code == 200
    signals = resp.get_json()["signals"]
    assert signals, "expected a duplicate signal to be detected"
    for signal in signals:
        for match in signal["matches"]:
            if match["accessible"] is False:
                assert set(match.keys()) == {"expense_id", "accessible"}, "inaccessible match leaked extra fields"
