"""Aura Retail -- branding (launch-readiness, "make the system be brandable
of whatever institute or coop or foundation bought it").

A senior review found subsystem-retail.js's `_printReceipt` printed the
literal string "Aura Retail" on every receipt, with no shop name, address or
tax number anywhere on it -- a fresh install could not print a receipt
legally adequate for its OWN shop, let alone a co-op's brand. This must never
become a fork per customer; it is configuration. `retail_settings` is already
a per-company key/value table (see `_DEFAULT_SETTINGS`/`_settings()` in
api/retail_api.py), so adding branding is adding keys, never a schema change.

THE PERFORMANCE TRAP THIS FILE GUARDS: `_settings(conn, cid)` is read
straight into a dict on EVERY call, including several times inside
create_sale() (base_currency). A logo stored as a data-URI under an ordinary
key would therefore be read, parsed and discarded on every single sale,
silently undoing the POS performance work in dfc0ef0/17efa5b. The logo is
instead stored under `_BRANDING_LOGO_KEY` (the `blob_` prefix `_settings()`
explicitly skips) and served only through its own dedicated
GET /settings/branding/logo endpoint -- see test 3 below, and its mutation
proof, for what actually pins that.

Run:
    pytest products/retail/tests/retail_branding_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_branding_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True
app.config["PROPAGATE_EXCEPTIONS"] = False

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from api import retail_api  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_admin_client(prefix):
    """One logged-in admin on its own company. role='admin' is waved through
    both mt_require_subsystem and mt_require_capability by role (see those
    decorators' own docstrings in commercial_runtime/identity/mt_auth.py), so
    no separate user_permissions/capability seeding is needed here -- mirrors
    retail_product_supplier_tenancy_test.py's identical helper."""
    email = f'{prefix}-{uuid.uuid4().hex[:8]}@test.local'
    password = 'BrandingTestPW1'
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
    return c, company_id


def _valid_logo_data_uri(decoded_bytes):
    """A `data:image/png;base64,...` string that decodes to exactly
    `decoded_bytes` bytes -- real image content is irrelevant here, only the
    branding_logo_set validation (format + size) is under test."""
    payload = base64.b64encode(b'\x00' * decoded_bytes).decode('ascii')
    return f'data:image/png;base64,{payload}'


# ═════════════════════════════════════════════════════════════════════════════
# 1 — defaults are sane when nothing is configured
# ═════════════════════════════════════════════════════════════════════════════

def test_defaults_are_sane_when_nothing_configured():
    """A fresh company that never touched branding must still get a coherent
    read -- empty text fields, no logo, no e-invoicing block -- so the
    receipt and the app can render their own hardcoded fallback rather than
    choke on a missing key."""
    client, _cid = _make_admin_client('brand-defaults')
    r = client.get('/api/sub/retail/settings/branding')
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'success'
    data = body['data']
    for key in ('branding_business_name', 'branding_address', 'branding_phone',
                'branding_tax_number', 'branding_receipt_header', 'branding_receipt_footer'):
        assert data[key] == '', f'{key} should default to empty, got {data[key]!r}'
    assert data['has_logo'] is False
    assert data['einvoicing_seller'] is None


# ═════════════════════════════════════════════════════════════════════════════
# 2 — branding round-trips through the settings API and is company-scoped
# ═════════════════════════════════════════════════════════════════════════════

def test_branding_round_trips_through_the_settings_api():
    client, _cid = _make_admin_client('brand-roundtrip')
    payload = {
        'branding_business_name': 'Sunrise Co-op',
        'branding_address': '12 Rainbow St, Amman',
        'branding_phone': '+962-6-000-0000',
        'branding_tax_number': 'TIN-778899',
        'branding_receipt_header': 'Fair trade, every time',
        'branding_receipt_footer': 'Members save 5% -- ask at the till',
    }
    post = client.post('/api/sub/retail/settings/branding', json=payload)
    assert post.status_code == 200, post.get_json()
    assert post.get_json()['status'] == 'success'

    get = client.get('/api/sub/retail/settings/branding')
    data = get.get_json()['data']
    for key, value in payload.items():
        assert data[key] == value, f'{key} did not round-trip: expected {value!r}, got {data[key]!r}'


def test_branding_is_company_scoped_company_a_never_reads_company_bs():
    """Company A must never read company B's branding, and vice versa --
    the same per-company isolation every other retail_settings write in
    this file already gets, proven the same way retail_product_supplier_
    tenancy_test.py proves cross-tenant isolation: two genuinely separate
    companies on one install, driven through the real HTTP routes."""
    client_a, _cid_a = _make_admin_client('brand-tenancy-a')
    client_b, _cid_b = _make_admin_client('brand-tenancy-b')

    post_a = client_a.post('/api/sub/retail/settings/branding', json={
        'branding_business_name': 'Company A Traders',
        'branding_tax_number': 'A-TIN-111',
    })
    assert post_a.status_code == 200

    # Company B never touched branding -- must still see ITS OWN defaults,
    # never company A's just-written values.
    get_b = client_b.get('/api/sub/retail/settings/branding')
    data_b = get_b.get_json()['data']
    assert data_b['branding_business_name'] == '', (
        f"company B read company A's business name: {data_b['branding_business_name']!r}"
    )
    assert data_b['branding_tax_number'] == ''

    # Company B sets its OWN branding; company A's must be unaffected.
    post_b = client_b.post('/api/sub/retail/settings/branding', json={
        'branding_business_name': 'Company B Foundation',
        'branding_tax_number': 'B-TIN-222',
    })
    assert post_b.status_code == 200

    get_a = client_a.get('/api/sub/retail/settings/branding')
    data_a = get_a.get_json()['data']
    assert data_a['branding_business_name'] == 'Company A Traders', (
        f"company A's branding changed after company B wrote its own: {data_a!r}"
    )
    assert data_a['branding_tax_number'] == 'A-TIN-111'


# ═════════════════════════════════════════════════════════════════════════════
# 3 — the logo key is NOT returned by the general settings read (perf guard)
# ═════════════════════════════════════════════════════════════════════════════

def test_logo_key_is_not_returned_by_the_general_settings_read():
    """`_settings(conn, cid)` is read straight into a dict inside
    create_sale() several times per sale. Pins that the logo -- the one
    branding value that can be hundreds of KB -- never rides along on that
    read, at the function actually called from the hot path, not merely at
    the branding-specific endpoint."""
    client, cid = _make_admin_client('brand-bloburi')
    logo_uri = _valid_logo_data_uri(2048)
    post = client.post('/api/sub/retail/settings/branding/logo', json={'logo': logo_uri})
    assert post.status_code == 200, post.get_json()
    assert post.get_json()['data']['has_logo'] is True

    conn = get_retail_conn()
    try:
        settings = retail_api._settings(conn, cid)
    finally:
        conn.close()
    assert retail_api._BRANDING_LOGO_KEY not in settings, (
        f"the blob-shaped logo key {retail_api._BRANDING_LOGO_KEY!r} leaked into "
        f"_settings()'s dict, which create_sale() reads several times per sale: "
        f"{list(settings.keys())}"
    )

    # The general settings dump route (credit_settings_get) returns this
    # exact dict verbatim -- confirm the leak is closed at the HTTP surface
    # too, not just in the function in isolation.
    credit_get = client.get('/api/sub/retail/settings/credit')
    assert retail_api._BRANDING_LOGO_KEY not in credit_get.get_json()['data']

    # The logo IS still reachable, just through its own dedicated endpoint.
    logo_get = client.get('/api/sub/retail/settings/branding/logo')
    assert logo_get.get_json()['data']['logo'] == logo_uri


# ═════════════════════════════════════════════════════════════════════════════
# 4 — an oversized logo is rejected with a clear message
# ═════════════════════════════════════════════════════════════════════════════

def test_oversized_logo_is_rejected_with_a_clear_message():
    client, _cid = _make_admin_client('brand-oversize')
    oversized = _valid_logo_data_uri(retail_api._MAX_LOGO_BYTES + 1024)
    r = client.post('/api/sub/retail/settings/branding/logo', json={'logo': oversized})
    assert r.status_code == 400
    body = r.get_json()
    assert body['status'] == 'error'
    assert 'KB' in body['message'] or 'kb' in body['message'].lower(), (
        f"rejection message should tell a shop pasting in a huge photo WHY, in plain "
        f"language, not just refuse silently: {body['message']!r}"
    )

    # Confirm it was genuinely rejected, not stored anyway -- an endpoint
    # that returns 400 but writes the row regardless would still blow the
    # performance budget test 3 guards.
    get = client.get('/api/sub/retail/settings/branding')
    assert get.get_json()['data']['has_logo'] is False


def test_logo_with_an_invalid_data_url_shape_is_rejected():
    """Same endpoint, the other half of the validation: not oversized, just
    not a recognizable image data URL at all (e.g. a bare string, or an
    attempted attribute-breakout payload) -- see _LOGO_DATA_URI_RE's own
    comment in retail_api.py for why the shape is validated this strictly."""
    client, _cid = _make_admin_client('brand-badshape')
    for bad in ('not-a-data-url', 'data:image/png;base64,not$$base64!!',
                'data:image/png" onerror="alert(1)";base64,QUFB'):
        r = client.post('/api/sub/retail/settings/branding/logo', json={'logo': bad})
        assert r.status_code == 400, f'{bad!r} should have been rejected, got {r.status_code}'


# ═════════════════════════════════════════════════════════════════════════════
# 5 — e-invoicing seller identity: read-only display, never a second writer
# ═════════════════════════════════════════════════════════════════════════════

def test_einvoicing_seller_identity_is_shown_read_only_when_configured():
    """Not one of the four required backend tests, but the plan asked this
    file to report on -- and prove -- whether displaying e-invoicing's
    seller_name/seller_tin read-only beside branding is safe. It is: this
    route only ever calls commercial_runtime.einvoicing.settings.get_setting
    (a plain parameterized SELECT), never set_setting, so there is no path
    from this screen back into that module's own tables."""
    from commercial_runtime.einvoicing import settings as einvoicing_settings

    client, cid = _make_admin_client('brand-einvoice')

    # Unconfigured company: no block at all (test 1 already pins this for a
    # fresh company; repeated here as the explicit contrast for what follows).
    before = client.get('/api/sub/retail/settings/branding').get_json()['data']
    assert before['einvoicing_seller'] is None

    conn = get_retail_conn()
    try:
        einvoicing_settings.set_setting(conn, cid, 'seller_name', 'Amman Textile Co-op')
        einvoicing_settings.set_setting(conn, cid, 'seller_tin', 'JO-TIN-55512')
        conn.commit()
    finally:
        conn.close()

    after = client.get('/api/sub/retail/settings/branding').get_json()['data']
    assert after['einvoicing_seller'] == {
        'seller_name': 'Amman Textile Co-op', 'seller_tin': 'JO-TIN-55512',
    }
    # Never silently copied into the branding fields themselves -- the two
    # identities stay independently editable, exactly as the plan requires.
    assert after['branding_business_name'] == ''
    assert after['branding_tax_number'] == ''


