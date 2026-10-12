"""Update rollback (INSTALL-1, review #8 items 1-2) -- "go back to the version
that worked".

THE SITUATION THIS EXISTS FOR. An update replaces the program AND may migrate
the shop's database forward. If the new build then cannot start (a migration
that fails on this shop's data, a crash on boot) the shopkeeper has a till that
will not open, a database an older build may refuse to read, and nobody to ask.
BACKUP-1 already takes a safety copy before every install; this module is the
other half -- the way back:

    1. install() keeps the installer of the version being REPLACED under
       <app_data>/updates/previous/ (exactly one), plus meta.json naming its
       version and SHA-256.  state.json records {from, to, backup} for the
       update that was just launched.
    2. The first boot after an update is "on probation": until the launcher has
       seen the server answer /api/health, a boot_ok marker for that version
       does not exist.
    3. If that boot fails, the launcher asks (plain words, one question) and,
       on yes, puts the pre-update backup back and runs the kept installer.

WHY THIS IS NOT THE UPDATE PATH. UpdateService.install() refuses any version
that is not newer than the running one (the downgrade guard) and only ever runs
a file whose hash matches an Owner-SIGNED descriptor. A rollback is the
opposite on both counts: it installs an OLDER version, and its trust comes from
us having kept that exact file ourselves, hashed at the moment we kept it and
again just before it runs. So the rollback has its own entry point here, takes
NO descriptor and NO network input, and the guard in install() stays untouched:
a remote older descriptor is still refused, a local rollback is still allowed.

This module must work when the application cannot boot, so it reads and writes
plain files and never needs the UpdateService object (which lives inside the
server that just failed to start). boot_ok.json is its own file, not a key in
state.json, because the UpdateService holds state.json in memory and rewrites
it whole: a flag the launcher wrote there would be erased by the service's next
save.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .versioning import parse_semver

log = logging.getLogger("aura.updates.rollback")

PREVIOUS_DIRNAME = "previous"
META_NAME = "meta.json"
BOOT_OK_NAME = "boot_ok.json"
STATE_NAME = "state.json"
INSTALLER_PREFIX = "AuraRetail-Setup-"
INSTALLER_CACHE_DIRNAME = "installer-cache"   # beside the program; written by the .iss

# Same switches as the update path (service.INSTALLER_ARGS), repeated rather
# than imported so a future tweak to the remote path cannot silently change
# what a rollback does.
ROLLBACK_INSTALLER_ARGS = (
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/FORCECLOSEAPPLICATIONS",
    "/RELAUNCH=1",
)

_HASH_BLOCK = 1024 * 1024


class RollbackError(Exception):
    pass


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_HASH_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Optional[dict]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else None
    except (OSError, ValueError):
        return None


def installer_name(version: str) -> str:
    parse_semver(version)                       # no separators / '..' can get through
    return "%s%s.exe" % (INSTALLER_PREFIX, version.split("+", 1)[0])


def updates_dir(app_data_dir) -> Path:
    return Path(app_data_dir) / "updates"


def previous_dir(app_data_dir) -> Path:
    return updates_dir(app_data_dir) / PREVIOUS_DIRNAME


# -- keeping the installer we are about to replace ----------------------------
def preserve_previous(app_data_dir, version: str, source_path) -> Optional[dict]:
    """Copy `source_path` (the installer of `version`, the build now running)
    into previous/, hash it, and make it the ONLY file there. Returns the meta
    dict, or None if it could not be kept (logged). The update itself is never
    blocked by this: the pre-update backup is the mandatory safety, the way
    back is the bonus."""
    try:
        target_dir = previous_dir(app_data_dir)
        target_dir.mkdir(parents=True, exist_ok=True)
        name = installer_name(version)
        target = target_dir / name
        tmp = target.with_name(name + ".part")
        shutil.copyfile(source_path, tmp)
        digest = _sha256(tmp)
        os.replace(tmp, target)
        meta = {"version": version, "file": name, "sha256": digest}
        _write_json_atomic(target_dir / META_NAME, meta)
        prune_previous(app_data_dir, keep_version=version)
        log.info("updates: kept the %s installer for rollback", version)
        return meta
    except (OSError, ValueError):
        log.warning("updates: could not keep the current installer for rollback", exc_info=True)
        return None


def prune_previous(app_data_dir, keep_version: Optional[str]) -> None:
    """previous/ holds exactly one installer: delete every other exe/.part."""
    directory = previous_dir(app_data_dir)
    keep = None
    if keep_version:
        try:
            keep = installer_name(keep_version)
        except ValueError:
            keep = None
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name == META_NAME or entry.name == keep:
            continue
        if entry.suffix in (".exe", ".part"):
            try:
                entry.unlink()
                log.info("updates: pruned %s from previous/", entry.name)
            except OSError:
                log.warning("updates: could not prune %s", entry.name, exc_info=True)


@dataclass(frozen=True)
class PreviousInstaller:
    version: str
    path: Path
    sha256: str


def read_previous(app_data_dir) -> Optional[PreviousInstaller]:
    directory = previous_dir(app_data_dir)
    meta = _read_json(directory / META_NAME)
    if not meta:
        return None
    try:
        version, sha = meta["version"], meta["sha256"]
        path = directory / installer_name(version)
    except (KeyError, ValueError, TypeError):
        return None
    if not path.is_file():
        return None
    return PreviousInstaller(version=version, path=path, sha256=sha)


# -- probation: did the first boot after an update succeed? -------------------
def mark_boot_ok(app_data_dir, version: str) -> None:
    """Called by the launcher the moment /api/health answered for `version`."""
    try:
        _write_json_atomic(updates_dir(app_data_dir) / BOOT_OK_NAME, {"version": version})
    except OSError:
        log.warning("updates: could not record a healthy boot", exc_info=True)


def pending_update(app_data_dir, running_version: str) -> Optional[dict]:
    """The {from, to, backup} record of an update that installed THIS running
    version and has not yet had a healthy boot -- else None. None for a normal
    boot, an old update that already booted fine, and a fresh install."""
    state = _read_json(updates_dir(app_data_dir) / STATE_NAME) or {}
    record = state.get("last_update")
    if not isinstance(record, dict) or record.get("to") != running_version:
        return None
    ok = _read_json(updates_dir(app_data_dir) / BOOT_OK_NAME) or {}
    if ok.get("version") == running_version:
        return None
    return record


# -- the way back --------------------------------------------------------------
@dataclass(frozen=True)
class RollbackPlan:
    from_version: str            # the version we would go back to
    to_version: str              # the version that failed to start
    installer: PreviousInstaller
    backup_name: str


def plan_rollback(app_data_dir, running_version: str, backup_dir) -> Optional[RollbackPlan]:
    """A plan only when EVERYTHING needed to go back is on disk: a boot that is
    still on probation, a kept installer for exactly the version we came from,
    and the backup taken before the update (without it an older build would
    meet a database already migrated forward)."""
    record = pending_update(app_data_dir, running_version)
    if record is None:
        return None
    previous = read_previous(app_data_dir)
    from_version, backup_name = record.get("from"), record.get("backup")
    if previous is None or previous.version != from_version:
        log.info("updates: no rollback offered -- no kept installer for %s", from_version)
        return None
    if not backup_name or not os.path.isfile(os.path.join(str(backup_dir), os.path.basename(str(backup_name)))):
        log.info("updates: no rollback offered -- the pre-update backup is missing")
        return None
    return RollbackPlan(from_version=from_version, to_version=running_version,
                        installer=previous, backup_name=os.path.basename(str(backup_name)))


def execute_rollback(
    plan: RollbackPlan,
    *,
    app_data_dir,
    backup_dir,
    product_code: str,
    launch: Callable[[str, Sequence[str]], None],
    restore: Callable[[str, str, str], dict],
) -> None:
    """Put the pre-update data back, then run the kept installer. Order matters:
    restore FIRST, so a restore that fails leaves the (broken but intact) new
    build in place rather than an old build over a database it cannot read.
    The installer is hashed again right before it runs."""
    path = plan.installer.path
    if _sha256(path) != plan.installer.sha256:
        raise RollbackError("The saved installer no longer matches the copy that was kept.")
    restore(product_code, str(app_data_dir), os.path.join(str(backup_dir), plan.backup_name))
    log.info("updates: rollback -- data restored from %s; starting the %s installer",
             plan.backup_name, plan.from_version)
    launch(str(path), list(ROLLBACK_INSTALLER_ARGS))
