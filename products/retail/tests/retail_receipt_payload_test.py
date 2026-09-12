"""
Aura Retail -- GET /printer/receipt-payload, the real-sale ESC/POS byte
payload (retail-hardware-viewports).

THE GAP this closes: `core/retail/escpos_receipt.py::render_receipt` is a
pure Python byte layer (no ctypes, no Windows), so it can run anywhere,
including inside the Android app's embedded Python backend -- but until this
route existed its ONLY caller was `printer_test` (retail_printer_routes_test.py),
which renders a fixed SAMPLE receipt from the Settings screen. Nothing
anywhere rendered a REAL sale, which is the blocker for printing from
Android: the printer hardware is reached from the Android (Kotlin) side, so
the backend's job is only to hand the client the exact bytes for a real
sale. This route is that; getting those bytes to Android's printer is a
separate, later task.

THE POINT of every test below is that the bytes describe THE REAL SALE, not
merely that some bytes came back -- "returns 200 with a non-empty payload"
cannot catch a wrong column mapping (e.g. `sales.change_amount` read as if
the key were `change`, or a line item's `product_name` read as if the key
were `name`); reading the sale straight back out of sqlite and searching the
decoded bytes for its own printed text can.

Self-contained bootstrap, matching retail_printer_kick_test.py /
retail_printer_routes_test.py (no shared conftest.py exists here). One file
per process (AUDIT-010): these boot an app at import.

Run:
    pytest products/retail/tests/retail_receipt_payload_test.py -v
"""
import base64
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_receipt_payload_"))
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
from database.schema import get_retail_conn  # noqa: E402
# Pure byte layer, no ctypes at all -- safe to import at module scope on
# every platform (see retail_printer_kick_test.py's identical note), needed
# here only to read `drawer_kick()`'s canonical bytes for tests 4 and 7.
from core.retail import escpos_receipt  # noqa: E402
from core.retail import money_format  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_printer_routes_test.py's own docstring for why nothing here is
#    shared via a conftest.py) ────────────────────────────────────────────

def _make_user(role, *, company_id=None, capabilities=None):
    """A real account with the legacy subsystem grant plus real capability
    rows, seeded through the SAME production helper account creation uses --
    matches retail_printer_kick_test.py / retail_route_capability_matrix_test.py's
    own `_make_user`.

    `capabilities`, when given, overrides individual seeded rows AFTER the
    role default (e.g. `{user_accounts.CAP_SELL: 'none'}`) -- this is how
    test 6 proves the DENY half without inventing a role that does not exist
    in this product."""
    email = f"receipt-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "ReceiptPayloadPW1"  # pragma: allowlist secret -- throwaway test fixture, not a real credential
    company_id = company_id or str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    # mt_require_subsystem still demands the legacy un-namespaced grant, so
    # without this the request never reaches the capability check at all.
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    if capabilities:
        for code, level in capabilities.items():
            conn.execute(
                "UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                (level, user_id, code),
            )
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client, company_id


PRODUCT_A_NAME = 'Payload Widget A'
PRODUCT_B_NAME = 'Payload Widget B'


def _seed_shop():
    """An admin plus TWO in-stock products, in a fresh company -- two
    products (not one) so the sale rung below has two real line items, per
    the task's own requirement that this route be proven against a sale with
    more than one line."""
    admin, company_id = _make_user('admin')
    admin.get('/api/sub/retail/settings/tax')  # forces the lazy schema helpers, same as sibling files

    import random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, random.randint(1, 5_000_000)),
    )
    dconn.commit()
    dconn.close()

    # Product A carries a real tax_rate so subtotal != total on every sale
    # rung below -- deliberately, so a mutation that hardcodes/misreads
    # `total` (test 1's mutation proof) cannot hide behind the Subtotal
    # line coincidentally printing the same figure for a tax-free sale.
    r_a = admin.post('/api/sub/retail/products', json={
        'name': PRODUCT_A_NAME, 'sku': f'RCPT-A-{uuid.uuid4().hex[:8]}',
        'sell_price': 10.0, 'tax_rate': 10, 'initial_stock': 1000,
    })
    assert r_a.status_code == 200, r_a.get_json()
    r_b = admin.post('/api/sub/retail/products', json={
        'name': PRODUCT_B_NAME, 'sku': f'RCPT-B-{uuid.uuid4().hex[:8]}',
        'sell_price': 8.0, 'tax_rate': 0, 'initial_stock': 1000,
    })
    assert r_b.status_code == 200, r_b.get_json()
    return admin, company_id, r_a.get_json()['data']['id'], r_b.get_json()['data']['id']


def _sale_two_items(client, product_a, product_b):
    """Rings ONE real sale with TWO line items through the real route (never
    a raw INSERT), so `sale_number`/`total`/line items on the row are exactly
    what create_sale itself writes -- the columns this route's mapping must
    read back correctly."""
    payload = {
        'items': [
            {'product_id': product_a, 'quantity': 1},
            {'product_id': product_b, 'quantity': 2},
        ],
        'amount_paid': 1000,
        'payment_method': 'cash',
        'idempotency_key': str(uuid.uuid4()),
    }
    r = client.post('/api/sub/retail/sales', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _real_sale_row(sale_id):
    """Reads the sale straight back out of sqlite -- independent of
    anything the route itself computed -- so tests assert against the
    database's own ground truth, not against a value the route might have
    gotten wrong in the same way twice."""
    conn = get_retail_conn()
    row = conn.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    conn.close()
    return row


def _expected_total_text(company_id, total):
    conn = get_retail_conn()
    currency = money_format.company_currency(conn, company_id)
    conn.close()
    return money_format.format_money(total, currency)


@pytest.fixture(scope='module')
def shop():
    return _seed_shop()


@pytest.fixture(scope='module')
def other_company_admin():
    """A second, unrelated company. Proves company scoping (test 5) is real,
    not assumed."""
    admin, company_id = _make_user('admin')
    return admin, company_id


# ── 1. THE test: the payload describes the REAL sale, not a fixed sample ────
# MUTATION-PROVEN (see the task's own verification step): hardcoding the
# rendered total (e.g. `'total': 0` in retail_api.py's sale_payload mapping)
# makes this go RED, restoring the real `sale['total']` mapping makes it
# GREEN again -- both directions quoted in the final report.

def test_payload_contains_the_real_sale_number_and_total(shop):
    admin, company_id, product_a, product_b = shop
    sale = _sale_two_items(admin, product_a, product_b)

    r = admin.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale["id"]}')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    payload = base64.b64decode(data['payload_b64'])

    # ANTI-VACUITY (test 7's own point, checked here too since these are the
    # exact bytes the `in` checks below search): a near-empty payload would
    # make every `in` check below fail for the RIGHT reason by accident, not
    # prove the mapping is correct.
    assert len(payload) > 150, payload

    row = _real_sale_row(sale['id'])
    assert str(row['sale_number']).encode('ascii') in payload, (row['sale_number'], payload)

    # Guards the guard: total must differ from subtotal (product A's
    # tax_rate=10 in _seed_shop sees to that), or a mutation that hardcodes
    # `total` could hide behind the Subtotal line coincidentally printing
    # the same real figure.
    assert row['total'] != row['subtotal'], "fixture must make total != subtotal for this test to mean anything"

    expected_total = _expected_total_text(company_id, row['total'])
    assert expected_total.encode('ascii') in payload, (expected_total, payload)


# ── 2. A line item's product NAME is on the paper ────────────────────────────

def test_payload_contains_a_line_items_product_name(shop):
    admin, company_id, product_a, product_b = shop
    sale = _sale_two_items(admin, product_a, product_b)

    r = admin.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale["id"]}')
    assert r.status_code == 200, r.get_json()
    payload = base64.b64decode(r.get_json()['data']['payload_b64'])

    assert PRODUCT_A_NAME.encode('ascii') in payload, payload
    assert PRODUCT_B_NAME.encode('ascii') in payload, payload


# ── 3. width narrows the wrap, and rejects anything but 42/32 ───────────────

def test_width_changes_the_bytes_and_rejects_an_invalid_width(shop):
    admin, company_id, product_a, product_b = shop
    sale = _sale_two_items(admin, product_a, product_b)
    sale_id = sale['id']

    r42 = admin.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale_id}&width=42')
    r32 = admin.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale_id}&width=32')
    assert r42.status_code == 200, r42.get_json()
    assert r32.status_code == 200, r32.get_json()
    d42, d32 = r42.get_json()['data'], r32.get_json()['data']
    assert d42['width_chars'] == 42, d42
    assert d32['width_chars'] == 32, d32

    p42 = base64.b64decode(d42['payload_b64'])
    p32 = base64.b64decode(d32['payload_b64'])
    assert p42 != p32, "42-char and 32-char wraps must not produce identical bytes"

    r_default = admin.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale_id}')
    assert r_default.get_json()['data']['width_chars'] == 42, "42 (80mm) is the documented default"

    r_bad = admin.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale_id}&width=99')
    assert r_bad.status_code == 400, r_bad.get_json()


# ── 4. kick defaults OFF; both directions ────────────────────────────────────
# MUTATION-PROVEN (see the task's own verification step): flipping the kick
# default (e.g. `kick = True` regardless of the query string) makes the
# OFF-half of this go RED, restoring the real default-False read makes it
# GREEN again -- both directions quoted in the final report.

def test_kick_defaults_off_and_appends_the_real_kick_bytes_when_asked(shop):
    admin, company_id, product_a, product_b = shop
    sale = _sale_two_items(admin, product_a, product_b)
    sale_id = sale['id']
    kick_bytes = escpos_receipt.drawer_kick()

    r_default = admin.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale_id}')
    payload_default = base64.b64decode(r_default.get_json()['data']['payload_b64'])
    assert not payload_default.endswith(kick_bytes), payload_default

    for kick_value in ('1', 'true', 'TRUE'):
        r_kick = admin.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale_id}&kick={kick_value}')
        assert r_kick.status_code == 200, r_kick.get_json()
        payload_kick = base64.b64decode(r_kick.get_json()['data']['payload_b64'])
        assert payload_kick.endswith(kick_bytes), (kick_value, payload_kick)
        assert payload_kick == payload_default + kick_bytes, (kick_value, "kick must ONLY append, never alter the receipt body")


# ── 5. Company scoping is real, not assumed ──────────────────────────────────

def test_sale_from_another_company_is_404(shop, other_company_admin):
    admin, company_id, product_a, product_b = shop
    sale = _sale_two_items(admin, product_a, product_b)
    other_admin, _ = other_company_admin

    r = other_admin.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale["id"]}')
    assert r.status_code == 404, r.get_json()


# ── 6. Capability gate, both directions ──────────────────────────────────────
# The deny half is the obvious one; the allow half is what proves the gate is
# not simply refusing everyone (a decorator that always returns 403 would
# pass a deny-only test just as happily).

def test_capability_gate_both_directions(shop):
    admin, company_id, product_a, product_b = shop
    sale = _sale_two_items(admin, product_a, product_b)
    sale_id = sale['id']

    denied, _ = _make_user('cashier', company_id=company_id, capabilities={user_accounts.CAP_SELL: 'none'})
    r_denied = denied.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale_id}')
    assert r_denied.status_code == 403, r_denied.get_json()

    allowed, _ = _make_user('cashier', company_id=company_id)
    r_allowed = allowed.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale_id}')
    assert r_allowed.status_code == 200, r_allowed.get_json()


# ── 7. ANTI-VACUITY, standalone and explicit ────────────────────────────────
# A payload of only a couple of bytes (e.g. just the ESC '@' init sequence)
# would make every `in` check in tests 1-2 above fail LOUDLY -- a real
# defect -- but nothing here proves that on its OWN, since a body-less
# assertion never distinguishes "the mapping is wrong" from "the renderer
# produced almost nothing". Pin a floor so "a real receipt was decoded", not
# merely assumed, is itself asserted, and cross-check the server's own
# `byte_count` against what actually decoded.

def test_anti_vacuity_the_decoded_payload_is_a_real_receipt(shop):
    admin, company_id, product_a, product_b = shop
    sale = _sale_two_items(admin, product_a, product_b)

    r = admin.get(f'/api/sub/retail/printer/receipt-payload?sale_id={sale["id"]}')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    payload = base64.b64decode(data['payload_b64'])

    # The bare init sequence (`ESC @`) is 2 bytes; a real two-line-item
    # receipt with a header, both item rows, subtotal/total/paid and a cut
    # sequence is easily an order of magnitude more than that.
    assert len(payload) > 150, payload
    assert data['byte_count'] == len(payload), data
