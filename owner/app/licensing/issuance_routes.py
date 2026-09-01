"""Direct license-issuance screen routes
(docs/owner/packages-and-issuance-design.md B.4 -- "Issue licence: three
acts"). A new, thin route module beside app/licensing/routes.py -- calls
only app.licensing.issuance.issue_license_direct, which itself calls only
existing canonical services. See that module's docstring for the
"orchestrate, never insert" contract and its one named gap (no whole-action
idempotency ledger beyond issue_license_key's own).

Permission: licenses.issue, the SAME permission licensing.routes::issue
already gates on -- reused deliberately rather than inventing a new one.
This screen's whole point is to reach the "press Issue" act; gating it on
anything weaker (e.g. licenses.create, which SALES already holds) would let
a role that cannot reveal a key through the existing detail-page flow reveal
one through this one instead, a real privilege-escalation shortcut around
an RBAC boundary Part S already draws deliberately. require_recent_auth
mirrors licensing.routes::issue exactly (Part F: MFA re-authentication on
issuance)."""
from __future__ import annotations

import uuid

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_babel import gettext as _
from sqlalchemy import select

from app.auth.session import has_recent_auth, load_current_staff
from app.commercial_sales.catalog_for_sales import describe_plan_for_sale
from app.extensions import db_session
from app.licensing.issuance import LicenseIssuanceError, issue_license_direct
from app.models.catalog import Plan
from app.models.customers import Customer
from app.security.rbac import require_permission, require_recent_auth

bp = Blueprint("license_issuance", __name__, url_prefix="/licenses/issuance")


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
        if described is not None:
            packages.append(described)
    return packages


def _customers_for_form() -> list[Customer]:
    return db_session.execute(
        select(Customer).where(Customer.archived_at.is_(None)).order_by(Customer.legal_name)
    ).scalars().all()


def _wants_json_error() -> bool:
    """True only for a genuine API caller -- a JSON request body, or an
    Accept header that asks for JSON and explicitly not HTML. False for
    this screen's own HTML form, which is the ONLY caller of this route
    today (verified by grepping owner/ for 'license_issuance.create' and
    '/licenses/issuance' -- nothing outside issuance.html and this module
    references it). A normal browser form POST always sends an Accept
    header that includes text/html, so it never gets misrouted into a raw
    JSON body here."""
    if request.is_json:
        return True
    return request.accept_mimetypes.accept_json and not request.accept_mimetypes.accept_html


def _render_issuance_form(*, error: str | None, idempotency_key: str, status: int = 200):
    """Re-renders the issuance form in place, error (if any) visible inline
    and everything the operator already typed preserved verbatim in
    `request.form` -- instead of the raw JSON error body the route used to
    return on a form POST, which the browser rendered as-is and which threw
    away every field the operator had just filled in. customers/packages
    must be re-fetched here: the success/JSON path never needed them
    (result is truthy there), but the form branch of the template always
    iterates them to build the two <select> lists."""
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


@bp.route("/new", methods=["GET"])
@require_permission("licenses.issue")
def new_form():
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
        result = issue_license_direct(
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
