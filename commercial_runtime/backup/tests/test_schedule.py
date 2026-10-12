"""BACKUP-1: scheduling, retention, second copy, status.

Every deny test is paired with its allow half, and the scheduler tests count
create_backup calls instead of trusting an outcome.
"""
import json
import os
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from commercial_runtime.backup import schedule as sch
from commercial_runtime.backup.service import BackupError


def _make_app(tmp_path):
    app = tmp_path / "app"
    (app / "database" / "subsystems").mkdir(parents=True)
    for p in (app / "database" / "registry.db", app / "database" / "subsystems" / "retail.db"):
        c = sqlite3.connect(p)
        c.execute("create table t(x)")
        c.execute("insert into t values (1)")
        c.commit()
        c.close()
    return str(app)


def _rig(tmp_path, **kw):
    app = _make_app(tmp_path)
    backup_dir = os.path.join(app, "backups")
    settings = sch.BackupSettings(app)
    status = sch.BackupStatus(app)
    clock = {"now": datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc)}
    scheduler = sch.BackupScheduler("retail", app, backup_dir, "1.0.0", settings=settings, status=status,
                                    clock=lambda: clock["now"], **kw)
    return app, backup_dir, settings, status, clock, scheduler


@pytest.fixture
def counted(monkeypatch):
    calls = []
    real = sch.create_backup

    def wrapper(*a, **k):
        calls.append(k.get("reason"))
        return real(*a, **k)

    monkeypatch.setattr(sch, "create_backup", wrapper)
    return calls


# -- schedule ---------------------------------------------------------------

def test_default_schedule_is_daily_even_with_no_or_corrupt_settings(tmp_path):
    app = _make_app(tmp_path)
    assert sch.BackupSettings(app).load()["backup_schedule"] == "daily"
    os.makedirs(os.path.join(app, "backup_state"))
    with open(os.path.join(app, "backup_state", "settings.json"), "w") as f:
        f.write("{not json")
    assert sch.BackupSettings(app).load()["backup_schedule"] == "daily"


def test_runs_once_per_day_then_again_the_next_day(tmp_path, counted):
    _app, _bd, _s, _st, clock, scheduler = _rig(tmp_path)
    assert scheduler.tick() is not None
    clock["now"] += timedelta(hours=3)          # same day, later
    assert scheduler.tick() is None
    clock["now"] = datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc)
    assert scheduler.tick() is not None
    assert counted == ["auto", "auto"]          # exactly two create_backup calls


def test_off_schedule_never_runs(tmp_path, counted):
    _app, _bd, settings, _st, _clock, scheduler = _rig(tmp_path)
    settings.save(schedule="off")
    assert scheduler.tick() is None
    assert counted == []
    settings.save(schedule="daily")             # allow half
    assert scheduler.tick() is not None
    assert counted == ["auto"]


def test_business_day_key_decides_the_day(tmp_path, counted):
    # A shop whose day starts at 04:00 (UTC here): 01:00 on the 13th is still the 12th.
    def key(dt):
        return (dt - timedelta(hours=4)).date().isoformat()

    _app, _bd, _s, _st, clock, scheduler = _rig(tmp_path, day_key_fn=key)
    clock["now"] = datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)
    scheduler.tick()
    clock["now"] = datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc)   # new calendar day, same business day
    assert scheduler.tick() is None
    clock["now"] = datetime(2026, 10, 13, 5, 0, tzinfo=timezone.utc)   # business day changed
    assert scheduler.tick() is not None
    assert counted == ["auto", "auto"]


def test_failed_attempt_waits_an_hour_before_retrying(tmp_path, monkeypatch):
    _app, _bd, _s, status, clock, scheduler = _rig(tmp_path)
    attempts = []

    def failing(*a, **k):
        attempts.append(1)
        raise BackupError("disk full")

    monkeypatch.setattr(sch, "create_backup", failing)
    scheduler.tick()
    assert status.load()["last_auto"]["ok"] is False
    assert attempts == [1]
    clock["now"] += timedelta(minutes=30)
    scheduler.tick()
    assert attempts == [1]                      # still waiting
    clock["now"] += timedelta(minutes=45)
    scheduler.tick()
    assert attempts == [1, 1]                   # allowed again


# -- retention --------------------------------------------------------------

def _touch(directory, stamp, reason=None, product="retail"):
    tag = f"-{reason}" if reason else ""
    name = f"aura-{product}-backup-{stamp}-app1.0.0-schema1{tag}.aurabak.zip"
    with open(os.path.join(directory, name), "wb") as f:
        f.write(b"x")
    return name


def test_retention_keeps_7_daily_and_4_weekly_and_never_deletes_manual(tmp_path):
    d = str(tmp_path)
    manual = [_touch(d, f"2026{m}T120000Z") for m in ("0101", "0301", "0501", "0701", "0901", "1001")]
    pre = _touch(d, "20260601T120000Z", "pre-update")
    other_product = _touch(d, "20260102T120000Z", "auto", product="clinic")
    unrelated = "notes.zip"
    with open(os.path.join(d, unrelated), "wb") as f:
        f.write(b"x")
    autos = {}
    day = datetime(2026, 10, 12, tzinfo=timezone.utc)
    for i in range(0, 70):                       # 70 days of one auto backup per day
        s = (day - timedelta(days=i)).strftime("%Y%m%dT030000Z")
        autos[i] = _touch(d, s, "auto")
    deleted = sch.prune_automatic(d, "retail")
    left = set(os.listdir(d))
    for name in manual + [pre, other_product, unrelated]:
        assert name in left, name
    for i in range(7):                           # the 7 newest days survive
        assert autos[i] in left
    survivors = [i for i in range(7, 70) if autos[i] in left]
    assert len(survivors) == 4                   # beyond the daily window: 4 weekly
    weeks = {(day - timedelta(days=i)).isocalendar()[:2] for i in survivors}
    assert len(weeks) == 4
    assert len(deleted) == 70 - 7 - 4


def test_retention_collapses_same_day_extras_but_only_auto(tmp_path):
    d = str(tmp_path)
    a1 = _touch(d, "20261012T010000Z", "auto")
    a2 = _touch(d, "20261012T020000Z", "auto")
    m = _touch(d, "20261012T015959Z")
    sch.prune_automatic(d, "retail")
    left = set(os.listdir(d))
    assert a2 in left and a1 not in left and m in left


# -- second copy ------------------------------------------------------------

def test_secondary_copy_is_made_and_the_zip_is_a_real_backup(tmp_path):
    _app, _bd, settings, status, _clock, scheduler = _rig(tmp_path)
    usb = tmp_path / "usb"
    usb.mkdir()
    settings.save(secondary_dir=str(usb))
    scheduler.tick()
    s = status.load()
    assert s["last_secondary"]["ok"] is True and s["last_secondary"]["outcome"] == "copied"
    copies = list(usb.iterdir())
    assert len(copies) == 1 and copies[0].name == s["last_auto"]["filename"]
    with zipfile.ZipFile(copies[0]) as z:
        assert z.testzip() is None and "retail.db" in z.namelist()


def test_missing_secondary_folder_is_recorded_not_fatal_and_not_created(tmp_path):
    _app, backup_dir, settings, status, _clock, scheduler = _rig(tmp_path)
    gone = tmp_path / "unplugged"
    settings.save(secondary_dir=str(gone))
    scheduler.tick()
    s = status.load()
    assert s["last_auto"]["ok"] is True                      # the backup itself succeeded
    assert os.listdir(backup_dir)                             # and is on disk
    assert s["last_secondary"]["ok"] is False and s["last_secondary"]["reason"] == "folder_missing"
    assert not gone.exists()


def test_unsafe_secondary_dir_is_refused_on_save_and_skipped_at_run_time(tmp_path):
    app, _bd, settings, status, _clock, scheduler = _rig(tmp_path)
    bad = "C:\\Program Files\\Aura\\copies"
    with pytest.raises(BackupError):
        settings.save(secondary_dir=bad)
    # A hand-edited settings file must not bypass the guard either.
    os.makedirs(os.path.join(app, "backup_state"), exist_ok=True)
    with open(os.path.join(app, "backup_state", "settings.json"), "w") as f:
        json.dump({"backup_schedule": "daily", "backup_dir_secondary": bad}, f)
    scheduler.tick()
    assert status.load()["last_secondary"]["reason"] == "unsafe_folder"


# -- health -----------------------------------------------------------------

def test_warning_after_48_hours_without_any_backup(tmp_path):
    _app, backup_dir, settings, status, clock, scheduler = _rig(tmp_path)
    now = clock["now"]
    assert sch.build_status("retail", backup_dir, settings, status, now)["warning"] is False   # brand-new install
    assert sch.build_status("retail", backup_dir, settings, status, now + timedelta(hours=49))["warning"] is True
    scheduler.tick()
    fresh = sch.build_status("retail", backup_dir, settings, status, now + timedelta(hours=1))
    assert fresh["warning"] is False and fresh["last_auto"]["ok"] is True
