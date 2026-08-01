"""Phase 9.5C Milestone 16 -- Lead web routes.

Follows app/customers/routes.py's exact conventions: CSRF via the global
csrf.init_app(app) (no per-route exemption), Post/Redirect/Get, no
destructive GET, ownership enforced on every parent AND child route,
stable-code errors localized only here (never in the service layer) via
app.i18n_labels' localize_lead_error()/localize_customer_crm_error()/
localize_location_error().
"""
from __future__ import annotations

import uuid
from datetime import datetime

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from app.auth.session import load_current_staff
from app.customers.services import describe_duplicate_candidates_for_actor
from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.i18n_labels import localize_lead_error, localize_location_error
from app.leads.contacts import add_lead_contact, list_lead_contacts
from app.leads.conversion import DuplicateCustomerError, InvalidLeadStateError, convert
from app.leads.engagement import (
    cancel_customer_followup,
    cancel_lead_followup,
    complete_customer_followup,
    complete_lead_followup,
    create_lead_followup,
    followup_status,
    is_overdue,
    log_lead_interaction,
)
from app.leads.errors import LeadError, LocationValidationError
from app.leads.location import validate_location_fields, verify_location
from app.leads.notes import list_lead_notes_visible_to
from app.leads.services import add_lead_note, assign_lead, capture_location, change_lead_status, create_lead, list_all_leads, list_own_leads, update_lead
from app.models.base import utcnow
from app.models.leads import Lead, LeadAssignment, LeadStatusHistory
from app.security.rbac import get_staff_permission_codes, require_any_permission, require_permission

bp = Blueprint("leads", __name__, url_prefix="/leads")
# Phase 9.5C -- follow-ups and locations are shared between Lead and
# Customer parents, so their action routes live at the top level
# (/followups/<id>/..., /locations/<id>/verify per the governing spec's
# own suggested route list), a separate blueprint with no url_prefix
# rather than nested under /leads.
shared_bp = Blueprint("crm_shared", __name__)


def _localized_lead_error(exc) -> str:
    if isinstance(exc, (LeadError,)):
        return localize_lead_error(exc.code, **exc.params)
    if isinstance(exc, LocationValidationError):
        return localize_location_error(exc.code, **exc.params)
    return str(exc)


def _actor():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    return staff, profile


def _lead_or_none(lead_id, profile, *, all_held: bool):
    lead = db_session.get(Lead, lead_id)
    if lead is None:
        return None
    if not all_held and profile is not None:
        if lead.created_by_employee_profile_id != profile.id and lead.assigned_employee_profile_id != profile.id:
            return None
    return lead


@bp.route("/dashboard", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def crm_dashboard():
    from app.leads.dashboard import employee_crm_dashboard, management_crm_dashboard

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    is_management = "leads.view_all" in codes
    summary = management_crm_dashboard() if is_management else employee_crm_dashboard(profile.id)
    return render_template("leads/dashboard.html", summary=summary, is_management=is_management)


@bp.route("", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def list_leads():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    page = request.args.get("page", 1, type=int)
    status_filter = request.args.get("status") or None
    if "leads.view_all" in codes:
        result = list_all_leads(page=page)
    else:
        result = list_own_leads(profile.id, page=page)
    rows = [r for r in result["rows"] if not status_filter or r.status == status_filter]
    return render_template("leads/list.html", leads=rows, result=result, status_filter=status_filter)


@bp.route("/new", methods=["GET"])
@require_permission("leads.create")
def new_form():
    return render_template("leads/new.html", error=None)


@bp.route("", methods=["POST"])
@require_permission("leads.create")
def create():
    staff, profile = _actor()
    fields = {
        "organization_or_prospect_name": request.form.get("organization_or_prospect_name", "").strip(),
        "primary_contact_name": request.form.get("primary_contact_name") or None,
        "phone": request.form.get("phone") or None,
        "email": request.form.get("email") or None,
        "source": request.form.get("source") or "OTHER",
        "priority": request.form.get("priority") or "MEDIUM",
    }
    try:
        lead = create_lead(fields, profile.id, staff.id, idempotency_key=request.form.get("idempotency_key") or None)
    except LeadError as exc:
        return render_template("leads/new.html", error=_localized_lead_error(exc)), 400
    return redirect(url_for("leads.detail", lead_id=lead.id))


@bp.route("/<uuid:lead_id>", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def detail(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    is_management = "leads.view_all" in codes
    lead = _lead_or_none(lead_id, profile, all_held=is_management)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    contacts = list_lead_contacts(lead_id)
    notes = list_lead_notes_visible_to(lead_id, profile.id, is_management=is_management)
    status_history = db_session.query(LeadStatusHistory).filter_by(lead_id=lead_id).order_by(LeadStatusHistory.changed_at.desc()).all()
    assignment_history = db_session.query(LeadAssignment).filter_by(lead_id=lead_id).order_by(LeadAssignment.assigned_at.desc()).all()
    from app.models.leads import CustomerLocation, LeadFollowup, LeadInteraction

    interactions = db_session.query(LeadInteraction).filter_by(lead_id=lead_id).order_by(LeadInteraction.occurred_at.desc()).all()
    followups = db_session.query(LeadFollowup).filter_by(lead_id=lead_id).order_by(LeadFollowup.due_at.desc()).all()
    locations = db_session.query(CustomerLocation).filter_by(lead_id=lead_id, archived_at=None).order_by(CustomerLocation.captured_at.desc()).all()
    error_code = request.args.get("error")
    error_text = localize_lead_error(error_code) if error_code else None
    return render_template(
        "leads/detail.html", lead=lead, contacts=contacts, notes=notes, status_history=status_history,
        assignment_history=assignment_history, interactions=interactions, followups=followups, locations=locations,
        followup_status=followup_status, is_overdue=is_overdue, error=error_text,
    )


@bp.route("/<uuid:lead_id>/edit", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def update(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_none(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    fields = {k: v for k, v in request.form.items() if k in (
        "organization_or_prospect_name", "primary_contact_name", "phone", "email", "source", "priority",
    )}
    try:
        update_lead(lead, fields, profile.id, staff.id, expected_version=request.form.get("version", type=int))
    except LeadError as exc:
        return redirect(url_for("leads.detail", lead_id=lead_id, error=exc.code))
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<uuid:lead_id>/status", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def status(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_none(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    try:
        change_lead_status(
            lead, request.form.get("status"), profile.id, staff.id,
            reason=request.form.get("reason"), expected_version=request.form.get("version", type=int),
        )
    except LeadError as exc:
        return redirect(url_for("leads.detail", lead_id=lead_id, error=exc.code))
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<uuid:lead_id>/assign", methods=["POST"])
@require_permission("leads.assign")
def assign(lead_id):
    staff, profile = _actor()
    lead = db_session.get(Lead, lead_id)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    target = request.form.get("assigned_employee_profile_id")
    if target:
        try:
            assign_lead(
                lead, uuid.UUID(target), profile.id, staff.id,
                reason=request.form.get("reason"), expected_version=request.form.get("version", type=int),
            )
        except LeadError as exc:
            return redirect(url_for("leads.detail", lead_id=lead_id, error=exc.code))
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<uuid:lead_id>/convert", methods=["POST"])
@require_permission("leads.convert")
def convert_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_none(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    idempotency_key = request.form.get("idempotency_key") or str(uuid.uuid4())
    existing_customer_id = request.form.get("existing_customer_id") or None
    if existing_customer_id and "customers.view_all" not in codes:
        return redirect(url_for("leads.detail", lead_id=lead_id))
    try:
        customer = convert(
            lead, actor_staff_user_id=staff.id,
            existing_customer_id=uuid.UUID(existing_customer_id) if existing_customer_id else None,
            idempotency_key=idempotency_key, expected_version=request.form.get("version", type=int),
        )
    except (InvalidLeadStateError, LeadError):
        return redirect(url_for("leads.detail", lead_id=lead_id))
    except DuplicateCustomerError as exc:
        described = describe_duplicate_candidates_for_actor(exc.candidates, staff.id, codes)
        return render_template("leads/detail.html", lead=lead, duplicate_candidates=described, error=None), 409
    return redirect(url_for("customers.detail", customer_id=customer.id))


@bp.route("/<uuid:lead_id>/archive", methods=["POST"])
@require_permission("leads.archive")
def archive(lead_id):
    staff, profile = _actor()
    lead = db_session.get(Lead, lead_id)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    try:
        change_lead_status(lead, "ARCHIVED", profile.id, staff.id, reason=request.form.get("reason"))
    except LeadError:
        pass
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<uuid:lead_id>/contacts", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def add_contact_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_none(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    fields = {
        "name": request.form.get("name", "").strip(),
        "title": request.form.get("title") or None,
        "business_email": request.form.get("business_email") or None,
        "business_phone": request.form.get("business_phone") or None,
        "is_primary": bool(request.form.get("is_primary")),
    }
    try:
        add_lead_contact(lead_id, fields, staff.id)
    except LeadError:
        pass
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<uuid:lead_id>/interactions", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def add_interaction_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_none(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    try:
        log_lead_interaction(lead_id, {"interaction_type": request.form.get("interaction_type"), "summary": request.form.get("summary")}, profile.id, staff.id)
    except LeadError:
        pass
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<uuid:lead_id>/followups", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def add_followup_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_none(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    due_raw = request.form.get("due_at")
    try:
        create_lead_followup(lead_id, {"due_at": datetime.fromisoformat(due_raw) if due_raw else None, "notes": request.form.get("notes")}, profile.id, staff.id)
    except LeadError:
        pass
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<uuid:lead_id>/notes", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def add_note_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_none(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    try:
        add_lead_note(lead, request.form.get("body", ""), profile.id, staff.id, visibility=request.form.get("visibility", "ASSIGNED_RECORD_USERS"))
    except LeadError:
        pass
    return redirect(url_for("leads.detail", lead_id=lead_id))


@bp.route("/<uuid:lead_id>/locations", methods=["POST"])
@require_permission("customers.capture_location")
def add_location_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_none(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "not_found"}), 404
    body = {
        "latitude": request.form.get("latitude"),
        "longitude": request.form.get("longitude"),
        "accuracy_meters": request.form.get("accuracy_meters"),
        "source": request.form.get("source") or "GPS",
        "manual_address": request.form.get("manual_address") or None,
    }
    try:
        cleaned = validate_location_fields(body, now=utcnow())
        capture_location(
            lead_id=lead_id, customer_id=None,
            fields={k: cleaned.get(k) for k in ("latitude", "longitude", "accuracy_meters", "source", "manual_address")},
            actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id,
        )
    except LocationValidationError:
        pass
    return redirect(url_for("leads.detail", lead_id=lead_id))


# --------------------------------------------------- shared child actions --

@shared_bp.route("/followups/<uuid:followup_id>/complete", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def complete_followup(followup_id):
    from app.models.leads import CustomerFollowup, LeadFollowup

    staff, _ = _actor()
    row = db_session.get(LeadFollowup, followup_id)
    if row is not None:
        complete_lead_followup(row, staff.id)
        return redirect(url_for("leads.detail", lead_id=row.lead_id))
    row = db_session.get(CustomerFollowup, followup_id)
    if row is not None:
        complete_customer_followup(row, staff.id)
        return redirect(url_for("customers.detail", customer_id=row.customer_id))
    return jsonify({"error": "not_found"}), 404


@shared_bp.route("/followups/<uuid:followup_id>/cancel", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def cancel_followup(followup_id):
    from app.models.leads import CustomerFollowup, LeadFollowup

    staff, _ = _actor()
    reason = request.form.get("reason")
    row = db_session.get(LeadFollowup, followup_id)
    if row is not None:
        try:
            cancel_lead_followup(row, reason, staff.id)
        except LeadError:
            pass
        return redirect(url_for("leads.detail", lead_id=row.lead_id))
    row = db_session.get(CustomerFollowup, followup_id)
    if row is not None:
        try:
            cancel_customer_followup(row, reason, staff.id)
        except LeadError:
            pass
        return redirect(url_for("customers.detail", customer_id=row.customer_id))
    return jsonify({"error": "not_found"}), 404


@shared_bp.route("/locations/<uuid:location_id>/verify", methods=["POST"])
@require_permission("customers.verify_location")
def verify_location_route(location_id):
    from app.models.leads import CustomerLocation

    staff, profile = _actor()
    location = db_session.get(CustomerLocation, location_id)
    if location is None:
        return jsonify({"error": "not_found"}), 404
    try:
        verify_location(location, request.form.get("reason"), profile.id, staff.id)
    except LocationValidationError:
        pass
    if location.lead_id:
        return redirect(url_for("leads.detail", lead_id=location.lead_id))
    return redirect(url_for("customers.detail", customer_id=location.customer_id))
