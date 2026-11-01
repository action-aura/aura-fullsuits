"""Seller agreement clauses 16-17 -- the screens.

THE SELLER (signed in; sees ONLY their own cases -- the seller is the signed-in staff member, and a
case that is not theirs answers 404 exactly like one that does not exist):
  GET  /seller-discipline/mine                        my notices
  GET  /seller-discipline/mine/<id>                   one notice + the reply box
  POST /seller-discipline/mine/<id>/reply             the written reply (once)
  POST /seller-discipline/end-my-agreement            "End my agreement" (7 days' notice), from the profile page
THE COMPANY (sellers.discipline):
  GET  /seller-discipline                             all cases, running notices
  GET|POST /seller-discipline/new                     open a case (pick the seller, then the details)
  GET  /seller-discipline/<id>                        one case; apply / drop
  POST /seller-discipline/<id>/apply   /drop
  GET|POST /seller-discipline/end-agreement           end a seller's agreement with 7 days' notice
The seller's pages skip the ID-photo step of the sign-in gates (seller_id/gate.py) so a seller can never
be sanctioned for not replying to a notice they were unable to open.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.i18n_labels import (
    localize_discipline_error, sanction_category_label, sanction_kind_label, sanction_status_label)
from app.models.base import utcnow
from app.models.commercial_sales import SalesOrder
from app.models.seller_discipline import IMMEDIATE_CATEGORIES, SANCTION_CATEGORIES, SANCTION_KINDS, SellerTermination
from app.models.staff import StaffUser
from app.operational_reports.periods import OWNER_TIMEZONE
from app.security.rbac import require_login, require_permission
from app.seller_discipline import people, sanctions, terminations
from app.seller_discipline.errors import DisciplineError

bp = Blueprint("seller_discipline", __name__, url_prefix="/seller-discipline")

ORDERS_SHOWN = 100


@bp.context_processor
def _labels():
    """The label helpers these pages use, scoped to this blueprint (no edit to the shared jinja globals)."""
    return {"sanction_kind_label": sanction_kind_label, "sanction_status_label": sanction_status_label,
            "sanction_category_label": sanction_category_label}


def _through(case):
    """A suspension runs through the END of its date: the stored instant is 00:00 the day after."""
    return case.suspension_until - timedelta(seconds=1) if case.suspension_until else None


def _fail(exc: DisciplineError) -> None:
    flash(localize_discipline_error(exc.code, **exc.params), "error")


def _end_of_jordan_day(text: str) -> datetime | None:
    """A typed date -> the instant that day ENDS in Jordan (00:00 the next day), as UTC."""
    try:
        day = date.fromisoformat((text or "").strip())
    except ValueError:
        return None
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=OWNER_TIMEZONE).astimezone(timezone.utc)


# ------------------------------------------------------------------ the seller

@bp.route("/mine", methods=["GET"])
@require_login
def my_cases():
    staff = load_current_staff()
    return render_template("seller_discipline/my_cases.html", cases=sanctions.cases_for_seller(staff.id),
                           notice=terminations.open_notice_for(staff.id))


def _own_case_or_404(case_id):
    staff = load_current_staff()
    case = sanctions.case_by_id(case_id)
    if case is None or case.seller_staff_user_id != staff.id:
        abort(404)
    return staff, case


@bp.route("/mine/<uuid:case_id>", methods=["GET"])
@require_login
def my_case(case_id):
    _staff, case = _own_case_or_404(case_id)
    return render_template("seller_discipline/my_case.html", case=case, now=utcnow(), suspended_through=_through(case))


@bp.route("/mine/<uuid:case_id>/reply", methods=["POST"])
@require_login
def my_case_reply(case_id):
    staff, case = _own_case_or_404(case_id)
    try:
        sanctions.reply_to_case(case.id, seller=staff, text=request.form.get("reply_text", ""))
    except DisciplineError as exc:
        _fail(exc)
    else:
        flash(_("Your reply is recorded. The company will read it before deciding."), "success")
    return redirect(url_for("seller_discipline.my_case", case_id=case.id))


@bp.route("/end-my-agreement", methods=["POST"])
@require_login
def end_my_agreement():
    staff = load_current_staff()
    if request.form.get("confirm") != "yes":
        flash(_("Tick the box to confirm that you want to end your agreement."), "error")
        return redirect(url_for("profile.index"))
    try:
        row = terminations.give_notice(staff.id, actor=staff, by_seller=True, reason=request.form.get("reason", ""))
    except DisciplineError as exc:
        _fail(exc)
    else:
        flash(_("Your agreement will end on %(when)s. You can keep working until then.", when=row.effective_at.strftime("%Y-%m-%d")), "success")
    return redirect(url_for("profile.index"))


# ----------------------------------------------------------------- the company

@bp.route("", methods=["GET"])
@require_permission("sellers.discipline")
def manager_index():
    now = utcnow()
    cases = sanctions.all_cases()
    names = _names({c.seller_staff_user_id for c in cases})
    notices = list(db_session.execute(
        select(SellerTermination).order_by(SellerTermination.effective_at.desc())).scalars().all())
    names.update(_names({n.seller_staff_user_id for n in notices} - set(names)))
    return render_template("seller_discipline/manager_index.html", cases=cases, names=names, notices=notices, now=now,
                           can_decide=sanctions.can_decide_now)


def _names(ids) -> dict:
    if not ids:
        return {}
    return {sid: name for sid, name in db_session.execute(
        select(StaffUser.id, StaffUser.display_name).where(StaffUser.id.in_(ids))).all()}


@bp.route("/new", methods=["GET", "POST"])
@require_permission("sellers.discipline")
def new_case():
    actor = load_current_staff()
    picker = people.sellers_for_picker()
    seller_id = request.values.get("seller") or ""
    selected = next(((s, p) for s, p in picker if str(s.id) == seller_id), None)
    orders = []
    if selected is not None:
        orders = list(db_session.execute(
            select(SalesOrder).where(SalesOrder.created_by_employee_profile_id == selected[1].id)
            .order_by(SalesOrder.created_at.desc()).limit(ORDERS_SHOWN)).scalars().all())
    error = None
    if request.method == "POST" and selected is not None:
        form = request.form
        try:
            case = sanctions.open_case(
                seller_staff_user_id=selected[0].id, actor=actor, kind=form.get("kind", ""), category=form.get("category", "OTHER"),
                reason_text=form.get("reason_text", ""), suspension_until=_end_of_jordan_day(form.get("suspension_date", "")),
                withheld_sales_order_id=form.get("sales_order_id") or None,
                link_kind=form.get("link_kind") or None, link_id=form.get("link_id") or None)
        except DisciplineError as exc:
            error = localize_discipline_error(exc.code, **exc.params)
        else:
            flash(_("The case is open and the seller has been told."), "success")
            return redirect(url_for("seller_discipline.case_detail", case_id=case.id), code=303)
    return render_template(
        "seller_discipline/new_case.html", sellers=picker, selected=selected, orders=orders, error=error,
        kinds=SANCTION_KINDS, categories=SANCTION_CATEGORIES, immediate=IMMEDIATE_CATEGORIES, form=request.form,
        link_kind=request.values.get("link_kind", ""), link_id=request.values.get("link_id", ""),
    ), (400 if error else 200)


def _case_or_404(case_id):
    case = sanctions.case_by_id(case_id)
    if case is None:
        abort(404)
    return case


@bp.route("/<uuid:case_id>", methods=["GET"])
@require_permission("sellers.discipline")
def case_detail(case_id):
    case = _case_or_404(case_id)
    now = utcnow()
    seller = db_session.get(StaffUser, case.seller_staff_user_id)
    order = db_session.get(SalesOrder, case.withheld_sales_order_id) if case.withheld_sales_order_id else None
    return render_template("seller_discipline/case_detail.html", case=case, seller=seller, order=order, now=now,
                           suspended_through=_through(case),
                           can_decide=sanctions.can_decide_now(case, now),
                           own=case.seller_staff_user_id == load_current_staff().id)


@bp.route("/<uuid:case_id>/apply", methods=["POST"])
@require_permission("sellers.discipline")
def case_apply(case_id):
    case = _case_or_404(case_id)
    try:
        sanctions.apply_case(case.id, actor=load_current_staff(), note=request.form.get("note", ""))
    except DisciplineError as exc:
        _fail(exc)
    else:
        flash(_("The sanction is applied and the seller has been told."), "success")
    return redirect(url_for("seller_discipline.case_detail", case_id=case.id), code=303)


@bp.route("/<uuid:case_id>/drop", methods=["POST"])
@require_permission("sellers.discipline")
def case_drop(case_id):
    case = _case_or_404(case_id)
    try:
        sanctions.drop_case(case.id, actor=load_current_staff(), note=request.form.get("note", ""))
    except DisciplineError as exc:
        _fail(exc)
    else:
        flash(_("The case is dropped and the seller has been told."), "success")
    return redirect(url_for("seller_discipline.case_detail", case_id=case.id), code=303)


@bp.route("/end-agreement", methods=["GET", "POST"])
@require_permission("sellers.discipline")
def end_agreement():
    actor = load_current_staff()
    picker = people.sellers_for_picker()
    error = None
    if request.method == "POST":
        form = request.form
        if form.get("confirm") != "yes":
            error = _("Tick the box to confirm.")
        else:
            try:
                row = terminations.give_notice(
                    form.get("seller") or None, actor=actor, by_seller=False, reason=form.get("reason", ""),
                    reassign_to_staff_user_id=form.get("reassign_to") or None)
            except DisciplineError as exc:
                error = localize_discipline_error(exc.code, **exc.params)
            else:
                flash(_("The agreement will end on %(when)s. The seller has been told.", when=row.effective_at.strftime("%Y-%m-%d")), "success")
                return redirect(url_for("seller_discipline.manager_index"), code=303)
    return render_template("seller_discipline/end_agreement.html", sellers=picker, error=error, form=request.form,
                           notice_days=terminations.NOTICE_DAYS), (400 if error else 200)
