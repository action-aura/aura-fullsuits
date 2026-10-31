"""Staff pages for the e-contract (new customer flow, S5).

  GET  /contracts                        all versions + the "new version" form   (contracts.manage)
  POST /contracts/new                    create a version (text, optional PDF)
  GET  /contracts/<id>                   one version: text, status, who accepted
  POST /contracts/<id>/approve           record that a lawyer read THIS text
  POST /contracts/<id>/publish           make it the live one (+ recent MFA)
  POST /contracts/<id>/retire            stop showing it (+ recent MFA)
  GET  /contracts/<id>/acceptances.json  evidence export for one version
  GET  /contracts/evidence/customer/<customer_id>.json
  GET  /contracts/evidence/seller/<staff_id>.json
  GET  /contracts/<id>/pdf               the archived lawyer's PDF (decrypted, no-store)
  GET  /contracts/seller                 a SELLER reads and accepts the live seller contract
  POST /contracts/seller/accept

Everything but the last two is Super Admin only (permission contracts.manage, which
only the Super Admin role holds). Publishing a version a lawyer has not approved is
refused unless the server runs with OWNER_CONTRACT_ALLOW_DRAFT=1 (practice only).
"""
from __future__ import annotations

import json

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

from app.audit.services import record as audit_record
from app.auth.session import has_recent_auth, load_current_staff
from app.contracts import service
from app.extensions import db_session
from app.models.staff import StaffUser
from app.security.rbac import require_login, require_permission, require_recent_auth

bp = Blueprint("contracts", __name__, url_prefix="/contracts")


def _message(code: str) -> str:
    # Literal strings so the message extractor sees every one.
    if code == "AUDIENCE_INVALID":
        return _("Choose who this contract is for.")
    if code == "LANGUAGE_INVALID":
        return _("Choose English or Arabic.")
    if code == "LABEL_INVALID":
        return _("The version name may only use letters, numbers, dots, dashes and spaces.")
    if code == "LABEL_TAKEN":
        return _("A version with that name already exists for this audience and language.")
    if code == "TITLE_INVALID":
        return _("Write a title.")
    if code == "BODY_REQUIRED":
        return _("Write the contract text.")
    if code == "BODY_TOO_LONG":
        return _("The contract text is too long.")
    if code == "APPROVAL_NOTE_REQUIRED":
        return _("Write who approved it and when (for example the lawyer's name and date).")
    if code == "NOT_LAWYER_APPROVED":
        return _("This version is not marked as approved by a lawyer, so it cannot be published.")
    if code == "ALREADY_PUBLISHED":
        return _("This version is already published.")
    if code == "NOT_PUBLISHED":
        return _("This version was never published.")
    if code in ("PDF_REQUIRED", "PDF_TOO_LARGE"):
        return _("The attached file must be a PDF of at most 10 MB.")
    if code == "KYC_NOT_CONFIGURED":
        return _("The encryption key for stored files is not set up on this server, so a PDF cannot be stored.")
    return _("Something went wrong. Nothing was changed.")


def _version_or_404(version_id):
    version = service.get_version(version_id)
    if version is None:
        abort(404)
    return version


def _json_download(payload: dict, filename: str) -> Response:
    response = Response(json.dumps(payload, ensure_ascii=False, indent=2), mimetype="application/json")
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _audit_export(what: str, entity_id: str, actor) -> None:
    audit_record(
        actor_staff_user_id=actor.id, actor_role_snapshot=None, action_code="CONTRACT_EVIDENCE_EXPORTED",
        entity_type="contract_evidence", entity_public_id=entity_id, after_state={"what": what},
    )


@bp.route("", methods=["GET"])
@require_permission("contracts.manage")
def index():
    return render_template("contracts/index.html", versions=service.list_versions(), draft_allowed=service.allow_draft_publish(),
                           recent_auth_ok=has_recent_auth())


@bp.route("/new", methods=["POST"])
@require_permission("contracts.manage")
def create():
    actor = load_current_staff()
    upload = request.files.get("pdf")
    pdf_bytes = upload.read() if upload and upload.filename else None
    try:
        version = service.create_version(
            audience=request.form.get("audience", ""), language=request.form.get("language", ""),
            version_label=request.form.get("version_label", ""), title=request.form.get("title", ""),
            body=request.form.get("body", ""), lawyer_approved=request.form.get("lawyer_approved") == "on",
            approval_note=request.form.get("approval_note", ""), pdf_bytes=pdf_bytes, actor_staff_user_id=actor.id,
        )
    except service.ContractError as exc:
        flash(_message(exc.code), "error")
        return redirect(url_for("contracts.index"))
    flash(_("Version saved as a draft. Publish it when it is ready."), "success")
    return redirect(url_for("contracts.detail", version_id=version.id))


@bp.route("/<uuid:version_id>", methods=["GET"])
@require_permission("contracts.manage")
def detail(version_id):
    version = _version_or_404(version_id)
    return render_template("contracts/detail.html", version=version, acceptances=service.acceptances_of_version(version.id),
                           draft_allowed=service.allow_draft_publish(), recent_auth_ok=has_recent_auth())


@bp.route("/<uuid:version_id>/approve", methods=["POST"])
@require_permission("contracts.manage")
def approve(version_id):
    version = _version_or_404(version_id)
    try:
        service.approve(version, note=request.form.get("approval_note", ""), actor_staff_user_id=load_current_staff().id)
    except service.ContractError as exc:
        flash(_message(exc.code), "error")
    else:
        flash(_("Marked as approved by a lawyer."), "success")
    return redirect(url_for("contracts.detail", version_id=version.id))


@bp.route("/<uuid:version_id>/publish", methods=["POST"])
@require_permission("contracts.manage")
@require_recent_auth
def publish(version_id):
    version = _version_or_404(version_id)
    try:
        service.publish(version, actor_staff_user_id=load_current_staff().id)
    except service.ContractError as exc:
        flash(_message(exc.code), "error")
    else:
        flash(_("Published. New sign-ins are asked to accept this text."), "success")
    return redirect(url_for("contracts.detail", version_id=version.id))


@bp.route("/<uuid:version_id>/retire", methods=["POST"])
@require_permission("contracts.manage")
@require_recent_auth
def retire(version_id):
    version = _version_or_404(version_id)
    try:
        service.retire(version, actor_staff_user_id=load_current_staff().id)
    except service.ContractError as exc:
        flash(_message(exc.code), "error")
    else:
        flash(_("Retired. It is no longer shown to anyone."), "success")
    return redirect(url_for("contracts.detail", version_id=version.id))


@bp.route("/<uuid:version_id>/acceptances.json", methods=["GET"])
@require_permission("contracts.manage")
def version_evidence(version_id):
    version = _version_or_404(version_id)
    payload = service._evidence(service.acceptances_of_version(version.id), {"kind": "version", "version_id": str(version.id)})
    _audit_export("version", str(version.id), load_current_staff())
    return _json_download(payload, f"contract-{version.version_label}-acceptances.json")


@bp.route("/evidence/customer/<uuid:customer_id>.json", methods=["GET"])
@require_permission("contracts.manage")
def customer_evidence(customer_id):
    _audit_export("customer", str(customer_id), load_current_staff())
    return _json_download(service.evidence_for_customer(customer_id), f"contract-evidence-customer-{customer_id}.json")


@bp.route("/evidence/seller/<uuid:staff_user_id>.json", methods=["GET"])
@require_permission("contracts.manage")
def seller_evidence(staff_user_id):
    if db_session.get(StaffUser, staff_user_id) is None:
        abort(404)
    _audit_export("seller", str(staff_user_id), load_current_staff())
    return _json_download(service.evidence_for_seller(staff_user_id), f"contract-evidence-seller-{staff_user_id}.json")


@bp.route("/<uuid:version_id>/pdf", methods=["GET"])
@require_permission("contracts.manage")
def pdf(version_id):
    version = _version_or_404(version_id)
    try:
        content = service.read_pdf(version)
    except service.ContractError:
        abort(404)
    audit_record(
        actor_staff_user_id=load_current_staff().id, actor_role_snapshot=None, action_code="CONTRACT_PDF_VIEWED",
        entity_type="contract_version", entity_public_id=str(version.id),
    )
    response = Response(content, mimetype="application/pdf")
    response.headers["Content-Disposition"] = f'attachment; filename="contract-{version.version_label}.pdf"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


# ---- the seller's own acceptance (any signed-in staff member) ----

@bp.route("/seller", methods=["GET"])
@require_login
def seller_page():
    actor = load_current_staff()
    version = service.live_version_for("SELLER", getattr(actor, "locale", None))
    accepted = bool(version and service.seller_has_accepted(actor.id, version.id))
    return render_template("contracts/seller.html", version=version, accepted=accepted)


@bp.route("/seller/accept", methods=["POST"])
@require_login
def seller_accept():
    actor = load_current_staff()
    version = service.get_version(request.form.get("version_id", ""))
    if version is None or version.audience != "SELLER":
        abort(404)
    # The seller ticks the box next to THE TEXT THEY SAW: the page posts its hash, and a
    # version that was replaced while they were reading refuses instead of recording
    # an acceptance of words they did not read.
    if request.form.get("agree") != "on" or request.form.get("text_sha256", "").strip().lower() != version.body_sha256:
        flash(_("Read the contract and tick the box to accept it."), "error")
        return redirect(url_for("contracts.seller_page"))
    try:
        service.record_seller_acceptance(staff=actor, version=version, ip=request.remote_addr)
    except service.ContractError:
        flash(_("This contract is no longer current. Please read the new one."), "error")
    else:
        flash(_("Thank you. Your acceptance is recorded."), "success")
    return redirect(url_for("contracts.seller_page"))
