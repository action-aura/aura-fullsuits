"""Aura Retail -- WhatsApp report triggers (whatsapp-recipients feature).

Covers the three retail-side trigger points wired to
commercial_runtime/notifications' WhatsApp channel:

  1. The low-stock post-sale trigger (core/retail/reorder_hook.py, alongside
     the pre-existing email trigger) -- a sale that opens a new pending
     reorder_requests row must ALSO queue a whatsapp_outbox row for every
     recipient subscribed to 'low_stock_alert', but ONLY when the transport
     is configured, the company opted in, a template name is set, AND at
     least one recipient is subscribed. Same "invisible until configured"
     guarantee retail_email_outbox_test.py already proves for the email
     side of this exact same trigger.

  2. Shift close (POST .../cash-sessions/<id>/close) -- queues one
     shift_close_report row per subscribed recipient, best-effort, never
     affecting the close response itself.

  3. POST /api/sub/retail/reports/whatsapp -- on-demand daily_sales_summary
     / ar_overdue_alert, mirrors POST /reports/email's contract.

Follows the same self-contained bootstrap convention as
retail_email_outbox_test.py (no shared conftest.py exists for
products/retail/tests/) -- run this file on its own:

    pytest products/retail/tests/retail_whatsapp_outbox_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_whatsapp_outbox_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
# Inert by construction until a test explicitly monkeypatches this in --
# matching commercial_runtime/notifications/whatsapp_settings.py::
# is_enabled's own first gate.
os.environ.pop("AURA_WHATSAPP_PHONE_NUMBER_ID", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from commercial_runtime.notifications import whatsapp_settings as wa_settings  # noqa: E402
from commercial_runtime.notifications import whatsapp_recipients as wa_recipients  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client(monkeypatch):
    """A fresh, logged-in Flask test client for its own newly-created
    company. AURA_WHATSAPP_PHONE_NUMBER_ID is left UNSET by default -- tests
    opt into "configured" via monkeypatch.setenv."""
    monkeypatch.delenv('AURA_WHATSAPP_PHONE_NUMBER_ID', raising=False)

    email = f'wa-outbox-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'WhatsAppOutboxPW1'
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

    c.get('/api/sub/retail/settings/tax')  # primes doc_sequences lazily, see retail_reorder_sync_test.py

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
        'name': f'WA Outbox Widget {uuid.uuid4().hex[:6]}',
        'sku': f'WAOUT-{uuid.uuid4().hex[:8]}',
        'cost_price': 2, 'sell_price': 10.0,
        'reorder_level': reorder_level, 'reorder_method': reorder_method,
        'initial_stock': initial_stock,
    })
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()['data']['id']


def _sell(client, product_id, qty, amount_paid=999999):
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': product_id, 'quantity': qty}],
        'amount_paid': amount_paid, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _enable_whatsapp(company_id, *, templates=None):
    conn = get_retail_conn()
    wa_settings.set_setting(conn, company_id, 'enabled', '1')
    for key, value in (templates or {}).items():
        wa_settings.set_setting(conn, company_id, key, value)
    conn.commit()
    conn.close()


def _add_recipient(company_id, *, phone='+15551234567', report_types=(), branch_id=None):
    conn = get_retail_conn()
    rid = wa_recipients.create_recipient(
        conn, company_id, display_name='Test Recipient', phone_e164=phone,
        branch_id=branch_id, report_types=list(report_types),
    )
    conn.commit()
    conn.close()
    return rid


def _whatsapp_outbox_rows(db_conn, company_id, message_type=None):
    q = "SELECT * FROM whatsapp_outbox WHERE company_id=?"
    params = [company_id]
    if message_type:
        q += " AND message_type=?"
        params.append(message_type)
    return [dict(r) for r in db_conn.execute(q, params).fetchall()]


# ── Low-stock trigger: inert by default ──────────────────────────────────

def test_low_stock_sale_queues_nothing_when_transport_unconfigured(client, db_conn):
    _add_recipient(client.test_company_id, report_types=['low_stock_alert'])
    # AURA_WHATSAPP_PHONE_NUMBER_ID unset (fixture default) -- even with a
    # recipient AND enabled='0' default, is_enabled() must be False.
    pid = _make_product(client, reorder_level=5, initial_stock=6)
    _sell(client, pid, 2)  # crosses the threshold -- reorder_requests DOES get a row

    reorder_rows = db_conn.execute("SELECT * FROM reorder_requests WHERE product_id=?", (pid,)).fetchall()
    assert len(reorder_rows) == 1, "the pre-existing reorder trigger must be completely unaffected"
    assert _whatsapp_outbox_rows(db_conn, client.test_company_id) == []


def test_low_stock_sale_queues_nothing_when_company_has_not_opted_in(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    conn = get_retail_conn()
    wa_settings.set_setting(conn, client.test_company_id, 'low_stock_template_name', 'aura_low_stock')
    conn.commit()
    conn.close()
    _add_recipient(client.test_company_id, report_types=['low_stock_alert'])
    # deliberately NOT calling _enable_whatsapp -- enabled stays '0'

    pid = _make_product(client, reorder_level=5, initial_stock=6)
    _sell(client, pid, 2)

    assert _whatsapp_outbox_rows(db_conn, client.test_company_id) == []


def test_low_stock_sale_queues_nothing_when_no_template_name_configured(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id)  # enabled, but no low_stock_template_name
    _add_recipient(client.test_company_id, report_types=['low_stock_alert'])

    pid = _make_product(client, reorder_level=5, initial_stock=6)
    _sell(client, pid, 2)

    assert _whatsapp_outbox_rows(db_conn, client.test_company_id) == []


def test_low_stock_sale_queues_nothing_when_no_recipient_subscribed(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id, templates={'low_stock_template_name': 'aura_low_stock'})
    # recipient exists but subscribed to a DIFFERENT report type only
    _add_recipient(client.test_company_id, report_types=['daily_sales_summary'])

    pid = _make_product(client, reorder_level=5, initial_stock=6)
    _sell(client, pid, 2)

    assert _whatsapp_outbox_rows(db_conn, client.test_company_id) == []


# ── Low-stock trigger: fires when fully configured ───────────────────────

def test_low_stock_sale_queues_an_alert_for_each_subscribed_recipient(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id, templates={'low_stock_template_name': 'aura_low_stock'})
    _add_recipient(client.test_company_id, phone='+15550000001', report_types=['low_stock_alert'])
    _add_recipient(client.test_company_id, phone='+15550000002', report_types=['low_stock_alert'])

    pid = _make_product(client, reorder_level=5, initial_stock=6)
    _sell(client, pid, 2)  # 6 - 2 = 4 <= 5

    rows = _whatsapp_outbox_rows(db_conn, client.test_company_id, message_type='low_stock_alert')
    assert len(rows) == 2
    assert {r['recipient_phone_e164'] for r in rows} == {'+15550000001', '+15550000002'}
    assert all(r['status'] == 'QUEUED' for r in rows)
    assert all(r['template_name'] == 'aura_low_stock' for r in rows)


def test_low_stock_sale_dedupes_two_recipients_sharing_one_phone(client, db_conn, monkeypatch):
    """AUDIT-fix regression test: whatsapp_recipients has no (company_id,
    phone_e164) uniqueness constraint, so two distinct recipient rows (e.g.
    "Owner" and "Accountant") can legitimately share one real phone number.
    Both being subscribed to the same report type must still send only ONE
    WhatsApp message to that number, not two."""
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id, templates={'low_stock_template_name': 'aura_low_stock'})
    _add_recipient(client.test_company_id, phone='+15550009999', report_types=['low_stock_alert'])
    _add_recipient(client.test_company_id, phone='+15550009999', report_types=['low_stock_alert'])

    pid = _make_product(client, reorder_level=5, initial_stock=6)
    _sell(client, pid, 2)  # 6 - 2 = 4 <= 5

    rows = _whatsapp_outbox_rows(db_conn, client.test_company_id, message_type='low_stock_alert')
    assert len(rows) == 1
    assert rows[0]['recipient_phone_e164'] == '+15550009999'


def test_two_low_stock_sales_back_to_back_only_queue_once(client, db_conn, monkeypatch):
    """Same idempotency guard as the email trigger -- see reorder_hook.py's
    module docstring."""
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id, templates={'low_stock_template_name': 'aura_low_stock'})
    _add_recipient(client.test_company_id, report_types=['low_stock_alert'])

    pid = _make_product(client, reorder_level=5, initial_stock=10)
    _sell(client, pid, 6)  # 10 - 6 = 4 <= 5 -> first (and only) request+alert
    _sell(client, pid, 1)  # 4 - 1 = 3 <= 5 -> must NOT queue a second alert

    rows = _whatsapp_outbox_rows(db_conn, client.test_company_id, message_type='low_stock_alert')
    assert len(rows) == 1


def test_sale_response_is_unaffected_by_whatsapp_queueing(client, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id, templates={'low_stock_template_name': 'aura_low_stock'})
    _add_recipient(client.test_company_id, report_types=['low_stock_alert'])

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
    # truthy -- inside a broad try/except that logs and moves on. So a
    # failed e-invoicing enqueue now surfaces as a WhatsApp-outbox key-set
    # failure. What this assertion is actually about is unchanged: WhatsApp
    # queueing adds nothing to the checkout response. A diff on this line is
    # fixed upstream in e-invoicing, never by shortening the list.
    assert sorted(data.keys()) == sorted([
        'amount_paid', 'balance_due', 'calculation_version', 'change', 'currency',
        'customer_id', 'customer_name', 'discount_amount', 'einvoice', 'employee_name',
        'id', 'idempotency_key', 'lines',
        'oversold_past_recorded_stock',
        'points_redeemed', 'points_redeemed_amount',
    'sale_number', 'subtotal', 'tax_amount', 'total', 'warning',
    ])


# ── Shift-close trigger ────────────────────────────────────────────────────

def _open_close_shift(client, *, opening_float=100.0, counted=100.0):
    r = client.post('/api/sub/retail/cash-sessions/open', json={'opening_float': opening_float})
    assert r.status_code == 200, r.get_json()
    session_id = r.get_json()['data']['id']
    r2 = client.post(f'/api/sub/retail/cash-sessions/{session_id}/close', json={'closing_float_counted': counted})
    assert r2.status_code == 200, r2.get_json()
    return session_id, r2.get_json()


def test_shift_close_queues_nothing_when_unconfigured(client, db_conn):
    _add_recipient(client.test_company_id, report_types=['shift_close_report'])
    _open_close_shift(client)
    assert _whatsapp_outbox_rows(db_conn, client.test_company_id) == []


def test_shift_close_queues_a_report_for_each_subscribed_recipient(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id, templates={'shift_close_template_name': 'aura_shift_close'})
    _add_recipient(client.test_company_id, report_types=['shift_close_report'])

    session_id, close_data = _open_close_shift(client, opening_float=50.0, counted=50.0)

    rows = _whatsapp_outbox_rows(db_conn, client.test_company_id, message_type='shift_close_report')
    assert len(rows) == 1
    assert rows[0]['template_name'] == 'aura_shift_close'
    # close response itself is untouched by the best-effort enqueue
    assert close_data['data']['report']['variance'] == 0.0


def test_shift_close_response_is_unaffected_by_whatsapp_queueing_failure(client, monkeypatch):
    """Best-effort: even if enqueueing were to raise, the close must still
    succeed -- simulate by leaving transport unconfigured (a guaranteed
    no-op path) and asserting the close response shape is unaffected."""
    _add_recipient(client.test_company_id, report_types=['shift_close_report'])
    _, close_data = _open_close_shift(client)
    assert close_data['status'] == 'success'
    assert 'session' in close_data['data'] and 'report' in close_data['data']


# ── POST /reports/whatsapp ─────────────────────────────────────────────────

def test_reports_whatsapp_requires_a_valid_report_type(client, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    r = client.post('/api/sub/retail/reports/whatsapp', json={'report_type': 'not_a_real_type'})
    assert r.status_code == 400


def test_reports_whatsapp_requires_whatsapp_enabled(client, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    r = client.post('/api/sub/retail/reports/whatsapp', json={'report_type': 'daily_sales_summary'})
    assert r.status_code == 409


def test_reports_whatsapp_requires_a_template_name(client, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id)  # enabled, no daily_sales_template_name
    r = client.post('/api/sub/retail/reports/whatsapp', json={'report_type': 'daily_sales_summary'})
    assert r.status_code == 400


def test_reports_whatsapp_requires_a_subscribed_recipient(client, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id, templates={'daily_sales_template_name': 'aura_daily_sales'})
    r = client.post('/api/sub/retail/reports/whatsapp', json={'report_type': 'daily_sales_summary'})
    assert r.status_code == 400
    assert 'recipients' in r.get_json()['message'].lower()


def test_reports_whatsapp_daily_sales_summary_queues_when_fully_configured(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id, templates={'daily_sales_template_name': 'aura_daily_sales'})
    _add_recipient(client.test_company_id, report_types=['daily_sales_summary'])

    r = client.post('/api/sub/retail/reports/whatsapp', json={'report_type': 'daily_sales_summary'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['queued'] == 1

    rows = _whatsapp_outbox_rows(db_conn, client.test_company_id, message_type='daily_sales_summary')
    assert len(rows) == 1
    assert rows[0]['template_name'] == 'aura_daily_sales'


def test_reports_whatsapp_ar_overdue_alert_queues_when_fully_configured(client, db_conn, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '1234567890')
    _enable_whatsapp(client.test_company_id, templates={'ar_overdue_template_name': 'aura_ar_overdue'})
    _add_recipient(client.test_company_id, report_types=['ar_overdue_alert'])

    r = client.post('/api/sub/retail/reports/whatsapp', json={'report_type': 'ar_overdue_alert'})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['data']['queued'] == 1

    rows = _whatsapp_outbox_rows(db_conn, client.test_company_id, message_type='ar_overdue_alert')
    assert len(rows) == 1
    assert rows[0]['template_name'] == 'aura_ar_overdue'
