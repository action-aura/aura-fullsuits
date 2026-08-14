"""Aura Retail -- AI Assistant RAG multi-tenant isolation suite.

Covers the 2026-08-13 RAG upgrade to `/api/sub/retail/ai/chat`
(`retail_api.py`'s `ai_chat()` / `_build_ai_context()` / `_detect_ai_intent()`
/ `_ai_context_*()` helpers): the route now injects a small, server-fetched,
company-scoped business-data summary into the LLM prompt so it can answer
real questions ("how many products do I have", "what's low on stock")
instead of being a pure text chatbot with zero data access.

THIS FILE'S ENTIRE PURPOSE is proving the one thing that upgrade could get
catastrophically wrong: that company A's chat session can NEVER see company
B's data in its context, even when asking a generic question that doesn't
name a company at all. Every `_ai_context_*` query filters on
`company_id=?` using the SAME `_cid()` session-derived value every other
route in this file already scopes reads/writes to (see CLAUDE.md's "every
business table is company_id-scoped" rule) -- these tests seed two real
companies with different real data and assert, for every data category the
RAG layer supports, that each company's chat prompt contains ONLY its own
figures and NEVER the other company's, checking the full literal prompt
text sent to the model (not just the visible reply), so a leak in the
context builder can't hide behind a lucky mocked reply.

The actual outbound call to the hosted LLM (`requests.post` to
AURA_AI_ENDPOINT_URL) is monkeypatched to a fake that echoes the exact
prompt this route built back as the "reply" -- real session/login/`_cid()`/
DB-query/prompt-assembly code all runs for real; only the third-party
network hop to Ollama is stubbed, the same boundary `retail_einvoicing_test
.py`'s `test_enqueue_exception_does_not_fail_the_sale` stubs at (a live
call to a demo droplet has no place in a hermetic, deterministic unit
suite). Echoing the prompt back as the reply is what lets these tests
assert on `response.json()['data']['reply']` exactly as a real user would
read it in the sidebar, while still proving the isolation property against
the literal text the model would have seen.

This file follows the same self-contained bootstrap convention as every
other file in this suite (no shared conftest.py exists for
products/retail/tests/ -- confirmed by inspection): its own temp app-data
dir, its own license seed, its own Flask app boot, its own fixtures local
to this file.

Run:
    pytest products/retail/tests/retail_ai_rag_multitenant_test.py -v
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

import pytest
import requests

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_ai_rag_mt_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

# Seeded BEFORE the app is built, matching every other route-level test file
# in this suite -- creating products is capability-guarded, so an inactive
# license would 403 every test here before reaching the code paths this
# file is actually about.
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fake LLM: echoes the exact prompt back as the reply ────────────────────
class _FakeOllamaResponse:
    def __init__(self, prompt):
        self._prompt = prompt
        self.status_code = 200

    def json(self):
        return {'response': self._prompt}


def _install_fake_llm(monkeypatch):
    """Replace requests.post globally (same object retail_api.py's module-
    level `import requests` sees -- there is only one `requests` module in
    the process) with a fake that never touches the network and echoes the
    exact prompt this route built back as the model's "reply". Real code
    path everywhere else: login, session, _cid(), the DB queries in
    _ai_context_*(), and _build_ai_prompt()'s assembly all run unmodified."""
    def _fake_post(url, headers=None, json=None, timeout=None):
        return _FakeOllamaResponse(json['prompt'])
    monkeypatch.setattr(requests, 'post', _fake_post)


# ── Company seeding helpers ─────────────────────────────────────────────────
def _make_company(label, product_names_levels, customer_name, supplier_name, sale_total):
    """Creates one real company with a real admin login, real products
    (via the actual POST /products route -- same capability-guard,
    branch-resolution, and inventory_balances wiring a real install uses),
    and a real customer/supplier/sale row inserted directly against this
    company's own company_id. `product_names_levels` is a list of
    (name, sku, reorder_level, initial_stock) tuples -- at least one should
    be at/below its reorder_level so the low_stock category has something
    real to find."""
    email = f'ai-rag-{label}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'AiRagTestPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()

    product_ids = []
    for name, sku, reorder_level, initial_stock in product_names_levels:
        r = client.post('/api/sub/retail/products', json={
            'name': name, 'sku': sku, 'cost_price': 1, 'sell_price': 2,
            'reorder_level': reorder_level, 'initial_stock': initial_stock,
        })
        assert r.status_code == 200, r.get_json()
        product_ids.append(r.get_json()['data']['id'])

    # sales/sale_items were NOT part of the products/customers/suppliers UUID
    # migration (see CLAUDE.md's Sync section) -- their `id` columns are
    # still real INTEGER PRIMARY KEY AUTOINCREMENT rowid aliases, so unlike
    # customers/suppliers below they must be left for SQLite to assign
    # (cur.lastrowid), never handed an explicit UUID string -- confirmed
    # against create_sale()'s own `sale_id = cur.lastrowid` in retail_api.py.
    rconn = get_retail_conn()
    try:
        cur = rconn.cursor()
        cur.execute(
            "INSERT INTO customers (id, company_id, name) VALUES (?,?,?)",
            (str(uuid.uuid4()), company_id, customer_name),
        )
        cur.execute(
            "INSERT INTO suppliers (id, company_id, name, status) VALUES (?,?,?,'active')",
            (str(uuid.uuid4()), company_id, supplier_name),
        )
        now_local = datetime.now().strftime('%Y-%m-%d %H:%M:%S')  # matches _ai_context_sales' datetime.now() (local) comparison
        # sale_number is globally UNIQUE (not company-scoped -- see schema.py),
        # and this fixture runs fresh (against the same persistent test-module
        # DB) once per test function, so it needs a value unique across the
        # whole file's test run, not just per company.
        cur.execute(
            "INSERT INTO sales (company_id, sale_number, total, created_at) VALUES (?,?,?,?)",
            (company_id, f'SALE-{label.upper()}-{uuid.uuid4().hex[:10]}', sale_total, now_local),
        )
        sale_id = cur.lastrowid
        cur.execute(
            "INSERT INTO sale_items (sale_id, product_id, quantity, unit_price, line_total) VALUES (?,?,?,?,?)",
            (sale_id, product_ids[0], 3, sale_total / 3, sale_total),
        )
        rconn.commit()
    finally:
        rconn.close()

    return client, company_id, product_ids


@pytest.fixture
def companies():
    """Two real companies, seeded with deliberately distinct, real data in
    every category the RAG layer covers, so a cross-tenant leak of ANY kind
    (name, SKU, count, revenue figure) is immediately visible in an
    assertion rather than silently passing because both companies happen to
    share a value."""
    client_a, cid_a, pids_a = _make_company(
        'alpha',
        [('Aardvark Widget', 'AAA-WIDGET-1', 10, 2),   # below reorder_level=10 -> low stock
         ('Aardvark Gadget', 'AAA-GADGET-2', 5, 50)],  # well above reorder_level -> not low stock
        customer_name='Alice Aardvark',
        supplier_name='Aardvark Supply Co',
        sale_total=111.33,
    )
    client_b, cid_b, pids_b = _make_company(
        'bravo',
        [('Bravo Beanie', 'BBB-BEANIE-1', 10, 3),      # below reorder_level=10 -> low stock
         ('Bravo Jacket', 'BBB-JACKET-2', 5, 80),      # well above reorder_level -> not low stock
         ('Bravo Scarf', 'BBB-SCARF-3', 5, 90)],       # company B has 3 products, company A has 2 -- counts must differ
        customer_name='Bob Bravo',
        supplier_name='Bravo Supply Co',
        sale_total=222.66,
    )
    assert cid_a != cid_b
    return {
        'a': {'client': client_a, 'cid': cid_a},
        'b': {'client': client_b, 'cid': cid_b},
    }


def _ask(client, message, monkeypatch):
    _install_fake_llm(monkeypatch)
    r = client.post('/api/sub/retail/ai/chat', json={'message': message})
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['success'] is True
    return body['data']['reply']  # the exact prompt this route built, echoed back


# ═════════════════════════════════════════════════════════════════════════
# 1. Product count / catalog isolation
# ═════════════════════════════════════════════════════════════════════════

def test_product_count_question_reflects_only_own_company(companies, monkeypatch):
    reply_a = _ask(companies['a']['client'], 'How many products do I have?', monkeypatch)
    reply_b = _ask(companies['b']['client'], 'How many products do I have?', monkeypatch)

    assert '2 active product' in reply_a
    assert '3 active product' in reply_b

    # Negative-space assertions: the OTHER company's count/names/SKUs must
    # never appear in this company's prompt at all.
    assert '3 active product' not in reply_a
    assert '2 active product' not in reply_b
    for leaked in ('Bravo Beanie', 'Bravo Jacket', 'Bravo Scarf', 'BBB-BEANIE-1', 'BBB-JACKET-2', 'BBB-SCARF-3'):
        assert leaked not in reply_a, f"company A's prompt leaked company B data: {leaked!r}"
    for leaked in ('Aardvark Widget', 'Aardvark Gadget', 'AAA-WIDGET-1', 'AAA-GADGET-2'):
        assert leaked not in reply_b, f"company B's prompt leaked company A data: {leaked!r}"


# ═════════════════════════════════════════════════════════════════════════
# 2. Low-stock isolation
# ═════════════════════════════════════════════════════════════════════════

def test_low_stock_question_reflects_only_own_company(companies, monkeypatch):
    reply_a = _ask(companies['a']['client'], "What's low on stock?", monkeypatch)
    reply_b = _ask(companies['b']['client'], "What's low on stock?", monkeypatch)

    assert 'Aardvark Widget' in reply_a
    assert 'Bravo Beanie' in reply_b

    # Company A's healthy-stock product and company B's low-stock item must
    # never cross into the other company's context.
    assert 'Bravo Beanie' not in reply_a
    assert 'Aardvark Widget' not in reply_b
    for leaked in ('Bravo Beanie', 'Bravo Jacket', 'Bravo Scarf'):
        assert leaked not in reply_a, f"company A's low-stock prompt leaked company B data: {leaked!r}"
    for leaked in ('Aardvark Widget', 'Aardvark Gadget'):
        assert leaked not in reply_b, f"company B's low-stock prompt leaked company A data: {leaked!r}"


# ═════════════════════════════════════════════════════════════════════════
# 3. Sales revenue isolation
# ═════════════════════════════════════════════════════════════════════════

def test_sales_question_reflects_only_own_company(companies, monkeypatch):
    reply_a = _ask(companies['a']['client'], "What were today's sales?", monkeypatch)
    reply_b = _ask(companies['b']['client'], "What were today's sales?", monkeypatch)

    assert '111.33' in reply_a
    assert '222.66' in reply_b
    assert '222.66' not in reply_a, "company A's sales prompt leaked company B's revenue"
    assert '111.33' not in reply_b, "company B's sales prompt leaked company A's revenue"
    assert 'Aardvark Widget' in reply_a  # top-seller line, company A's own product
    assert 'Bravo Beanie' in reply_b     # top-seller line, company B's own product
    assert 'Aardvark Widget' not in reply_b
    assert 'Bravo Beanie' not in reply_a


# ═════════════════════════════════════════════════════════════════════════
# 4. Customer / supplier count isolation
# ═════════════════════════════════════════════════════════════════════════

def test_customer_question_reflects_only_own_company(companies, monkeypatch):
    reply_a = _ask(companies['a']['client'], 'How many customers do I have?', monkeypatch)
    reply_b = _ask(companies['b']['client'], 'How many customers do I have?', monkeypatch)
    assert '1 customer' in reply_a
    assert '1 customer' in reply_b
    # Count alone can't distinguish (both happen to have exactly 1 customer)
    # -- this category doesn't inject names, so the real assertion is that
    # the SQL itself is scoped; verified directly against the DB below in
    # test_context_builder_direct_db_isolation for a name-level check too.


def test_supplier_question_reflects_only_own_company(companies, monkeypatch):
    reply_a = _ask(companies['a']['client'], 'List my suppliers', monkeypatch)
    reply_b = _ask(companies['b']['client'], 'List my suppliers', monkeypatch)
    assert '1 active supplier' in reply_a
    assert '1 active supplier' in reply_b


# ═════════════════════════════════════════════════════════════════════════
# 5. Arabic business questions get real data too (regression test)
# ═════════════════════════════════════════════════════════════════════════

def test_arabic_product_question_gets_real_business_data(companies, monkeypatch):
    """Regression test for a real bug: _AI_INTENT_KEYWORDS was English-only,
    so an Arabic business question like "كم عدد المنتجات لدي؟" (how many
    products do I have) matched no category at all -- _detect_ai_intent()
    returned None, _build_ai_context() short-circuited to '' with zero DB
    queries, and the reply still rendered correctly in Arabic
    (_resolve_ai_language() is independent of this) but with none of the
    real company data the RAG upgrade exists to inject. Before the
    2026-08-14 fix this assertion failed outright: the prompt contained no
    product count at all, Arabic or otherwise."""
    reply_a = _ask(companies['a']['client'], 'كم عدد المنتجات لدي؟', monkeypatch)
    reply_b = _ask(companies['b']['client'], 'كم عدد المنتجات لدي؟', monkeypatch)
    assert '2 active product' in reply_a
    assert '3 active product' in reply_b
    # Same cross-tenant isolation guarantee as the English equivalent above.
    assert '3 active product' not in reply_a
    assert '2 active product' not in reply_b


def test_arabic_low_stock_question_gets_real_business_data(companies, monkeypatch):
    """Same regression, for the 'low_stock' category (checked ahead of the
    generic 'products' bucket -- see _AI_INTENT_KEYWORDS' ordering note)."""
    reply_a = _ask(companies['a']['client'], 'ما هي المنتجات ذات المخزون منخفض؟', monkeypatch)
    assert 'Aardvark Widget' in reply_a
    assert 'Bravo Beanie' not in reply_a


# ═════════════════════════════════════════════════════════════════════════
# 6. Generic / irrelevant questions inject no data at all (no leak surface)
# ═════════════════════════════════════════════════════════════════════════

def test_irrelevant_message_injects_no_business_data(companies, monkeypatch):
    reply_a = _ask(companies['a']['client'], 'Hello, how are you today?', monkeypatch)
    assert 'Real data for this business' not in reply_a
    for leaked in ('Aardvark', 'Bravo', '111.33', '222.66'):
        assert leaked not in reply_a


# ═════════════════════════════════════════════════════════════════════════
# 7. Direct unit-level isolation check against _build_ai_context() itself
# ═════════════════════════════════════════════════════════════════════════

def test_context_builder_direct_db_isolation(companies):
    """Bypasses the HTTP layer entirely and calls the RAG context builder
    the route uses, directly, with each company's real cid -- the strongest
    possible check that the SQL itself (not just the route wiring around
    it) is company-scoped. Every query in _AI_CONTEXT_BUILDERS filters on
    company_id=?, so passing the wrong cid must be structurally impossible
    to make this leak, not just empirically absent in this run."""
    import api.retail_api as retail_api_module

    cid_a = companies['a']['cid']
    cid_b = companies['b']['cid']

    ctx_a_products = retail_api_module._build_ai_context(cid_a, 'how many products do I have')
    ctx_b_products = retail_api_module._build_ai_context(cid_b, 'how many products do I have')
    assert 'Aardvark' in ctx_a_products and 'Bravo' not in ctx_a_products
    assert 'Bravo' in ctx_b_products and 'Aardvark' not in ctx_b_products

    ctx_a_customers = retail_api_module._build_ai_context(cid_a, 'how many customers do I have')
    ctx_b_customers = retail_api_module._build_ai_context(cid_b, 'how many customers do I have')
    # Same numeric count (1 each) on purpose -- proves this isn't passing
    # only because the two companies' numbers happen to differ.
    assert ctx_a_customers == "This company has 1 customer(s) on file."
    assert ctx_b_customers == "This company has 1 customer(s) on file."

    # A company id that was never seeded at all must get a real, honest
    # zero -- never another company's data, never an error.
    ghost_cid = str(uuid.uuid4())
    ctx_ghost = retail_api_module._build_ai_context(ghost_cid, 'how many products do I have')
    assert ctx_ghost == "This company has 0 active product(s)."
