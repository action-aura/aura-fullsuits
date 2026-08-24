"""Phase 5 prerequisite #3's required Owner console visibility
(docs/launch-readiness/phase5-prerequisites.md section 3: "The console
view is part of this task, not a follow-up"). Staff-facing internal UI --
registered unconditionally in app/__init__.py (like licensing_admin_bp),
never gated behind EXTERNAL_API_ENABLED (that flag is only for the
device-facing external APIs; this is an internal admin screen with a real
session/CSRF-protected form, same as every other console blueprint).

Every quarantined event stays visible and REPLAYABLE here regardless of
its resolution ("nothing is ever silently dropped") -- replay/discard mark
a row REPLAYED/DISCARDED, they never DELETE it.

CSP note (owner ships `script-src 'self'`, no inline handlers -- see
app/security/headers.py): the confirm-before-submit behavior on the
replay/discard buttons below uses the existing `data-confirm` attribute +
static/js/confirm.js convention (already used by licensing_admin's
device-key revoke), NOT a new inline onsubmit/onclick handler."""
from __future__ import annotations

import uuid

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy.exc import DataError, IntegrityError

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.models.base import utcnow
from app.models.sync import SyncEvent, SyncQuarantineEvent
from app.security.rbac import require_permission, require_recent_auth
from app.sync import list_queries
from app.sync.routes import InvalidEventError, _build_event

bp = Blueprint("sync_quarantine", __name__, url_prefix="/sync")


@bp.route("/quarantine", methods=["GET"])
@require_permission("sync_quarantine.view")
def list_view():
    # Default view is PENDING -- an operator opening this screen wants to
    # see what still needs attention, not a wall of already-resolved
    # history. `?status=` (empty string, table.html's filter_bar "All"
    # option) explicitly asks for the unfiltered view.
    raw_status = request.args.get("status", "PENDING")
    status_filter = raw_status or None
    result = list_queries.list_quarantine_events(
        page=request.args.get("page", 1, type=int), status=status_filter,
    )
    return render_template("sync/quarantine_list.html", result=result, status_filter=status_filter)


@bp.route("/quarantine/<uuid:quarantine_id>/replay", methods=["POST"])
@require_permission("sync_quarantine.replay")
@require_recent_auth
def replay(quarantine_id: uuid.UUID):
    actor = load_current_staff()
    row = db_session.get(SyncQuarantineEvent, quarantine_id)
    if row is None:
        return jsonify({"error": "not_found"}), 404
    if row.status != "PENDING":
        flash(_("This event was already resolved."), "error")
        return redirect(url_for("sync_quarantine.list_view"))

    # Same order _store_events itself uses: build first (this re-validates
    # against the CURRENT rules -- if the underlying cause was fixed since
    # this row was quarantined, this now succeeds; if it wasn't, it fails
    # again, identically, and the row stays PENDING and visible, never
    # silently marked resolved), THEN check for an already-landed row.
    try:
        event = _build_event(row.raw_payload)
    except InvalidEventError as exc:
        flash(_("Replay failed -- payload is still invalid: %(reason)s", reason=str(exc)), "error")
        return redirect(url_for("sync_quarantine.list_view"))

    if db_session.get(SyncEvent, event.id) is not None:
        # Already landed for real -- e.g. the client's own outbox retried
        # the original batch and succeeded before an operator got to this
        # row. Idempotent success, not an error: resolve without a
        # duplicate insert attempt.
        row.status = "REPLAYED"
        row.resolved_at = utcnow()
        row.resolved_by_staff_user_id = actor.id
        db_session.commit()
        flash(_("Event was already present -- marked as replayed."), "success")
        return redirect(url_for("sync_quarantine.list_view"))

    event.license_id = row.license_id
    event.device_id = row.device_id
    try:
        db_session.add(event)
        db_session.flush()
    except (IntegrityError, DataError) as exc:
        db_session.rollback()
        flash(_("Replay failed: %(reason)s", reason=str(exc)), "error")
        return redirect(url_for("sync_quarantine.list_view"))

    row.status = "REPLAYED"
    row.resolved_at = utcnow()
    row.resolved_by_staff_user_id = actor.id
    db_session.commit()
    flash(_("Event replayed successfully."), "success")
    return redirect(url_for("sync_quarantine.list_view"))


@bp.route("/quarantine/<uuid:quarantine_id>/discard", methods=["POST"])
@require_permission("sync_quarantine.discard")
@require_recent_auth
def discard(quarantine_id: uuid.UUID):
    actor = load_current_staff()
    row = db_session.get(SyncQuarantineEvent, quarantine_id)
    if row is None:
        return jsonify({"error": "not_found"}), 404
    if row.status == "PENDING":
        # Never DELETEd -- stays a real, visible row under the DISCARDED
        # filter forever ("nothing is ever silently dropped" applies to
        # operator decisions too, not just the original skip).
        row.status = "DISCARDED"
        row.resolved_at = utcnow()
        row.resolved_by_staff_user_id = actor.id
        db_session.commit()
        flash(_("Event discarded."), "success")
    return redirect(url_for("sync_quarantine.list_view"))
