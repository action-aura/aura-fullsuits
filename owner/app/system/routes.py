"""Owner database backup/restore routes (Part Y). Restricted to Super Admin
with a recent MFA confirmation -- not merely 'system.backup'/'system.restore'
permission, since the permission alone is grantable to a role, and this action
is explicitly listed among the recent-auth-gated actions in Part F."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.models.audit import DatabaseBackupRecord
from app.security.rbac import require_permission, require_recent_auth
from app.system.backup import BackupError, RestoreError, create_backup, restore_backup

bp = Blueprint("system", __name__, url_prefix="/system")


@bp.route("/backups", methods=["GET"])
@require_permission("system.view")
def list_backups():
    rows = db_session.execute(select(DatabaseBackupRecord).order_by(DatabaseBackupRecord.created_at.desc())).scalars().all()
    return render_template("system/backups.html", rows=rows)


@bp.route("/backups", methods=["POST"])
@require_permission("system.backup")
@require_recent_auth
def create_backup_route():
    actor = load_current_staff()
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
