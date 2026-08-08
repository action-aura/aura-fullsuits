"""Customer organization routes (Part K)."""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from app.auth.session import load_current_staff
from app.customers.services import (
    add_contact,
    add_note,
    archive_customer,
    assign_customer,
    create_customer,
    customer_visible_to_actor,
    find_duplicate_candidates,
    list_customers as list_customers_query,
    update_customer,
)
from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.leads.engagement import create_customer_followup, log_customer_interaction
from app.leads.errors import CustomerCrmError, LeadError, LocationValidationError
from app.leads.location import validate_location_fields
from app.leads.notes import list_customer_notes_visible_to
from app.leads.services import capture_location
from app.models.base import utcnow
from app.models.customers import Customer
from app.models.leads import CustomerAssignment, CustomerFollowup, CustomerInteraction, CustomerLocation
from app.security.rbac import get_staff_permission_codes, require_any_permission, require_permission

bp = Blueprint("customers", __name__, url_prefix="/customers")


def _customer_visible_to(customer: Customer, actor) -> bool:
    return customer_visible_to_actor(customer, actor.id, get_staff_permission_codes(actor))


@bp.route("", methods=["GET"])
@require_permission("customers.view")
def list_customers():
    actor = load_current_staff()
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    page = request.args.get("page", 1, type=int)
    # Phase 9.5C Milestone 3 -- customers.view only gates entry to this
    # route; the actual visibility SCOPE is customers.view_own (assigned
    # only) vs customers.view_all (everyone), enforced server-side via the
    # same shared apply_ownership_filter() every Lead query uses (never a
    # second, independently-written filter -- see
    # docs/owner/phase9_5c/crm-record-ownership-contract.md). UI
    # modernization Stage D -- filtering/sort/pagination now live in
    # customers.services.list_customers(), same factoring as
    # leads.services.list_own_leads/list_all_leads.
    codes = get_staff_permission_codes(actor)
    all_held = "customers.view_all" in codes
    profile = None if all_held else find_own_profile(actor.id)
    result = list_customers_query(
        page=page, status=status_filter, search=search, sort=sort, direction=direction,
        actor_employee_profile_id=profile.id if profile else None, all_permission_held=all_held,
    )
    return render_template(
        "customers/list.html", result=result, status_filter=status_filter, search_value=search,
        sort=sort, direction=direction,
    )


@bp.route("/new", methods=["GET"])
@require_permission("customers.create")
def new_form():
    return render_template("customers/new.html", duplicates=[])


@bp.route("", methods=["POST"])
@require_permission("customers.create")
def create():
    actor = load_current_staff()
    legal_name = request.form.get("legal_name", "").strip()
    duplicates = find_duplicate_candidates(
        legal_name,
        request.form.get("commercial_registration_reference") or None,
        request.form.get("contact_email") or None,
        None,
    )
    if duplicates and not request.form.get("confirm_duplicate"):
        return render_template("customers/new.html", duplicates=duplicates, form=request.form)

    customer = create_customer(
        {
            "legal_name": legal_name,
            "trade_name": request.form.get("trade_name") or None,
            "organization_type": request.form.get("organization_type") or None,
            "country": request.form.get("country") or None,
            "city": request.form.get("city") or None,
            "commercial_registration_reference": request.form.get("commercial_registration_reference") or None,
            "acquisition_source": request.form.get("acquisition_source") or None,
        },
        actor.id,
    )
    return redirect(url_for("customers.detail", customer_id=customer.id))


@bp.route("/<uuid:customer_id>", methods=["GET"])
@require_permission("customers.view")
def detail(customer_id):
    actor = load_current_staff()
    codes = get_staff_permission_codes(actor)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not _customer_visible_to(customer, actor):
        return jsonify({"error": "not_found"}), 404
    from app.leads.engagement import followup_status, is_overdue

    is_management = "customers.view_all" in codes
    notes = list_customer_notes_visible_to(customer_id, actor.id, is_management=is_management)
    interactions = db_session.query(CustomerInteraction).filter_by(customer_id=customer_id).order_by(CustomerInteraction.occurred_at.desc()).all()
    followups = db_session.query(CustomerFollowup).filter_by(customer_id=customer_id).order_by(CustomerFollowup.due_at.desc()).all()
    locations = db_session.query(CustomerLocation).filter_by(customer_id=customer_id, archived_at=None).order_by(CustomerLocation.captured_at.desc()).all()
    assignment_history = db_session.query(CustomerAssignment).filter_by(customer_id=customer_id).order_by(CustomerAssignment.assigned_at.desc()).all()
    error_code = request.args.get("error")
    from app.i18n_labels import localize_customer_crm_error

    error_text = localize_customer_crm_error(error_code) if error_code else None
    return render_template(
        "customers/detail.html", customer=customer, notes=notes, interactions=interactions, followups=followups,
        locations=locations, assignment_history=assignment_history, followup_status=followup_status,
        is_overdue=is_overdue, error=error_text,
    )


@bp.route("/<uuid:customer_id>", methods=["POST"])
@require_permission("customers.update")
def update(customer_id):
    actor = load_current_staff()
    customer = db_session.get(Customer, customer_id)
    if customer is None or not _customer_visible_to(customer, actor):
        return jsonify({"error": "not_found"}), 404
    # lifecycle_status is deliberately excluded here -- status changes only
    # happen through the dedicated archive() route, gated on the more specific
    # customers.archive permission. Allowing it through this generic update
    # would let anyone with only customers.update silently reach the same
    # effect as customers.archive.
    fields = {k: v for k, v in request.form.items() if k in ("legal_name", "trade_name", "city", "country")}
    update_customer(customer, fields, actor.id)
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/<uuid:customer_id>/archive", methods=["POST"])
@require_permission("customers.archive")
def archive(customer_id):
    actor = load_current_staff()
    customer = db_session.get(Customer, customer_id)
    if customer is None or not _customer_visible_to(customer, actor):
        return jsonify({"error": "not_found"}), 404
    archive_customer(customer, actor.id)
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/<uuid:customer_id>/contacts", methods=["POST"])
@require_permission("customers.manage_contacts")
def add_contact_route(customer_id):
    actor = load_current_staff()
    customer = db_session.get(Customer, customer_id)
    if customer is None or not _customer_visible_to(customer, actor):
        return jsonify({"error": "not_found"}), 404
    add_contact(
        customer,
        {
            "name": request.form.get("name"),
            "title": request.form.get("title") or None,
            "business_email": request.form.get("business_email") or None,
            "business_phone": request.form.get("business_phone") or None,
            "is_primary": bool(request.form.get("is_primary")),
        },
        actor.id,
    )
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/<uuid:customer_id>/notes", methods=["POST"])
@require_permission("customers.manage_notes")
def add_note_route(customer_id):
    actor = load_current_staff()
    customer = db_session.get(Customer, customer_id)
    if customer is None or not _customer_visible_to(customer, actor):
        return jsonify({"error": "not_found"}), 404
    try:
        add_note(customer, request.form.get("body", ""), actor.id, visibility=request.form.get("visibility", "ASSIGNED_RECORD_USERS"))
    except CustomerCrmError as exc:
        return redirect(url_for("customers.detail", customer_id=customer_id, error=exc.code))
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/<uuid:customer_id>/assign", methods=["POST"])
@require_permission("customers.assign")
def assign_route(customer_id):
    import uuid as uuid_mod

    actor = load_current_staff()
    customer = db_session.get(Customer, customer_id)
    if customer is None:
        return jsonify({"error": "not_found"}), 404
    target = request.form.get("assigned_to_staff_user_id")
    if target:
        try:
            assign_customer(customer, uuid_mod.UUID(target), actor.id, reason=request.form.get("reason"), expected_version=request.form.get("version", type=int))
        except CustomerCrmError as exc:
            return redirect(url_for("customers.detail", customer_id=customer_id, error=exc.code))
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/<uuid:customer_id>/interactions", methods=["POST"])
@require_any_permission("customers.update_own", "customers.update_all")
def add_interaction_route(customer_id):
    actor = load_current_staff()
    codes = get_staff_permission_codes(actor)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not _customer_visible_to(customer, actor):
        return jsonify({"error": "not_found"}), 404
    profile = find_own_profile(actor.id)
    try:
        log_customer_interaction(customer_id, {"interaction_type": request.form.get("interaction_type"), "summary": request.form.get("summary")}, profile.id, actor.id)
    except LeadError as exc:
        return redirect(url_for("customers.detail", customer_id=customer_id, error=exc.code))
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/<uuid:customer_id>/followups", methods=["POST"])
@require_any_permission("customers.update_own", "customers.update_all")
def add_followup_route(customer_id):
    from datetime import datetime

    actor = load_current_staff()
    customer = db_session.get(Customer, customer_id)
    if customer is None or not _customer_visible_to(customer, actor):
        return jsonify({"error": "not_found"}), 404
    profile = find_own_profile(actor.id)
    due_raw = request.form.get("due_at")
    try:
        create_customer_followup(customer_id, {"due_at": datetime.fromisoformat(due_raw) if due_raw else None, "notes": request.form.get("notes")}, profile.id, actor.id)
    except LeadError as exc:
        return redirect(url_for("customers.detail", customer_id=customer_id, error=exc.code))
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/<uuid:customer_id>/locations", methods=["POST"])
@require_permission("customers.capture_location")
def add_location_route(customer_id):
    actor = load_current_staff()
    customer = db_session.get(Customer, customer_id)
    if customer is None or not _customer_visible_to(customer, actor):
        return jsonify({"error": "not_found"}), 404
    profile = find_own_profile(actor.id)
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
            lead_id=None, customer_id=customer_id,
            fields={k: cleaned.get(k) for k in ("latitude", "longitude", "accuracy_meters", "source", "manual_address")},
            actor_employee_profile_id=profile.id, actor_staff_user_id=actor.id,
        )
    except LocationValidationError as exc:
        return redirect(url_for("customers.detail", customer_id=customer_id, error=exc.code))
    return redirect(url_for("customers.detail", customer_id=customer_id))
