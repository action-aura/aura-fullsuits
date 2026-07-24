"""require_license_capability (Part T) -- the reusable Flask route decorator
factory every product mutation route is guarded with. This is the ONLY
enforcement mechanism; product route modules never re-implement a license
check inline (Part T: "cannot be bypassed by calling the API directly" --
achieved by making this decorator the single choke point every mutation
route must pass through, not a UI-layer hint).

Deliberately re-reads state on every request rather than caching -- a
CAPABILITY_DENIED decision must reflect the current licensing state, not a
stale snapshot from process start (state can change between requests: a
background check-in scheduler tick, a staff-initiated reactivation, etc).
The cost is one extra sqlite read per guarded request, which is cheap
(local, single-row table, WAL mode).
"""
from __future__ import annotations

import json
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

from flask import jsonify

from .capability_guard import evaluate_capability
from .events import LicensingEventRecorder
from .state_machine import LicenseState
from .state_repository import LicenseStateRepository


def make_capability_guard(app_data_dir: str) -> Callable:
    """Called once per product, at route-module import time (mirrors
    make_licensing_blueprint()/make_backup_blueprint()'s "call once, get a
    configured thing back" pattern) -- returns the actual
    require_license_capability decorator factory, closed over this
    product's own licensing.db path."""
    db_path = Path(app_data_dir) / "database" / "subsystems" / "licensing.db"

    def require_license_capability(
        capability_code: str,
        *,
        restricted_mode_allowlist: frozenset,
        required_entitlement: Optional[str] = None,
    ):
        def decorator(view_func):
            @wraps(view_func)
            def wrapper(*args, **kwargs):
                state_repository = LicenseStateRepository(db_path)
                record = state_repository.load()
                current_state = LicenseState(record.current_state) if record else LicenseState.NOT_CONFIGURED
                entitlements = (
                    json.loads(record.entitlements_json) if record and record.entitlements_json else {}
                )

                decision = evaluate_capability(
                    capability_code=capability_code,
                    current_state=current_state,
                    restricted_mode_allowlist=restricted_mode_allowlist,
                    required_entitlement=required_entitlement,
                    entitlements=entitlements,
                )

                if not decision.allowed:
                    event_recorder = LicensingEventRecorder(db_path)
                    # trusted_keys=capability_code: this value is always a
                    # hardcoded literal supplied at the @require_license_
                    # capability(...) call site in our own route module
                    # (see clinic-restriction-capability-matrix.md /
                    # retail-restriction-capability-matrix.md for the fixed
                    # vocabulary) -- never derived from the request body or
                    # any patient/customer data, so it's exempted from the
                    # forbidden-marker scan that would otherwise reject
                    # legitimate codes like "clinic.patient.create".
                    event_recorder.record(
                        "CAPABILITY_DENIED",
                        {"capability_code": capability_code},
                        trusted_keys=frozenset({"capability_code"}),
                    )
                    return (
                        jsonify(
                            {
                                "status": "error",
                                "reason_code": decision.denial_reason,
                                "message": "This action is not available in the current licensing state.",
                            }
                        ),
                        403,
                    )

                return view_func(*args, **kwargs)

            return wrapper

        return decorator

    return require_license_capability
