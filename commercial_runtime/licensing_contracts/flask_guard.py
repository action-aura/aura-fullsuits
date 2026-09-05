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
import logging
from functools import wraps
from pathlib import Path
from typing import Callable, Optional

from flask import jsonify

from .capability_guard import evaluate_capability
from .events import LicensingEventRecorder
from .state_machine import LicenseState
from .state_repository import LicenseStateRecord, LicenseStateRepository

logger = logging.getLogger(__name__)


def _resolve_current_state(record: Optional[LicenseStateRecord], db_path: Path) -> LicenseState:
    """Turn whatever `licensing_state.current_state` actually holds into a
    LicenseState, without ever raising (AUDIT: LicenseState(record.
    current_state) used to be called unguarded here, so a single stored
    value outside the enum's fixed vocabulary -- a downgrade writing back a
    state a newer build introduced, a restored backup, a partial/corrupted
    write -- took the whole route down with an unhandled ValueError -> HTTP
    500, no JSON body, on EVERY guarded route, since every mutation route in
    both products goes through this one decorator).

    A MISSING record is a fresh install, not a damaged one -- NOT_CONFIGURED,
    same as before. A record that exists but whose current_state is None,
    empty, or not a recognized LicenseState member is a genuinely damaged
    row, and LOCAL_STATE_CORRUPT already exists in the enum for precisely
    this: it's in DATA_PRESERVED_FAMILY (state_machine.py), so
    capability_guard.py still allows read/backup/restore/export -- which an
    owner needs MORE than usual right now, since restoring is how they
    recover -- while denying mutations, instead of crashing.
    """
    if record is None:
        return LicenseState.NOT_CONFIGURED

    raw_state = record.current_state
    try:
        # None and "" both fail this lookup exactly like a bogus string
        # does (LicenseState has no member whose value is None or ""), so
        # one except branch below correctly covers all three cases without
        # needing to special-case None/"" ahead of the enum lookup.
        return LicenseState(raw_state)
    except ValueError:
        logger.warning(
            "licensing: stored current_state %r in %s is not a recognized "
            "LicenseState (missing, empty, or unrecognized value -- a "
            "downgrade, restored backup, or partial write can all produce "
            "this). Treating as LOCAL_STATE_CORRUPT: mutations will be "
            "denied but read/backup/restore/export stay available.",
            raw_state,
            db_path,
        )
        # raw_state is UNTRUSTED data read off disk -- unlike capability_code
        # below (always a hardcoded literal from our own fixed vocabulary),
        # a corrupted or tampered licensing_state row could contain
        # anything, including a forbidden-marker substring (see
        # events.FORBIDDEN_DETAIL_MARKERS / _guard_details's trusted_keys
        # contract). So it is never passed into the recorded event's
        # details -- only the fact that corruption was detected, tagged
        # with a hardcoded literal, is trusted enough to record.
        LicensingEventRecorder(db_path).record(
            "LOCAL_STATE_CORRUPT",
            {"detected_by": "flask_guard._resolve_current_state"},
            trusted_keys=frozenset({"detected_by"}),
        )
        return LicenseState.LOCAL_STATE_CORRUPT


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
                current_state = _resolve_current_state(record, db_path)
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


def make_entitlement_reader(app_data_dir: str) -> Callable[[], dict]:
    """Returns a zero-argument reader of THIS product's current licence
    entitlements -- the `entitlements` dict the signed assertion carried,
    as stored by activation/check-in -- for routes that gate on a VALUE
    (an integer limit) rather than on a capability. `make_capability_guard`
    already json-loads the same column per request; this is the same read
    without the decision, so a limit-shaped rule lives next to the route
    that owns the count it compares against. `{}` when there is no record,
    no entitlements, or the column cannot be parsed: an unreadable licence
    state must never raise inside a business route -- the capability guard
    stacked above the route is what refuses a broken state, and it already
    ran."""
    db_path = Path(app_data_dir) / "database" / "subsystems" / "licensing.db"

    def read_entitlements() -> dict:
        try:
            record = LicenseStateRepository(db_path).load()
            if record is None or not record.entitlements_json:
                return {}
            value = json.loads(record.entitlements_json)
            return value if isinstance(value, dict) else {}
        except Exception:  # noqa: BLE001 -- see docstring: never raise here
            return {}

    return read_entitlements
