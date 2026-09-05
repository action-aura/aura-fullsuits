"""Aura Retail -- the EMIT side of shop-level settings sync (2026-09-05).

One licence, two devices, and the phone showed "$" while the desktop showed
"JD": `retail_settings` (company_id, skey, svalue) was never a synced entity,
so currency, tax mode, credit defaults, business day and branding text were
per DEVICE when every one of them describes the SHOP.
`retail_api.py::_queue_setting_sync_event` is the fix; this file proves it
actually queues the right `sync_outbox` row from the REAL routes, not from a
hand-built payload -- commercial_runtime/sync/tests/
test_retail_setting_sync_apply.py covers the APPLY side of the same wire
shape.

Run:
    pytest products/retail/tests/retail_settings_sync_emit_test.py -v
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_settings_sync_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from api import retail_api as _retail_api  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'
OWNER_EMAIL = 'settings-sync-owner@test.local'
OWNER_PASSWORD = 'SettingsSyncPW1'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _owner():
    """The one admin this install allows -- same shape as
    retail_employee_management_test.py's own `_owner()`."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': OWNER_EMAIL, 'password': OWNER_PASSWORD,
    })
    if r.status_code == 409:
        r = client.post('/api/auth/login', json={'email': OWNER_EMAIL, 'password': OWNER_PASSWORD})
    assert r.status_code == 200, r.get_json()
    return client


def _outbox():
    """Every `sync_outbox` row queued so far, oldest first -- read straight
    from the real retail DB the way sibling sync tests do."""
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT entity_type, entity_id, event_type, payload FROM sync_outbox ORDER BY created_at, rowid"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _retail_setting_rows(skey=None):
    """Only the `retail_setting` outbox rows, decoded, optionally filtered
    to one skey -- what every test below actually asserts against."""
    rows = []
    for r in _outbox():
        if r['entity_type'] != 'retail_setting':
            continue
        payload = json.loads(r['payload'])
        if skey is not None and payload.get('skey') != skey:
            continue
        rows.append({'entity_id': r['entity_id'], 'event_type': r['event_type'], 'payload': payload})
    return rows


def _expected_entity_id(skey):
    """The SAME uuid5 identity retail_api.py's `_retail_setting_entity_id`
    computes -- recomputed here from the published namespace constant so
    this test proves the actual wire value, not merely that the helper
    called itself consistently."""
    return str(uuid.uuid5(_retail_api._RETAIL_SETTING_SYNC_NAMESPACE, f"retail_setting:{skey}"))


# All tests below share ONE owner/company (module-level `OWNER_EMAIL`,
# matching retail_employee_management_test.py's convention), so
# `sync_outbox` -- which carries no company_id column at all -- accumulates
# rows across every test function that runs before a given one. Every
# assertion therefore snapshots `_retail_setting_rows(skey)` BEFORE its own
# action and asserts on the NEWLY APPENDED slice, never on an absolute
# count: `_outbox()` is ordered by `created_at, rowid`, so a skey's own new
# rows always land at the end of that skey's own filtered list.

# ── 1/2. credit settings (base_currency) -- stable identity, last write wins ─

def test_credit_settings_post_queues_one_retail_setting_row():
    owner = _owner()
    before = _retail_setting_rows('base_currency')
    r = owner.post(f'{API}/settings/credit', json={'base_currency': 'USD'})
    assert r.status_code == 200, r.get_json()

    new_rows = _retail_setting_rows('base_currency')[len(before):]
    assert len(new_rows) == 1
    row = new_rows[0]
    assert row['event_type'] == 'update'
    assert row['payload'] == {'skey': 'base_currency', 'svalue': 'USD'}
    assert row['entity_id'] == _expected_entity_id('base_currency')


def test_second_credit_settings_post_reuses_the_same_stable_entity_id():
    owner = _owner()
    before = _retail_setting_rows('base_currency')
    owner.post(f'{API}/settings/credit', json={'base_currency': 'USD'})
    r = owner.post(f'{API}/settings/credit', json={'base_currency': 'JOD'})
    assert r.status_code == 200, r.get_json()

    new_rows = _retail_setting_rows('base_currency')[len(before):]
    assert len(new_rows) == 2, 'both writes must each queue their own outbox row'
    assert new_rows[0]['entity_id'] == new_rows[1]['entity_id'] == _expected_entity_id('base_currency'), (
        'a stable per-key entity_id is the whole point -- Owner must see one '
        'identity per SETTING, not one per write'
    )
    assert new_rows[1]['payload']['svalue'] == 'JOD'


# ── 3. tax settings ──────────────────────────────────────────────────────────

def test_tax_settings_post_queues_a_retail_setting_row():
    owner = _owner()
    before = _retail_setting_rows('tax_calculation_mode')
    r = owner.post(f'{API}/settings/tax', json={'tax_calculation_mode': 'before_discount'})
    assert r.status_code == 200, r.get_json()

    new_rows = _retail_setting_rows('tax_calculation_mode')[len(before):]
    assert len(new_rows) == 1
    assert new_rows[0]['event_type'] == 'update'
    assert new_rows[0]['payload']['svalue'] == 'before_discount'


# ── 4. business-day settings -- update then delete/clear ───────────────────

def test_business_day_post_then_clear_queues_update_then_delete():
    owner = _owner()
    before = _retail_setting_rows('business_day_start_hour')
    r = owner.post(f'{API}/settings/business-day', json={'business_day_start_hour': 6})
    assert r.status_code == 200, r.get_json()

    r = owner.post(f'{API}/settings/business-day', json={'business_day_start_hour': None})
    assert r.status_code == 200, r.get_json()

    new_rows = _retail_setting_rows('business_day_start_hour')[len(before):]
    assert len(new_rows) == 2, 'the set and the clear must each queue their own row'
    assert new_rows[0]['event_type'] == 'update'
    assert new_rows[0]['payload']['svalue'] == '6'
    assert new_rows[1]['event_type'] == 'delete'
    assert new_rows[1]['payload']['svalue'] is None, (
        'a cleared setting must travel as svalue=None, matching '
        '_queue_setting_sync_event\'s delete-vs-update split'
    )


# ── 5. branding text queues; the logo blob never does ───────────────────────

def test_branding_text_post_queues_a_retail_setting_row():
    owner = _owner()
    before = _retail_setting_rows('branding_business_name')
    r = owner.post(f'{API}/settings/branding', json={'branding_business_name': 'Rehearsal Shop'})
    assert r.status_code == 200, r.get_json()

    new_rows = _retail_setting_rows('branding_business_name')[len(before):]
    assert len(new_rows) == 1
    assert new_rows[0]['event_type'] == 'update'
    assert new_rows[0]['payload']['svalue'] == 'Rehearsal Shop'


def test_branding_logo_post_never_queues_a_blob_prefixed_outbox_row():
    """The logo route accepts any value matching _LOGO_DATA_URI_RE -- there
    is no real-image decode -- so a minimal valid data URI (3 zero bytes'
    worth of base64) exercises the real route cheaply, no image library
    needed."""
    owner = _owner()
    r = owner.post(f'{API}/settings/branding/logo', json={'logo': 'data:image/png;base64,AAAA'})
    assert r.status_code == 200, r.get_json()

    blobby = [row for row in _retail_setting_rows()
              if row['payload'].get('skey', '').startswith(_retail_api._SETTINGS_BLOB_PREFIX)]
    assert blobby == [], (
        'a kilobyte logo must never reach sync_outbox -- refused by '
        '_queue_setting_sync_event\'s own blob-prefix check'
    )
