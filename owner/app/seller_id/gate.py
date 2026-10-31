"""The seller ID-photo gate: step two after the seller-agreement gate (contracts/gate.py).

A seller (the SALES role, the same people the agreement binds) who has no CURRENT photo
of their own ID on file cannot use any page except the upload page, the agreement pages,
sign-out / language / profile and static files. They are sent to /seller-id.

ORDER. This hook is registered AFTER the agreement hook, and Flask stops at the first
hook that answers, so the seller meets the agreement first and the ID photo second.
Nothing here re-checks the agreement.

SAME SWITCH. The agreement gate is off while no SELLER contract is live (and for a seller
whose language has none). This gate asks the same question the same way, so when that
gate is off this one is off too: a company that has not switched the seller rules on yet
is not locked out by half of them.

FAIL CLOSED. If the check itself raises, the seller is blocked (sent to the upload
page), never waved through.

THE ONE DELIBERATE OPENING. If this server has no usable OWNER_KYC_KEYS, a photo cannot be
stored, so demanding one would lock every seller out forever. In that case the ID step is
skipped and a warning is logged (once per process while the key is missing). The customer
form makes the opposite choice for ITS case (refuse, create nothing) because there the
form is the only thing blocked; here the whole staff app would be.

Like the agreement gate it reads the session WITHOUT touching it (peek_current_staff).
"""
from __future__ import annotations

import logging

from flask import current_app, jsonify, redirect, request, url_for

from app.auth.session import peek_current_staff
from app.contracts import gate as agreement_gate
from app.contracts import service as contract_service
from app.extensions import db_session
from app.seller_id import documents

log = logging.getLogger(__name__)

# What a seller without a photo can still reach, on top of everything the agreement gate
# already exempts (sign-in/out, language, profile, the agreement pages, static, health).
ID_STEP_EXEMPT_ENDPOINTS = frozenset({"seller_id.upload_page", "seller_id.upload"})
_WARNED_KEY = "seller_id_kyc_missing_warned"


def is_exempt(endpoint: str | None, blueprint: str | None) -> bool:
    return agreement_gate.is_exempt(endpoint, blueprint) or endpoint in ID_STEP_EXEMPT_ENDPOINTS


def id_photo_still_owed(staff) -> bool:
    """True when this person must upload a photo of their ID before they may work."""
    if not contract_service.staff_is_bound_by_seller_agreement(staff):
        return False
    if contract_service.live_version_for("SELLER", getattr(staff, "locale", None)) is None:
        return False  # the seller-agreement switch is off, so this one is too
    if not documents.storage_is_ready():
        if not current_app.extensions.get(_WARNED_KEY):
            current_app.extensions[_WARNED_KEY] = True
            log.warning(
                "seller ID-photo gate skipped: OWNER_KYC_KEYS is missing or malformed, so photos cannot be "
                "stored; sellers are NOT being asked for one until it is set"
            )
        return False
    current_app.extensions[_WARNED_KEY] = False
    return not documents.has_current_photo(staff.id)


def enforce_seller_id_photo():
    if is_exempt(request.endpoint, request.blueprint):
        return None
    staff = peek_current_staff()
    if staff is None:
        return None
    try:
        owed = id_photo_still_owed(staff)
    except Exception:  # noqa: BLE001 -- fail closed: a check that cannot run must not wave anyone through
        log.exception("seller ID-photo gate could not decide; blocking")
        db_session.rollback()
        owed = True
    if not owed:
        return None
    if agreement_gate._wants_json():
        return jsonify({"error": "seller_id_photo_required", "upload_url": url_for("seller_id.upload_page")}), 403
    # 303 so a refused POST is not replayed against the upload page.
    return redirect(url_for("seller_id.upload_page", required="1"), code=303)


def register(app) -> None:
    app.before_request(enforce_seller_id_photo)
