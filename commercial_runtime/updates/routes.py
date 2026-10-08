"""HTTP surface of the update channel: /api/updates/*.

  GET  /status    any signed-in user (so the "Update ready" pill can show)
  POST /check     administrator -- same guard as backup/restore
  POST /download  administrator
  POST /install   administrator

Why administrator-only for the three actions: installing closes the app on
every screen for a minute and replaces the program. That is the same weight as
a restore, so it reuses the restore route's guard rather than inventing a
weaker one.

Why this blueprint is NOT behind the retail licence guard: that guard is a
per-route decorator (require_license_capability), and a new blueprint carries
none. An expired or restricted shop must still be able to install the build
that may fix it -- Owner itself refuses discovery to a licence it will not
serve, so the gate that matters lives there.

When no Owner URL is configured the service is None: /status says so, the
POSTs answer 409 UPDATES_DISABLED, nothing is scheduled and no client is ever
built.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from flask import Blueprint, jsonify, session

from commercial_runtime.backup.routes import _require_admin
from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig
from commercial_runtime.licensing_contracts.state_repository import LicenseStateRepository
from commercial_runtime.licensing_contracts.trust_store import OwnerTrustStore

from .descriptor import UpdateError
from .service import UpdateService

log = logging.getLogger("aura.updates")


def build_update_service(
    *,
    app_data_dir: str,
    owner_base_url: str,
    verify_tls: bool,
    timeout_seconds: float,
    device_identity_factory: Callable[[Path], object],
    release_channel: Optional[str],
    app_version: str,
    **service_overrides,
) -> Optional[UpdateService]:
    """Returns None when there is no Owner URL (updates are off).

    Uses the SAME licensing directory, licence database, trust store and device
    identity the licensing blueprint builds -- never a second key or a second
    trust set -- so a discovery request verifies as the installation Owner
    already knows from activation.
    """
    if not owner_base_url:
        return None
    licensing_dir = Path(app_data_dir) / "licensing"
    db_path = Path(app_data_dir) / "database" / "subsystems" / "licensing.db"

    def client_factory():
        # One retry, not the licence client's four: this runs behind a button
        # a person is watching, and the daily poll will try again tomorrow.
        return LicensingClient(
            LicensingClientConfig(
                base_url=owner_base_url, timeout_seconds=timeout_seconds, verify_tls=verify_tls, max_retries=1
            )
        )

    service = UpdateService(
        app_data_dir=app_data_dir,
        client_factory=client_factory,
        trust_store_factory=lambda: OwnerTrustStore(licensing_dir / "trust_store.json"),
        signer_factory=lambda: device_identity_factory(licensing_dir),
        state_repository_factory=lambda: LicenseStateRepository(db_path),
        app_version=app_version,
        release_channel=release_channel,
        **service_overrides,
    )
    return service


def _disabled_status(current_version: str, reason: str) -> dict:
    return {
        "enabled": False,
        "reason_disabled": reason,
        "current_version": current_version,
        "checked_at": None,
        "available": None,
        "phase": "idle",
        "progress": None,
        "error_code": None,
        "can_install": False,
    }


def make_updates_blueprint(service: Optional[UpdateService], *, current_version: str) -> Blueprint:
    bp = Blueprint("updates_api", __name__, url_prefix="/api/updates")

    def _status_body() -> dict:
        if service is None:
            return _disabled_status(current_version, "OWNER_NOT_CONFIGURED")
        reason = service.disabled_reason()
        if reason:
            return _disabled_status(current_version, reason)
        return {"enabled": True, "reason_disabled": None, **service.status()}

    def _refuse_if_disabled():
        """The 409 every action shares. Checked AFTER authentication, so a
        cashier is told 403 (not 'disabled') and learns nothing about config."""
        body = _status_body()
        if body["enabled"]:
            return None
        log.info("updates: action refused -- updates are disabled (%s)", body["reason_disabled"])
        return jsonify({"error_code": "UPDATES_DISABLED", "reason_disabled": body["reason_disabled"]}), 409

    @bp.route('/status', methods=['GET'])
    def status():
        if 'mt_user_id' not in session:
            return jsonify({'error': 'Authentication required', 'code': 401}), 401
        return jsonify(_status_body()), 200

    @bp.route('/check', methods=['POST'])
    def check():
        denied = _require_admin() or _refuse_if_disabled()
        if denied:
            return denied
        body_error = None
        try:
            service.check()
        except UpdateError as exc:
            body_error = exc.code
        body = _status_body()
        if body_error:
            body["error_code"] = body_error
        return jsonify(body), 200

    @bp.route('/download', methods=['POST'])
    def download():
        denied = _require_admin() or _refuse_if_disabled()
        if denied:
            return denied
        try:
            service.download()
        except UpdateError as exc:
            return jsonify({"error_code": exc.code}), 409
        return jsonify(_status_body()), 202

    @bp.route('/install', methods=['POST'])
    def install():
        denied = _require_admin() or _refuse_if_disabled()
        if denied:
            return denied
        try:
            service.install()
        except UpdateError as exc:
            return jsonify({"error_code": exc.code}), 409
        return jsonify({"result": "INSTALLING"}), 202

    return bp
