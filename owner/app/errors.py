"""Styled error pages for security-relevant HTTP failures (Part X / UI
modernization). Real, existing behavior only rendered as a bare Werkzeug
default page before this: 403 (access denied), CSRF/session failures.
Never reveals request internals, stack traces, or which specific check
failed beyond the generic category -- matches the same anti-enumeration
discipline already applied to login (auth/routes.py)."""
from __future__ import annotations

from flask import Flask, redirect, render_template, request, url_for
from flask_babel import gettext as _
from flask_wtf.csrf import CSRFError


def register_error_handlers(app: Flask) -> None:
    def _wants_json() -> bool:
        return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"

    @app.errorhandler(403)
    def _forbidden(error):
        if _wants_json():
            return {"error": "forbidden"}, 403
        return render_template(
            "errors/security.html",
            title=_("Access denied"),
            heading=_("Access denied"),
            message=_("You don't have permission to view this page. If you believe this is wrong, contact your administrator."),
        ), 403

    @app.errorhandler(CSRFError)
    def _csrf_error(error):
        if _wants_json():
            return {"error": "invalid_csrf"}, 400
        # A CSRF failure on the bare login POST (page left open long enough,
        # or the same login page open in more than one tab, that the token
        # went stale) is always safe to recover from silently: nothing has
        # been authenticated yet, so there is no in-progress session state a
        # transparent retry could corrupt or lose -- unlike every other
        # route this handler also covers (MFA verify/enroll, reauth,
        # invitation accept, password change), which carry state or a
        # single-use token a blind redirect could discard, so those still
        # fall through to the generic dead-end page below. CSRF protection
        # itself is unchanged either way -- a fresh token is still required
        # and still validated; this only changes what happens on failure.
        if request.endpoint == "auth.login_submit":
            return redirect(url_for("auth.login_form", reason="session_expired"))
        return render_template(
            "errors/security.html",
            title=_("Session check failed"),
            heading=_("Session check failed"),
            message=_("This page took too long to submit, or your session changed in another tab. Go back and try again."),
        ), 400

    @app.errorhandler(401)
    def _unauthorized(error):
        if _wants_json():
            return {"error": "authentication_required"}, 401
        return render_template(
            "errors/security.html",
            title=_("Sign-in required"),
            heading=_("Sign-in required"),
            message=_("You need to sign in to view this page."),
        ), 401
