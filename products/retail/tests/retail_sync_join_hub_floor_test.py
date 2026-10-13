"""SYNC-JOIN stop-gap (2026-10-13): the shop hub's hourly housekeeping must
never delete a site_sync_events row until new devices can bootstrap.

Why: owner/tests/test_sync_late_join_after_prune_cross_stack.py measured, on
three real retail processes against a real Owner, that pruning by "every
device that exists has acked it" loses the shop's history for a device that
joins later (15 events -> 1; the late joiner got 0 of 2 products). The hub's
own sweep prunes by the same rule (commercial_runtime/sync/site_relay/
pruning.py), so app.py now hands it `_hub_housekeeping_floors`, whose leading
0 holds the watermark at 0. Replay nonces still expire (that half of the
sweep is untouched).

Both directions on the REAL watermark function: with the hub's floors the
watermark is 0 however far the hub's own streams have consumed (deny prune);
the existing floors alone would allow it (shows the 0 is what holds it).
"""
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix='aura_syncjoin_hub_'))
(DATA / 'database' / 'subsystems').mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE='1', AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
                  AURA_SITE_RELAY_ENABLED='0', AURA_BACKUP_SCHEDULER='0')
os.environ.pop('AURA_DEV', None)

import app as _app_module  # noqa: E402
from commercial_runtime.sync.site_relay import pruning  # noqa: E402
from database import schema as retail_schema  # noqa: E402

retail_schema.init_retail()


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _conn_with_consumed_log(n_events=5):
    """A site log of `n_events` rows that every reader the old rule knew
    about (forwarder; no paired devices) has consumed in full."""
    c = sqlite3.connect(retail_schema._get_path('retail'))
    c.row_factory = sqlite3.Row
    c.execute('DELETE FROM site_sync_events')
    c.execute("DELETE FROM sqlite_sequence WHERE name = 'site_sync_events'")
    c.execute('DELETE FROM site_device_cursors')
    c.execute('DELETE FROM site_paired_devices')
    cols = [r[1] for r in c.execute('PRAGMA table_info(site_sync_events)')]
    for i in range(n_events):
        row = {}
        for col in cols:
            if col == 'seq':
                continue
            row[col] = f'x{i}' if col not in ('created_at', 'received_at') else '2026-10-13T00:00:00+00:00'
        c.execute(f"INSERT INTO site_sync_events ({','.join(row)}) VALUES ({','.join('?' * len(row))})",
                  list(row.values()))
    c.execute('UPDATE site_forward_cursor SET forwarded_to_seq = ? WHERE id = 1', (n_events,))
    c.commit()
    return c


def test_hub_floors_hold_the_watermark_at_zero(monkeypatch):
    monkeypatch.setattr(_app_module, '_hub_own_consumer_floors', lambda: [10_000, 10_000])
    floors = _app_module._hub_housekeeping_floors()
    assert floors[0] == 0
    c = _conn_with_consumed_log()
    try:
        assert pruning.safe_prune_watermark(c, extra_floors=floors) == 0
        assert pruning.prune_site_log(c, extra_floors=floors) == 0
        assert c.execute('SELECT COUNT(*) FROM site_sync_events').fetchone()[0] == 5
    finally:
        c.close()


def test_without_the_zero_the_same_log_would_be_pruned(monkeypatch):
    # Control: the hub's own floors alone allow the prune, so the test above
    # is held by the 0, not by the fixture.
    c = _conn_with_consumed_log()
    try:
        assert pruning.safe_prune_watermark(c, extra_floors=[10_000, 10_000]) == 5
    finally:
        c.close()
