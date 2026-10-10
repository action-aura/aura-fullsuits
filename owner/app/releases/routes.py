"""Release/version routes: the read-only views over owner_product_versions /
owner_release_channels (Part I), plus the staff "publish a new version" flow
(upload a DRAFT, then publish / withdraw it).

Permission: viewing stays catalog.view. Changing anything needs
catalog.manage_versions ("Manage product versions/releases" in
app/staff/seed_data.py) -- the most specific permission the RBAC seed has for
this; there is no separate release-management permission. SUPER_ADMIN holds it
through "*"; no other seeded role does.

Publish and withdraw additionally need recent authentication
(require_recent_auth), like the other sensitive staff actions: they decide what
every shop is offered. Both are two-step on purpose: a GET page that shows what
is about to change, then a CSRF-protected POST from that page -- the same URL,
so the re-authentication redirect (which returns to request.path) lands on a
page that can actually be shown.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.models.catalog import ProductVersion, ReleaseChannel
from app.releases import upload
from app.releases.authority import ReleaseAuthorityError, publish_release, withdraw_release
from app.security.rbac import require_permission, require_recent_auth

bp = Blueprint("releases", __name__, url_prefix="/releases")

MANAGE = "catalog.manage_versions"


@bp.route("/versions", methods=["GET"])
@require_permission("catalog.view")
def versions():
    product_filter = request.args.get("product_id")
    stmt = select(ProductVersion).order_by(ProductVersion.imported_at.desc())
    if product_filter:
        stmt = stmt.where(ProductVersion.product_id == product_filter)
    version_rows = db_session.execute(stmt).scalars().all()
    return render_template("catalog/versions.html", versions=version_rows)


@bp.route("/channels", methods=["GET"])
@require_permission("catalog.view")
def channels():
    channel_rows = db_session.execute(select(ReleaseChannel)).scalars().all()
    return render_template("catalog/channels.html", channels=channel_rows)


# ---- P1: upload a new version (creates a DRAFT) -------------------------------------------------

def _render_upload_form(values: dict, status: int = 200):
    return render_template("catalog/version_upload.html", values=values, **upload.form_choices()), status


def _default_values() -> dict:
    return {
        "product_code": "AURA_RETAIL", "platform_code": "WINDOWS", "channel_code": upload.DEFAULT_CHANNEL_CODE,
        "version": "", "build_number": "", "release_notes": "", "min_supported_version": "", "update_required": False,
    }


@bp.route("/versions/upload", methods=["GET"])
@require_permission(MANAGE)
def upload_form():
    return _render_upload_form(_default_values())


@bp.route("/versions/upload", methods=["POST"])
@require_permission(MANAGE)
def upload_submit():
    # The size ceiling for THIS endpoint is raised by upload.apply_request_size_limit
    # (a before_request hook, because CSRF reads the body before any view runs).
    actor = load_current_staff()
    form = upload.UploadForm(
        product_code=request.form.get("product_code", "").strip(),
        platform_code=request.form.get("platform_code", "").strip(),
        channel_code=request.form.get("channel_code", "").strip(),
        version=request.form.get("version", "").strip(),
        build_number=request.form.get("build_number", ""),
        release_notes=request.form.get("release_notes", ""),
        min_supported_version=request.form.get("min_supported_version", "").strip(),
        update_required=request.form.get("update_required") == "1",
    )
    try:
        row = upload.create_draft_from_upload(form, request.files.get("artifact"), actor_staff_user_id=actor.id)
    except upload.UploadRejected as exc:
        flash(str(exc), "error")
        values = {k: getattr(form, k) for k in form.__dataclass_fields__}
        return _render_upload_form(values, 400)
    flash(_("Version uploaded as a draft. Shops will not be offered it until you publish it."), "info")
    return redirect(url_for("releases.version_detail", version_id=row.id))


# ---- P2: detail, publish, withdraw --------------------------------------------------------------

def _get_or_404(version_id) -> ProductVersion:
    row = db_session.get(ProductVersion, version_id)
    if row is None:
        abort(404)
    return row


@bp.route("/versions/<uuid:version_id>", methods=["GET"])
@require_permission("catalog.view")
def version_detail(version_id):
    return render_template("catalog/version_detail.html", v=_get_or_404(version_id))


@bp.route("/versions/<uuid:version_id>/publish", methods=["GET"])
@require_permission(MANAGE)
@require_recent_auth
def publish_confirm(version_id):
    return render_template("catalog/version_confirm.html", v=_get_or_404(version_id), action="publish")


@bp.route("/versions/<uuid:version_id>/publish", methods=["POST"])
@require_permission(MANAGE)
@require_recent_auth
def publish_submit(version_id):
    row = _get_or_404(version_id)
    try:
        publish_release(row, actor_staff_user_id=load_current_staff().id)
    except ReleaseAuthorityError as exc:
        flash(str(exc), "error")
    else:
        flash(_("Published. Shops can now be offered this version."), "info")
    return redirect(url_for("releases.version_detail", version_id=version_id))


@bp.route("/versions/<uuid:version_id>/withdraw", methods=["GET"])
@require_permission(MANAGE)
@require_recent_auth
def withdraw_confirm(version_id):
    return render_template("catalog/version_confirm.html", v=_get_or_404(version_id), action="withdraw")


@bp.route("/versions/<uuid:version_id>/withdraw", methods=["POST"])
@require_permission(MANAGE)
@require_recent_auth
def withdraw_submit(version_id):
    row = _get_or_404(version_id)
    if not request.form.get("reason", "").strip():
        flash(_("Please say why you are withdrawing this version."), "error")
        return redirect(url_for("releases.withdraw_confirm", version_id=version_id))
    try:
        withdraw_release(row, actor_staff_user_id=load_current_staff().id, reason=request.form.get("reason", ""))
    except ReleaseAuthorityError as exc:
        flash(str(exc), "error")
    else:
        flash(_("Withdrawn. Shops will no longer be offered this version."), "info")
    return redirect(url_for("releases.version_detail", version_id=version_id))
