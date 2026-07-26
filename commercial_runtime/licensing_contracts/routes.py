"""Product-side licensing HTTP surface (Parts E/G/H/I/J/K/L wiring).

Mirrors commercial_runtime/backup/routes.py's make_*_blueprint(...) factory
pattern -- each product's app.py calls this once, exactly like it already
does for make_backup_blueprint(). This is the seam between the generic
licensing domain (this package) and each concrete product; it never imports
anything product-specific (no Retail/Clinic import here), matching the same
boundary backup/routes.py already keeps.

Registers no capability guards on any OTHER route -- that is Parts P/Q/R/T's
job, layered on top of this once the Clinic/Retail capability matrices are
wired. This module's own routes (status/activate/check-in/deactivate) are
intentionally always reachable regardless of license state, since they are
the interface used to FIX a bad license state (Part P: "no hidden bypass"
cuts both ways -- the licensing routes themselves are never capability-
gated by license state, or a customer could get permanently locked out of
the one screen that lets them reactivate).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from flask import Blueprint, current_app, jsonify, request

from .activation import ActivationFailed, ActivationPending, ingest_activation_response, perform_activation
from .checkin_scheduler import LicenseCheckInScheduler
from .client import LicensingClient, LicensingClientConfig
from .deactivation import DeactivationFailed, ingest_deactivation_response, perform_deactivation
from .events import LicensingEventRecorder
from .state_repository import LicenseStateRepository
from .status_presenter import present_status
from .trust_anchor_loader import TrustAnchorLoadError, load_bundled_trust_anchor
from .trust_store import OwnerTrustStore

DeviceIdentityFactory = Callable[[Path], object]  # -> DeviceIdentityProvider


def make_licensing_blueprint(
    *,
    product_code: str,
    platform: str,
    app_version: str,
    app_data_dir: str,
    owner_base_url: str,
    verify_tls: bool,
    timeout_seconds: float,
    trust_anchor_path: Path,
    device_identity_factory: DeviceIdentityFactory,
    release_channel: Optional[str] = "rc",
    internal_shared_secret: Optional[str] = None,
) -> Blueprint:
    """internal_shared_secret: only set on Android. Enables the
    /_internal/sync-* routes (Part U) that the Kotlin layer -- which holds
    the AndroidKeystore-wrapped device key and makes the actual signed
    Owner HTTP calls itself -- uses to hand this embedded backend a raw,
    still-untrusted Owner response for INDEPENDENT re-verification and
    persistence. Left None on Windows, where this process makes its own
    Owner calls directly via /activate, /check-in, /deactivate above and the
    /_internal/* routes are not registered at all.

    The secret is generated fresh by the Kotlin layer at process startup
    and threaded into Chaquopy's environment before Flask boots (the same
    pattern already used for AURA_APP_DATA/SECRET_KEY) -- never hardcoded,
    never persisted, never valid across a process restart. Binding to
    localhost only is already guaranteed (this embedded server never binds
    anywhere but 127.0.0.1), so the shared secret's job is narrower: proving
    a request came from THIS app's own Kotlin process, not some other app
    on the same device probing the loopback port.
    """
    bp = Blueprint("licensing", __name__, url_prefix="/api/licensing")

    licensing_dir = Path(app_data_dir) / "licensing"
    db_path = Path(app_data_dir) / "database" / "subsystems" / "licensing.db"

    def _not_configured_response():
        return jsonify({"current_state": "NOT_CONFIGURED", "detail": "Owner licensing URL is not configured."}), 200

    def _build_context():
        """Constructs the per-request object graph. Cheap (sqlite/file
        handles only, no network I/O until a route actually needs it) -- not
        cached at blueprint-creation time so a corrected configuration or a
        newly-generated device key takes effect on the very next request
        without an app restart."""
        state_repository = LicenseStateRepository(db_path)
        event_recorder = LicensingEventRecorder(db_path)
        trust_store = OwnerTrustStore(licensing_dir / "trust_store.json")
        signer = device_identity_factory(licensing_dir)

        if not (licensing_dir / "trust_store.json").exists():
            try:
                anchor = load_bundled_trust_anchor(trust_anchor_path)
                trust_store.bootstrap_from_anchor(anchor)
            except TrustAnchorLoadError:
                pass  # status route below reports this clearly; do not crash the request

        client = LicensingClient(
            LicensingClientConfig(base_url=owner_base_url, timeout_seconds=timeout_seconds, verify_tls=verify_tls)
        )
        return state_repository, event_recorder, trust_store, signer, client

    @bp.route("/status", methods=["GET"])
    def status():
        if not owner_base_url:
            return _not_configured_response()
        state_repository, *_ = _build_context()
        return jsonify(present_status(state_repository.load())), 200

    @bp.route("/activate", methods=["POST"])
    def activate():
        if not owner_base_url:
            return jsonify({"reason_code": "SERVICE_TEMPORARILY_UNAVAILABLE", "detail": "Owner licensing URL is not configured."}), 503

        body = request.get_json(silent=True) or {}
        license_key = body.get("license_key")
        if not license_key or not isinstance(license_key, str):
            return jsonify({"reason_code": "INVALID_REQUEST", "detail": "license_key is required."}), 400

        state_repository, event_recorder, trust_store, signer, client = _build_context()

        if not signer.has_key():
            signer.generate_new_key()

        try:
            result = perform_activation(
                client=client,
                signer=signer,
                trust_store=trust_store,
                state_repository=state_repository,
                event_recorder=event_recorder,
                product_code=product_code,
                platform=platform,
                app_version=app_version,
                release_channel=release_channel,
                license_key=license_key,
                device_public_key_fingerprint=_device_fingerprint(signer),
            )
        except ActivationPending as exc:
            return jsonify({"result": "PENDING", "reason_code": exc.reason_code, "installation_id": exc.installation_id, "detail": str(exc)}), 202
        except ActivationFailed as exc:
            return jsonify({"reason_code": exc.reason_code, "detail": str(exc)}), 400
        finally:
            # Discard the local references to the full license key as soon
            # as this request is done with them (Part G) -- defense in
            # depth on top of perform_activation()'s own clearing.
            license_key = None
            if "license_key" in body:
                body["license_key"] = None

        return jsonify({"result": "SUCCESS", "state": result.state.value, "installation_id": result.owner_installation_id}), 200

    @bp.route("/check-in", methods=["POST"])
    def check_in():
        if not owner_base_url:
            return _not_configured_response()
        state_repository, event_recorder, trust_store, signer, client = _build_context()
        if not signer.has_key() or state_repository.load() is None:
            return jsonify({"current_state": "ACTIVATION_REQUIRED"}), 200

        scheduler = LicenseCheckInScheduler(
            client=client,
            signer=signer,
            trust_store=trust_store,
            state_repository=state_repository,
            event_recorder=event_recorder,
            product_code=product_code,
            platform=platform,
            device_public_key_fingerprint=_device_fingerprint(signer),
        )
        scheduler.run_once()
        # present_status() only reflects the currently-persisted snapshot --
        # it cannot by itself distinguish "check-in reached Owner and
        # succeeded" from "Owner was unreachable, state unchanged" (both can
        # legitimately leave current_state at ACTIVE_ONLINE, e.g. a fresh
        # check-in attempt within the grace window after a network blip).
        # The UI needs that distinction to show an honest "could not reach
        # the licensing service" message instead of a false "check-in
        # complete" -- read it off the event this exact call just recorded,
        # rather than guessing from state alone.
        # CHECK_IN_SUCCEEDED is followed immediately by a second event
        # (ASSERTION_ACCEPTED) in the same call -- looking at only the very
        # last event would see ASSERTION_ACCEPTED and miss it. Look at the
        # last few events from this call instead of just the newest one.
        recent_event_types = {e.event_type for e in event_recorder.recent(limit=3)}
        response_body = present_status(state_repository.load())
        response_body["last_attempt_reached_owner"] = "CHECK_IN_SUCCEEDED" in recent_event_types
        return jsonify(response_body), 200

    @bp.route("/deactivate", methods=["POST"])
    def deactivate():
        # Deliberately NOT gated on owner_base_url being configured, and
        # never capability-guarded by license state (see module docstring):
        # this route, status, and activate together form the one interface
        # a customer always has to fix or reset their licensing state.
        state_repository, event_recorder, trust_store, signer, client = _build_context()
        try:
            new_state = perform_deactivation(
                client=client, signer=signer, state_repository=state_repository, event_recorder=event_recorder
            )
        except DeactivationFailed as exc:
            return jsonify({"reason_code": exc.reason_code, "detail": str(exc)}), 400
        return jsonify({"result": "SUCCESS", "state": new_state.value}), 200

    if internal_shared_secret:
        _register_internal_sync_routes(bp, internal_shared_secret, _build_context, product_code, platform)

    return bp


def _register_internal_sync_routes(
    bp: Blueprint, shared_secret: str, build_context, product_code: str, platform: str
) -> None:
    def _authorized() -> bool:
        provided = request.headers.get("X-Aura-Internal-Secret", "")
        # Constant-time comparison -- this is a local, single-device secret,
        # but there is no reason to accept a timing side channel just
        # because the attack surface is small.
        import hmac

        return hmac.compare_digest(provided, shared_secret)

    @bp.route("/_internal/sync-activation", methods=["POST"])
    def internal_sync_activation():
        if not _authorized():
            return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Unauthorized."}), 403
        owner_response = request.get_json(silent=True)
        if not isinstance(owner_response, dict):
            return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Expected a JSON object."}), 400

        state_repository, event_recorder, trust_store, signer, _client = build_context()
        if not signer.has_key():
            return jsonify({"reason_code": "DEVICE_KEY_UNAVAILABLE", "detail": "No local device key registered."}), 400

        try:
            result = ingest_activation_response(
                owner_response,
                trust_store=trust_store,
                state_repository=state_repository,
                event_recorder=event_recorder,
                product_code=product_code,
                platform=platform,
                device_public_key_fingerprint=_device_fingerprint(signer),
            )
        except ActivationPending as exc:
            return jsonify({"result": "PENDING", "reason_code": exc.reason_code, "installation_id": exc.installation_id, "detail": str(exc)}), 202
        except ActivationFailed as exc:
            return jsonify({"reason_code": exc.reason_code, "detail": str(exc)}), 400
        return jsonify({"result": "SUCCESS", "state": result.state.value, "installation_id": result.owner_installation_id}), 200

    @bp.route("/_internal/sync-checkin", methods=["POST"])
    def internal_sync_checkin():
        if not _authorized():
            return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Unauthorized."}), 403
        owner_response = request.get_json(silent=True)
        if not isinstance(owner_response, dict):
            return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Expected a JSON object."}), 400

        state_repository, event_recorder, trust_store, signer, _client = build_context()
        scheduler = LicenseCheckInScheduler(
            client=None,  # never used -- ingest_checkin_response() makes no Owner call itself
            signer=signer,
            trust_store=trust_store,
            state_repository=state_repository,
            event_recorder=event_recorder,
            product_code=product_code,
            platform=platform,
            device_public_key_fingerprint=_device_fingerprint(signer),
        )
        new_state = scheduler.ingest_checkin_response(owner_response)
        return jsonify(present_status(state_repository.load())), 200

    @bp.route("/_internal/reevaluate", methods=["POST"])
    def internal_reevaluate():
        """Android path for the offline-policy re-check the Windows
        run_once() gets for free on every failed check-in (see
        checkin_scheduler.LicenseCheckInScheduler.run_once()'s except
        branch). Android's OwnerClient makes its own HTTP call and never
        goes through run_once(), so a failed check-in attempt would
        otherwise never call evaluate() at all and current_state would
        stay stuck at whatever was last persisted, no matter how much
        trusted time has actually elapsed (Phase 7V-A gate I). Makes no
        Owner network call itself -- purely re-runs evaluate() against the
        already-stored assertion and elapsed trusted time, exactly like
        reevaluate_only() does for Windows."""
        if not _authorized():
            return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Unauthorized."}), 403

        state_repository, event_recorder, trust_store, signer, _client = build_context()
        if not signer.has_key() or state_repository.load() is None:
            return jsonify({"current_state": "ACTIVATION_REQUIRED"}), 200

        scheduler = LicenseCheckInScheduler(
            client=None,  # never used -- reevaluate_only() makes no Owner call
            signer=signer,
            trust_store=trust_store,
            state_repository=state_repository,
            event_recorder=event_recorder,
            product_code=product_code,
            platform=platform,
            device_public_key_fingerprint=_device_fingerprint(signer),
        )
        scheduler.reevaluate_only(checkin_ok=False)
        return jsonify(present_status(state_repository.load())), 200

    @bp.route("/_internal/sync-deactivation", methods=["POST"])
    def internal_sync_deactivation():
        if not _authorized():
            return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Unauthorized."}), 403
        owner_response = request.get_json(silent=True)
        if not isinstance(owner_response, dict):
            return jsonify({"reason_code": "INVALID_REQUEST", "detail": "Expected a JSON object."}), 400

        state_repository, event_recorder, *_rest = build_context()
        try:
            new_state = ingest_deactivation_response(
                owner_response, state_repository=state_repository, event_recorder=event_recorder
            )
        except DeactivationFailed as exc:
            return jsonify({"reason_code": exc.reason_code, "detail": str(exc)}), 400
        return jsonify({"result": "SUCCESS", "state": new_state.value}), 200


def _device_fingerprint(signer) -> str:
    return signer.get_metadata().public_key_fingerprint
