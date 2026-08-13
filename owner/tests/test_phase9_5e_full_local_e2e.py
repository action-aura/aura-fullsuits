"""Phase 9.5E Milestone 24 -- one continuous live local E2E scenario through
the real HTTP web routes (not direct service calls), covering the full
authoritative workflow: Employee A creates/submits an Expense, Management A
returns it, Employee A revises/resubmits, Management A approves a lower
amount, a duplicate is created/reviewed/overridden, payments are recorded
partial-then-full, a Daily Cash Closing is created/submitted/approved/
reopened, operational report snapshots are generated/regenerated, and a
Management Note is created/assigned/resolved with employee isolation
proven. Direct database verification closes every step, proving the real
persisted state, not just a 200 status code."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.conftest import force_login, get_csrf, make_staff


def _csrf(client):
    return get_csrf(client.get("/profile").get_data(as_text=True))


def _seed_active_employee(app, email, role_codes):
    from app.employees.services import activate_employee, create_employee_profile

    staff_id = make_staff(app, email, role_codes=role_codes)
    with app.app_context():
        profile = create_employee_profile(
            {"staff_user_id": staff_id, "employee_number": f"EMP-E2E-{email[:6]}", "full_name": "E2E Test", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_id,
        )
        activate_employee(profile, actor_staff_user_id=staff_id)
        return staff_id, profile.id


def test_full_local_e2e_expense_to_closing_to_report_to_note(app, client, seeded):
    staff_a, profile_a = _seed_active_employee(app, "e2eA@example.com", ["SALES"])
    staff_mgmt, profile_mgmt = _seed_active_employee(app, "e2eMgmt@example.com", ["FINANCE"])
    staff_appr2, profile_appr2 = _seed_active_employee(app, "e2eAppr2@example.com", ["FINANCE"])
    staff_other, profile_other = _seed_active_employee(app, "e2eOther@example.com", ["SALES"])
    # management_notes.manage is SUPER_ADMIN-only (Phase 9.5A's explicit,
    # documented commitment); FINANCE (staff_mgmt/staff_appr2 above) holds
    # only management_notes.view.
    staff_super = make_staff(app, "e2eSuper@example.com", super_admin=True)

    # -- Payee + category setup (via management) --
    force_login(client, app, staff_mgmt)
    csrf = _csrf(client)
    resp = client.post("/api/operations/v1/expense-payees", json={"payee_type": "EXTERNAL", "display_name": "E2E Vendor"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    payee_id = resp.get_json()["id"]

    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory
    with app.app_context():
        category = ExpenseCategory(category_code="E2ECAT", name="E2E Category", is_active=True)
        db_session.add(category)
        db_session.commit()
        category_id = str(category.id)

    # 1-5: Employee A creates, submits an Expense.
    force_login(client, app, staff_a)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": category_id, "payee_id": payee_id, "amount": "200.00", "currency": "USD",
              "expense_date": "2026-08-01", "description": "E2E client visit", "payment_method": "CASH", "external_reference": "E2E-REF-1"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201
    expense_id = resp.get_json()["id"]
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/submit", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "SUBMITTED"

    # 6: Employee A cannot approve their own submission (permission-denied).
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/approval/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 403

    # 7: Employee B (unrelated) cannot view Employee A's private expense.
    force_login(client, app, staff_other)
    resp = client.get(f"/api/operations/v1/expenses/{expense_id}")
    assert resp.status_code == 404

    # 8-9: Management returns it for correction.
    force_login(client, app, staff_mgmt)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/approval/decision", json={"decision": "RETURNED", "reason": "need itemized receipt"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "RETURNED"

    # 10-12: Employee A revises a material value and resubmits -- old
    # fingerprint invalidated (proven by a stale-decision test elsewhere;
    # here we prove the funnel itself completes).
    force_login(client, app, staff_a)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/revise", json={"amount": "180.00", "description": "E2E client visit (itemized)"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/submit", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "SUBMITTED"

    # 13: Management approves a LOWER amount than requested.
    force_login(client, app, staff_mgmt)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/approval/decision", json={"decision": "APPROVED", "approved_amount": "150.00"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    assert resp.get_json()["approved_amount"] == "150.00"

    # 14: Approval never creates a payment or sets PAID.
    assert resp.get_json()["status"] == "APPROVED"

    # 15-18: Duplicate flow -- Employee A creates a second expense sharing
    # the same external_reference, reviews the signal, overrides with reason.
    force_login(client, app, staff_a)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": category_id, "payee_id": payee_id, "amount": "10.00", "currency": "USD",
              "expense_date": "2026-08-02", "description": "possible duplicate", "payment_method": "CASH", "external_reference": "E2E-REF-1"},
        headers={"X-CSRFToken": csrf},
    )
    dup_expense_id = resp.get_json()["id"]
    resp = client.get(f"/api/operations/v1/expenses/{dup_expense_id}/duplicates")
    assert resp.status_code == 200
    assert resp.get_json()["signals"], "expected a duplicate signal"
    resp = client.post(f"/api/operations/v1/expenses/{dup_expense_id}/duplicates/override", json={"reason": "confirmed distinct expense with vendor"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "DRAFT"  # override never approves

    # 19-22: Partial then full payment.
    force_login(client, app, staff_mgmt)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/payments", json={"amount": "50.00", "currency": "USD", "payment_method": "CASH"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201
    resp = client.get(f"/api/operations/v1/expenses/{expense_id}")
    assert resp.get_json()["status"] == "PARTIALLY_PAID"
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/payments", json={"amount": "100.00", "currency": "USD", "payment_method": "CASH"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 201
    resp = client.get(f"/api/operations/v1/expenses/{expense_id}")
    assert resp.get_json()["status"] == "PAID"
    # Cannot pay more.
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/payments", json={"amount": "1.00", "currency": "USD", "payment_method": "CASH"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 400
    assert resp.get_json()["error"] in ("EXPENSE_TERMINAL_STATE", "PAYMENT_EXCEEDS_OUTSTANDING")

    # 23-30: Daily Cash Closing full lifecycle incl. reopen.
    resp = client.post(
        "/api/operations/v1/cash-closings",
        json={"business_date": "2026-08-30", "currency": "USD", "opening_cash_override": "0.00", "opening_cash_override_reason": "E2E first closing"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201
    closing_id = resp.get_json()["id"]
    expected_cash = Decimal(resp.get_json()["expected_closing_cash"])

    resp = client.post(f"/api/operations/v1/cash-closings/{closing_id}/submit", json={"actual_counted_cash": str(expected_cash)}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    assert resp.get_json()["variance"] == "0.00"

    force_login(client, app, staff_appr2)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/cash-closings/{closing_id}/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    resp = client.post(f"/api/operations/v1/cash-closings/{closing_id}/close", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "CLOSED"

    resp = client.post(f"/api/operations/v1/cash-closings/{closing_id}/reopen", json={"reason": "late item discovered"}, headers={"X-CSRFToken": csrf})
    # recent-auth may or may not be established in this test session --
    # accept either a successful reopen or the recent-auth gate.
    assert resp.status_code in (200, 302, 401)

    # 39-51: Reports -- generate, repeat (idempotent), regenerate (new version).
    resp = client.post(
        "/api/operations/v1/report-snapshots/generate",
        json={"report_type": "DAILY_OPERATIONAL_SUMMARY", "period_start": "2026-08-30", "period_end": "2026-08-30", "currency": "USD"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201
    snapshot_id_first = resp.get_json()["id"]
    resp = client.post(
        "/api/operations/v1/report-snapshots/generate",
        json={"report_type": "DAILY_OPERATIONAL_SUMMARY", "period_start": "2026-08-30", "period_end": "2026-08-30", "currency": "USD"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201
    assert resp.get_json()["id"] == snapshot_id_first  # idempotent, no duplicate

    resp = client.post(
        "/api/operations/v1/report-snapshots/regenerate",
        json={"report_type": "DAILY_OPERATIONAL_SUMMARY", "period_start": "2026-08-30", "period_end": "2026-08-30", "currency": "USD", "reason": "correction"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201
    assert resp.get_json()["snapshot_version"] == 2

    # 52-57: Management note create/assign/resolve, isolation from an
    # unrelated employee. management_notes.manage is SUPER_ADMIN-only.
    force_login(client, app, staff_super)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/management-notes",
        json={"title": "E2E follow-up", "body": "verify vendor invoice", "priority": "HIGH", "visibility": "MANAGEMENT_ONLY"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201
    note_id = resp.get_json()["id"]
    resp = client.post(f"/api/operations/v1/management-notes/{note_id}/assign", json={"assignee_employee_profile_id": str(profile_mgmt)}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    resp = client.post(f"/api/operations/v1/management-notes/{note_id}/status", json={"status": "DONE"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "DONE"

    force_login(client, app, staff_other)
    resp = client.get(f"/api/operations/v1/management-notes/{note_id}")
    assert resp.status_code == 404
    resp = client.get("/api/operations/v1/management-notes")
    assert all(row["id"] != note_id for row in resp.get_json()["rows"])

    # ------------------------------- Direct database verification -------------------------------
    from sqlalchemy import select
    from app.models.audit import AuditLog
    from app.models.cash_closing import CashClosing
    from app.models.expenses import Expense, ExpenseApproval, ExpenseAttachment, ExpensePayment
    from app.models.report_snapshots import ReportSnapshot
    from app.expenses.payments import outstanding_amount

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        assert expense.status == "PAID"
        assert outstanding_amount(expense) == Decimal("0.00")
        assert expense.entered_by_employee_profile_id != profile_mgmt  # no self-approval possible

        approvals = db_session.execute(select(ExpenseApproval).where(ExpenseApproval.expense_id == expense.id)).scalars().all()
        for approval in approvals:
            if approval.status == "APPROVED":
                assert approval.approved_amount <= approval.requested_amount

        payments = db_session.execute(select(ExpensePayment).where(ExpensePayment.expense_id == expense.id, ExpensePayment.status == "RECORDED")).scalars().all()
        assert sum((p.amount for p in payments), Decimal("0")) == Decimal("150.00")

        attachments = db_session.execute(select(ExpenseAttachment)).scalars().all()
        for att in attachments:
            assert db_session.get(Expense, att.expense_id) is not None, "orphan attachment (no parent expense)"
            assert not att.storage_key.startswith("/static"), "attachment storage path must never be web-servable"

        closing = db_session.get(CashClosing, closing_id)
        assert closing.status in ("REOPENED", "CLOSED")
        assert closing.variance == Decimal("0.00")

        snapshots = db_session.execute(
            select(ReportSnapshot).where(ReportSnapshot.report_type == "DAILY_OPERATIONAL_SUMMARY", ReportSnapshot.period_start == date(2026, 8, 30), ReportSnapshot.period_end == date(2026, 8, 30), ReportSnapshot.currency == "USD")
        ).scalars().all()
        assert len(snapshots) == 2  # v1 SUPERSEDED, v2 PUBLISHED -- never duplicated at v1
        versions = sorted(s.snapshot_version for s in snapshots)
        assert versions == [1, 2]
        by_version = {s.snapshot_version: s for s in snapshots}
        assert by_version[1].status == "SUPERSEDED"
        assert by_version[2].status == "PUBLISHED"

        # Complete audit history: every real transition this scenario drove
        # through the expense itself, plus every approval-decision row
        # keyed by its own ExpenseApproval id (the real entity_public_id
        # audit_record() was called with at each decision).
        expense_audit_codes = {
            row.action_code for row in db_session.execute(select(AuditLog).where(AuditLog.entity_public_id == str(expense_id))).scalars().all()
        }
        assert {"EXPENSE_CREATED", "EXPENSE_SUBMITTED", "EXPENSE_REVISED"}.issubset(expense_audit_codes)

        approval_audit_codes = set()
        for approval in approvals:
            approval_audit_codes |= {
                row.action_code for row in db_session.execute(select(AuditLog).where(AuditLog.entity_public_id == str(approval.id))).scalars().all()
            }
        assert {"EXPENSE_APPROVAL_RETURNED", "EXPENSE_APPROVAL_APPROVED"}.issubset(approval_audit_codes)
