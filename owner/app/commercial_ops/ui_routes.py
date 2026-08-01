"""Phase 8V: internal Owner UI for the Milestone 4-6 backlog (renewals,
payments, pilots, emergency extensions, manual activation review,
device-slot operations, notifications, operational queues, reconciliation,
and the commercial timeline).

Every route here is a thin wrapper: resolve a public UUID, enforce
server-side RBAC (`@require_permission`) and recent-auth/MFA
(`@require_recent_auth`) exactly per `docs/owner/phase8v/phase8-service-route-map.md`,
call the existing Phase 8 service function, and either redirect (PRG, on
success) or re-render the same template with `error=...` (on a domain
error) -- the same convention `app/auth/routes.py` already uses
everywhere in this codebase. No lifecycle decision is made in this file;
every rule (self-approval, MFA, device limits, extension caps, ...) is
already enforced inside the service layer these routes call.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, redirect, render_template, request, url_for
from flask_babel import gettext as _
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.i18n_labels import (
    localize_activation_policy_error,
    localize_device_slot_error,
    localize_emergency_extension_error,
    localize_notification_error,
    localize_pending_activation_error,
    localize_pilot_lifecycle_error,
    localize_pilot_transition_error,
    localize_renewal_transition_error,
)

from app.auth.session import load_current_staff
from app.commercial_ops.activation_policy import (
    ActivationPolicyError,
    PendingActivationError,
    approve_pending_activation,
    create_activation_policy,
    reject_pending_activation,
    resolve_activation_mode,
)
from app.commercial_ops.commercial_policy import NotificationError, acknowledge_notification, assign_notification, resolve_notification
from app.commercial_ops.device_slot_ops import (
    DeviceSlotError,
    create_device_slot_exception,
    release_device_slot,
    replace_device_slot,
    resolve_effective_device_limit,
    revoke_device_slot_exception,
)
from app.commercial_ops.emergency_extensions import (
    MAX_EMERGENCY_EXTENSION_HOURS,
    EmergencyExtensionError,
    create_emergency_extension,
    revoke_emergency_extension,
)
from app.commercial_ops.pilot_lifecycle import (
    InvalidPilotTransitionError,
    PilotLifecycleError,
    activate_pilot,
    approve_pilot,
    cancel_pilot,
    complete_pilot,
    create_pilot_record,
    extend_pilot,
    mark_pilot_converted,
)
from app.commercial_ops.queues import QUEUE_ROLE_CODES, UnknownQueueRoleError, get_queue_for_role
from app.commercial_ops.reconciliation import run_reconciliation
from app.commercial_ops.renewal_requests import (
    InvalidRenewalTransitionError,
    RenewalApplicationError,
    RenewalConcurrencyError,
    approve_renewal_request,
    apply_renewal_request,
    create_renewal_request,
    transition_renewal_request,
)
from app.commercial_ops.timeline import build_subscription_timeline
from app.extensions import db_session
from app.models.activation_governance import ActivationPolicy, DeviceSlotException, PendingActivation
from app.models.catalog import Plan, Product
from app.models.commercial_ops import EmergencyExtension, InternalNotification, PilotRecord, RenewalRequest
from app.models.installations import Installation
from app.models.licensing import License
from app.models.subscriptions import Subscription
from app.security.rbac import require_permission, require_recent_auth

bp = Blueprint("commercial_ops_ui", __name__, url_prefix="/commercial-ops/ui")


# -- shared helpers -----------------------------------------------------------

def _localized_error_text(exc: Exception) -> str:
    """Presentation-boundary translation for the three stable-code
    exceptions raised by the pilot/renewal service layer (Phase 9.5B-R3).
    Any other exception type (e.g. RenewalApplicationError, whose message
    text is deliberately left as a stable code -- see
    commercial_ops/routes.py's own "SELF_APPROVAL" string match --
    StaleDataError) falls through to its existing str(exc)/pre-built
    message, unchanged."""
    if isinstance(exc, InvalidRenewalTransitionError):
        return localize_renewal_transition_error(exc.code, **exc.params)
    if isinstance(exc, InvalidPilotTransitionError):
        return localize_pilot_transition_error(exc.code, **exc.params)
    if isinstance(exc, PilotLifecycleError):
        return localize_pilot_lifecycle_error(exc.code, **exc.params)
    if isinstance(exc, DeviceSlotError):
        return localize_device_slot_error(exc.code, **exc.params)
    if isinstance(exc, EmergencyExtensionError):
        return localize_emergency_extension_error(exc.code, **exc.params)
    if isinstance(exc, PendingActivationError):
        return localize_pending_activation_error(exc.code, **exc.params)
    if isinstance(exc, ActivationPolicyError):
        return localize_activation_policy_error(exc.code, **exc.params)
    if isinstance(exc, NotificationError):
        return localize_notification_error(exc.code, **exc.params)
    return str(exc)


def _staff():
    return load_current_staff()


def _staff_role_codes(staff) -> list[str]:
    if staff.is_super_admin:
        return ["SUPER_ADMIN"]
    return [ra.role.code for ra in staff.role_assignments if ra.role.code in QUEUE_ROLE_CODES]


# -- Renewals -------------------------------------------------------------

@bp.route("/renewals", methods=["GET"])
@require_permission("subscriptions.view")
def list_renewals():
    status_filter = request.args.get("status")
    stmt = select(RenewalRequest).order_by(RenewalRequest.created_at.desc())
    if status_filter:
        stmt = stmt.where(RenewalRequest.status == status_filter)
    renewals = db_session.execute(stmt).scalars().all()
    return render_template("commercial_ops/renewals_list.html", renewals=renewals, status_filter=status_filter)


@bp.route("/renewals/new", methods=["GET"])
@require_permission("subscriptions.renew")
def new_renewal_form():
    subscription_id = request.args.get("subscription_id")
    subscription = db_session.get(Subscription, subscription_id) if subscription_id else None
    if subscription is None:
        return render_template("commercial_ops/renewals_new.html", subscription=None, plans=[], error=_("A valid subscription_id is required.")), 400
    plans = db_session.execute(select(Plan).where(Plan.product_id == subscription.product_id)).scalars().all()
    return render_template("commercial_ops/renewals_new.html", subscription=subscription, plans=plans, error=None)


@bp.route("/renewals", methods=["POST"])
@require_permission("subscriptions.renew")
def create_renewal():
    actor = _staff()
    subscription = db_session.get(Subscription, request.form.get("subscription_id"))
    if subscription is None:
        return render_template("commercial_ops/renewals_new.html", subscription=None, plans=[], error=_("Subscription not found.")), 404
    plans = db_session.execute(select(Plan).where(Plan.product_id == subscription.product_id)).scalars().all()
    try:
        proposed_start = date.fromisoformat(request.form["proposed_term_start"])
        proposed_end = date.fromisoformat(request.form["proposed_term_end"])
    except (KeyError, ValueError):
        return render_template("commercial_ops/renewals_new.html", subscription=subscription, plans=plans, error=_("Proposed term start/end must be valid dates.")), 400

    commercial_amount = request.form.get("commercial_amount") or None
    if commercial_amount is not None:
        try:
            commercial_amount = Decimal(commercial_amount)
        except InvalidOperation:
            return render_template("commercial_ops/renewals_new.html", subscription=subscription, plans=plans, error=_("Commercial amount must be a number.")), 400

    renewal = create_renewal_request(
        subscription=subscription,
        date_rule=request.form.get("date_rule", "EARLY_RENEWAL_FROM_CURRENT_END"),
        proposed_term_start=proposed_start,
        proposed_term_end=proposed_end,
        currency=request.form.get("currency", "USD"),
        actor_staff_user_id=actor.id,
        requested_plan_id=request.form.get("requested_plan_id") or None,
        commercial_amount=commercial_amount,
        device_allowance_after=int(request.form["device_allowance_after"]) if request.form.get("device_allowance_after") else None,
        reason=request.form.get("reason") or None,
        notes=request.form.get("notes") or None,
    )
    return redirect(url_for("commercial_ops_ui.renewal_detail", renewal_id=renewal.id))


@bp.route("/renewals/<uuid:renewal_id>", methods=["GET"])
@require_permission("subscriptions.view")
def renewal_detail(renewal_id):
    renewal = db_session.get(RenewalRequest, renewal_id)
    if renewal is None:
        return render_template("commercial_ops/not_found.html", entity=_("Renewal request")), 404
    return render_template("commercial_ops/renewal_detail.html", renewal=renewal, error=None)


@bp.route("/renewals/<uuid:renewal_id>/transition", methods=["POST"])
@require_permission("subscriptions.renew")
def transition_renewal(renewal_id):
    actor = _staff()
    renewal = db_session.get(RenewalRequest, renewal_id)
    if renewal is None:
        return render_template("commercial_ops/not_found.html", entity=_("Renewal request")), 404
    try:
        transition_renewal_request(renewal, request.form.get("to_status"), actor.id, reason=request.form.get("reason") or None)
    except InvalidRenewalTransitionError as exc:
        return render_template("commercial_ops/renewal_detail.html", renewal=renewal, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.renewal_detail", renewal_id=renewal_id))


@bp.route("/renewals/<uuid:renewal_id>/approve", methods=["POST"])
@require_permission("subscriptions.renew")
@require_recent_auth
def approve_renewal(renewal_id):
    actor = _staff()
    renewal = db_session.get(RenewalRequest, renewal_id)
    if renewal is None:
        return render_template("commercial_ops/not_found.html", entity=_("Renewal request")), 404
    try:
        approve_renewal_request(renewal, actor.id, reason=request.form.get("reason") or None)
    except (RenewalApplicationError, InvalidRenewalTransitionError) as exc:
        return render_template("commercial_ops/renewal_detail.html", renewal=renewal, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.renewal_detail", renewal_id=renewal_id))


@bp.route("/renewals/<uuid:renewal_id>/apply", methods=["POST"])
@require_permission("subscriptions.renew")
@require_recent_auth
def apply_renewal(renewal_id):
    actor = _staff()
    renewal = db_session.get(RenewalRequest, renewal_id)
    if renewal is None:
        return render_template("commercial_ops/not_found.html", entity=_("Renewal request")), 404
    try:
        apply_renewal_request(renewal.id, actor.id)
    except RenewalConcurrencyError as exc:
        db_session.rollback()
        return render_template("commercial_ops/renewal_detail.html", renewal=db_session.get(RenewalRequest, renewal_id), error=_localized_error_text(exc)), 409
    except (RenewalApplicationError, InvalidRenewalTransitionError) as exc:
        db_session.rollback()
        return render_template("commercial_ops/renewal_detail.html", renewal=db_session.get(RenewalRequest, renewal_id), error=_localized_error_text(exc)), 400
    except StaleDataError:
        db_session.rollback()
        return render_template("commercial_ops/renewal_detail.html", renewal=db_session.get(RenewalRequest, renewal_id), error=_("Someone else already changed this renewal request. Reload and retry.")), 409
    return redirect(url_for("commercial_ops_ui.renewal_detail", renewal_id=renewal_id))


# -- Pilots -----------------------------------------------------------------

@bp.route("/pilots", methods=["GET"])
@require_permission("pilots.view")
def list_pilots():
    status_filter = request.args.get("status")
    stmt = select(PilotRecord).order_by(PilotRecord.created_at.desc())
    if status_filter:
        stmt = stmt.where(PilotRecord.status == status_filter)
    pilots = db_session.execute(stmt).scalars().all()
    return render_template("commercial_ops/pilots_list.html", pilots=pilots, status_filter=status_filter)


@bp.route("/pilots/new", methods=["GET"])
@require_permission("pilots.manage")
def new_pilot_form():
    subscription_id = request.args.get("subscription_id")
    subscription = db_session.get(Subscription, subscription_id) if subscription_id else None
    if subscription is None or subscription.status != "PILOT":
        return render_template("commercial_ops/pilots_new.html", subscription=None, error=_("A subscription already in PILOT status is required.")), 400
    return render_template("commercial_ops/pilots_new.html", subscription=subscription, error=None)


@bp.route("/pilots", methods=["POST"])
@require_permission("pilots.manage")
def create_pilot():
    actor = _staff()
    subscription = db_session.get(Subscription, request.form.get("subscription_id"))
    if subscription is None:
        return render_template("commercial_ops/pilots_new.html", subscription=None, error=_("Subscription not found.")), 404
    try:
        pilot_start = date.fromisoformat(request.form["pilot_start"])
        pilot_end = date.fromisoformat(request.form["pilot_end"])
    except (KeyError, ValueError):
        return render_template("commercial_ops/pilots_new.html", subscription=subscription, error=_("Pilot start/end must be valid dates.")), 400
    try:
        pilot = create_pilot_record(
            subscription=subscription, pilot_start=pilot_start, pilot_end=pilot_end, actor_staff_user_id=actor.id,
            allowed_device_count=int(request.form.get("allowed_device_count") or 1),
            support_level=request.form.get("support_level") or None,
            agreed_limitations=request.form.get("agreed_limitations") or None,
            success_criteria=request.form.get("success_criteria") or None,
            review_date=date.fromisoformat(request.form["review_date"]) if request.form.get("review_date") else None,
            exit_rollback_plan=request.form.get("exit_rollback_plan") or None,
        )
    except PilotLifecycleError as exc:
        return render_template("commercial_ops/pilots_new.html", subscription=subscription, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.pilot_detail", pilot_id=pilot.id))


@bp.route("/pilots/<uuid:pilot_id>", methods=["GET"])
@require_permission("pilots.view")
def pilot_detail(pilot_id):
    pilot = db_session.get(PilotRecord, pilot_id)
    if pilot is None:
        return render_template("commercial_ops/not_found.html", entity=_("Pilot")), 404
    return render_template("commercial_ops/pilot_detail.html", pilot=pilot, error=None)


def _pilot_action(pilot_id, action, **kwargs):
    actor = _staff()
    pilot = db_session.get(PilotRecord, pilot_id)
    if pilot is None:
        return render_template("commercial_ops/not_found.html", entity=_("Pilot")), 404
    try:
        action(pilot, actor, **kwargs)
    except (PilotLifecycleError, InvalidPilotTransitionError) as exc:
        return render_template("commercial_ops/pilot_detail.html", pilot=pilot, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.pilot_detail", pilot_id=pilot_id))


@bp.route("/pilots/<uuid:pilot_id>/approve", methods=["POST"])
@require_permission("pilots.manage")
def approve_pilot_route(pilot_id):
    return _pilot_action(pilot_id, lambda p, actor: approve_pilot(p, actor.id))


@bp.route("/pilots/<uuid:pilot_id>/activate", methods=["POST"])
@require_permission("pilots.manage")
def activate_pilot_route(pilot_id):
    return _pilot_action(pilot_id, lambda p, actor: activate_pilot(p, actor.id))


@bp.route("/pilots/<uuid:pilot_id>/extend", methods=["POST"])
@require_permission("pilots.manage")
def extend_pilot_route(pilot_id):
    actor = _staff()
    pilot = db_session.get(PilotRecord, pilot_id)
    if pilot is None:
        return render_template("commercial_ops/not_found.html", entity=_("Pilot")), 404
    try:
        new_end_date = date.fromisoformat(request.form["new_end_date"])
    except (KeyError, ValueError):
        return render_template("commercial_ops/pilot_detail.html", pilot=pilot, error=_("new_end_date must be a valid date.")), 400
    try:
        extend_pilot(pilot, new_end_date=new_end_date, reason=request.form.get("reason", ""), actor_staff_user_id=actor.id)
    except PilotLifecycleError as exc:
        return render_template("commercial_ops/pilot_detail.html", pilot=pilot, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.pilot_detail", pilot_id=pilot_id))


@bp.route("/pilots/<uuid:pilot_id>/convert", methods=["GET"])
@require_permission("pilots.manage")
def convert_pilot_form(pilot_id):
    pilot = db_session.get(PilotRecord, pilot_id)
    if pilot is None:
        return render_template("commercial_ops/not_found.html", entity=_("Pilot")), 404
    plans = db_session.execute(select(Plan).where(Plan.product_id == pilot.product_id)).scalars().all()
    return render_template("commercial_ops/pilot_convert.html", pilot=pilot, plans=plans, error=None)


@bp.route("/pilots/<uuid:pilot_id>/convert", methods=["POST"])
@require_permission("pilots.manage")
@require_recent_auth
def convert_pilot(pilot_id):
    """Guided flow: creates a real RenewalRequest against the pilot's
    subscription, then (only once the caller separately approves and
    applies it -- separation-of-duties is not bypassed just because this
    is a guided flow) marks the pilot converted. This route only ever
    performs step 1 (create); approval/apply happen through the normal
    renewal detail page, same as any other renewal -- see
    pilot_lifecycle.py's own docstring on why conversion is never a single
    hidden click."""
    actor = _staff()
    pilot = db_session.get(PilotRecord, pilot_id)
    if pilot is None:
        return render_template("commercial_ops/not_found.html", entity=_("Pilot")), 404
    plans = db_session.execute(select(Plan).where(Plan.product_id == pilot.product_id)).scalars().all()
    try:
        proposed_start = date.fromisoformat(request.form["proposed_term_start"])
        proposed_end = date.fromisoformat(request.form["proposed_term_end"])
    except (KeyError, ValueError):
        return render_template("commercial_ops/pilot_convert.html", pilot=pilot, plans=plans, error=_("Term start/end must be valid dates.")), 400
    renewal = create_renewal_request(
        subscription=pilot.subscription, date_rule="PILOT_CONVERSION", proposed_term_start=proposed_start,
        proposed_term_end=proposed_end, currency=request.form.get("currency", "USD"), actor_staff_user_id=actor.id,
        requested_plan_id=request.form.get("requested_plan_id") or None,
        device_allowance_after=int(request.form["device_allowance_after"]) if request.form.get("device_allowance_after") else None,
        reason="Pilot conversion to paid plan.",
    )
    return redirect(url_for("commercial_ops_ui.renewal_detail", renewal_id=renewal.id))


@bp.route("/pilots/<uuid:pilot_id>/mark-converted", methods=["POST"])
@require_permission("pilots.manage")
def mark_pilot_converted_route(pilot_id):
    actor = _staff()
    pilot = db_session.get(PilotRecord, pilot_id)
    if pilot is None:
        return render_template("commercial_ops/not_found.html", entity=_("Pilot")), 404
    renewal = db_session.get(RenewalRequest, request.form.get("renewal_request_id"))
    if renewal is None:
        return render_template("commercial_ops/pilot_detail.html", pilot=pilot, error=_("Renewal request not found.")), 404
    try:
        mark_pilot_converted(pilot, renewal, actor.id)
    except (PilotLifecycleError, InvalidPilotTransitionError) as exc:
        return render_template("commercial_ops/pilot_detail.html", pilot=pilot, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.pilot_detail", pilot_id=pilot_id))


@bp.route("/pilots/<uuid:pilot_id>/complete", methods=["POST"])
@require_permission("pilots.manage")
def complete_pilot_route(pilot_id):
    return _pilot_action(pilot_id, lambda p, actor: complete_pilot(p, actor_staff_user_id=actor.id, reason=request.form.get("reason") or None))


@bp.route("/pilots/<uuid:pilot_id>/cancel", methods=["POST"])
@require_permission("pilots.manage")
def cancel_pilot_route(pilot_id):
    return _pilot_action(pilot_id, lambda p, actor: cancel_pilot(p, reason=request.form.get("reason", ""), actor_staff_user_id=actor.id))


# -- Emergency extensions ------------------------------------------------

@bp.route("/emergency-extensions", methods=["GET"])
@require_permission("emergency_extensions.view")
def list_emergency_extensions():
    status_filter = request.args.get("status", "ACTIVE")
    stmt = select(EmergencyExtension).order_by(EmergencyExtension.created_at.desc())
    if status_filter:
        stmt = stmt.where(EmergencyExtension.status == status_filter)
    extensions = db_session.execute(stmt).scalars().all()
    return render_template("commercial_ops/emergency_extensions_list.html", extensions=extensions, status_filter=status_filter)


@bp.route("/emergency-extensions/new", methods=["GET"])
@require_permission("emergency_extensions.create")
@require_recent_auth
def new_emergency_extension_form():
    subscription_id = request.args.get("subscription_id")
    subscription = db_session.get(Subscription, subscription_id) if subscription_id else None
    return render_template("commercial_ops/emergency_extensions_new.html", subscription=subscription, error=None, max_hours=MAX_EMERGENCY_EXTENSION_HOURS)


@bp.route("/emergency-extensions", methods=["POST"])
@require_permission("emergency_extensions.create")
@require_recent_auth
def create_emergency_extension_route():
    actor = _staff()
    subscription = db_session.get(Subscription, request.form.get("subscription_id"))
    if subscription is None:
        return render_template("commercial_ops/emergency_extensions_new.html", subscription=None, error=_("Subscription not found.")), 404
    license_row = db_session.get(License, request.form["license_id"]) if request.form.get("license_id") else None
    try:
        duration_hours = int(request.form["duration_hours"])
    except (KeyError, ValueError):
        return render_template("commercial_ops/emergency_extensions_new.html", subscription=subscription, error=_("duration_hours must be an integer.")), 400
    try:
        extension = create_emergency_extension(
            subscription=subscription, reason=request.form.get("reason", ""), duration_hours=duration_hours,
            actor_staff_user_id=actor.id, license=license_row, incident_reference=request.form.get("incident_reference") or None,
        )
    except EmergencyExtensionError as exc:
        return render_template("commercial_ops/emergency_extensions_new.html", subscription=subscription, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.emergency_extension_detail", extension_id=extension.id))


@bp.route("/emergency-extensions/<uuid:extension_id>", methods=["GET"])
@require_permission("emergency_extensions.view")
def emergency_extension_detail(extension_id):
    extension = db_session.get(EmergencyExtension, extension_id)
    if extension is None:
        return render_template("commercial_ops/not_found.html", entity=_("Emergency extension")), 404
    return render_template("commercial_ops/emergency_extension_detail.html", extension=extension, error=None, now=datetime.now(timezone.utc))


@bp.route("/emergency-extensions/<uuid:extension_id>/revoke", methods=["POST"])
@require_permission("emergency_extensions.revoke")
@require_recent_auth
def revoke_emergency_extension_route(extension_id):
    actor = _staff()
    extension = db_session.get(EmergencyExtension, extension_id)
    if extension is None:
        return render_template("commercial_ops/not_found.html", entity=_("Emergency extension")), 404
    try:
        revoke_emergency_extension(extension, reason=request.form.get("reason", ""), actor_staff_user_id=actor.id)
    except EmergencyExtensionError as exc:
        return render_template("commercial_ops/emergency_extension_detail.html", extension=extension, error=_localized_error_text(exc), now=datetime.now(timezone.utc)), 400
    return redirect(url_for("commercial_ops_ui.emergency_extension_detail", extension_id=extension_id))


# -- Manual activation review ----------------------------------------------

@bp.route("/pending-activations", methods=["GET"])
@require_permission("pending_activations.view")
def list_pending_activations():
    status_filter = request.args.get("status", "PENDING_REVIEW")
    stmt = select(PendingActivation).order_by(PendingActivation.created_at.asc())
    if status_filter:
        stmt = stmt.where(PendingActivation.status == status_filter)
    rows = db_session.execute(stmt).scalars().all()
    return render_template("commercial_ops/pending_activations_list.html", rows=rows, status_filter=status_filter)


@bp.route("/pending-activations/<uuid:pending_id>", methods=["GET"])
@require_permission("pending_activations.view")
def pending_activation_detail(pending_id):
    pending = db_session.get(PendingActivation, pending_id)
    if pending is None:
        return render_template("commercial_ops/not_found.html", entity=_("Pending activation")), 404
    return render_template("commercial_ops/pending_activation_detail.html", pending=pending, error=None)


@bp.route("/pending-activations/<uuid:pending_id>/approve", methods=["POST"])
@require_permission("pending_activations.decide")
@require_recent_auth
def approve_pending_activation_route(pending_id):
    actor = _staff()
    pending = db_session.get(PendingActivation, pending_id)
    if pending is None:
        return render_template("commercial_ops/not_found.html", entity=_("Pending activation")), 404
    try:
        approve_pending_activation(pending, actor.id)
    except PendingActivationError as exc:
        return render_template("commercial_ops/pending_activation_detail.html", pending=pending, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.pending_activation_detail", pending_id=pending_id))


@bp.route("/pending-activations/<uuid:pending_id>/reject", methods=["POST"])
@require_permission("pending_activations.decide")
@require_recent_auth
def reject_pending_activation_route(pending_id):
    actor = _staff()
    pending = db_session.get(PendingActivation, pending_id)
    if pending is None:
        return render_template("commercial_ops/not_found.html", entity=_("Pending activation")), 404
    try:
        reject_pending_activation(pending, actor.id, reason=request.form.get("reason", ""))
    except PendingActivationError as exc:
        return render_template("commercial_ops/pending_activation_detail.html", pending=pending, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.pending_activation_detail", pending_id=pending_id))


@bp.route("/activation-policy", methods=["GET"])
@require_permission("activation_policy.manage")
def activation_policy_list():
    products = db_session.execute(select(Product)).scalars().all()
    policies = db_session.execute(select(ActivationPolicy).order_by(ActivationPolicy.created_at.desc())).scalars().all()
    modes = {p.id: resolve_activation_mode(p.id) for p in products}
    return render_template("commercial_ops/activation_policy.html", products=products, policies=policies, modes=modes, error=None)


@bp.route("/activation-policy", methods=["POST"])
@require_permission("activation_policy.manage")
@require_recent_auth
def activation_policy_create():
    actor = _staff()
    try:
        create_activation_policy(
            policy_code=request.form["policy_code"], mode=request.form["mode"],
            effective_date=date.fromisoformat(request.form["effective_date"]), actor_staff_user_id=actor.id,
            product_id=request.form.get("product_id") or None, notes=request.form.get("notes") or None,
        )
    except (ActivationPolicyError, KeyError, ValueError) as exc:
        products = db_session.execute(select(Product)).scalars().all()
        policies = db_session.execute(select(ActivationPolicy).order_by(ActivationPolicy.created_at.desc())).scalars().all()
        modes = {p.id: resolve_activation_mode(p.id) for p in products}
        return render_template("commercial_ops/activation_policy.html", products=products, policies=policies, modes=modes, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.activation_policy_list"))


# -- Device-slot operations ------------------------------------------------

@bp.route("/installations/<uuid:installation_id>/release", methods=["POST"])
@require_permission("installations.replace_device")
def release_installation(installation_id):
    actor = _staff()
    installation = db_session.get(Installation, installation_id)
    if installation is None:
        return render_template("commercial_ops/not_found.html", entity=_("Installation")), 404
    try:
        release_device_slot(installation, reason=request.form.get("reason", ""), actor_staff_user_id=actor.id)
    except DeviceSlotError as exc:
        return render_template("installations/detail.html", installation=installation, allowed_transitions=[], error=_localized_error_text(exc)), 400
    return redirect(url_for("installations.detail", installation_id=installation_id))


@bp.route("/installations/<uuid:installation_id>/replace", methods=["POST"])
@require_permission("installations.replace_device")
def replace_installation(installation_id):
    actor = _staff()
    installation = db_session.get(Installation, installation_id)
    if installation is None:
        return render_template("commercial_ops/not_found.html", entity=_("Installation")), 404
    try:
        replace_device_slot(installation, reason=request.form.get("reason", ""), actor_staff_user_id=actor.id)
    except DeviceSlotError as exc:
        return render_template("installations/detail.html", installation=installation, allowed_transitions=[], error=_localized_error_text(exc)), 400
    return redirect(url_for("installations.detail", installation_id=installation_id))


@bp.route("/licenses/<uuid:license_id>/slot-exceptions", methods=["GET"])
@require_permission("device_slot_exceptions.view")
def license_slot_exceptions(license_id):
    license_row = db_session.get(License, license_id)
    if license_row is None:
        return render_template("commercial_ops/not_found.html", entity=_("License")), 404
    exceptions = db_session.execute(
        select(DeviceSlotException).where(DeviceSlotException.license_id == license_id).order_by(DeviceSlotException.created_at.desc())
    ).scalars().all()
    effective_limit = resolve_effective_device_limit(license_row)
    return render_template("commercial_ops/slot_exceptions.html", license=license_row, exceptions=exceptions, effective_limit=effective_limit, error=None)


@bp.route("/licenses/<uuid:license_id>/slot-exceptions", methods=["POST"])
@require_permission("device_slot_exceptions.manage")
def create_slot_exception(license_id):
    actor = _staff()
    license_row = db_session.get(License, license_id)
    if license_row is None:
        return render_template("commercial_ops/not_found.html", entity=_("License")), 404
    try:
        expires_at = datetime.fromisoformat(request.form["expires_at"]).replace(tzinfo=timezone.utc)
        extra_slots = int(request.form["extra_slots"])
    except (KeyError, ValueError):
        exceptions = db_session.execute(select(DeviceSlotException).where(DeviceSlotException.license_id == license_id)).scalars().all()
        return render_template("commercial_ops/slot_exceptions.html", license=license_row, exceptions=exceptions, effective_limit=resolve_effective_device_limit(license_row), error=_("extra_slots must be an integer and expires_at a valid date/time.")), 400
    try:
        create_device_slot_exception(
            license_row=license_row, extra_slots=extra_slots, reason=request.form.get("reason", ""),
            expires_at=expires_at, actor_staff_user_id=actor.id,
        )
    except DeviceSlotError as exc:
        exceptions = db_session.execute(select(DeviceSlotException).where(DeviceSlotException.license_id == license_id)).scalars().all()
        return render_template("commercial_ops/slot_exceptions.html", license=license_row, exceptions=exceptions, effective_limit=resolve_effective_device_limit(license_row), error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.license_slot_exceptions", license_id=license_id))


@bp.route("/slot-exceptions/<uuid:exception_id>/revoke", methods=["POST"])
@require_permission("device_slot_exceptions.manage")
def revoke_slot_exception(exception_id):
    actor = _staff()
    exception = db_session.get(DeviceSlotException, exception_id)
    if exception is None:
        return render_template("commercial_ops/not_found.html", entity=_("Device slot exception")), 404
    try:
        revoke_device_slot_exception(exception, reason=request.form.get("reason", ""), actor_staff_user_id=actor.id)
    except DeviceSlotError as exc:
        exceptions = db_session.execute(select(DeviceSlotException).where(DeviceSlotException.license_id == exception.license_id)).scalars().all()
        return render_template("commercial_ops/slot_exceptions.html", license=exception.license, exceptions=exceptions, effective_limit=resolve_effective_device_limit(exception.license), error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.license_slot_exceptions", license_id=exception.license_id))


# -- Notifications ----------------------------------------------------------

@bp.route("/notifications", methods=["GET"])
@require_permission("subscriptions.view")
def list_notifications():
    status_filter = request.args.get("status")
    severity_filter = request.args.get("severity")
    role_filter = request.args.get("assigned_role_code")
    stmt = select(InternalNotification).order_by(InternalNotification.created_at.desc())
    if status_filter:
        stmt = stmt.where(InternalNotification.status == status_filter)
    else:
        stmt = stmt.where(InternalNotification.status.in_(("OPEN", "IN_PROGRESS", "ACKNOWLEDGED")))
    if severity_filter:
        stmt = stmt.where(InternalNotification.severity == severity_filter)
    if role_filter:
        stmt = stmt.where(InternalNotification.assigned_role_code == role_filter)
    notifications = db_session.execute(stmt).scalars().all()
    return render_template(
        "commercial_ops/notifications_list.html", notifications=notifications,
        status_filter=status_filter, severity_filter=severity_filter, role_filter=role_filter,
    )


@bp.route("/notifications/<uuid:notification_id>/assign", methods=["POST"])
@require_permission("subscriptions.view")
def assign_notification_route(notification_id):
    actor = _staff()
    notification = db_session.get(InternalNotification, notification_id)
    if notification is None:
        return render_template("commercial_ops/not_found.html", entity=_("Notification")), 404
    assignee_id = request.form.get("assignee_staff_user_id") or actor.id
    assign_notification(notification, actor.id, assignee_id)
    return redirect(url_for("commercial_ops_ui.list_notifications"))


@bp.route("/notifications/<uuid:notification_id>/acknowledge", methods=["POST"])
@require_permission("subscriptions.view")
def acknowledge_notification_route(notification_id):
    actor = _staff()
    notification = db_session.get(InternalNotification, notification_id)
    if notification is None:
        return render_template("commercial_ops/not_found.html", entity=_("Notification")), 404
    try:
        acknowledge_notification(notification, actor.id)
    except NotificationError as exc:
        notifications = db_session.execute(select(InternalNotification)).scalars().all()
        return render_template("commercial_ops/notifications_list.html", notifications=notifications, status_filter=None, severity_filter=None, role_filter=None, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.list_notifications"))


@bp.route("/notifications/<uuid:notification_id>/resolve", methods=["POST"])
@require_permission("subscriptions.view")
def resolve_notification_route(notification_id):
    actor = _staff()
    notification = db_session.get(InternalNotification, notification_id)
    if notification is None:
        return render_template("commercial_ops/not_found.html", entity=_("Notification")), 404
    try:
        resolve_notification(notification, actor.id, request.form.get("resolution", ""))
    except NotificationError as exc:
        notifications = db_session.execute(select(InternalNotification)).scalars().all()
        return render_template("commercial_ops/notifications_list.html", notifications=notifications, status_filter=None, severity_filter=None, role_filter=None, error=_localized_error_text(exc)), 400
    return redirect(url_for("commercial_ops_ui.list_notifications"))


# -- Operational queues -------------------------------------------------------

@bp.route("/queue", methods=["GET"])
@require_permission("subscriptions.view")
def queue_view():
    actor = _staff()
    available_roles = _staff_role_codes(actor)
    requested = request.args.get("role")
    role_code = requested if requested in available_roles else (available_roles[0] if available_roles else "VIEWER")
    try:
        snapshot = get_queue_for_role(role_code)
    except UnknownQueueRoleError:
        snapshot = get_queue_for_role("VIEWER")
        role_code = "VIEWER"
    return render_template("commercial_ops/queue.html", snapshot=snapshot, available_roles=available_roles or ["VIEWER"], role_code=role_code)


# -- Reconciliation -----------------------------------------------------------

@bp.route("/reconciliation", methods=["GET"])
@require_permission("system.view")
def reconciliation_view():
    result = run_reconciliation(dry_run=True)
    return render_template("commercial_ops/reconciliation.html", result=result)


@bp.route("/reconciliation/run", methods=["POST"])
@require_permission("system.manage_settings")
def reconciliation_run():
    result = run_reconciliation(dry_run=False)
    return render_template("commercial_ops/reconciliation.html", result=result, applied=True)


# -- Commercial timeline -------------------------------------------------------

@bp.route("/timeline/subscription/<uuid:subscription_id>", methods=["GET"])
@require_permission("subscriptions.view")
def subscription_timeline(subscription_id):
    subscription = db_session.get(Subscription, subscription_id)
    if subscription is None:
        return render_template("commercial_ops/not_found.html", entity=_("Subscription")), 404
    events = build_subscription_timeline(subscription_id)
    return render_template("commercial_ops/timeline.html", subscription=subscription, events=events)
