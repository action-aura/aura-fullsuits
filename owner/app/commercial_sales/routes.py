"""Phase 9.5D Milestone 19 -- commercial-sales web routes.

Follows app/leads/routes.py's exact conventions: CSRF via the global
csrf.init_app(app) (no per-route exemption), Post/Redirect/Get, no
destructive GET, ownership enforced on every route, stable-code errors
localized only here (never in the service layer) via
app.i18n_labels.localize_commercial_sales_error()/localize_commission_error().
Calls the exact same service functions the API (Milestone 18) uses -- no
business logic here either.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from flask import Blueprint, current_app, redirect, render_template, request, url_for

from app.auth.session import load_current_staff
from app.commercial_sales.allocation import allocate_payment, reverse_allocation, unallocated_payment_balance
from app.commercial_sales.approvals import decide_approval
from app.commercial_sales.dashboards import (
    DEFAULT_DASHBOARD_CURRENCY,
    employee_commercial_dashboard,
    finance_commercial_dashboard,
)
from app.commercial_sales.errors import CommercialSalesError
from app.commercial_sales.fulfillment import fulfill_order
from app.commercial_sales.invoices import confirmed_allocated_amount, create_invoice_from_order, issue_invoice, void_invoice
from app.commercial_sales.payments import confirm_payment, reject_payment, submit_payment
from app.commercial_sales.quotes import (
    add_quote_line,
    cancel_quote,
    create_quote,
    record_customer_decision,
    remove_quote_line,
    submit_quote,
)
from app.commercial_sales.refunds import approve_refund, confirm_refund, create_refund, refundable_balance, void_refund
from app.commercial_sales.sales_orders import cancel_order, confirm_order, create_order_from_quote
from app.commercial_sales import list_queries
from app.commissions.errors import CommissionError
from app.commissions.ledger import approve_commission_entry, approve_payout_batch, create_payout_batch, record_payout
from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.i18n_labels import localize_commercial_sales_error, localize_commission_error
from app.models.commercial_sales import (
    CommercialApproval,
    CommercialInvoice,
    CommercialInvoiceItem,
    CommercialRefund,
    PaymentAllocation,
    Quote,
    QuoteLine,
    SalesOrder,
    SalesOrderLine,
)
from app.models.commissions import CommissionLedgerEntry, CommissionPayoutBatch
from app.models.customers import Customer
from app.models.employees import EmployeeProfile
from app.models.staff import StaffUser
from app.models.subscriptions import PaymentRecord
from app.security.rbac import get_staff_permission_codes, require_any_permission, require_permission, require_recent_auth
from app.services.pagination import DEFAULT_PAGE_SIZE
from sqlalchemy import select

bp = Blueprint("commercial_sales_web", __name__)

_NO_PROFILE_MESSAGE = "This action requires a real employee profile; administrative accounts without one cannot perform it."


def _actor():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    return staff, profile


def _missing_profile(profile) -> bool:
    return profile is None


def _localized_error(exc) -> str:
    if isinstance(exc, CommissionError):
        return localize_commission_error(exc.code, **exc.params)
    return localize_commercial_sales_error(exc.code, **exc.params)


def _model_or_none(model, record_id, actor_profile, *, all_held: bool):
    row = db_session.get(model, record_id)
    if row is None:
        return None
    if not all_held:
        if actor_profile is None:
            return None
        if row.created_by_employee_profile_id != actor_profile.id:
            return None
    return row


def _own_or_all(codes, own_code, all_code):
    return all_code in codes


# UI modernization Stage D -- actor/audit-context resolution, mirroring
# customers/routes.py::detail's exact `db_session.get(StaffUser, ...)`
# pattern for "Assigned employee". Quote/SalesOrder/CommercialInvoice/
# CommercialRefund's own creator column is an EmployeeProfile id (not a
# StaffUser id directly) -- EmployeeProfile.full_name is real and already
# nullable=False, so no second join to StaffUser is needed for it.
def _employee_profile_name(profile_id):
    if profile_id is None:
        return None
    profile = db_session.get(EmployeeProfile, profile_id)
    return profile.full_name if profile is not None else None


def _staff_display_name(staff_user_id):
    if staff_user_id is None:
        return None
    staff = db_session.get(StaffUser, staff_user_id)
    return staff.display_name if staff is not None else None


# --------------------------------------------------------------- Quotes --

@bp.route("/quotes", methods=["GET"])
@require_any_permission("quotes.create", "quotes.approve")
def list_quotes():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_quotes(
        page=request.args.get("page", 1, type=int), page_size=DEFAULT_PAGE_SIZE,
        status=status_filter, search=search, sort=sort, direction=direction,
        actor_employee_profile_id=profile.id if profile else None,
        all_permission_held=_own_or_all(codes, "quotes.create", "quotes.approve"),
    )
    return render_template("commercial_sales/quotes_list.html", result=result, status_filter=status_filter, search=search)


@bp.route("/quotes/new", methods=["GET"])
@require_permission("quotes.create")
def new_quote_form():
    customers = db_session.execute(select(Customer).order_by(Customer.legal_name).limit(200)).scalars().all()
    return render_template("commercial_sales/quote_new.html", customers=customers, error=None)


@bp.route("/quotes", methods=["POST"])
@require_permission("quotes.create")
def create_quote_route():
    staff, profile = _actor()
    if _missing_profile(profile):
        return render_template("commercial_sales/quote_new.html", customers=[], error=_NO_PROFILE_MESSAGE), 400
    fields = {
        "customer_id": request.form.get("customer_id") or None,
        "currency": request.form.get("currency", "USD"),
        "notes": request.form.get("notes") or None,
    }
    try:
        quote = create_quote(fields, actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id)
    except CommercialSalesError as exc:
        customers = db_session.execute(select(Customer).order_by(Customer.legal_name).limit(200)).scalars().all()
        return render_template("commercial_sales/quote_new.html", customers=customers, error=_localized_error(exc)), 400
    return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote.id))


@bp.route("/quotes/<uuid:quote_id>", methods=["GET"])
@require_any_permission("quotes.create", "quotes.approve")
def quote_detail(quote_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_none(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return render_template("commercial_sales/not_found.html"), 404
    lines = db_session.execute(select(QuoteLine).where(QuoteLine.quote_id == quote.id).order_by(QuoteLine.sort_order)).scalars().all()
    approvals = []
    if lines:
        approvals = db_session.execute(
            select(CommercialApproval).where(
                CommercialApproval.target_type == "QUOTE_LINE", CommercialApproval.target_id.in_([l.id for l in lines])
            )
        ).scalars().all()
    error_code = request.args.get("error")
    error_text = localize_commercial_sales_error(error_code) if error_code else None
    from app.catalog.services import read_active_catalog

    # UI modernization Stage D -- real downstream relationship (Quote -> the
    # SalesOrder created from it, if any): SalesOrder.quote_id is a real FK,
    # not previously surfaced anywhere on this page.
    order = db_session.execute(
        select(SalesOrder).where(SalesOrder.quote_id == quote.id, SalesOrder.status != "CANCELLED")
    ).scalars().first()
    # Real upstream relationship (Quote -> Customer, when customer-based
    # rather than lead-based -- Quote.customer_id is nullable per the
    # Milestone 7 lead-quote-customer boundary, see app/models/
    # commercial_sales.py's own comment on this column).
    customer = db_session.get(Customer, quote.customer_id) if quote.customer_id else None

    return render_template(
        "commercial_sales/quote_detail.html", quote=quote, lines=lines, approvals=approvals, order=order, customer=customer,
        plans=read_active_catalog(), can_approve=("quotes.approve" in codes), error=error_text,
        created_by_name=_employee_profile_name(quote.created_by_employee_profile_id),
    )


@bp.route("/quotes/<uuid:quote_id>/lines", methods=["POST"])
@require_any_permission("quotes.create", "quotes.approve")
def add_quote_line_route(quote_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_none(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return render_template("commercial_sales/not_found.html"), 404
    override_price = request.form.get("override_unit_price") or None
    try:
        add_quote_line(
            quote,
            plan_id=uuid.UUID(request.form["plan_id"]) if request.form.get("plan_id") else None,
            addon_id=uuid.UUID(request.form["addon_id"]) if request.form.get("addon_id") else None,
            quantity=request.form.get("quantity", 1, type=int),
            override_unit_price=Decimal(str(override_price)) if override_price else None,
            override_reason=request.form.get("override_reason") or None,
            discount_amount=Decimal(str(request.form.get("discount_amount") or "0")),
            actor_staff_user_id=staff.id,
        )
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id))


@bp.route("/quotes/<uuid:quote_id>/lines/<uuid:line_id>/remove", methods=["POST"])
@require_any_permission("quotes.create", "quotes.approve")
def remove_quote_line_route(quote_id, line_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_none(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return render_template("commercial_sales/not_found.html"), 404
    line = db_session.get(QuoteLine, line_id)
    if line is not None and line.quote_id == quote.id:
        try:
            remove_quote_line(quote, line, actor_staff_user_id=staff.id)
        except CommercialSalesError as exc:
            return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id))


@bp.route("/quotes/<uuid:quote_id>/submit", methods=["POST"])
@require_any_permission("quotes.create", "quotes.approve")
def submit_quote_route(quote_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_none(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        submit_quote(quote, actor_staff_user_id=staff.id, expected_version=request.form.get("version", type=int))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id))


@bp.route("/quotes/<uuid:quote_id>/cancel", methods=["POST"])
@require_any_permission("quotes.create", "quotes.approve")
def cancel_quote_route(quote_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_none(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        cancel_quote(quote, reason=request.form.get("reason", ""), actor_staff_user_id=staff.id, expected_version=request.form.get("version", type=int))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id))


@bp.route("/quotes/<uuid:quote_id>/decision", methods=["POST"])
@require_any_permission("quotes.create", "quotes.approve")
def quote_decision_route(quote_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_none(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return render_template("commercial_sales/not_found.html"), 404
    accepted = request.form.get("accepted") == "1"
    try:
        record_customer_decision(
            quote, accepted=accepted, reason=request.form.get("reason") or None,
            actor_staff_user_id=staff.id, expected_version=request.form.get("version", type=int),
        )
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id))


@bp.route("/approvals/<uuid:approval_id>/decide", methods=["POST"])
@require_any_permission("quotes.approve", "pricing.override")
def decide_approval_route(approval_id):
    staff, _profile = _actor()
    approval = db_session.get(CommercialApproval, approval_id)
    if approval is None:
        return render_template("commercial_sales/not_found.html"), 404
    approved = request.form.get("approved") == "1"
    line = db_session.get(QuoteLine, approval.target_id) if approval.target_type == "QUOTE_LINE" else None
    try:
        decide_approval(approval, approved=approved, decision_reason=request.form.get("decision_reason") or None, decided_by_staff_user_id=staff.id)
    except CommercialSalesError as exc:
        if line is not None:
            return redirect(url_for("commercial_sales_web.quote_detail", quote_id=line.quote_id, error=exc.code))
        return redirect(request.referrer or url_for("commercial_sales_web.list_quotes"))
    if line is not None:
        return redirect(url_for("commercial_sales_web.quote_detail", quote_id=line.quote_id))
    return redirect(request.referrer or url_for("commercial_sales_web.list_quotes"))


# ----------------------------------------------------------------- Orders --

@bp.route("/orders", methods=["GET"])
@require_any_permission("orders.create", "orders.approve")
def list_orders():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_orders(
        page=request.args.get("page", 1, type=int), page_size=DEFAULT_PAGE_SIZE,
        status=status_filter, search=search, sort=sort, direction=direction,
        actor_employee_profile_id=profile.id if profile else None,
        all_permission_held=_own_or_all(codes, "orders.create", "orders.approve"),
    )
    return render_template("commercial_sales/orders_list.html", result=result, status_filter=status_filter, search=search)


@bp.route("/orders/from-quote/<uuid:quote_id>", methods=["POST"])
@require_permission("orders.create")
def create_order_route(quote_id):
    staff, profile = _actor()
    if _missing_profile(profile):
        return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id, error="EMPLOYEE_PROFILE_REQUIRED"))
    quote = _model_or_none(Quote, quote_id, profile, all_held=False)
    if quote is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        order = create_order_from_quote(quote, actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id, idempotency_key=str(uuid.uuid4()))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.quote_detail", quote_id=quote_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.order_detail", order_id=order.id))


@bp.route("/orders/<uuid:order_id>", methods=["GET"])
@require_any_permission("orders.create", "orders.approve")
def order_detail(order_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    order = _model_or_none(SalesOrder, order_id, profile, all_held=_own_or_all(codes, "orders.create", "orders.approve"))
    if order is None:
        return render_template("commercial_sales/not_found.html"), 404
    lines = db_session.execute(select(SalesOrderLine).where(SalesOrderLine.sales_order_id == order.id).order_by(SalesOrderLine.sort_order)).scalars().all()
    invoice = db_session.execute(select(CommercialInvoice).where(CommercialInvoice.sales_order_id == order.id, CommercialInvoice.status != "VOID")).scalars().first()
    quote = db_session.get(Quote, order.quote_id) if order.quote_id else None
    customer = db_session.get(Customer, order.customer_id)
    error_code = request.args.get("error")
    error_text = localize_commercial_sales_error(error_code) if error_code else None
    return render_template(
        "commercial_sales/order_detail.html", order=order, lines=lines, invoice=invoice, quote=quote, customer=customer,
        can_approve=("orders.approve" in codes), error=error_text,
        created_by_name=_employee_profile_name(order.created_by_employee_profile_id),
    )


@bp.route("/orders/<uuid:order_id>/confirm", methods=["POST"])
@require_permission("orders.approve")
def confirm_order_route(order_id):
    staff, _profile = _actor()
    order = db_session.get(SalesOrder, order_id)
    if order is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        confirm_order(order, actor_staff_user_id=staff.id, expected_version=request.form.get("version", type=int))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.order_detail", order_id=order_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.order_detail", order_id=order_id))


@bp.route("/orders/<uuid:order_id>/cancel", methods=["POST"])
@require_permission("orders.approve")
def cancel_order_route(order_id):
    staff, _profile = _actor()
    order = db_session.get(SalesOrder, order_id)
    if order is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        cancel_order(order, reason=request.form.get("reason", ""), actor_staff_user_id=staff.id, expected_version=request.form.get("version", type=int))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.order_detail", order_id=order_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.order_detail", order_id=order_id))


@bp.route("/orders/<uuid:order_id>/fulfill", methods=["POST"])
@require_permission("orders.approve")
def fulfill_order_route(order_id):
    staff, _profile = _actor()
    order = db_session.get(SalesOrder, order_id)
    if order is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        fulfill_order(order, actor_staff_user_id=staff.id, license_pepper=current_app.config["LICENSE_PEPPER"], idempotency_key=str(uuid.uuid4()))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.order_detail", order_id=order_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.order_detail", order_id=order_id))


# -------------------------------------------------------------- Invoices --

@bp.route("/invoices", methods=["GET"])
@require_any_permission("invoices.create", "invoices.issue")
def list_invoices():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_invoices(
        page=request.args.get("page", 1, type=int), page_size=DEFAULT_PAGE_SIZE,
        status=status_filter, search=search, sort=sort, direction=direction,
        actor_employee_profile_id=profile.id if profile else None,
        all_permission_held=_own_or_all(codes, "invoices.create", "invoices.issue"),
    )
    return render_template("commercial_sales/invoices_list.html", result=result, status_filter=status_filter, search=search)


@bp.route("/orders/<uuid:order_id>/invoice", methods=["POST"])
@require_permission("invoices.create")
def create_invoice_route(order_id):
    staff, profile = _actor()
    if _missing_profile(profile):
        return redirect(url_for("commercial_sales_web.order_detail", order_id=order_id, error="EMPLOYEE_PROFILE_REQUIRED"))
    order = _model_or_none(SalesOrder, order_id, profile, all_held=False)
    if order is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        invoice = create_invoice_from_order(order, actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id, idempotency_key=str(uuid.uuid4()))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.order_detail", order_id=order_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice.id))


@bp.route("/invoices/<uuid:invoice_id>", methods=["GET"])
@require_any_permission("invoices.create", "invoices.issue")
def invoice_detail(invoice_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    invoice = _model_or_none(CommercialInvoice, invoice_id, profile, all_held=_own_or_all(codes, "invoices.create", "invoices.issue"))
    if invoice is None:
        return render_template("commercial_sales/not_found.html"), 404
    lines = db_session.execute(select(CommercialInvoiceItem).where(CommercialInvoiceItem.commercial_invoice_id == invoice.id).order_by(CommercialInvoiceItem.sort_order)).scalars().all()
    allocations = db_session.execute(select(PaymentAllocation).where(PaymentAllocation.commercial_invoice_id == invoice.id).order_by(PaymentAllocation.allocated_at.desc())).scalars().all()
    refunds = db_session.execute(select(CommercialRefund).where(CommercialRefund.commercial_invoice_id == invoice.id).order_by(CommercialRefund.created_at.desc())).scalars().all()
    order = db_session.get(SalesOrder, invoice.sales_order_id) if invoice.sales_order_id else None
    customer = db_session.get(Customer, invoice.customer_id)
    error_code = request.args.get("error")
    error_text = localize_commercial_sales_error(error_code) if error_code else None
    return render_template(
        "commercial_sales/invoice_detail.html", invoice=invoice, lines=lines, allocations=allocations, refunds=refunds, order=order, customer=customer,
        collected=confirmed_allocated_amount(invoice), refundable=refundable_balance(invoice),
        can_issue=("invoices.issue" in codes), error=error_text,
        created_by_name=_employee_profile_name(invoice.created_by_employee_profile_id),
    )


@bp.route("/invoices/<uuid:invoice_id>/issue", methods=["POST"])
@require_permission("invoices.issue")
def issue_invoice_route(invoice_id):
    staff, _profile = _actor()
    invoice = db_session.get(CommercialInvoice, invoice_id)
    if invoice is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        issue_invoice(invoice, actor_staff_user_id=staff.id, expected_version=request.form.get("version", type=int))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id))


@bp.route("/invoices/<uuid:invoice_id>/void", methods=["POST"])
@require_permission("invoices.issue")
@require_recent_auth
def void_invoice_route(invoice_id):
    staff, _profile = _actor()
    invoice = db_session.get(CommercialInvoice, invoice_id)
    if invoice is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        void_invoice(invoice, reason=request.form.get("reason", ""), actor_staff_user_id=staff.id, expected_version=request.form.get("version", type=int))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id))


@bp.route("/invoices/<uuid:invoice_id>/allocate", methods=["POST"])
@require_permission("payments.confirm")
def allocate_payment_route(invoice_id):
    staff, _profile = _actor()
    invoice = db_session.get(CommercialInvoice, invoice_id)
    if invoice is None:
        return render_template("commercial_sales/not_found.html"), 404
    payment = db_session.get(PaymentRecord, uuid.UUID(request.form["payment_record_id"])) if request.form.get("payment_record_id") else None
    if payment is None:
        return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id, error="RECORD_NOT_FOUND"))
    try:
        allocate_payment(payment=payment, invoice=invoice, amount=Decimal(str(request.form["amount"])), actor_staff_user_id=staff.id)
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id))


@bp.route("/allocations/<uuid:allocation_id>/reverse", methods=["POST"])
@require_permission("payments.confirm")
def reverse_allocation_route(allocation_id):
    staff, _profile = _actor()
    allocation = db_session.get(PaymentAllocation, allocation_id)
    if allocation is None:
        return render_template("commercial_sales/not_found.html"), 404
    invoice_id = allocation.commercial_invoice_id
    try:
        reverse_allocation(allocation, reason=request.form.get("reason", ""), actor_staff_user_id=staff.id)
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id))


# -------------------------------------------------------------- Payments --

@bp.route("/payments", methods=["GET"])
@require_permission("payments.view")
def list_payments():
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_payments(
        page=request.args.get("page", 1, type=int), page_size=DEFAULT_PAGE_SIZE,
        status=status_filter, search=search, sort=sort, direction=direction,
    )
    return render_template("commercial_sales/payments_list.html", result=result, status_filter=status_filter, search=search)


@bp.route("/payments/new", methods=["GET"])
@require_permission("payments.create")
def new_payment_form():
    customers = db_session.execute(select(Customer).order_by(Customer.legal_name).limit(200)).scalars().all()
    return render_template("commercial_sales/payment_new.html", customers=customers, error=None)


@bp.route("/payments", methods=["POST"])
@require_permission("payments.create")
def submit_payment_route():
    staff, _profile = _actor()
    try:
        payment = submit_payment(
            customer_id=uuid.UUID(request.form["customer_id"]), amount=Decimal(str(request.form["amount"])),
            currency=request.form.get("currency", "USD"), method=request.form.get("method", "CASH"),
            payment_date=date.fromisoformat(request.form["payment_date"]),
            commercial_invoice_id=uuid.UUID(request.form["commercial_invoice_id"]) if request.form.get("commercial_invoice_id") else None,
            reference=request.form.get("reference") or None, actor_staff_user_id=staff.id,
        )
    except CommercialSalesError as exc:
        customers = db_session.execute(select(Customer).order_by(Customer.legal_name).limit(200)).scalars().all()
        return render_template("commercial_sales/payment_new.html", customers=customers, error=_localized_error(exc)), 400
    return redirect(url_for("commercial_sales_web.payment_detail", payment_id=payment.id))


@bp.route("/payments/<uuid:payment_id>", methods=["GET"])
@require_permission("payments.view")
def payment_detail(payment_id):
    payment = db_session.get(PaymentRecord, payment_id)
    if payment is None:
        return render_template("commercial_sales/not_found.html"), 404
    unallocated = unallocated_payment_balance(payment) if payment.status == "CONFIRMED" else None
    # UI modernization Stage D -- real downstream relationship (Payment ->
    # the Invoice(s) it funds via PaymentAllocation): verified real FK,
    # not previously surfaced anywhere on this page.
    allocations = db_session.execute(
        select(PaymentAllocation).where(PaymentAllocation.payment_record_id == payment.id).order_by(PaymentAllocation.allocated_at.desc())
    ).scalars().all()
    invoices_by_id = {}
    if allocations:
        invoice_ids = {a.commercial_invoice_id for a in allocations}
        invoices_by_id = {
            inv.id: inv
            for inv in db_session.execute(select(CommercialInvoice).where(CommercialInvoice.id.in_(invoice_ids))).scalars().all()
        }
    customer = db_session.get(Customer, payment.customer_id)
    error_code = request.args.get("error")
    error_text = localize_commercial_sales_error(error_code) if error_code else None
    return render_template(
        "commercial_sales/payment_detail.html", payment=payment, unallocated=unallocated, error=error_text,
        allocations=allocations, invoices_by_id=invoices_by_id, customer=customer,
        recorded_by_name=_staff_display_name(payment.recorded_by_staff_user_id),
        verified_by_name=_staff_display_name(payment.verified_by_staff_user_id),
    )


@bp.route("/payments/<uuid:payment_id>/confirm", methods=["POST"])
@require_permission("payments.confirm")
def confirm_payment_route(payment_id):
    staff, _profile = _actor()
    payment = db_session.get(PaymentRecord, payment_id)
    if payment is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        confirm_payment(payment, actor_staff_user_id=staff.id, note=request.form.get("note") or None)
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.payment_detail", payment_id=payment_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.payment_detail", payment_id=payment_id))


@bp.route("/payments/<uuid:payment_id>/reject", methods=["POST"])
@require_permission("payments.confirm")
def reject_payment_route(payment_id):
    staff, _profile = _actor()
    payment = db_session.get(PaymentRecord, payment_id)
    if payment is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        reject_payment(payment, reason=request.form.get("reason", ""), actor_staff_user_id=staff.id)
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.payment_detail", payment_id=payment_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.payment_detail", payment_id=payment_id))


# ---------------------------------------------------------------- Refunds --

@bp.route("/refunds", methods=["GET"])
@require_any_permission("refunds.create", "refunds.approve")
def list_refunds():
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_refunds(
        page=request.args.get("page", 1, type=int), page_size=DEFAULT_PAGE_SIZE,
        status=status_filter, search=search, sort=sort, direction=direction,
    )
    return render_template("commercial_sales/refunds_list.html", result=result, status_filter=status_filter, search=search)


@bp.route("/invoices/<uuid:invoice_id>/refunds/new", methods=["POST"])
@require_permission("refunds.create")
def create_refund_route(invoice_id):
    staff, profile = _actor()
    if _missing_profile(profile):
        return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id, error="EMPLOYEE_PROFILE_REQUIRED"))
    invoice = db_session.get(CommercialInvoice, invoice_id)
    if invoice is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        refund = create_refund(
            invoice, amount=Decimal(str(request.form["amount"])), reason=request.form.get("reason", ""),
            payment_record_id=uuid.UUID(request.form["payment_record_id"]) if request.form.get("payment_record_id") else None,
            actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id,
        )
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.invoice_detail", invoice_id=invoice_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.refund_detail", refund_id=refund.id))


@bp.route("/refunds/<uuid:refund_id>", methods=["GET"])
@require_any_permission("refunds.create", "refunds.approve")
def refund_detail(refund_id):
    staff, _profile = _actor()
    codes = get_staff_permission_codes(staff)
    refund = db_session.get(CommercialRefund, refund_id)
    if refund is None:
        return render_template("commercial_sales/not_found.html"), 404
    # UI modernization Stage D -- real upstream relationships (Refund's own
    # real FKs, not previously surfaced anywhere on this page): the
    # originating Invoice (required) and, when set, the source Payment.
    invoice = db_session.get(CommercialInvoice, refund.commercial_invoice_id)
    payment = db_session.get(PaymentRecord, refund.payment_record_id) if refund.payment_record_id else None
    customer = db_session.get(Customer, invoice.customer_id) if invoice is not None else None
    error_code = request.args.get("error")
    error_text = localize_commercial_sales_error(error_code) if error_code else None
    return render_template(
        "commercial_sales/refund_detail.html", refund=refund, can_approve=("refunds.approve" in codes), error=error_text,
        invoice=invoice, payment=payment, customer=customer,
        created_by_name=_employee_profile_name(refund.created_by_employee_profile_id),
        approved_by_name=_staff_display_name(refund.approved_by_staff_user_id),
    )


@bp.route("/refunds/<uuid:refund_id>/approve", methods=["POST"])
@require_permission("refunds.approve")
def approve_refund_route(refund_id):
    staff, _profile = _actor()
    refund = db_session.get(CommercialRefund, refund_id)
    if refund is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        approve_refund(refund, actor_staff_user_id=staff.id, expected_version=request.form.get("version", type=int))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.refund_detail", refund_id=refund_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.refund_detail", refund_id=refund_id))


@bp.route("/refunds/<uuid:refund_id>/confirm", methods=["POST"])
@require_permission("refunds.approve")
def confirm_refund_route(refund_id):
    staff, _profile = _actor()
    refund = db_session.get(CommercialRefund, refund_id)
    if refund is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        confirm_refund(refund, actor_staff_user_id=staff.id, expected_version=request.form.get("version", type=int))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.refund_detail", refund_id=refund_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.refund_detail", refund_id=refund_id))


@bp.route("/refunds/<uuid:refund_id>/void", methods=["POST"])
@require_permission("refunds.approve")
def void_refund_route(refund_id):
    staff, _profile = _actor()
    refund = db_session.get(CommercialRefund, refund_id)
    if refund is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        void_refund(refund, actor_staff_user_id=staff.id, expected_version=request.form.get("version", type=int))
    except CommercialSalesError as exc:
        return redirect(url_for("commercial_sales_web.refund_detail", refund_id=refund_id, error=exc.code))
    return redirect(url_for("commercial_sales_web.refund_detail", refund_id=refund_id))


# ------------------------------------------------------------ Commissions --

@bp.route("/commissions", methods=["GET"])
@require_any_permission("commissions.view_own", "commissions.view_all")
def list_commissions():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    status_filter = request.args.get("status") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_commissions(
        page=request.args.get("page", 1, type=int), page_size=DEFAULT_PAGE_SIZE,
        status=status_filter, sort=sort, direction=direction,
        employee_profile_id=profile.id if profile else None, view_all=("commissions.view_all" in codes),
    )
    error_code = request.args.get("error")
    error_text = localize_commission_error(error_code) if error_code else None
    return render_template("commercial_sales/commissions_list.html", result=result, status_filter=status_filter, can_approve=("commissions.approve" in codes), error=error_text)


@bp.route("/commissions/<uuid:entry_id>/approve", methods=["POST"])
@require_permission("commissions.approve")
def approve_commission_route(entry_id):
    staff, _profile = _actor()
    entry = db_session.get(CommissionLedgerEntry, entry_id)
    if entry is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        approve_commission_entry(entry, actor_staff_user_id=staff.id)
    except CommissionError as exc:
        return redirect(url_for("commercial_sales_web.list_commissions", error=exc.code))
    return redirect(url_for("commercial_sales_web.list_commissions"))


@bp.route("/commission-payouts", methods=["GET"])
@require_permission("commissions.pay")
def list_payout_batches():
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    result = list_queries.list_payout_batches(
        page=request.args.get("page", 1, type=int), page_size=DEFAULT_PAGE_SIZE,
        search=search, sort=sort, direction=direction,
    )
    # Secondary, bounded embedded lists (the "pay via <batch>" form on this
    # same page) -- deliberately NOT put behind their own ?page= (would
    # need a second, separately-namespaced pagination control on one
    # page); kept at the pre-existing unbounded (200-row-capped) query
    # shape, same scoped-out reasoning enterprise-table-system.md used for
    # customer_360's bounded-not-paginated tabs. approved_batches is
    # deliberately a SEPARATE query from the paginated `result.rows`
    # above (not filtered from it): an APPROVED batch beyond page 1 of
    # the main table must still be payable from this same page, matching
    # the exact real behavior the pre-pagination unconditional
    # `.limit(200)` query already gave every batch, not a regression
    # introduced by adding real pagination to the primary list.
    approved_batches = db_session.execute(
        select(CommissionPayoutBatch).where(CommissionPayoutBatch.status == "APPROVED").order_by(CommissionPayoutBatch.created_at.desc()).limit(200)
    ).scalars().all()
    approved_entries = db_session.execute(select(CommissionLedgerEntry).where(CommissionLedgerEntry.status == "APPROVED").limit(200)).scalars().all()
    error_code = request.args.get("error")
    error_text = localize_commission_error(error_code) if error_code else None
    return render_template(
        "commercial_sales/payout_batches_list.html", result=result, approved_entries=approved_entries,
        approved_batches=approved_batches, search=search, error=error_text,
    )


@bp.route("/commission-payouts", methods=["POST"])
@require_permission("commissions.pay")
def create_payout_batch_route():
    staff, _profile = _actor()
    try:
        create_payout_batch(
            {
                "batch_reference": request.form.get("batch_reference", ""),
                "period_start": date.fromisoformat(request.form["period_start"]),
                "period_end": date.fromisoformat(request.form["period_end"]),
            },
            staff.id,
        )
    except CommissionError as exc:
        return redirect(url_for("commercial_sales_web.list_payout_batches", error=exc.code))
    return redirect(url_for("commercial_sales_web.list_payout_batches"))


@bp.route("/commission-payouts/<uuid:batch_id>/approve", methods=["POST"])
@require_permission("commissions.pay")
def approve_payout_batch_route(batch_id):
    staff, _profile = _actor()
    batch = db_session.get(CommissionPayoutBatch, batch_id)
    if batch is not None:
        try:
            approve_payout_batch(batch, actor_staff_user_id=staff.id)
        except CommissionError as exc:
            return redirect(url_for("commercial_sales_web.list_payout_batches", error=exc.code))
    return redirect(url_for("commercial_sales_web.list_payout_batches"))


@bp.route("/commission-payouts/<uuid:batch_id>/pay/<uuid:entry_id>", methods=["POST"])
@require_permission("commissions.pay")
def record_payout_route(batch_id, entry_id):
    staff, _profile = _actor()
    batch = db_session.get(CommissionPayoutBatch, batch_id)
    entry = db_session.get(CommissionLedgerEntry, entry_id)
    if batch is not None and entry is not None:
        try:
            record_payout(entry, batch, actor_staff_user_id=staff.id)
        except CommissionError as exc:
            return redirect(url_for("commercial_sales_web.list_payout_batches", error=exc.code))
    return redirect(url_for("commercial_sales_web.list_payout_batches"))


# ------------------------------------------------------------- Dashboards --

@bp.route("/commercial-dashboard", methods=["GET"])
@require_any_permission("quotes.create", "orders.create", "invoices.create")
def employee_dashboard_view():
    staff, profile = _actor()
    if profile is None:
        return render_template("commercial_sales/employee_dashboard.html", data=None)
    # AUDIT-owner-cross-screen: the commission figures are single-currency
    # now (they used to add USD and JOD together). Same ?currency= contract
    # and same default as every other dashboard route here. The screen names
    # the currency and offers a selector for it -- an unlabelled figure that
    # silently depends on this default would be worse than the blended one.
    #
    # Deliberately NOT passed as a second template variable: the template
    # reads data.currency, the one value the service actually scoped its
    # numbers to, so the label can never disagree with the numbers under it
    # (and metrics_contracts pins that key).
    currency = request.args.get("currency") or DEFAULT_DASHBOARD_CURRENCY
    data = employee_commercial_dashboard(profile.id, staff.id, currency)
    return render_template("commercial_sales/employee_dashboard.html", data=data)


@bp.route("/commercial-dashboard/finance", methods=["GET"])
@require_permission("commissions.view_all")
def finance_dashboard_view():
    data = finance_commercial_dashboard()
    return render_template("commercial_sales/finance_dashboard.html", data=data)
