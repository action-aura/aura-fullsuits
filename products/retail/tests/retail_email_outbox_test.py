"""Aura Retail -- email outbox foundation (feat/email-outbox-foundation).

Covers the two retail-side trigger points wired to
commercial_runtime/notifications:

  1. The low-stock post-sale trigger (core/retail/reorder_hook.py's
     `_maybe_queue_low_stock_email`) -- a sale that opens a new pending
     `reorder_requests` row must ALSO queue a low_stock_alert email, but
     ONLY when notifications are configured+enabled for the company AND a
     recipient is set. An install that has done neither must see ZERO new
     email_outbox rows, ever -- same "invisible until configured" guarantee
     retail_reorder_sync_test.py already proves for reorder_requests itself.

  2. POST /api/sub/retail/reports/email -- queues a report_summary email
     using the same data /reports/summary already computes, only when
     notifications are enabled and a recipient (explicit or the company's
     configured default) is available.

Follows the same self-contained bootstrap convention as
retail_reorder_sync_test.py (no shared conftest.py exists for
products/retail/tests/) -- run this file on its own, not combined with
other self-booting-app test files in the same pytest invocation (see this
suite's existing module-caching limitation, reproducible today even between
two completely unrelated pre-existing files here, e.g.
retail_einvoicing_regression_test.py + retail_customer_sync_test.py):

    pytest products/retail/tests/retail_email_outbox_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_email_outbox_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# Inert by construction until a test explicitly monkeypatches AURA_SMTP_HOST
# in -- matching commercial_runtime/notifications/settings.py::is_enabled's
# own first gate.
os.environ.pop("AURA_SMTP_HOST", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.notifications import settings as notif_settings  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client(monkeypatch):
    """A fresh, logged-in Flask test client for its own newly-created
    company. AURA_SMTP_HOST is left UNSET by default (see module-level
    os.environ.pop above) -- individual tests opt into "configured" via
    monkeypatch.setenv, so a test that forgets to configure it gets the
    real inert behavior rather than silently inheriting state from an
    earlier test."""
    monkeypatch.delenv('AURA_SMTP_HOST', raising=False)

    email = f'emailoutbox-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'EmailOutboxPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()

    # doc_sequences is created lazily -- see retail_reorder_sync_test.py's
    # identical priming call for the full explanation.
    c.get('/api/sub/retail/settings/tax')

    import random as _random
    seed_conn = get_retail_conn()
    seed_conn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'po',?)",
        (company_id, _random.randint(1, 5_000_000)),
    )
    seed_conn.commit()
    seed_conn.close()

    c.test_company_id = company_id
    return c


@pytest.fixture
def db_conn():
    conn = get_retail_conn()
    yield conn
    conn.close()


def _make_product(client, *, reorder_level=5, reorder_method='whatsapp', initial_stock=10):
    resp = client.post('/api/sub/retail/products', json={
        'name': f'Email Outbox Widget {uuid.uuid4().hex[:6]}',
        'sku': f'EMAILOUT-{uuid.uuid4().hex[:8]}',
        'cost_price': 2, 'sell_price': 10.0,
        'reorder_level': reorder_level, 'reorder_method': reorder_method,
        'initial_stock': initial_stock,
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()['data']['id']


def _sell(client, product_id, qty):
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': product_id, 'quantity': qty}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _configure_notifications(company_id, *, low_stock_recipient=None, reports_recipient=None):
    conn = get_retail_conn()
    notif_settings.set_setting(conn, company_id, 'enabled', '1')
    if low_stock_recipient:
        notif_settings.set_setting(conn, company_id, 'low_stock_recipient', low_stock_recipient)
    if reports_recipient:
        notif_settings.set_setting(conn, company_id, 'reports_recipient', reports_recipient)
    conn.commit()
    conn.close()


def _email_outbox_rows(db_conn, company_id, email_type=None):
    q = "SELECT * FROM email_outbox WHERE company_id=?"
    params = [company_id]
    if email_type:
        q += " AND email_type=?"
        params.append(email_type)
    return [dict(r) for r in db_conn.execute(q, params).fetchall()]


# ── Low-stock trigger: inert by default ──────────────────────────────────

def test_low_stock_sale_queues_nothing_when_smtp_unconfigured(client, db_conn):
    """AURA_SMTP_HOST is unset for this test (the fixture's default) --
    even though the company sets low_stock_recipient, notifications.
    is_enabled() must be False, so no email_outbox row is ever created.
    This is the single most important inertness guarantee this feature
    has to hold."""
    _configure_notifications(client.test_company_id, low_stock_recipient='owner@shop.test')
    pid = _make_product(client, reorder_level=5, initial_stock=6)
    _sell(client, pid, 2)  # crosses the threshold -- reorder_requests DOES get a row

    reorder_rows = db_conn.execute("SELECT * FROM reorder_requests WHERE product_id=?", (pid,)).fetchall()
    assert len(reorder_rows) == 1, "the pre-existing reorder trigger must be completely unaffected"

    assert _email_outbox_rows(db_conn, client.test_company_id) == []


def test_low_stock_sale_queues_nothing_when_company_has_not_opted_in(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    conn = get_retail_conn()
    notif_settings.set_setting(conn, client.test_company_id, 'low_stock_recipient', 'owner@shop.test')
    # deliberately NOT setting 'enabled'
    conn.commit()
    conn.close()

    pid = _make_product(client, reorder_level=5, initial_stock=6)
    _sell(client, pid, 2)

    assert _email_outbox_rows(db_conn, client.test_company_id) == []


def test_low_stock_sale_queues_nothing_when_no_recipient_configured(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    _configure_notifications(client.test_company_id)  # enabled=1, no recipient

    pid = _make_product(client, reorder_level=5, initial_stock=6)
    _sell(client, pid, 2)

    assert _email_outbox_rows(db_conn, client.test_company_id) == []


# ── Low-stock trigger: fires when fully configured ───────────────────────

def test_low_stock_sale_queues_an_alert_email_when_fully_configured(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    _configure_notifications(client.test_company_id, low_stock_recipient='owner@shop.test')

    pid = _make_product(client, reorder_level=5, initial_stock=6)
    _sell(client, pid, 2)  # 6 - 2 = 4 <= 5

    rows = _email_outbox_rows(db_conn, client.test_company_id, email_type='low_stock_alert')
    assert len(rows) == 1
    assert rows[0]['recipient'] == 'owner@shop.test'
    assert rows[0]['status'] == 'QUEUED'
    assert 'Low stock alert' in rows[0]['subject']


def test_two_low_stock_sales_back_to_back_only_queue_one_email(client, db_conn, monkeypatch):
    """Reuses reorder_requests' own idempotency guard -- see
    reorder_hook.py's module docstring for why this needs no separate
    dedup mechanism of its own."""
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    _configure_notifications(client.test_company_id, low_stock_recipient='owner@shop.test')

    pid = _make_product(client, reorder_level=5, initial_stock=10)
    _sell(client, pid, 6)  # 10 - 6 = 4 <= 5 -> first (and only) request+email
    _sell(client, pid, 1)  # 4 - 1 = 3 <= 5 -> must NOT queue a second email

    rows = _email_outbox_rows(db_conn, client.test_company_id, email_type='low_stock_alert')
    assert len(rows) == 1


def test_sale_response_is_unaffected_by_email_queueing(client, monkeypatch):
    """The hook's existing 'never touch the sale's response' contract
    (retail_reorder_hook_regression_test.py) must hold even when the email
    branch is fully active, not just when it's a no-op."""
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    _configure_notifications(client.test_company_id, low_stock_recipient='owner@shop.test')

    pid = _make_product(client, reorder_level=5, initial_stock=6)
    data = _sell(client, pid, 2)

    # 'oversold_past_recorded_stock' added launch-readiness Phase 7 stage
    # 7d-iii (docs/launch-readiness/phase7-offline-ux.md "Correction to
    # Decision 1") -- a real, deliberate checkout-response contract change,
    # matching every other frozen SALE_RESPONSE_KEYS literal in this suite
    # (see retail_cash_drawer_test.py's own comment on its copy).
    #
    # 'einvoice' added 2026-09-08. E-invoicing now defaults ON
    # (commercial_runtime/einvoicing/settings.py DEFAULTS 'enabled': '1'),
    # so create_sale always reaches the enqueue block at retail_api.py:4816
    # and sets response_data['einvoice'] whenever enqueue_sale(...) returns
    # truthy -- inside a broad try/except that logs and moves on. The
    # coupling that creates is worth naming in an EMAIL test: from now on a
    # failed e-invoicing enqueue surfaces here as an email-outbox key-set
    # failure. This assertion's own subject is unchanged -- email queueing
    # adds nothing to the checkout response -- so the fix for a diff on this
    # line is upstream in e-invoicing, never a shorter list here.
    assert sorted(data.keys()) == sorted([
        'amount_paid', 'balance_due', 'calculation_version', 'change', 'currency',
        'customer_id', 'customer_name', 'discount_amount', 'einvoice', 'employee_name',
        'id', 'idempotency_key', 'lines',
        'oversold_past_recorded_stock',
        'points_redeemed', 'points_redeemed_amount',
    'sale_number', 'subtotal', 'tax_amount', 'total', 'warning',
    ])


# ── Reports email route ───────────────────────────────────────────────────

def test_reports_email_requires_a_recipient(client):
    r = client.post('/api/sub/retail/reports/email', json={})
    assert r.status_code == 400
    assert 'recipient' in r.get_json()['message'].lower()


def test_reports_email_requires_notifications_enabled(client, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    # recipient given explicitly, but company never called _configure_notifications
    r = client.post('/api/sub/retail/reports/email', json={'recipient': 'ops@shop.test'})
    assert r.status_code == 409


def test_reports_email_queues_a_report_summary_email_with_explicit_recipient(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    _configure_notifications(client.test_company_id)  # enabled, no default recipient needed

    r = client.post('/api/sub/retail/reports/email', json={'recipient': 'ops@shop.test', 'days': 7})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()['data']
    assert body['queued'] is True
    assert body['recipient'] == 'ops@shop.test'

    rows = _email_outbox_rows(db_conn, client.test_company_id, email_type='report_summary')
    assert len(rows) == 1
    assert rows[0]['recipient'] == 'ops@shop.test'
    assert '7-day summary report' in rows[0]['subject']
    assert 'Revenue' in rows[0]['body_text']


def test_reports_email_falls_back_to_configured_default_recipient(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    _configure_notifications(client.test_company_id, reports_recipient='default-ops@shop.test')

    r = client.post('/api/sub/retail/reports/email', json={})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['recipient'] == 'default-ops@shop.test'
