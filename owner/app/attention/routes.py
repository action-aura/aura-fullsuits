"""Attention Center web route. `@require_login` only -- there is no single
permission that gates "the Attention Center" as a screen, because it is a
personalized aggregation whose actual content is already independently
permission-filtered per category inside `service.get_attention_items()`.
An employee with none of `ATTENTION_CATEGORY_PERMISSIONS` still reaches
this page (same as the dashboard's own real empty-state precedent) and
sees a real, honest empty state rather than a 403 -- the page itself
exposes nothing; only the already-permission-checked items inside it do.
"""
from __future__ import annotations

from flask import Blueprint, render_template

from app.attention.service import get_attention_items
from app.auth.session import load_current_staff
from app.security.rbac import require_login

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
