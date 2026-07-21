from __future__ import annotations

from flask import Blueprint, render_template

from app.dashboard.services import get_dashboard_summary
from app.security.rbac import require_login

bp = Blueprint("dashboard", __name__, url_prefix="/")


@bp.route("", methods=["GET"])
@require_login
def index():
    summary = get_dashboard_summary()
    return render_template("dashboard/index.html", summary=summary)
