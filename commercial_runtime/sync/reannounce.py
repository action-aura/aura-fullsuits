"""Re-announce the shop's authority state, SIGNED, once after an upgrade (AUDIT-033 B2b).

THE PROBLEM. Relays keep history. Every authority event a shop made BEFORE the
upgrade (the owner, the cashiers, their permissions, who the main computer is, the
till numbers) sits on the relay unsigned. A device that joins -- or re-pulls from
cursor 0 -- after the upgrade receives all of it, and every up-to-date receiver
parks it as `unsigned_authority_event`: no users, no permissions, no main
computer, no till numbers, so nobody can sign in on the new computer. Events
parked during a mixed-version period stay parked for ever for the same reason.

THE FIX. After this computer FIRST has a signer, it re-emits the current state
it holds, through the SAME emitters ordinary changes use (so the payloads are
byte-for-byte the normal shapes), with the SAME versions: no `row_version`, no
`epoch` is ever bumped here -- a re-announce is a statement of what is already
true, never a change. `read_outbox` signs them like any authority event.

  * on EVERY computer: its own `device` self-announce, and the numbering books
    (`doc_series`) it owns;
  * on the MAIN computer, in addition: the main-computer assignment, EVERY user
    (the owner included), EVERY user permission, every till allocation and every
    numbering book. The main computer is the only one entitled to speak for the
    shop's staff, permissions and till numbers (`authority.authority_refusal`).

Receivers: an equal-version re-emit is a no-op on a device that already has the
row and applies on one that lacks it (the appliers' own version gates; user
permissions carry the signed `_reannounce` marker, meaning "insert if absent,
never overwrite"). Once a signed event applies, the unsigned parked copies it
answers are cleared (`SyncService._supersede_parked_unsigned`).

WHEN IT RUNS (`run_if_due`, called from the same boot / heartbeat / "changed"
seam that announces the device):
  * once per device, when a signer first exists (marker `self_done`);
  * again each time this device is the main computer at an epoch it has not yet
    re-announced at -- i.e. whenever it BECOMES main by a claim, a transfer or a
    takeover, the new main re-signs the shop state once (marker `main_done`).

THE MARKER is a small device-local file next to `local_device.json`
(`<app data>/device/authority_reannounce.json`), chosen over a table or a setting
because it needs no schema bump (registry v10 is claimed and shipped as is), can
never be synced (no sync path reads that directory) and is not a business fact
anyone should be able to edit through the app. It is written only AFTER both
outboxes are committed, so a crash in between re-sends (harmless duplicates)
instead of never sending. A device with no signer writes no marker and tries
again on the next tick: queuing events a device cannot sign would only send them
unsigned.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from commercial_runtime.sync import authority

logger = logging.getLogger(__name__)

MARKER_NAME = "authority_reannounce.json"
#: Version 2 (AUDIT-033 fix round, verifier V finding #1): receivers now refuse a
#: signer whose Owner lease does not attest its registry id, so a re-announce done
#: before this fix (under a lease that attested nothing) was judged by nobody and
#: answered nothing. A version-1 marker is therefore treated as "never done" and
#: the re-announce runs once more -- idempotent by construction. `run_if_due`
#: already waits for `local_signer_or_none`, which now returns None until the
#: lease attests this computer, so the re-announce waits for an attested lease.
MARKER_VERSION = 2

#: `retail_emit(is_main, device_id) -> int`: queues the RETAIL-stream part
#: (till allocations, numbering books) and COMMITS it itself, returning how many
#: events it queued. A callback because the payload builders live in the product
#: (`retail_api`) and `commercial_runtime` must not import products.
RetailEmitter = Callable[[bool, str], int]


def marker_path(app_data_dir) -> Path:
    return Path(app_data_dir) / "device" / MARKER_NAME


def read_marker(app_data_dir) -> dict:
    """The marker, or an empty one. An unreadable file reads as empty: the worst
    outcome is one extra (idempotent) re-announce, never a skipped one."""
    try:
        raw = json.loads(marker_path(app_data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"v": MARKER_VERSION, "self_done": False, "main_done": []}
    if not isinstance(raw, dict) or raw.get("v") != MARKER_VERSION:
        return {"v": MARKER_VERSION, "self_done": False, "main_done": []}
    main_done = raw.get("main_done")
    return {"v": MARKER_VERSION,
            "self_done": raw.get("self_done") is True,
            "main_done": [str(x) for x in main_done] if isinstance(main_done, list) else []}


def write_marker(app_data_dir, marker: dict) -> None:
    path = marker_path(app_data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(marker, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _main_key(company_id: str, epoch) -> str:
    return f"{company_id}:{epoch}"


def queue_registry_state(reg: sqlite3.Connection, company_id: str, device_id: str,
                         *, is_main: bool) -> int:
    """Queue the REGISTRY-stream re-announce into registry.db's outbox through the
    existing emitters, never bumping a version. NO commit. Returns the number of
    events queued."""
    from commercial_runtime.identity import device_registry, user_accounts

    queued = 0
    device_registry.queue_device_sync_event(reg, device_id, "update")
    queued += 1
    if not is_main:
        return queued
    if device_registry.get_assignment(reg, company_id) is not None:
        device_registry.queue_admin_device_sync_event(reg, company_id, "update")
        queued += 1
    # Every user the shop has, the owner included. `_queue_user_sync_event` re-reads
    # the row, so the payload is what is stored, at the version it is stored at.
    # Rows with no uid cannot be addressed on the wire and are skipped by it.
    # A soft-deleted account is NOT re-announced: the wire has no "deleted" flag
    # (`_SYNCED_USER_COLUMNS`), so re-stating it would resurrect it, active, on a
    # device that never saw it.
    try:
        user_ids = [r[0] for r in reg.execute(
            "SELECT id FROM users WHERE company_id=? AND uid IS NOT NULL AND deleted_at_utc IS NULL",
            (company_id,)).fetchall()]
    except sqlite3.OperationalError:   # a registry without the soft-delete column
        user_ids = [r[0] for r in reg.execute(
            "SELECT id FROM users WHERE company_id=? AND uid IS NOT NULL", (company_id,)).fetchall()]
    for user_id in user_ids:
        user_accounts._queue_user_sync_event(reg, user_id, "update")
        queued += 1
        for (subsystem,) in reg.execute(
                "SELECT subsystem FROM user_permissions WHERE user_id=?", (user_id,)).fetchall():
            user_accounts._queue_user_permission_sync_event(
                reg, user_id, subsystem, "update", reannounce=True)
            queued += 1
    return queued


def run_if_due(reg: sqlite3.Connection, company_id: str, *, app_data_dir,
               retail_emit: Optional[RetailEmitter] = None) -> dict:
    """Re-announce if this device has not yet done so for its current situation.
    Cheap when nothing is due (a small file read and one registry lookup), so it is
    safe on every heartbeat. NEVER raises: a till must keep running. Commits the
    registry outbox itself. Returns a small report."""
    report = {"ran": False, "why": None, "registry_events": 0, "retail_events": 0}
    try:
        app = Path(app_data_dir)
        device_id = authority._local_device_id(app)
        if not device_id:
            report["why"] = "no_local_device_id"
            return report
        from commercial_runtime.identity.device_registry import get_assignment
        assignment = get_assignment(reg, company_id)
        is_main = bool(assignment) and str(assignment["device_id"]) == device_id
        marker = read_marker(app)
        main_key = _main_key(company_id, assignment["epoch"]) if is_main else None
        self_due = not marker["self_done"]
        main_due = is_main and main_key not in marker["main_done"]
        if not (self_due or main_due):
            report["why"] = "nothing_due"
            return report
        if authority.local_signer_or_none(app) is None:
            # No marker: the first tick WITH a signer does it. Queuing now would
            # only send these events unsigned, to be parked by every peer.
            report["why"] = "no_signer"
            return report

        report["registry_events"] = queue_registry_state(
            reg, company_id, device_id, is_main=is_main)
        try:
            if retail_emit is not None:
                report["retail_events"] = int(retail_emit(is_main, device_id) or 0)
            reg.commit()
        except Exception:
            reg.rollback()
            raise
        marker["self_done"] = True
        if is_main and main_key not in marker["main_done"]:
            marker["main_done"] = [*marker["main_done"], main_key]
        write_marker(app, marker)
        report["ran"] = True
        logger.info("authority re-announce: queued %d registry and %d retail events (main=%s)",
                    report["registry_events"], report["retail_events"], is_main)
        return report
    except Exception:  # noqa: BLE001 -- see docstring: a till must keep running
        logger.warning("authority re-announce failed; it will be tried again on the next tick",
                       exc_info=True)
        report["why"] = "error"
        return report


def force_main_reannounce(reg: sqlite3.Connection, company_id: str, *, app_data_dir,
                          retail_emit: Optional[RetailEmitter] = None) -> dict:
    """Re-announce the shop's authority state NOW, because a computer asked for a
    bootstrap (SYNC-BOOT Phase 2). `run_if_due` answers "has this computer done
    its once-per-epoch announce"; this answers "somebody joined or fell behind
    and needs the CURRENT state, signed, after the point it fast-forwarded to".
    It must therefore run every time it is asked: the new computer's registry
    cursor was moved to the log's end at ITS request, so a re-announce made
    earlier (even a moment earlier, for another computer) sits behind that cursor
    and the new computer would never see it.

    ONLY THE MAIN COMPUTER DOES ANYTHING. Everyone else is not entitled to speak
    for the shop's staff or till numbers (`authority.authority_refusal` would
    park what it sent), so a non-main computer that sees a request returns
    `ran=False, why='not_main'` and sends nothing. A computer that cannot sign
    sends nothing either: unsigned authority events are parked by every peer.

    Same emitters, same versions, no bump -- a statement of what is already true,
    exactly as `run_if_due`. Does NOT touch the once-per-epoch marker (that is
    `run_if_due`'s record of its own, separate duty). NEVER raises."""
    report = {"ran": False, "why": None, "registry_events": 0, "retail_events": 0}
    try:
        app = Path(app_data_dir)
        device_id = authority._local_device_id(app)
        if not device_id:
            report["why"] = "no_local_device_id"
            return report
        from commercial_runtime.identity.device_registry import get_assignment
        assignment = get_assignment(reg, company_id)
        if not (assignment and str(assignment["device_id"]) == device_id):
            report["why"] = "not_main"
            return report
        if authority.local_signer_or_none(app) is None:
            report["why"] = "no_signer"
            return report
        report["registry_events"] = queue_registry_state(reg, company_id, device_id, is_main=True)
        try:
            if retail_emit is not None:
                report["retail_events"] = int(retail_emit(True, device_id) or 0)
            reg.commit()
        except Exception:
            reg.rollback()
            raise
        report["ran"] = True
        logger.info("authority re-announce (bootstrap request): queued %d registry and %d retail events",
                    report["registry_events"], report["retail_events"])
        return report
    except Exception:  # noqa: BLE001 -- see docstring: a till must keep running
        logger.warning("forced authority re-announce failed; the requester will ask again", exc_info=True)
        report["why"] = "error"
        return report
