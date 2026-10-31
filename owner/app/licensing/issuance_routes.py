"""Licence issuance screen routes
(docs/owner/packages-and-issuance-design.md B.4 -- "Issue licence: three
acts", reshaped by Step 0, 2026-10-24).

The one screen now SELLS a licence (commercial_sales/direct_sale.py):
  GET  /new                       the sale form -- customer, package, extra
                                  devices, seller, payment
  POST /sale                      records the sale + its PENDING payment; no key
  GET  /sales                     sales waiting for a manager (?sale=<order>
                                  is where a phone notification lands)
  GET  /sales/count               the managers' badge count, polled
  POST /sales/<order>/confirm     a manager confirms -> licence + key, shown once
  POST /sales/<order>/reject      the money did not arrive -> no licence
and keeps, for a licence nobody pays for, the old immediate path:
  GET  /no-charge, POST ""        SUPER_ADMIN only, written reason, audited
                                  (licensing/issuance.py::issue_no_charge_license)

Before Step 0 this screen only issued immediately, with no payment and no
seller, so a licence sold this way earned its seller nothing and was tied
to no money (measured on 829c4cde -- tests/test_issuance_sale.py).

Permissions, every one an existing seeded code (no new permission):
  - the sale form: orders.create + payments.create (SALES holds both --
    a seller records their own sale; SUPER_ADMIN holds all);
  - confirming: payments.confirm + orders.approve + invoices.issue (FINANCE,
    SUPER_ADMIN) and recent MFA, because confirming reveals the key -- the
    same reason the no-charge path needs require_recent_auth (Part F);
  - the no-charge path: licenses.issue, recent MFA AND is_super_admin.
The two-person rule (not the recorder, not the seller) is enforced in the
services, not here: hiding a button is not authorization.
"""
from __future__ import annotations

import uuid
from datetime import date

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select

from app.auth.session import has_recent_auth, load_current_staff
from app.commercial_sales.catalog_for_sales import describe_plan_for_sale, open_to_sellers
from app.commercial_sales.direct_sale import (
    CHOOSE_SELLER_PERMISSION,
    CONFIRM_SALE_PERMISSIONS,
    DIRECT_SALE_PAYMENT_METHODS,
    DirectSaleError,
    confirm_direct_sale,
    extra_device_pair_offer,
    list_sales_awaiting_confirmation,
    parse_amount,
    record_direct_sale,
    count_sales_awaiting_for,
    direct_sale_payment,
    reject_direct_sale,
    sale_is_waiting,
)
from app.commercial_sales.errors import CommercialSalesError
from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.i18n_labels import first_payment_breakdown, localize_commercial_sales_error, localize_direct_sale_error
from app.licensing.issuance import LicenseIssuanceError, issue_no_charge_license
from app.models.catalog import Plan
from app.models.customers import Customer
from app.models.commercial_sales import SalesOrder
from app.models.employees import EmployeeProfile
from app.models.staff import StaffUser
from app.operational_reports.periods import jordan_today
from app.push.vapid import keys_from_config
from app.security.rbac import get_staff_permission_codes, require_permission, require_recent_auth

bp = Blueprint("license_issuance", __name__, url_prefix="/licenses/issuance")

# One byte over the 10 MB limit is enough for the slip check to refuse it.
MAX_SLIP_READ_BYTES = 10 * 1024 * 1024 + 1


def _sellable_packages() -> list[dict]:
    """Plans a support/sales user may pick from this screen -- only ones
    describe_plan_for_sale itself considers currently sellable (real
    effective date, real current price), the same eligibility bar the sales
    pipeline already enforces. A half-configured plan (missing
    effective_date -- the exact trap spec section 0.5 names) simply does not
    appear here, rather than being offered and failing at Issue time."""
    plans = db_session.execute(select(Plan).order_by(Plan.name)).scalars().all()
    packages = []
    for plan in plans:
        described = describe_plan_for_sale(plan.id)
        if described is not None and open_to_sellers(described):
            packages.append(described)
    return packages


def _customers_for_form() -> list[Customer]:
    return db_session.execute(
        select(Customer).where(Customer.archived_at.is_(None)).order_by(Customer.legal_name)
    ).scalars().all()


def _customers_visible_to(actor, codes: set[str]) -> list[Customer]:
    """The sale form lists only customers the actor may see (the CRM
    own-scoping rule, customers.services.customer_visible_to_actor). The
    service re-checks a posted id, so a hand-edited form gains nothing."""
    stmt = select(Customer).where(Customer.archived_at.is_(None)).order_by(Customer.legal_name)
    if "customers.view_all" not in codes:
        stmt = stmt.where(Customer.assigned_sales_staff_id == actor.id)
    return db_session.execute(stmt).scalars().all()


def _active_sellers() -> list[EmployeeProfile]:
    return db_session.execute(
        select(EmployeeProfile)
        .where(EmployeeProfile.employment_status == "ACTIVE", EmployeeProfile.archived_at.is_(None))
        .order_by(EmployeeProfile.full_name)
    ).scalars().all()


def _wants_json_error() -> bool:
    """True only for a genuine API caller -- a JSON request body, or an
    Accept header that asks for JSON and explicitly not HTML. False for
    this screen's own HTML form. A normal browser form POST always sends an
    Accept header that includes text/html, so it never gets misrouted into
    a raw JSON body here."""
    if request.is_json:
        return True
    return request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html


# ------------------------------------------------------------ the sale form --

def _conversion_context(raw_subscription_id, actor, codes):
    """The trial a sale form is opened FOR (?convert=<subscription>), or None. A
    trial the actor cannot see answers like one that does not exist."""
    if not raw_subscription_id:
        return None
    from app.commercial_sales.direct_sale import _resolve_conversion

    try:
        subscription = _resolve_conversion(raw_subscription_id, "-", actor.id, codes)
    except DirectSaleError:
        return None
    return {
        "subscription_id": subscription.id,
        "customer": db_session.get(Customer, subscription.customer_id),
        "plan": db_session.get(Plan, subscription.plan_id),
    }


def _render_sale_form(*, error: str | None, idempotency_key: str, form_values=None, recorded=None, status: int = 200):
    actor = load_current_staff()
    codes = get_staff_permission_codes(actor)
    convert_raw = (form_values or {}).get("converts_subscription_id") if form_values else None
    convert = _conversion_context(convert_raw or request.args.get("convert"), actor, codes)
    may_choose_seller = CHOOSE_SELLER_PERMISSION in codes
    packages = _sellable_packages()
    for package in packages:
        offer = extra_device_pair_offer(uuid.UUID(package["product_id"]))
        package["extra_pair_price"] = offer["unit_price"] if offer and offer["currency"] == package["currency"] else None
        # What the customer pays first (the first period + any first-time fee) and what that is
        # made of -- the trial-conversion page pre-fills the amount with it and says so, the way
        # 7e223f38 did for the other payment screens. For a trial nothing has been paid yet, so
        # this is exactly packages.next_payment_due for its subscription.
        package["breakdown"] = first_payment_breakdown(package)
    return render_template(
        "licensing/sale.html",
        customers=_customers_visible_to(actor, codes),
        packages=packages,
        sellers=_active_sellers() if may_choose_seller else [],
        may_choose_seller=may_choose_seller,
        own_profile=find_own_profile(actor.id),
        payment_methods=DIRECT_SALE_PAYMENT_METHODS,
        today=jordan_today().isoformat(),  # the form's default and max: Jordan's date
        error=error,
        form_values=form_values,
        recorded=recorded,
        sale_idempotency_key=idempotency_key,
        can_issue_no_charge=bool(actor.is_super_admin),
        can_confirm="payments.confirm" in codes,
        convert=convert,
        prefill_amount=(
            next((p["first_sale_unit_price"] for p in packages
                  if str(p["plan_id"]) == str((form_values or {}).get("plan_id") or convert["plan"].id)), None)
            if convert and not (form_values or {}).get("amount") else None
        ),
        convert_plan_id=str(convert["plan"].id) if convert else None,
        sent_from_sale=bool(convert) and request.args.get("from_sale") == "1",
    ), status


@bp.route("/new", methods=["GET"])
@require_permission("orders.create")
@require_permission("payments.create")
def new_form():
    return _render_sale_form(error=None, idempotency_key=str(uuid.uuid4()))


def _sale_error(message: str, idempotency_key: str):
    # The form keeps its key, so the corrected resubmit is the SAME sale
    # (direct_sale.py refuses any write until every check passes).
    return _render_sale_form(error=message, idempotency_key=idempotency_key, form_values=request.form, status=400)


@bp.route("/sale", methods=["POST"])
@require_permission("orders.create")
@require_permission("payments.create")
def record_sale():
    actor = load_current_staff()
    form = request.form
    idempotency_key = form.get("idempotency_key")
    if not idempotency_key:
        return _sale_error(_("An idempotency key is required to record a sale."), str(uuid.uuid4()))
    # Step 0 packages: extra devices are sold only as PAIRS (1 desktop + 1
    # phone). The old single-device field is no longer read: a form posting
    # it sells no device.
    try:
        extra_device_pairs = int(form.get("extra_device_pairs") or "0")
    except ValueError:
        return _sale_error(localize_direct_sale_error("EXTRA_PAIRS_NOT_A_NUMBER"), idempotency_key)
    try:
        payment_date = date.fromisoformat(form.get("payment_date") or "")
    except ValueError:
        return _sale_error(_("Type the date the money arrived."), idempotency_key)
    slip = request.files.get("slip_photo")
    slip_bytes = slip.read(MAX_SLIP_READ_BYTES) if slip is not None and slip.filename else b""
    try:
        amount = parse_amount(form.get("amount") or "")
        result = record_direct_sale(
            converts_subscription_id=form.get("converts_subscription_id") or None,
            slip_photo=slip_bytes or None,
            require_slip_unless_cash=True,
            customer_id=form.get("customer_id") or None,
            new_customer_legal_name=form.get("new_customer_legal_name") or None,
            plan_id=form.get("plan_id") or None,
            extra_device_pairs=extra_device_pairs,
            sales_owner_employee_profile_id=form.get("sales_owner_employee_profile_id") or None,
            amount=amount,
            method=form.get("method") or "",
            reference=form.get("reference") or "",
            payment_date=payment_date,
            idempotency_key=idempotency_key,
            actor_staff_user_id=actor.id,
            actor_permission_codes=get_staff_permission_codes(actor),
        )
    except DirectSaleError as exc:
        if exc.code == "CUSTOMER_HAS_LIVE_TRIAL":
            # Not an error screen: take the seller to the page that does this right.
            return redirect(url_for("license_issuance.new_form", convert=exc.params["subscription_id"], from_sale="1"))
        return _sale_error(localize_direct_sale_error(exc.code, **exc.params), idempotency_key)
    except CommercialSalesError as exc:
        return _sale_error(localize_commercial_sales_error(exc.code, **exc.params), idempotency_key)
    return _render_sale_form(error=None, idempotency_key=str(uuid.uuid4()), recorded=result)


# ------------------------------------------------------- the manager side --

def _push_card():
    """The "phone notifications" card: only while push is configured and only
    for someone the sender would ever notify (app/push/recipients.py)."""
    from app.push.recipients import staff_may_confirm_sales

    keys = keys_from_config(current_app.config)
    if keys is None or not staff_may_confirm_sales(load_current_staff()):
        return None
    return {
        "public_key": keys.public_key_b64,
        "subscribe_url": url_for("push.subscribe"),
        "unsubscribe_url": url_for("push.unsubscribe"),
    }


def _tapped_sale(raw_order_id):
    """Where a phone notification lands (?sale=<order>). The confirm is
    already race-safe -- the first manager wins -- so a second manager tapping
    the same notification must be TOLD the sale is done, not left looking for
    a row that silently vanished from the list."""
    if not raw_order_id:
        return None
    try:
        order = db_session.get(SalesOrder, uuid.UUID(str(raw_order_id)))
    except ValueError:
        return None
    payment = direct_sale_payment(order) if order is not None else None
    if order is None or payment is None:
        return None
    if sale_is_waiting(order.id):
        return {"state": "waiting", "order_id": order.id, "order_number": order.order_number}
    if order.status == "FULFILLED" or payment.status == "CONFIRMED":
        confirmer = db_session.get(StaffUser, payment.verified_by_staff_user_id) if payment.verified_by_staff_user_id else None
        return {"state": "confirmed", "order_id": order.id, "order_number": order.order_number,
                "by": confirmer.display_name if confirmer is not None else None}
    return {"state": "rejected", "order_id": order.id, "order_number": order.order_number}


def _account_prompt(confirmed):
    """Step 2 CA1: after a sale is confirmed, remind the confirmer when the customer
    has no website account yet (never creates one by itself)."""
    if not confirmed or not confirmed.get("order_id"):
        return None
    from app.customer_accounts.staff_routes import prompt_for_order

    actor = load_current_staff()
    return prompt_for_order(confirmed["order_id"], actor, get_staff_permission_codes(actor))


def _render_sales_list(*, error: str | None = None, confirmed=None, status: int = 200, tapped=None):
    return render_template(
        "licensing/sales_pending.html",
        sales=list_sales_awaiting_confirmation(),
        error=error,
        confirmed=confirmed,
        recent_auth_ok=has_recent_auth(),
        tapped=tapped,
        account_prompt=_account_prompt(confirmed),
        push_card=_push_card(),
    ), status


@bp.route("/sales", methods=["GET"])
@require_permission("payments.confirm")
def sales_awaiting_confirmation():
    return _render_sales_list(tapped=_tapped_sale(request.args.get("sale")))


@bp.route("/sales/count", methods=["GET"])
def sales_waiting_count():
    """Polled every 30 s by static/js/count-badge.js (the managers' badge).
    load_current_staff(touch=False), like attention.website_leads_count: the
    session is validated as usual but its idle timer is NOT pushed forward, so
    a tab left open still times out. Only for someone who may confirm a sale;
    the count leaves out the sales they recorded or sold."""
    staff = load_current_staff(touch=False)
    if staff is None:
        status, payload = 401, {"error": "authentication_required"}
    elif not CONFIRM_SALE_PERMISSIONS <= get_staff_permission_codes(staff):
        status, payload = 403, {"error": "forbidden"}
    else:
        status, payload = 200, {"count": count_sales_awaiting_for(staff.id)}
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/sales/<uuid:order_id>/confirm", methods=["POST"])
@require_permission("payments.confirm")
@require_permission("orders.approve")
@require_permission("invoices.issue")
@require_recent_auth
def confirm_sale(order_id):
    actor = load_current_staff()
    try:
        result = confirm_direct_sale(
            order_id, actor_staff_user_id=actor.id, license_pepper=current_app.config["LICENSE_PEPPER"]
        )
    except DirectSaleError as exc:
        return _render_sales_list(error=localize_direct_sale_error(exc.code, **exc.params), status=400)
    except CommercialSalesError as exc:
        return _render_sales_list(error=localize_commercial_sales_error(exc.code, **exc.params), status=400)
    # Rendered inline, never redirected: a redirect would lose the
    # once-shown key (ADR-9), the same reason the no-charge path and
    # commercial_sales_web.fulfill_order_route render inline.
    return _render_sales_list(confirmed=result)


@bp.route("/sales/<uuid:order_id>/reject", methods=["POST"])
@require_permission("payments.confirm")
@require_permission("orders.approve")
def reject_sale(order_id):
    actor = load_current_staff()
    try:
        order = reject_direct_sale(order_id, reason=request.form.get("reason", ""), actor_staff_user_id=actor.id)
    except DirectSaleError as exc:
        return _render_sales_list(error=localize_direct_sale_error(exc.code, **exc.params), status=400)
    except CommercialSalesError as exc:
        return _render_sales_list(error=localize_commercial_sales_error(exc.code, **exc.params), status=400)
    # Say it happened: the row simply vanished from the list before (rehearsal 2026-10-31), which
    # looked like nothing had been done.
    if order.converts_subscription_id is not None:
        flash(_("Sale %(order)s was rejected. The shop's trial stays as it was, and the seller can record the payment again.",
                order=order.order_number), "success")
    else:
        flash(_("Sale %(order)s was rejected. No licence was made.", order=order.order_number), "success")
    return redirect(url_for("license_issuance.sales_awaiting_confirmation"))


# --------------------------------------------------- the no-charge licence --

def _render_issuance_form(*, error: str | None, idempotency_key: str, status: int = 200):
    """Re-renders the no-charge form in place, error (if any) visible inline
    and everything the operator already typed preserved verbatim in
    `request.form` -- instead of the raw JSON error body the route used to
    return on a form POST, which the browser rendered as-is and which threw
    away every field the operator had just filled in."""
    return render_template(
        "licensing/issuance.html",
        customers=_customers_for_form(),
        packages=_sellable_packages(),
        result=None,
        error=error,
        form_values=request.form,
        issue_idempotency_key=idempotency_key,
        recent_auth_ok=has_recent_auth(),
    ), status


def _require_super_admin():
    # licenses.issue alone is not enough: it is a grantable permission, and
    # a custom role holding it must not be a way to give paid licences away.
    if not load_current_staff().is_super_admin:
        if _wants_json_error():
            return jsonify({"error": "forbidden"}), 403
        abort(403)
    return None


@bp.route("/no-charge", methods=["GET"])
@require_permission("licenses.issue")
def no_charge_form():
    refused = _require_super_admin()
    if refused is not None:
        return refused
    return render_template(
        "licensing/issuance.html",
        customers=_customers_for_form(),
        packages=_sellable_packages(),
        result=None,
        error=None,
        form_values=None,
        issue_idempotency_key=str(uuid.uuid4()),
        recent_auth_ok=has_recent_auth(),
    )


@bp.route("", methods=["POST"])
@require_permission("licenses.issue")
@require_recent_auth
def create():
    refused = _require_super_admin()
    if refused is not None:
        return refused
    actor = load_current_staff()
    idempotency_key = request.form.get("idempotency_key") or request.headers.get("Idempotency-Key")
    if not idempotency_key:
        if _wants_json_error():
            return jsonify({"error": "idempotency_key_required"}), 400
        # Nothing was submitted to key a retry on -- give the re-rendered
        # form a fresh token rather than an empty hidden field.
        return _render_issuance_form(
            error=_("An idempotency key is required to issue a license."),
            idempotency_key=str(uuid.uuid4()), status=400,
        )
    try:
        extra_devices = int(request.form.get("extra_devices") or "0")
    except ValueError:
        if _wants_json_error():
            return jsonify({"error": "invalid_extra_devices"}), 400
        return _render_issuance_form(
            error=_("Extra devices must be a whole number."),
            idempotency_key=idempotency_key, status=400,
        )

    try:
        result = issue_no_charge_license(
            reason=request.form.get("no_charge_reason"),
            customer_id=request.form.get("customer_id") or None,
            new_customer_legal_name=request.form.get("new_customer_legal_name") or None,
            plan_id=request.form.get("plan_id"),
            extra_devices=extra_devices,
            idempotency_key=idempotency_key,
            actor_staff_user_id=actor.id,
            license_pepper=current_app.config["LICENSE_PEPPER"],
        )
    except LicenseIssuanceError as exc:
        if _wants_json_error():
            return jsonify({"error": str(exc)}), 400
        # exc's message is already a full, operator-facing sentence (see
        # LicenseIssuanceError's own docstring) -- safe to show as-is,
        # exactly like the JSON path already did with str(exc).
        return _render_issuance_form(error=str(exc), idempotency_key=idempotency_key, status=400)

    # full_key/whatsapp_message are shown exactly once, in this response
    # only -- there is no detail-style GET for this screen that could ever
    # re-render them (mirrors licensing.routes::issue's own "reload never
    # re-shows it" property, see licensing/detail.html).
    return render_template(
        "licensing/issuance.html",
        customers=[],
        packages=[],
        result=result,
        error=None,
        form_values=None,
        issue_idempotency_key=str(uuid.uuid4()),
        recent_auth_ok=has_recent_auth(),
    )
