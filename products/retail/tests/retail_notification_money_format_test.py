"""Shared money-formatting for notifications: unit + parity coverage.

WHY THIS FILE EXISTS

`core/retail/money_format.py` is the ONE place `email_hook.py` and
`whatsapp_hook.py` both go to answer "what currency is this shop in" and
"how do I print an amount of it". Before it existed, WhatsApp hardcoded
`:.2f` while email read `base_currency` and formatted to the currency's real
minor units -- for a JOD shop (three decimal places, 1000 fils) that meant
the SAME closed drawer printed `12.350` in the email and `12.35` over
WhatsApp. A shop with both channels enabled saw its own records disagree
with each other about the same event, which is worse than either channel
alone being wrong (see money_format.py's own docstring).

This file covers the shared module directly (currency-driven precision,
never-raise degrade-to-default), AND -- the actual point of the whole task
-- a parity test that closes one real cash session and checks BOTH channels'
own produced text for the same figure, not a shared helper both happen to
call.

Self-booting, matching the convention here (there is no shared conftest.py
for products/retail/tests/). Run on its own, from the repo root:

    py -3.14 -m pytest products/retail/tests/retail_notification_money_format_test.py -q
"""
import json
import os
import shutil
import sqlite3
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

DATA = Path(tempfile.mkdtemp(prefix="aura_notification_money_format_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SMTP_HOST", None)
os.environ.pop("AURA_WHATSAPP_PHONE_NUMBER_ID", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.notifications import settings as notif_settings  # noqa: E402
from commercial_runtime.notifications import whatsapp_settings  # noqa: E402
from commercial_runtime.notifications import whatsapp_recipients  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from core.retail import money_format  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("AURA_SMTP_HOST", raising=False)
    monkeypatch.delenv("AURA_WHATSAPP_PHONE_NUMBER_ID", raising=False)
    email = f'moneyfmt-{uuid.uuid4().hex[:8]}@test.local'
    password = 'MoneyFormatPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, "
        "role, status, require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email,
         hash_password(password), 'admin', 'active'))
    conn.commit(); conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    c.get('/api/sub/retail/settings/tax')   # creates retail_settings lazily
    c.test_company_id = company_id
    return c


def _enable_email(monkeypatch, company_id, recipient='owner@shop.test'):
    """Same gate as retail_shift_close_email_test.py::_enable_email -- is_
    enabled() folds smtp_client.is_configured() into its answer, so the SMTP
    host env var has to be set as well as the per-company toggle."""
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    conn = get_retail_conn()
    notif_settings.set_setting(conn, company_id, 'enabled', '1')
    if recipient:
        notif_settings.set_setting(conn, company_id, 'reports_recipient', recipient)
    conn.commit(); conn.close()


def _enable_whatsapp(monkeypatch, company_id, phone='+15551234567'):
    """Mirrors _enable_email above: whatsapp_settings.is_enabled() folds
    whatsapp_client.is_configured() into its answer, so the transport env
    var has to be set as well as the per-company toggle; a template name has
    to be configured (_ready_template requires one); and a recipient has to
    be subscribed to 'shift_close_report' or _enqueue_for_recipients()
    enqueues nothing -- same 'invisible unless opted in' contract email
    uses. No network call happens here: queue_shift_close_report() only
    INSERTs into whatsapp_outbox, it never calls whatsapp_client.send_*, so
    enabling this in a test needs no real Meta credentials."""
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', 'test-phone-id')
    conn = get_retail_conn()
    whatsapp_settings.set_setting(conn, company_id, 'enabled', '1')
    whatsapp_settings.set_setting(conn, company_id, 'shift_close_template_name', 'shift_close_report_v1')
    whatsapp_recipients.create_recipient(
        conn, company_id, display_name='Owner', phone_e164=phone,
        report_types=['shift_close_report'], language_code='en_US')
    conn.commit(); conn.close()


def _set_currency(company_id, code):
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?) "
        "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
        (company_id, 'base_currency', code))
    conn.commit(); conn.close()


def _emails(company_id, email_type='shift_close_report'):
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT recipient, subject, body_text FROM email_outbox "
            "WHERE company_id=? AND email_type=? ORDER BY rowid",
            (company_id, email_type)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _whatsapp_messages(company_id, message_type='shift_close_report'):
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT recipient_phone_e164, template_name, component_params_json "
            "FROM whatsapp_outbox WHERE company_id=? AND message_type=? ORDER BY id",
            (company_id, message_type)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _open_and_close(client, *, opening=100.0, counted=100.0):
    r = client.post('/api/sub/retail/cash-sessions/open',
                    json={'opening_float': opening, 'terminal_id': 'TILL-1'})
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()['data']['id']
    r = client.post(f'/api/sub/retail/cash-sessions/{sid}/close',
                    json={'closing_float_counted': counted})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


# ── (a) format_money is currency-driven, never a fixed precision ────────────

def test_format_money_uses_currency_specific_minor_units():
    """The exact bug that shipped for WhatsApp: a hardcoded `:.2f` would
    print 12.35 for a JOD figure that must print 12.345 (three decimals,
    1000 fils). Also proves the allow-half -- a 2-decimal currency must
    still get 2, not 'always three' from an overcorrected fix."""
    assert money_format.format_money(12.345, 'JOD') == 'JD 12.345'
    assert money_format.format_money(12.3, 'USD') == '$12.30'


def test_format_money_on_unknown_currency_falls_back_to_2dp_and_does_not_raise():
    """An unknown/malformed currency code must degrade to the generic
    2-decimal default rather than raise -- the same never-raise posture
    pricing.currency_quantum documents for itself. A bad stored setting must
    never turn a best-effort notification path into a 500."""
    result = money_format.format_money(12.3, 'XYZ')
    assert result == 'XYZ 12.30'


# ── (b)/(c)/(d) company_currency ─────────────────────────────────────────────

def test_company_currency_returns_the_stored_code(client):
    """A shop that DID configure a currency gets that currency back --
    catches company_currency ignoring an actually-stored setting."""
    _set_currency(client.test_company_id, 'USD')
    conn = get_retail_conn()
    try:
        assert money_format.company_currency(conn, client.test_company_id) == 'USD'
    finally:
        conn.close()


def test_company_currency_defaults_to_jod_when_no_row_exists(client):
    """THE DEFAULT INSTALL -- the case a fixture that always writes a
    base_currency row would hide. A shop that has never opened settings is a
    Jordanian shop selling in dinars; this must answer 'JOD', not None (which
    would route format_money to the 2-decimal generic default and round its
    fils away) and not raise."""
    conn = get_retail_conn()
    try:
        assert money_format.company_currency(conn, client.test_company_id) == 'JOD'
    finally:
        conn.close()


def test_company_currency_does_not_raise_with_no_settings_table():
    """A database with no retail_settings table at all (the sqlite3.Error
    branch in company_currency's try/except) must still answer 'JOD' rather
    than raise -- this is called from a best-effort notification path that
    must never block on a missing table, same posture as
    metrics.business_day degrading on the identical condition."""
    conn = sqlite3.connect(':memory:')
    try:
        assert money_format.company_currency(conn, 'some-company') == 'JOD'
    finally:
        conn.close()


# ── (e) THE PARITY TEST -- the point of this whole file ─────────────────────

def test_email_and_whatsapp_shift_close_report_the_same_figure(client, monkeypatch):
    """Both channels fire off the SAME close_cash_session() call
    (products/retail/backend/api/retail_api.py's close route enqueues both,
    each best-effort, from the SAME `report` dict). If either channel
    formats money differently, a shop with both channels enabled sees its
    own drawer disagree with itself for the identical event -- the exact
    failure money_format.py exists to prevent.

    Asserted on the actual text each channel PRODUCES -- the email's
    body_text, and the WhatsApp row's component_params_json (the ordered
    template placeholder values whatsapp_hook.py actually builds) -- not on
    money_format.format_money() directly, because a helper-level assertion
    cannot catch one channel forgetting to call the shared helper at all.

    Deliberately opens and closes with two DIFFERENT amounts (expected_cash
    and closing_float_counted end up different once a sale runs the drawer
    up) and checks each figure at its OWN position, not with a blanket
    `substring in whole message` check -- an early version of this test used
    `in` on the joined WhatsApp params, which stayed green even when the
    expected_cash param alone was mutated back to a bare `:.2f`, because the
    OTHER money param (closing_float_counted) still matched the same
    literal. A parity test that cannot fail when exactly the code path it
    exists to guard is broken is decorative, not a test.
    """
    _set_currency(client.test_company_id, 'JOD')
    _enable_email(monkeypatch, client.test_company_id)
    _enable_whatsapp(monkeypatch, client.test_company_id)

    _open_and_close(client, opening=12.345, counted=9.123)

    email_rows = _emails(client.test_company_id)
    assert len(email_rows) == 1, "expected exactly one Z-report email, got %d" % len(email_rows)
    email_body = email_rows[0]['body_text']

    wa_rows = _whatsapp_messages(client.test_company_id)
    assert len(wa_rows) == 1, "expected exactly one Z-report WhatsApp message, got %d" % len(wa_rows)
    wa_params = json.loads(wa_rows[0]['component_params_json'])
    # branch, closed_at, expected_cash, closing_float_counted, variance --
    # the exact order queue_shift_close_report() builds them in.
    assert len(wa_params) == 5, "unexpected WhatsApp param shape: %r" % (wa_params,)
    wa_expected_cash, wa_counted, wa_variance = wa_params[2], wa_params[3], wa_params[4]

    # Both channels must print EXPECTED CASH at JOD's 3 decimal places,
    # checked at each channel's own position -- not via a substring check
    # that a differently-formatted OTHER figure in the same message could
    # accidentally satisfy.
    assert 'JD 12.345' in email_body, (
        "email did not format expected cash at JOD's 3 decimal places. Body was:\n" + email_body)
    assert wa_expected_cash == 'JD 12.345', (
        "WhatsApp's expected_cash param did not match JOD's 3 decimal places -- "
        "this is the exact regression a private `:.2f` would reintroduce. Got %r, "
        "full params were:\n%r" % (wa_expected_cash, wa_params))

    # And CLOSING COUNTED, independently -- a different amount from expected
    # cash on purpose, so a bug in one figure cannot hide behind the other.
    assert 'JD 9.123' in email_body, (
        "email did not format the counted float at JOD's 3 decimal places. Body was:\n" + email_body)
    assert wa_counted == 'JD 9.123', (
        "WhatsApp's closing_float_counted param did not match JOD's 3 decimal "
        "places. Got %r, full params were:\n%r" % (wa_counted, wa_params))

    # variance = counted - expected = 9.123 - 12.345 = -3.222 (drawer short).
    # format_money() does not strip the sign -- it formats the float as-is,
    # so a negative variance already reads "-3.222"; only a POSITIVE
    # variance gets an explicit '+' prepended (queue_shift_close_report's
    # own `('+' if variance > 0 else '')`, mirroring email_hook's).
    assert wa_variance == 'JD -3.222', (
        "WhatsApp's variance param did not match JOD's 3 decimal places. "
        "Got %r, full params were:\n%r" % (wa_variance, wa_params))
