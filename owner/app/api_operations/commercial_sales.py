"""Phase 9.5D Milestone 18 -- commercial-sales subset of /api/operations/v1.

Same blueprint prefix, same cookie-session auth + CSRF protection, same
conventions as app/api_operations/crm.py (jsonify({"error": CODE,
"message": ...}), version param for optimistic locking, fail-closed 404
on unauthorized existence per the established IDOR pattern). Delegates
to the exact same service functions the web routes (Milestone 19) will
use -- no commercial-sales business logic lives in this file, only
request parsing, permission/ownership checks, and response
serialization. Route surface: Quotes, Approvals, Orders, Invoices,
Payments (+ Allocations), Refunds, Fulfillment, Commissions (+ Payouts),
per the funnel contract's own permission column
(commercial-funnel-contract.md).
"""
from __future__ import annotations

import uuid
from datetime import date

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.commercial_sales.allocation import allocate_payment, reverse_allocation, unallocated_payment_balance
from app.commercial_sales.approvals import decide_approval
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
from app.commissions.errors import CommissionError
from app.commissions.ledger import approve_commission_entry, approve_payout_batch, create_payout_batch, record_payout
from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.leads.ownership import apply_ownership_filter
from app.models.commercial_sales import (
    CommercialApproval,
    CommercialInvoice,
    CommercialRefund,
    PaymentAllocation,
    Quote,
    QuoteLine,
    SalesOrder,
)
from app.models.commissions import CommissionLedgerEntry, CommissionPayoutBatch
from app.models.subscriptions import PaymentRecord
from app.security.rbac import get_staff_permission_codes, require_any_permission, require_permission, require_recent_auth

bp = Blueprint("api_operations_commercial_sales", __name__, url_prefix="/api/operations/v1")


def _iso(value):
    return value.isoformat() if value is not None else None


def _decimal(value):
    return str(value) if value is not None else None


def _to_decimal(value):
    """JSON has no Decimal type -- request bodies carry monetary fields as
    float or string. Every service function in commercial_sales/commissions
    expects a real Decimal (calculator.py's LineInput/calculate_line etc.
    perform Decimal arithmetic with no internal coercion, unlike
    leads/validation.py's estimated_value which coerces via
    Decimal(str(value)) itself) -- so the API boundary must coerce here,
    always via str() first to avoid float binary-representation error
    (Decimal(0.1) != Decimal("0.1"))."""
    if value is None:
        return None
    from decimal import Decimal

    return Decimal(str(value))


def _actor():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    return staff, profile


def _error(exc, default_status=400):
    status = 409 if exc.code in ("STALE_VERSION", "IDEMPOTENCY_CONFLICT") else default_status
    return jsonify({"error": exc.code, "message": str(exc)}), status


def _own_or_all(codes, own_code, all_code):
    return all_code in codes


def _model_or_404(model, record_id, actor_profile, *, all_held: bool):
    """Fails CLOSED, matching app/api_operations/crm.py::_lead_or_404 --
    an actor with neither the *_all permission nor a real EmployeeProfile
    is denied, never granted by default."""
    row = db_session.get(model, record_id)
    if row is None:
        return None
    if not all_held:
        if actor_profile is None:
            return None
        if row.created_by_employee_profile_id != actor_profile.id:
            return None
    return row


# --------------------------------------------------------------- Quotes --

def _serialize_quote(q: Quote) -> dict:
    return {
        "id": str(q.id), "quote_number": q.quote_number, "status": q.status,
        "customer_id": str(q.customer_id) if q.customer_id else None,
        "lead_id": str(q.lead_id) if q.lead_id else None,
        "created_by_employee_profile_id": str(q.created_by_employee_profile_id),
        "currency": q.currency, "subtotal": _decimal(q.subtotal), "discount_total": _decimal(q.discount_total),
        "total": _decimal(q.total), "valid_until": _iso(q.valid_until), "sent_at": _iso(q.sent_at),
        "accepted_at": _iso(q.accepted_at), "rejected_at": _iso(q.rejected_at), "cancelled_at": _iso(q.cancelled_at),
        "version": q.version, "created_at": _iso(q.created_at),
    }


def _serialize_quote_line(l: QuoteLine) -> dict:
    return {
        "id": str(l.id), "quote_id": str(l.quote_id), "plan_id": str(l.plan_id) if l.plan_id else None,
        "addon_id": str(l.addon_id) if l.addon_id else None, "quantity": l.quantity,
        "unit_price": _decimal(l.unit_price), "overridden_unit_price": _decimal(l.overridden_unit_price),
        "discount_amount": _decimal(l.discount_amount), "line_total": _decimal(l.line_total),
        "sort_order": l.sort_order, "version": l.version,
    }


@bp.route("/quotes", methods=["GET"])
@require_any_permission("quotes.create", "quotes.approve")
def list_quotes_route():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    stmt = apply_ownership_filter(select(Quote), Quote, profile.id if profile else None, all_permission_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    status = request.args.get("status")
    if status:
        stmt = stmt.where(Quote.status == status)
    rows = db_session.execute(stmt.order_by(Quote.created_at.desc()).limit(200)).scalars().all()
    return jsonify({"rows": [_serialize_quote(q) for q in rows]})


@bp.route("/quotes", methods=["POST"])
@require_permission("quotes.create")
def create_quote_route():
    staff, profile = _actor()
    if profile is None:
        return jsonify({"error": "EMPLOYEE_PROFILE_REQUIRED"}), 400
    body = request.get_json(silent=True) or {}
    try:
        quote = create_quote(body, actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id)
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_quote(quote)), 201


@bp.route("/quotes/<uuid:quote_id>", methods=["GET"])
@require_any_permission("quotes.create", "quotes.approve")
def get_quote_route(quote_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_404(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    lines = db_session.execute(select(QuoteLine).where(QuoteLine.quote_id == quote.id).order_by(QuoteLine.sort_order)).scalars().all()
    data = _serialize_quote(quote)
    data["lines"] = [_serialize_quote_line(l) for l in lines]
    return jsonify(data)


@bp.route("/quotes/<uuid:quote_id>/lines", methods=["POST"])
@require_any_permission("quotes.create", "quotes.approve")
def add_quote_line_route(quote_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_404(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        line = add_quote_line(
            quote,
            plan_id=uuid.UUID(body["plan_id"]) if body.get("plan_id") else None,
            addon_id=uuid.UUID(body["addon_id"]) if body.get("addon_id") else None,
            quantity=int(body.get("quantity", 1)),
            override_unit_price=_to_decimal(body.get("override_unit_price")),
            override_reason=body.get("override_reason"),
            discount_amount=_to_decimal(body.get("discount_amount", 0)),
            sort_order=int(body.get("sort_order", 0)),
            actor_staff_user_id=staff.id,
        )
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_quote_line(line)), 201


@bp.route("/quotes/<uuid:quote_id>/lines/<uuid:line_id>", methods=["DELETE"])
@require_any_permission("quotes.create", "quotes.approve")
def remove_quote_line_route(quote_id, line_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_404(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    line = db_session.get(QuoteLine, line_id)
    if line is None or line.quote_id != quote.id:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        remove_quote_line(quote, line, actor_staff_user_id=staff.id)
    except CommercialSalesError as exc:
        return _error(exc)
    return "", 204


@bp.route("/quotes/<uuid:quote_id>/submit", methods=["POST"])
@require_any_permission("quotes.create", "quotes.approve")
def submit_quote_route(quote_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_404(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        submit_quote(quote, actor_staff_user_id=staff.id, expected_version=body.get("version"))
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_quote(quote))


@bp.route("/quotes/<uuid:quote_id>/cancel", methods=["POST"])
@require_any_permission("quotes.create", "quotes.approve")
def cancel_quote_route(quote_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_404(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        cancel_quote(quote, reason=body.get("reason", ""), actor_staff_user_id=staff.id, expected_version=body.get("version"))
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_quote(quote))


@bp.route("/quotes/<uuid:quote_id>/decision", methods=["POST"])
@require_any_permission("quotes.create", "quotes.approve")
def quote_decision_route(quote_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    quote = _model_or_404(Quote, quote_id, profile, all_held=_own_or_all(codes, "quotes.create", "quotes.approve"))
    if quote is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        record_customer_decision(
            quote, accepted=bool(body.get("accepted")), reason=body.get("reason"),
            actor_staff_user_id=staff.id, expected_version=body.get("version"),
        )
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_quote(quote))


# ------------------------------------------------------------ Approvals --

def _serialize_approval(a: CommercialApproval) -> dict:
    return {
        "id": str(a.id), "target_type": a.target_type, "target_id": str(a.target_id),
        "reason_code": a.reason_code, "status": a.status,
        "requested_by_staff_user_id": str(a.requested_by_staff_user_id),
        "requested_values": a.requested_values, "original_values": a.original_values,
        "decision_reason": a.decision_reason, "requested_at": _iso(a.requested_at),
        "decided_at": _iso(a.decided_at), "version": a.version,
    }


@bp.route("/approvals/<uuid:approval_id>/decide", methods=["POST"])
@require_any_permission("quotes.approve", "pricing.override")
def decide_approval_route(approval_id):
    staff, _profile = _actor()
    approval = db_session.get(CommercialApproval, approval_id)
    if approval is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        decide_approval(
            approval, approved=bool(body.get("approved")), decision_reason=body.get("decision_reason"),
            decided_by_staff_user_id=staff.id,
        )
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_approval(approval))


# ----------------------------------------------------------------- Orders --

def _serialize_order(o: SalesOrder) -> dict:
    return {
        "id": str(o.id), "order_number": o.order_number, "status": o.status,
        "quote_id": str(o.quote_id) if o.quote_id else None, "customer_id": str(o.customer_id),
        "created_by_employee_profile_id": str(o.created_by_employee_profile_id),
        "currency": o.currency, "total": _decimal(o.total), "confirmed_at": _iso(o.confirmed_at),
        "cancelled_at": _iso(o.cancelled_at), "version": o.version, "created_at": _iso(o.created_at),
    }


@bp.route("/orders", methods=["GET"])
@require_any_permission("orders.create", "orders.approve")
def list_orders_route():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    stmt = apply_ownership_filter(select(SalesOrder), SalesOrder, profile.id if profile else None, all_permission_held=_own_or_all(codes, "orders.create", "orders.approve"))
    status = request.args.get("status")
    if status:
        stmt = stmt.where(SalesOrder.status == status)
    rows = db_session.execute(stmt.order_by(SalesOrder.created_at.desc()).limit(200)).scalars().all()
    return jsonify({"rows": [_serialize_order(o) for o in rows]})


@bp.route("/orders", methods=["POST"])
@require_permission("orders.create")
def create_order_route():
    staff, profile = _actor()
    if profile is None:
        return jsonify({"error": "EMPLOYEE_PROFILE_REQUIRED"}), 400
    body = request.get_json(silent=True) or {}
    quote = db_session.get(Quote, uuid.UUID(body["quote_id"])) if body.get("quote_id") else None
    if quote is None or quote.created_by_employee_profile_id != profile.id:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        order = create_order_from_quote(
            quote, actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id,
            idempotency_key=body.get("idempotency_key", str(uuid.uuid4())),
        )
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_order(order)), 201


@bp.route("/orders/<uuid:order_id>", methods=["GET"])
@require_any_permission("orders.create", "orders.approve")
def get_order_route(order_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    order = _model_or_404(SalesOrder, order_id, profile, all_held=_own_or_all(codes, "orders.create", "orders.approve"))
    if order is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    return jsonify(_serialize_order(order))


@bp.route("/orders/<uuid:order_id>/confirm", methods=["POST"])
@require_permission("orders.approve")
def confirm_order_route(order_id):
    staff, _profile = _actor()
    order = db_session.get(SalesOrder, order_id)
    if order is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        confirm_order(order, actor_staff_user_id=staff.id, expected_version=body.get("version"))
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_order(order))


@bp.route("/orders/<uuid:order_id>/cancel", methods=["POST"])
@require_permission("orders.approve")
def cancel_order_route(order_id):
    staff, _profile = _actor()
    order = db_session.get(SalesOrder, order_id)
    if order is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        cancel_order(order, reason=body.get("reason", ""), actor_staff_user_id=staff.id, expected_version=body.get("version"))
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_order(order))


@bp.route("/orders/<uuid:order_id>/fulfill", methods=["POST"])
@require_permission("orders.approve")
def fulfill_order_route(order_id):
    staff, _profile = _actor()
    order = db_session.get(SalesOrder, order_id)
    if order is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        result = fulfill_order(
            order, actor_staff_user_id=staff.id, license_pepper=current_app.config["LICENSE_PEPPER"],
            idempotency_key=body.get("idempotency_key", str(uuid.uuid4())),
        )
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify({k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in result.items()})


# -------------------------------------------------------------- Invoices --

def _serialize_invoice(i: CommercialInvoice) -> dict:
    return {
        "id": str(i.id), "invoice_number": i.invoice_number, "status": i.status,
        "sales_order_id": str(i.sales_order_id) if i.sales_order_id else None, "customer_id": str(i.customer_id),
        "created_by_employee_profile_id": str(i.created_by_employee_profile_id),
        "currency": i.currency, "subtotal": _decimal(i.subtotal), "discount_total": _decimal(i.discount_total),
        "tax_total": _decimal(i.tax_total), "total": _decimal(i.total),
        "allocated_amount": _decimal(confirmed_allocated_amount(i)),
        "issued_at": _iso(i.issued_at), "due_date": _iso(i.due_date), "version": i.version, "created_at": _iso(i.created_at),
    }


@bp.route("/invoices", methods=["GET"])
@require_any_permission("invoices.create", "invoices.issue")
def list_invoices_route():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    stmt = apply_ownership_filter(select(CommercialInvoice), CommercialInvoice, profile.id if profile else None, all_permission_held=_own_or_all(codes, "invoices.create", "invoices.issue"))
    status = request.args.get("status")
    if status:
        stmt = stmt.where(CommercialInvoice.status == status)
    rows = db_session.execute(stmt.order_by(CommercialInvoice.created_at.desc()).limit(200)).scalars().all()
    return jsonify({"rows": [_serialize_invoice(i) for i in rows]})


@bp.route("/invoices", methods=["POST"])
@require_permission("invoices.create")
def create_invoice_route():
    staff, profile = _actor()
    if profile is None:
        return jsonify({"error": "EMPLOYEE_PROFILE_REQUIRED"}), 400
    body = request.get_json(silent=True) or {}
    order = db_session.get(SalesOrder, uuid.UUID(body["order_id"])) if body.get("order_id") else None
    if order is None or order.created_by_employee_profile_id != profile.id:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        invoice = create_invoice_from_order(
            order, actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id,
            idempotency_key=body.get("idempotency_key", str(uuid.uuid4())),
        )
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_invoice(invoice)), 201


@bp.route("/invoices/<uuid:invoice_id>", methods=["GET"])
@require_any_permission("invoices.create", "invoices.issue")
def get_invoice_route(invoice_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    invoice = _model_or_404(CommercialInvoice, invoice_id, profile, all_held=_own_or_all(codes, "invoices.create", "invoices.issue"))
    if invoice is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    return jsonify(_serialize_invoice(invoice))


@bp.route("/invoices/<uuid:invoice_id>/issue", methods=["POST"])
@require_permission("invoices.issue")
def issue_invoice_route(invoice_id):
    staff, _profile = _actor()
    invoice = db_session.get(CommercialInvoice, invoice_id)
    if invoice is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        issue_invoice(invoice, actor_staff_user_id=staff.id, expected_version=body.get("version"))
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_invoice(invoice))


@bp.route("/invoices/<uuid:invoice_id>/void", methods=["POST"])
@require_permission("invoices.issue")
@require_recent_auth
def void_invoice_route(invoice_id):
    staff, _profile = _actor()
    invoice = db_session.get(CommercialInvoice, invoice_id)
    if invoice is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        void_invoice(invoice, reason=body.get("reason", ""), actor_staff_user_id=staff.id, expected_version=body.get("version"))
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_invoice(invoice))


# -------------------------------------------------------------- Payments --

def _serialize_payment(p: PaymentRecord) -> dict:
    return {
        "id": str(p.id), "customer_id": str(p.customer_id), "commercial_invoice_id": str(p.commercial_invoice_id) if p.commercial_invoice_id else None,
        "amount": _decimal(p.amount), "currency": p.currency, "method": p.method, "status": p.status,
        "payment_date": _iso(p.payment_date), "reference": p.reference,
        "unallocated_balance": _decimal(unallocated_payment_balance(p)) if p.status == "CONFIRMED" else None,
    }


@bp.route("/payments", methods=["POST"])
@require_permission("payments.create")
def submit_payment_route():
    staff, _profile = _actor()
    body = request.get_json(silent=True) or {}
    try:
        payment = submit_payment(
            customer_id=uuid.UUID(body["customer_id"]), amount=_to_decimal(body["amount"]), currency=body.get("currency"),
            method=body.get("method"), payment_date=date.fromisoformat(body["payment_date"]),
            commercial_invoice_id=uuid.UUID(body["commercial_invoice_id"]) if body.get("commercial_invoice_id") else None,
            reference=body.get("reference"), actor_staff_user_id=staff.id,
        )
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_payment(payment)), 201


@bp.route("/payments/<uuid:payment_id>", methods=["GET"])
@require_permission("payments.view")
def get_payment_route(payment_id):
    payment = db_session.get(PaymentRecord, payment_id)
    if payment is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    return jsonify(_serialize_payment(payment))


@bp.route("/payments/<uuid:payment_id>/confirm", methods=["POST"])
@require_permission("payments.confirm")
def confirm_payment_route(payment_id):
    staff, _profile = _actor()
    payment = db_session.get(PaymentRecord, payment_id)
    if payment is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        confirm_payment(payment, actor_staff_user_id=staff.id, note=body.get("note"))
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_payment(payment))


@bp.route("/payments/<uuid:payment_id>/reject", methods=["POST"])
@require_permission("payments.confirm")
def reject_payment_route(payment_id):
    staff, _profile = _actor()
    payment = db_session.get(PaymentRecord, payment_id)
    if payment is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        reject_payment(payment, reason=body.get("reason", ""), actor_staff_user_id=staff.id)
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_payment(payment))


# ----------------------------------------------------------- Allocations --

def _serialize_allocation(a: PaymentAllocation) -> dict:
    return {
        "id": str(a.id), "payment_record_id": str(a.payment_record_id), "commercial_invoice_id": str(a.commercial_invoice_id),
        "allocated_amount": _decimal(a.allocated_amount), "currency": a.currency,
        "allocated_at": _iso(a.allocated_at), "reversed_at": _iso(a.reversed_at), "version": a.version,
    }


@bp.route("/allocations", methods=["POST"])
@require_permission("payments.confirm")
def allocate_payment_route():
    staff, _profile = _actor()
    body = request.get_json(silent=True) or {}
    payment = db_session.get(PaymentRecord, uuid.UUID(body["payment_record_id"])) if body.get("payment_record_id") else None
    invoice = db_session.get(CommercialInvoice, uuid.UUID(body["commercial_invoice_id"])) if body.get("commercial_invoice_id") else None
    if payment is None or invoice is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        allocation = allocate_payment(payment=payment, invoice=invoice, amount=_to_decimal(body["amount"]), actor_staff_user_id=staff.id)
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_allocation(allocation)), 201


@bp.route("/allocations/<uuid:allocation_id>/reverse", methods=["POST"])
@require_permission("payments.confirm")
def reverse_allocation_route(allocation_id):
    staff, _profile = _actor()
    allocation = db_session.get(PaymentAllocation, allocation_id)
    if allocation is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        reverse_allocation(allocation, reason=body.get("reason", ""), actor_staff_user_id=staff.id)
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_allocation(allocation))


# ---------------------------------------------------------------- Refunds --

def _serialize_refund(r: CommercialRefund) -> dict:
    return {
        "id": str(r.id), "commercial_invoice_id": str(r.commercial_invoice_id),
        "payment_record_id": str(r.payment_record_id) if r.payment_record_id else None,
        "amount": _decimal(r.amount), "currency": r.currency, "reason": r.reason, "status": r.status,
        "created_by_employee_profile_id": str(r.created_by_employee_profile_id),
        "approved_at": _iso(r.approved_at), "paid_at": _iso(r.paid_at), "voided_at": _iso(r.voided_at), "version": r.version,
    }


@bp.route("/refunds", methods=["POST"])
@require_permission("refunds.create")
def create_refund_route():
    staff, profile = _actor()
    if profile is None:
        return jsonify({"error": "EMPLOYEE_PROFILE_REQUIRED"}), 400
    body = request.get_json(silent=True) or {}
    invoice = db_session.get(CommercialInvoice, uuid.UUID(body["commercial_invoice_id"])) if body.get("commercial_invoice_id") else None
    if invoice is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        refund = create_refund(
            invoice, amount=_to_decimal(body["amount"]), reason=body.get("reason", ""),
            payment_record_id=uuid.UUID(body["payment_record_id"]) if body.get("payment_record_id") else None,
            actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id,
        )
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_refund(refund)), 201


@bp.route("/refunds/<uuid:refund_id>", methods=["GET"])
@require_any_permission("refunds.create", "refunds.approve")
def get_refund_route(refund_id):
    refund = db_session.get(CommercialRefund, refund_id)
    if refund is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    return jsonify(_serialize_refund(refund))


@bp.route("/refunds/<uuid:refund_id>/approve", methods=["POST"])
@require_permission("refunds.approve")
def approve_refund_route(refund_id):
    staff, _profile = _actor()
    refund = db_session.get(CommercialRefund, refund_id)
    if refund is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        approve_refund(refund, actor_staff_user_id=staff.id, expected_version=body.get("version"))
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_refund(refund))


@bp.route("/refunds/<uuid:refund_id>/confirm", methods=["POST"])
@require_permission("refunds.approve")
def confirm_refund_route(refund_id):
    staff, _profile = _actor()
    refund = db_session.get(CommercialRefund, refund_id)
    if refund is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        confirm_refund(refund, actor_staff_user_id=staff.id, expected_version=body.get("version"))
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_refund(refund))


@bp.route("/refunds/<uuid:refund_id>/void", methods=["POST"])
@require_permission("refunds.approve")
def void_refund_route(refund_id):
    staff, _profile = _actor()
    refund = db_session.get(CommercialRefund, refund_id)
    if refund is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        void_refund(refund, actor_staff_user_id=staff.id, expected_version=body.get("version"))
    except CommercialSalesError as exc:
        return _error(exc)
    return jsonify(_serialize_refund(refund))


# ------------------------------------------------------------ Commissions --

def _serialize_commission_entry(e: CommissionLedgerEntry) -> dict:
    return {
        "id": str(e.id), "employee_profile_id": str(e.employee_profile_id),
        "source_commercial_invoice_id": str(e.source_commercial_invoice_id),
        "source_payment_allocation_id": str(e.source_payment_allocation_id),
        "base_amount": _decimal(e.base_amount), "commission_amount": _decimal(e.commission_amount),
        "currency": e.currency, "status": e.status, "earned_at": _iso(e.earned_at),
        "approved_at": _iso(e.approved_at), "paid_at": _iso(e.paid_at),
        "reversal_of_ledger_entry_id": str(e.reversal_of_ledger_entry_id) if e.reversal_of_ledger_entry_id else None,
    }


@bp.route("/commissions", methods=["GET"])
@require_any_permission("commissions.view_own", "commissions.view_all")
def list_commissions_route():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    stmt = select(CommissionLedgerEntry)
    if "commissions.view_all" not in codes:
        if profile is None:
            return jsonify({"rows": []})
        stmt = stmt.where(CommissionLedgerEntry.employee_profile_id == profile.id)
    status = request.args.get("status")
    if status:
        stmt = stmt.where(CommissionLedgerEntry.status == status)
    rows = db_session.execute(stmt.order_by(CommissionLedgerEntry.created_at.desc()).limit(200)).scalars().all()
    return jsonify({"rows": [_serialize_commission_entry(e) for e in rows]})


@bp.route("/commissions/<uuid:entry_id>/approve", methods=["POST"])
@require_permission("commissions.approve")
def approve_commission_route(entry_id):
    staff, _profile = _actor()
    entry = db_session.get(CommissionLedgerEntry, entry_id)
    if entry is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        approve_commission_entry(entry, actor_staff_user_id=staff.id)
    except CommissionError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_commission_entry(entry))


def _serialize_payout_batch(b: CommissionPayoutBatch) -> dict:
    return {
        "id": str(b.id), "batch_reference": b.batch_reference, "period_start": _iso(b.period_start),
        "period_end": _iso(b.period_end), "status": b.status,
        "created_by_staff_user_id": str(b.created_by_staff_user_id) if b.created_by_staff_user_id else None,
        "approved_by_staff_user_id": str(b.approved_by_staff_user_id) if b.approved_by_staff_user_id else None,
    }


@bp.route("/commission-payout-batches", methods=["POST"])
@require_permission("commissions.pay")
def create_payout_batch_route():
    staff, _profile = _actor()
    body = request.get_json(silent=True) or {}
    if body.get("period_start"):
        body["period_start"] = date.fromisoformat(body["period_start"])
    if body.get("period_end"):
        body["period_end"] = date.fromisoformat(body["period_end"])
    try:
        batch = create_payout_batch(body, staff.id)
    except CommissionError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_payout_batch(batch)), 201


@bp.route("/commission-payout-batches/<uuid:batch_id>/approve", methods=["POST"])
@require_permission("commissions.pay")
def approve_payout_batch_route(batch_id):
    staff, _profile = _actor()
    batch = db_session.get(CommissionPayoutBatch, batch_id)
    if batch is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        approve_payout_batch(batch, actor_staff_user_id=staff.id)
    except CommissionError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_payout_batch(batch))


@bp.route("/commission-payout-batches/<uuid:batch_id>/pay/<uuid:entry_id>", methods=["POST"])
@require_permission("commissions.pay")
def record_payout_route(batch_id, entry_id):
    staff, _profile = _actor()
    batch = db_session.get(CommissionPayoutBatch, batch_id)
    entry = db_session.get(CommissionLedgerEntry, entry_id)
    if batch is None or entry is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        line = record_payout(entry, batch, actor_staff_user_id=staff.id)
    except CommissionError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify({"id": str(line.id), "commission_ledger_entry_id": str(line.commission_ledger_entry_id), "amount": _decimal(line.amount)}), 201


# -------------------------------------------------------------- Dashboards --

@bp.route("/commercial/dashboard/employee", methods=["GET"])
@require_any_permission("quotes.create", "orders.create", "invoices.create")
def employee_commercial_dashboard_route():
    from app.commercial_sales.dashboards import employee_commercial_dashboard

    staff, profile = _actor()
    if profile is None:
        return jsonify({"error": "EMPLOYEE_PROFILE_REQUIRED"}), 400
    data = employee_commercial_dashboard(profile.id, staff.id)
    for key in ("commission_earned_unapproved", "commission_approved_unpaid", "commission_paid_total", "commission_reversed_total"):
        data[key] = str(data[key])
    return jsonify(data)


@bp.route("/commercial/dashboard/finance", methods=["GET"])
@require_permission("commissions.view_all")
def finance_commercial_dashboard_route():
    from app.commercial_sales.dashboards import finance_commercial_dashboard

    data = finance_commercial_dashboard()
    data["outstanding_invoice_total"] = str(data["outstanding_invoice_total"])
    return jsonify(data)
