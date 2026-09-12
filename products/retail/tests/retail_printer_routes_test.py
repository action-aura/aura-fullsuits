"""
Aura Retail -- hardware (ESC/POS) receipt printer routes.

`core/retail/escpos_receipt.py` and `escpos_transport.py` (commit f606739)
render a sale to ESC/POS bytes and can send them to a Windows printer through
the print spooler in RAW mode -- the only path that reaches the cash-drawer
kick byte sequence (the existing `_printReceipt` HTML/spooler route cannot;
see escpos_receipt.py's own module docstring). This file proves the two
routes that first wire those two modules to anything at all:

    GET  /api/sub/retail/printer/devices  -- list installed Windows printers
    POST /api/sub/retail/printer/test     -- render + send/write a sample receipt

The SALE PATH is untouched by this work and carries no route of its own here.

Self-contained bootstrap, matching retail_device_branch_pin_test.py /
retail_route_capability_matrix_test.py (no shared conftest.py exists here).

Run:
    pytest products/retail/tests/retail_printer_routes_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_printer_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
# Pure byte layer -- no ctypes, safe to import at module scope on every
# platform (including CI's Linux collection run). Only used here for
# `drawer_kick()`'s canonical bytes and to monkeypatch `render_receipt` for
# the mutation proof below -- `escpos_transport` (the ctypes/Windows-only
# half) is never imported directly by this file; the routes under test
# import it locally themselves, exactly as their own docstrings explain.
from core.retail import escpos_receipt


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _receipt_test_file():
    return DATA / 'receipt-test.bin'


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_device_branch_pin_test.py's own docstring for why nothing here
#    is shared via a conftest.py) ────────────────────────────────────────────

def _make_user(role):
    """A real account with the legacy subsystem grant plus real capability
    rows, seeded through the SAME production helper account creation uses --
    matches retail_route_capability_matrix_test.py's `_make_user`."""
    email = f"printer-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "PrinterRoutesPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    client.get('/api/sub/retail/settings/tax')  # forces the lazy schema helpers, same as its sibling files
    return client, company_id


@pytest.fixture(scope='module')
def admin():
    client, company_id = _make_user('admin')
    return client, company_id


@pytest.fixture(autouse=True)
def _clean_test_file():
    """Every test in this module shares the one AURA_APP_DATA directory for
    the whole process (matches retail_device_branch_pin_test.py's identical
    device-pin fixture reasoning) -- clear any file a previous test left
    behind so a later test's "the file has exactly N bytes" assertion can
    never pass because an EARLIER run's file happened to still be there."""
    path = _receipt_test_file()
    if path.exists():
        path.unlink()
    yield
    if path.exists():
        path.unlink()


# ── GET /printer/devices ──────────────────────────────────────────────────────

def test_devices_route_returns_a_list(admin):
    client, _ = admin
    r = client.get('/api/sub/retail/printer/devices')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert isinstance(data['printers'], list)
    # This suite runs on a real Windows dev machine (per the task's own
    # environment) -- on win32 the list must be genuinely non-empty (an
    # empty list here would be indistinguishable from "the EnumPrinters
    # call silently returned nothing", the exact "asserts an outcome where
    # it should assert the check ran" shape). On any other platform the
    # route degrades to an empty list plus a reason instead of raising.
    if sys.platform == 'win32':
        assert data['platform'] == 'win32'
        assert data['reason'] is None
        assert len(data['printers']) > 0, data
    else:
        assert data['platform'] == 'other'
        assert data['reason']


def test_devices_route_refuses_an_unauthenticated_caller():
    client = app.test_client()
    r = client.get('/api/sub/retail/printer/devices')
    assert r.status_code == 401, r.get_json()


def test_devices_route_refuses_a_user_without_the_capability():
    """A cashier -- no retail.employees -- gets the same 403 the neighbouring
    settings routes give a manager in retail_route_capability_matrix_test.py's
    test_a_manager_may_not_administer_the_shop."""
    cashier, _ = _make_user('cashier')
    r = cashier.get('/api/sub/retail/printer/devices')
    assert r.status_code == 403, r.get_json()


# ── POST /printer/test ────────────────────────────────────────────────────────

def test_test_route_refuses_an_unauthenticated_caller():
    client = app.test_client()
    r = client.post('/api/sub/retail/printer/test', json={'printer': None, 'kick': False})
    assert r.status_code == 401, r.get_json()


def test_test_route_refuses_a_user_without_the_capability():
    cashier, _ = _make_user('cashier')
    r = cashier.post('/api/sub/retail/printer/test', json={'printer': None, 'kick': False})
    assert r.status_code == 403, r.get_json()
    # And it really did not write the file -- a gate that returns 403 after
    # acting anyway is not a gate (same discipline as the stock-adjust
    # on-hand re-read in the capability matrix suite).
    assert not _receipt_test_file().exists()


def test_no_printer_writes_a_file_with_the_reported_byte_count(admin):
    client, _ = admin
    r = client.post('/api/sub/retail/printer/test', json={'printer': None, 'kick': False, 'paper_width': 42})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['sent'] is False
    assert d['bytes'] > 0
    out_path = Path(d['file'])
    assert out_path.exists(), d
    assert out_path.stat().st_size == d['bytes']


def test_no_printer_also_accepts_the_empty_string(admin):
    """The frontend sends `printer: null` for "not set", but an empty string
    from a stale/blank <select> must degrade the same way, not attempt to
    open a printer literally named ''."""
    client, _ = admin
    r = client.post('/api/sub/retail/printer/test', json={'printer': '', 'kick': False})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['sent'] is False


def test_sample_receipt_carries_currency_at_jod_precision(admin):
    """A fresh company's base_currency defaults to JOD (pricing.py's
    DEFAULT_BASE_CURRENCY), which is THREE decimal places (1000 fils), not
    two -- the exact bug class escpos_receipt.py's own docstring documents
    having already reopened once on a different channel. Asserted on the
    real bytes, not on a mock of format_money."""
    client, _ = admin
    r = client.post('/api/sub/retail/printer/test', json={'printer': None, 'kick': False})
    assert r.status_code == 200, r.get_json()
    payload = Path(r.get_json()['data']['file']).read_bytes()
    assert b'13.000' in payload, payload  # the sample sale's total, 5+8=13, at 3dp
    assert b'JD' in payload, payload      # JOD's own symbol (money_format.format_money)


def test_kick_false_produces_no_drawer_kick_bytes(admin):
    client, _ = admin
    r = client.post('/api/sub/retail/printer/test', json={'printer': None, 'kick': False})
    assert r.status_code == 200, r.get_json()
    payload = Path(r.get_json()['data']['file']).read_bytes()
    assert b'\x1b\x70' not in payload, payload


def test_kick_true_ends_with_drawer_kick_bytes(admin):
    client, _ = admin
    r = client.post('/api/sub/retail/printer/test', json={'printer': None, 'kick': True})
    assert r.status_code == 200, r.get_json()
    payload = Path(r.get_json()['data']['file']).read_bytes()
    assert payload.endswith(escpos_receipt.drawer_kick()), payload


def test_unknown_printer_name_returns_a_readable_error_not_a_traceback(admin):
    client, _ = admin
    r = client.post('/api/sub/retail/printer/test', json={
        'printer': f'Nonexistent Printer {uuid.uuid4().hex[:8]}', 'kick': False,
    })
    body = r.get_json()
    assert r.status_code in (400, 500), body
    assert body['status'] == 'error'
    assert 'Traceback' not in (body.get('message') or '')
