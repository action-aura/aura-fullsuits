"""Seller ID photo pages.

  GET  /seller-id                  the seller's "Upload your ID photo" page; once a photo is
                                   on file it only says "ID photo on file since <date>"
  POST /seller-id/upload           the seller uploads (only while none is on file)
  GET  /seller-id/documents/<id>   open a photo -- staff_documents.view only, audited

A seller can NEVER download a photo, not even their own: the only route that returns the
bytes needs staff_documents.view, which the SALES role does not hold. Replacing a photo
that is already on file is a manager action (the edit-employee form): a stolen seller
session must not be able to swap the ID the company holds.
"""
from __future__ import annotations

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for

from app.audit.services import record as audit_record
from app.auth.session import load_current_staff
from app.contracts import service as contract_service
from app.extensions import db_session
from app.models.staff_identity import StaffIdentityDocument
from app.security.rbac import require_login, require_permission
from app.seller_id import documents, service
from app.seller_id.messages import photo_error_message

bp = Blueprint("seller_id", __name__, url_prefix="/seller-id")


@bp.route("", methods=["GET"])
@require_login
def upload_page():
    staff = load_current_staff()
    if not contract_service.staff_is_bound_by_seller_agreement(staff):
        return redirect(url_for("dashboard.index"))
    return render_template(
        "seller_id/upload.html", on_file=documents.current_photo(staff.id), required=request.args.get("required") == "1",
        storage_ready=documents.storage_is_ready(), error=None,
    )


@bp.route("/upload", methods=["POST"])
@require_login
def upload():
    staff = load_current_staff()
    if not contract_service.staff_is_bound_by_seller_agreement(staff):
        return redirect(url_for("dashboard.index"))
    if documents.current_photo(staff.id) is not None:
        flash(photo_error_message("ALREADY_ON_FILE"), "error")
        return redirect(url_for("seller_id.upload_page"), code=303)
    upload_file = request.files.get("id_photo")
    content = upload_file.read() if upload_file and upload_file.filename else b""
    try:
        if not content:
            raise service.PhotoError("NO_FILE")
        service.save_photo(content=content, uploaded_by_staff_user_id=staff.id, staff_user_id=staff.id)
    except service.PhotoError as exc:
        return render_template(
            "seller_id/upload.html", on_file=None, required=True, storage_ready=documents.storage_is_ready(),
            error=photo_error_message(exc.code, **exc.params),
        ), 400
    return redirect(url_for("dashboard.index"), code=303)


@bp.route("/documents/<uuid:document_id>", methods=["GET"])
@require_permission("staff_documents.view")
def view_photo(document_id):
    actor = load_current_staff()
    document = db_session.get(StaffIdentityDocument, document_id)
    if document is None:
        abort(404)
    try:
        content, content_type = documents.read_photo(document)
    except documents.DocumentError:
        abort(404)
    audit_record(
        actor_staff_user_id=actor.id, actor_role_snapshot=None, action_code=service.VIEWED_ACTION,
        entity_type=service.ENTITY_TYPE, entity_public_id=str(document.id),
        after_state={
            "staff_user_id": str(document.staff_user_id) if document.staff_user_id else None,
            "invitation_id": str(document.invitation_id) if document.invitation_id else None,
        },
    )
    response = Response(content, mimetype=content_type)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["Content-Disposition"] = "inline"
    return response
