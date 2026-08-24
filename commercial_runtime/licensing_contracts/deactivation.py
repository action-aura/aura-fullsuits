"""Controlled device deactivation (Part X). Requires an explicit caller-
confirmed action -- this module never runs on its own initiative. Mirrors
activation.py's shape.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from .client import LicensingClientError
from .events import LicensingEventRecorder
from .state_machine import LicenseState
from .state_repository import LicenseStateRecord, LicenseStateRepository

logger = logging.getLogger(__name__)


class DeactivationFailed(Exception):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def _resolve_stored_state(record: Optional[LicenseStateRecord], event_recorder: LicensingEventRecorder) -> LicenseState:
    """Turn whatever `licensing_state.current_state` actually holds into a
    LicenseState, without ever raising. Mirrors flask_guard.py's
    _resolve_current_state() / checkin_scheduler.py's identically-named
    twin -- same reasoning, same shape (AUDIT: this module had the same
    unguarded `LicenseState(record.current_state)` construction at both of
    its no-op early-return branches).

    A MISSING record, or one whose owner_installation_id is unset, is still
    a legitimate "nothing to deactivate" no-op, not an error -- but the
    record CAN exist with a genuinely corrupt current_state (a downgrade, a
    restored backup, a partial write) while still carrying a real
    owner_installation_id, and the pre-fix code called LicenseState(...) on
    that value unconditionally on the way out, crashing an explicit,
    user-initiated deactivation request instead of just no-op'ing or
    reporting LOCAL_STATE_CORRUPT.

    A MISSING record is a fresh install, not a damaged one -- NOT_CONFIGURED,
    exactly as before this fix.

    The raw stored value is logged (logs are not subject to the
    forbidden-marker scan) but never placed in the recorded event's details:
    it is untrusted data read off disk, and event_recorder.record() itself
    RAISES LicensingEventError when details contain a forbidden-marker
    substring (license_key, patient, sale_total, ...) -- echoing the raw
    value in would risk that exact re-raise from inside the handler written
    to prevent one.
    """
    if record is None:
        return LicenseState.NOT_CONFIGURED

    raw_state = record.current_state
    try:
        return LicenseState(raw_state)
    except ValueError:
        logger.warning(
            "deactivation: stored current_state %r is not a recognized "
            "LicenseState (missing, empty, or unrecognized value -- a "
            "downgrade, restored backup, or partial write can all produce "
            "this). Treating as LOCAL_STATE_CORRUPT instead of raising.",
            raw_state,
        )
        event_recorder.record(
            "LOCAL_STATE_CORRUPT",
            {"detected_by": "deactivation._resolve_stored_state"},
            trusted_keys=frozenset({"detected_by"}),
        )
        return LicenseState.LOCAL_STATE_CORRUPT


def perform_deactivation(
    *,
    client,
    signer,
    state_repository: LicenseStateRepository,
    event_recorder: LicensingEventRecorder,
) -> LicenseState:
    """Idempotent by design (Owner's /deactivations endpoint is itself
    idempotent, Phase 6 Part L) -- calling this twice in a row, including
    after a prior network failure left the outcome ambiguous, is safe: a
    repeat call reuses a fresh idempotency key each time but Owner's own
    installation-status bookkeeping means a second deactivation of an
    already-deactivated installation still returns SUCCESS, not a spurious
    error.

    The device private key is deliberately NOT destroyed here (Part X:
    "retained or destroyed according to the documented deactivation
    policy" -- this policy is retain). Destruction only happens through the
    explicit device-replacement flow's destroy_key() call, after Owner has
    confirmed a *new* device is registered -- never as a side effect of
    plain deactivation, so an accidental deactivation can be undone by
    reactivating the same device without losing its identity.
    """
    record = state_repository.load()
    if record is None or not record.owner_installation_id:
        # Nothing to deactivate -- not an error, just a no-op. Resolved
        # lazily (only on this branch, exactly like the original bare
        # LicenseState(record.current_state) expression it replaces) so a
        # deactivation that actually proceeds below never fires a spurious
        # LOCAL_STATE_CORRUPT event/log for a record it isn't even using
        # current_state from.
        return _resolve_stored_state(record, event_recorder)

    idempotency_key = str(uuid.uuid4())
    try:
        response = client.deactivate(
            installation_id=record.owner_installation_id, idempotency_key=idempotency_key, signer=signer
        )
    except LicensingClientError as exc:
        # Ambiguous outcome (Part I: "no unsafe repeated license-key
        # transmission after ambiguous server acceptance without
        # idempotency" -- deactivation has no license key to worry about,
        # but the same ambiguity-safety applies: local state is left
        # unchanged, a retry with the same underlying installation_id is
        # always safe because Owner's own endpoint is idempotent).
        raise DeactivationFailed(exc.reason_code, str(exc)) from exc

    return ingest_deactivation_response(response, state_repository=state_repository, event_recorder=event_recorder)


def ingest_deactivation_response(
    response: dict,
    *,
    state_repository: LicenseStateRepository,
    event_recorder: LicensingEventRecorder,
) -> LicenseState:
    """Android path (Part U): Kotlin already made the signed deactivation
    call (it holds the device key); hands the raw response here. No
    assertion to independently verify for this particular call (a
    deactivation response carries no signed_assertion, per the protocol),
    but state is still only ever mutated from this one function -- Kotlin's
    own belief that deactivation succeeded is not, by itself, sufficient."""
    record = state_repository.load()
    if record is None or not record.owner_installation_id:
        return _resolve_stored_state(record, event_recorder)

    if response.get("result") != "SUCCESS":
        reason_code = response.get("reason_code", "ACTIVATION_REJECTED")
        raise DeactivationFailed(reason_code, "Owner rejected the deactivation request.")

    record.current_state = LicenseState.DEVICE_DEACTIVATED.value
    state_repository.save(record)
    event_recorder.record("DEVICE_DEACTIVATED")

    return LicenseState.DEVICE_DEACTIVATED
