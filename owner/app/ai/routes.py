"""POST /assistant/ask -- the staff Help button's backend, plus the one JSON
error mapping every browser-facing AI endpoint shares.

Staff-only (`require_login`, no extra permission: any signed-in employee may
ask how to use the tool they work in). CSRF is the app-wide Flask-WTF check,
carried in the X-CSRFToken header by static/js/ai-help.js (same pattern as
static/js/push.js) -- no per-route exemption.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_babel import gettext as _

from app.ai import staff_help
from app.ai.providers import AIError
from app.auth.session import load_current_staff
from app.security.rbac import require_login

bp = Blueprint("assistant", __name__, url_prefix="/assistant")


def ai_error_response(exc: AIError):
    """(JSON response, status) for an AIError, with a message safe to show a
    staff member. Built per call, not at import, so it follows the request's
    locale. Never includes exc.detail: that is for logs and diagnosis."""
    if exc.code == "AI_BUDGET_EXHAUSTED":
        message, status = _("The AI usage limit has been reached. It resets at midnight, Jordan time."), 429
    elif exc.code in ("AI_NOT_CONFIGURED", "AI_PRIVACY_REFUSED"):
        message, status = _("AI is not available on this server."), 503
    else:
        message, status = _("The AI service is busy or unavailable. Try again in a minute."), 502
    return jsonify({"error": exc.code, "message": message}), status


@bp.route("/ask", methods=["POST"])
@require_login
def ask():
    staff = load_current_staff()
    payload = request.get_json(silent=True)
    try:
        reply = staff_help.ask(payload, staff.id, current_app.config)
    except staff_help.HelpRequestError:
        return jsonify({"error": "INVALID_REQUEST", "message": _("Check the question and try again.")}), 400
    except AIError as exc:
        return ai_error_response(exc)
    return jsonify({"reply": reply})
