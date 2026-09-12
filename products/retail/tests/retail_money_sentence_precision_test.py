"""Aura Retail -- money figures rendered into SENTENCES keep the shop's real
currency precision, not a hardcoded `:.2f`.

WHY THIS EXISTS

A long money-precision pass this cycle made every PERSISTED and every
REPORTED figure follow the shop's real currency (the Jordanian dinar has
three decimal places, 1000 fils) -- `_money()` in api/retail_api.py,
`metrics._money()`, and `core/retail/money_format.py::format_money()` all
honour that now. Two places still formatted money into SENTENCES with a
hardcoded `{:.2f}`, so the underlying value was already correct and the TEXT
a human actually reads was not:

  * `_ai_context_sales` (api/retail_api.py) -- the figures handed to the AI
    assistant, injected verbatim into the prompt as "real data ... use these
    exact figures". A user asking the chat box what today's sales are will
    believe the sentence over the dashboard card three inches away.
  * `_render_report_email_body` -- the on-demand report email a manager
    reads on their phone, with nothing beside it to contradict a wrong
    figure.

A THIRD site was found while writing this file, not named in the original
two-site list: `create_sale`'s credit-limit-exceeded error message (~line
4456) hardcoded `{:.2f}` for `limit`/`outstanding`/`this sale adds`, even
though all three values were already currency-quantized via `_money()`
before this sentence was built.

All three now go through `core/retail/money_format.py::format_money()` --
the ONE shared money-STRING renderer `email_hook.py` and `whatsapp_hook.py`
already use, for the identical "two channels must not disagree about the
same figure" reason documented in that module's own docstring. Currency is
resolved with `api/retail_api.py::_company_currency` (the pre-existing local
helper every other money site in this file already uses) -- `money_format.py`
's own docstring names this exact helper as making "the identical choice on
the API side for the identical reason".

Every test below proves BOTH halves this defect class requires, using the
SAME `12.345` / `12.34` trap values the rest of this money-precision pass
uses throughout the suite (retail_shift_close_email_test.py,
retail_sale_money_precision_test.py):

  (a) DEFAULT INSTALL -- no explicit `base_currency` row. This is the
      COMMON case a fresh install is actually in, not an edge one; earlier
      in this same cycle a test file missed this defect entirely because
      its fixture always wrote an explicit currency row, manufacturing the
      one state where both the old (None-defaulting) and new code paths
      agreed. `12.345` must appear in the rendered text and `12.35` must
      NOT.
  (b) THE ALLOW-HALF -- `base_currency` explicitly 'USD'. Proves the fix is
      currency-DRIVEN, not "always three decimals now": a fix that only
      proves fils survive cannot be told apart from one that hardcoded 3dp
      instead of 2dp, and the second would silently wrong every
      2-decimal-currency shop the moment it shipped. `12.34` must appear
      and `12.340` must NOT.

Assertions are on the rendered STRING throughout, never on a numeric value
behind it -- the whole defect was that the persisted number and the sentence
about it disagreed while both "looked" correct in isolation.

Self-contained, one process per file (AURA_APP_DATA binds at import) -- no
shared conftest.py exists for products/retail/tests/. Boot block copied
verbatim from retail_shift_close_email_test.py.

Run alone, from the repo root:
    py -3.14 -m pytest products/retail/tests/retail_money_sentence_precision_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_money_sentence_precision_"))
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
from api import retail_api  # noqa: E402 -- the SAME module object app.py registered

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("AURA_SMTP_HOST", raising=False)
    email = f'moneysentence-{uuid.uuid4().hex[:8]}@test.local'
    password = 'MoneySentencePW1'
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
    c.get(f'{API}/settings/tax')   # creates retail_settings lazily
    c.test_company_id = company_id
    return c


def _set_currency(company_id, code):
    """Writes an EXPLICIT base_currency row -- used only by the (b) tests.
    The (a) tests deliberately never call this; see module docstring."""
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?) "
        "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
        (company_id, 'base_currency', code))
    conn.commit(); conn.close()


def _set_enforce_credit_limit(company_id, mode):
    """Same direct-write technique as `_set_currency` -- `enforce_credit_limit`
    defaults to 'warn' (`_DEFAULT_SETTINGS`), which never reaches the 400
    branch this file is testing."""
    conn = get_retail_conn()
    conn.execute(
        "INSERT INTO retail_settings (company_id, skey, svalue) VALUES (?,?,?) "
        "ON CONFLICT(company_id,skey) DO UPDATE SET svalue=excluded.svalue",
        (company_id, 'enforce_credit_limit', mode))
    conn.commit(); conn.close()


def _seed_product(c, sell_price, cost_price=0, tax_rate=0):
    r = c.post(f'{API}/products', json={
        'name': 'Money Sentence Widget', 'sku': f'MONEYSENT-{uuid.uuid4().hex[:8]}',
        'cost_price': cost_price, 'sell_price': sell_price, 'tax_rate': tax_rate,
        'initial_stock': 10,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


def _sell_for_cash(c, product_id):
    """A plain cash sale, overpaid so no customer/credit path is touched --
    used by the AI-context and report-email sites, which read REVENUE, not
    the credit-limit message (that one builds its own sale below)."""
    r = c.post(f'{API}/sales', json={
        'items': [{'product_id': product_id, 'quantity': 1}],
        'payment_method': 'cash', 'amount_paid': 999999,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _seed_limited_customer(company_id, *, credit_limit):
    """Direct DB insert, same technique retail_financial_authority_test.py's
    `test_negative_amount_paid_is_clamped_to_zero_not_credited` already uses
    -- the customer-creation route has no field for credit_mode/credit_limit
    at creation time."""
    conn = get_retail_conn()
    customer_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO customers (id, company_id, name, credit_mode, credit_limit, credit_balance, status) "
        "VALUES (?,?,?,'limited',?,0,'active')",
        (customer_id, company_id, 'Money Sentence Customer', credit_limit))
    conn.commit(); conn.close()
    return customer_id


def _enable_email(monkeypatch, company_id, recipient='owner@shop.test'):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.test')
    conn = get_retail_conn()
    notif_settings.set_setting(conn, company_id, 'enabled', '1')
    if recipient:
        notif_settings.set_setting(conn, company_id, 'reports_recipient', recipient)
    conn.commit(); conn.close()


def _report_emails(company_id):
    conn = get_retail_conn()
    try:
        rows = conn.execute(
            "SELECT body_text FROM email_outbox WHERE company_id=? AND email_type='report_summary' "
            "ORDER BY rowid", (company_id,)).fetchall()
    finally:
        conn.close()
    return [r['body_text'] for r in rows]


# ═════════════════════════════════════════════════════════════════════════
# 1. `_ai_context_sales` -- the sentence handed to the AI assistant as
#    "real data for this business ... use these exact figures".
#    Called directly (not through POST /ai/chat, which proxies to an
#    external Ollama endpoint this test suite has no business depending on)
#    -- `conn`/`cid` ARE in scope for this function, so this is a direct
#    unit test of the exact sentence-building code, not a workaround.
# ═════════════════════════════════════════════════════════════════════════

def test_ai_context_sales_default_install_keeps_fils(client):
    pid = _seed_product(client, sell_price=12.345)
    _sell_for_cash(client, pid)

    conn = get_retail_conn()
    try:
        text = retail_api._ai_context_sales(conn, client.test_company_id)
    finally:
        conn.close()

    assert '12.345' in text, (
        "a default (JOD) install's assistant context must quote fils, not "
        f"round them away -- got: {text!r}")
    assert '12.35' not in text, (
        "12.35 means the sentence re-flattened an already-correct 12.345 "
        f"figure to 2dp -- got: {text!r}")
    assert 'JD' in text, "the currency mark should be the shop's, not a bare number"


def test_ai_context_sales_two_decimal_currency_still_gets_two(client):
    """THE ALLOW-HALF: currency-DRIVEN, not 'always three'."""
    _set_currency(client.test_company_id, 'USD')
    pid = _seed_product(client, sell_price=12.34)
    _sell_for_cash(client, pid)

    conn = get_retail_conn()
    try:
        text = retail_api._ai_context_sales(conn, client.test_company_id)
    finally:
        conn.close()

    assert '12.34' in text, f"got: {text!r}"
    assert '12.340' not in text, (
        f"a dollar shop's assistant context must not print fils -- got: {text!r}")
    assert '$' in text, "the currency mark should be the shop's, not a bare number"


# ═════════════════════════════════════════════════════════════════════════
# 2. `_render_report_email_body` -- the on-demand report email
#    (POST /reports/email), exercised end-to-end through the route exactly
#    like retail_email_outbox_test.py's own report-email tests, so this
#    proves the real call site (currency resolved at `report_summary_email`,
#    not injected by the test).
# ═════════════════════════════════════════════════════════════════════════

def test_report_email_default_install_keeps_fils(client, monkeypatch):
    _enable_email(monkeypatch, client.test_company_id)
    pid = _seed_product(client, sell_price=12.345)
    _sell_for_cash(client, pid)

    r = client.post(f'{API}/reports/email', json={})
    assert r.status_code == 200, r.get_json()

    body = _report_emails(client.test_company_id)[-1]
    assert '12.345' in body, (
        f"a JOD shop's report email must print fils. Body was:\n{body}")
    assert '12.35' not in body, (
        f"12.35 means a figure was re-flattened to 2dp somewhere on the way. Body was:\n{body}")
    assert 'JD' in body, "the currency mark should be the shop's, not a bare number"


def test_report_email_two_decimal_currency_still_gets_two(client, monkeypatch):
    """THE ALLOW-HALF: currency-DRIVEN, not 'always three'."""
    _set_currency(client.test_company_id, 'USD')
    _enable_email(monkeypatch, client.test_company_id)
    pid = _seed_product(client, sell_price=12.34)
    _sell_for_cash(client, pid)

    r = client.post(f'{API}/reports/email', json={})
    assert r.status_code == 200, r.get_json()

    body = _report_emails(client.test_company_id)[-1]
    assert '12.34' in body, f"Body was:\n{body}"
    assert '12.340' not in body, (
        f"a dollar shop's report email must not print fils. Body was:\n{body}")
    assert '$' in body, "the currency mark should be the shop's, not a bare number"


# ═════════════════════════════════════════════════════════════════════════
# 3. `create_sale`'s credit-limit-exceeded 400 message -- found while
#    writing this file, not in the original two-site list (see module
#    docstring). `limit`/`outstanding`/`this sale adds` were already
#    currency-quantized via `_money()`; only the SENTENCE re-flattened them.
# ═════════════════════════════════════════════════════════════════════════

def test_credit_limit_message_default_install_keeps_fils(client):
    _set_enforce_credit_limit(client.test_company_id, 'block')
    customer_id = _seed_limited_customer(client.test_company_id, credit_limit=5.345)
    pid = _seed_product(client, sell_price=12.345)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'amount_paid': 0, 'customer_id': customer_id,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 400, r.get_json()
    message = r.get_json()['message']

    assert '5.345' in message, (
        f"the credit LIMIT must print fils on a JOD install -- got: {message!r}")
    assert '5.35' not in message, (
        f"5.35 means the limit was re-flattened to 2dp -- got: {message!r}")
    assert '12.345' in message, (
        f"'this sale adds' must print fils on a JOD install -- got: {message!r}")
    assert '12.35' not in message, (
        f"12.35 means the sale total was re-flattened to 2dp -- got: {message!r}")
    assert 'JD' in message, "the currency mark should be the shop's, not a bare number"


def test_credit_limit_message_two_decimal_currency_still_gets_two(client):
    """THE ALLOW-HALF: currency-DRIVEN, not 'always three'."""
    _set_currency(client.test_company_id, 'USD')
    _set_enforce_credit_limit(client.test_company_id, 'block')
    customer_id = _seed_limited_customer(client.test_company_id, credit_limit=5.34)
    pid = _seed_product(client, sell_price=12.34)

    r = client.post(f'{API}/sales', json={
        'items': [{'product_id': pid, 'quantity': 1}],
        'payment_method': 'credit', 'amount_paid': 0, 'customer_id': customer_id,
        'idempotency_key': str(uuid.uuid4())})
    assert r.status_code == 400, r.get_json()
    message = r.get_json()['message']

    assert '5.34' in message, f"got: {message!r}"
    assert '5.340' not in message, (
        f"a dollar shop's credit-limit message must not print fils -- got: {message!r}")
    assert '12.34' in message, f"got: {message!r}"
    assert '12.340' not in message, f"got: {message!r}"
    assert '$' in message, "the currency mark should be the shop's, not a bare number"
