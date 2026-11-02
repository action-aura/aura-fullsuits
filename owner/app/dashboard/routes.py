from __future__ import annotations

from flask import Blueprint, render_template

from app.auth.session import load_current_staff
from app.dashboard.services import get_dashboard_summary
from app.privacy_requests import service as data_request_service
from app.security.rbac import get_staff_permission_codes, require_login
from app.seller_discipline import sanctions
from app.seller_discipline.people import seller_page_context

bp = Blueprint("dashboard", __name__, url_prefix="/")


@bp.route("", methods=["GET"])
@require_login
def index():
    actor = load_current_staff()
    codes = get_staff_permission_codes(actor)
    summary = get_dashboard_summary(actor, codes)
    # Clauses 16-17: a seller sees their own notices / running notice; a holder of
    # sellers.discipline sees the open-case counts. Everyone else sees nothing new.
    open_cases, decidable_cases = sanctions.open_counts() if "sellers.discipline" in codes else (0, 0)
    # Personal-data requests: a manager sees how many are open and how many are past their 30 days.
    data_requests_open, data_requests_late = data_request_service.open_counts() if "privacy.requests" in codes else (0, 0)
    return render_template("dashboard/index.html", summary=summary, seller=seller_page_context(actor),
                           discipline_open=open_cases, discipline_decidable=decidable_cases,
                           data_requests_open=data_requests_open, data_requests_late=data_requests_late)
