"""The seller-agreement gate (clauses 17 and 20 of the seller agreement).

A seller who has not accepted the CURRENT live SELLER contract cannot use any selling
screen. Until they accept they are sent to /contracts/seller, where they can read the
text (and the PDF), tick the box, and download their copy.

WHY ONE HOOK AND NOT A DECORATOR ON EACH ROUTE. The selling surface is dozens of
routes in a dozen blueprints (the one-screen sale, new customer, payments, leads,
quotes, commissions, statements, the JSON API behind the mobile app...). A decorator
would have to be remembered on every one and on every future one; a route somebody
forgets would silently sell without an agreement. This hook FAILS CLOSED instead: for a
bound seller with an unaccepted live agreement EVERYTHING in the staff app is refused
except a short exempt list (sign-in/out, language, profile + password, the agreement
page itself and its downloads, static files, health). The exempt list is the thing to
review; test_seller_agreement_gate walks the real URL map and fails if a route outside
it ever stops being gated.

The hook reads the session WITHOUT touching it (auth.session.peek_current_staff) so it
never changes idle-timeout behaviour. Requests without a staff session are not its
business (device APIs, the website BFF, public pages): they carry no staff cookie.
"""
from __future__ import annotations

from flask import jsonify, redirect, request, url_for

from app.auth.session import peek_current_staff
from app.contracts import service

# Blueprints that are not part of the staff app, or that a bound seller must still
# reach to sign in, change a password, switch language, install the PWA or be
# monitored. None of them sells anything.
EXEMPT_BLUEPRINTS = frozenset({
    "auth", "locale", "health", "pwa", "profile", "push",
    # device / website / server-to-server APIs: authenticated by signatures or bearer
    # secrets, never by a staff cookie
    "api_external_licensing", "website_account_api", "website_intake", "sync", "external_api",
})
# The agreement page, the acceptance POST and the two downloads.
EXEMPT_ENDPOINTS = frozenset({
    "static", "contracts.seller_page", "contracts.seller_accept", "contracts.seller_pdf", "contracts.seller_copy",
})


def is_exempt(endpoint: str | None, blueprint: str | None) -> bool:
    return endpoint is None or endpoint in EXEMPT_ENDPOINTS or blueprint in EXEMPT_BLUEPRINTS


def _wants_json() -> bool:
    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"


def enforce_seller_agreement():
    if is_exempt(request.endpoint, request.blueprint):
        return None
    staff = peek_current_staff()
    if staff is None or service.seller_agreement_still_owed(staff) is None:
        return None
    if _wants_json():
        return jsonify({"error": "seller_agreement_required", "agreement_url": url_for("contracts.seller_page")}), 403
    # 303 so a refused POST is not replayed against the agreement page.
    return redirect(url_for("contracts.seller_page", required="1"), code=303)


def register(app) -> None:
    app.before_request(enforce_seller_agreement)
