"""Turn phone notifications on / off for this device.

  POST /push/subscribe     {endpoint, keys: {p256dh, auth}}  -- the browser's
                           PushSubscription.toJSON(), sent by static/js/push.js
  POST /push/unsubscribe   {endpoint}

Both are JSON from the page's own script, CSRF-protected by the X-CSRFToken
header (Flask-WTF checks it like a form token), behind login. 404 while push
is off (no VAPID key) -- exactly like a route that does not exist. Only a
person who may confirm sales (recipients.staff_may_confirm_sales) can turn it
on: nobody else is ever sent anything, so a row for them would be noise.

The subscription is always bound to the SIGNED-IN person -- never to a staff
id from the request -- and the endpoint (a capability URL) is never echoed,
logged or written to the audit log; the audit row names the device row's id.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_babel import get_locale

from app.audit.services import record as audit_record
from app.auth.session import load_current_staff
from app.push import subscriptions as push_subscriptions
from app.push.recipients import staff_may_confirm_sales
from app.push.vapid import push_enabled
from app.push.webpush import InvalidSubscription
from app.security.rbac import require_login

bp = Blueprint("push", __name__, url_prefix="/push")


def _answer(payload: dict, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/subscribe", methods=["POST"])
@require_login
def subscribe():
    if not push_enabled():
        return _answer({"error": "not_found"}, 404)
    staff = load_current_staff()
    if not staff_may_confirm_sales(staff):
        return _answer({"error": "forbidden"}, 403)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _answer({"error": "invalid_subscription", "code": "NOT_JSON"}, 400)
    keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
    try:
        row, is_new = push_subscriptions.save_subscription(
            staff_user_id=staff.id,
            endpoint=data.get("endpoint"),
            p256dh=keys.get("p256dh") or "",
            auth=keys.get("auth") or "",
            locale=str(get_locale() or "") or None,
            user_agent=request.user_agent.string,
        )
    except InvalidSubscription as exc:
        return _answer({"error": "invalid_subscription", "code": str(exc)}, 400)
    if is_new:
        audit_record(
            actor_staff_user_id=staff.id, actor_role_snapshot=None, action_code="PUSH_DEVICE_SUBSCRIBED",
            entity_type="push_subscription", entity_public_id=str(row.id),
            after_state={"devices": push_subscriptions.count_devices(staff.id)},
        )
    return _answer({"ok": True})


@bp.route("/unsubscribe", methods=["POST"])
@require_login
def unsubscribe():
    if not push_enabled():
        return _answer({"error": "not_found"}, 404)
    staff = load_current_staff()
    data = request.get_json(silent=True)
    endpoint = data.get("endpoint") if isinstance(data, dict) else None
    if not isinstance(endpoint, str) or not endpoint:
        return _answer({"error": "invalid_subscription", "code": "ENDPOINT_MISSING_OR_TOO_LONG"}, 400)
    removed = push_subscriptions.remove_subscription(staff_user_id=staff.id, endpoint=endpoint)
    if removed:
        audit_record(
            actor_staff_user_id=staff.id, actor_role_snapshot=None, action_code="PUSH_DEVICE_UNSUBSCRIBED",
            entity_type="push_subscription", entity_public_id=None,
            after_state={"devices": push_subscriptions.count_devices(staff.id)},
        )
    return _answer({"ok": True, "removed": removed})
