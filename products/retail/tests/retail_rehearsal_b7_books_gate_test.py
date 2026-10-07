"""REHEARSAL-1007 B7: the books (trial balance, P&L, balance sheet, cash flow) are gated on
the BOOKS-READ capability (retail.books.view), not on retail.reports.

THE DEFECT: user_accounts.py makes the books owner-only by default (a manager gets none
unless the owner gives it), yet the four GL-derived report routes were gated on
retail.reports, which managers hold -- so a manager could open the books.

BOTH DIRECTIONS: a manager is refused (403) on all four; a bookkeeper (an account holding
retail.books.view) and the owner get 200; and a manager still reads the SALES report.

Run (one pytest process per file):
    pytest products/retail/tests/retail_rehearsal_b7_books_gate_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_rehearsal_b7_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA), AURA_SITE_RELAY_ENABLED="0")
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

API = '/api/sub/retail'
BOOKS_URLS = (
    f'{API}/reports/trial-balance',
    f'{API}/reports/profit-and-loss?start_date=2026-01-01&end_date=2026-12-31',
    f'{API}/reports/balance-sheet?as_of_date=2026-12-31',
    f'{API}/reports/cash-flow?start_date=2026-01-01&end_date=2026-12-31',
)


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _login(role, company_id, grants=None):
    email = f'b7-{role}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'B7PW123456789'  # pragma: allowlist secret
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f'EMP-{uuid.uuid4().hex[:6]}', email, hash_password(password), role, 'active'))
    conn.execute("INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
                 (str(uuid.uuid4()), user_id, 'retail', 'full'))
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    for code, level in (grants or {}).items():
        conn.execute("UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                     (level, user_id, code))
    conn.commit(); conn.close()
    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return c


@pytest.fixture
def shop():
    return str(uuid.uuid4())


def test_a_manager_cannot_open_any_of_the_books(shop):
    mgr = _login('manager', shop)
    for url in BOOKS_URLS:
        r = mgr.get(url)
        assert r.status_code == 403, (url, r.status_code, r.get_json())


def test_a_bookkeeper_and_the_owner_can(shop):
    book = _login('cashier', shop, grants={'retail.books.view': 'full', 'retail.reports': 'full'})
    owner = _login('admin', shop)
    for who in (book, owner):
        for url in BOOKS_URLS:
            r = who.get(url)
            assert r.status_code == 200, (url, r.status_code, r.get_json())


def test_a_manager_still_reads_the_sales_reports(shop):
    mgr = _login('manager', shop)
    r = mgr.get(f'{API}/reports/summary')
    assert r.status_code == 200, r.get_json()
