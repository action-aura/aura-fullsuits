"""Phase 9.5E Milestone 16 -- web UI for Expenses/Payees/Cash-Closing/
Report-Snapshots/Management-Notes. Same conventions as
app/commercial_sales/routes.py: CSRF via global csrf.init_app(app),
Post/Redirect/Get, no destructive GET, ownership enforced on every route,
stable-code errors localized only here via app.i18n_labels. Calls the exact
same service functions the API (Milestone 15) uses -- no business logic
duplicated at this layer either."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select

from app.auth.session import has_recent_auth, load_current_staff
from app.cash_closing import list_queries as cash_closing_list_queries
from app.cash_closing import services as cash_closing_services
from app.employees.queries import find_own_profile
from app.expenses import approvals as expense_approvals
from app.expenses import attachments as expense_attachments
from app.expenses import duplicates as expense_duplicates
from app.expenses import lifecycle as expense_lifecycle
from app.expenses import list_queries as expense_list_queries
from app.expenses import payees as expense_payees
from app.expenses import payments as expense_payments
from app.expenses.errors import ExpenseError
from app.extensions import db_session
from app.i18n_labels import localize_expense_error, localize_management_note_error
from app.management_notes import service as note_service
from app.management_notes.errors import ManagementNoteError
from app.models.cash_closing import CashClosing
from app.models.employees import EmployeeProfile
from app.models.expenses import Expense, ExpenseAttachment, ExpenseCategory, Payee
from app.models.management_notes import ManagementNoteComment, SharedManagementNote
from app.models.report_snapshots import REPORT_TYPES, ReportSnapshot
from app.models.staff import StaffUser
from app.operational_reports import scheduler as report_scheduler
from app.security.rbac import get_staff_permission_codes, require_any_permission, require_permission, require_recent_auth
from app.services.pagination import DEFAULT_PAGE_SIZE

bp = Blueprint("operations_ui", __name__, url_prefix="/operations")

_NO_PROFILE_MESSAGE = "This action requires a real employee profile; administrative accounts without one cannot perform it."

_EMPTY_PAGE_RESULT = {"rows": [], "page": 1, "page_size": DEFAULT_PAGE_SIZE, "total": 0, "total_pages": 1, "has_prev": False, "has_next": False}


def _actor():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    return staff, profile


# UI modernization Stage D -- actor/audit-context resolution, mirroring
# commercial_sales/routes.py::_employee_profile_name/_staff_display_name's
# exact db_session.get(...) pattern (a fresh, local copy per module, not a
# cross-blueprint import -- same discipline as every other domain here).
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


def _to_decimal(raw):
    if raw is None or raw == "":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _expense_or_none(expense_id, actor_profile, *, all_held: bool):
    expense = db_session.get(Expense, expense_id)
    if expense is None:
        return None
    if not all_held and (actor_profile is None or expense.entered_by_employee_profile_id != actor_profile.id):
        return None
    return expense


# ---------------------------------------------------------------- Expenses --

@bp.route("/expenses", methods=["GET"])
@require_any_permission("expenses.view_own", "expenses.view_all")
def list_expenses():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    all_held = "expenses.view_all" in codes
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    page = request.args.get("page", 1, type=int)
    # UI modernization Stage D -- the `all_held` bypass is checked first
    # (it is the outer condition here, same discipline
    # commercial-flow-ui-contract.md requires: an _all-permission bypass
    # must be verified before, or structurally independent of, any
    # "no profile" guard), and the actual ownership scoping now delegates
    # entirely to the shared apply_ownership_filter() (via
    # expenses.list_queries.list_expenses()) rather than a hand-rolled
    # `entered_by_employee_profile_id ==` filter.
    if not all_held and profile is None:
        result = dict(_EMPTY_PAGE_RESULT)
    else:
        result = expense_list_queries.list_expenses(
            page=page, status=status_filter, search=search, sort=sort, direction=direction,
            actor_employee_profile_id=profile.id if profile else None, all_permission_held=all_held,
        )
    return render_template("operations_ui/expenses_list.html", result=result, status_filter=status_filter, search=search)


@bp.route("/expenses/new", methods=["GET"])
@require_permission("expenses.create")
def new_expense_form():
    categories = db_session.execute(select(ExpenseCategory).where(ExpenseCategory.is_active.is_(True))).scalars().all()
    payees = expense_payees.list_active_payees()
    return render_template("operations_ui/expense_new.html", categories=categories, payees=payees, error=None)


@bp.route("/expenses", methods=["POST"])
@require_permission("expenses.create")
def create_expense_route():
    staff, profile = _actor()
    if profile is None:
        categories = db_session.execute(select(ExpenseCategory).where(ExpenseCategory.is_active.is_(True))).scalars().all()
        return render_template("operations_ui/expense_new.html", categories=categories, payees=expense_payees.list_active_payees(), error=_NO_PROFILE_MESSAGE), 400
    try:
        expense = expense_lifecycle.create_expense(
            category_id=uuid.UUID(request.form["category_id"]), payee_id=uuid.UUID(request.form["payee_id"]),
            amount=_to_decimal(request.form.get("amount")), currency=request.form.get("currency", "USD"),
            expense_date=date.fromisoformat(request.form["expense_date"]), description=request.form.get("description", ""),
            external_reference=request.form.get("external_reference") or None,
            payment_method=request.form.get("payment_method", "CASH"), payment_reference=None,
            entered_by_employee_profile_id=profile.id,
        )
    except (ExpenseError, KeyError, ValueError) as exc:
        error = localize_expense_error(exc.code, **exc.params) if isinstance(exc, ExpenseError) else _("Invalid input.")
        categories = db_session.execute(select(ExpenseCategory).where(ExpenseCategory.is_active.is_(True))).scalars().all()
        return render_template("operations_ui/expense_new.html", categories=categories, payees=expense_payees.list_active_payees(), error=error), 400
    return redirect(url_for("operations_ui.expense_detail", expense_id=expense.id))


@bp.route("/expenses/<uuid:expense_id>", methods=["GET"])
@require_any_permission("expenses.view_own", "expenses.view_all", "expenses.approve", "expenses.pay")
def expense_detail(expense_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    all_held = bool({"expenses.view_all", "expenses.approve", "expenses.pay"} & codes)
    expense = _expense_or_none(expense_id, profile, all_held=all_held)
    if expense is None:
        return render_template("commercial_sales/not_found.html"), 404
    approval = expense_approvals.pending_approval_for_expense(expense)
    # UI modernization Stage D -- latest (not just pending) approval cycle,
    # for the real status-timeline UI (see
    # app.expenses.status_presentation.expense_timeline).
    latest_approval = expense_approvals.latest_approval_for_expense(expense)
    attachments = db_session.execute(
        select(ExpenseAttachment).where(ExpenseAttachment.expense_id == expense.id, ExpenseAttachment.status == "ACTIVE")
    ).scalars().all()
    outstanding = expense_payments.outstanding_amount(expense) if expense.approved_amount is not None else None
    duplicate_signals = expense_duplicates.find_duplicate_signals(expense)
    is_own = profile is not None and expense.entered_by_employee_profile_id == profile.id
    # UI modernization Stage D -- real, previously-unsurfaced context: which
    # category/payee/beneficiary this expense belongs to, and who entered/
    # approved it (real FKs, never shown on this page before this pass).
    category = db_session.get(ExpenseCategory, expense.category_id)
    payee = db_session.get(Payee, expense.payee_id) if expense.payee_id else None
    beneficiary_name = _employee_profile_name(expense.beneficiary_employee_profile_id)
    entered_by_name = _employee_profile_name(expense.entered_by_employee_profile_id)
    approved_by_name = _staff_display_name(expense.approved_by_staff_user_id)
    return render_template(
        "operations_ui/expense_detail.html", expense=expense, approval=approval, latest_approval=latest_approval,
        attachments=attachments, outstanding=outstanding, duplicate_signals=duplicate_signals, is_own=is_own, codes=codes,
        category=category, payee=payee, beneficiary_name=beneficiary_name,
        entered_by_name=entered_by_name, approved_by_name=approved_by_name,
    )


@bp.route("/expenses/<uuid:expense_id>/submit", methods=["POST"])
@require_permission("expenses.create")
def submit_expense_route(expense_id):
    staff, profile = _actor()
    expense = _expense_or_none(expense_id, profile, all_held=False)
    if expense is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        expense_lifecycle.submit_expense(expense, actor_staff_user_id=staff.id)
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.expense_detail", expense_id=expense_id))


@bp.route("/expenses/<uuid:expense_id>/revise", methods=["POST"])
@require_permission("expenses.create")
def revise_expense_route(expense_id):
    staff, profile = _actor()
    expense = _expense_or_none(expense_id, profile, all_held=False)
    if expense is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        expense_lifecycle.revise_expense(
            expense, actor_staff_user_id=staff.id, amount=_to_decimal(request.form.get("amount")),
            description=request.form.get("description") or None, external_reference=request.form.get("external_reference") or None,
        )
        expense_lifecycle.submit_expense(expense, actor_staff_user_id=staff.id)
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.expense_detail", expense_id=expense_id))


@bp.route("/expenses/<uuid:expense_id>/void", methods=["POST"])
@require_any_permission("expenses.create", "expenses.void")
def void_expense_route(expense_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    expense = _expense_or_none(expense_id, profile, all_held="expenses.void" in codes)
    if expense is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        expense_lifecycle.void_expense(expense, actor_staff_user_id=staff.id, reason=request.form.get("reason", ""))
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.expense_detail", expense_id=expense_id))


@bp.route("/expenses/<uuid:expense_id>/approval/decision", methods=["POST"])
@require_permission("expenses.approve")
def decide_approval_route(expense_id):
    staff, profile = _actor()
    expense = db_session.get(Expense, expense_id)
    if expense is None:
        return render_template("commercial_sales/not_found.html"), 404
    approval = expense_approvals.pending_approval_for_expense(expense)
    if approval is not None:
        approved_amount = _to_decimal(request.form.get("approved_amount")) if request.form.get("decision") == "APPROVED" else None
        try:
            expense_approvals.decide_expense_approval(
                approval, expense, decision=request.form.get("decision"), decided_by=staff,
                approved_amount=approved_amount, decision_reason=request.form.get("reason"),
            )
        except ExpenseError as exc:
            flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.expense_detail", expense_id=expense_id))


@bp.route("/expenses/<uuid:expense_id>/payments", methods=["POST"])
@require_permission("expenses.pay")
def record_payment_route(expense_id):
    staff, profile = _actor()
    expense = db_session.get(Expense, expense_id)
    if expense is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        expense_payments.record_expense_payment(
            expense, amount=_to_decimal(request.form.get("amount")), currency=expense.currency,
            payment_method=request.form.get("payment_method", "CASH"), payment_reference=request.form.get("payment_reference") or None,
            recorded_by_staff_user_id=staff.id,
        )
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.expense_detail", expense_id=expense_id))


@bp.route("/expenses/<uuid:expense_id>/attachments", methods=["POST"])
@require_permission("expenses.create")
def upload_attachment_route(expense_id):
    staff, profile = _actor()
    expense = _expense_or_none(expense_id, profile, all_held=False)
    if expense is None:
        return render_template("commercial_sales/not_found.html"), 404
    upload = request.files.get("file")
    if upload is not None and upload.filename:
        try:
            expense_attachments.upload_attachment(
                expense, content=upload.read(), original_filename=upload.filename,
                declared_content_type=upload.mimetype, uploaded_by_employee_profile_id=profile.id,
            )
        except ExpenseError as exc:
            flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.expense_detail", expense_id=expense_id))


@bp.route("/expenses/<uuid:expense_id>/attachments/<uuid:attachment_id>/download", methods=["GET"])
@require_any_permission("expenses.view_own", "expenses.view_all")
def download_attachment_route(expense_id, attachment_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    expense = _expense_or_none(expense_id, profile, all_held="expenses.view_all" in codes)
    if expense is None:
        return render_template("commercial_sales/not_found.html"), 404
    attachment = db_session.get(ExpenseAttachment, attachment_id)
    if attachment is None or attachment.expense_id != expense.id:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        content = expense_attachments.read_attachment_bytes(attachment)
    except ExpenseError:
        return render_template("commercial_sales/not_found.html"), 404
    response = Response(content, mimetype=attachment.content_type)
    response.headers["Content-Disposition"] = f'attachment; filename="{attachment.original_filename}"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.route("/expenses/<uuid:expense_id>/duplicates/override", methods=["POST"])
@require_any_permission("expenses.view_own", "expenses.view_all")
def override_duplicate_route(expense_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    expense = _expense_or_none(expense_id, profile, all_held="expenses.view_all" in codes)
    if expense is None:
        return render_template("commercial_sales/not_found.html"), 404
    signals = expense_duplicates.find_duplicate_signals(expense)
    try:
        expense_duplicates.override_duplicate_warning(expense, actor_staff_user_id=staff.id, reason=request.form.get("reason", ""), signals=signals)
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.expense_detail", expense_id=expense_id))


# ----------------------------------------------------------------- Payees --

@bp.route("/expense-payees", methods=["GET"])
@require_any_permission("expenses.create", "expenses.manage_payees")
def list_payees():
    payees = expense_payees.list_active_payees()
    return render_template("operations_ui/payees_list.html", payees=payees, error=None)


@bp.route("/expense-payees", methods=["POST"])
@require_permission("expenses.manage_payees")
def create_payee_route():
    staff, profile = _actor()
    try:
        expense_payees.create_payee(
            payee_type=request.form.get("payee_type", "EXTERNAL"), display_name=request.form.get("display_name", ""),
            employee_profile_id=uuid.UUID(request.form["employee_profile_id"]) if request.form.get("employee_profile_id") else None,
            external_contact_reference=request.form.get("external_contact_reference") or None, created_by_staff_user_id=staff.id,
        )
    except ExpenseError as exc:
        payees = expense_payees.list_active_payees()
        return render_template("operations_ui/payees_list.html", payees=payees, error=localize_expense_error(exc.code, **exc.params)), 400
    return redirect(url_for("operations_ui.list_payees"))


# ------------------------------------------------------------ Cash Closing --

@bp.route("/cash-closings", methods=["GET"])
@require_any_permission("cash_closing.view_own", "cash_closing.view_all", "cash_closing.prepare")
def list_closings():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    # UI modernization Stage D -- `cash_closing.approve` ("Approve/reject a
    # submitted cash closing", seed_data.py) must bypass the same as
    # `view_all`: an approver reviews closings *other* preparers submitted
    # by definition of maker-checker -- restricting an approve-only holder
    # to their own prepared closings would make the permission functionally
    # useless the moment a narrower approver-only role exists. Today every
    # `cash_closing.*` holder is FINANCE with all codes together (view_all
    # included), so this has no live behavioral effect yet -- same
    # zero-current-impact, real-latent-gap shape already documented for the
    # `view_own` fix itself.
    view_all_held = bool({"cash_closing.view_all", "cash_closing.approve"} & codes)
    currency = request.args.get("currency", "USD")
    status_filter = request.args.get("status") or None
    sort = request.args.get("sort", "business_date")
    direction = request.args.get("dir", "desc")
    page = request.args.get("page", 1, type=int)
    # UI modernization Stage D -- real, disclosed bug fix (see
    # finance-ui-contract.md): this query previously filtered ONLY by
    # currency, no ownership restriction at all, even though
    # cash_closing.view_own's own seed_data.py label is "View cash
    # closings this account prepared". Restriction is now real, bypassed
    # by view_all/approve (see view_all_held's own comment above, checked
    # first, same bypass-before-any-narrower-guard discipline every other
    # list route here follows) -- see cash_closing/list_queries.py's own
    # docstring for why this can't delegate to apply_ownership_filter().
    result = cash_closing_list_queries.list_closings(
        page=page, currency=currency, status=status_filter, sort=sort, direction=direction,
        actor_staff_user_id=staff.id, view_all_held=view_all_held,
    )
    return render_template("operations_ui/cash_closings_list.html", result=result, currency=currency, status_filter=status_filter)


@bp.route("/cash-closings/new", methods=["GET"])
@require_permission("cash_closing.prepare")
def new_closing_form():
    return render_template("operations_ui/cash_closing_new.html", error=None)


@bp.route("/cash-closings", methods=["POST"])
@require_permission("cash_closing.prepare")
def create_closing_route():
    staff, profile = _actor()
    try:
        closing = cash_closing_services.get_or_create_draft_closing(
            date.fromisoformat(request.form["business_date"]), request.form.get("currency", "USD"),
            prepared_by_staff_user_id=staff.id, opening_cash_override=_to_decimal(request.form.get("opening_cash_override")),
            opening_cash_override_reason=request.form.get("opening_cash_override_reason") or None,
        )
    except ExpenseError as exc:
        return render_template("operations_ui/cash_closing_new.html", error=localize_expense_error(exc.code, **exc.params)), 400
    return redirect(url_for("operations_ui.closing_detail", closing_id=closing.id))


@bp.route("/cash-closings/<uuid:closing_id>", methods=["GET"])
@require_any_permission("cash_closing.view_own", "cash_closing.view_all", "cash_closing.prepare", "cash_closing.approve")
def closing_detail(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return render_template("commercial_sales/not_found.html"), 404
    codes = get_staff_permission_codes(staff)
    # UI modernization Stage D -- real IDOR-shaped gap found during
    # curl-verification of the list-ownership fix above: this route had no
    # per-record ownership check at all (unlike expense_detail's
    # _expense_or_none), so a view_own-only holder could view ANY closing
    # directly by ID even though the list already hid it from them -- same
    # bug class as the list fix, just on the detail GET. Same bypass set
    # (view_all OR approve, see list_closings()'s own comment); a
    # prepare-only holder is restricted to their own prepared closings,
    # matching cash_closing/list_queries.py's documented "scoped the same
    # way view_own holders are" choice for prepare-only.
    if not ({"cash_closing.view_all", "cash_closing.approve"} & codes) and closing.prepared_by_staff_user_id != staff.id:
        return render_template("commercial_sales/not_found.html"), 404
    cash_closing_services.recalculate_expected(closing)
    # UI modernization Stage D -- real, previously-unsurfaced context for
    # the status-timeline + related-records UI (see
    # app.cash_closing.status_presentation.cash_closing_timeline).
    prior_closing = cash_closing_services.prior_closing_for_display(closing)
    latest_reopen_event = cash_closing_services.latest_reopen_event(closing)
    prepared_by_name = _staff_display_name(closing.prepared_by_staff_user_id)
    reviewed_by_name = _staff_display_name(closing.reviewed_by_staff_user_id)
    approved_by_name = _staff_display_name(closing.approved_by_staff_user_id)
    return render_template(
        "operations_ui/cash_closing_detail.html", closing=closing, codes=codes, staff=staff,
        prior_closing=prior_closing, latest_reopen_event=latest_reopen_event,
        prepared_by_name=prepared_by_name, reviewed_by_name=reviewed_by_name, approved_by_name=approved_by_name,
    )


@bp.route("/cash-closings/<uuid:closing_id>/submit", methods=["POST"])
@require_permission("cash_closing.prepare")
def submit_closing_route(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        cash_closing_services.submit_closing(
            closing, actor_staff_user_id=staff.id, actual_counted_cash=_to_decimal(request.form.get("actual_counted_cash")),
            variance_explanation=request.form.get("variance_explanation") or None,
        )
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.closing_detail", closing_id=closing_id))


@bp.route("/cash-closings/<uuid:closing_id>/decision", methods=["POST"])
@require_permission("cash_closing.approve")
def decide_closing_route(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        cash_closing_services.decide_closing(closing, decision=request.form.get("decision"), actor_staff_user_id=staff.id, reason=request.form.get("reason"))
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.closing_detail", closing_id=closing_id))


@bp.route("/cash-closings/<uuid:closing_id>/close", methods=["POST"])
@require_permission("cash_closing.approve")
def close_closing_route(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        cash_closing_services.close_closing(closing, actor_staff_user_id=staff.id)
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.closing_detail", closing_id=closing_id))


@bp.route("/cash-closings/<uuid:closing_id>/reopen", methods=["POST"])
@require_recent_auth
@require_permission("cash_closing.reopen")
def reopen_closing_route(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        cash_closing_services.reopen_closing(closing, actor_staff_user_id=staff.id, reason=request.form.get("reason", ""), recent_auth_verified=has_recent_auth())
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.closing_detail", closing_id=closing_id))


@bp.route("/cash-closings/<uuid:closing_id>/adjustments", methods=["POST"])
@require_permission("cash_closing.adjust")
def add_adjustment_route(closing_id):
    staff, profile = _actor()
    closing = db_session.get(CashClosing, closing_id)
    if closing is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        cash_closing_services.add_adjustment(closing, amount=_to_decimal(request.form.get("amount")), reason=request.form.get("reason", ""), created_by_staff_user_id=staff.id)
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.closing_detail", closing_id=closing_id))


# ----------------------------------------------------------------- Reports --

@bp.route("/report-snapshots", methods=["GET"])
@require_permission("report_snapshots.view")
def list_snapshots():
    report_type = request.args.get("report_type") or None
    stmt = select(ReportSnapshot).where(ReportSnapshot.status == "PUBLISHED")
    if report_type:
        stmt = stmt.where(ReportSnapshot.report_type == report_type)
    snapshots = db_session.execute(stmt.order_by(ReportSnapshot.period_start.desc()).limit(100)).scalars().all()
    return render_template("operations_ui/report_snapshots_list.html", snapshots=snapshots, report_type=report_type, report_types=REPORT_TYPES)


@bp.route("/report-snapshots/generate", methods=["POST"])
@require_permission("report_snapshots.view")
def generate_snapshot_route():
    staff, profile = _actor()
    try:
        report_scheduler.generate_snapshot(
            report_type=request.form.get("report_type"), period_start=date.fromisoformat(request.form["period_start"]),
            period_end=date.fromisoformat(request.form["period_end"]), currency=request.form.get("currency") or None,
            generated_by="MANUAL", generated_by_staff_user_id=staff.id,
        )
    except ValueError:
        flash("Invalid report request.", "error")
    return redirect(url_for("operations_ui.list_snapshots"))


@bp.route("/report-snapshots/<uuid:snapshot_id>/regenerate", methods=["POST"])
@require_permission("report_snapshots.regenerate")
def regenerate_snapshot_route(snapshot_id):
    staff, profile = _actor()
    snapshot = db_session.get(ReportSnapshot, snapshot_id)
    if snapshot is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        report_scheduler.regenerate_snapshot(
            report_type=snapshot.report_type, period_start=snapshot.period_start, period_end=snapshot.period_end,
            currency=snapshot.currency, actor_staff_user_id=staff.id, reason=request.form.get("reason", ""),
        )
    except ExpenseError as exc:
        flash(localize_expense_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.list_snapshots"))


# ----------------------------------------------------------- Mgmt Notes --

@bp.route("/management-notes", methods=["GET"])
@require_permission("management_notes.view")
def list_notes():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    notes = note_service.notes_visible_to(actor_employee_profile_id=profile.id if profile else None, has_manage_permission="management_notes.manage" in codes)
    return render_template("operations_ui/management_notes_list.html", notes=notes, codes=codes)


@bp.route("/management-notes/new", methods=["GET"])
@require_permission("management_notes.manage")
def new_note_form():
    return render_template("operations_ui/management_note_new.html", error=None)


@bp.route("/management-notes", methods=["POST"])
@require_permission("management_notes.manage")
def create_note_route():
    staff, profile = _actor()
    try:
        note = note_service.create_note(
            title=request.form.get("title", ""), body=request.form.get("body", ""), category=request.form.get("category") or None,
            priority=request.form.get("priority", "MEDIUM"), visibility=request.form.get("visibility", "MANAGEMENT_ONLY"),
            assigned_employee_profile_id=None, created_by_staff_user_id=staff.id,
        )
    except ManagementNoteError as exc:
        return render_template("operations_ui/management_note_new.html", error=localize_management_note_error(exc.code, **exc.params)), 400
    return redirect(url_for("operations_ui.note_detail", note_id=note.id))


def _note_or_none(note_id, staff, profile):
    codes = get_staff_permission_codes(staff)
    note = db_session.get(SharedManagementNote, note_id)
    if note is None:
        return None
    if not note_service.can_view_note(note, actor_employee_profile_id=profile.id if profile else None, has_manage_permission="management_notes.manage" in codes):
        return None
    return note


@bp.route("/management-notes/<uuid:note_id>", methods=["GET"])
@require_permission("management_notes.view")
def note_detail(note_id):
    staff, profile = _actor()
    note = _note_or_none(note_id, staff, profile)
    if note is None:
        return render_template("commercial_sales/not_found.html"), 404
    codes = get_staff_permission_codes(staff)
    comments = db_session.execute(
        select(ManagementNoteComment).where(ManagementNoteComment.management_note_id == note.id).order_by(ManagementNoteComment.created_at)
    ).scalars().all()
    return render_template("operations_ui/management_note_detail.html", note=note, codes=codes, comments=comments)


@bp.route("/management-notes/<uuid:note_id>/status", methods=["POST"])
@require_permission("management_notes.manage")
def set_note_status_route(note_id):
    staff, profile = _actor()
    note = db_session.get(SharedManagementNote, note_id)
    if note is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        note_service.set_note_status(note, target_status=request.form.get("status"), actor_staff_user_id=staff.id)
    except ManagementNoteError as exc:
        flash(localize_management_note_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.note_detail", note_id=note_id))


@bp.route("/management-notes/<uuid:note_id>/comments", methods=["POST"])
@require_permission("management_notes.view")
def add_comment_route(note_id):
    staff, profile = _actor()
    note = _note_or_none(note_id, staff, profile)
    if note is None:
        return render_template("commercial_sales/not_found.html"), 404
    try:
        note_service.add_comment(note, body=request.form.get("body", ""), author_staff_user_id=staff.id)
    except ManagementNoteError as exc:
        flash(localize_management_note_error(exc.code, **exc.params), "error")
    return redirect(url_for("operations_ui.note_detail", note_id=note_id))


# ------------------------------------------------------------- Dashboards --

@bp.route("/dashboard/employee-expenses", methods=["GET"])
@require_permission("dashboard.view_own")
def employee_expense_dashboard_view():
    from app.operational_reports.dashboards import employee_expense_dashboard

    staff, profile = _actor()
    if profile is None:
        return render_template("commercial_sales/not_found.html"), 404
    data = employee_expense_dashboard(profile.id)
    return render_template("operations_ui/dashboard_employee.html", data=data)


@bp.route("/dashboard/management-operations", methods=["GET"])
@require_permission("dashboard.view_all")
def management_operational_dashboard_view():
    from app.operational_reports.dashboards import management_operational_dashboard

    currency = request.args.get("currency", "USD")
    data = management_operational_dashboard(currency)
    return render_template("operations_ui/dashboard_management.html", data=data, currency=currency)


@bp.route("/dashboard/finance-operations", methods=["GET"])
@require_permission("dashboard.view_all")
def finance_operational_dashboard_view():
    from app.operational_reports.dashboards import finance_operational_dashboard

    currency = request.args.get("currency", "USD")
    data = finance_operational_dashboard(currency)
    return render_template("operations_ui/dashboard_finance.html", data=data, currency=currency)
