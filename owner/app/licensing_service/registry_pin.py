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

Rules, in this order:
 1. No id sent (absent, None, "") -> nothing happens and the key is NEVER put
    in the assertion. Old clients reject unknown assertion fields
    (assertion_verifier.py's allowlist; the Phase 8 Part W outage), so an old
    client must keep getting exactly the lease it always got.
 2. Not a canonical UUID string (<= 64 chars, str(uuid.UUID(x)) == x) ->
    REGISTRY_DEVICE_ID_INVALID, nothing pinned.
 3. The installation already holds a pin -> the pin is immutable. Same id again
    is a no-op; a different id records REGISTRY_DEVICE_ID_CHANGE_REFUSED and
    the lease keeps carrying the ORIGINAL pin.
 4. Another installation ON THE SAME LICENCE CHAIN holds this id, in ANY status
    (review V3o G1/G2) -> REGISTRY_DEVICE_ID_CLASH. A pinned id is reserved for
    its installation for good: it survives deactivation, slot replacement and
    licence replacement. Nothing is pinned and the lease omits the key. The
    activation / check-in itself still SUCCEEDS: Owner never bricks licensing
    over this. The id moves only by transfer_registry_device_id (staff).
 5. History guard (review V3o G3): if the shop's synced history already names
    this id from before this installation existed, this installation cannot
    be the id's original computer -> REGISTRY_DEVICE_ID_HISTORY_CONFLICT, nothing
    pinned. Same lease/outcome shape as a clash.
 6. Otherwise pin it inside a savepoint. A concurrent pin that wins the unique
    index (uq_owner_installations_license_registry_device) is treated as rule 4.

NOT implemented (plan R2 carry-over by device-key fingerprint): a claimant on a
replacement licence can never present the key of the holder, because
owner_device_public_keys.fingerprint is globally UNIQUE (one key, one
installation, ever) and activation answers DEVICE_ALREADY_REGISTERED first. The
only way an id changes hands is therefore the staff transfer.

None of these outcomes raises: a pin problem must never turn into a failed
activation or check-in.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.models.installations import ActivationEvent, Installation
from app.models.licensing import License
from app.models.sync import SyncEvent

EVENT_PINNED = "REGISTRY_DEVICE_ID_PINNED"
EVENT_CLASH = "REGISTRY_DEVICE_ID_CLASH"
EVENT_CHANGE_REFUSED = "REGISTRY_DEVICE_ID_CHANGE_REFUSED"
EVENT_INVALID = "REGISTRY_DEVICE_ID_INVALID"
EVENT_HISTORY_CONFLICT = "REGISTRY_DEVICE_ID_HISTORY_CONFLICT"
EVENT_TRANSFERRED = "REGISTRY_ID_TRANSFERRED"

# How much earlier than the installation's own creation a synced event naming
# the id may be before we call it "the id was already in the shop's history".
# client_created_at is the ORIGINAL device's own clock, which can run behind
# Owner's (or the main's first events can be stamped a little before it
# reached Owner). Ten minutes forgives that skew; a real second computer is
# activated hours or days after the main's first synced events.
HISTORY_SKEW_MARGIN = timedelta(minutes=10)

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


def licence_chain_ids(session, license_id) -> set:
    """Every licence connected to `license_id` through replaced_by_license_id,
    in both directions (older and newer). staff replace a licence by issuing a
    new one (licensing/services.py::replace_license); the shop is the same."""
    chain = {license_id}
    frontier = {license_id}
    while frontier:
        rows = session.execute(
            select(License.id, License.replaced_by_license_id).where(
                or_(License.id.in_(frontier), License.replaced_by_license_id.in_(frontier))
            )
        ).all()
        found = set()
        for lid, replaced_by in rows:
            found.add(lid)
            if replaced_by is not None:
                found.add(replaced_by)
        frontier = found - chain
        chain |= found
    return chain


def _holder(installation: Installation, registry_device_id: str) -> Installation | None:
    """Any installation on the licence chain pinned to this id, whatever its status."""
    return db_session.execute(
        select(Installation).where(
            Installation.license_id.in_(licence_chain_ids(db_session, installation.license_id)),
            Installation.registry_device_id == registry_device_id,
            Installation.id != installation.id,
        )
    ).scalars().first()


def _first_history_at(chain: set, registry_device_id: str) -> datetime | None:
    """client_created_at of the earliest synced event that names the id:
    a registry `device` row (entity_id is the id) or an `admin_device` event
    (payload.device_id is the id). Two bounded index lookups, never a scan."""
    as_uuid = uuid.UUID(registry_device_id)
    best = None
    for cond in (
        (SyncEvent.entity_type == "device") & (SyncEvent.entity_id == as_uuid),
        (SyncEvent.entity_type == "admin_device") & (SyncEvent.payload["device_id"].astext == registry_device_id),
    ):
        got = db_session.execute(
            select(SyncEvent.client_created_at).where(SyncEvent.license_id.in_(chain), cond).order_by(SyncEvent.seq).limit(1)
        ).scalar()
        if got is not None and (best is None or got < best):
            best = got
    return best


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
    holder = _holder(installation, requested)
    if holder is not None:
        return _clash(installation, requested, holder)

    # Rule 5: history guard
    first_seen = _first_history_at(licence_chain_ids(db_session, installation.license_id), requested)
    created = installation.created_at
    if first_seen is not None and created is not None and first_seen < created - HISTORY_SKEW_MARGIN:
        return PinOutcome(
            None, EVENT_HISTORY_CONFLICT, "FAILURE",
            {"requested": requested, "first_seen_at": first_seen.isoformat(), "installation_created_at": created.isoformat()},
        )

    # Rule 6
    try:
        with db_session.begin_nested():
            installation.registry_device_id = requested
            installation.registry_device_id_pinned_at = datetime.now(timezone.utc)
            db_session.flush()
    except IntegrityError:
        # A concurrent request pinned this id to another installation
        # between the check above and the flush (the index covers every status). begin_nested() already rolled
        # the savepoint back; the installation row is expired, so its in-memory
        # pin is gone and the holder query below sees the winner.
        return _clash(installation, requested, _holder(installation, requested))
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


class RegistryTransferError(Exception):
    """A staff transfer was refused. `code` is stable for the CLI and tests."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def transfer_registry_device_id(
    session, *, registry_device_id: str, to_installation_id, actor_staff_user_id, reason: str
) -> None:
    """The ONLY way a pinned registry id changes installation (review V3o R4).

    Staff say "installation T is the real computer for id X". The current
    holder (any status) and T must be on the same licence chain and T must not
    already hold a different id. The holder is cleared and T pinned in one
    transaction, with an ActivationEvent on both installations and an audit row.
    Never auto-triggered: a pin is reserved for good until a human decides."""
    if not isinstance(reason, str) or not reason.strip():
        raise RegistryTransferError("REASON_REQUIRED")
    if not _is_canonical_uuid(registry_device_id):
        raise RegistryTransferError("REGISTRY_DEVICE_ID_INVALID")
    target = session.get(Installation, to_installation_id)
    if target is None:
        raise RegistryTransferError("TARGET_NOT_FOUND")
    if target.registry_device_id == registry_device_id:
        raise RegistryTransferError("TARGET_ALREADY_HOLDS_ID")
    if target.registry_device_id is not None:
        raise RegistryTransferError("TARGET_PINNED_TO_A_DIFFERENT_ID")
    chain = licence_chain_ids(session, target.license_id)
    holder = session.execute(
        select(Installation).where(
            Installation.registry_device_id == registry_device_id, Installation.license_id.in_(chain)
        )
    ).scalars().first()
    if holder is None:
        elsewhere = session.execute(
            select(Installation.id).where(Installation.registry_device_id == registry_device_id).limit(1)
        ).first()
        raise RegistryTransferError("HOLDER_ON_A_DIFFERENT_LICENCE_CHAIN" if elsewhere else "NO_CURRENT_HOLDER")

    metadata = {
        "registry_device_id": registry_device_id, "from_installation": str(holder.id),
        "to_installation": str(target.id), "reason": reason.strip(),
    }
    holder.registry_device_id = None
    holder.registry_device_id_pinned_at = None
    session.flush()
    target.registry_device_id = registry_device_id
    target.registry_device_id_pinned_at = datetime.now(timezone.utc)
    for inst in (holder, target):
        session.add(
            ActivationEvent(
                license_id=inst.license_id, installation_id=inst.id, event_type=EVENT_TRANSFERRED, result="SUCCESS",
                reason_code=EVENT_TRANSFERRED, originating_staff_user_id=actor_staff_user_id, safe_metadata=dict(metadata),
            )
        )
    session.commit()
    audit_record(
        actor_staff_user_id=actor_staff_user_id, actor_role_snapshot=None, action_code=EVENT_TRANSFERRED,
        entity_type="installation", entity_public_id=str(target.id), result="SUCCESS",
        after_state=dict(metadata), reason=reason.strip(),
    )
