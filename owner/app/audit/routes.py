"""Audit log and security-event routes (Part T)."""
from __future__ import annotations

import csv
import io

from flask import Blueprint, Response, render_template, request
from sqlalchemy import select

from app.audit.services import verify_chain
from app.extensions import db_session
from app.models.audit import AuditLog, SecurityEvent
from app.security.rbac import require_permission, require_recent_auth

bp = Blueprint("audit", __name__, url_prefix="/audit")


@bp.route("", methods=["GET"])
@require_permission("audit.view")
def list_audit():
    action_filter = request.args.get("action_code")
    entity_filter = request.args.get("entity_type")
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(500)
    if action_filter:
        stmt = stmt.where(AuditLog.action_code == action_filter)
    if entity_filter:
        stmt = stmt.where(AuditLog.entity_type == entity_filter)
    rows = db_session.execute(stmt).scalars().all()
    return render_template("audit/list.html", rows=rows, action_filter=action_filter, entity_filter=entity_filter)


@bp.route("/security-events", methods=["GET"])
@require_permission("audit.view")
def security_events():
    rows = db_session.execute(select(SecurityEvent).order_by(SecurityEvent.created_at.desc()).limit(500)).scalars().all()
    return render_template("audit/security_events.html", rows=rows)


@bp.route("/verify-chain", methods=["GET"])
@require_permission("audit.view")
def verify_chain_route():
    ok, broken_id = verify_chain()
    return render_template("audit/verify_chain.html", ok=ok, broken_id=broken_id)


@bp.route("/export", methods=["POST"])
@require_permission("audit.export")
@require_recent_auth
def export():
    stmt = select(AuditLog).order_by(AuditLog.created_at.asc())
    rows = db_session.execute(stmt).scalars().all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["id", "created_at", "actor_staff_user_id", "action_code", "entity_type", "entity_public_id", "result", "current_hash"])
    for row in rows:
        writer.writerow(
            [row.id, row.created_at.isoformat(), row.actor_staff_user_id, row.action_code, row.entity_type, row.entity_public_id, row.result, row.current_hash]
        )
    from app.audit.services import record as audit_record
    from app.auth.session import load_current_staff

    actor = load_current_staff()
    audit_record(
        actor_staff_user_id=actor.id if actor else None,
        actor_role_snapshot=None,
        action_code="AUDIT_LOG_EXPORTED",
        entity_type="audit_log",
        entity_public_id=None,
        after_state={"row_count": len(rows)},
    )
    return Response(
        buffer.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=owner-audit-export.csv"},
    )
