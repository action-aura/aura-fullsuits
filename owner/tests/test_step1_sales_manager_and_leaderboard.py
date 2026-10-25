"""Step 1 (2026-10-25) -- the Sales Manager role and the sales leaderboard.

The owner's rules (Aura Growth Plan, "Your answers" + Step 1):
  - sellers are employees registered in Owner (SALES, own-only data);
  - managers confirm payments -- any manager, the first one wins -- and a
    manager never confirms their own sale or a payment they recorded;
  - a Sales Manager "confirms payments, hands out leads, sees the team":
    all sellers' sales/leads/customers, every commission, the leaderboard;
    never catalog prices, no-charge licences, staff/roles, or anything only
    a Super Admin may do;
  - the leaderboard: this Jordan month and last month, per seller: sales
    counted on the ladder, ladder total, held vs payable, renewals; ranked by
    counted sales, then total. A seller sees every name and count but only
    their OWN money. No customer names anywhere on it.

Every number on the board is asserted against the ladder's own summary
(ladder_month_summary / ladder_month_overview), never against a number this
file computes itself.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from tests.conftest import force_login, get_csrf, login_and_verify_mfa, make_staff
from tests.test_issuance_sale import _profile
from tests.test_step0_ladder import _pay_month, _refund, _rows, _sell, _world

AMMAN = ZoneInfo("Asia/Amman")
SALES_MANAGER = "SALES_MANAGER"


# ------------------------------------------------------------------ setup --

def _team(app, **kwargs):
    """The ladder world (two SALES sellers, a FINANCE manager, a Super Admin)
    plus a Sales Manager with an employee profile."""
    w = _world(app, **kwargs)
    w["sm"] = make_staff(app, f"sm-{uuid.uuid4().hex[:6]}@example.com", role_codes=[SALES_MANAGER], mfa=True)
    w["sm_profile"] = _profile(app, w["sm"], f"EMP-SM-{uuid.uuid4().hex[:4]}", "Maha Manager")
    return w


def _email(app, staff_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        return db_session.get(StaffUser, staff_id).email


def _record_only(app, w, *, recorder, seller_profile=None):
    """A one-screen sale RECORDED (not confirmed): its PENDING payment waits
    for a manager. seller_profile: the seller a holder of orders.approve
    picks on the form (anyone else can only sell as themselves)."""
    with app.app_context():
        from app.commercial_sales.direct_sale import record_direct_sale
        from tests.conftest import jordan_today
        from tests.test_issuance_sale import _codes

        result = record_direct_sale(
            new_customer_legal_name=f"Shop {uuid.uuid4().hex[:6]}", plan_id=w["p1"], extra_device_pairs=0,
            amount=Decimal("250.00"), method="BANK_TRANSFER", reference=f"CLIQ-{uuid.uuid4().hex[:6]}",
            payment_date=jordan_today(), idempotency_key=str(uuid.uuid4()), actor_staff_user_id=recorder,
            actor_permission_codes=_codes(recorder), as_of=jordan_today(),
            sales_owner_employee_profile_id=seller_profile,
        )
        return result


def _order_status(app, order_id):
    with app.app_context():
        from app.extensions import db_session
        from app.models.commercial_sales import SalesOrder

        return db_session.get(SalesOrder, order_id).status


def _role_codes(app, code):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.staff import Permission, Role, RolePermission

        return set(db_session.execute(
            select(Permission.code).join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id).where(Role.code == code)
        ).scalars().all())


def _confirm_over_http(client, order_id):
    page = client.get("/licenses/issuance/sales")
    assert page.status_code == 200, page.status_code
    csrf = get_csrf(page.get_data(as_text=True))
    return client.post(f"/licenses/issuance/sales/{order_id}/confirm", data={"csrf_token": csrf})


# ------------------------------------------------------- the role, seeded --

def test_the_sales_manager_role_exists_with_its_arabic_label(app):
    from app.staff.seed_data import ROLES

    assert SALES_MANAGER in ROLES
    with app.test_request_context():
        from flask_babel import force_locale

        from app.i18n_labels import role_label

        with force_locale("ar"):
            assert role_label(SALES_MANAGER) == "مدير المبيعات"
        with force_locale("en"):
            assert role_label(SALES_MANAGER) == "Sales Manager"


def test_seed_rbac_adds_the_role_to_an_existing_install_and_running_it_again_changes_nothing(app):
    """`flask seed-rbac` is how every install gets its roles (no migration):
    an install seeded BEFORE this role existed gets it -- with exactly the
    permissions in code -- on the next run, and a further run writes
    nothing."""
    from app.staff.seed_data import ROLES

    with app.app_context():
        from sqlalchemy import delete, func, select

        from app.extensions import db_session
        from app.models.staff import Role, RolePermission
        from app.staff.services import seed_rbac

        seed_rbac()
        # an install from before Step 1: no Sales Manager role at all
        role = db_session.execute(select(Role).where(Role.code == SALES_MANAGER)).scalars().one()
        db_session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
        db_session.delete(role)
        db_session.commit()

        seed_rbac()
        assert _role_codes(app, SALES_MANAGER) == set(ROLES[SALES_MANAGER]["permissions"])
        roles_before = db_session.execute(select(func.count()).select_from(Role)).scalar_one()
        grants_before = db_session.execute(select(func.count()).select_from(RolePermission)).scalar_one()

        seed_rbac()
        assert db_session.execute(select(func.count()).select_from(Role)).scalar_one() == roles_before
        assert db_session.execute(select(func.count()).select_from(RolePermission)).scalar_one() == grants_before
        assert _role_codes(app, SALES_MANAGER) == set(ROLES[SALES_MANAGER]["permissions"])

        from app.commercial_ops.preflight import _check_role_permission_assignments

        checks = []
        assert _check_role_permission_assignments(checks) is True, [c.__dict__ for c in checks]


# What the screens behind "confirm payments / sees the team / commissions"
# actually require -- read off the routes (licensing/issuance_routes.py,
# commercial_sales/routes.py, leads/customers list scoping), not guessed.
MANAGER_MUST_HOLD = (
    "payments.view", "payments.confirm", "orders.approve", "invoices.issue",
    "leads.view_all", "leads.update_all", "leads.assign",
    "customers.view", "customers.view_all", "customers.update_all", "customers.assign",
    "commissions.view_all", "commissions.approve",
    "employees.view_all",
)

# The owner's "can NOT" list, and everything only a Super Admin may do.
MANAGER_MUST_NOT_HOLD = (
    # catalog prices and price exceptions
    "catalog.manage_products", "catalog.manage_versions", "catalog.manage_plans", "catalog.manage_addons",
    "catalog.manage_prices", "catalog.manage_entitlements", "pricing.override", "quotes.approve",
    # no-charge licences / licence secrets / licence stops
    "licenses.issue", "licenses.revoke", "licenses.suspend", "licenses.replace", "licenses.reactivate",
    # staff and roles
    "staff.view", "staff.create", "staff.update", "staff.disable", "staff.assign_roles", "staff.reset_mfa",
    "employees.create", "employees.update", "employees.suspend", "employees.terminate", "employees.assign_role",
    "employees.manage_commission_plan", "security_sessions.revoke",
    # refunds (the 30-day window is a code rule; nobody below Super Admin/Finance decides refunds)
    "refunds.create", "refunds.approve",
    # money out stays with Finance: approve != pay
    "commissions.pay", "commissions.reverse", "payments.correct",
    # system / security / commercial configuration
    "system.view", "system.manage_settings", "system.backup", "system.restore", "audit.view", "audit.export",
    "signing_keys.manage", "activation_policy.manage", "device_policy.manage", "management_notes.manage",
    "subscriptions.cancel", "subscriptions.suspend", "emergency_extensions.create",
    # maker-checker: the manager is the CHECKER of a sale, never its maker
    "orders.create", "quotes.create", "invoices.create",
)


def test_the_sales_manager_holds_what_confirming_and_seeing_the_team_need_and_nothing_super_admin_only():
    from app.staff.seed_data import PERMISSIONS, ROLES

    granted = ROLES[SALES_MANAGER]["permissions"]
    assert granted != "*"
    known = {code for code, _category, _description in PERMISSIONS}
    assert set(granted) <= known, set(granted) - known
    missing = [code for code in MANAGER_MUST_HOLD if code not in granted]
    assert missing == [], missing
    leaked = [code for code in MANAGER_MUST_NOT_HOLD if code in granted]
    assert leaked == [], leaked


def test_a_sales_manager_must_sign_in_with_mfa(app, seeded):
    staff_id = make_staff(app, "sm-mfa@example.com", role_codes=[SALES_MANAGER])
    with app.app_context():
        from app.auth.services import mfa_is_mandatory
        from app.extensions import db_session
        from app.models.staff import StaffUser

        assert mfa_is_mandatory(db_session.get(StaffUser, staff_id)) is True


# ------------------------------------------- confirming, both directions --

def test_a_sales_manager_confirms_another_sellers_sale_over_http(app, client, seeded):
    w = _team(app)
    sale = _record_only(app, w, recorder=w["seller"])
    login_and_verify_mfa(client, _email(app, w["sm"]))
    resp = _confirm_over_http(client, sale["order_id"])
    assert resp.status_code == 200, resp.get_data(as_text=True)[:1500]
    assert "AURA-" in resp.get_data(as_text=True)  # the key, shown once
    assert _order_status(app, sale["order_id"]) == "FULFILLED"
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        payment = db_session.get(PaymentRecord, sale["payment_id"])
        assert payment.status == "CONFIRMED"


def test_a_sales_manager_who_also_sells_cannot_confirm_their_own_sale(app, client, seeded):
    """A manager who sells holds SALES and SALES_MANAGER. Their own sale --
    recorded by them -- is refused, nothing is written, and another manager
    can still confirm it."""
    w = _team(app)
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.staff import Role, StaffRoleAssignment

        sales = db_session.execute(select(Role).where(Role.code == "SALES")).scalars().one()
        db_session.add(StaffRoleAssignment(staff_user_id=w["sm"], role_id=sales.id))
        db_session.commit()
    # holding orders.approve, the form makes them pick the seller: themselves
    sale = _record_only(app, w, recorder=w["sm"], seller_profile=w["sm_profile"])
    login_and_verify_mfa(client, _email(app, w["sm"]))
    resp = _confirm_over_http(client, sale["order_id"])
    assert resp.status_code == 400
    assert "You cannot confirm a payment you submitted yourself." in resp.get_data(as_text=True)
    assert _order_status(app, sale["order_id"]) == "DRAFT"
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import PaymentRecord

        assert db_session.get(PaymentRecord, sale["payment_id"]).status == "PENDING"


def test_a_sales_manager_named_as_the_seller_cannot_confirm_that_sale(app, client, seeded):
    """Recorded by someone else (the Super Admin picks the manager as the
    seller) -- still the manager's own sale: refused."""
    w = _team(app)
    sale = _record_only(app, w, recorder=w["admin"], seller_profile=w["sm_profile"])
    login_and_verify_mfa(client, _email(app, w["sm"]))
    resp = _confirm_over_http(client, sale["order_id"])
    assert resp.status_code == 400
    assert "You cannot confirm the payment for a sale you made yourself." in resp.get_data(as_text=True)
    assert _order_status(app, sale["order_id"]) == "DRAFT"


@pytest.mark.parametrize("method, path", [
    ("GET", "/staff"),
    ("GET", "/catalog/plans/new"),
    ("GET", "/employees/new"),
    ("GET", "/licenses/issuance/no-charge"),
    ("GET", "/licenses/issuance/new"),  # a manager is not a seller (maker-checker)
    ("GET", "/system/backups"),
    ("GET", "/audit"),
    ("GET", "/commission-payouts"),
    ("GET", "/refunds"),
])
def test_a_sales_manager_cannot_reach_super_admin_or_finance_only_screens(app, client, seeded, method, path):
    w = _team(app)
    force_login(client, app, w["sm"])
    resp = client.open(path, method=method)
    assert resp.status_code in (403, 404), (path, resp.status_code)
    if resp.status_code == 404:  # a 404 proves nothing about the guard: the route must exist
        pytest.fail(f"{path} does not exist -- fix the path, a 404 is not a refusal")


def test_a_sales_manager_cannot_change_a_catalog_price(app, client, seeded):
    w = _team(app)
    force_login(client, app, w["sm"])
    page = client.get("/catalog")
    assert page.status_code == 200
    csrf = get_csrf(page.get_data(as_text=True))
    assert csrf  # a real token: the refusal below is the permission, not CSRF
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import PlanPrice

        before = db_session.query(PlanPrice).count()
    resp = client.post(f"/catalog/plans/{w['p1']}/prices",
                       data={"csrf_token": csrf, "base_price": "1", "currency": "JOD", "effective_from": "2026-01-01"})
    assert resp.status_code == 403
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import PlanPrice

        assert db_session.query(PlanPrice).count() == before


def test_a_sales_manager_sees_every_sellers_customers_and_leads_a_seller_only_their_own(app, client, seeded):
    w = _team(app)
    sale = _record_only(app, w, recorder=w["seller"])
    with app.app_context():
        from app.extensions import db_session
        from app.leads.services import create_lead
        from app.models.customers import Customer
        from app.models.commercial_sales import SalesOrder

        customer_name = db_session.get(Customer, db_session.get(SalesOrder, sale["order_id"]).customer_id).legal_name
        create_lead({"organization_or_prospect_name": "Lead Of Sami"}, actor_employee_profile_id=w["seller_profile"],
                    actor_staff_user_id=w["seller"])
    force_login(client, app, w["sm"])
    assert customer_name in client.get("/customers").get_data(as_text=True)
    assert "Lead Of Sami" in client.get("/leads").get_data(as_text=True)
    other = app.test_client()
    force_login(other, app, w["seller2"])
    assert customer_name not in other.get("/customers").get_data(as_text=True)
    assert "Lead Of Sami" not in other.get("/leads").get_data(as_text=True)


def test_a_sales_manager_approves_another_sellers_commission_after_the_hold_never_their_own(app, client, seeded):
    w = _team(app)
    old = datetime.now(timezone.utc) - timedelta(days=35)
    _sell(app, w, "p1", confirmed_at=old)
    with app.app_context():
        from sqlalchemy import select

        from app.commissions.errors import CommissionError
        from app.commissions.ledger import approve_commission_entry
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        entry = db_session.execute(select(CommissionLedgerEntry).where(
            CommissionLedgerEntry.employee_profile_id == w["seller_profile"],
            CommissionLedgerEntry.status == "EARNED")).scalars().one()
        entry_id = entry.id
        # the manager's own (payable) row: approving it is refused in the service
        own = CommissionLedgerEntry(
            employee_profile_id=w["sm_profile"], commission_rule_version_id=entry.commission_rule_version_id,
            source_commercial_invoice_id=entry.source_commercial_invoice_id,
            source_payment_record_id=entry.source_payment_record_id, base_amount=Decimal("10.00"),
            rate_or_fixed_applied=Decimal("10.00"), commission_amount=Decimal("10.00"), currency="JOD",
            status="EARNED", earned_at=old, entry_kind="RENEWAL", payable_after=old,
        )
        db_session.add(own)
        db_session.commit()
        with pytest.raises(CommissionError) as refused:
            approve_commission_entry(own, actor_staff_user_id=w["sm"])
        assert refused.value.code == "COMMISSION_SELF_APPROVAL_FORBIDDEN"
    force_login(client, app, w["sm"])
    page = client.get("/commissions")
    assert page.status_code == 200
    csrf = get_csrf(page.get_data(as_text=True))
    resp = client.post(f"/commissions/{entry_id}/approve", data={"csrf_token": csrf})
    assert resp.status_code == 302
    with app.app_context():
        from app.extensions import db_session
        from app.models.commissions import CommissionLedgerEntry

        approved = db_session.get(CommissionLedgerEntry, entry_id)
        assert (approved.status, approved.approved_by_staff_user_id) == ("APPROVED", w["sm"])


# ------------------------------------------------------------ the board --

def _jordan_moment(day: date, hour=12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=AMMAN).astimezone(timezone.utc)


def _seeded_month(app, w):
    """This month: Sami sells three Package 1s and one is refunded inside
    its hold (2 counted = 50) and earns a renewal (10); Tala's Package 2 --
    sold LAST month -- counts THIS month at its third payment (1 = 20).
    Last month: Sami's one Package 1 (counted then) -- the one renewed now."""
    from app.commissions.leaderboard import board_months

    with app.app_context():
        this_month, last_month = board_months()
    w["this_month"], w["last_month"] = this_month, last_month
    last = _jordan_moment(last_month + timedelta(days=1))
    renewed = _sell(app, w, "p1", confirmed_at=last)
    first, _second, _third = (_sell(app, w, "p1")["order_id"] for _ in range(3))
    _refund(app, w, first, "250.00")
    _pay_month(app, w, _rows(app, renewed["order_id"])["subscription_id"], "100.00")
    p2 = _sell(app, w, "p2", seller="seller2", confirmed_at=last)
    sub = _rows(app, p2["order_id"])["subscription_id"]
    _pay_month(app, w, sub, "35.00", confirmed_at=last + timedelta(hours=1))
    _pay_month(app, w, sub, "35.00")
    w["customer_names"] = []
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.customers import Customer

        w["customer_names"] = [c.legal_name for c in db_session.execute(select(Customer)).scalars().all()]
    assert w["customer_names"]


MONEY_KEYS = ("total", "held", "payable", "renewals_total")


def test_the_board_numbers_are_the_ladders_own_summaries_with_a_refund_and_a_p2_counted_later(app, seeded):
    w = _team(app)
    _seeded_month(app, w)
    now = datetime.now(timezone.utc)
    with app.app_context():
        from app.commissions.ladder import ladder_month_overview, ladder_month_summary
        from app.commissions.leaderboard import leaderboard

        for month in (w["this_month"], w["last_month"]):
            board = leaderboard(month, viewer_profile_id=None, show_all_money=True, now=now)
            overview = {o["employee_name"]: o for o in ladder_month_overview(month, now=now)}
            for row in board:
                summary = ladder_month_summary(row["employee_profile_id"], month, now=now)
                for key in ("count", "renewals") + MONEY_KEYS:
                    assert row[key] == summary[key], (month, row["employee_name"], key)
                if row["employee_name"] in overview:
                    o = overview[row["employee_name"]]
                    assert (row["count"], row["total"], row["held"], row["payable"]) == (
                        o["count"], o["total"], o["held"], o["payable"])
            # every seller with ladder activity that month is on the board
            assert set(overview) <= {r["employee_name"] for r in board}

        this = {r["employee_name"]: r for r in leaderboard(w["this_month"], viewer_profile_id=None,
                                                         show_all_money=True, now=now)}
        last = {r["employee_name"]: r for r in leaderboard(w["last_month"], viewer_profile_id=None,
                                                         show_all_money=True, now=now)}
    # the facts the scenario was built to produce (read from the ladder above)
    assert (this["Sami Seller"]["count"], this["Sami Seller"]["total"], this["Sami Seller"]["renewals"]) == (
        2, Decimal("50.00"), 1)
    assert (this["Tala Seller"]["count"], this["Tala Seller"]["total"]) == (1, Decimal("20.00"))
    assert (last["Sami Seller"]["count"], last["Tala Seller"]["count"]) == (1, 0)  # the P2 did NOT count last month
    # ranked by counted sales, then total
    assert (this["Sami Seller"]["rank"], this["Tala Seller"]["rank"]) == (1, 2)
    assert (last["Sami Seller"]["rank"], last["Tala Seller"]["rank"]) == (1, 2)


def test_the_board_ranks_by_counted_sales_then_total_and_ties_share_a_rank(app, seeded):
    w = _team(app)
    with app.app_context():
        from app.commissions import leaderboard as board_module

        rows = [
            {"employee_profile_id": uuid.uuid4(), "employee_name": "A", "count": 2, "total": Decimal("50.00")},
            {"employee_profile_id": uuid.uuid4(), "employee_name": "B", "count": 3, "total": Decimal("90.00")},
            {"employee_profile_id": uuid.uuid4(), "employee_name": "C", "count": 3, "total": Decimal("100.00")},
            {"employee_profile_id": uuid.uuid4(), "employee_name": "D", "count": 2, "total": Decimal("50.00")},
            {"employee_profile_id": uuid.uuid4(), "employee_name": "E", "count": 0, "total": Decimal("0.00")},
            # more money on fewer sales (a month with a take-back elsewhere): count still wins
            {"employee_profile_id": uuid.uuid4(), "employee_name": "F", "count": 2, "total": Decimal("120.00")},
        ]
        ranked = board_module.rank(rows)
    assert [(r["employee_name"], r["rank"]) for r in ranked] == [("C", 1), ("B", 2), ("F", 3), ("A", 4), ("D", 4), ("E", 6)]


@pytest.mark.parametrize("now_utc, this_month, last_month", [
    (datetime(2026, 10, 31, 21, 30, tzinfo=timezone.utc), date(2026, 11, 1), date(2026, 10, 1)),  # 00:30 1 Nov Amman
    (datetime(2026, 10, 31, 20, 30, tzinfo=timezone.utc), date(2026, 10, 1), date(2026, 9, 1)),   # 23:30 31 Oct Amman
    (datetime(2026, 12, 31, 22, 30, tzinfo=timezone.utc), date(2027, 1, 1), date(2026, 12, 1)),   # 00:30 1 Jan Amman
])
def test_this_month_and_last_month_are_jordan_calendar_months(app, now_utc, this_month, last_month):
    with app.app_context():
        from app.commissions.leaderboard import board_months

        assert board_months(now_utc) == (this_month, last_month)


def test_a_sale_confirmed_after_midnight_in_amman_is_on_the_new_months_board(app, seeded):
    """23:30 UTC on 30 September is 02:30 on 1 October in Amman: October's
    board, not September's."""
    w = _team(app, rule_from_days_ago=400)
    confirmed = datetime(2026, 9, 30, 23, 30, tzinfo=timezone.utc)
    _sell(app, w, "p1", confirmed_at=confirmed)
    with app.app_context():
        from app.commissions.leaderboard import leaderboard

        october = {r["employee_name"]: r for r in leaderboard(date(2026, 10, 1), viewer_profile_id=None,
                                                            show_all_money=True)}
        september = {r["employee_name"]: r for r in leaderboard(date(2026, 9, 1), viewer_profile_id=None,
                                                              show_all_money=True)}
    assert october["Sami Seller"]["count"] == 1
    assert september.get("Sami Seller", {"count": 0})["count"] == 0


def test_a_seller_gets_every_name_and_count_but_only_their_own_money(app, seeded):
    w = _team(app)
    _seeded_month(app, w)
    with app.app_context():
        from app.commissions.leaderboard import leaderboard

        board = {r["employee_name"]: r for r in leaderboard(w["this_month"], viewer_profile_id=w["seller2_profile"],
                                                          show_all_money=False)}
    assert board["Sami Seller"]["count"] == 2 and board["Sami Seller"]["renewals"] == 1
    for key in MONEY_KEYS:
        assert key not in board["Sami Seller"], key  # not hidden: never sent to the page
    assert board["Tala Seller"]["total"] == Decimal("20.00")  # their own
    assert board["Tala Seller"]["is_viewer"] is True


def _board_html(app, staff_id, path="/leaderboard"):
    c = app.test_client()
    force_login(c, app, staff_id)
    resp = c.get(path)
    assert resp.status_code == 200, (path, resp.status_code)
    return resp.get_data(as_text=True)


def _row(html, name):
    """The <li> of one seller on the board."""
    match = re.search(r'<li[^>]*data-board-seller="%s"[^>]*>.*?</li>' % re.escape(name), html, re.S)
    assert match, name
    return match.group(0)


def test_the_sellers_page_shows_others_names_and_counts_and_no_money_but_their_own(app, seeded):
    w = _team(app)
    _seeded_month(app, w)
    html = _board_html(app, w["seller2"])
    sami = _row(html, "Sami Seller")
    assert 'data-board-count="2"' in sami and 'data-board-renewals="1"' in sami
    assert "data-board-total" not in sami and "data-board-held" not in sami and "data-board-payable" not in sami
    assert "50.00" not in html and "10.00" not in html  # Sami's ladder total and renewal money, nowhere
    tala = _row(html, "Tala Seller")
    assert 'data-board-total="20.00"' in tala and 'data-board-viewer="true"' in tala


def test_a_sales_manager_and_the_super_admin_see_every_sellers_money(app, seeded):
    w = _team(app)
    _seeded_month(app, w)
    for viewer in (w["sm"], w["admin"]):
        html = _board_html(app, viewer)
        sami = _row(html, "Sami Seller")
        assert 'data-board-total="50.00"' in sami and 'data-board-renewals-total="10.00"' in sami
        assert 'data-board-held="50.00"' in sami and 'data-board-payable="0.00"' in sami
        assert 'data-board-total="20.00"' in _row(html, "Tala Seller")


def test_last_months_board_is_one_click_away(app, seeded):
    w = _team(app)
    _seeded_month(app, w)
    html = _board_html(app, w["sm"], "/leaderboard?period=last")
    assert 'data-board-month="%s"' % w["last_month"].isoformat() in html
    assert 'data-board-count="1"' in _row(html, "Sami Seller")
    assert 'data-board-count="0"' in _row(html, "Tala Seller")


def test_no_customer_name_is_ever_on_the_board(app, seeded):
    w = _team(app)
    _seeded_month(app, w)
    for viewer in (w["sm"], w["seller"], w["admin"]):
        for path in ("/leaderboard", "/leaderboard?period=last"):
            html = _board_html(app, viewer, path)
            for name in w["customer_names"]:
                assert name not in html, (viewer, path, name)


def test_the_board_is_in_arabic_and_right_to_left(app, seeded):
    w = _team(app)
    _seeded_month(app, w)
    c = app.test_client()
    force_login(c, app, w["seller"])
    c.get("/locale/ar?next=/")
    html = c.get("/leaderboard").get_data(as_text=True)
    assert 'lang="ar" dir="rtl"' in html
    for arabic in ("لوحة صدارة المبيعات", "هذا الشهر", "الشهر الماضي", "المبيعات المحتسبة", "التجديدات", "أنت"):
        assert arabic in html, arabic
    for english in ("Sales leaderboard", "This month", "Last month", "Sales counted"):
        assert english not in html, english


def test_the_board_is_in_the_menu_for_who_may_see_it_and_refused_to_who_may_not(app, client, seeded):
    w = _team(app)
    for staff_id in (w["sm"], w["seller"], w["admin"]):
        assert 'href="/leaderboard"' in _board_html(app, staff_id, "/")
    for role in ("SUPPORT", "VIEWER"):
        staff_id = make_staff(app, f"no-board-{role.lower()}@example.com", role_codes=[role])
        c = app.test_client()
        force_login(c, app, staff_id)
        assert 'href="/leaderboard"' not in c.get("/").get_data(as_text=True)
        assert c.get("/leaderboard").status_code == 403
