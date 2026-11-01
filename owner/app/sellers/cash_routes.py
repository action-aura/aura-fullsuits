"""Clause 7 screens.

  GET  /sellers/cash                        the seller's own cash to hand in           (sellers.self_service)
  POST /sellers/cash/<id>/slip              the seller uploads the bank-transfer slip  (sellers.self_service)
  GET  /sellers/cash/manage                 every seller's open / overdue cash          (payments.confirm)
  POST /sellers/cash/manage/<id>/receive    confirm the slip, or "received at the office"
  POST /sellers/cash/manage/<id>/reject     reject the slip, with a reason

A seller only ever sees and acts on THEIR OWN rows: the seller is the signed-in staff member, and a row
that is not theirs answers exactly like one that does not exist.
"""
from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.i18n_labels import localize_seller_error
from app.models.base import utcnow
from app.models.customers import Customer
from app.models.subscriptions import PaymentRecord
from app.security.rbac import require_permission
from app.sellers import cash_handin
from app.sellers.errors import SellerRulesError

bp = Blueprint("seller_cash", __name__, url_prefix="/sellers/cash")


def _row_view(row, now, *, with_seller=False, with_slip=False) -> dict:
    payment = db_session.get(PaymentRecord, row.payment_record_id)
    customer = db_session.get(Customer, payment.customer_id) if payment is not None else None
    seconds_left = (row.due_at - now).total_seconds()
    view = {
        "row": row, "customer": customer.legal_name if customer is not None else "",
        "overdue": cash_handin.is_overdue(row, now), "hours_left": max(0, int(seconds_left // 3600)),
    }
    if with_seller:
        view["seller"] = cash_handin.seller_name(row.seller_staff_user_id)
        view["seller_id"] = row.seller_staff_user_id
    if with_slip and row.slip_document_id is not None and payment is not None:
        view["slip_url"] = url_for("customer_onboarding.view_document", customer_id=payment.customer_id, document_id=row.slip_document_id)
    return view


@bp.route("", methods=["GET"])
@require_permission("sellers.self_service")
def mine():
    staff = load_current_staff()
    now = utcnow()
    rows = [_row_view(r, now) for r in cash_handin.for_seller(staff.id)]
    return render_template("sellers/cash_mine.html", rows=rows)


@bp.route("/<uuid:handin_id>/slip", methods=["POST"])
@require_permission("sellers.self_service")
def upload_slip(handin_id):
    staff = load_current_staff()
    upload = request.files.get("slip")
    content = upload.read() if upload and upload.filename else b""
    try:
        cash_handin.submit_slip(handin_id, seller=staff, content=content)
    except SellerRulesError as exc:
        flash(localize_seller_error(exc.code, **exc.params), "error")
    else:
        flash(_("The slip is uploaded. A manager will check it."), "success")
    return redirect(url_for("seller_cash.mine"), code=303)


@bp.route("/manage", methods=["GET"])
@require_permission("payments.confirm")
def manage():
    now = utcnow()
    show_closed = request.args.get("closed") == "1"
    rows = [_row_view(r, now, with_seller=True, with_slip=True) for r in cash_handin.for_managers(include_closed=show_closed)]
    return render_template("sellers/cash_manage.html", rows=rows, show_closed=show_closed)


@bp.route("/manage/<uuid:handin_id>/receive", methods=["POST"])
@require_permission("payments.confirm")
def receive(handin_id):
    manager = load_current_staff()
    try:
        cash_handin.receive(handin_id, manager=manager, at_office=request.form.get("mode") == "office")
    except SellerRulesError as exc:
        flash(localize_seller_error(exc.code, **exc.params), "error")
    else:
        flash(_("Marked as received."), "success")
    return redirect(url_for("seller_cash.manage"), code=303)


@bp.route("/manage/<uuid:handin_id>/reject", methods=["POST"])
@require_permission("payments.confirm")
def reject(handin_id):
    manager = load_current_staff()
    try:
        cash_handin.reject_slip(handin_id, manager=manager, reason=request.form.get("reason", ""))
    except SellerRulesError as exc:
        flash(localize_seller_error(exc.code, **exc.params), "error")
    else:
        flash(_("The slip is rejected. The seller must hand in the cash again."), "success")
    return redirect(url_for("seller_cash.manage"), code=303)
