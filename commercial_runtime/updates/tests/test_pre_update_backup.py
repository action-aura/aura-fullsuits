"""BACKUP-1: no safety copy, no install.

The refusal test counts launcher calls (an outcome assertion alone cannot tell
"refused before acting" from "acted, then failed"), and the allow-half proves
the backup really ran BEFORE the installer and that its name was recorded.
"""
import json
import os
import sqlite3

import pytest

from commercial_runtime.updates.descriptor import UpdateError
from commercial_runtime.updates.tests.test_service import Rig


def test_backup_is_taken_before_the_installer_launches_and_its_name_is_recorded(tmp_path):
    order = []

    def backup():
        order.append("backup")
        return "aura-retail-backup-20261012T000000Z-app1.0.0-schema1-pre-update.aurabak.zip"

    rig = Rig(tmp_path, pre_install_backup=backup)
    original_launch = rig.service._launch
    rig.service._launch = lambda path, args: (order.append("launch"), original_launch(path, args))
    rig.to_ready()
    rig.service.install()
    assert order == ["backup", "launch"]
    state = json.loads((rig.updates_dir / "state.json").read_text(encoding="utf-8"))
    assert state["pre_update_backup"].endswith("-pre-update.aurabak.zip")


def test_install_is_refused_and_installer_not_launched_when_the_backup_fails(tmp_path):
    calls = []

    def backup():
        calls.append(1)
        raise OSError("disk full")

    rig = Rig(tmp_path, pre_install_backup=backup)
    rig.to_ready()
    with pytest.raises(UpdateError) as exc:
        rig.service.install()
    assert exc.value.code == "PRE_UPDATE_BACKUP_FAILED"
    assert calls == [1]                      # the backup WAS attempted...
    assert rig.launcher.calls == []          # ...and the installer never ran
    # Retryable: still ready, downloaded file kept.
    assert rig.service.status()["phase"] == "ready"
    assert rig.installer().exists()


def test_real_pre_update_backup_writes_a_tagged_file_that_retention_never_touches(tmp_path):
    from commercial_runtime.backup.schedule import make_pre_update_backup, prune_automatic, parse_backup_name
    app = tmp_path / "app"
    (app / "database" / "subsystems").mkdir(parents=True)
    for p in (app / "database" / "registry.db", app / "database" / "subsystems" / "retail.db"):
        c = sqlite3.connect(p)
        c.execute("create table t(x)")
        c.commit()
        c.close()
    name = make_pre_update_backup("retail", str(app), str(app / "backups"), "1.0.0")()
    assert parse_backup_name(name)[1] == "pre-update"
    assert prune_automatic(str(app / "backups"), "retail", keep_daily=0, keep_weekly=0) == []
    assert os.path.exists(app / "backups" / name)
