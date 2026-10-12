"""INSTALL-1: keeping the previous installer, probation, and the way back.

What each group is for (ENGINEERING.md 1: assert that the thing RAN):
  * the kept-installer tests look at the files on disk, and the "only one
    previous" test seeds a STALE one first -- a pruner that never ran would
    otherwise pass by there being nothing to prune;
  * every refusal counts BOTH collaborators (restore and installer launcher),
    because "no rollback" must mean neither ran, and "restore failed" must
    mean the installer did not;
  * the downgrade-guard pair proves both directions of one rule: a remote /
    recorded OLDER version is refused by install(), while the local rollback
    path (own entry point, no descriptor) runs an older installer.
"""
import json
import os

import pytest

from commercial_runtime.updates import rollback
from commercial_runtime.updates.descriptor import UpdateError
from commercial_runtime.updates.service import installer_filename
from commercial_runtime.updates.tests.test_service import CURRENT, Rig

OLD = "1.0.0-rc.5"


def _seed_cache(rig, version=CURRENT, data=b"CURRENT-INSTALLER"):
    cache = rig.install_dir / rollback.INSTALLER_CACHE_DIRNAME
    cache.mkdir(exist_ok=True)
    path = cache / installer_filename(version)
    path.write_bytes(data)
    return path


def _state(rig):
    return json.loads((rig.updates_dir / "state.json").read_text(encoding="utf-8"))


# -- keeping the installer being replaced --------------------------------------
def test_install_keeps_the_running_versions_installer_and_records_from_to_backup(tmp_path):
    rig = Rig(tmp_path, pre_install_backup=lambda: "b-pre-update.aurabak.zip")
    _seed_cache(rig)
    rig.to_ready()
    rig.service.install()
    kept = rollback.read_previous(rig.app_data)
    assert kept is not None and kept.version == CURRENT
    assert kept.path.read_bytes() == b"CURRENT-INSTALLER"
    assert _state(rig)["last_update"] == {"from": CURRENT, "to": "1.0.0-rc.9", "backup": "b-pre-update.aurabak.zip"}
    assert len(rig.launcher.calls) == 1          # and the update itself still ran


def test_previous_holds_exactly_one_installer(tmp_path):
    rig = Rig(tmp_path)
    prev = rollback.previous_dir(rig.app_data)
    prev.mkdir(parents=True)
    (prev / installer_filename(OLD)).write_bytes(b"STALE")
    (prev / (installer_filename("1.0.0-rc.4") + ".part")).write_bytes(b"HALF")
    (prev / rollback.META_NAME).write_text(json.dumps({"version": OLD, "sha256": "x"}))
    _seed_cache(rig)
    rig.to_ready()
    rig.service.install()
    names = sorted(p.name for p in prev.iterdir())
    assert names == sorted([installer_filename(CURRENT), rollback.META_NAME])


def test_an_update_still_installs_when_no_installer_of_the_current_version_exists(tmp_path):
    rig = Rig(tmp_path)                           # nothing cached, nothing downloaded
    rig.to_ready()
    rig.service.install()
    assert len(rig.launcher.calls) == 1
    assert rollback.read_previous(rig.app_data) is None
    assert _state(rig)["last_update"]["from"] == CURRENT


def test_startup_prunes_stray_previous_files_and_old_cache_entries(tmp_path):
    rig = Rig(tmp_path)
    prev = rollback.previous_dir(rig.app_data)
    prev.mkdir(parents=True)
    kept = prev / installer_filename(OLD)
    kept.write_bytes(b"K")
    (prev / rollback.META_NAME).write_text(json.dumps({"version": OLD, "sha256": "x"}))
    (prev / installer_filename("1.0.0-rc.4")).write_bytes(b"STRAY")
    _seed_cache(rig, CURRENT)
    stale_cache = _seed_cache(rig, "1.0.0-rc.7")
    rig.service.startup()
    assert kept.exists() and not (prev / installer_filename("1.0.0-rc.4")).exists()
    assert not stale_cache.exists()
    assert (rig.install_dir / rollback.INSTALLER_CACHE_DIRNAME / installer_filename(CURRENT)).exists()


# -- downgrade guard: both directions -------------------------------------------
def test_install_still_refuses_an_older_recorded_offer(tmp_path):
    rig = Rig(tmp_path)
    _seed_cache(rig)
    rig.to_ready()
    rig.service._state["available"]["version"] = OLD       # a remote/older descriptor
    shutil_target = rig.installer(OLD)
    shutil_target.write_bytes(b"x")
    with pytest.raises(UpdateError) as exc:
        rig.service.install()
    assert exc.value.code == "NOT_READY"
    assert rig.launcher.calls == []


def _boot_record(app_data, frm, to, backup):
    d = rollback.updates_dir(app_data)
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({"last_update": {"from": frm, "to": to, "backup": backup}}))


class _Spy:
    def __init__(self, restore_error=None):
        self.order = []
        self.restore_error = restore_error

    def restore(self, product, app_data, path):
        self.order.append(("restore", os.path.basename(path)))
        if self.restore_error:
            raise self.restore_error
        return {}

    def launch(self, path, args):
        self.order.append(("launch", os.path.basename(path), tuple(args)))


def _rollbackable(tmp_path, running="1.0.0-rc.9", frm=CURRENT, data=b"OLD-BUILD"):
    app = tmp_path / "app"
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "b-pre-update.aurabak.zip").write_bytes(b"zip")
    src = tmp_path / "src.exe"
    src.write_bytes(data)
    assert rollback.preserve_previous(app, frm, src)
    _boot_record(app, frm, running, "b-pre-update.aurabak.zip")
    return app, backups


def test_plan_exists_only_while_the_first_boot_is_unproven(tmp_path):
    app, backups = _rollbackable(tmp_path)
    plan = rollback.plan_rollback(app, "1.0.0-rc.9", backups)
    assert plan is not None and plan.from_version == CURRENT and plan.backup_name == "b-pre-update.aurabak.zip"
    rollback.mark_boot_ok(app, "1.0.0-rc.9")
    assert rollback.plan_rollback(app, "1.0.0-rc.9", backups) is None        # healthy boot: never offered


def test_boot_ok_of_another_version_does_not_clear_probation(tmp_path):
    app, backups = _rollbackable(tmp_path)
    rollback.mark_boot_ok(app, CURRENT)
    assert rollback.plan_rollback(app, "1.0.0-rc.9", backups) is not None


def test_no_plan_for_a_normal_boot_or_a_different_running_version(tmp_path):
    app, backups = _rollbackable(tmp_path)
    assert rollback.plan_rollback(app, "1.0.0-rc.10", backups) is None       # not the version the update installed
    assert rollback.plan_rollback(tmp_path / "fresh", "1.0.0-rc.9", backups) is None


def test_no_plan_without_the_kept_installer_for_that_version_or_the_backup(tmp_path):
    app, backups = _rollbackable(tmp_path)
    _boot_record(app, "1.0.0-rc.1", "1.0.0-rc.9", "b-pre-update.aurabak.zip")   # kept one is rc.8, not rc.1
    assert rollback.plan_rollback(app, "1.0.0-rc.9", backups) is None
    _boot_record(app, CURRENT, "1.0.0-rc.9", "gone.aurabak.zip")
    assert rollback.plan_rollback(app, "1.0.0-rc.9", backups) is None
    _boot_record(app, CURRENT, "1.0.0-rc.9", None)
    assert rollback.plan_rollback(app, "1.0.0-rc.9", backups) is None


def test_execute_restores_then_runs_the_older_installer_locally(tmp_path):
    app, backups = _rollbackable(tmp_path)
    plan = rollback.plan_rollback(app, "1.0.0-rc.9", backups)
    spy = _Spy()
    rollback.execute_rollback(plan, app_data_dir=app, backup_dir=backups, product_code="retail",
                              launch=spy.launch, restore=spy.restore)
    assert [step[0] for step in spy.order] == ["restore", "launch"]           # ALLOW: older version runs
    assert spy.order[1][1] == installer_filename(CURRENT)
    assert "/VERYSILENT" in spy.order[1][2] and "/RELAUNCH=1" in spy.order[1][2]


def test_a_failed_restore_never_launches_the_installer(tmp_path):
    app, backups = _rollbackable(tmp_path)
    plan = rollback.plan_rollback(app, "1.0.0-rc.9", backups)
    spy = _Spy(restore_error=OSError("file in use"))
    with pytest.raises(OSError):
        rollback.execute_rollback(plan, app_data_dir=app, backup_dir=backups, product_code="retail",
                                  launch=spy.launch, restore=spy.restore)
    assert [step[0] for step in spy.order] == ["restore"]


def test_a_tampered_kept_installer_runs_nothing(tmp_path):
    app, backups = _rollbackable(tmp_path)
    plan = rollback.plan_rollback(app, "1.0.0-rc.9", backups)
    plan.installer.path.write_bytes(b"TAMPERED")
    spy = _Spy()
    with pytest.raises(rollback.RollbackError):
        rollback.execute_rollback(plan, app_data_dir=app, backup_dir=backups, product_code="retail",
                                  launch=spy.launch, restore=spy.restore)
    assert spy.order == []                                                    # neither restore nor launch
