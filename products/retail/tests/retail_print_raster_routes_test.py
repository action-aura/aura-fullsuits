"""
Aura Retail -- PRINT-1 routes: receipt layout model + silent raster print.

    GET  /api/sub/retail/printer/receipt-model
    POST /api/sub/retail/printer/print-raster

WHAT THIS PROVES (and what it does not). A real sale -- an Arabic-named
product with an extra, a manual discount, tax, a cash+card split -- is rung
through the real route; the model is read back and compared with the stored
figures; a bitmap is posted with the transport stubbed and the captured bytes
are inspected. The kick rule and the company scope each have a mutation proof
(bypass the rule / drop `company_id` -> RED), quoted in the PRINT-1 report.
It does NOT prove a printer: no physical device has received these bytes.

Self-contained bootstrap, matching retail_printer_kick_test.py (no shared
conftest). One file per process (AUDIT-010): this boots an app at import.

Run:
    pytest products/retail/tests/retail_print_raster_routes_test.py -v
"""
import base64
import json
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_print_raster_"))
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
# Both safe to import at module scope on every platform: escpos_receipt is a
# pure byte layer (no ctypes at all, ever); escpos_transport only touches
# `ctypes.windll` INSIDE its functions, behind `_require_windows()` as their
# very first statement (see that module's own "WHY IMPORT MUST NEVER TOUCH
# ctypes.windll" docstring section) -- importing the MODULE object itself
# never accesses ctypes, so this is safe on CI's Linux collection run too.
# The route under test imports both locally itself, exactly as its own
# docstring explains; they are imported here as well ONLY so this file can
# monkeypatch `escpos_transport.send_raw` and read `escpos_receipt.
# drawer_kick()`'s canonical bytes for the mutation-critical tests below.
from core.retail import escpos_receipt  # noqa: E402
from core.retail import escpos_transport  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_printer_routes_test.py's own docstring for why nothing here is
#    shared via a conftest.py) ────────────────────────────────────────────

def _make_user(role, *, company_id=None, capabilities=None):
    """A real account with the legacy subsystem grant plus real capability
    rows, seeded through the SAME production helper account creation uses --
    matches retail_route_capability_matrix_test.py's `_make_user`.

    `capabilities`, when given, overrides individual seeded rows AFTER the
    role default (e.g. `{user_accounts.CAP_SELL: 'none'}`) -- this is how
    test 5 proves the DENY half without inventing a role that does not exist
    in this product (every seeded role -- admin, manager, cashier -- holds
    CAP_SELL by default, since all three can ring a sale)."""
    email = f"praster-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "PrintRasterPW1"  # pragma: allowlist secret -- throwaway test fixture, not a real credential
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



from core.retail import money_format  # noqa: E402
from core.retail import receipt_model  # noqa: E402

API = '/api/sub/retail'
ARABIC_NAME = "قهوة عربية"
ESC_AT = b"\x1b@"
KICK = escpos_receipt.drawer_kick()
WIDTH, HEIGHT = 576, 20
BITMAP_B64 = base64.b64encode(b"\x00" * (WIDTH // 8 * HEIGHT)).decode()


def _seed_shop():
    admin, company_id = _make_user('admin')
    admin.get(f'{API}/settings/tax')  # forces the lazy schema helpers
    import random
    dconn = get_retail_conn()
    dconn.execute(
        "INSERT OR REPLACE INTO doc_sequences (company_id,doc_type,last_no) VALUES (?,'sale',?)",
        (company_id, random.randint(1, 5_000_000)))
    dconn.commit()
    dconn.close()

    def product(name, price, tax=0):
        r = admin.post(f'{API}/products', json={
            'name': name, 'sku': f'PR-{uuid.uuid4().hex[:8]}', 'sell_price': price,
            'cost_price': 1.0, 'tax_rate': tax, 'initial_stock': 1000})
        assert r.status_code == 200, r.get_json()
        return r.get_json()['data']['id']

    arabic = product(ARABIC_NAME, 5.0, tax=16)
    english = product('Mint tea', 2.5)
    g = admin.post(f'{API}/modifier-groups', json={'name': 'Size', 'min_select': 0})
    assert g.status_code == 200, g.get_json()
    gid = g.get_json()['data']['id']
    o = admin.post(f'{API}/modifier-groups/{gid}/options', json={'name': 'Large', 'price_delta': 0.5})
    assert o.status_code == 200, o.get_json()
    oid = o.get_json()['data']['id']
    assert admin.post(f'{API}/products/{arabic}/modifier-groups', json={'group_id': gid}).status_code == 200
    return admin, company_id, arabic, english, oid


@pytest.fixture(scope='module')
def shop():
    return _seed_shop()


@pytest.fixture(scope='module')
def other_company_admin():
    return _make_user('admin')


def _items(shop, discount_pct=0):
    _, _, arabic, english, oid = shop
    first = {'product_id': arabic, 'quantity': 2, 'modifier_option_ids': [oid]}
    if discount_pct:
        first['discount_pct'] = discount_pct
    return [first, {'product_id': english, 'quantity': 1}]


def _ring(shop, *, method='cash', discount_pct=0, tenders=None):
    admin = shop[0]
    body = {'items': _items(shop, discount_pct), 'payment_method': method,
            'idempotency_key': str(uuid.uuid4())}
    if method == 'cash':
        body['amount_paid'] = 1000
    if tenders:
        body['tenders'] = tenders
    r = admin.post(f'{API}/sales', json=body)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _split_sale(shop, discount_pct=0):
    total = _ring(shop, discount_pct=discount_pct)['total']  # dry run for the figure
    return _ring(shop, method='split', discount_pct=discount_pct,
                 tenders=[{'method': 'cash', 'amount': 1}, {'method': 'card', 'amount': round(total - 1, 3)}])


def _model(client, sale_id, **params):
    q = {'sale_id': sale_id, **params}
    return client.get(f'{API}/printer/receipt-model', query_string=q)


def _print(client, sale_id, **over):
    body = {'sale_id': sale_id, 'bitmap_b64': BITMAP_B64, 'width_dots': WIDTH, 'height': HEIGHT,
            'host': '192.0.2.10', 'port': 9100, 'cut': True, 'kick': False}
    body.update(over)
    return client.post(f'{API}/printer/print-raster', json=body)


@pytest.fixture
def sent(monkeypatch):
    """Capture instead of print: every transport call as (kind, target, bytes)."""
    calls = []
    monkeypatch.setattr(escpos_transport, 'send_to_network',
                        lambda host, port, payload: calls.append(('net', f'{host}:{port}', payload)))
    monkeypatch.setattr(escpos_transport, 'send_raw',
                        lambda name, payload: calls.append(('raw', name, payload)))
    return calls


def _texts(model):
    out = []
    for ln in model['lines']:
        for key in ('text', 'start', 'end', 'strike'):
            if ln.get(key):
                out.append(ln[key])
    return out


# ── receipt-model ────────────────────────────────────────────────────────────

def test_model_of_a_real_split_sale_matches_the_stored_figures(shop):
    admin = shop[0]
    sale = _split_sale(shop, discount_pct=10)
    stored = admin.get(f"{API}/sales/{sale['id']}").get_json()['data']['sale']
    r = _model(admin, sale['id'])
    assert r.status_code == 200, r.get_json()
    m = r.get_json()['data']
    assert m['version'] == 1 and m['rtl'] is False and m['width_dots'] == 576

    texts = _texts(m)
    joined = "\n".join(texts)
    # Arabic product name, the extra, and the English product are all there.
    assert ARABIC_NAME in joined
    assert 'Large' in joined
    assert 'Mint tea' in joined
    # Money is the shop currency at THREE decimals -- never 2dp.
    total = money_format.format_money(stored['total'], 'JOD')
    assert total in texts
    assert len(total.split('.')[-1]) == 3
    assert money_format.format_money(stored['subtotal'], 'JOD') in texts
    assert money_format.format_money(stored['tax_amount'], 'JOD') in texts
    # Split tender: one row per tender, not a single "Paid".
    labels = [ln['start'] for ln in m['lines'] if ln['kind'] == 'row']
    assert 'Cash' in labels and 'Card' in labels and 'Paid' not in labels
    # The manual discount (10%) is its own row, from the stored sale.
    assert 'Discount' in labels
    assert m['qr_payload'] is None


def test_single_tender_prints_paid_and_change(shop):
    admin = shop[0]
    sale = _ring(shop)
    m = _model(admin, sale['id']).get_json()['data']
    labels = [ln['start'] for ln in m['lines'] if ln['kind'] == 'row']
    assert 'Paid' in labels and 'Change due' in labels and 'Cash' not in labels


def test_arabic_language_translates_labels_and_mirrors(shop):
    admin = shop[0]
    sale = _ring(shop)
    m = _model(admin, sale['id'], lang='ar').get_json()['data']
    assert m['rtl'] is True and m['lang'] == 'ar'
    labels = [ln['start'] for ln in m['lines'] if ln['kind'] == 'row']
    assert receipt_model.AR_LABELS['Total'] in labels
    assert 'Total' not in labels
    # Currency mark is the Arabic one, digits stay Western at 3 decimals.
    assert any(receipt_model.AR_LABELS['JD'] in t for t in _texts(m))


def test_ar_labels_match_the_shipped_arabic_catalogue():
    catalogue = json.loads((PRODUCT_DIR / 'frontend' / 'locales' / 'ar.json').read_text(encoding='utf-8'))
    for english, arabic in receipt_model.AR_LABELS.items():
        assert catalogue.get(english) == arabic, english


def test_arabic_bidi_guards_are_in_the_model_strings():
    """Pure (no sale): the three strings that came out WRONG on the real canvas
    on 2026-10-12 and the isolate that fixes each. Without them the Arabic
    receipt printed "9485 006 78 962+" for the phone, the hash on the wrong side
    of the receipt number, and the currency mark after the digits."""
    m = receipt_model.build_receipt_model(
        sale={'sale_number': 'SALE-9', 'subtotal': 7, 'total': 7, 'amount_paid': 7},
        items=[{'name': 'x', 'quantity': 2, 'unit_price': 3.5, 'applied_promotion': None}],
        branding={'branding_phone': '+962 78 006 9485', 'business_name': 'S'},
        currency='JOD', requested_lang='ar')
    texts = _texts(m)
    lrm = receipt_model.LRM
    assert f"{receipt_model.LRI}+962 78 006 9485{receipt_model.PDI}" in texts
    assert any(t.endswith(f"{receipt_model.LRI}#SALE-9{receipt_model.PDI}") for t in texts)
    assert any(t.startswith(receipt_model.LRI + receipt_model.AR_LABELS['JD'] + receipt_model.PDI) for t in texts)
    # quantity is flanked by LRMs so "x2" keeps its order beside Arabic text
    assert any(f"{lrm}×2{lrm}" in t for t in texts)


def test_model_other_company_sale_is_404(shop, other_company_admin):
    sale = _ring(shop)
    other, _ = other_company_admin
    assert _model(other, sale['id']).status_code == 404


def test_model_rejects_bad_width_and_missing_sale_id(shop):
    admin = shop[0]
    sale = _ring(shop)
    assert _model(admin, sale['id'], width_dots=500).status_code == 400
    assert admin.get(f'{API}/printer/receipt-model').status_code == 400


def test_model_requires_sell_capability(shop):
    _, company_id, *_ = shop
    sale = _ring(shop)
    denied, _ = _make_user('cashier', company_id=company_id, capabilities={user_accounts.CAP_SELL: 'none'})
    assert _model(denied, sale['id']).status_code == 403


# ── print-raster ─────────────────────────────────────────────────────────────

def test_cash_sale_with_kick_sends_one_job_with_the_pulse_after_the_cut(shop, sent):
    admin = shop[0]
    sale = _ring(shop)
    r = _print(admin, sale['id'], kick=True)
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['printed'] is True and d['kicked'] is True, d
    assert len(sent) == 1, "receipt and pulse must be ONE transport call"
    kind, target, payload = sent[0]
    assert (kind, target) == ('net', '192.0.2.10:9100')
    assert payload.startswith(ESC_AT)
    assert payload.endswith(escpos_receipt.partial_cut() + KICK)


def test_kick_false_sends_no_pulse(shop, sent):
    admin = shop[0]
    r = _print(admin, _ring(shop)['id'], kick=False)
    assert r.get_json()['data'] == {'printed': True, 'kicked': False, 'reason': None}
    assert KICK not in sent[0][2]


def test_card_sale_with_kick_prints_but_refuses_the_drawer(shop, sent):
    admin = shop[0]
    sale = _ring(shop, method='card')
    r = _print(admin, sale['id'], kick=True)
    d = r.get_json()['data']
    assert d['printed'] is True and d['kicked'] is False, d
    assert 'card' in d['reason'] and 'not cash' in d['reason'], d
    assert len(sent) == 1 and KICK not in sent[0][2]


def test_split_sale_with_a_cash_part_opens_the_drawer(shop, sent):
    admin = shop[0]
    r = _print(admin, _split_sale(shop)['id'], kick=True)
    assert r.get_json()['data']['kicked'] is True
    assert sent[0][2].endswith(KICK)


def test_spooler_name_used_when_no_host(shop, sent):
    admin = shop[0]
    r = _print(admin, _ring(shop)['id'], host='', printer='POS-80')
    assert r.get_json()['data']['printed'] is True
    assert sent[0][:2] == ('raw', 'POS-80')


def test_host_wins_over_printer_name(shop, sent):
    admin = shop[0]
    _print(admin, _ring(shop)['id'], host='192.0.2.9', printer='POS-80')
    assert [c[0] for c in sent] == ['net']


def test_no_printer_configured_is_a_200_and_sends_nothing(shop, sent):
    admin = shop[0]
    r = _print(admin, _ring(shop)['id'], host='', printer='', kick=True)
    assert r.status_code == 200
    d = r.get_json()['data']
    assert d['printed'] is False and d['kicked'] is False and 'No ESC/POS printer' in d['reason']
    assert sent == []


def test_transport_failure_is_a_200_never_a_failed_sale(shop, monkeypatch):
    admin = shop[0]

    def boom(*a, **k):
        raise escpos_transport.EscPosTransportError('printer offline')

    monkeypatch.setattr(escpos_transport, 'send_to_network', boom)
    r = _print(admin, _ring(shop)['id'], kick=True)
    assert r.status_code == 200
    assert r.get_json()['data'] == {'printed': False, 'kicked': False, 'reason': 'printer offline'}


def test_qr_payload_is_forwarded_natively(shop, sent):
    admin = shop[0]
    _print(admin, _ring(shop)['id'], qr_payload='ISTD-QR-123')
    assert escpos_receipt._qr_command('ISTD-QR-123') in sent[0][2]


def test_other_company_sale_is_404_and_sends_nothing(shop, other_company_admin, sent):
    sale = _ring(shop)
    other, _ = other_company_admin
    r = _print(other, sale['id'], kick=True)
    assert r.status_code == 404, r.get_json()
    assert sent == []


def _audit_actions(company_id, sale_id):
    conn = get_retail_conn()
    try:
        return {r['action'] for r in conn.execute(
            "SELECT action FROM audit_log WHERE company_id=? AND entity='sale' AND entity_id=?",
            (company_id, sale_id)).fetchall()}
    finally:
        conn.close()


def test_audit_rows_written_for_print_and_kick(shop, sent):
    admin, company_id, *_ = shop
    sale = _ring(shop)
    _print(admin, sale['id'], kick=True)
    assert {'RECEIPT_PRINTED', 'CASH_DRAWER_KICKED'} <= _audit_actions(company_id, sale['id'])


def test_card_sale_audits_print_but_no_kick(shop, sent):
    admin, company_id, *_ = shop
    sale = _ring(shop, method='card')
    _print(admin, sale['id'], kick=True)
    actions = _audit_actions(company_id, sale['id'])
    assert 'RECEIPT_PRINTED' in actions and 'CASH_DRAWER_KICKED' not in actions


def test_oversized_body_is_refused_413(shop, sent):
    admin = shop[0]
    huge = base64.b64encode(b"\x00" * 1_100_000).decode()
    r = _print(admin, _ring(shop)['id'], bitmap_b64=huge)
    assert r.status_code == 413
    assert sent == []


@pytest.mark.parametrize("over", [
    {'width_dots': 500},
    {'height': 21},                       # length no longer matches
    {'bitmap_b64': '!!!not base64!!!'},
    {'qr_payload': 'x' * 2000},
])
def test_malformed_requests_are_400_and_send_nothing(shop, sent, over):
    admin = shop[0]
    assert _print(admin, _ring(shop)['id'], **over).status_code == 400
    assert sent == []


def test_print_raster_requires_sell_capability(shop, sent):
    _, company_id, *_ = shop
    sale = _ring(shop)
    denied, _ = _make_user('cashier', company_id=company_id, capabilities={user_accounts.CAP_SELL: 'none'})
    assert _print(denied, sale['id']).status_code == 403
    assert sent == []
