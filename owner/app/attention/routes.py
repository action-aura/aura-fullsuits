"""Attention Center web route. `@require_login` only -- there is no single
permission that gates "the Attention Center" as a screen, because it is a
personalized aggregation whose actual content is already independently
permission-filtered per category inside `service.get_attention_items()`.
An employee with none of `ATTENTION_CATEGORY_PERMISSIONS` still reaches
this page (same as the dashboard's own real empty-state precedent) and
sees a real, honest empty state rather than a 403 -- the page itself
exposes nothing; only the already-permission-checked items inside it do.

website_leads_count does its own auth (not @require_login) so it can validate
the session WITHOUT refreshing its idle timer -- see that function.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template

from app.attention.service import WEBSITE_LEAD_PERMISSIONS, count_new_website_leads, get_attention_items
from app.auth.session import load_current_staff
from app.security.rbac import get_staff_permission_codes, require_login

bp = Blueprint("attention", __name__, url_prefix="/attention")


@bp.route("", methods=["GET"])
@require_login
def index():
    staff = load_current_staff()
    items = get_attention_items(staff)
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(item.category_label, []).append(item)
    return render_template("attention/index.html", items=items, grouped=grouped)


@bp.route("/website-leads/count", methods=["GET"])
def website_leads_count():
    """Polled every 30 s by static/js/website-leads-badge.js.

    Uses load_current_staff(touch=False): the session is validated exactly
    as usual (expiry, idle timeout, revocation, session_version) but its
    last_seen_at is NOT advanced, so a tab left open still times out after
    OWNER_SESSION_IDLE_SECONDS exactly as it would without the poll. Gated on
    the leads.view_own / leads.view_all pair, like the leads list; the count
    is ownership-scoped the same way (service.count_new_website_leads)."""
    staff = load_current_staff(touch=False)
    if staff is None:
        status, payload = 401, {"error": "authentication_required"}
    else:
        codes = get_staff_permission_codes(staff)
        if codes & WEBSITE_LEAD_PERMISSIONS:
            status, payload = 200, {"count": count_new_website_leads(staff, codes)}
        else:
            status, payload = 403, {"error": "forbidden"}
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response
