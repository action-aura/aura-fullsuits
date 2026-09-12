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

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_babel import gettext as _

from app.auth.session import load_current_staff
from app.customers.services import describe_duplicate_candidates_for_actor
from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.i18n_labels import localize_lead_error, localize_location_error
from app.leads.contacts import add_lead_contact, list_lead_contacts
from app.leads.conversion import DuplicateCustomerError, InvalidLeadStateError, convert
from app.leads.discovery import LeadDiscoveryError, MAX_RESULTS, build_provider, is_discovery_enabled
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


def _localized_discovery_error(code: str) -> tuple[str, int]:
    """Route-boundary localization for app.leads.discovery.LeadDiscoveryError,
    built fresh on every call rather than as a module-level dict -- same
    discipline as app.i18n_labels' own localize_*_error functions: a
    module-level dict would call gettext() once at import time, before any
    request/locale context exists, permanently freezing the message in
    whatever locale (or none) happened to be active at import time.
    Returns (message, http_status) since each discovery failure mode maps
    to a distinct status the caller renders with."""
    messages = {
        "INVALID_QUERY": (_("Check the segment and area and try again."), 400),
        "RATE_LIMITED": (_("The map service is busy -- try again in a minute."), 502),
        "PROVIDER_ERROR": (_("The map service could not be reached."), 502),
    }
    return messages.get(code, (_("The map service could not be reached."), 502))


def _actor():
    staff = load_current_staff()
    profile = find_own_profile(staff.id)
    return staff, profile


_NO_PROFILE_MESSAGE = "This action requires a real employee profile; administrative accounts without one cannot perform it."


def _missing_profile(profile) -> bool:
    """Real bug found via Milestone 23 browser validation: every CRM
    write path assumed `profile` (the actor's EmployeeProfile) is never
    None, but a StaffUser is not guaranteed to have one -- a Super Admin
    bootstrap account, in particular, legitimately has no EmployeeProfile
    (it is not a working staff member). Calling profile.id on None
    crashed with a 500 instead of a clear, actionable error. Every write
    handler below now checks this first. Real employees created through
    the normal onboarding flow always have an EmployeeProfile, so this
    only ever triggers for an admin-only account attempting a CRM action
    it was never meant to perform."""
    return profile is None


def _lead_or_none(lead_id, profile, *, all_held: bool):
    """Fails CLOSED, not open: an actor who lacks view_all/update_all AND
    has no EmployeeProfile to check ownership against gets denied, never
    silently granted access because the ownership check had nothing to
    compare against. (Real bug found via Milestone 23 browser validation
    -- the original `and profile is not None` guard skipped the ownership
    check entirely instead of treating "no profile" as "can't prove
    ownership, so deny.")"""
    lead = db_session.get(Lead, lead_id)
    if lead is None:
        return None
    if not all_held:
        if profile is None:
            return None
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
    if not is_management and _missing_profile(profile):
        # No EmployeeProfile and no view_all -- nothing this actor could
        # own; an empty employee-shaped summary is the correct, honest
        # answer (not a 500).
        summary = {"own_active_leads": 0, "new_leads": 0, "potential_leads": 0, "qualified_leads": 0,
                   "converted_leads": 0, "lost_leads": 0, "followups_due_today": 0, "followups_overdue": 0,
                   "recent_interactions": 0}
    else:
        summary = management_crm_dashboard() if is_management else employee_crm_dashboard(profile.id)
    return render_template("leads/dashboard.html", summary=summary, is_management=is_management)


@bp.route("", methods=["GET"])
@require_any_permission("leads.view_own", "leads.view_all")
def list_leads():
    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    page = request.args.get("page", 1, type=int)
    status_filter = request.args.get("status") or None
    search = request.args.get("q") or None
    sort = request.args.get("sort", "created_at")
    direction = request.args.get("dir", "desc")
    # UI modernization Stage D -- status/search filtering now happens
    # inside list_all_leads/list_own_leads' own WHERE clause, before
    # pagination (fixes the real, disclosed bug where the status filter
    # used to be applied in Python only over the current page's rows,
    # while result.total/total_pages still reflected the unfiltered set --
    # see leads/services.py's _apply_lead_list_filters docstring).
    if "leads.view_all" in codes:
        result = list_all_leads(page=page, status=status_filter, search=search, sort=sort, direction=direction)
    elif _missing_profile(profile):
        result = {"rows": [], "page": 1, "page_size": 25, "total": 0, "total_pages": 1, "has_prev": False, "has_next": False}
    else:
        result = list_own_leads(profile.id, page=page, status=status_filter, search=search, sort=sort, direction=direction)
    return render_template(
        "leads/list.html", result=result, status_filter=status_filter, search_value=search,
        sort=sort, direction=direction, discovery_enabled=is_discovery_enabled(current_app.config),
    )


@bp.route("/new", methods=["GET"])
@require_permission("leads.create")
def new_form():
    return render_template("leads/new.html", error=None)


@bp.route("", methods=["POST"])
@require_permission("leads.create")
def create():
    staff, profile = _actor()
    if _missing_profile(profile):
        return render_template("leads/new.html", error=_NO_PROFILE_MESSAGE), 400
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


# --------------------------------------------------- Milestone -- lead discovery --
#
# Google Places-backed prospecting: a sales rep types a segment ("pharmacies")
# and an area ("Irbid, Jordan"), gets back real businesses from
# app.leads.discovery's provider, ticks the ones with a usable phone number,
# and imports them as leads. Gated on its own "leads.discover" permission
# (not folded into leads.create) so it can be revoked independently of the
# ability to hand-enter a lead -- this hits an external paid API per search,
# a materially different cost/abuse profile than a manual form post.

_DISCOVERY_FORM_DEFAULTS = {"segment": "", "area": "", "priority": "MEDIUM", "assigned_employee_profile_id": ""}


@bp.route("/discover", methods=["GET"])
@require_permission("leads.discover")
def discover_form():
    return render_template(
        "leads/discover.html", enabled=is_discovery_enabled(current_app.config),
        results=None, error=None, **_DISCOVERY_FORM_DEFAULTS,
    )


@bp.route("/discover/search", methods=["POST"])
@require_permission("leads.discover")
def discover_search():
    enabled = is_discovery_enabled(current_app.config)
    if not enabled:
        # Race/misconfiguration guard: the form that posted here was itself
        # rendered from discover_form(), so this should be unreachable in
        # practice, but never trust that the config didn't change between
        # GET and POST -- fail into the same disabled notice, not a 500.
        return render_template(
            "leads/discover.html", enabled=False, results=None, error=None, **_DISCOVERY_FORM_DEFAULTS,
        )

    staff, profile = _actor()
    if _missing_profile(profile):
        return render_template(
            "leads/discover.html", enabled=True, results=None, error=_NO_PROFILE_MESSAGE, **_DISCOVERY_FORM_DEFAULTS,
        ), 400

    segment = (request.form.get("segment") or "").strip()
    area = (request.form.get("area") or "").strip()
    priority = request.form.get("priority") or "MEDIUM"
    assigned_employee_profile_id = (request.form.get("assigned_employee_profile_id") or "").strip()
    echo = {"segment": segment, "area": area, "priority": priority, "assigned_employee_profile_id": assigned_employee_profile_id}

    if not segment or not area or len(segment) > 120 or len(area) > 120:
        return render_template(
            "leads/discover.html", enabled=True, results=None,
            error=_("Enter both a segment and an area, each 120 characters or fewer."), **echo,
        ), 400

    try:
        provider = build_provider(current_app.config)
        results = provider.search(segment, area, limit=MAX_RESULTS)
    except LeadDiscoveryError as exc:
        if exc.code == "NOT_CONFIGURED":
            return render_template("leads/discover.html", enabled=False, results=None, error=None, **echo)
        message, status = _localized_discovery_error(exc.code)
        return render_template("leads/discover.html", enabled=True, results=None, error=message, **echo), status

    return render_template("leads/discover.html", enabled=True, results=results, error=None, **echo)


@bp.route("/discover/import", methods=["POST"])
@require_permission("leads.discover")
def discover_import():
    staff, profile = _actor()
    if _missing_profile(profile):
        return render_template(
            "leads/discover.html", enabled=is_discovery_enabled(current_app.config), results=None,
            error=_NO_PROFILE_MESSAGE, **_DISCOVERY_FORM_DEFAULTS,
        ), 400

    priority = request.form.get("priority") or "MEDIUM"
    if priority not in ("LOW", "MEDIUM", "HIGH"):
        priority = "MEDIUM"
    assigned_raw = (request.form.get("assigned_employee_profile_id") or "").strip()
    assigned_id = None
    if assigned_raw:
        try:
            assigned_id = uuid.UUID(assigned_raw)
        except ValueError:
            return render_template(
                "leads/discover.html", enabled=is_discovery_enabled(current_app.config), results=None,
                error=_("That employee profile ID is not a valid UUID."),
                segment="", area="", priority=priority, assigned_employee_profile_id=assigned_raw,
            ), 400

    # Clamped to what a search can ever have rendered. `count` comes from a
    # hidden input, so a crafted POST could otherwise set it to ten million and
    # spin this loop that many times on an authenticated route -- cheap per
    # iteration, but there is no reason to let it run past MAX_RESULTS.
    count = max(0, min(request.form.get("count", 0, type=int) or 0, MAX_RESULTS))
    imported = 0
    skipped_no_phone = 0
    row_errors = []
    for n in range(count):
        if not request.form.get(f"select-{n}"):
            continue
        phone = (request.form.get(f"phone-{n}") or "").strip()
        if not phone:
            # The checkbox for a no-phone row is rendered disabled, but a
            # crafted/replayed POST could still set select-<n> -- re-check
            # server-side rather than trusting the client, same discipline
            # as every other write path in this file.
            skipped_no_phone += 1
            continue
        place_id = (request.form.get(f"place_id-{n}") or "").strip()
        name = (request.form.get(f"name-{n}") or "").strip()
        address = (request.form.get(f"address-{n}") or "").strip()
        if not place_id or not name:
            # Never let an empty place_id reach the idempotency key. Every
            # such row would share the single key "maps-discovery:", so the
            # first would create a lead and every later one would silently be
            # deduplicated INTO it -- N places collapsing to one lead with no
            # error. A real search always renders both; only a crafted or
            # truncated POST lacks them, and that is refused per row.
            row_errors.append(_("Row %(row)d is missing its place ID or name and was not imported.", row=n + 1))
            continue
        fields = {
            "organization_or_prospect_name": name[:200],
            "phone": phone[:32],
            "source": "MAPS_DISCOVERY",
            "priority": priority,
            "location_summary": address[:200] or None,
        }
        if assigned_id is not None:
            fields["assigned_employee_profile_id"] = assigned_id
        try:
            # idempotency_key is THE dedup mechanism -- create_lead() looks
            # up this exact key first and returns the existing lead instead
            # of inserting a new row, so re-importing the same Google Place
            # (same place_id) twice, whether by accident or by re-running a
            # search, never creates a second lead for it.
            create_lead(fields, profile.id, staff.id, idempotency_key=f"maps-discovery:{place_id}")
            imported += 1
        except LeadError as exc:
            row_errors.append(_localized_lead_error(exc))

    # create_lead() commits internally on every path (see leads/services.py) --
    # no extra db_session.commit() needed here, same as the create() route above.
    flash(_("%(count)d lead(s) imported or already present.", count=imported), "info")
    if skipped_no_phone:
        flash(_("%(count)d row(s) skipped -- no phone listed.", count=skipped_no_phone), "info")
    if row_errors:
        flash(_("Some rows could not be imported: %(errors)s", errors="; ".join(row_errors)), "error")
    return redirect(url_for("leads.list_leads"))


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
    if _missing_profile(profile):
        return redirect(url_for("leads.detail", lead_id=lead_id, error="EMPLOYEE_PROFILE_REQUIRED"))
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
    if _missing_profile(profile):
        return redirect(url_for("leads.detail", lead_id=lead_id, error="EMPLOYEE_PROFILE_REQUIRED"))
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
    if _missing_profile(profile):
        return redirect(url_for("leads.detail", lead_id=lead_id, error="EMPLOYEE_PROFILE_REQUIRED"))
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
    if _missing_profile(profile):
        return redirect(url_for("leads.detail", lead_id=lead_id, error="EMPLOYEE_PROFILE_REQUIRED"))
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
    if _missing_profile(profile):
        return redirect(url_for("leads.detail", lead_id=lead_id, error="EMPLOYEE_PROFILE_REQUIRED"))
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
    if _missing_profile(profile):
        return redirect(url_for("leads.detail", lead_id=lead_id, error="EMPLOYEE_PROFILE_REQUIRED"))
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
    if _missing_profile(profile):
        return redirect(url_for("leads.detail", lead_id=lead_id, error="EMPLOYEE_PROFILE_REQUIRED"))
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
    if _missing_profile(profile):
        return redirect(url_for("leads.detail", lead_id=lead_id, error="EMPLOYEE_PROFILE_REQUIRED"))
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

def _resolve_followup_with_parent_check(followup_id, staff, profile, codes):
    """Loads a follow-up by UUID and verifies the actor may access its
    PARENT Lead/Customer before returning it -- mirrors
    api_operations/crm.py's identical guard. A follow-up UUID alone must
    never be sufficient to act on it (real IDOR closed here: this web
    route previously had no ownership check at all)."""
    from app.customers.services import customer_visible_to_actor
    from app.models.customers import Customer
    from app.models.leads import CustomerFollowup, LeadFollowup

    row = db_session.get(LeadFollowup, followup_id)
    if row is not None:
        lead = _lead_or_none(row.lead_id, profile, all_held="leads.update_all" in codes)
        return row if lead is not None else None
    row = db_session.get(CustomerFollowup, followup_id)
    if row is not None:
        customer = db_session.get(Customer, row.customer_id)
        if customer is not None and customer_visible_to_actor(customer, staff.id, codes):
            return row
    return None


@shared_bp.route("/followups/<uuid:followup_id>/complete", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def complete_followup(followup_id):
    from app.models.leads import LeadFollowup

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    row = _resolve_followup_with_parent_check(followup_id, staff, profile, codes)
    if row is None:
        return jsonify({"error": "not_found"}), 404
    if isinstance(row, LeadFollowup):
        complete_lead_followup(row, staff.id)
        return redirect(url_for("leads.detail", lead_id=row.lead_id))
    complete_customer_followup(row, staff.id)
    return redirect(url_for("customers.detail", customer_id=row.customer_id))


@shared_bp.route("/followups/<uuid:followup_id>/cancel", methods=["POST"])
@require_any_permission("leads.update_own", "leads.update_all")
def cancel_followup(followup_id):
    from app.models.leads import LeadFollowup

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    row = _resolve_followup_with_parent_check(followup_id, staff, profile, codes)
    if row is None:
        return jsonify({"error": "not_found"}), 404
    reason = request.form.get("reason")
    try:
        if isinstance(row, LeadFollowup):
            cancel_lead_followup(row, reason, staff.id)
            return redirect(url_for("leads.detail", lead_id=row.lead_id))
        cancel_customer_followup(row, reason, staff.id)
        return redirect(url_for("customers.detail", customer_id=row.customer_id))
    except LeadError:
        if isinstance(row, LeadFollowup):
            return redirect(url_for("leads.detail", lead_id=row.lead_id))
        return redirect(url_for("customers.detail", customer_id=row.customer_id))


@shared_bp.route("/locations/<uuid:location_id>/verify", methods=["POST"])
@require_permission("customers.verify_location")
def verify_location_route(location_id):
    from app.models.leads import CustomerLocation

    staff, profile = _actor()
    codes = get_staff_permission_codes(staff)
    location = db_session.get(CustomerLocation, location_id)
    # IDOR fix: verify the actor may access the location's PARENT
    # Lead/Customer before allowing verification (customers.verify_location
    # alone previously let any holder verify ANY location by UUID,
    # regardless of ownership of the record it belongs to).
    if location is not None:
        if location.lead_id:
            parent_ok = _lead_or_none(location.lead_id, profile, all_held="leads.update_all" in codes) is not None
        else:
            from app.customers.services import customer_visible_to_actor
            from app.models.customers import Customer

            customer = db_session.get(Customer, location.customer_id)
            parent_ok = customer is not None and customer_visible_to_actor(customer, staff.id, codes)
        if not parent_ok:
            location = None
    if location is None:
        return jsonify({"error": "not_found"}), 404
    if _missing_profile(profile):
        if location.lead_id:
            return redirect(url_for("leads.detail", lead_id=location.lead_id, error="EMPLOYEE_PROFILE_REQUIRED"))
        return redirect(url_for("customers.detail", customer_id=location.customer_id, error="EMPLOYEE_PROFILE_REQUIRED"))
    try:
        verify_location(location, request.form.get("reason"), profile.id, staff.id)
    except LocationValidationError:
        pass
    if location.lead_id:
        return redirect(url_for("leads.detail", lead_id=location.lead_id))
    return redirect(url_for("customers.detail", customer_id=location.customer_id))
