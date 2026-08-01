"""Phase 9.5C Milestone 15 -- CRM subset of /api/operations/v1.

Same blueprint prefix as app/api_operations/routes.py (a second Blueprint
object, registered separately in app/__init__.py -- Flask allows several
blueprints sharing one url_prefix as long as blueprint/endpoint names
differ), same cookie-session auth + CSRF protection, same conventions
(jsonify({"error": CODE, "message": ...}), version param for optimistic
locking, page/page_size/total pagination shape).

Delegates to the exact same service functions the web routes (Milestone
16) use -- no CRM business logic lives in this file, only request
parsing, permission/ownership checks, and response serialization.
"""
from __future__ import annotations

import uuid

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.customers.services import (
    DUPLICATE_REVIEW_MARKER,
    assign_customer,
    customer_visible_to_actor,
    describe_duplicate_candidates_for_actor,
    find_duplicate_candidates,
    update_customer,
)
from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.leads.contacts import add_lead_contact, list_lead_contacts, update_lead_contact
from app.leads.conversion import DuplicateCustomerError, InvalidLeadStateError, convert
from app.leads.engagement import (
    cancel_customer_followup,
    cancel_lead_followup,
    complete_customer_followup,
    complete_lead_followup,
    create_customer_followup,
    create_lead_followup,
    followup_status,
    is_overdue,
    list_own_lead_followups_due_today,
    list_own_lead_followups_overdue,
    log_customer_interaction,
    log_lead_interaction,
)
from app.leads.errors import CustomerCrmError, LeadError, LocationValidationError
from app.leads.location import validate_location_fields, verify_location
from app.leads.notes import list_customer_notes_visible_to, list_lead_notes_visible_to
from app.leads.ownership import apply_ownership_filter
from app.leads.services import (
    assign_lead,
    capture_location,
    change_lead_status,
    create_lead,
    list_all_leads,
    list_own_leads,
    update_lead,
)
from app.customers.services import add_contact as add_customer_contact, add_note as add_customer_note
from app.models.customers import Customer, CustomerContact
from app.models.employees import EmployeeProfile
from app.models.leads import CustomerAssignment, Lead, LeadAssignment, LeadStatusHistory
from app.security.rbac import get_staff_permission_codes, require_any_permission, require_permission
from app.services.pagination import DEFAULT_PAGE_SIZE

bp = Blueprint("api_operations_crm", __name__, url_prefix="/api/operations/v1")


def _iso(value):
    return value.isoformat() if value is not None else None


def _actor():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    return staff, profile


def _pagination_response(rows_serialized, result):
    return {
        "rows": rows_serialized,
        "page": result["page"],
        "page_size": result["page_size"],
        "total": result["total"],
        "total_pages": result["total_pages"],
    }


# ---------------------------------------------------------------- Leads --

def _serialize_lead(lead: Lead) -> dict:
    return {
        "id": str(lead.id),
        "organization_or_prospect_name": lead.organization_or_prospect_name,
        "primary_contact_name": lead.primary_contact_name,
        "phone": lead.phone,
        "email": lead.email,
        "source": lead.source,
        "status": lead.status,
        "priority": lead.priority,
        "assigned_employee_profile_id": str(lead.assigned_employee_profile_id) if lead.assigned_employee_profile_id else None,
        "created_by_employee_profile_id": str(lead.created_by_employee_profile_id),
        "estimated_value": str(lead.estimated_value) if lead.estimated_value is not None else None,
        "currency": lead.currency,
        "next_follow_up_at": _iso(lead.next_follow_up_at),
        "last_interaction_at": _iso(lead.last_interaction_at),
        "location_summary": lead.location_summary,
        "created_at": _iso(lead.created_at),
        "updated_at": _iso(lead.updated_at),
        "converted_at": _iso(lead.converted_at),
        "lost_at": _iso(lead.lost_at),
        "archived_at": _iso(lead.archived_at),
        "version": lead.version,
    }


def _lead_or_404(lead_id, actor_profile, *, all_held: bool):
    """Fails CLOSED: an actor with no view_all/update_all AND no
    EmployeeProfile is denied, never granted by default because the
    ownership check had nothing to compare against. Mirrors the identical
    fix in app/leads/routes.py:_lead_or_none (Milestone 23 finding)."""
    lead = db_session.get(Lead, lead_id)
    if lead is None:
        return None
    if not all_held:
        if actor_profile is None:
            return None
        visible = lead.created_by_employee_profile_id == actor_profile.id or lead.assigned_employee_profile_id == actor_profile.id
        if not visible:
            return None
    return lead


@bp.route("/leads", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def list_leads_route():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", DEFAULT_PAGE_SIZE, type=int), 100)
    if "leads.view_all" in codes:
        result = list_all_leads(page=page, page_size=page_size)
    else:
        result = list_own_leads(profile.id, page=page, page_size=page_size)
    return jsonify(_pagination_response([_serialize_lead(r) for r in result["rows"]], result))


@bp.route("/leads", methods=["POST"])
@require_permission("leads.create")
def create_lead_route():
    staff, profile = _actor()
    if profile is None:
        return jsonify({"error": "EMPLOYEE_PROFILE_REQUIRED", "message": "This action requires a real employee profile."}), 400
    body = request.get_json(silent=True) or {}
    try:
        lead = create_lead(
            {k: v for k, v in body.items() if k in (
                "organization_or_prospect_name", "primary_contact_name", "phone", "email", "source", "priority",
                "estimated_value", "currency", "location_summary", "assigned_employee_profile_id",
            )},
            profile.id, staff.id,
            idempotency_key=body.get("idempotency_key"),
        )
    except LeadError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_lead(lead)), 201


@bp.route("/leads/<uuid:lead_id>", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def get_lead_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.view_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    return jsonify(_serialize_lead(lead))


@bp.route("/leads/<uuid:lead_id>", methods=["PATCH"])
@require_any_permission("leads.update_own", "leads.update_all")
def update_lead_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        update_lead(lead, body, profile.id, staff.id, expected_version=body.get("version"))
    except LeadError as exc:
        status = 409 if exc.code == "STALE_LEAD_VERSION" else 400
        return jsonify({"error": exc.code, "message": str(exc)}), status
    return jsonify(_serialize_lead(lead))


@bp.route("/leads/<uuid:lead_id>/status", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def lead_status_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        change_lead_status(
            lead, body.get("status"), profile.id, staff.id, reason=body.get("reason"),
            expected_version=body.get("version"),
        )
    except LeadError as exc:
        status = 409 if exc.code == "STALE_LEAD_VERSION" else 400
        return jsonify({"error": exc.code, "message": str(exc)}), status
    return jsonify(_serialize_lead(lead))


@bp.route("/leads/<uuid:lead_id>/assign", methods=["POST"])
@require_permission("leads.assign")
def lead_assign_route(lead_id):
    staff, profile = _actor()
    lead = db_session.get(Lead, lead_id)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    target = body.get("assigned_employee_profile_id")
    if not target:
        return jsonify({"error": "VALIDATION_ERROR", "message": "assigned_employee_profile_id is required"}), 400
    try:
        assign_lead(
            lead, uuid.UUID(target), profile.id, staff.id,
            reason=body.get("reason"), expected_version=body.get("version"),
        )
    except LeadError as exc:
        status = 409 if exc.code == "STALE_LEAD_VERSION" else 400
        return jsonify({"error": exc.code, "message": str(exc)}), status
    return jsonify(_serialize_lead(lead))


@bp.route("/leads/<uuid:lead_id>/convert", methods=["POST"])
@require_permission("leads.convert")
def lead_convert_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    idempotency_key = body.get("idempotency_key")
    if not idempotency_key:
        return jsonify({"error": "VALIDATION_ERROR", "message": "idempotency_key is required"}), 400
    existing_customer_id = body.get("existing_customer_id")
    if existing_customer_id and "customers.view_all" not in codes:
        return jsonify({"error": "PERMISSION_DENIED", "message": "Linking to an existing customer requires management authority."}), 403
    try:
        customer = convert(
            lead, actor_staff_user_id=staff.id,
            existing_customer_id=uuid.UUID(existing_customer_id) if existing_customer_id else None,
            idempotency_key=idempotency_key,
            expected_version=body.get("version"),
        )
    except InvalidLeadStateError as exc:
        return jsonify({"error": "INVALID_LEAD_STATE", "message": str(exc)}), 409
    except DuplicateCustomerError as exc:
        described = describe_duplicate_candidates_for_actor(exc.candidates, staff.id, codes)
        return jsonify({"error": "DUPLICATE_CUSTOMER", "candidates": described}), 409
    except LeadError as exc:
        status = 409 if exc.code in ("STALE_LEAD_VERSION", "IDEMPOTENCY_CONFLICT") else 400
        return jsonify({"error": exc.code, "message": str(exc)}), status
    return jsonify({"customer_id": str(customer.id), "lead": _serialize_lead(lead)}), 201


@bp.route("/leads/<uuid:lead_id>/archive", methods=["POST"])
@require_permission("leads.archive")
def lead_archive_route(lead_id):
    staff, profile = _actor()
    lead = db_session.get(Lead, lead_id)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        change_lead_status(lead, "ARCHIVED", profile.id, staff.id, reason=body.get("reason"), expected_version=body.get("version"))
    except LeadError as exc:
        status = 409 if exc.code == "STALE_LEAD_VERSION" else 400
        return jsonify({"error": exc.code, "message": str(exc)}), status
    return jsonify(_serialize_lead(lead))


@bp.route("/leads/<uuid:lead_id>/status-history", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def lead_status_history_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.view_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    rows = db_session.execute(
        select(LeadStatusHistory).where(LeadStatusHistory.lead_id == lead_id).order_by(LeadStatusHistory.changed_at)
    ).scalars().all()
    return jsonify({"rows": [
        {"from_status": r.from_status, "to_status": r.to_status, "reason": r.reason, "changed_at": _iso(r.changed_at),
         "changed_by_employee_profile_id": str(r.changed_by_employee_profile_id)}
        for r in rows
    ]})


@bp.route("/leads/<uuid:lead_id>/assignment-history", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def lead_assignment_history_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.view_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    rows = db_session.execute(
        select(LeadAssignment).where(LeadAssignment.lead_id == lead_id).order_by(LeadAssignment.assigned_at)
    ).scalars().all()
    return jsonify({"rows": [
        {"assigned_to_employee_profile_id": str(r.assigned_to_employee_profile_id),
         "assigned_by_employee_profile_id": str(r.assigned_by_employee_profile_id),
         "assigned_at": _iso(r.assigned_at), "unassigned_at": _iso(r.unassigned_at), "reason": r.reason}
        for r in rows
    ]})


# ----------------------------------------------------------- Customers --

def _serialize_customer(customer: Customer) -> dict:
    return {
        "id": str(customer.id),
        "legal_name": customer.legal_name,
        "trade_name": customer.trade_name,
        "lifecycle_status": customer.lifecycle_status,
        "assigned_sales_staff_id": str(customer.assigned_sales_staff_id) if customer.assigned_sales_staff_id else None,
        "converted_from_lead_id": str(customer.converted_from_lead_id) if customer.converted_from_lead_id else None,
        "archived_at": _iso(customer.archived_at),
        "version": customer.version,
    }


@bp.route("/customers", methods=["GET"])
@require_permission("customers.view_own")
def list_customers_route():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    page = request.args.get("page", 1, type=int)
    page_size = min(request.args.get("page_size", DEFAULT_PAGE_SIZE, type=int), 100)
    stmt = select(Customer).order_by(Customer.created_at.desc())
    if "customers.view_all" not in codes:
        stmt = apply_ownership_filter(stmt, Customer, profile.id, all_permission_held=False)
    from app.services.pagination import paginate

    result = paginate(stmt, page, page_size)
    return jsonify(_pagination_response([_serialize_customer(r) for r in result["rows"]], result))


@bp.route("/customers/<uuid:customer_id>", methods=["GET"])
@require_permission("customers.view_own")
def get_customer_route(customer_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    return jsonify(_serialize_customer(customer))


@bp.route("/customers/<uuid:customer_id>", methods=["PATCH"])
@require_permission("customers.update_own")
def update_customer_route(customer_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    if body.get("version") is not None and customer.version != body["version"]:
        return jsonify({"error": "STALE_CUSTOMER_VERSION"}), 409
    fields = {k: v for k, v in body.items() if k in ("legal_name", "trade_name", "city", "country")}
    update_customer(customer, fields, staff.id)
    return jsonify(_serialize_customer(customer))


@bp.route("/customers/<uuid:customer_id>/assign", methods=["POST"])
@require_permission("customers.assign")
def customer_assign_route(customer_id):
    staff, profile = _actor()
    customer = db_session.get(Customer, customer_id)
    if customer is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    target = body.get("assigned_to_staff_user_id")
    if not target:
        return jsonify({"error": "VALIDATION_ERROR", "message": "assigned_to_staff_user_id is required"}), 400
    try:
        assign_customer(customer, uuid.UUID(target), staff.id, reason=body.get("reason"), expected_version=body.get("version"))
    except CustomerCrmError as exc:
        status = 409 if exc.code == "STALE_CUSTOMER_VERSION" else 400
        return jsonify({"error": exc.code, "message": str(exc)}), status
    return jsonify(_serialize_customer(customer))


@bp.route("/customers/<uuid:customer_id>/archive", methods=["POST"])
@require_permission("customers.archive")
def customer_archive_route(customer_id):
    from app.customers.services import archive_customer

    staff, _ = _actor()
    customer = db_session.get(Customer, customer_id)
    if customer is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    archive_customer(customer, staff.id)
    return jsonify(_serialize_customer(customer))


@bp.route("/customers/<uuid:customer_id>/assignment-history", methods=["GET"])
@require_permission("customers.view_own")
def customer_assignment_history_route(customer_id):
    staff, _ = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    rows = db_session.execute(
        select(CustomerAssignment).where(CustomerAssignment.customer_id == customer_id).order_by(CustomerAssignment.assigned_at)
    ).scalars().all()
    return jsonify({"rows": [
        {"assigned_to_staff_user_id": str(r.assigned_to_staff_user_id), "assigned_by_staff_user_id": str(r.assigned_by_staff_user_id),
         "assigned_at": _iso(r.assigned_at), "unassigned_at": _iso(r.unassigned_at), "reason": r.reason}
        for r in rows
    ]})


# ------------------------------------------------------------ Contacts --

def _serialize_lead_contact(c) -> dict:
    return {"id": str(c.id), "name": c.name, "title": c.title, "business_email": c.business_email,
            "business_phone": c.business_phone, "is_primary": c.is_primary, "version": c.version}


@bp.route("/leads/<uuid:lead_id>/contacts", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def list_lead_contacts_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.view_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    return jsonify({"rows": [_serialize_lead_contact(c) for c in list_lead_contacts(lead_id)]})


@bp.route("/leads/<uuid:lead_id>/contacts", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def add_lead_contact_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        contact = add_lead_contact(lead_id, body, staff.id)
    except LeadError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_lead_contact(contact)), 201


@bp.route("/customers/<uuid:customer_id>/contacts", methods=["GET"])
@require_permission("customers.view_own")
def list_customer_contacts_route(customer_id):
    staff, _ = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    rows = db_session.execute(select(CustomerContact).where(CustomerContact.customer_id == customer_id, CustomerContact.archived_at.is_(None))).scalars().all()
    return jsonify({"rows": [_serialize_lead_contact(c) for c in rows]})


@bp.route("/customers/<uuid:customer_id>/contacts", methods=["POST"])
@require_permission("customers.manage_contacts")
def add_customer_contact_route(customer_id):
    staff, _ = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    contact = add_customer_contact(customer, {k: v for k, v in body.items() if k in ("name", "title", "business_email", "business_phone", "preferred_channel", "is_primary", "notes")}, staff.id)
    return jsonify(_serialize_lead_contact(contact)), 201


# --------------------------------------------------------- Interactions --

def _serialize_interaction(i) -> dict:
    return {"id": str(i.id), "interaction_type": i.interaction_type, "summary": i.summary, "occurred_at": _iso(i.occurred_at)}


@bp.route("/leads/<uuid:lead_id>/interactions", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def list_lead_interactions_route(lead_id):
    from app.models.leads import LeadInteraction

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.view_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    rows = db_session.execute(select(LeadInteraction).where(LeadInteraction.lead_id == lead_id).order_by(LeadInteraction.occurred_at.desc())).scalars().all()
    return jsonify({"rows": [_serialize_interaction(r) for r in rows]})


@bp.route("/leads/<uuid:lead_id>/interactions", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def add_lead_interaction_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        interaction = log_lead_interaction(lead_id, body, profile.id, staff.id)
    except LeadError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_interaction(interaction)), 201


@bp.route("/customers/<uuid:customer_id>/interactions", methods=["GET"])
@require_permission("customers.view_own")
def list_customer_interactions_route(customer_id):
    from app.models.leads import CustomerInteraction

    staff, _ = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    rows = db_session.execute(select(CustomerInteraction).where(CustomerInteraction.customer_id == customer_id).order_by(CustomerInteraction.occurred_at.desc())).scalars().all()
    return jsonify({"rows": [_serialize_interaction(r) for r in rows]})


@bp.route("/customers/<uuid:customer_id>/interactions", methods=["POST"])
@require_permission("customers.update_own")
def add_customer_interaction_route(customer_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        interaction = log_customer_interaction(customer_id, body, profile.id, staff.id)
    except LeadError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_interaction(interaction)), 201


# ----------------------------------------------------------- Follow-ups --

def _serialize_followup(f) -> dict:
    return {"id": str(f.id), "due_at": _iso(f.due_at), "notes": f.notes, "status": followup_status(f), "overdue": is_overdue(f)}


@bp.route("/followups", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def list_followups_route():
    staff, profile = _actor()
    view = request.args.get("view", "due_today")
    if view == "overdue":
        result = list_own_lead_followups_overdue(profile.id)
    else:
        result = list_own_lead_followups_due_today(profile.id)
    return jsonify(_pagination_response([_serialize_followup(r) for r in result["rows"]], result))


@bp.route("/leads/<uuid:lead_id>/followups", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def create_lead_followup_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    if body.get("due_at"):
        from datetime import datetime
        body = {**body, "due_at": datetime.fromisoformat(body["due_at"])}
    try:
        followup = create_lead_followup(lead_id, body, profile.id, staff.id)
    except LeadError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_followup(followup)), 201


@bp.route("/customers/<uuid:customer_id>/followups", methods=["POST"])
@require_permission("customers.update_own")
def create_customer_followup_route(customer_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    if body.get("due_at"):
        from datetime import datetime
        body = {**body, "due_at": datetime.fromisoformat(body["due_at"])}
    try:
        followup = create_customer_followup(customer_id, body, profile.id, staff.id)
    except LeadError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_followup(followup)), 201


def _resolve_followup_with_parent_check(followup_id, staff, profile, codes):
    """Loads a follow-up by UUID and verifies the actor may access its
    PARENT Lead/Customer before returning it -- closes the child-resource
    IDOR gap flagged in crm-operations-api-report.md: a follow-up UUID
    alone must never be sufficient to act on it."""
    from app.models.leads import CustomerFollowup, LeadFollowup

    followup = db_session.get(LeadFollowup, followup_id)
    if followup is not None:
        lead = _lead_or_404(followup.lead_id, profile, all_held="leads.update_all" in codes)
        return followup if lead is not None else None
    followup = db_session.get(CustomerFollowup, followup_id)
    if followup is not None:
        customer = db_session.get(Customer, followup.customer_id)
        if customer is not None and customer_visible_to_actor(customer, staff.id, codes):
            return followup
    return None


@bp.route("/followups/<uuid:followup_id>/complete", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def complete_followup_route(followup_id):
    from app.models.leads import LeadFollowup

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    followup = _resolve_followup_with_parent_check(followup_id, staff, profile, codes)
    if followup is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    try:
        if isinstance(followup, LeadFollowup):
            complete_lead_followup(followup, staff.id)
        else:
            complete_customer_followup(followup, staff.id)
    except LeadError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_followup(followup))


@bp.route("/followups/<uuid:followup_id>/cancel", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def cancel_followup_route(followup_id):
    from app.models.leads import LeadFollowup

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    followup = _resolve_followup_with_parent_check(followup_id, staff, profile, codes)
    if followup is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        if isinstance(followup, LeadFollowup):
            cancel_lead_followup(followup, body.get("reason"), staff.id)
        else:
            cancel_customer_followup(followup, body.get("reason"), staff.id)
    except LeadError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_followup(followup))


# ---------------------------------------------------------------- Notes --

@bp.route("/leads/<uuid:lead_id>/notes", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def list_lead_notes_route(lead_id):
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.view_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    is_management = "leads.view_all" in codes
    notes = list_lead_notes_visible_to(lead_id, profile.id, is_management=is_management)
    return jsonify({"rows": [{"id": str(n.id), "body": n.body, "visibility": n.visibility, "created_at": _iso(n.created_at)} for n in notes]})


@bp.route("/leads/<uuid:lead_id>/notes", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def add_lead_note_route(lead_id):
    from app.leads.services import add_lead_note

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        note = add_lead_note(lead, body.get("body", ""), profile.id, staff.id, visibility=body.get("visibility", "ASSIGNED_RECORD_USERS"))
    except LeadError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify({"id": str(note.id), "body": note.body, "visibility": note.visibility}), 201


@bp.route("/customers/<uuid:customer_id>/notes", methods=["GET"])
@require_permission("customers.view_own")
def list_customer_notes_route(customer_id):
    staff, _ = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    is_management = "customers.view_all" in codes
    notes = list_customer_notes_visible_to(customer_id, staff.id, is_management=is_management)
    return jsonify({"rows": [{"id": str(n.id), "body": n.body, "visibility": n.visibility, "created_at": _iso(n.created_at)} for n in notes]})


@bp.route("/customers/<uuid:customer_id>/notes", methods=["POST"])
@require_permission("customers.manage_notes")
def add_customer_note_route(customer_id):
    staff, _ = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        note = add_customer_note(customer, body.get("body", ""), staff.id, visibility=body.get("visibility", "ASSIGNED_RECORD_USERS"))
    except CustomerCrmError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify({"id": str(note.id), "body": note.body, "visibility": note.visibility}), 201


# ------------------------------------------------------------ Locations --

def _serialize_location(loc) -> dict:
    return {
        "id": str(loc.id), "latitude": str(loc.latitude) if loc.latitude is not None else None,
        "longitude": str(loc.longitude) if loc.longitude is not None else None,
        "accuracy_meters": str(loc.accuracy_meters) if loc.accuracy_meters is not None else None,
        "source": loc.source, "manual_address": loc.manual_address, "verified": loc.verified,
        "verification_method": loc.verification_method, "captured_at": _iso(loc.captured_at), "version": loc.version,
    }


@bp.route("/leads/<uuid:lead_id>/locations", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def list_lead_locations_route(lead_id):
    from app.models.leads import CustomerLocation

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.view_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    rows = db_session.execute(select(CustomerLocation).where(CustomerLocation.lead_id == lead_id, CustomerLocation.archived_at.is_(None)).order_by(CustomerLocation.captured_at.desc())).scalars().all()
    return jsonify({"rows": [_serialize_location(r) for r in rows]})


@bp.route("/leads/<uuid:lead_id>/locations", methods=["POST"])
@require_permission("customers.capture_location")
def capture_lead_location_route(lead_id):
    from app.models.base import utcnow

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    lead = _lead_or_404(lead_id, profile, all_held="leads.update_all" in codes)
    if lead is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        cleaned = validate_location_fields(body, now=utcnow())
        location = capture_location(
            lead_id=lead_id, customer_id=None,
            fields={k: cleaned.get(k) for k in ("latitude", "longitude", "accuracy_meters", "source", "manual_address")},
            actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id,
        )
    except LocationValidationError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_location(location)), 201


@bp.route("/customers/<uuid:customer_id>/locations", methods=["GET"])
@require_permission("customers.view_own")
def list_customer_locations_route(customer_id):
    from app.models.leads import CustomerLocation

    staff, _ = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    rows = db_session.execute(select(CustomerLocation).where(CustomerLocation.customer_id == customer_id, CustomerLocation.archived_at.is_(None)).order_by(CustomerLocation.captured_at.desc())).scalars().all()
    return jsonify({"rows": [_serialize_location(r) for r in rows]})


@bp.route("/customers/<uuid:customer_id>/locations", methods=["POST"])
@require_permission("customers.capture_location")
def capture_customer_location_route(customer_id):
    from app.models.base import utcnow

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not customer_visible_to_actor(customer, staff.id, codes):
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        cleaned = validate_location_fields(body, now=utcnow())
        location = capture_location(
            lead_id=None, customer_id=customer_id,
            fields={k: cleaned.get(k) for k in ("latitude", "longitude", "accuracy_meters", "source", "manual_address")},
            actor_employee_profile_id=profile.id, actor_staff_user_id=staff.id,
        )
    except LocationValidationError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_location(location)), 201


@bp.route("/locations/<uuid:location_id>/verify", methods=["POST"])
@require_permission("customers.verify_location")
def verify_location_route(location_id):
    from app.models.leads import CustomerLocation

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    location = db_session.get(CustomerLocation, location_id)
    # IDOR fix: customers.verify_location alone must not let the holder
    # verify ANY location by UUID -- the location's parent Lead/Customer
    # must also be one the actor can access.
    if location is not None:
        if location.lead_id:
            parent_ok = _lead_or_404(location.lead_id, profile, all_held="leads.update_all" in codes) is not None
        else:
            customer = db_session.get(Customer, location.customer_id)
            parent_ok = customer is not None and customer_visible_to_actor(customer, staff.id, codes)
        if not parent_ok:
            location = None
    if location is None:
        return jsonify({"error": "RECORD_NOT_FOUND"}), 404
    body = request.get_json(silent=True) or {}
    try:
        verify_location(location, body.get("reason"), profile.id, staff.id)
    except LocationValidationError as exc:
        return jsonify({"error": exc.code, "message": str(exc)}), 400
    return jsonify(_serialize_location(location))
