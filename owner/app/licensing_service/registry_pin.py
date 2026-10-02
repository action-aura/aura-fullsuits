"""Owner pins each installation's shop-authority registry device id.

AUDIT-033 S-AUTH fix round (verifier V finding #1, CRITICAL).

THE BUG THIS EXISTS FOR: receivers used to bind "this signing computer is
registry device X" by trust-on-first-use. Any computer of the shop could
therefore sign an event naming the MAIN's device id before the main did, bind
that id to itself, and from then on be treated as the main. The device id is
chosen by the computer and nothing outside the shop vouched for it.

THE FIX: Owner is the one party every receiver already trusts (it signs every
lease). Owner records the id a computer claims at activation or check-in,
refuses to ever change it, and signs it into every later lease as
`registry_device_id`. A receiver accepts a signer's id only if that signer's
Owner-signed lease says so (commercial_runtime/sync/authority.py).

Rules, in this order (the plan's registry_pin.py rules 1-5):
 1. No id sent (absent, None, "") -> nothing happens and the key is NEVER put
    in the assertion. Old clients reject unknown assertion fields
    (assertion_verifier.py's allowlist; the Phase 8 Part W outage), so an old
    client must keep getting exactly the lease it always got.
 2. Not a canonical UUID string (<= 64 chars, str(uuid.UUID(x)) == x) ->
    REGISTRY_DEVICE_ID_INVALID, nothing pinned.
 3. The installation already holds a pin -> the pin is immutable. Same id again
    is a no-op; a different id records REGISTRY_DEVICE_ID_CHANGE_REFUSED and
    the lease keeps carrying the ORIGINAL pin.
 4. Another installation of the SAME licence holds this id and can still speak
    (not DEACTIVATED/REPLACED) -> REGISTRY_DEVICE_ID_CLASH. Nothing is pinned
    and the lease omits the key. The activation / check-in itself still
    SUCCEEDS: Owner never bricks licensing over this. Staff see the event and
    deactivate the squatter; the real computer then pins on its next check-in.
 5. Otherwise pin it inside a savepoint. A concurrent pin that wins the unique
    index (uq_owner_installations_license_registry_device) is treated as rule 4.

None of these outcomes raises: a pin problem must never turn into a failed
activation or check-in.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.installations import ActivationEvent, Installation

# Keep identical to the partial unique index predicate in
# models/installations.py and migration 5f2c8e1a9b37.
TERMINAL_STATUSES = ("DEACTIVATED", "REPLACED")

EVENT_PINNED = "REGISTRY_DEVICE_ID_PINNED"
EVENT_CLASH = "REGISTRY_DEVICE_ID_CLASH"
EVENT_CHANGE_REFUSED = "REGISTRY_DEVICE_ID_CHANGE_REFUSED"
EVENT_INVALID = "REGISTRY_DEVICE_ID_INVALID"

_MAX_LEN = 64


@dataclass(frozen=True)
class PinOutcome:
    attested_id: str | None  # value for the assertion; None = OMIT the key
    event_type: str | None  # ActivationEvent type to record, or None
    result: str  # "SUCCESS" | "FAILURE"
    metadata: dict = field(default_factory=dict)  # safe_metadata: ids only


def _is_canonical_uuid(value) -> bool:
    if not isinstance(value, str) or not value or len(value) > _MAX_LEN:
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def _live_holder(installation: Installation, registry_device_id: str) -> Installation | None:
    return db_session.execute(
        select(Installation).where(
            Installation.license_id == installation.license_id,
            Installation.registry_device_id == registry_device_id,
            Installation.status.not_in(TERMINAL_STATUSES),
            Installation.id != installation.id,
        )
    ).scalars().first()


def _clash(installation: Installation, requested: str, holder: Installation | None) -> PinOutcome:
    return PinOutcome(
        None, EVENT_CLASH, "FAILURE",
        {"requested": requested, "held_by_installation": str(holder.id) if holder is not None else None},
    )


def resolve_registry_device_pin(*, installation: Installation, requested) -> PinOutcome:
    # Rule 1: an old client never sends it, so never answer with it.
    if requested is None or requested == "":
        return PinOutcome(None, None, "SUCCESS")

    existing = installation.registry_device_id or None

    # Rule 2
    if not _is_canonical_uuid(requested):
        return PinOutcome(existing, EVENT_INVALID, "FAILURE", {"pinned": existing})

    # Rule 3
    if existing is not None:
        if existing == requested:
            return PinOutcome(existing, None, "SUCCESS")
        return PinOutcome(existing, EVENT_CHANGE_REFUSED, "FAILURE", {"requested": requested, "pinned": existing})

    # Rule 4
    holder = _live_holder(installation, requested)
    if holder is not None:
        return _clash(installation, requested, holder)

    # Rule 5
    try:
        with db_session.begin_nested():
            installation.registry_device_id = requested
            installation.registry_device_id_pinned_at = datetime.now(timezone.utc)
            db_session.flush()
    except IntegrityError:
        # A concurrent request pinned this id to another live installation
        # between the check above and the flush. begin_nested() already rolled
        # the savepoint back; the installation row is expired, so its in-memory
        # pin is gone and the holder query below sees the winner.
        return _clash(installation, requested, _live_holder(installation, requested))
    return PinOutcome(requested, EVENT_PINNED, "SUCCESS", {"registry_device_id": requested})


def record_pin_event(outcome: PinOutcome, *, installation: Installation, license_id, correlation_id) -> None:
    """Add the ActivationEvent to the session. No commit: it rides the caller's
    single decision transaction. Staff read it on the installation detail page."""
    if outcome.event_type is None:
        return
    db_session.add(
        ActivationEvent(
            license_id=license_id, installation_id=installation.id, event_type=outcome.event_type,
            result=outcome.result, reason_code=outcome.event_type, correlation_id=correlation_id,
            safe_metadata=dict(outcome.metadata),
        )
    )


def audit_pin_outcome(outcome: PinOutcome, *, installation: Installation, correlation_id) -> None:
    """Audit-log row for the outcome. Call AFTER the caller's commit:
    audit_record() commits on its own."""
    if outcome.event_type is None:
        return
    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code=outcome.event_type,
        entity_type="installation", entity_public_id=str(installation.id), result=outcome.result,
        after_state=dict(outcome.metadata), correlation_id=correlation_id,
    )
