"""Closing a till must email the Z-report -- if, and only if, email is on.

WHY THIS EXISTS

WhatsApp had TWO automatic triggers (low-stock after a sale, shift-close when
a cash session closes). Email had ONE: the low-stock alert in
`reorder_hook._maybe_queue_low_stock_email`. So a shop that chose email
instead of WhatsApp -- which is most of them, since WhatsApp needs a Meta
business number -- got NOTHING when a till was closed and counted. The Z-report
is the single most important automatic message a retail system sends: it is
the one that says whether the drawer balanced.

Both halves are tested, because this is a notification and the product's rule
is "invisible unless opted in":

  * ON  -- enabled + a reports recipient  -> exactly one row, right content
  * OFF -- not enabled, or no recipient   -> ZERO rows, no error, no log

The OFF half matters as much as the ON half. Every install that has not
configured email hits it on every single shift close, and a regression there
would spam an outbox that nothing drains.

Self-booting, matching the convention here (there is no shared conftest.py for
products/retail/tests/). Run on its own:

    py -3.14 -m pytest products/retail/tests/retail_shift_close_email_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_shift_close_email_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SMTP_HOST", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.notifications import settings as notif_settings  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("AURA_SMTP_HOST", raising=False)
    email = f'shiftclose-{uuid.uuid4().hex[:8]}@test.local'
    password = 'ShiftClosePW1'
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
    """is_enabled() folds smtp_client.is_configured() into its answer, so the
    host has to be set as well as the per-company toggle -- exactly what a
    real install needs, and the reason an unconfigured one is a silent no-op."""
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    conn = get_retail_conn()
    notif_settings.set_setting(conn, company_id, 'enabled', '1')
    if recipient:
        notif_settings.set_setting(conn, company_id, 'reports_recipient', recipient)
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


def _open_and_close(client, *, opening=100.0, counted=100.0):
    r = client.post('/api/sub/retail/cash-sessions/open',
                    json={'opening_float': opening, 'terminal_id': 'TILL-1'})
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()['data']['id']
    r = client.post(f'/api/sub/retail/cash-sessions/{sid}/close',
                    json={'closing_float_counted': counted})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


# ── The OFF half: invisible unless opted in ──────────────────────────────────

def test_no_email_when_notifications_are_not_enabled(client):
    _open_and_close(client)
    assert _emails(client.test_company_id) == [], (
        "an install that has not configured email must queue NOTHING on a "
        "shift close -- this is the branch every unconfigured shop takes on "
        "every close, and a regression here fills an outbox nothing drains")


def test_no_email_when_enabled_but_no_reports_recipient(client, monkeypatch):
    _enable_email(monkeypatch, client.test_company_id, recipient=None)
    _open_and_close(client)
    assert _emails(client.test_company_id) == [], (
        "enabled but with nowhere to send is still a no-op, not an error")


# ── The ON half ──────────────────────────────────────────────────────────────

def test_closing_a_till_queues_one_z_report_email(client, monkeypatch):
    _enable_email(monkeypatch, client.test_company_id)
    _open_and_close(client)

    rows = _emails(client.test_company_id)
    assert len(rows) == 1, "exactly one Z-report per close, got %d" % len(rows)
    assert rows[0]['recipient'] == 'owner@shop.test'
    assert 'Shift closed' in rows[0]['subject']


def test_a_balanced_drawer_says_so_and_an_unbalanced_one_says_so(client, monkeypatch):
    _enable_email(monkeypatch, client.test_company_id)

    _open_and_close(client, opening=100.0, counted=100.0)
    balanced = _emails(client.test_company_id)[-1]['body_text']
    assert 'balanced' in balanced.lower()
    assert 'did NOT balance' not in balanced

    _open_and_close(client, opening=100.0, counted=93.5)
    off = _emails(client.test_company_id)[-1]['body_text']
    assert 'did NOT balance' in off, (
        "a drawer that is short is the entire reason someone opens this email; "
        "it must not read the same as one that balanced")


def test_money_is_formatted_at_the_shops_own_precision(client, monkeypatch):
    """The Jordanian dinar has THREE decimal places -- 1000 fils.

    The WhatsApp shift-close report renders its figures with `:.2f` and would
    print 12.35 for a drawer holding 12.350. This channel reads base_currency
    and formats to that currency's real minor units instead.
    """
    _set_currency(client.test_company_id, 'JOD')
    _enable_email(monkeypatch, client.test_company_id)
    _open_and_close(client, opening=12.345, counted=12.345)

    body = _emails(client.test_company_id)[-1]['body_text']
    assert '12.345' in body, (
        "a JOD drawer must print fils. Body was:\n" + body)
    assert 'JD' in body, "the currency mark should be the shop's, not a bare number"


def test_a_shop_that_never_chose_a_currency_still_keeps_its_fils(client, monkeypatch):
    """THE DEFAULT INSTALL -- the case the first version of this file missed.

    `_set_currency` is deliberately NOT called here. Every other money test in
    this file writes an explicit `base_currency` row, which manufactures the
    one state in which both ways of reading that setting agree, and so proved
    nothing about the state almost every real install is actually in.

    `_settings()` builds from `dict(_DEFAULT_SETTINGS)` and answers 'JOD' for
    a shop that has never opened the settings screen. The raw SELECT helpers
    that the drawer and this email use answered None, and None means "use the
    2-decimal default". So a fresh Jordanian install -- the common case, not
    an edge case -- kept fils in the sale total and rounded them away in the
    drawer count and the Z-report.

    Mutation-proved in both directions: make `email_hook._currency` return
    None again and this test fails while every other test in the file still
    passes, which is precisely how the defect shipped.
    """
    _enable_email(monkeypatch, client.test_company_id)
    _open_and_close(client, opening=12.345, counted=12.345)

    body = _emails(client.test_company_id)[-1]['body_text']
    assert '12.345' in body, (
        "a shop that never opened settings sells in dinars and its Z-report "
        "must print fils. Body was:\n" + body)
    assert '12.35' not in body, (
        "12.35 means the amount was quantized to cents somewhere on the way")


def test_the_drawer_itself_stores_fils_for_a_default_install(client, monkeypatch):
    """The same defect one layer down, where it actually moves money.

    The test above proves the EMAIL prints fils. This one proves the drawer
    PERSISTED them -- a report can only be as precise as the number it reads,
    and `_money()` is what coerces the client's figure before it is stored.
    Asserted on the close response's own echo of the counted float rather
    than on any email, so it still fails if the email channel is removed
    entirely.
    """
    report = _open_and_close(client, opening=12.345, counted=12.345)['report']
    assert float(report['expected_cash']) == 12.345, (
        "expected cash was quantized to cents while the counted float kept "
        "its fils, which invents a variance out of nothing: expected %r"
        % (report['expected_cash'],))
    assert float(report['closing_float_counted']) == 12.345, (
        "the drawer quantized a default-install (JOD) count to cents: "
        "stored %r" % (report['closing_float_counted'],))
    assert float(report['opening_float']) == 12.345, (
        "same for the opening float: stored %r" % (report['opening_float'],))


def test_a_two_decimal_currency_still_gets_two(client, monkeypatch):
    """The allow-half: this must be currency-DRIVEN, not 'always three'."""
    _set_currency(client.test_company_id, 'USD')
    _enable_email(monkeypatch, client.test_company_id)
    _open_and_close(client, opening=12.34, counted=12.34)

    body = _emails(client.test_company_id)[-1]['body_text']
    assert '12.34' in body and '12.340' not in body, (
        "a dollar drawer must print cents, not fils. Body was:\n" + body)
