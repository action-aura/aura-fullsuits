"""Phase 9.5E Milestone 12 -- Employee/Management/Finance dashboard tests.
Proves isolation, currency separation, no-widening, and agreement with the
canonical aggregation/service functions (never a second source of truth)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.conftest import force_login, get_csrf, make_staff

_employee_number_counter = iter(range(800000, 900000))


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
        cat = ExpenseCategory(category_code=f"DASH-{uuid.uuid4().hex[:8]}", name="Dashboard Test", is_active=True)
        db_session.add(cat)
        db_session.commit()
        return cat.id


def _create_expense(app, requester_staff, requester_profile, category_id, payee_id, amount, currency="USD", status="DRAFT"):
    from app.expenses.lifecycle import create_expense, submit_expense

    with app.app_context():
        expense = create_expense(
            category_id=category_id, payee_id=payee_id, amount=Decimal(amount), currency=currency,
            expense_date=date(2026, 8, 1), description="dashboard test", external_reference=None,
            payment_method="CASH", payment_reference=None, entered_by_employee_profile_id=requester_profile,
        )
        if status == "SUBMITTED":
            submit_expense(expense, actor_staff_user_id=requester_staff)
        return expense.id


def test_employee_a_cannot_see_employee_b_dashboard_metrics(app, seeded):
    staff_a, profile_a = _seed_active_employee(app, "dashA@example.com", ["SALES"])
    staff_b, profile_b = _seed_active_employee(app, "dashB@example.com", ["SALES"])
    category_id = _seed_category(app)

    from app.expenses.payees import create_payee
    with app.app_context():
        payee = create_payee(payee_type="EXTERNAL", display_name="V", employee_profile_id=None, external_contact_reference=None, created_by_staff_user_id=staff_a)
        payee_id = payee.id

    _create_expense(app, staff_a, profile_a, category_id, payee_id, "10.00", status="SUBMITTED")
    _create_expense(app, staff_a, profile_a, category_id, payee_id, "20.00", status="SUBMITTED")

    from app.operational_reports.dashboards import employee_expense_dashboard
    with app.app_context():
        dash_a = employee_expense_dashboard(profile_a)
        dash_b = employee_expense_dashboard(profile_b)

    assert dash_a["submitted"] == 2
    assert dash_b["submitted"] == 0


def test_employee_dashboard_route_requires_own_profile_no_widening(app, client, seeded):
    """A caller cannot widen the dashboard scope via a query parameter --
    there is no employee_profile_id query param accepted at all; the route
    always uses the authenticated actor's own profile."""
    staff_a, profile_a = _seed_active_employee(app, "dashC@example.com", ["SALES"])
    staff_b, profile_b = _seed_active_employee(app, "dashD@example.com", ["SALES"])
    category_id = _seed_category(app)

    from app.expenses.payees import create_payee
    with app.app_context():
        payee = create_payee(payee_type="EXTERNAL", display_name="V", employee_profile_id=None, external_contact_reference=None, created_by_staff_user_id=staff_a)
        payee_id = payee.id
    _create_expense(app, staff_a, profile_a, category_id, payee_id, "99.00", status="SUBMITTED")

    force_login(client, app, staff_b)
    # Attempt to widen scope by supplying employee_profile_id -- must be ignored.
    resp = client.get(f"/api/operations/v1/dashboards/employee-expenses?employee_profile_id={profile_a}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["submitted"] == 0, "dashboard leaked Employee A's data to Employee B via a query parameter"


def test_management_dashboard_requires_permission(app, client, seeded):
    staff = make_staff(app, "dashE@example.com", role_codes=["SALES"])
    force_login(client, app, staff)
    resp = client.get("/api/operations/v1/dashboards/management-operations")
    # SALES holds dashboard.view_own, not dashboard.view_all.
    assert resp.status_code == 403


def test_currencies_never_merge_into_one_total(app, seeded):
    staff, profile = _seed_active_employee(app, "dashF@example.com", ["FINANCE"])
    category_id = _seed_category(app)

    from app.expenses.payees import create_payee
    with app.app_context():
        payee = create_payee(payee_type="EXTERNAL", display_name="V", employee_profile_id=None, external_contact_reference=None, created_by_staff_user_id=staff)
        payee_id = payee.id

    _create_expense(app, staff, profile, category_id, payee_id, "100.00", currency="USD")
    _create_expense(app, staff, profile, category_id, payee_id, "500.00", currency="EUR")

    from app.operational_reports.dashboards import management_operational_dashboard
    with app.app_context():
        usd_dash = management_operational_dashboard("USD")
        eur_dash = management_operational_dashboard("EUR")

    usd_total = sum((Decimal(v) for v in usd_dash["expense_totals_by_category"].values()), Decimal("0"))
    eur_total = sum((Decimal(v) for v in eur_dash["expense_totals_by_category"].values()), Decimal("0"))
    assert usd_total == Decimal("100.00")
    assert eur_total == Decimal("500.00")
    # Never a blended 600 appearing under either currency.
    assert usd_total != Decimal("600.00")
    assert eur_total != Decimal("600.00")


def test_dashboard_values_match_canonical_aggregation_service(app, seeded):
    """The dashboard must never compute a second, independent version of a
    figure the aggregation service already owns -- proves they agree
    exactly, not just approximately."""
    from app.operational_reports.aggregation import paid_expenses_total
    from app.operational_reports.dashboards import management_operational_dashboard

    staff, profile = _seed_active_employee(app, "dashG@example.com", ["FINANCE"])
    with app.app_context():
        today = date.today()
        month_start = today.replace(day=1)
        direct = paid_expenses_total(month_start, today, "USD")
        dash = management_operational_dashboard("USD")
        assert dash["paid_expenses"] == str(direct)


def test_no_hidden_count_leakage_via_management_notes_metric(app, client, seeded):
    """A management-only note existing in the system must still be counted
    in the aggregate 'requiring action' figure available to management --
    but an employee without management_notes.manage cannot query it at all
    (permission-gated at the route, matching every other dashboard route)."""
    staff_fin, _ = _seed_active_employee(app, "dashH@example.com", ["FINANCE"])
    staff_sales, _ = _seed_active_employee(app, "dashI@example.com", ["SALES"])

    from app.management_notes.service import create_note
    with app.app_context():
        create_note(title="secret", body="body", category=None, priority="MEDIUM", visibility="MANAGEMENT_ONLY", assigned_employee_profile_id=None, created_by_staff_user_id=staff_fin)

    from app.operational_reports.dashboards import management_operational_dashboard
    with app.app_context():
        dash = management_operational_dashboard("USD")
    assert dash["management_notes_requiring_action"] >= 1

    force_login(client, app, staff_sales)
    resp = client.get("/api/operations/v1/dashboards/management-operations")
    assert resp.status_code == 403  # SALES cannot query this dashboard at all
