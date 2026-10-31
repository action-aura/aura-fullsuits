from __future__ import annotations

from flask import Blueprint, render_template

from app.auth.session import load_current_staff
from app.dashboard.services import get_dashboard_summary
from app.security.rbac import get_staff_permission_codes, require_login

bp = Blueprint("dashboard", __name__, url_prefix="/")


@bp.route("", methods=["GET"])
@require_login
def index():
    actor = load_current_staff()
    summary = get_dashboard_summary(actor, get_staff_permission_codes(actor))
    return render_template("dashboard/index.html", summary=summary)
