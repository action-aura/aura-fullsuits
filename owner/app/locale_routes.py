"""Phase 9.5B-R Milestone 4 -- safe language switcher.

GET is used deliberately (same established convention as e.g.
staff.detail's plain navigation links) because switching the display
language changes no protected business state -- it is not "destructive" in
the sense the repository reserves POST for (employee lifecycle, session
revocation, role changes). Documented here per the governing spec's own
requirement to record this choice explicitly.
"""
from __future__ import annotations

from flask import Blueprint, abort, current_app, redirect, request, session, url_for

from app.auth.session import load_current_staff
from app.extensions import db_session
from app.i18n import LOCALE_COOKIE_MAX_AGE_SECONDS, LOCALE_COOKIE_NAME, LOCALE_SESSION_KEY, is_supported_locale

bp = Blueprint("locale", __name__, url_prefix="/locale")


def _safe_redirect_target() -> str:
    """Only ever redirect to a same-site relative path -- rejects absolute
    URLs and protocol-relative '//host' values (identical discipline to
    app.auth.routes._safe_next), preventing an open redirect via ?next=."""
    next_value = request.args.get("next")
    if next_value and next_value.startswith("/") and not next_value.startswith("//"):
        return next_value
    return url_for("dashboard.index")


@bp.route("/<code>", methods=["GET"])
def switch(code: str):
    if not is_supported_locale(code):
        abort(404)

    session[LOCALE_SESSION_KEY] = code

    # Persist to the account when authenticated (follows the user across
    # sessions/devices) -- never required, never blocks an anonymous switch.
    staff = load_current_staff()
    if staff is not None:
        staff.locale = code
        db_session.commit()

    response = redirect(_safe_redirect_target())
    response.set_cookie(
        LOCALE_COOKIE_NAME,
        code,
        max_age=LOCALE_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="Lax",
        secure=current_app.config["SESSION_COOKIE_SECURE"],
    )
    return response
