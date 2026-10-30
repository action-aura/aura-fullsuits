"""2026-10-26 seller-launch phone/Arabic fixes.

Covers the parts a unit test can see: the commission-ladder button on the
employee page (who may press it, that a second press changes nothing), the
Arabic date/package-name display, the RTL sidebar rule, the z-index ordering
and the tour's seen-marker. Layout itself (393px, no horizontal scroll) was
checked in a real browser, not here.
"""
from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path

from tests.conftest import force_login, get_csrf, make_staff
from tests.test_issuance_sale import _profile

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


# ----------------------------------------------------- commission ladder ----

def _world(app):
    admin = make_staff(app, f"adm-{uuid.uuid4().hex[:6]}@example.com", super_admin=True, mfa=True)
    seller = make_staff(app, f"sel-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES"], mfa=True)
    mgr = make_staff(app, f"mgr-{uuid.uuid4().hex[:6]}@example.com", role_codes=["SALES_MANAGER"], mfa=True)
    profile = _profile(app, seller, f"EMP-L-{uuid.uuid4().hex[:4]}", "Sami Seller")
    # The SALES / SALES_MANAGER callers need a profile too so they reach the
    # permission check, not a profile lookup.
    _profile(app, mgr, f"EMP-N-{uuid.uuid4().hex[:4]}", "Mona Manager")
    with app.app_context():
        from app.commissions.ladder_setup import seed_ladder_plan

        seed_ladder_plan(actor_staff_user_id=admin)
    return admin, seller, mgr, profile


def _post(client, app, who, profile):
    force_login(client, app, who)
    with app.app_context():  # force_login never sets recent MFA; the route needs it
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.base import utcnow
        from app.models.staff import StaffSession

        for s in db_session.execute(select(StaffSession).where(StaffSession.staff_user_id == who)).scalars():
            s.mfa_verified_at = utcnow()
        db_session.commit()
    csrf = get_csrf(client.get("/profile").get_data(as_text=True))
    return client.post(f"/employees/{profile}/commission-ladder", data={"csrf_token": csrf})


def _assignments(app, profile):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.commissions import EmployeeCommissionPlanAssignment as A

        return db_session.execute(select(A).where(A.employee_profile_id == profile)).scalars().all()


def test_super_admin_assigns_ladder_and_second_press_is_a_noop(app, client, seeded):
    admin, _seller, _mgr, profile = _world(app)
    assert _assignments(app, profile) == []
    resp = _post(client, app, admin, profile)
    assert resp.status_code == 302
    # One press puts the seller on BOTH ladders (Package 1; monthly packages).
    rows = _assignments(app, profile)
    assert len(rows) == 2 and all(r.effective_until is None for r in rows)
    assert len({r.commission_plan_id for r in rows}) == 2
    # Second press: no new rows, none closed.
    assert _post(client, app, admin, profile).status_code == 302
    rows = _assignments(app, profile)
    assert len(rows) == 2 and all(r.effective_until is None for r in rows)
    page = client.get(f"/employees/{profile}").get_data(as_text=True)
    assert "commission-plan" in page and "SALES_LADDER_MONTHLY_PKGS" in page and "Already on both ladder plans." in page


def test_sales_and_sales_manager_are_refused_and_nothing_changes(app, client, seeded):
    _admin, seller, mgr, profile = _world(app)
    for who in (seller, mgr):
        assert _post(client, app, who, profile).status_code == 403
    assert _assignments(app, profile) == []


# ------------------------------------------------------ Arabic display ----

def test_arabic_date_has_no_rtl_marks_and_is_isolated(app):
    with app.test_request_context():
        from flask import session

        session["locale"] = "ar"
        from app.i18n_format import format_owner_date, format_owner_datetime

        text = format_owner_date(date(2026, 10, 26))
        assert text == "⁦26/10/2026⁩"
        assert "‏" not in text
        assert "‏" not in format_owner_datetime(__import__("datetime").datetime(2026, 10, 26, 14, 5))


def test_english_date_is_unchanged(app):
    with app.test_request_context():
        from app.i18n_format import format_owner_date

        assert "⁦" not in format_owner_date(date(2026, 10, 26))


def test_package_names_translate_in_arabic_and_unknown_names_pass_through(app):
    with app.test_request_context():
        from flask import session

        session["locale"] = "ar"
        from app.i18n_format import catalog_label

        assert catalog_label("Aura Retail - Package 1 (yearly)") == "الباقة 1 (سنوية)"
        assert catalog_label("Aura Retail - Package 2 (monthly, 12-month contract)") == "الباقة 2 (شهرية، التزام 12 شهرًا)"
        assert catalog_label("Aura Retail - Package 3 (monthly, 24-month contract)") == "الباقة 3 (شهرية، التزام 24 شهرًا)"
        assert catalog_label("Custom thing") == "Custom thing"


# ------------------------------------------------- static layout guards ----

def test_rtl_open_rule_beats_rtl_closed_rule():
    css = (STATIC / "css" / "shell.css").read_text(encoding="utf-8")
    assert re.search(r':root\[dir="rtl"\] \.aura-sidebar\.aura-sidebar--open\s*\{\s*transform:\s*translateX\(0\)', css)


def test_sticky_table_head_sits_below_every_shell_layer():
    comp = (STATIC / "css" / "components.css").read_text(encoding="utf-8")
    block = comp[comp.index("table.aura-table thead th {"):]
    block = block[: block.index("}")]
    assert re.search(r"z-index:\s*1;", block), block
    shell = (STATIC / "css" / "shell.css").read_text(encoding="utf-8")
    assert "z-index: var(--aura-z-overlay)" in shell  # off-canvas panel above sticky layers


def test_tour_is_marked_seen_on_open_not_only_on_dismiss():
    js = (STATIC / "js" / "product-tour.js").read_text(encoding="utf-8")
    assert re.search(r"markSeen\(\);\s*\n\s*open\(\);", js)
    assert "document.cookie" in js
