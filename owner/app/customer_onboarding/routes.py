"""Staff routes of the new customer flow (slice S2).

  GET  /customers/new-trial                      the seller's form (mobile first)
  POST /customers/new-trial                      the one action; the result page is
                                                 rendered inline (never redirected):
                                                 the licence key and the e-mail
                                                 link are shown ONCE
  GET  /customers/<id>/documents/<doc_id>       the ID / registration photo, for a
                                                 Sales Manager or Super Admin only
  POST /customers/<id>/trial/extend              one extension, <= 7 days, with a reason

OFF unless OWNER_CUSTOMER_ONBOARDING_ENABLED=true: every route answers 404 like a
route that does not exist. Permissions (all in staff/seed_data.py):
  customers.onboard            the form (SALES, SALES_MANAGER, SUPER_ADMIN)
  trials.override_duplicate    press through a duplicate shop, with a reason
  trials.extend                give a trial 1-7 more days, once (+ recent MFA)
  customer_documents.view      read an ID / registration photo (+ audited)
"""
from __future__ import annotations

import uuid

from flask import Blueprint, Response, abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

from app.audit.services import record as audit_record
from app.auth.session import load_current_staff
from app.customer_onboarding import documents, service, trial
from app.customers.services import customer_visible_to_actor
from app.extensions import db_session
from app.i18n_labels import localize_onboarding_error
from app.models.customer_onboarding import CustomerDocument
from app.models.customers import Customer
from app.security.rbac import get_staff_permission_codes, require_login, require_permission, require_recent_auth

bp = Blueprint("customer_onboarding", __name__, url_prefix="/customers")

# Two photos of up to 10 MB each plus the form: above Flask's global 12 MB
# ceiling, so this one route raises its own (request.max_content_length is
# settable per request since Werkzeug 3.1).
ONBOARDING_MAX_REQUEST_BYTES = 2 * documents.MAX_DOCUMENT_BYTES + 1024 * 1024


def _enabled() -> bool:
    return bool(current_app.config.get("CUSTOMER_ONBOARDING_ENABLED"))


@bp.url_value_preprocessor
def _raise_the_body_limit(endpoint, values):
    """Runs after the URL is matched and BEFORE any before_request hook -- notably
    before CSRFProtect, which reads request.form and so parses (and size-checks) the
    whole multipart body. Raising the limit later, inside the view, would be too
    late: two 7 MB photos would already have been refused at the global 12 MB
    ceiling. Only this one POST, and only while the feature is on."""
    if endpoint == "customer_onboarding.create" and request.method == "POST" and _enabled():
        request.max_content_length = ONBOARDING_MAX_REQUEST_BYTES


@bp.before_request
def _gate():
    # The form and the trial extension exist only while the feature is on. Opening a
    # stored document does not depend on the flag: a payment slip can be attached to an
    # ordinary sale too, and the confirming manager must be able to open it either way
    # (the document route has its own permission check).
    if request.endpoint != "customer_onboarding.view_document" and not _enabled():
        abort(404)


def _form_context(*, error=None, values=None, key=None, status=200):
    actor = load_current_staff()
    codes = get_staff_permission_codes(actor)
    return render_template(
        "customer_onboarding/new.html",
        packages=service.sellable_packages(),
        form_key=key or str(uuid.uuid4()),
        error=error,
        values=values or {},
        may_override=service.OVERRIDE_PERMISSION in codes,
        kyc_ready=documents.storage_is_ready(),
        # The form can carry the temporary password the seller typed (kept after a refusal), so a browser
        # or proxy must never store the page.
    ), status, {"Cache-Control": "no-store"}


@bp.route("/new-trial", methods=["GET"])
@require_permission("customers.onboard")
def new_form():
    return _form_context()


def _uploaded(name: str) -> bytes:
    storage = request.files.get(name)
    if storage is None or not storage.filename:
        return b""
    return storage.read(documents.MAX_DOCUMENT_BYTES + 1)  # one byte over the limit is enough to refuse it


@bp.route("/new-trial", methods=["POST"])
@require_permission("customers.onboard")
def create():
    actor = load_current_staff()
    form = request.form
    key = form.get("idempotency_key") or ""
    # The form's text is handed back on a refusal, the temporary password included: the seller
    # typed it (or made one) and has probably already told the customer -- blanking it made them
    # invent and tell a different one (rehearsal 2026-10-31). The page is served no-store. The
    # photos cannot be handed back (a browser never refills a file input); the form says so.
    echo = {k: v for k, v in form.items() if k != "csrf_token"}
    try:
        result = service.create_trial_customer(
            idempotency_key=key,
            business_name=form.get("business_name", ""),
            contact_name=form.get("contact_name"),
            email=form.get("email", ""),
            temp_password=form.get("temp_password", ""),
            phone=form.get("phone", ""),
            address=form.get("address", ""),
            city=form.get("city"),
            latitude=form.get("latitude") or None,
            longitude=form.get("longitude") or None,
            accuracy_meters=form.get("accuracy_meters") or None,
            cr_number=form.get("cr_number", ""),
            plan_id=form.get("plan_id"),
            product_code=form.get("product_code") or service.DEFAULT_PRODUCT_CODE,
            language=form.get("language", "ar"),
            consent=form.get("consent") == "1",
            id_photo=_uploaded("id_photo"),
            cr_photo=_uploaded("cr_photo"),
            override_duplicate=form.get("override_duplicate") == "1",
            override_reason=form.get("override_reason"),
            actor_staff_user_id=actor.id,
            actor_permission_codes=get_staff_permission_codes(actor),
            license_pepper=current_app.config["LICENSE_PEPPER"],
        )
    except service.OnboardingError as exc:
        # Keep the key: the corrected resubmit is the SAME form (nothing was written).
        return _form_context(error=localize_onboarding_error(exc.code, **exc.params), values=echo,
                             key=key or None, status=400)
    # The temporary password is shown ONCE on this page, from what was just typed; a replayed form shows
    # nothing (it may carry a different password from the one the account was made with).
    response = render_template(
        "customer_onboarding/result.html", result=result, customer=db_session.get(Customer, result.customer_id),
        temp_password=None if result.replayed else form.get("temp_password", ""),
    )
    return response, 200, {"Cache-Control": "no-store"}


@bp.route("/<uuid:customer_id>/documents/<uuid:document_id>", methods=["GET"])
@require_login
def view_document(customer_id, document_id):
    # Who may open what: IDs and registrations need customer_documents.view (Sales
    # Manager, Super Admin). A PAYMENT SLIP is also open to anyone who may confirm
    # payments (Finance): confirming a payment means looking at its slip, and a slip
    # is a bank paper, not an identity document. The seller who took the photo can open neither.
    actor = load_current_staff()
    codes = get_staff_permission_codes(actor)
    document = db_session.get(CustomerDocument, document_id)
    may_open = "customer_documents.view" in codes or (
        document is not None and document.kind == "PAYMENT_SLIP" and "payments.confirm" in codes
    )
    if not may_open:
        abort(403)
    if document is None or document.customer_id != customer_id:
        abort(404)
    try:
        content, content_type = documents.read_document(document)
    except documents.DocumentError:
        abort(404)
    audit_record(
        actor_staff_user_id=actor.id, actor_role_snapshot=None, action_code="CUSTOMER_DOCUMENT_VIEWED",
        entity_type="customer_document", entity_public_id=str(document.id),
        after_state={"kind": document.kind, "customer_id": str(customer_id)},
    )
    response = Response(content, mimetype=content_type)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["Content-Disposition"] = "inline"
    return response


@bp.route("/<uuid:customer_id>/trial/extend", methods=["POST"])
@require_permission("trials.extend")
@require_recent_auth
def extend(customer_id):
    actor = load_current_staff()
    codes = get_staff_permission_codes(actor)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, actor.id, codes):
        abort(404)
    pilot = trial.trial_pilot_for_customer(customer_id)
    if pilot is None:
        abort(404)
    try:
        days = int(request.form.get("days", ""))
    except ValueError:
        days = 0
    try:
        trial.extend_trial(pilot, days=days, reason=request.form.get("reason", ""), actor_staff_user_id=actor.id)
    except trial.TrialError as exc:
        flash(localize_onboarding_error(exc.code), "error")
    else:
        flash(_("The trial was extended."), "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))
