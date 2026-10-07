"""REHEARSAL-1007 B8 + B9.

B8: on a brand-new shop the first branch the owner ADDED silently became the main branch
(no "Main Branch" existed, and every route takes the lowest-id active branch). Creating the
first branch now makes the standard "Main Branch" first. An existing shop is never touched.

B9: POST /cash-sessions/open {"opening_float": "abc"} answered 200 with 0.000 because
`_money` swallows every parse failure. A non-number, NaN/Infinity or a negative is now a 400
with a code; an absent float and a real amount still open the drawer.

Run (one pytest process per file):
    pytest products/retail/tests/retail_rehearsal_b8_b9_branch_and_float_test.py -q
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_rehearsal_b8_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA), AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    company_id = str(uuid.uuid4())
    email = f'b8-{uuid.uuid4().hex[:8]}@test.local'
    password = 'B8PW123456789'  # pragma: allowlist secret
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'))
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.test_company_id = company_id
    return c


def _branches(cid):
    conn = get_retail_conn()
    try:
        return [(r['id'], r['name']) for r in conn.execute(
            "SELECT id, name FROM branches WHERE company_id=? ORDER BY id", (cid,))]
    finally:
        conn.close()


def test_first_added_branch_on_a_new_shop_is_not_the_main_branch(client):
    cid = client.test_company_id
    assert _branches(cid) == []
    r = client.post(f'{API}/branches', json={'name': 'Downtown'})
    assert r.status_code == 200, r.get_json()
    names = [n for _, n in _branches(cid)]
    assert names == ['Main Branch', 'Downtown'], names          # Main Branch is the lowest id
    conn = get_retail_conn()
    try:
        # and it is what an unpinned till resolves to
        import api.retail_api as ra
        assert ra._default_branch(conn, cid) == _branches(cid)[0][0]
    finally:
        conn.close()


def test_a_shop_that_already_has_a_branch_is_left_alone(client):
    cid = client.test_company_id
    conn = get_retail_conn()
    conn.execute("INSERT INTO branches (company_id,name,uid) VALUES (?,?,?)", (cid, 'Old Shop', str(uuid.uuid4())))
    conn.commit(); conn.close()
    assert client.post(f'{API}/branches', json={'name': 'Second'}).status_code == 200
    assert [n for _, n in _branches(cid)] == ['Old Shop', 'Second']


@pytest.mark.parametrize('bad', ['abc', 'NaN', 'Infinity', '-5', -1, '1e999x', True])
def test_a_bad_opening_float_is_refused_with_a_code(client, bad):
    r = client.post(f'{API}/cash-sessions/open', json={'opening_float': bad})
    assert r.status_code == 400, (bad, r.get_json())
    assert r.get_json()['reason_code'] == 'INVALID_OPENING_FLOAT'


def test_a_real_or_absent_opening_float_still_opens_the_drawer(client):
    ok = client.post(f'{API}/cash-sessions/open', json={'opening_float': '50.5'})
    assert ok.status_code == 200, ok.get_json()
    conn = get_retail_conn()
    try:
        assert conn.execute("SELECT opening_float FROM cash_sessions ORDER BY rowid DESC LIMIT 1").fetchone()[0] == 50.5
    finally:
        conn.close()
