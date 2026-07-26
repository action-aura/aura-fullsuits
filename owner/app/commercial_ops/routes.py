"""Internal renewal-workflow routes (Phase 8 Milestone 2, Part U slice).

JSON API only in this milestone -- no Jinja templates yet. Full internal
Owner UI (confirmation dialogs, before/after summaries, status badges) is
deliberately deferred to Milestone 7 per
docs/owner/phase8/phase8-implementation-plan.md; this is the backend
surface Milestone 7's UI will call into, built and tested first so the
transaction semantics (idempotency, concurrency, separation-of-duties) are
proven before any frontend is layered on top.

Every route requires `subscriptions.renew` (the existing Phase 5 permission
code -- see app/staff/seed_data.py) plus recent authentication for the two
money-moving actions (approve, apply), matching the existing pattern for
sensitive actions elsewhere in this codebase (e.g. licensing_admin's
signing-key routes).
"""
from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.commercial_ops.renewal_requests import (
    InvalidRenewalTransitionError,
    RenewalApplicationError,
    approve_renewal_request,
    apply_renewal_request,
    create_renewal_request,
    transition_renewal_request,
)
from app.extensions import db_session
from app.models.commercial_ops import RenewalRequest
from app.models.subscriptions import Subscription
from app.security.rbac import require_permission, require_recent_auth

bp = Blueprint("commercial_ops", __name__, url_prefix="/commercial-ops")


def _serialize(renewal: RenewalRequest) -> dict:
    return {
        "id": str(renewal.id),
        "subscription_id": str(renewal.subscription_id),
        "customer_id": str(renewal.customer_id),
        "product_id": str(renewal.product_id),
        "status": renewal.status,
        "current_plan_id": str(renewal.current_plan_id),
        "requested_plan_id": str(renewal.requested_plan_id) if renewal.requested_plan_id else None,
        "current_term_start": renewal.current_term_start.isoformat() if renewal.current_term_start else None,
        "current_term_end": renewal.current_term_end.isoformat() if renewal.current_term_end else None,
        "proposed_term_start": renewal.proposed_term_start.isoformat() if renewal.proposed_term_start else None,
        "proposed_term_end": renewal.proposed_term_end.isoformat() if renewal.proposed_term_end else None,
        "date_rule": renewal.date_rule,
        "currency": renewal.currency,
        "commercial_amount": str(renewal.commercial_amount) if renewal.commercial_amount is not None else None,
        "device_allowance_before": renewal.device_allowance_before,
        "device_allowance_after": renewal.device_allowance_after,
        "reason": renewal.reason,
        "notes": renewal.notes,
        "created_by_staff_user_id": str(renewal.created_by_staff_user_id) if renewal.created_by_staff_user_id else None,
        "approved_by_staff_user_id": str(renewal.approved_by_staff_user_id) if renewal.approved_by_staff_user_id else None,
        "approved_at": renewal.approved_at.isoformat() if renewal.approved_at else None,
        "applied_by_staff_user_id": str(renewal.applied_by_staff_user_id) if renewal.applied_by_staff_user_id else None,
        "applied_at": renewal.applied_at.isoformat() if renewal.applied_at else None,
        "applied_renewal_record_id": str(renewal.applied_renewal_record_id) if renewal.applied_renewal_record_id else None,
        "created_at": renewal.created_at.isoformat(),
    }


def _get_renewal_or_404(renewal_id: str) -> RenewalRequest | None:
    try:
        parsed = uuid.UUID(renewal_id)
    except ValueError:
        return None
    return db_session.execute(select(RenewalRequest).where(RenewalRequest.id == parsed)).scalars().first()


@bp.route("/renewals", methods=["GET"])
@require_permission("subscriptions.view")
def list_renewals():
    stmt = select(RenewalRequest).order_by(RenewalRequest.created_at.desc())
    subscription_id = request.args.get("subscription_id")
    status_filter = request.args.get("status")
    if subscription_id:
        stmt = stmt.where(RenewalRequest.subscription_id == subscription_id)
    if status_filter:
        stmt = stmt.where(RenewalRequest.status == status_filter)
    renewals = db_session.execute(stmt).scalars().all()
    return jsonify({"renewals": [_serialize(r) for r in renewals]})


@bp.route("/renewals/<renewal_id>", methods=["GET"])
@require_permission("subscriptions.view")
def get_renewal(renewal_id):
    renewal = _get_renewal_or_404(renewal_id)
    if renewal is None:
        return jsonify({"error": "not_found"}), 404
    return jsonify(_serialize(renewal))


@bp.route("/renewals", methods=["POST"])
@require_permission("subscriptions.renew")
def create_renewal():
    actor = load_current_staff()
    body = request.get_json(silent=True) or {}

    subscription_id = body.get("subscription_id")
    if not subscription_id:
        return jsonify({"error": "invalid_request", "detail": "subscription_id is required."}), 400
    subscription = db_session.execute(
        select(Subscription).where(Subscription.id == subscription_id)
    ).scalars().first()
    if subscription is None:
        return jsonify({"error": "subscription_not_found"}), 404

    required_date_fields = ("date_rule", "proposed_term_start", "proposed_term_end", "currency")
    missing = [f for f in required_date_fields if not body.get(f)]
    if missing:
        return jsonify({"error": "invalid_request", "detail": f"Missing required fields: {missing}"}), 400

    from datetime import date as date_cls

    try:
        proposed_start = date_cls.fromisoformat(body["proposed_term_start"])
        proposed_end = date_cls.fromisoformat(body["proposed_term_end"])
    except ValueError:
        return jsonify({"error": "invalid_request", "detail": "Dates must be ISO format (YYYY-MM-DD)."}), 400

    renewal = create_renewal_request(
        subscription=subscription,
        date_rule=body["date_rule"],
        proposed_term_start=proposed_start,
        proposed_term_end=proposed_end,
        currency=body["currency"],
        actor_staff_user_id=actor.id,
        requested_plan_id=body.get("requested_plan_id"),
        billing_interval=body.get("billing_interval"),
        commercial_amount=body.get("commercial_amount"),
        adjustment_amount=body.get("adjustment_amount"),
        device_allowance_after=body.get("device_allowance_after"),
        sales_owner_staff_user_id=body.get("sales_owner_staff_user_id"),
        reason=body.get("reason"),
        notes=body.get("notes"),
        idempotency_key=body.get("idempotency_key"),
    )
    return jsonify(_serialize(renewal)), 201


@bp.route("/renewals/<renewal_id>/transition", methods=["POST"])
@require_permission("subscriptions.renew")
def transition_renewal(renewal_id):
    actor = load_current_staff()
    renewal = _get_renewal_or_404(renewal_id)
    if renewal is None:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    to_status = body.get("to_status")
    if not to_status:
        return jsonify({"error": "invalid_request", "detail": "to_status is required."}), 400
    try:
        transition_renewal_request(renewal, to_status, actor.id, reason=body.get("reason"))
    except InvalidRenewalTransitionError as exc:
        return jsonify({"error": "invalid_transition", "detail": str(exc)}), 400
    return jsonify(_serialize(renewal))


@bp.route("/renewals/<renewal_id>/approve", methods=["POST"])
@require_permission("subscriptions.renew")
@require_recent_auth
def approve_renewal(renewal_id):
    actor = load_current_staff()
    renewal = _get_renewal_or_404(renewal_id)
    if renewal is None:
        return jsonify({"error": "not_found"}), 404
    body = request.get_json(silent=True) or {}
    try:
        approve_renewal_request(renewal, actor.id, reason=body.get("reason"))
    except RenewalApplicationError as exc:
        return jsonify({"error": "self_approval_not_allowed" if "SELF_APPROVAL" in str(exc) else "approval_failed", "detail": str(exc)}), 403
    except InvalidRenewalTransitionError as exc:
        return jsonify({"error": "invalid_transition", "detail": str(exc)}), 400
    return jsonify(_serialize(renewal))


@bp.route("/renewals/<renewal_id>/apply", methods=["POST"])
@require_permission("subscriptions.renew")
@require_recent_auth
def apply_renewal(renewal_id):
    actor = load_current_staff()
    renewal = _get_renewal_or_404(renewal_id)
    if renewal is None:
        return jsonify({"error": "not_found"}), 404
    try:
        applied = apply_renewal_request(renewal.id, actor.id)
    except InvalidRenewalTransitionError as exc:
        db_session.rollback()
        return jsonify({"error": "invalid_transition", "detail": str(exc)}), 400
    except RenewalApplicationError as exc:
        db_session.rollback()
        return jsonify({"error": "application_failed", "detail": str(exc)}), 409
    return jsonify(_serialize(applied))
