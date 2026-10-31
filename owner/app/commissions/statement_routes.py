"""Seller agreement clause 5 -- the screens.

  GET  /commissions/statements                        the seller's own closed months  (commissions.view_own)
  GET  /commissions/statements/<YYYY-MM>              one statement + the Object button / the answer
  POST /commissions/statements/<YYYY-MM>/object       the objection (reason required, 7-day window)
  GET  /commissions/objections                        the manager queue, overdue first (commission_objections.answer)
  GET  /commissions/objections/<id>                   one objection with the statement as the seller saw it
  POST /commissions/objections/<id>/answer            reply text + outcome (accepted / rejected)

A seller only ever sees THEIR OWN statement: the seller is the signed-in staff member, never a
URL parameter, so there is no id to tamper with. The manager queue is the only place other
people's statements (as snapshotted at the moment of objection) appear.
"""
from __future__ import annotations

import uuid
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.commissions import statements
from app.commissions.errors import CommissionError
from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.i18n_labels import localize_commission_error
from app.models.base import utcnow
from app.models.commissions import CommissionStatementObjection
from app.models.staff import StaffUser
from app.security.rbac import require_permission

bp = Blueprint("commission_statements", __name__, url_prefix="/commissions")


def _parse_month(text: str) -> date:
    try:
        year, month = text.split("-")
        return date(int(year), int(month), 1)
    except (ValueError, TypeError):
        abort(404)


# ------------------------------------------------------------------ the seller

@bp.route("/statements", methods=["GET"])
@require_permission("commissions.view_own")
def statements_index():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    now = utcnow()
    rows = []
    for month in (statements.closed_statement_months(profile.id, now=now) if profile is not None else []):
        objection = statements.objection_for(staff.id, month)
        rows.append({
            "month": month, "objection": objection,
            "window_open": statements.objection_window_is_open(month, now) and objection is None,
            "window_closes_at": statements.objection_window_closes_at(month),
        })
    return render_template("commissions/statements_index.html", rows=rows, has_profile=profile is not None)


@bp.route("/statements/<month_text>", methods=["GET"])
@require_permission("commissions.view_own")
def statement_detail(month_text):
    month = _parse_month(month_text)
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    now = utcnow()
    if profile is None or not statements.month_is_closed(month, now):
        abort(404)  # an open month has no statement yet
    objection = statements.objection_for(staff.id, month)
    return render_template(
        "commissions/statement.html", statement=statements.build_statement(profile.id, month, now=now), month=month,
        objection=objection, window_open=statements.objection_window_is_open(month, now) and objection is None,
        window_closes_at=statements.objection_window_closes_at(month), now=now,
    )


@bp.route("/statements/<month_text>/object", methods=["POST"])
@require_permission("commissions.view_own")
def statement_object(month_text):
    month = _parse_month(month_text)
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    if profile is None:
        abort(404)
    try:
        statements.object_to_statement(staff=staff, profile=profile, month=month, reason=request.form.get("reason", ""))
    except CommissionError as exc:
        flash(localize_commission_error(exc.code), "error")
    else:
        flash(_("Your objection is recorded. The company will reply within 2 working days."), "success")
    return redirect(url_for("commission_statements.statement_detail", month_text=month.strftime("%Y-%m")))


# ----------------------------------------------------------------- the manager

@bp.route("/objections", methods=["GET"])
@require_permission("commission_objections.answer")
def objections_queue():
    now = utcnow()
    rows = statements.list_objections()
    names = {sid: name for sid, name in db_session.execute(
        select(StaffUser.id, StaffUser.display_name).where(StaffUser.id.in_({r.staff_user_id for r in rows}))).all()} if rows else {}
    return render_template("commissions/objections_queue.html", rows=rows, names=names, now=now, overdue=statements.is_overdue)


@bp.route("/objections/<uuid:objection_id>", methods=["GET"])
@require_permission("commission_objections.answer")
def objection_detail(objection_id):
    row = db_session.get(CommissionStatementObjection, objection_id)
    if row is None:
        abort(404)
    seller = db_session.get(StaffUser, row.staff_user_id)
    return render_template(
        "commissions/objection.html", row=row, seller=seller, now=utcnow(), overdue=statements.is_overdue(row, utcnow()),
        own=row.staff_user_id == load_current_staff().id,
    )


@bp.route("/objections/<uuid:objection_id>/answer", methods=["POST"])
@require_permission("commission_objections.answer")
def objection_answer(objection_id):
    answerer = load_current_staff()
    try:
        statements.answer_objection(
            objection_id, answerer=answerer, outcome=request.form.get("outcome", ""), reply_text=request.form.get("reply_text", ""))
    except CommissionError as exc:
        flash(localize_commission_error(exc.code), "error")
    else:
        flash(_("Your reply is recorded. The seller can read it on their statement."), "success")
    return redirect(url_for("commission_statements.objection_detail", objection_id=objection_id))
