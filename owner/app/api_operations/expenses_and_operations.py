"""Phase 9.5E Milestone 15 -- Expenses/Payees/Cash-Closing/Reports/Management-
Notes subset of /api/operations/v1. Same conventions as
app/api_operations/commercial_sales.py: cookie-session auth + CSRF, fail-
closed 404 on unauthorized existence, jsonify({"error": CODE, "message": ...}),
version param for optimistic locking. Delegates entirely to the domain
services built in Milestones 2-14 -- no fingerprint/self-approval/
overpayment/duplicate/cash-closing/scheduler logic is reimplemented here.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request

from app.audit.services import record as audit_record
from app.auth.session import has_recent_auth, load_current_staff
from app.cash_closing import list_queries as cash_closing_list_queries
from app.cash_closing import services as cash_closing_services
from app.employees.queries import find_own_profile
from app.expenses import approvals as expense_approvals
from app.expenses import attachments as expense_attachments
from app.expenses import duplicates as expense_duplicates
from app.expenses import lifecycle as expense_lifecycle
from app.expenses import payees as expense_payees
from app.expenses import payments as expense_payments
from app.expenses.errors import ExpenseError
from app.extensions import db_session
from app.management_notes import service as note_service
from app.management_notes.errors import ManagementNoteError
from app.models.cash_closing import CashClosing, CashClosingAdjustment
from app.models.expenses import Expense, ExpenseAttachment, ExpenseCategory, Payee
from app.models.management_notes import SharedManagementNote
from app.models.report_snapshots import ReportSnapshot
from app.operational_reports import scheduler as report_scheduler
from app.security.rbac import get_staff_permission_codes, require_any_permission, require_permission, require_recent_auth

bp = Blueprint("api_operations_expenses", __name__, url_prefix="/api/operations/v1")


def _iso(value):
    return value.isoformat() if value is not None else None


def _dec(value):
    return str(value) if value is not None else None


def _to_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise ExpenseError("NON_FINITE_AMOUNT")


def _actor():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    return staff, profile


def _expense_error(exc, default_status=400):
    status = 409 if exc.code in ("STALE_VERSION", "IDEMPOTENCY_CONFLICT", "APPROVAL_STALE") else default_status
    return jsonify({"error": exc.code, "message": str(exc)}), status


def _note_error(exc, default_status=400):
    status = 409 if exc.code == "STALE_VERSION" else default_status
    return jsonify({"error": exc.code, "message": str(exc)}), status


# ---------------------------------------------------------------- Expenses --

def _serialize_expense(e: Expense) -> dict:
    return {
        "id": str(e.id), "expense_number": e.expense_number, "status": e.status,
        "category_id": str(e.category_id), "payee_id": str(e.payee_id) if e.payee_id else None,
        "beneficiary_employee_profile_id": str(e.beneficiary_employee_profile_id) if e.beneficiary_employee_profile_id else None,
        "amount": _dec(e.amount), "approved_amount": _dec(e.approved_amount), "currency": e.currency,
        "expense_date": _iso(e.expense_date), "description": e.description, "external_reference": e.external_reference,
        "entered_by_employee_profile_id": str(e.entered_by_employee_profile_id), "version": e.version,
        "created_at": _iso(e.created_at),
    }


def _expense_or_404(expense_id, actor_profile, *, all_held: bool):
    expense = db_session.get(Expense, expense_id)
    if expense is None:
        return None
    if not all_held:
        if actor_profile is None or expense.entered_by_employee_profile_id != actor_profile.id:
            return None
    return expense


@bp.route("/expenses", methods=["GET"])
@require_any_permission("expenses.view_own", "expenses.view_all")
def list_expenses_route():
    from sqlalchemy import select

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    all_held = "expenses.view_all" in codes
    stmt = select(Expense)
    if not all_held:
        if profile is None:
            return jsonify({"rows": []})
        stmt = stmt.where(Expense.entered_by_employee_profile_id == profile.id)
    status = request.args.get("status")
    if status:
        stmt = stmt.where(Expense.status == status)
    limit = min(int(request.args.get("limit", 50)), 200)
    rows = db_session.execute(stmt.order_by(Expense.created_at.desc()).limit(limit)).scalars().all()
    return jsonify({"rows": [_serialize_expense(e) for e in rows]})


@bp.route("/expenses", methods=["POST"])
@require_permission("expenses.create")
def create_expense_route():
    staff, profile = _actor()
    if profile is None:
        return jsonify({"error": "EMPLOYEE_PROFILE_REQUIRED"}), 400
    body = request.get_json(silent=True) or {}
    try:
        expense = expense_lifecycle.create_expense(
            category_id=uuid.UUID(body["category_id"]), payee_id=uuid.UUID(body["payee_id"]),
            amount=_to_decimal(body.get("amount")), currency=body.get("currency", "USD"),
            expense_date=date.fromisoformat(body["expense_date"]), description=body.get("description", ""),
            external_reference=body.get("external_reference"), payment_method=body.get("payment_method", "CASH"),
            payment_reference=body.get("payment_reference"), entered_by_employee_profile_id=profile.id,
        )
    except ExpenseError as exc:
        return _expense_error(exc)
    except (KeyError, ValueError):
        return jsonify({"error": "INVALID_REQUEST"}), 400
    return jsonify(_serialize_expense(expense)), 201


@bp.route("/expenses/<uuid:expense_id>", methods=["GET"])
@require_any_permission("expenses.view_own", "expenses.view_all")
def get_expense_route(expense_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    expense = _expense_or_404(expense_id, profile, all_held="expenses.view_all" in codes)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    return jsonify(_serialize_expense(expense))


@bp.route("/expenses/<uuid:expense_id>/submit", methods=["POST"])
@require_permission("expenses.create")
def submit_expense_route(expense_id):
    staff, profile = _actor()
    expense = _expense_or_404(expense_id, profile, all_held=False)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        expense_lifecycle.submit_expense(expense, actor_staff_user_id=staff.id)
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify(_serialize_expense(expense))


@bp.route("/expenses/<uuid:expense_id>/revise", methods=["POST"])
@require_permission("expenses.create")
def revise_expense_route(expense_id):
    staff, profile = _actor()
    expense = _expense_or_404(expense_id, profile, all_held=False)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        expense_lifecycle.revise_expense(
            expense, actor_staff_user_id=staff.id,
            amount=_to_decimal(body["amount"]) if "amount" in body else None,
            category_id=uuid.UUID(body["category_id"]) if body.get("category_id") else None,
            payee_id=uuid.UUID(body["payee_id"]) if body.get("payee_id") else None,
            expense_date=date.fromisoformat(body["expense_date"]) if body.get("expense_date") else None,
            description=body.get("description"), external_reference=body.get("external_reference"),
        )
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify(_serialize_expense(expense))


@bp.route("/expenses/<uuid:expense_id>/void", methods=["POST"])
@require_any_permission("expenses.create", "expenses.void")
def void_expense_route(expense_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    expense = _expense_or_404(expense_id, profile, all_held="expenses.void" in codes)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        expense_lifecycle.void_expense(expense, actor_staff_user_id=staff.id, reason=body.get("reason", ""))
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify(_serialize_expense(expense))


@bp.route("/expenses/<uuid:expense_id>/duplicates", methods=["GET"])
@require_any_permission("expenses.view_own", "expenses.view_all")
def expense_duplicate_signals_route(expense_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    can_view_all = "expenses.view_all" in codes
    expense = _expense_or_404(expense_id, profile, all_held=can_view_all)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    signals = expense_duplicates.find_duplicate_signals(expense)
    result = []
    for s in signals:
        matches = []
        for matched_id in s["matched_expense_ids"]:
            matched = db_session.get(Expense, matched_id)
            if matched is not None:
                matches.append(expense_duplicates.summarize_match_for_viewer(
                    matched, viewer_can_view_all=can_view_all, viewer_employee_profile_id=profile.id if profile else None,
                ))
        result.append({"signal_type": s["signal_type"], "confidence": s["confidence"], "matches": matches})
    return jsonify({"signals": result})


@bp.route("/expenses/<uuid:expense_id>/duplicates/override", methods=["POST"])
@require_any_permission("expenses.view_own", "expenses.view_all")
def override_duplicate_route(expense_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    expense = _expense_or_404(expense_id, profile, all_held="expenses.view_all" in codes)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    signals = expense_duplicates.find_duplicate_signals(expense)
    try:
        expense_duplicates.override_duplicate_warning(expense, actor_staff_user_id=staff.id, reason=body.get("reason", ""), signals=signals)
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify(_serialize_expense(expense))


# ----------------------------------------------------------- Approvals --

@bp.route("/expenses/<uuid:expense_id>/approval", methods=["GET"])
@require_any_permission("expenses.view_own", "expenses.view_all", "expenses.approve")
def get_pending_approval_route(expense_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    expense = _expense_or_404(expense_id, profile, all_held=("expenses.view_all" in codes or "expenses.approve" in codes))
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    approval = expense_approvals.pending_approval_for_expense(expense)
    if approval is None:
        return jsonify({"error": "EXPENSE_NOT_PENDING_APPROVAL"}), 404
    return jsonify({
        "id": str(approval.id), "status": approval.status, "fingerprint": approval.fingerprint,
        "requested_amount": _dec(approval.requested_amount), "approved_amount": _dec(approval.approved_amount),
        "requested_at": _iso(approval.requested_at),
    })


@bp.route("/expenses/<uuid:expense_id>/approval/decision", methods=["POST"])
@require_permission("expenses.approve")
def decide_expense_approval_route(expense_id):
    staff, profile = _actor()
    expense = db_session.get(Expense, expense_id)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    approval = expense_approvals.pending_approval_for_expense(expense)
    if approval is None:
        return jsonify({"error": "EXPENSE_NOT_PENDING_APPROVAL"}), 404
    body = request.get_json(silent=True) or {}
    decision = body.get("decision")
    try:
        expense_approvals.decide_expense_approval(
            approval, expense, decision=decision, decided_by=staff,
            approved_amount=_to_decimal(body["approved_amount"]) if body.get("approved_amount") is not None else None,
            decision_reason=body.get("reason"),
        )
    except ExpenseError as exc:
        return _expense_error(exc)
    except ValueError:
        return jsonify({"error": "INVALID_REQUEST"}), 400
    return jsonify(_serialize_expense(expense))


# ------------------------------------------------------------- Payments --

def _serialize_payment(p):
    return {
        "id": str(p.id), "expense_id": str(p.expense_id), "amount": _dec(p.amount), "currency": p.currency,
        "payment_method": p.payment_method, "status": p.status, "recorded_by_staff_user_id": str(p.recorded_by_staff_user_id),
        "paid_at": _iso(p.paid_at),
    }


@bp.route("/expenses/<uuid:expense_id>/payments", methods=["POST"])
@require_permission("expenses.pay")
def record_expense_payment_route(expense_id):
    staff, profile = _actor()
    expense = db_session.get(Expense, expense_id)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    # recorded_by is always the authenticated actor -- a client-supplied
    # value is never trusted (matches Non-Negotiable: no client-authoritative
    # totals/actors anywhere in this API).
    try:
        payment = expense_payments.record_expense_payment(
            expense, amount=_to_decimal(body.get("amount")), currency=body.get("currency", expense.currency),
            payment_method=body.get("payment_method", "CASH"), payment_reference=body.get("payment_reference"),
            recorded_by_staff_user_id=staff.id, idempotency_key=request.headers.get("Idempotency-Key") or body.get("idempotency_key"),
        )
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify(_serialize_payment(payment)), 201


@bp.route("/expenses/<uuid:expense_id>/payments/<uuid:payment_id>/reverse", methods=["POST"])
@require_permission("expenses.pay")
def reverse_expense_payment_route(expense_id, payment_id):
    from app.models.expenses import ExpensePayment

    staff, profile = _actor()
    expense = db_session.get(Expense, expense_id)
    payment = db_session.get(ExpensePayment, payment_id)
    if expense is None or payment is None or payment.expense_id != expense.id:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        expense_payments.reverse_expense_payment(payment, expense, actor_staff_user_id=staff.id, reason=body.get("reason", ""))
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify(_serialize_payment(payment))


# ----------------------------------------------------------- Attachments --

_ALLOWED_UPLOAD_TYPES = {"application/pdf", "image/jpeg", "image/png"}


@bp.route("/expenses/<uuid:expense_id>/attachments", methods=["POST"])
@require_permission("expenses.create")
def upload_attachment_route(expense_id):
    staff, profile = _actor()
    expense = _expense_or_404(expense_id, profile, all_held=False)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    if "file" not in request.files:
        return jsonify({"error": "ATTACHMENT_EMPTY"}), 400
    upload = request.files["file"]
    content = upload.read()
    try:
        attachment = expense_attachments.upload_attachment(
            expense, content=content, original_filename=upload.filename or "attachment",
            declared_content_type=upload.mimetype, uploaded_by_employee_profile_id=profile.id,
        )
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify({
        "id": str(attachment.id), "expense_id": str(attachment.expense_id), "original_filename": attachment.original_filename,
        "content_type": attachment.content_type, "size_bytes": attachment.size_bytes, "status": attachment.status,
        # storage_key/filesystem path is never returned to the client.
    }), 201


@bp.route("/expenses/<uuid:expense_id>/attachments/<uuid:attachment_id>/download", methods=["GET"])
@require_any_permission("expenses.view_own", "expenses.view_all")
def download_attachment_route(expense_id, attachment_id):
    from flask import Response

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    expense = _expense_or_404(expense_id, profile, all_held="expenses.view_all" in codes)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    attachment = db_session.get(ExpenseAttachment, attachment_id)
    if attachment is None or attachment.expense_id != expense.id:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        # Authorization re-checked above on every call, immediately before
        # the read -- never cached, never assumed from a prior request.
        content = expense_attachments.read_attachment_bytes(attachment)
    except ExpenseError as exc:
        return _expense_error(exc, default_status=404)
    audit_record(
        actor_staff_user_id=staff.id, actor_role_snapshot=None, action_code="EXPENSE_ATTACHMENT_DOWNLOADED",
        entity_type="expense_attachment", entity_public_id=str(attachment.id),
    )
    response = Response(content, mimetype=attachment.content_type)
    response.headers["Content-Disposition"] = f'attachment; filename="{attachment.original_filename}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.route("/expenses/<uuid:expense_id>/attachments/<uuid:attachment_id>/archive", methods=["POST"])
@require_permission("expenses.create")
def archive_attachment_route(expense_id, attachment_id):
    staff, profile = _actor()
    expense = _expense_or_404(expense_id, profile, all_held=False)
    if expense is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    attachment = db_session.get(ExpenseAttachment, attachment_id)
    if attachment is None or attachment.expense_id != expense.id:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    expense_attachments.archive_attachment(attachment, actor_staff_user_id=staff.id)
    return jsonify({"id": str(attachment.id), "status": attachment.status})


# ----------------------------------------------------------------- Payees --

@bp.route("/expense-payees", methods=["GET"])
@require_any_permission("expenses.create", "expenses.manage_payees")
def list_payees_route():
    rows = expense_payees.list_active_payees()
    return jsonify({"rows": [
        {"id": str(p.id), "payee_type": p.payee_type, "display_name": p.display_name,
         "employee_profile_id": str(p.employee_profile_id) if p.employee_profile_id else None} for p in rows
    ]})


@bp.route("/expense-payees", methods=["POST"])
@require_permission("expenses.manage_payees")
def create_payee_route():
    staff, profile = _actor()
    body = request.get_json(silent=True) or {}
    try:
        payee = expense_payees.create_payee(
            payee_type=body.get("payee_type", "EXTERNAL"), display_name=body.get("display_name", ""),
            employee_profile_id=uuid.UUID(body["employee_profile_id"]) if body.get("employee_profile_id") else None,
            external_contact_reference=body.get("external_contact_reference"), created_by_staff_user_id=staff.id,
        )
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify({"id": str(payee.id), "payee_type": payee.payee_type, "display_name": payee.display_name}), 201


@bp.route("/expense-payees/<uuid:payee_id>/deactivate", methods=["POST"])
@require_permission("expenses.manage_payees")
def deactivate_payee_route(payee_id):
    staff, profile = _actor()
    payee = db_session.get(Payee, payee_id)
    if payee is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    expense_payees.deactivate_payee(payee, actor_staff_user_id=staff.id)
    return jsonify({"id": str(payee.id), "is_active": payee.is_active})


# ------------------------------------------------------------ Categories --

@bp.route("/expense-categories", methods=["GET"])
@require_any_permission("expenses.create", "expenses.view_all")
def list_expense_categories_route():
    from sqlalchemy import select
    rows = db_session.execute(select(ExpenseCategory).where(ExpenseCategory.is_active.is_(True))).scalars().all()
    return jsonify({"rows": [{"id": str(c.id), "category_code": c.category_code, "name": c.name} for c in rows]})


# ------------------------------------------------------------ Cash Closing --

def _serialize_closing(c: CashClosing) -> dict:
    return {
        "id": str(c.id), "business_date": _iso(c.business_date), "currency": c.currency, "status": c.status,
        "opening_cash": _dec(c.opening_cash), "confirmed_cash_collections": _dec(c.confirmed_cash_collections),
        "confirmed_cash_refunds": _dec(c.confirmed_cash_refunds), "cash_expense_payments": _dec(c.cash_expense_payments),
        "cash_commission_payouts": _dec(c.cash_commission_payouts), "approved_cash_adjustments": _dec(c.approved_cash_adjustments),
        "expected_closing_cash": _dec(c.expected_closing_cash), "actual_counted_cash": _dec(c.actual_counted_cash),
        "variance": _dec(c.variance), "variance_explanation": c.variance_explanation, "version": c.version,
        "reopen_count": c.reopen_count,
    }


@bp.route("/cash-closings", methods=["GET", "POST"])
@require_any_permission("cash_closing.view_own", "cash_closing.view_all", "cash_closing.prepare")
def cash_closings_route():
    staff, profile = _actor()
    if request.method == "GET":
        # AUDIT-031's JSON-API twin: this branch used to run its own raw,
        # unscoped query (no ownership restriction, no shared pagination
        # shape, and date.fromisoformat() crashing 500 on a malformed
        # ?business_date= instead of a clean 400) -- bypassing
        # cash_closing.list_queries.list_closings(), which the web route
        # (operations_ui/routes.py::list_closings) already used for real
        # ownership scoping. Same bypass-set logic (view_all_held), same
        # shared query, same paginated response shape as the web route now.
        business_date_raw = request.args.get("business_date")
        try:
            business_date = date.fromisoformat(business_date_raw) if business_date_raw else None
        except ValueError:
            return jsonify({"error": "INVALID_REQUEST"}), 400
        codes = get_staff_permission_codes(staff)
        view_all_held = bool({"cash_closing.view_all", "cash_closing.approve"} & codes)
        result = cash_closing_list_queries.list_closings(
            page=request.args.get("page", 1, type=int),
            currency=request.args.get("currency", "USD"),
            status=request.args.get("status") or None,
            business_date=business_date,
            sort=request.args.get("sort", "business_date"),
            direction=request.args.get("dir", "desc"),
            actor_staff_user_id=staff.id,
            view_all_held=view_all_held,
        )
        result["rows"] = [_serialize_closing(c) for c in result["rows"]]
        return jsonify(result)

    body = request.get_json(silent=True) or {}
    try:
        closing = cash_closing_services.get_or_create_draft_closing(
            date.fromisoformat(body["business_date"]), body.get("currency", "USD"), prepared_by_staff_user_id=staff.id,
            opening_cash_override=_to_decimal(body.get("opening_cash_override")),
            opening_cash_override_reason=body.get("opening_cash_override_reason"),
        )
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify(_serialize_closing(closing)), 201


@bp.route("/cash-closings/<uuid:closing_id>/submit", methods=["POST"])
@require_permission("cash_closing.prepare")
def submit_closing_route(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        cash_closing_services.submit_closing(
            closing, actor_staff_user_id=staff.id, actual_counted_cash=_to_decimal(body.get("actual_counted_cash")),
            variance_explanation=body.get("variance_explanation"),
        )
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify(_serialize_closing(closing))


@bp.route("/cash-closings/<uuid:closing_id>/decision", methods=["POST"])
@require_permission("cash_closing.approve")
def decide_closing_route(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        cash_closing_services.decide_closing(closing, decision=body.get("decision"), actor_staff_user_id=staff.id, reason=body.get("reason"))
    except ExpenseError as exc:
        return _expense_error(exc)
    except ValueError:
        return jsonify({"error": "INVALID_REQUEST"}), 400
    return jsonify(_serialize_closing(closing))


@bp.route("/cash-closings/<uuid:closing_id>/close", methods=["POST"])
@require_permission("cash_closing.approve")
def close_closing_route(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        cash_closing_services.close_closing(closing, actor_staff_user_id=staff.id)
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify(_serialize_closing(closing))


@bp.route("/cash-closings/<uuid:closing_id>/reopen", methods=["POST"])
@require_recent_auth
@require_permission("cash_closing.reopen")
def reopen_closing_route(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        # @require_recent_auth already blocked the request otherwise --
        # recent_auth_verified=True here reflects a fact already enforced,
        # never taken on the client's word.
        cash_closing_services.reopen_closing(closing, actor_staff_user_id=staff.id, reason=body.get("reason", ""), recent_auth_verified=has_recent_auth())
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify(_serialize_closing(closing))


@bp.route("/cash-closings/<uuid:closing_id>/adjustments", methods=["POST"])
@require_permission("cash_closing.adjust")
def add_adjustment_route(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        adjustment = cash_closing_services.add_adjustment(
            closing, amount=_to_decimal(body.get("amount")), reason=body.get("reason", ""), created_by_staff_user_id=staff.id,
        )
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify({"id": str(adjustment.id), "amount": _dec(adjustment.amount)}), 201


@bp.route("/cash-closings/<uuid:closing_id>/adjustments/<uuid:adjustment_id>/approve", methods=["POST"])
@require_permission("cash_closing.approve_adjustment")
def approve_adjustment_route(closing_id, adjustment_id):
    staff, profile = _actor()
    adjustment = db_session.get(CashClosingAdjustment, adjustment_id)
    if adjustment is None or adjustment.cash_closing_id != closing_id:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        cash_closing_services.approve_adjustment(adjustment, actor_staff_user_id=staff.id)
    except ExpenseError as exc:
        return _expense_error(exc)
    return jsonify({"id": str(adjustment.id), "approved": True})


# ----------------------------------------------------------------- Reports --

def _serialize_snapshot(s: ReportSnapshot) -> dict:
    return {
        "id": str(s.id), "report_type": s.report_type, "period_start": _iso(s.period_start), "period_end": _iso(s.period_end),
        "currency": s.currency, "snapshot_version": s.snapshot_version, "status": s.status, "payload": s.payload,
        "generated_by": s.generated_by, "cutoff_at": _iso(s.cutoff_at),
    }


@bp.route("/report-snapshots", methods=["GET"])
@require_permission("report_snapshots.view")
def list_report_snapshots_route():
    from sqlalchemy import select

    report_type = request.args.get("report_type")
    stmt = select(ReportSnapshot).where(ReportSnapshot.status == "PUBLISHED")
    if report_type:
        stmt = stmt.where(ReportSnapshot.report_type == report_type)
    rows = db_session.execute(stmt.order_by(ReportSnapshot.period_start.desc()).limit(100)).scalars().all()
    return jsonify({"rows": [_serialize_snapshot(s) for s in rows]})


@bp.route("/report-snapshots/generate", methods=["POST"])
@require_permission("report_snapshots.view")
def generate_report_snapshot_route():
    staff, profile = _actor()
    body = request.get_json(silent=True) or {}
    try:
        snapshot = report_scheduler.generate_snapshot(
            report_type=body.get("report_type"), period_start=date.fromisoformat(body["period_start"]),
            period_end=date.fromisoformat(body["period_end"]), currency=body.get("currency"),
            generated_by="MANUAL", generated_by_staff_user_id=staff.id, request_id=request.headers.get("Idempotency-Key"),
        )
    except ValueError:
        return jsonify({"error": "INVALID_REQUEST"}), 400
    return jsonify(_serialize_snapshot(snapshot)), 201


@bp.route("/report-snapshots/regenerate", methods=["POST"])
@require_permission("report_snapshots.regenerate")
def regenerate_report_snapshot_route():
    staff, profile = _actor()
    body = request.get_json(silent=True) or {}
    try:
        snapshot = report_scheduler.regenerate_snapshot(
            report_type=body.get("report_type"), period_start=date.fromisoformat(body["period_start"]),
            period_end=date.fromisoformat(body["period_end"]), currency=body.get("currency"),
            actor_staff_user_id=staff.id, reason=body.get("reason", ""),
        )
    except ExpenseError as exc:
        return _expense_error(exc)
    except ValueError:
        return jsonify({"error": "INVALID_REQUEST"}), 400
    return jsonify(_serialize_snapshot(snapshot)), 201


# ----------------------------------------------------------- Mgmt Notes --

def _serialize_note(n: SharedManagementNote) -> dict:
    return {
        "id": str(n.id), "title": n.title, "body": n.body, "category": n.category, "priority": n.priority,
        "status": n.status, "pinned": n.pinned, "visibility": n.visibility,
        "assigned_employee_profile_id": str(n.assigned_employee_profile_id) if n.assigned_employee_profile_id else None,
        "due_date": _iso(n.due_date), "version": n.version, "created_at": _iso(n.created_at),
    }


@bp.route("/management-notes", methods=["GET"])
@require_permission("management_notes.view")
def list_management_notes_route():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    rows = note_service.notes_visible_to(
        actor_employee_profile_id=profile.id if profile else None, has_manage_permission="management_notes.manage" in codes,
    )
    return jsonify({"rows": [_serialize_note(n) for n in rows]})


@bp.route("/management-notes", methods=["POST"])
@require_permission("management_notes.manage")
def create_management_note_route():
    staff, profile = _actor()
    body = request.get_json(silent=True) or {}
    if "author_staff_user_id" in body:
        return jsonify({"error": "AUTHOR_SPOOF_FORBIDDEN"}), 400
    try:
        note = note_service.create_note(
            title=body.get("title", ""), body=body.get("body", ""), category=body.get("category"),
            priority=body.get("priority", "MEDIUM"), visibility=body.get("visibility", "MANAGEMENT_ONLY"),
            assigned_employee_profile_id=uuid.UUID(body["assigned_employee_profile_id"]) if body.get("assigned_employee_profile_id") else None,
            due_date=date.fromisoformat(body["due_date"]) if body.get("due_date") else None,
            specific_employee_profile_ids=[uuid.UUID(x) for x in body.get("specific_employee_profile_ids", [])] or None,
            created_by_staff_user_id=staff.id,
        )
    except ManagementNoteError as exc:
        return _note_error(exc)
    return jsonify(_serialize_note(note)), 201


def _note_or_404(note_id, staff, profile):
    codes = get_staff_permission_codes(staff)
    note = db_session.get(SharedManagementNote, note_id)
    if note is None:
        return None
    if not note_service.can_view_note(note, actor_employee_profile_id=profile.id if profile else None, has_manage_permission="management_notes.manage" in codes):
        return None
    return note


@bp.route("/management-notes/<uuid:note_id>", methods=["GET"])
@require_permission("management_notes.view")
def get_management_note_route(note_id):
    staff, profile = _actor()
    note = _note_or_404(note_id, staff, profile)
    if note is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    return jsonify(_serialize_note(note))


@bp.route("/management-notes/<uuid:note_id>/assign", methods=["POST"])
@require_permission("management_notes.manage")
def assign_management_note_route(note_id):
    staff, profile = _actor()
    note = db_session.get(SharedManagementNote, note_id)
    if note is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    note_service.assign_note(
        note, assignee_employee_profile_id=uuid.UUID(body["assignee_employee_profile_id"]) if body.get("assignee_employee_profile_id") else None,
        actor_staff_user_id=staff.id,
    )
    return jsonify(_serialize_note(note))


@bp.route("/management-notes/<uuid:note_id>/status", methods=["POST"])
@require_permission("management_notes.manage")
def set_management_note_status_route(note_id):
    staff, profile = _actor()
    note = db_session.get(SharedManagementNote, note_id)
    if note is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        note_service.set_note_status(note, target_status=body.get("status"), actor_staff_user_id=staff.id)
    except ManagementNoteError as exc:
        return _note_error(exc)
    return jsonify(_serialize_note(note))


@bp.route("/management-notes/<uuid:note_id>/comments", methods=["POST"])
@require_permission("management_notes.view")
def add_management_note_comment_route(note_id):
    staff, profile = _actor()
    note = _note_or_404(note_id, staff, profile)
    if note is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    if "author_staff_user_id" in body:
        return jsonify({"error": "AUTHOR_SPOOF_FORBIDDEN"}), 400
    try:
        comment = note_service.add_comment(note, body=body.get("body", ""), author_staff_user_id=staff.id)
    except ManagementNoteError as exc:
        return _note_error(exc)
    return jsonify({"id": str(comment.id), "body": comment.body, "created_at": _iso(comment.created_at)}), 201


# ------------------------------------------------------------- Dashboards --
# Phase 9.5E Milestone 12. Every metric delegates to
# app.operational_reports.dashboards / .aggregation -- no calculation here.

@bp.route("/dashboards/employee-expenses", methods=["GET"])
@require_permission("dashboard.view_own")
def employee_expense_dashboard_route():
    from app.operational_reports.dashboards import employee_expense_dashboard

    staff, profile = _actor()
    if profile is None:
        return jsonify({"error": "EMPLOYEE_PROFILE_REQUIRED"}), 400
    return jsonify(employee_expense_dashboard(profile.id))


@bp.route("/dashboards/management-operations", methods=["GET"])
@require_permission("dashboard.view_all")
def management_operational_dashboard_route():
    from app.operational_reports.dashboards import management_operational_dashboard

    currency = request.args.get("currency", "USD")
    return jsonify(management_operational_dashboard(currency))


@bp.route("/dashboards/finance-operations", methods=["GET"])
@require_permission("dashboard.view_all")
def finance_operational_dashboard_route():
    from app.operational_reports.dashboards import finance_operational_dashboard

    currency = request.args.get("currency", "USD")
    return jsonify(finance_operational_dashboard(currency))
