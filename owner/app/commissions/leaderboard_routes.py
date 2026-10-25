"""Step 1 (2026-10-25) -- the sales leaderboard screen (GET /leaderboard).

Who may open it: anyone who may see commissions at all -- a seller
(commissions.view_own) or a Sales Manager / Finance / Super Admin
(commissions.view_all). No new permission: the board is the commission
ladder seen side by side, and how much of it each viewer sees is decided by
the same pair (see app/commissions/leaderboard.py, WHO SEES WHAT).

?period=last shows last month; anything else shows this month (Jordan).
"""
from __future__ import annotations

from flask import Blueprint, render_template, request

from app.auth.session import load_current_staff
from app.commissions.leaderboard import board_months, leaderboard
from app.employees.queries import find_own_profile
from app.models.base import utcnow
from app.security.rbac import get_staff_permission_codes, require_any_permission

bp = Blueprint("leaderboard", __name__, url_prefix="/leaderboard")


@bp.route("", methods=["GET"])
@require_any_permission("commissions.view_own", "commissions.view_all")
def index():
    staff = load_current_staff()
    codes = get_staff_permission_codes(staff)
    profile = find_own_profile(staff.id)
    now = utcnow()
    this_month, last_month = board_months(now)
    period = "last" if request.args.get("period") == "last" else "this"
    month = last_month if period == "last" else this_month
    show_all_money = "commissions.view_all" in codes
    rows = leaderboard(month, viewer_profile_id=profile.id if profile is not None else None,
                       show_all_money=show_all_money, now=now)
    return render_template("commercial_sales/leaderboard.html", rows=rows, month=month, period=period,
                           show_all_money=show_all_money, has_own_row=any(r["is_viewer"] for r in rows))
