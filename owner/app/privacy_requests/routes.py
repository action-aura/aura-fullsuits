"""Personal-data requests -- the screens (permission privacy.requests, nothing else opens them).

  GET  /data-requests                           every request, open ones first (managers)
  POST /data-requests/customers/<customer_id>   log a request from the customer page
  GET  /data-requests/<id>/export               download the copy (ZIP) -- recent re-auth, audited
  POST /data-requests/<id>/erase                carry out the erasure -- recent re-auth, audited
  POST /data-requests/<id>/close                mark DONE / REFUSED with a written answer
"""
from __future__ import annotations

from datetime import date

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

from app.auth.session import load_current_staff
from app.customers.services import customer_visible_to_actor
from app.extensions import db_session
from app.i18n_labels import (
    data_request_channel_label, data_request_kind_label, data_request_status_label, localize_data_request_error)
from app.models.base import utcnow
from app.models.customers import Customer
from app.models.data_requests import DataRequest
from app.operational_reports.periods import OWNER_TIMEZONE
from app.privacy_requests import erase, export, service
from app.privacy_requests.errors import DataRequestError
from app.security.rbac import get_staff_permission_codes, require_permission, require_recent_auth

bp = Blueprint("data_requests", __name__, url_prefix="/data-requests")


@bp.app_context_processor
def _labels():
    """Label helpers for these screens AND the card on the customer page (which another blueprint
    renders), so they are app-wide; `jday` turns a stored instant into the Jordan calendar day."""
    return {"data_request_kind_label": data_request_kind_label, "data_request_status_label": data_request_status_label,
            "data_request_channel_label": data_request_channel_label,
            "jday": lambda moment: moment.astimezone(OWNER_TIMEZONE).date()}


def _back(customer_id):
    return redirect(url_for("customers.detail", customer_id=customer_id) + "#data-requests-card", code=303)


def _request_and_customer_or_404(request_id):
    """The request and its customer, 404 for a customer this actor may not see (same rule as every
    single-customer route: a record that is not yours answers like one that does not exist)."""
    actor = load_current_staff()
    row = db_session.get(DataRequest, request_id)
    customer = db_session.get(Customer, row.customer_id) if row is not None else None
    if customer is None or not customer_visible_to_actor(customer, actor.id, get_staff_permission_codes(actor)):
        abort(404)
    return actor, row, customer


@bp.route("", methods=["GET"])
@require_permission("privacy.requests")
def index():
    rows = service.list_requests()
    names = {c.id: c.legal_name for c in db_session.query(Customer).filter(Customer.id.in_({r.customer_id for r in rows})).all()} if rows else {}
    return render_template("data_requests/index.html", rows=rows, names=names, is_overdue=service.is_overdue)


@bp.route("/customers/<uuid:customer_id>", methods=["POST"])
@require_permission("privacy.requests")
def log_request(customer_id):
    actor = load_current_staff()
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, actor.id, get_staff_permission_codes(actor)):
        abort(404)
    raw_date = (request.form.get("received_on") or "").strip()
    try:
        received_on = date.fromisoformat(raw_date) if raw_date else None
    except ValueError:
        flash(localize_data_request_error("RECEIVED_DATE_INVALID"), "error")
        return _back(customer_id)
    try:
        row = service.log_request(
            customer_id=customer_id, kind=request.form.get("kind", ""), channel=request.form.get("channel", ""),
            received_on=received_on, actor_staff_user_id=actor.id)
    except DataRequestError as exc:
        db_session.rollback()
        flash(localize_data_request_error(exc.code, **exc.params), "error")
    else:
        flash(_("The request is logged. It must be answered by %(due)s.", due=_due_text(row)), "success")
    return _back(customer_id)


def _due_text(row: DataRequest) -> str:
    from app.i18n_format import format_owner_date

    return format_owner_date(row.due_at.astimezone(OWNER_TIMEZONE).date())


@bp.route("/<uuid:request_id>/export", methods=["GET"])
@require_permission("privacy.requests")
@require_recent_auth
def download_export(request_id):
    actor, row, customer = _request_and_customer_or_404(request_id)
    if row.kind != "EXPORT" or row.status != "OPEN":
        flash(localize_data_request_error("NOT_AN_OPEN_EXPORT"), "error")
        return _back(customer.id)
    codes = get_staff_permission_codes(actor)
    content = export.build_export(customer, actor_staff_user_id=actor.id, may_open_documents="customer_documents.view" in codes)
    if row.fulfilled_at is None:
        row.fulfilled_at = utcnow()
        db_session.commit()
    response = Response(content, mimetype="application/zip")
    response.headers["Content-Disposition"] = f'attachment; filename="customer-data-{str(customer.id)[:8]}.zip"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.route("/<uuid:request_id>/erase", methods=["POST"])
@require_permission("privacy.requests")
@require_recent_auth
def run_erase(request_id):
    actor, row, customer = _request_and_customer_or_404(request_id)
    try:
        erase.carry_out_erase(row.id, actor_staff_user_id=actor.id)
    except DataRequestError as exc:
        db_session.rollback()
        flash(localize_data_request_error(exc.code, **exc.params), "error")
    else:
        flash(_("The person was erased and the request is closed."), "success")
    return _back(customer.id)


@bp.route("/<uuid:request_id>/close", methods=["POST"])
@require_permission("privacy.requests")
def close_request(request_id):
    actor, row, customer = _request_and_customer_or_404(request_id)
    try:
        service.close_request(row.id, status=request.form.get("status", ""), note=request.form.get("answer_note", ""),
                              actor_staff_user_id=actor.id)
    except DataRequestError as exc:
        db_session.rollback()
        flash(localize_data_request_error(exc.code, **exc.params), "error")
    else:
        flash(_("The request is closed."), "success")
    return _back(customer.id)
