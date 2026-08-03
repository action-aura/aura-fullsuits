"""Phase 9.5E Milestone 15 -- real HTTP-layer tests for
/api/operations/v1 Expenses/Payees/Cash-Closing/Reports/Management-Notes.
Same pattern as test_phase9_5d_api_commercial_sales.py: force_login +
CSRF-token-bearing JSON POSTs against a real Flask test client."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.conftest import force_login, get_csrf, make_staff

_employee_number_counter = iter(range(500000, 600000))


def _csrf(client):
    return get_csrf(client.get("/profile").get_data(as_text=True))


def _make_profile(app, staff_id):
    from app.employees.services import activate_employee, create_employee_profile

    with app.app_context():
        profile = create_employee_profile(
            {"staff_user_id": staff_id, "employee_number": f"EMP-{next(_employee_number_counter):05d}",
             "full_name": f"Employee {staff_id}", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=staff_id,
        )
        activate_employee(profile, actor_staff_user_id=staff_id)
        return profile.id


def _seed_payee(app, creator_staff_id):
    from app.expenses.payees import create_payee

    with app.app_context():
        payee = create_payee(
            payee_type="EXTERNAL", display_name="API Test Vendor", employee_profile_id=None,
            external_contact_reference=None, created_by_staff_user_id=creator_staff_id,
        )
        return payee.id


def _seed_category(app):
    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory
    import uuid

    with app.app_context():
        cat = ExpenseCategory(category_code=f"APICAT-{uuid.uuid4().hex[:8]}", name="API Test Category", is_active=True)
        db_session.add(cat)
        db_session.commit()
        return cat.id


def test_full_expense_chain_via_http(app, client, seeded):
    staff_sales = make_staff(app, "eapi1@example.com", role_codes=["SALES"])
    staff_finance = make_staff(app, "eapi2@example.com", role_codes=["FINANCE"])
    _make_profile(app, staff_sales)
    _make_profile(app, staff_finance)
    category_id = _seed_category(app)

    force_login(client, app, staff_sales)
    csrf = _csrf(client)

    resp = client.post(
        "/api/operations/v1/expense-payees", json={"payee_type": "EXTERNAL", "display_name": "API Vendor"},
        headers={"X-CSRFToken": csrf},
    )
    # SALES doesn't hold expenses.manage_payees -- must be forbidden.
    assert resp.status_code == 403

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/expense-payees", json={"payee_type": "EXTERNAL", "display_name": "API Vendor"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    payee_id = resp.get_json()["id"]

    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": str(category_id), "payee_id": payee_id, "amount": "75.50", "currency": "USD",
              "expense_date": "2026-08-01", "description": "API test expense", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    expense_id = resp.get_json()["id"]
    assert resp.get_json()["status"] == "DRAFT"

    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/submit", json={}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "SUBMITTED"

    # requester cannot approve their own expense -- must be forbidden at
    # the permission layer before ever reaching the SoD service check
    # (SALES doesn't hold expenses.approve).
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/approval/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 403

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/approval/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert resp.get_json()["status"] == "APPROVED"
    assert resp.get_json()["approved_amount"] == "75.50"

    resp = client.post(
        f"/api/operations/v1/expenses/{expense_id}/payments", json={"amount": "75.50", "currency": "USD", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)

    resp = client.get(f"/api/operations/v1/expenses/{expense_id}")
    assert resp.get_json()["status"] == "PAID"


def test_self_approval_forbidden_even_with_finance_role_via_http(app, client, seeded):
    staff = make_staff(app, "eapi3@example.com", role_codes=["SALES", "FINANCE"])
    _make_profile(app, staff)
    category_id = _seed_category(app)

    force_login(client, app, staff)
    csrf = _csrf(client)
    resp = client.post("/api/operations/v1/expense-payees", json={"payee_type": "EXTERNAL", "display_name": "Vendor"}, headers={"X-CSRFToken": csrf})
    payee_id = resp.get_json()["id"]

    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": str(category_id), "payee_id": payee_id, "amount": "10.00", "currency": "USD",
              "expense_date": "2026-08-01", "description": "self approval test", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf},
    )
    expense_id = resp.get_json()["id"]
    client.post(f"/api/operations/v1/expenses/{expense_id}/submit", json={}, headers={"X-CSRFToken": csrf})

    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/approval/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 400, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "SELF_APPROVAL_FORBIDDEN"


def test_expense_idor_peer_cannot_read_via_api(app, client, seeded):
    staff_a = make_staff(app, "eapi4a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "eapi4b@example.com", role_codes=["SALES"])
    _make_profile(app, staff_a)
    _make_profile(app, staff_b)
    category_id = _seed_category(app)

    force_login(client, app, staff_a)
    csrf = _csrf(client)
    payee_id = _seed_payee(app, staff_a)
    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": str(category_id), "payee_id": payee_id, "amount": "10.00", "currency": "USD",
              "expense_date": "2026-08-01", "description": "private", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf},
    )
    expense_id = resp.get_json()["id"]

    force_login(client, app, staff_b)
    resp = client.get(f"/api/operations/v1/expenses/{expense_id}")
    assert resp.status_code == 404, resp.get_data(as_text=True)


def test_stale_fingerprint_returns_409_via_api(app, client, seeded):
    staff_sales = make_staff(app, "eapi5a@example.com", role_codes=["SALES"])
    staff_finance = make_staff(app, "eapi5b@example.com", role_codes=["FINANCE"])
    _make_profile(app, staff_sales)
    _make_profile(app, staff_finance)
    category_id = _seed_category(app)

    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    payee_id = _seed_payee(app, staff_sales)
    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": str(category_id), "payee_id": payee_id, "amount": "10.00", "currency": "USD",
              "expense_date": "2026-08-01", "description": "original", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf},
    )
    expense_id = resp.get_json()["id"]
    client.post(f"/api/operations/v1/expenses/{expense_id}/submit", json={}, headers={"X-CSRFToken": csrf})

    from app.extensions import db_session
    from app.models.expenses import Expense
    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        expense.description = "materially changed after submission"
        db_session.commit()

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    resp = client.post(f"/api/operations/v1/expenses/{expense_id}/approval/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 409, resp.get_data(as_text=True)
    assert resp.get_json()["error"] == "APPROVAL_STALE"


def test_overpayment_rejected_via_api(app, client, seeded):
    staff_sales = make_staff(app, "eapi6a@example.com", role_codes=["SALES"])
    staff_finance = make_staff(app, "eapi6b@example.com", role_codes=["FINANCE"])
    _make_profile(app, staff_sales)
    _make_profile(app, staff_finance)
    category_id = _seed_category(app)

    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    payee_id = _seed_payee(app, staff_sales)
    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": str(category_id), "payee_id": payee_id, "amount": "50.00", "currency": "USD",
              "expense_date": "2026-08-01", "description": "overpay test", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf},
    )
    expense_id = resp.get_json()["id"]
    client.post(f"/api/operations/v1/expenses/{expense_id}/submit", json={}, headers={"X-CSRFToken": csrf})

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    client.post(f"/api/operations/v1/expenses/{expense_id}/approval/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})

    resp = client.post(
        f"/api/operations/v1/expenses/{expense_id}/payments", json={"amount": "999.00", "currency": "USD", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "PAYMENT_EXCEEDS_OUTSTANDING"


def test_payment_idempotency_conflict_returns_409_via_api(app, client, seeded):
    staff_sales = make_staff(app, "eapi7a@example.com", role_codes=["SALES"])
    staff_finance = make_staff(app, "eapi7b@example.com", role_codes=["FINANCE"])
    _make_profile(app, staff_sales)
    _make_profile(app, staff_finance)
    category_id = _seed_category(app)

    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    payee_id = _seed_payee(app, staff_sales)
    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": str(category_id), "payee_id": payee_id, "amount": "50.00", "currency": "USD",
              "expense_date": "2026-08-01", "description": "idem test", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf},
    )
    expense_id = resp.get_json()["id"]
    client.post(f"/api/operations/v1/expenses/{expense_id}/submit", json={}, headers={"X-CSRFToken": csrf})

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    client.post(f"/api/operations/v1/expenses/{expense_id}/approval/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})

    resp1 = client.post(
        f"/api/operations/v1/expenses/{expense_id}/payments", json={"amount": "20.00", "currency": "USD", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf, "Idempotency-Key": "API-IDEM-1"},
    )
    assert resp1.status_code == 201

    resp2 = client.post(
        f"/api/operations/v1/expenses/{expense_id}/payments", json={"amount": "30.00", "currency": "USD", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf, "Idempotency-Key": "API-IDEM-1"},
    )
    assert resp2.status_code == 409
    assert resp2.get_json()["error"] == "IDEMPOTENCY_CONFLICT"


def test_attachment_upload_and_idor_protected_download_via_api(app, client, seeded):
    staff_a = make_staff(app, "eapi8a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "eapi8b@example.com", role_codes=["SALES"])
    _make_profile(app, staff_a)
    _make_profile(app, staff_b)
    category_id = _seed_category(app)

    force_login(client, app, staff_a)
    csrf = _csrf(client)
    payee_id = _seed_payee(app, staff_a)
    resp = client.post(
        "/api/operations/v1/expenses",
        json={"category_id": str(category_id), "payee_id": payee_id, "amount": "10.00", "currency": "USD",
              "expense_date": "2026-08-01", "description": "attach test", "payment_method": "CASH"},
        headers={"X-CSRFToken": csrf},
    )
    expense_id = resp.get_json()["id"]

    import io
    resp = client.post(
        f"/api/operations/v1/expenses/{expense_id}/attachments",
        data={"file": (io.BytesIO(b"%PDF-1.4\nreal pdf bytes\n"), "receipt.pdf")},
        content_type="multipart/form-data", headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    attachment_id = resp.get_json()["id"]
    assert "storage_key" not in resp.get_json()

    resp = client.get(f"/api/operations/v1/expenses/{expense_id}/attachments/{attachment_id}/download")
    assert resp.status_code == 200
    assert resp.data.startswith(b"%PDF-1.4")

    force_login(client, app, staff_b)
    resp = client.get(f"/api/operations/v1/expenses/{expense_id}/attachments/{attachment_id}/download")
    assert resp.status_code == 404


def test_cash_closing_maker_checker_via_api(app, client, seeded):
    staff = make_staff(app, "eapi9@example.com", role_codes=["FINANCE"])
    _make_profile(app, staff)

    force_login(client, app, staff)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/cash-closings",
        json={"business_date": "2026-08-10", "currency": "USD", "opening_cash_override": "100.00", "opening_cash_override_reason": "first closing"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    closing_id = resp.get_json()["id"]

    resp = client.post(
        f"/api/operations/v1/cash-closings/{closing_id}/submit",
        json={"actual_counted_cash": "100.00"}, headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    resp = client.post(f"/api/operations/v1/cash-closings/{closing_id}/decision", json={"decision": "APPROVED"}, headers={"X-CSRFToken": csrf})
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "SELF_APPROVAL_FORBIDDEN_CLOSING"


def test_report_snapshot_regenerate_requires_permission_via_api(app, client, seeded):
    staff_viewer = make_staff(app, "eapi10a@example.com", role_codes=["VIEWER"])
    force_login(client, app, staff_viewer)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/report-snapshots/regenerate",
        json={"report_type": "DAILY_OPERATIONAL_SUMMARY", "period_start": "2026-08-01", "period_end": "2026-08-01", "currency": "USD", "reason": "test"},
        headers={"X-CSRFToken": csrf},
    )
    # VIEWER holds report_snapshots.view but not report_snapshots.regenerate.
    assert resp.status_code == 403


def test_management_note_hidden_from_unauthorized_employee_via_api(app, client, seeded):
    # management_notes.manage is SUPER_ADMIN-only (Phase 9.5A's explicit,
    # documented commitment); FINANCE holds only management_notes.view.
    staff_manager = make_staff(app, "eapi11a@example.com", super_admin=True)
    staff_other = make_staff(app, "eapi11b@example.com", role_codes=["SALES"])
    _make_profile(app, staff_manager)
    _make_profile(app, staff_other)

    force_login(client, app, staff_manager)
    csrf = _csrf(client)
    resp = client.post(
        "/api/operations/v1/management-notes",
        json={"title": "Confidential", "body": "sensitive management discussion", "visibility": "MANAGEMENT_ONLY", "priority": "HIGH"},
        headers={"X-CSRFToken": csrf},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    note_id = resp.get_json()["id"]

    force_login(client, app, staff_other)
    resp = client.get(f"/api/operations/v1/management-notes/{note_id}")
    assert resp.status_code == 404

    resp = client.get("/api/operations/v1/management-notes")
    assert all(row["id"] != note_id for row in resp.get_json()["rows"])
