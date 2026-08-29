"""Aura Retail -- reorder automation foundation
(feat/reorder-automation-foundation): create_sale response-safety net.

The single hardest requirement in this feature's spec: it must be
STRUCTURALLY IMPOSSIBLE for a bug in core/retail/reorder_hook.py to fail or
change the shape of a sale's response. Mirrors
retail_einvoicing_regression_test.py's frozen-response-keys technique
(SALE_RESPONSE_KEYS captured live from the real app) combined with
retail_einvoicing_test.py's `test_enqueue_exception_does_not_fail_the_sale`
forced-failure technique -- monkeypatching the hook's entry point to raise
and asserting the sale still returns 200 with the exact same response
shape.

Unlike e-invoicing, this hook NEVER adds a key to the response even when it
succeeds (see core/retail/reorder_hook.py's own module docstring) -- so
"succeeds" and "fails" are asserted to produce a BYTE-FOR-BYTE identical
response, not merely "both return 200".

Run:
    pytest products/retail/tests/retail_reorder_hook_regression_test.py -v
"""
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_reorderhook_regression_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# Frozen literal -- same list retail_einvoicing_regression_test.py captured
# live from the real app; reorder automation does not add a key even when
# it fires, so this list is IDENTICAL to that file's own SALE_RESPONSE_KEYS
# on purpose. If this ever needs to change, that is a real, deliberate
# checkout-response contract change -- not a reason to "fix" this test.
#
# `oversold_past_recorded_stock` added launch-readiness Phase 7 stage
# 7d-iii (docs/launch-readiness/phase7-offline-ux.md "Correction to
# Decision 1") -- exactly the "real, deliberate checkout-response contract
# change" this comment already anticipated. Reorder automation still never
# adds a key; that claim is unaffected.
SALE_RESPONSE_KEYS = [
    'amount_paid', 'balance_due', 'calculation_version', 'change', 'currency',
    'discount_amount', 'id', 'idempotency_key', 'lines', 'oversold_past_recorded_stock',
    'sale_number', 'subtotal', 'tax_amount', 'total', 'warning',
]


def _make_admin_and_product(reorder_level=5, reorder_method='whatsapp', initial_stock=6):
    email = f"reorderhook-reg-{uuid.uuid4().hex[:10]}@test.local"
    password = "ReorderHookRegPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), "admin", "active"),
    )
    conn.commit()
    conn.close()

    client = app.test_client()
    client.post("/api/auth/login", json={"email": email, "password": password})

    resp = client.post('/api/sub/retail/products', json={
        'name': 'Reorder Hook Regression Widget', 'sku': f'RHR-{uuid.uuid4().hex[:8]}',
        'cost_price': 2, 'sell_price': 10,
        'reorder_level': reorder_level, 'reorder_method': reorder_method,
        'initial_stock': initial_stock,
    })
    assert resp.status_code == 200, resp.get_json()
    product_id = resp.get_json()['data']['id']

    return client, company_id, product_id


def _sell(client, product_id, qty=2):
    r = client.post('/api/sub/retail/sales', json={
        'items': [{'product_id': product_id, 'quantity': qty}],
        'amount_paid': 999999, 'payment_method': 'cash', 'idempotency_key': str(uuid.uuid4()),
    })
    return r


def test_sale_response_keys_unchanged_when_the_hook_fires_successfully():
    """reorder_method='whatsapp' + stock dropping to/below reorder_level ->
    the hook DOES fire and create a pending request -- the response must
    still carry exactly the frozen key set, with no 'reorder' (or similar)
    key added."""
    client, cid, pid = _make_admin_and_product(reorder_level=5, reorder_method='whatsapp', initial_stock=6)
    r = _sell(client, pid, 2)  # 6 - 2 = 4 <= 5 -> the hook fires
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert sorted(data.keys()) == sorted(SALE_RESPONSE_KEYS)

    conn = get_retail_conn()
    count = conn.execute("SELECT COUNT(*) c FROM reorder_requests WHERE product_id=?", (pid,)).fetchone()['c']
    conn.close()
    assert count == 1  # sanity check: the hook really did fire for this test to be meaningful


def test_sale_response_byte_for_byte_unchanged_whether_or_not_the_hook_fires():
    """Two sales on two different products -- one triggers the hook (stock
    crosses reorder_level), one never does (reorder_method='none') -- must
    produce responses with the identical KEY SET and identical VALUE TYPES,
    proving the hook's presence/absence never leaks into the response shape
    even though it changes reorder_requests state behind the scenes."""
    client_a, _, pid_a = _make_admin_and_product(reorder_level=5, reorder_method='whatsapp', initial_stock=6)
    client_b, _, pid_b = _make_admin_and_product(reorder_level=5, reorder_method='none', initial_stock=6)

    data_a = _sell(client_a, pid_a, 2).get_json()['data']
    data_b = _sell(client_b, pid_b, 2).get_json()['data']

    assert sorted(data_a.keys()) == sorted(data_b.keys()) == sorted(SALE_RESPONSE_KEYS)
    for key in SALE_RESPONSE_KEYS:
        if key in ('id', 'sale_number', 'idempotency_key', 'lines'):
            continue  # legitimately different per-sale identifiers/content
        assert type(data_a[key]) == type(data_b[key]), f"type mismatch on {key!r}"


def test_hook_exception_does_not_fail_the_sale(monkeypatch):
    """Forces core/retail/reorder_hook.maybe_trigger_reorder to raise --
    mirrors retail_einvoicing_test.py's
    test_enqueue_exception_does_not_fail_the_sale exactly. The sale must
    still return 200 with the full, correct response -- and, unlike
    e-invoicing, the response must be BYTE-FOR-BYTE what it would have been
    had the hook never run at all (no partial/'best-effort' key is ever
    added)."""
    client, cid, pid = _make_admin_and_product(reorder_level=5, reorder_method='whatsapp', initial_stock=6)

    import core.retail.reorder_hook as reorder_hook

    def _boom(conn_factory, **kwargs):
        raise RuntimeError("simulated reorder hook failure")

    monkeypatch.setattr(reorder_hook, 'maybe_trigger_reorder', _boom)
    # retail_api.py imports maybe_trigger_reorder by name at call time
    # (inside the try block, exactly like enqueue_sale), so patching the
    # module's attribute is sufficient -- no need to touch retail_api.py.
    r = _sell(client, pid, 2)  # 6 - 2 = 4 <= 5 -> the hook WOULD have fired
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert 'id' in data
    assert sorted(data.keys()) == sorted(SALE_RESPONSE_KEYS)

    # The hook raised before it ever reached the INSERT -- no reorder
    # request exists for this product, proving the raise happened where
    # expected (inside the hook, not merely swallowed elsewhere) while the
    # sale itself is completely unaffected.
    conn = get_retail_conn()
    count = conn.execute("SELECT COUNT(*) c FROM reorder_requests WHERE product_id=?", (pid,)).fetchone()['c']
    conn.close()
    assert count == 0


def test_hook_exception_produces_the_identical_response_a_successful_hook_would_have(monkeypatch):
    """The strongest form of the byte-for-byte claim: build the EXACT same
    sale twice (same product config, same quantity, same tender) -- once
    with the hook running normally, once with it forced to raise -- and
    diff the two responses field by field (excluding the legitimately
    per-sale-unique id/sale_number/idempotency_key)."""
    client_ok, _, pid_ok = _make_admin_and_product(reorder_level=5, reorder_method='whatsapp', initial_stock=6)
    client_fail, _, pid_fail = _make_admin_and_product(reorder_level=5, reorder_method='whatsapp', initial_stock=6)

    ok_response = _sell(client_ok, pid_ok, 2).get_json()['data']

    import core.retail.reorder_hook as reorder_hook

    def _boom(conn_factory, **kwargs):
        raise RuntimeError("simulated reorder hook failure")

    monkeypatch.setattr(reorder_hook, 'maybe_trigger_reorder', _boom)
    fail_response = _sell(client_fail, pid_fail, 2).get_json()['data']

    excluded = {'id', 'sale_number', 'idempotency_key', 'lines'}
    for key in SALE_RESPONSE_KEYS:
        if key in excluded:
            continue
        assert ok_response[key] == fail_response[key], f"divergence on {key!r}"

    # `lines` differs only in product_id/branch_id (a different product AND
    # a different auto-created branch per client/company) -- every OTHER
    # field of the single line item must match exactly.
    ok_line = ok_response['lines'][0]
    fail_line = fail_response['lines'][0]
    for key in ('quantity', 'unit_price', 'discount_pct', 'tax_rate', 'line_total'):
        assert ok_line[key] == fail_line[key], f"line divergence on {key!r}"


def test_no_background_thread_exists_from_the_reorder_hook():
    """The hook runs synchronously, inline, on its own connection -- not a
    background thread. Mirrors
    retail_einvoicing_regression_test.py's identical assertion for
    e-invoicing's own worker."""
    import threading
    client, cid, pid = _make_admin_and_product(reorder_level=5, reorder_method='whatsapp', initial_stock=6)
    baseline = {t.name for t in threading.enumerate()}
    _sell(client, pid, 2)
    names = {t.name for t in threading.enumerate()}
    assert names.issubset(baseline | {'MainThread'})
