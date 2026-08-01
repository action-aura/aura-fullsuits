"""Customer organization routes (Part K)."""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from sqlalchemy import select

from app.auth.session import load_current_staff
from app.customers.services import add_contact, add_note, archive_customer, create_customer, find_duplicate_candidates, update_customer
from app.employees.queries import find_own_profile
from app.extensions import db_session
from app.leads.ownership import apply_ownership_filter
from app.models.customers import Customer
from app.security.rbac import get_staff_permission_codes, require_permission

bp = Blueprint("customers", __name__, url_prefix="/customers")


def _customer_visible_to(customer: Customer, actor) -> bool:
    """Record-level ownership check reused by every single-Customer route
    below -- avoids the exact per-route-reimplementation IDOR risk
    apply_ownership_filter's own docstring warns about. A customer with no
    assignee (should not normally occur post-Milestone-3's create_customer
    default, but real for pre-existing/legacy rows) is visible only to a
    customers.view_all holder, never by accident to everyone."""
    codes = get_staff_permission_codes(actor)
    if "customers.view_all" in codes:
        return True
    return customer.assigned_sales_staff_id is not None and customer.assigned_sales_staff_id == actor.id


@bp.route("", methods=["GET"])
@require_permission("customers.view")
def list_customers():
    actor = load_current_staff()
    status_filter = request.args.get("status")
    stmt = select(Customer).order_by(Customer.created_at.desc())
    if status_filter:
        stmt = stmt.where(Customer.lifecycle_status == status_filter)
    # Phase 9.5C Milestone 3 -- customers.view only gates entry to this
    # route; the actual visibility SCOPE is customers.view_own (assigned
    # only) vs customers.view_all (everyone), enforced server-side via the
    # same shared apply_ownership_filter() every Lead query uses (never a
    # second, independently-written filter -- see
    # docs/owner/phase9_5c/crm-record-ownership-contract.md).
    codes = get_staff_permission_codes(actor)
    all_held = "customers.view_all" in codes
    if not all_held:
        profile = find_own_profile(actor.id)
        stmt = apply_ownership_filter(stmt, Customer, profile.id if profile else None, all_permission_held=False)
    customers = db_session.execute(stmt).scalars().all()
    return render_template("customers/list.html", customers=customers, status_filter=status_filter)


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
    customer = db_session.get(Customer, customer_id)
    if customer is None or not _customer_visible_to(customer, actor):
        return jsonify({"error": "not_found"}), 404
    return render_template("customers/detail.html", customer=customer)


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
    add_note(customer, request.form.get("body", ""), actor.id)
    return redirect(url_for("customers.detail", customer_id=customer_id))
