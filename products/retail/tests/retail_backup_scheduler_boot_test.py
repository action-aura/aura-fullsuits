"""The daily backup timer is armed by a normal desktop boot, and only
AURA_BACKUP_SCHEDULER=0 stops it.

Why this file exists (2026-10-12): run_all_tests.py now defaults
AURA_BACKUP_SCHEDULER=0 for every child, so that in-process test apps stop
carrying a live backup timer (it broke a process-level thread check in
retail_einvoicing_regression_test.py). A kill switch that every test turns on
is exactly the shape that hides "the feature never starts in production" --
so this file proves BOTH directions on a real init_app():

  * allow: the variable unset (a shop's normal boot) -> the named timer thread
    is alive after init_app();
  * deny:  AURA_BACKUP_SCHEDULER=0 -> no such thread.

Each direction runs in its own subprocess: app.py builds its scheduler at
import time, so one process can only ever show one boot.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / 'backend'
SUITE_ROOT = BACKEND_DIR.parents[2]

_PROBE = r"""
import os, sys, threading
sys.path[:0] = [{suite!r}, {backend!r}]
import app as _app_module
_app_module.init_app()
from commercial_runtime.backup.schedule import THREAD_NAME
alive = any(t.name == THREAD_NAME and t.is_alive() for t in threading.enumerate())
print('BACKUP_TIMER_ALIVE=' + ('1' if alive else '0'))
"""


def _boot(scheduler_env):
    data = Path(tempfile.mkdtemp(prefix='aura_backup_boot_'))
    (data / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for k in ('AURA_OWNER_LICENSING_URL', 'AURA_SYNC_RELAY_URL', 'AURA_DEV',
              'AURA_RETAIL_DEMO_MODE', 'AURA_BACKUP_SCHEDULER'):
        env.pop(k, None)
    env.update(AURA_STANDALONE='1', AURA_BUNDLE_DIR=str(BACKEND_DIR),
               AURA_APP_DATA=str(data), AURA_SITE_RELAY_ENABLED='0')
    if scheduler_env is not None:
        env['AURA_BACKUP_SCHEDULER'] = scheduler_env
    code = _PROBE.format(suite=str(SUITE_ROOT), backend=str(BACKEND_DIR))
    out = subprocess.run([sys.executable, '-c', code], env=env, cwd=str(BACKEND_DIR),
                         capture_output=True, text=True, timeout=300)
    lines = [l for l in out.stdout.splitlines() if l.startswith('BACKUP_TIMER_ALIVE=')]
    assert lines, f'probe did not report (exit {out.returncode}):\n{out.stdout[-2000:]}\n{out.stderr[-3000:]}'
    return lines[-1].endswith('1')


def test_a_normal_boot_arms_the_daily_backup_timer():
    assert _boot(None) is True, (
        'a normal desktop boot did not arm the backup timer -- automatic '
        'backups would silently never run for a shop')


def test_the_kill_switch_stops_it():
    assert _boot('0') is False, 'AURA_BACKUP_SCHEDULER=0 did not stop the backup timer'
