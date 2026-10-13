"""Phase 5 prerequisite #2 (docs/launch-readiness/phase5-prerequisites.md
section 2) -- prunes `owner_sync_events` below the slowest ACTIVE device's
cursor, per license. NEVER runs in the request path (routes.py's push/pull
handlers never import this module) -- this is its own job, invoked via
`flask sync prune-events` (app/cli.py), meant to run on a schedule
(systemd timer, same convention as `flask reports generate-scheduled` --
see deploy/systemd/).

WHY THIS EXISTS AT ALL: today owner_sync_events holds tens of rows. Once
Phase 5 widens the sync allowlist, it holds roughly 5,000 events/device/day
of full-row JSONB -- a ten-device customer alone generates ~50,000 rows a
day, on a 2GB host that also runs the web app, with zero TTL and zero
`DELETE` anywhere in routes.py or models/sync.py before this module. Left
unaddressed, this is an outage that arrives weeks after launch, when it is
hardest to fix (the design doc's own framing, preserved here because it is
the actual reason this module's correctness bar is as high as it is).

THE SAFETY RULE (the one thing this module must never get wrong): an event
is deletable ONLY once every currently-active device for its license has a
persisted cursor (app/models/sync.py::SyncDeviceCursor) at or beyond it.
Pruning by age or by row count would silently drop events a device that
was offline for a fortnight still needs -- that device would then resync
into a hole with no error, ever. There is no salvage for that failure mode
after the fact; the row is gone. So this module computes a real per-license
watermark from real device state on every run, never a heuristic.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.extensions import db_session
from app.licensing_service.device_identity import get_active_device_key
from app.models.installations import Installation
from app.models.sync import SyncDeviceCursor, SyncEvent, SyncLicenseState, SyncQuarantineEvent

# Installation statuses that can currently authenticate against
# /api/sync/v1 -- mirrors routes.py::_authenticate's own rejection set
# EXACTLY (it rejects SUSPENDED/DEACTIVATED/REPLACED), so "active" here
# means "can push/pull right now", never a looser definition. A device
# that cannot currently sync must never be allowed to pin the table
# forever (the design doc's own words: "the rule most likely to be got
# wrong"). PENDING_ACTIVATION is included even though such a device has
# never yet completed activation -- it may complete at any moment and
# would then expect its full backfilled history to still be there.
_SYNCABLE_INSTALLATION_STATUSES = ("REGISTERED", "PENDING_ACTIVATION", "ACTIVE")

DEFAULT_BATCH_SIZE = 500
DEFAULT_ROW_COUNT_ALARM_THRESHOLD = 20000
DEFAULT_QUARANTINE_RATE_ALARM_THRESHOLD = 50


@dataclass(frozen=True)
class PruneReport:
    dry_run: bool
    per_license_deleted: dict[str, int] = field(default_factory=dict)

    @property
    def total_deleted(self) -> int:
        return sum(self.per_license_deleted.values())


@dataclass(frozen=True)
class RowCountAlarm:
    license_id: str
    row_count: int
    threshold: int


@dataclass(frozen=True)
class QuarantineRateAlarm:
    license_id: str
    pending_count: int
    threshold: int


def _active_device_ids(license_id: uuid.UUID) -> list[uuid.UUID]:
    """Every device (Installation) for this license that can currently
    authenticate -- syncable installation status AND a currently-ACTIVE
    device key. Both conditions matter independently: an installation can
    be suspended/deactivated/replaced without its device key ever being
    revoked, and a device key can be revoked (licensing_admin's
    device-keys console) without the installation's own status ever
    changing -- either one alone is enough to make a device unable to
    push/pull, per _authenticate's own two independent checks."""
    installation_ids = db_session.execute(
        select(Installation.id).where(
            Installation.license_id == license_id,
            Installation.status.in_(_SYNCABLE_INSTALLATION_STATUSES),
        )
    ).scalars().all()
    return [
        installation_id for installation_id in installation_ids
        if get_active_device_key(installation_id) is not None
    ]


def _watermark_for_license(license_id: uuid.UUID) -> int | None:
    """Returns MIN(last_acked_seq) across every currently-active device for
    this license, or None if there are zero active devices.

    None is load-bearing, not just "zero": the design doc's own
    most-likely-to-be-got-wrong rule is that a license with ZERO active
    devices prunes NOTHING -- deleting a shop's entire history because
    nobody is currently listening is exactly how a reinstall loses its
    ledger. Returning 0 here instead of None would be silently wrong: 0
    happens to also be the correct watermark for "every active device
    exists but has never pulled anything yet" (see the next paragraph),
    so the caller cannot tell the two cases apart from an integer alone --
    it needs None to mean "do not prune this license at all, ever, on
    this run", distinct from a real, computed 0.

    A device with NO SyncDeviceCursor row at all (never pulled) is treated
    as watermark 0 -- "nothing acknowledged yet" -- never as "not counted".
    A brand-new active device that hasn't caught up yet must block pruning
    of everything it hasn't seen, exactly like a slow-but-caught-up-once
    device would; silently excluding it because it has no row yet would
    let its own unseen history be deleted out from under it before it
    ever gets a chance to pull.

    SYNC-BOOT Phase 1: a device now has up to one cursor row PER STREAM, and
    its single contribution to the MIN is `device_position` below."""
    active_ids = _active_device_ids(license_id)
    if not active_ids:
        return None

    cursor_rows = db_session.execute(
        select(
            SyncDeviceCursor.installation_id, SyncDeviceCursor.stream, SyncDeviceCursor.last_acked_seq,
        ).where(SyncDeviceCursor.installation_id.in_(active_ids))
    ).all()
    streams_by_device: dict[uuid.UUID, dict[str, int]] = {}
    for row in cursor_rows:
        streams_by_device.setdefault(row.installation_id, {})[row.stream] = row.last_acked_seq
    return min(device_position(streams_by_device.get(device_id, {})) for device_id in active_ids)


def device_position(positions: dict[str, int]) -> int:
    """Collapses one device's per-stream cursors into the ONE number pruning
    may trust for it (SYNC-BOOT plan section 4).

    A retail device runs two streams (retail, registry) over one installation
    id, and each is only as caught up as its own cursor. The old single row
    followed whichever stream pulled furthest, so the shop's data could be
    pruned out from under the stream that was lagging.

      * ANY stream-tagged row ('retail' / 'registry'): min(retail, registry),
        and a stream with no row yet counts as 0 -- "the registry stream has
        not reported" must hold the watermark, never be skipped. The legacy
        row is IGNORED from here on: an upgraded client's old pre-upgrade
        cursor says nothing about the streams it now tracks separately.
      * else an 'all' row (the LAN hub's forwarder, which forwards both
        streams under one cursor): that value.
      * else a 'legacy' row (an old client): that value -- today's behaviour,
        so a legacy-only device pins its licence exactly as before.
      * else 0 (no row at all).
    """
    if "retail" in positions or "registry" in positions:
        return min(positions.get("retail", 0), positions.get("registry", 0))
    if "all" in positions:
        return positions["all"]
    if "legacy" in positions:
        return positions["legacy"]
    return 0


def _record_pruned_through(license_id: uuid.UUID, seq: int) -> None:
    """Raises (never lowers) owner_sync_license_state.pruned_through_seq.
    Called INSIDE the same transaction as the delete batch it describes: if
    the delete committed without this row (or vice versa) a device could be
    served a partial stream (events gone, no RESYNC_REQUIRED) or be told to
    resync for events that still exist. GREATEST keeps it monotonic even if
    two prune runs overlap."""
    stmt = pg_insert(SyncLicenseState).values(
        license_id=license_id, pruned_through_seq=seq, last_prune_at=func.now(),
    )
    db_session.execute(stmt.on_conflict_do_update(
        index_elements=[SyncLicenseState.license_id],
        set_={
            "pruned_through_seq": func.greatest(SyncLicenseState.pruned_through_seq, stmt.excluded.pruned_through_seq),
            "last_prune_at": func.now(),
            "updated_at": func.now(),
        },
    ))


def min_stored_seq(license_id: uuid.UUID) -> int | None:
    """Lowest seq still stored for this licence, or None if it has no events."""
    return db_session.execute(
        select(func.min(SyncEvent.seq)).where(SyncEvent.license_id == license_id)
    ).scalar_one()


def mark_pruned(license_id: uuid.UUID, through: int | None = None, *, dry_run: bool = True) -> dict:
    """Operator escape hatch: explicitly record that this licence's history is
    gone up to `through`. For a live relay that pruned for real BEFORE
    pruned_through existed (the migration deliberately backfills nothing -- seq
    is shared by all licences, so it cannot tell "pruned" from "starts late").
    Default `through` = (lowest seq still stored for the licence) - 1, returned
    so the operator can review it; a licence with no stored events needs an
    explicit --through. Monotonic (GREATEST) like every other writer."""
    if through is None:
        lowest = min_stored_seq(license_id)
        if lowest is None:
            raise ValueError("licence has no stored events; pass an explicit --through")
        through = lowest - 1
    if through < 0:
        raise ValueError("--through must be >= 0")
    result = {"license_id": str(license_id), "through": through, "dry_run": dry_run}
    if not dry_run and through > 0:
        _record_pruned_through(license_id, through)
        db_session.commit()
    return result


def prune_owner_sync_events(*, dry_run: bool = True, batch_size: int = DEFAULT_BATCH_SIZE) -> PruneReport:
    """Deletes every owner_sync_events row at or below its license's
    watermark (see _watermark_for_license), one license at a time, in
    bounded batches -- NEVER one unbounded DELETE, so a single prune run
    can never hold a long lock on a table sync is actively writing to (the
    design doc's own explicit requirement). Each batch is its own
    committed transaction: `batch_size` rows deleted and committed, then
    the next batch is queried fresh, repeated until nothing more matches
    that license's watermark.

    dry_run (the default) NEVER deletes anything -- it reports, per
    license, exactly how many rows a real run WOULD delete (a single
    COUNT query, not a batch loop, since nothing is removed between
    counts there is no pagination concern to worry about)."""
    per_license_deleted: dict[str, int] = {}
    license_ids = db_session.execute(select(SyncEvent.license_id).distinct()).scalars().all()

    for license_id in license_ids:
        watermark = _watermark_for_license(license_id)
        if watermark is None:
            continue  # zero active devices -- prune NOTHING for this license, ever, on this run

        if dry_run:
            count = db_session.execute(
                select(func.count()).select_from(SyncEvent).where(
                    SyncEvent.license_id == license_id, SyncEvent.seq <= watermark
                )
            ).scalar_one()
            if count:
                per_license_deleted[str(license_id)] = count
            continue

        deleted_for_license = 0
        while True:
            batch_ids = db_session.execute(
                select(SyncEvent.id)
                .where(SyncEvent.license_id == license_id, SyncEvent.seq <= watermark)
                .order_by(SyncEvent.seq)
                .limit(batch_size)
            ).scalars().all()
            if not batch_ids:
                break
            # pruned_through advances to THIS batch's highest deleted seq, in the
            # same transaction as the delete (see _record_pruned_through). The
            # batch is the lowest-seq slice, so everything <= its max is gone
            # once the commit lands; never claim more than was actually deleted.
            batch_max_seq = db_session.execute(
                select(func.max(SyncEvent.seq)).where(SyncEvent.id.in_(batch_ids))
            ).scalar_one()
            db_session.execute(delete(SyncEvent).where(SyncEvent.id.in_(batch_ids)))
            _record_pruned_through(license_id, batch_max_seq)
            db_session.commit()  # each batch is its own short transaction, never one long-held lock
            deleted_for_license += len(batch_ids)
            if len(batch_ids) < batch_size:
                break  # fewer than a full batch matched -- nothing left for this license
        if deleted_for_license:
            per_license_deleted[str(license_id)] = deleted_for_license

    return PruneReport(dry_run=dry_run, per_license_deleted=per_license_deleted)


def check_row_count_alarms(*, threshold: int = DEFAULT_ROW_COUNT_ALARM_THRESHOLD) -> list[RowCountAlarm]:
    """Per-license row-count alarm: pruning that has silently stopped
    working looks EXACTLY like pruning that is working -- an empty diff,
    no error, nothing to notice -- so this is the independent signal that
    catches that failure mode instead of relying on someone noticing disk
    usage climb weeks later."""
    rows = db_session.execute(
        select(SyncEvent.license_id, func.count().label("row_count"))
        .group_by(SyncEvent.license_id)
        .having(func.count() > threshold)
    ).all()
    return [RowCountAlarm(license_id=str(r.license_id), row_count=r.row_count, threshold=threshold) for r in rows]


def check_quarantine_rate_alarm(*, threshold: int = DEFAULT_QUARANTINE_RATE_ALARM_THRESHOLD) -> list[QuarantineRateAlarm]:
    """A PENDING-quarantine rate above threshold means some client has
    started emitting rows the server rejects -- a release problem, and the
    first symptom of that should be an operator alert, not a customer
    phone call. Counts PENDING rows only: REPLAYED/DISCARDED rows are
    already resolved, not an ongoing problem, so they must not keep an
    alarm firing after an operator has already dealt with them (an alarm
    that never clears once tripped trains people to ignore it)."""
    rows = db_session.execute(
        select(SyncQuarantineEvent.license_id, func.count().label("pending_count"))
        .where(SyncQuarantineEvent.status == "PENDING")
        .group_by(SyncQuarantineEvent.license_id)
        .having(func.count() > threshold)
    ).all()
    return [
        QuarantineRateAlarm(license_id=str(r.license_id), pending_count=r.pending_count, threshold=threshold)
        for r in rows
    ]
