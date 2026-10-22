"""Owner database backup/restore routes (Part Y). Restricted to Super Admin
with a recent MFA confirmation -- not merely 'system.backup'/'system.restore'
permission, since the permission alone is grantable to a role, and this action
is explicitly listed among the recent-auth-gated actions in Part F."""
from __future__ import annotations

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.audit.services import record as audit_record
from app.extensions import db_session
from app.leads.website_retention import (
    MAX_RETENTION_MONTHS,
    MIN_RETENTION_MONTHS,
    RETENTION_SETTING_KEY,
    get_retention_months,
    set_retention_months,
)
from app.models.audit import DatabaseBackupRecord
from app.security.rbac import require_permission, require_recent_auth
from app.system.backup import BackupError, RestoreError, create_backup, restore_backup

bp = Blueprint("system", __name__, url_prefix="/system")


@bp.route("/backups", methods=["GET"])
@require_permission("system.view")
def list_backups():
    rows = db_session.execute(select(DatabaseBackupRecord).order_by(DatabaseBackupRecord.created_at.desc())).scalars().all()
    return render_template(
        "system/backups.html",
        rows=rows,
        retention_months=get_retention_months(db_session),
        retention_min=MIN_RETENTION_MONTHS,
        retention_max=MAX_RETENTION_MONTHS,
    )


@bp.route("/website-lead-retention", methods=["POST"])
@require_permission("system.restore")
@require_recent_auth
def set_website_lead_retention():
    """Lets the business owner change how many months unconverted website demo
    requests are kept. Guarded exactly like the most sensitive write on this page
    (restore): it decides when personal data is deleted. The change and its audit
    row are committed together (audit_record commits)."""
    actor = load_current_staff()
    raw = (request.form.get("months") or "").strip()
    try:
        months = int(raw)
        before = get_retention_months(db_session)
        set_retention_months(db_session, months, actor)
    except ValueError:
        db_session.rollback()
        flash(
            _("Enter a whole number of months from %(min)s to %(max)s.", min=MIN_RETENTION_MONTHS, max=MAX_RETENTION_MONTHS),
            "error",
        )
        return redirect(url_for("system.list_backups"))
    audit_record(
        actor_staff_user_id=actor.id,
        actor_role_snapshot="SUPER_ADMIN" if actor.is_super_admin else None,
        action_code="WEBSITE_LEAD_RETENTION_CHANGED",
        entity_type="system_setting",
        entity_public_id=RETENTION_SETTING_KEY,
        before_state={"months": before},
        after_state={"months": months},
    )
    flash(_("Website demo request retention saved."), "success")
    return redirect(url_for("system.list_backups"))


@bp.route("/backups", methods=["POST"])
@require_permission("system.backup")
@require_recent_auth
def create_backup_route():
    actor = load_current_staff()
    if not actor.is_super_admin:
        return jsonify({"error": "forbidden -- database backup requires Super Admin"}), 403
    try:
        create_backup(current_app.config["BACKUP_DIRECTORY"], actor.id)
    except BackupError as exc:
        return jsonify({"error": str(exc)}), 500
    return redirect(url_for("system.list_backups"))


@bp.route("/backups/<uuid:backup_id>/restore", methods=["POST"])
@require_permission("system.restore")
@require_recent_auth
def restore_backup_route(backup_id):
    actor = load_current_staff()
    if not actor.is_super_admin:
        return jsonify({"error": "forbidden -- database restore requires Super Admin"}), 403
    backup_record = db_session.get(DatabaseBackupRecord, backup_id)
    if backup_record is None:
        return jsonify({"error": "not_found"}), 404
    try:
        restore_backup(backup_record, current_app.config["BACKUP_DIRECTORY"], actor.id)
    except RestoreError as exc:
        return jsonify({"error": str(exc)}), 500
    return redirect(url_for("system.list_backups"))
