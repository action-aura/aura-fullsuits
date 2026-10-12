"""
Aura FullSuits -- BACKUP-1: automatic backups that leave the device.

service.py makes and restores ONE archive. This module decides WHEN one is
made, which old ones may go, and where a second copy lands. Why it exists:
before this wave a backup happened only if a person remembered to click, and
it always sat on the same disk as the data it protected -- so a dead disk, a
stolen till or a wiped phone took the shop AND its backups. "A backup nobody
turned on" was the failure mode, so the default here is ON.

Rules this module holds (each one has a test that fails if it is broken):

  * `backup_schedule` is 'daily' unless the owner says 'off'. Missing, corrupt
    or unreadable settings read as 'daily', never as 'off'.
  * One automatic backup per business day (the caller supplies the day key,
    because the shop's trading day belongs to retail's own settings).
  * Retention prunes ONLY files tagged `-auto`. Manual backups and the
    'pre-update' safety copy are never deleted by code -- a person made them
    on purpose, and a bug here must not be able to cost one.
  * The second copy is best-effort: an unplugged USB stick is recorded as
    "copy skipped", it never turns a good backup into a failed one.
  * Encryption is deliberately NOT here: a forgotten passphrase would make the
    backup worthless. Recorded in ROADMAP as a future opt-in.

Scope of the settings: install-wide, not per company -- a backup is a copy of
registry.db + the product db, which hold every company of the install. So the
two settings live in a small JSON file next to the data, not in the
company-scoped `retail_settings` table (no schema change in this wave).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .service import (
    BackupError,
    REASON_AUTO,
    REASON_PRE_UPDATE,
    create_backup,
    is_safe_backup_dir,
)

log = logging.getLogger("aura.backup")

SCHEDULE_OFF = "off"
SCHEDULE_DAILY = "daily"
SCHEDULES = (SCHEDULE_OFF, SCHEDULE_DAILY)

KEEP_DAILY = 7
KEEP_WEEKLY = 4
#: No successful backup (of any kind) for this long -> the warning pill.
STALE_AFTER = timedelta(hours=48)
#: After a FAILED attempt, wait this long before trying again. Without it a
#: persistent failure (disk full) would retry on every tick and fill the log.
RETRY_AFTER_FAILURE = timedelta(hours=1)
#: Safety net under the business-day rule: even if the day key never changes
#: (a broken zone setting), a backup this old is always due.
MAX_AGE = timedelta(hours=24)
TICK_SECONDS = 15 * 60
FIRST_TICK_DELAY_SECONDS = 5 * 60

_NAME_RE = re.compile(
    r"^aura-(?P<product>[a-z]+)-backup-(?P<stamp>\d{8}T\d{6}Z)-app.+?-schema\d+"
    r"(?:-(?P<reason>auto|pre-update))?\.aurabak\.zip$"
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(raw) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_backup_name(name: str):
    """(created_at_utc, reason) for a file name we wrote, else None. Reason is
    'manual' for an untagged name. Anything that does not match is not ours
    and is never touched by retention."""
    m = _NAME_RE.match(name or "")
    if not m:
        return None
    try:
        stamp = datetime.strptime(m.group("stamp"), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return stamp, (m.group("reason") or "manual")


# ── settings + status (small JSON files, atomic writes) ─────────────────────

def _state_dir(app_data_dir) -> str:
    return os.path.join(str(app_data_dir), "backup_state")


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("backup: %s unreadable, using defaults", os.path.basename(path), exc_info=True)
        return {}


def _write_json(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


class BackupSettings:
    """backup_schedule + backup_dir_secondary. Unreadable file == defaults, and
    the default schedule is 'daily' (see module docstring)."""

    def __init__(self, app_data_dir):
        self._path = os.path.join(_state_dir(app_data_dir), "settings.json")
        self._lock = threading.Lock()

    def load(self) -> dict:
        raw = _read_json(self._path)
        schedule = raw.get("backup_schedule")
        return {
            "backup_schedule": schedule if schedule in SCHEDULES else SCHEDULE_DAILY,
            "backup_dir_secondary": str(raw.get("backup_dir_secondary") or ""),
        }

    def save(self, *, schedule=None, secondary_dir=None) -> dict:
        """Validates, then writes. Raises BackupError for a value we refuse."""
        with self._lock:
            current = self.load()
            if schedule is not None:
                if schedule not in SCHEDULES:
                    raise BackupError("backup_schedule must be 'off' or 'daily'")
                current["backup_schedule"] = schedule
            if secondary_dir is not None:
                secondary_dir = str(secondary_dir).strip()
                if secondary_dir and not is_safe_backup_dir(secondary_dir):
                    # Same guard create_backup applies to the primary folder.
                    raise BackupError("That folder is inside Program Files and cannot receive backups")
                current["backup_dir_secondary"] = secondary_dir
            _write_json(self._path, current)
            return current


class BackupStatus:
    """What the last automatic run did. Written by the scheduler, read by the
    Settings screen. It is an index of what happened, not a trust anchor."""

    def __init__(self, app_data_dir):
        self._path = os.path.join(_state_dir(app_data_dir), "status.json")
        self._lock = threading.Lock()

    def load(self) -> dict:
        return _read_json(self._path)

    def update(self, **changes) -> dict:
        with self._lock:
            data = self.load()
            data.update(changes)
            try:
                _write_json(self._path, data)
            except OSError:
                log.warning("backup: could not write status.json", exc_info=True)
            return data

    def first_seen(self, now: datetime) -> datetime:
        """When this install first looked at its backup status. The 48 h
        warning is measured from here for an install that has never backed up,
        so a brand-new shop is not told off in its first minute."""
        data = self.load()
        seen = _parse_iso(data.get("first_seen_at"))
        if seen is None:
            seen = now
            self.update(first_seen_at=_iso(now))
        return seen


# ── retention ───────────────────────────────────────────────────────────────

def _auto_files(directory: str, product_code: str):
    """[(created_at, name)] of the AUTOMATIC backups for this product, newest
    first. The only set retention may ever delete from."""
    out = []
    try:
        names = os.listdir(directory)
    except OSError:
        return out
    for name in names:
        parsed = parse_backup_name(name)
        if not parsed:
            continue
        stamp, reason = parsed
        if reason != REASON_AUTO or not name.startswith(f"aura-{product_code}-backup-"):
            continue
        out.append((stamp, name))
    out.sort(reverse=True)
    return out


def prune_automatic(directory: str, product_code: str, keep_daily=KEEP_DAILY, keep_weekly=KEEP_WEEKLY):
    """Keep the newest automatic backup of each of the last `keep_daily` days,
    plus the newest of each of the next `keep_weekly` ISO weeks that lie beyond
    those days. Delete the rest of the AUTOMATIC files. Returns deleted names.

    Decided from file names alone (clock-free), so it is deterministic and
    cannot be fooled by a copied file's mtime."""
    files = _auto_files(directory, product_code)
    keep = set()
    days = []
    for stamp, name in files:  # newest first
        day = stamp.date()
        if day in days:
            continue
        if len(days) >= keep_daily:
            break
        days.append(day)
        keep.add(name)
    oldest_daily_day = days[-1] if days else None
    weeks = []
    for stamp, name in files:
        if oldest_daily_day is not None and stamp.date() >= oldest_daily_day:
            continue  # inside the daily window already
        week = stamp.isocalendar()[:2]
        if week in weeks:
            continue
        if len(weeks) >= keep_weekly:
            break
        weeks.append(week)
        keep.add(name)
    deleted = []
    for _stamp, name in files:
        if name in keep:
            continue
        try:
            os.remove(os.path.join(directory, name))
            deleted.append(name)
        except OSError:
            log.warning("backup: could not prune %s", name, exc_info=True)
    return deleted


# ── one automatic run ───────────────────────────────────────────────────────

def _copy_to_secondary(src_path: str, secondary_dir: str, product_code: str, now: datetime) -> dict:
    """Never raises. Outcome is data: {'at','ok','folder','outcome','reason'}."""
    result = {"at": _iso(now), "ok": False, "folder": secondary_dir, "outcome": "skipped", "reason": ""}
    if not secondary_dir:
        result["outcome"] = "not_configured"
        return result
    if not is_safe_backup_dir(secondary_dir):
        result["reason"] = "unsafe_folder"
        return result
    if not os.path.isdir(secondary_dir):
        # An unplugged USB stick / signed-out cloud folder. We do NOT create
        # it: makedirs on a vanished drive letter would either fail or, worse,
        # quietly create a plain folder on the system disk and report success.
        result["reason"] = "folder_missing"
        return result
    final = os.path.join(secondary_dir, os.path.basename(src_path))
    partial = final + ".partial"
    try:
        shutil.copyfile(src_path, partial)
        os.replace(partial, final)
        prune_automatic(secondary_dir, product_code)
    except OSError as exc:
        try:
            os.remove(partial)
        except OSError:
            pass
        result["reason"] = f"copy_failed: {exc.__class__.__name__}"
        return result
    result.update(ok=True, outcome="copied", reason="")
    return result


def run_automatic_backup(product_code, app_data_dir, backup_dir, app_version, *,
                         settings: BackupSettings, status: BackupStatus,
                         now: Optional[datetime] = None) -> dict:
    """One automatic backup + prune + best-effort second copy. Records the
    outcome in status.json. Never raises; returns the status dict section."""
    now = now or _utcnow()
    status.update(last_attempt_at=_iso(now))
    try:
        made = create_backup(product_code, app_data_dir, backup_dir, app_version, reason=REASON_AUTO)
    except Exception as exc:  # BackupError, sqlite errors, disk full ...
        log.warning("backup: automatic backup failed: %s", exc, exc_info=not isinstance(exc, BackupError))
        status.update(last_auto={"at": _iso(now), "ok": False, "filename": None, "error": str(exc)[:300]})
        return status.load()
    name = os.path.basename(made["path"])
    try:
        prune_automatic(backup_dir, product_code)
    except Exception:
        log.warning("backup: pruning failed (backup itself is fine)", exc_info=True)
    secondary = _copy_to_secondary(
        made["path"], settings.load()["backup_dir_secondary"], product_code, now)
    status.update(
        last_auto={"at": _iso(now), "ok": True, "filename": name, "error": None},
        last_auto_success_at=_iso(now),
        last_secondary=secondary,
    )
    log.info("backup: automatic backup %s done; second copy: %s %s",
             name, secondary["outcome"], secondary["reason"])
    return status.load()


def make_pre_update_backup(product_code, app_data_dir, backup_dir, app_version) -> Callable[[], str]:
    """The callable UpdateService runs before it launches an installer.
    Returns the new file name; raises BackupError/OSError if it cannot -- the
    updater turns ANY exception into a refusal to install."""
    def run() -> str:
        made = create_backup(product_code, app_data_dir, backup_dir, app_version, reason=REASON_PRE_UPDATE)
        return os.path.basename(made["path"])
    return run


# ── health (the 48 h warning) ───────────────────────────────────────────────

def newest_backup_time(backup_dir: str, product_code: str) -> Optional[datetime]:
    """Newest backup of ANY kind, from file names."""
    newest = None
    try:
        names = os.listdir(backup_dir)
    except OSError:
        return None
    for name in names:
        parsed = parse_backup_name(name)
        if parsed and name.startswith(f"aura-{product_code}-backup-"):
            if newest is None or parsed[0] > newest:
                newest = parsed[0]
    return newest


def build_status(product_code, backup_dir, settings: BackupSettings, status: BackupStatus,
                 now: Optional[datetime] = None) -> dict:
    now = now or _utcnow()
    cfg = settings.load()
    data = status.load()
    newest = newest_backup_time(backup_dir, product_code)
    reference = newest or status.first_seen(now)
    stale = (now - reference) > STALE_AFTER
    return {
        "backup_schedule": cfg["backup_schedule"],
        "backup_dir_secondary": cfg["backup_dir_secondary"],
        "last_auto": data.get("last_auto"),
        "last_secondary": data.get("last_secondary"),
        "last_backup_at": _iso(newest) if newest else None,
        # True => Settings shows the warning pill. Counts manual backups too:
        # what matters is "is there a recent copy", not who made it.
        "warning": stale,
        "warning_reason": ("no_recent_backup" if newest else "never_backed_up") if stale else None,
    }


# ── the scheduler ───────────────────────────────────────────────────────────

class BackupScheduler:
    """Timer chain shaped like updates.scheduler.UpdateScheduler: daemon
    timers, a tick that can never kill the chain, start/stop idempotent.

    `tick()` is public and clock-injectable so tests drive it without sleeping.
    `day_key_fn(now_utc) -> str` names the shop's business day (retail passes
    one built on core.retail.metrics.business_now)."""

    def __init__(self, product_code, app_data_dir, backup_dir, app_version, *,
                 settings: BackupSettings, status: BackupStatus,
                 day_key_fn: Optional[Callable[[datetime], str]] = None,
                 clock: Callable[[], datetime] = _utcnow,
                 first_delay: float = FIRST_TICK_DELAY_SECONDS,
                 interval: float = TICK_SECONDS,
                 timer_factory: Callable[..., threading.Timer] = threading.Timer):
        self._args = (product_code, app_data_dir, backup_dir, app_version)
        self._settings = settings
        self._status = status
        self._day_key = day_key_fn or (lambda dt: dt.astimezone().date().isoformat())
        self._clock = clock
        self._first_delay = first_delay
        self._interval = interval
        self._timer_factory = timer_factory
        self._timer = None
        self._stopped = False
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()  # one backup at a time

    def is_due(self, now: datetime) -> bool:
        if self._settings.load()["backup_schedule"] != SCHEDULE_DAILY:
            return False
        data = self._status.load()
        attempt = _parse_iso(data.get("last_attempt_at"))
        last_auto = data.get("last_auto") or {}
        if attempt and last_auto.get("ok") is False and now - attempt < RETRY_AFTER_FAILURE:
            return False
        last_ok = _parse_iso(data.get("last_auto_success_at"))
        if last_ok is None:
            return True
        if now - last_ok >= MAX_AGE:
            return True
        try:
            return self._day_key(now) != self._day_key(last_ok)
        except Exception:
            log.warning("backup: day key failed; falling back to the 24h rule", exc_info=True)
            return False

    def tick(self) -> Optional[dict]:
        """Run a backup if one is due. Returns the status or None."""
        now = self._clock()
        if not self.is_due(now):
            return None
        if not self._run_lock.acquire(blocking=False):
            return None
        try:
            product_code, app_data_dir, backup_dir, app_version = self._args
            return run_automatic_backup(
                product_code, app_data_dir, backup_dir, app_version,
                settings=self._settings, status=self._status, now=now)
        finally:
            self._run_lock.release()

    def start(self) -> None:
        with self._lock:
            if self._timer is not None or self._stopped:
                return
            self._arm(self._first_delay)
        log.info("backup: scheduler started (first look in %ss, then every %ss)", self._first_delay, self._interval)

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._timer is not None:
                self._timer.cancel()

    def _arm(self, delay: float) -> None:
        timer = self._timer_factory(delay, self._run_tick)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _run_tick(self) -> None:
        try:
            self.tick()
        except Exception:
            log.exception("backup: scheduler tick crashed")
        finally:
            with self._lock:
                if not self._stopped:
                    self._arm(self._interval)
