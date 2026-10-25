"""Staff side of customer website accounts: the 'Website account' panel on the
customer page and its actions.

Invisible unless OWNER_CUSTOMER_ACCOUNTS_ENABLED=true: every route here answers 404
(like a route that does not exist) and the panel is never rendered.

Who may do what (the owner's decisions, 2026-10-25):
  view / create+invite / resend / reset link / sign out everywhere
      customer_accounts.view / .manage  -- Sales (OWN customers only), Sales Manager,
      Support, Super Admin (Finance: view only)
  disable / enable / change email
      customer_accounts.disable / .change_email -- Support + Super Admin, and each
      needs a RECENT sign-in check.
SCOPE: anyone with customers.view_all or customer_accounts.disable sees every
customer; everyone else (Sales) only customers assigned to them -- the same shared
rule every customer route uses (customers.services.customer_visible_to_actor).

A link that was not mailed (email is off here, or queueing failed) is shown ONCE on
the page that follows the action, never stored in the clear: it is the staff member's
to hand over (e.g. by WhatsApp). Nothing here touches a licence or a device.
"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_babel import gettext as _

from app.auth.session import has_recent_auth, load_current_staff
from app.customer_accounts import mail, service
from app.customers.services import customer_visible_to_actor
from app.extensions import db_session
from app.models.customers import Customer
from app.security.rbac import get_staff_permission_codes, require_permission, require_recent_auth

bp = Blueprint("customer_accounts", __name__, url_prefix="/customers/<uuid:customer_id>/website-account")

GLOBAL_SCOPE_PERMISSIONS = ("customers.view_all", "customer_accounts.disable")


def accounts_enabled() -> bool:
    return bool(current_app.config.get("CUSTOMER_ACCOUNTS_ENABLED"))


def can_reach_customer(customer: Customer, actor, codes: set) -> bool:
    if any(code in codes for code in GLOBAL_SCOPE_PERMISSIONS):
        return True
    return customer_visible_to_actor(customer, actor.id, codes)


def panel_context(customer: Customer, actor, codes: set) -> dict | None:
    """What the customer page needs to draw the panel, or None (feature off, not
    allowed to see accounts, or this customer is not theirs)."""
    if not accounts_enabled() or "customer_accounts.view" not in codes or not can_reach_customer(customer, actor, codes):
        return None
    primary = next((c for c in customer.contacts if c.is_primary and c.business_email), None) \
        or next((c for c in customer.contacts if c.business_email), None)
    language = customer.primary_language if customer.primary_language in service.LANGUAGES else "en"
    return {
        "account": service.get_account_for_customer(customer.id),
        "can_manage": "customer_accounts.manage" in codes,
        "can_disable": "customer_accounts.disable" in codes,
        "can_change_email": "customer_accounts.change_email" in codes,
        "recent_auth_ok": has_recent_auth(),
        "default_email": primary.business_email if primary else "",
        "default_language": language,
    }


def prompt_for_order(order_id, actor, codes: set) -> dict | None:
    """After a sale is confirmed: the customer has no website account yet and this
    staff member may create one -> a reminder (never an automatic creation)."""
    if not accounts_enabled() or "customer_accounts.manage" not in codes or not order_id:
        return None
    from app.models.commercial_sales import SalesOrder

    order = db_session.get(SalesOrder, order_id)
    customer = db_session.get(Customer, order.customer_id) if order else None
    if customer is None or not can_reach_customer(customer, actor, codes):
        return None
    if service.customer_has_account(customer.id):
        return None
    return {"customer_id": customer.id, "customer_name": customer.legal_name}


def _context(customer_id):
    """(customer, actor, codes) or aborts 404 -- also when the customer is not the
    actor's: a seller cannot tell another seller's customer exists."""
    if not accounts_enabled():
        abort(404)
    actor = load_current_staff()
    codes = get_staff_permission_codes(actor)
    customer = db_session.get(Customer, customer_id)
    if customer is None or not can_reach_customer(customer, actor, codes):
        abort(404)
    return customer, actor, codes


def _account_or_404(customer):
    account = service.get_account_for_customer(customer.id)
    if account is None:
        abort(404)
    return account


def _message(code: str) -> str:
    # Literal strings so the message extractor sees every one.
    if code == "invalid_email":
        return _("That email address does not look right.")
    if code == "account_exists":
        return _("This customer already has a website account.")
    if code == "email_in_use":
        return _("That email address is already used by another customer's account.")
    if code == "reason_required":
        return _("Write the reason first.")
    if code == "not_invited":
        return _("This account has already been set up.")
    if code == "not_active":
        return _("Only an account that has been set up can be reset.")
    if code == "same_email":
        return _("That is already the account's email address.")
    if code == "account_disabled":
        return _("This account is disabled.")
    return _("Something went wrong. Nothing was changed.")


def _back(customer_id):
    return redirect(url_for("customers.detail", customer_id=customer_id) + "#website-account")


def _shown_once(issued: service.IssuedLink, what: str):
    response = render_template("customer_accounts/link.html", issued=issued, what=what, customer_id=issued.account.customer_id)
    return response, 200, {"Cache-Control": "no-store"}


@bp.route("/create", methods=["POST"])
@require_permission("customer_accounts.manage")
def create(customer_id):
    customer, actor, _codes = _context(customer_id)
    try:
        issued = service.create_account(customer, request.form.get("email", ""), request.form.get("language", ""), actor.id)
    except mail.PortalUrlNotSet:
        flash(_("The website address for account links is not set up yet (OWNER_CUSTOMER_PORTAL_BASE_URL)."), "error")
        return _back(customer_id)
    except service.AccountError as exc:
        flash(_message(exc.code), "error")
        return _back(customer_id)
    return _shown_once(issued, "INVITE")


@bp.route("/resend", methods=["POST"])
@require_permission("customer_accounts.manage")
def resend(customer_id):
    customer, actor, _codes = _context(customer_id)
    account = _account_or_404(customer)
    try:
        issued = service.resend_invitation(account, actor.id)
    except service.AccountError as exc:
        flash(_message(exc.code), "error")
        return _back(customer_id)
    return _shown_once(issued, "INVITE")


@bp.route("/reset-link", methods=["POST"])
@require_permission("customer_accounts.manage")
def reset_link(customer_id):
    customer, actor, _codes = _context(customer_id)
    account = _account_or_404(customer)
    try:
        issued = service.issue_reset_link(account, actor.id)
    except service.AccountError as exc:
        flash(_message(exc.code), "error")
        return _back(customer_id)
    return _shown_once(issued, "RESET")


@bp.route("/sign-out", methods=["POST"])
@require_permission("customer_accounts.manage")
def sign_out(customer_id):
    customer, actor, _codes = _context(customer_id)
    service.sign_out_everywhere(_account_or_404(customer), actor.id)
    flash(_("Signed out everywhere. Licences and devices are not affected."), "success")
    return _back(customer_id)


@bp.route("/disable", methods=["POST"])
@require_permission("customer_accounts.disable")
@require_recent_auth
def disable(customer_id):
    customer, actor, _codes = _context(customer_id)
    try:
        service.disable_account(_account_or_404(customer), request.form.get("reason", ""), actor.id)
    except service.AccountError as exc:
        flash(_message(exc.code), "error")
        return _back(customer_id)
    flash(_("Account disabled. The customer cannot sign in to the website. Licences and devices are not affected."), "success")
    return _back(customer_id)


@bp.route("/enable", methods=["POST"])
@require_permission("customer_accounts.disable")
@require_recent_auth
def enable(customer_id):
    customer, actor, _codes = _context(customer_id)
    service.enable_account(_account_or_404(customer), actor.id)
    flash(_("Account enabled."), "success")
    return _back(customer_id)


@bp.route("/change-email", methods=["POST"])
@require_permission("customer_accounts.change_email")
@require_recent_auth
def change_email(customer_id):
    customer, actor, _codes = _context(customer_id)
    account = _account_or_404(customer)
    try:
        issued = service.request_email_change(account, request.form.get("new_email", ""), request.form.get("reason", ""), actor.id)
    except mail.PortalUrlNotSet:
        flash(_("The website address for account links is not set up yet (OWNER_CUSTOMER_PORTAL_BASE_URL)."), "error")
        return _back(customer_id)
    except service.AccountError as exc:
        flash(_message(exc.code), "error")
        return _back(customer_id)
    return _shown_once(issued, "EMAIL_CHANGE")
