"""Aura Retail -- the till's Favourites pill, server half (UI-2 item 1).

The owner pins up to 40 products to the till's first category pill. The list is
ONE retail_settings row (`pos_favourites`, a JSON list of product ids) written
through the existing owner-only POST /settings/credit -- no schema change.

What is pinned, and why each assertion can fail:
  * ALLOW: the owner can store a list; GET /settings/credit returns it; it
    round-trips in pin order; repeats are dropped.
  * CAP: 41 ids are refused with FAVOURITES_FULL and NOTHING is stored (the
    previous list survives) -- all-or-nothing, mutation-proved by raising the
    constant in the route.
  * DENY: a cashier is refused (OWNER_ONLY 403) and the stored list is untouched
    -- the "owner-curated" rule. Mutation: drop the guard and the cashier write
    lands.
  * SHAPE: junk (a dict, a list holding a number) is a 400, not a stored blob.
  * SYNC: the write queues a retail_setting outbox row, like every other key.

CRITICAL: exactly ONE pytest process per file (AUDIT-010).

Run:
    pytest products/retail/tests/retail_pos_favourites_test.py -q
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_favs_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _login(role):
    email = f'favs-{role}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'FavouritesPW1'
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, 'EMP-0001', email, hash_password(password), role, 'active'))
    if role != 'admin':
        conn.execute(
            "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
            (str(uuid.uuid4()), user_id, 'retail', 'full'))
        user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


@pytest.fixture
def owner():
    return _login('admin')


def _stored(c):
    r = c.get(f'{API}/settings/credit')
    assert r.status_code == 200, r.get_json()
    raw = (r.get_json()['data'] or {}).get('pos_favourites')
    return json.loads(raw) if raw else []


def test_owner_can_store_and_read_back_in_pin_order(owner):
    ids = [str(uuid.uuid4()) for _ in range(5)]
    r = owner.post(f'{API}/settings/credit', json={'pos_favourites': ids})
    assert r.status_code == 200, r.get_json()
    assert _stored(owner) == ids


def test_repeats_are_dropped_but_order_kept(owner):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    r = owner.post(f'{API}/settings/credit', json={'pos_favourites': [a, b, a]})
    assert r.status_code == 200, r.get_json()
    assert _stored(owner) == [a, b]


def test_exactly_forty_is_accepted_forty_one_is_refused_and_changes_nothing(owner):
    forty = [str(uuid.uuid4()) for _ in range(40)]
    assert owner.post(f'{API}/settings/credit', json={'pos_favourites': forty}).status_code == 200
    r = owner.post(f'{API}/settings/credit', json={'pos_favourites': forty + [str(uuid.uuid4())]})
    assert r.status_code == 400
    body = r.get_json()
    assert body['code'] == 'FAVOURITES_FULL' and '40' in body['message']
    assert _stored(owner) == forty, 'a refused write must leave the stored list alone'


def test_junk_shapes_are_refused(owner):
    for junk in ({'a': 1}, [1, 2], ['ok', ''], 'not json', [['nested']]):
        r = owner.post(f'{API}/settings/credit', json={'pos_favourites': junk})
        assert r.status_code == 400, (junk, r.get_json())
        assert r.get_json()['code'] == 'FAVOURITES_INVALID'


def test_a_cashier_cannot_curate_the_list(owner):
    cashier = _login('cashier')
    r = cashier.post(f'{API}/settings/credit', json={'pos_favourites': [str(uuid.uuid4())]})
    assert r.status_code == 403, r.get_json()
    assert _stored(cashier) == [], 'the refused write must not have stored anything'
    # ...but a cashier can READ the owner's list (the till shows the pill to everyone)
    ids = [str(uuid.uuid4())]
    assert owner.post(f'{API}/settings/credit', json={'pos_favourites': ids}).status_code == 200


def test_write_queues_a_sync_row(owner):
    ids = [str(uuid.uuid4())]
    conn = get_retail_conn()
    before = conn.execute("SELECT COUNT(*) FROM sync_outbox WHERE entity_type='retail_setting'").fetchone()[0]
    conn.close()
    assert owner.post(f'{API}/settings/credit', json={'pos_favourites': ids}).status_code == 200
    conn = get_retail_conn()
    rows = conn.execute("SELECT payload FROM sync_outbox WHERE entity_type='retail_setting' ORDER BY rowid").fetchall()
    conn.close()
    assert len(rows) == before + 1
    last = json.loads(rows[-1]['payload'])
    assert last['skey'] == 'pos_favourites' and json.loads(last['svalue']) == ids
