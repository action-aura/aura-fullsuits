"""Phase 9.5E Milestone 16 -- real HTTP-layer tests for the web UI (Expenses/
Payees/Cash-Closing/Report-Snapshots/Management-Notes). Proves every template
actually renders (Jinja errors only surface at render time) and the full
create->submit->approve->pay PRG flow works end to end through real form
posts, not just the API."""
from __future__ import annotations

from datetime import date

from tests.conftest import force_login, get_csrf, make_staff

_employee_number_counter = iter(range(700000, 800000))


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


def _seed_category(app):
    from app.extensions import db_session
    from app.models.expenses import ExpenseCategory
    import uuid

    with app.app_context():
        cat = ExpenseCategory(category_code=f"WEBCAT-{uuid.uuid4().hex[:8]}", name="Web Test Category", is_active=True)
        db_session.add(cat)
        db_session.commit()
        return cat.id


def test_expense_list_and_new_form_render(app, client, seeded):
    staff = make_staff(app, "web1@example.com", role_codes=["SALES"])
    _make_profile(app, staff)
    force_login(client, app, staff)

    resp = client.get("/operations/expenses")
    assert resp.status_code == 200
    assert b"Expenses" in resp.data

    resp = client.get("/operations/expenses/new")
    assert resp.status_code == 200
    assert b"New expense" in resp.data


def test_full_expense_web_chain(app, client, seeded):
    staff_sales = make_staff(app, "web2a@example.com", role_codes=["SALES"])
    staff_finance = make_staff(app, "web2b@example.com", role_codes=["FINANCE"])
    _make_profile(app, staff_sales)
    _make_profile(app, staff_finance)
    category_id = _seed_category(app)

    from app.expenses.payees import create_payee
    with app.app_context():
        payee = create_payee(payee_type="EXTERNAL", display_name="Web Vendor", employee_profile_id=None, external_contact_reference=None, created_by_staff_user_id=staff_finance)
        payee_id = payee.id

    force_login(client, app, staff_sales)
    csrf = _csrf(client)
    resp = client.post(
        "/operations/expenses",
        data={"csrf_token": csrf, "category_id": str(category_id), "payee_id": str(payee_id), "amount": "42.00",
              "currency": "USD", "expense_date": "2026-08-01", "payment_method": "CASH", "description": "Web test expense"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Web test expense" in resp.data
    expense_url = resp.request.path
    expense_id = expense_url.rsplit("/", 1)[-1]

    csrf = _csrf(client)
    resp = client.post(f"/operations/expenses/{expense_id}/submit", data={"csrf_token": csrf}, follow_redirects=True)
    assert resp.status_code == 200

    force_login(client, app, staff_finance)
    csrf = _csrf(client)
    resp = client.post(
        f"/operations/expenses/{expense_id}/approval/decision",
        data={"csrf_token": csrf, "decision": "APPROVED", "approved_amount": "42.00"}, follow_redirects=True,
    )
    assert resp.status_code == 200

    from app.extensions import db_session
    from app.models.expenses import Expense
    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        assert expense.status == "APPROVED"

    resp = client.post(
        f"/operations/expenses/{expense_id}/payments",
        data={"csrf_token": csrf, "amount": "42.00", "payment_method": "CASH"}, follow_redirects=True,
    )
    assert resp.status_code == 200

    with app.app_context():
        expense = db_session.get(Expense, expense_id)
        assert expense.status == "PAID"


def test_cash_closing_web_pages_render(app, client, seeded):
    staff = make_staff(app, "web3@example.com", role_codes=["FINANCE"])
    _make_profile(app, staff)
    force_login(client, app, staff)

    resp = client.get("/operations/cash-closings")
    assert resp.status_code == 200

    resp = client.get("/operations/cash-closings/new")
    assert resp.status_code == 200

    csrf = _csrf(client)
    resp = client.post(
        "/operations/cash-closings",
        data={"csrf_token": csrf, "business_date": "2026-08-15", "currency": "USD",
              "opening_cash_override": "100.00", "opening_cash_override_reason": "first closing web test"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Expected closing cash" in resp.data


def test_report_snapshots_page_renders(app, client, seeded):
    staff = make_staff(app, "web4@example.com", role_codes=["FINANCE"])
    _make_profile(app, staff)
    force_login(client, app, staff)
    resp = client.get("/operations/report-snapshots")
    assert resp.status_code == 200


def test_management_notes_web_flow(app, client, seeded):
    staff = make_staff(app, "web5@example.com", role_codes=["FINANCE"])
    _make_profile(app, staff)
    force_login(client, app, staff)

    resp = client.get("/operations/management-notes")
    assert resp.status_code == 200

    csrf = _csrf(client)
    resp = client.post(
        "/operations/management-notes",
        data={"csrf_token": csrf, "title": "Web note", "body": "Body text", "priority": "MEDIUM", "visibility": "MANAGEMENT_ONLY"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Web note" in resp.data


def test_expense_web_idor_returns_404(app, client, seeded):
    staff_a = make_staff(app, "web6a@example.com", role_codes=["SALES"])
    staff_b = make_staff(app, "web6b@example.com", role_codes=["SALES"])
    _make_profile(app, staff_a)
    _make_profile(app, staff_b)
    category_id = _seed_category(app)

    from app.expenses.payees import create_payee
    with app.app_context():
        payee = create_payee(payee_type="EXTERNAL", display_name="V", employee_profile_id=None, external_contact_reference=None, created_by_staff_user_id=staff_a)
        payee_id = payee.id

    force_login(client, app, staff_a)
    csrf = _csrf(client)
    resp = client.post(
        "/operations/expenses",
        data={"csrf_token": csrf, "category_id": str(category_id), "payee_id": str(payee_id), "amount": "5.00",
              "currency": "USD", "expense_date": "2026-08-01", "payment_method": "CASH", "description": "private web expense"},
        follow_redirects=True,
    )
    expense_id = resp.request.path.rsplit("/", 1)[-1]

    force_login(client, app, staff_b)
    resp = client.get(f"/operations/expenses/{expense_id}")
    assert resp.status_code == 404
