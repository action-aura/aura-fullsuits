"""
Aura Retail -- cash-drawer kick on a completed sale (checkout, NOT settings).

THE GAP this closes: `core/retail/escpos_receipt.py` (`drawer_kick()`) and
`escpos_transport.py` (the Windows RAW print-spooler transport) are real and
already tested on their own (retail_escpos_receipt_test.py,
retail_escpos_transport_test.py), but until this route existed the ONLY
caller of either module was `printer_test` (retail_printer_routes_test.py) --
a Settings screen button an administrator presses by hand. So in a real shop
the drawer never opened when a cashier rang a cash sale; the whole reason
this file exists is that `subsystem-retail.js`'s own comment used to say so
outright ("this sub-section is the only thing that can reach a cash-drawer
kick, and it never touches the sale path"). This file proves the route that
closes that gap:

    POST /api/sub/retail/printer/kick

THE SERVER DECIDES, NOT THE CLIENT. The client sends only {sale_id, printer};
this route looks the sale up itself, COMPANY-SCOPED, and refuses anything
that is not a cash sale belonging to the caller's own company -- see
printer_kick's own docstring in retail_api.py for the full reasoning,
including why it is gated CAP_SELL rather than the CAP_EMPLOYEES every other
route in that hardware-printer section uses.

Self-contained bootstrap, matching retail_printer_routes_test.py /
retail_route_capability_matrix_test.py (no shared conftest.py exists here).
One file per process (AUDIT-010): these boot an app at import.

Run:
    pytest products/retail/tests/retail_printer_kick_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_printer_kick_"))
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
    email = f"kick-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "PrinterKickPW1"  # pragma: allowlist secret -- throwaway test fixture, not a real credential
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


def _seed_shop():
    """An admin plus one in-stock product, in a fresh company -- same shape
    as retail_route_capability_matrix_test.py's own `_seed_shop`."""
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

    r = admin.post('/api/sub/retail/products', json={
        'name': 'Printer Kick Test Product', 'sku': f'KICK-{uuid.uuid4().hex[:8]}',
        'sell_price': 10.0, 'tax_rate': 0, 'initial_stock': 1000,
    })
    assert r.status_code == 200, r.get_json()
    product_id = r.get_json()['data']['id']
    return admin, company_id, product_id


def _sale(client, product_id, *, payment_method='cash'):
    """Rings ONE real sale through the real route (never a raw INSERT), so
    `payment_method` on the row is exactly what create_sale itself writes --
    the column printer_kick reads to decide whether to open the drawer."""
    payload = {
        'items': [{'product_id': product_id, 'quantity': 1}],
        'amount_paid': 1000,
        'payment_method': payment_method,
        'idempotency_key': str(uuid.uuid4()),
    }
    r = client.post('/api/sub/retail/sales', json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']['id']


@pytest.fixture(scope='module')
def shop():
    return _seed_shop()


@pytest.fixture(scope='module')
def other_company_admin():
    """A second, unrelated company. The only fixture this file needs beyond
    `shop` -- proves company scoping (test 3) is real, not assumed."""
    admin, company_id = _make_user('admin')
    return admin, company_id


# ── 1. Never a 500, on whichever platform actually runs this test ───────────

def test_cash_sale_kick_is_never_a_500_even_when_unreachable(shop):
    """The honest result on the platform CI actually executes (non-Windows):
    200, `kicked: False`, with a reason naming the platform -- never a
    traceback. On a real Windows dev machine (this suite's own environment
    per CLAUDE.md) the printer name below does not exist, so the honest
    result there is instead a real OpenPrinter failure, still surfaced as
    `kicked: False`, never a 500 -- see
    test_devices_route_returns_a_list's identical branch in
    retail_printer_routes_test.py for the same "assert what THIS platform
    actually does" discipline, rather than asserting one platform's answer
    unconditionally."""
    admin, _, product_id = shop
    sale_id = _sale(admin, product_id, payment_method='cash')
    r = admin.post('/api/sub/retail/printer/kick', json={
        'sale_id': sale_id,
        'printer': f'Nonexistent Kick Printer {uuid.uuid4().hex[:8]}',
    })
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['kicked'] is False, d
    if sys.platform != 'win32':
        assert 'Windows print spooler' in d['reason'], d
        assert repr(sys.platform) in d['reason'], d
    else:
        assert d['reason'], d  # some OpenPrinter-shaped explanation, never blank


# ── 2. A non-cash sale refuses and never touches the transport ──────────────
# MUTATION-PROVEN (see the task's own verification step): breaking the
# `payment_method != 'cash'` guard in retail_api.py's printer_kick makes this
# go RED, restoring it makes it GREEN again -- both directions quoted in the
# final report. Counting the transport calls, not just reading the response
# body, is what makes this a real proof: a body-only assertion cannot tell
# "refused because non-cash" from "refused because non-Windows", which is
# exactly the shape a broken guard could pass under.

def test_non_cash_sale_refuses_without_touching_the_transport(shop, monkeypatch):
    admin, _, product_id = shop
    sale_id = _sale(admin, product_id, payment_method='card')
    calls = []
    monkeypatch.setattr(escpos_transport, 'send_raw', lambda *a, **k: calls.append((a, k)))

    r = admin.post('/api/sub/retail/printer/kick', json={'sale_id': sale_id, 'printer': 'Any Printer'})

    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['kicked'] is False, d
    assert 'card' in d['reason'], d
    assert calls == [], "transport must NOT be invoked for a non-cash sale"


# ── 3. Company scoping is real, not assumed ──────────────────────────────────

def test_sale_from_another_company_is_404(shop, other_company_admin):
    admin_a, _, product_a = shop
    sale_id = _sale(admin_a, product_a, payment_method='cash')
    admin_b, _ = other_company_admin

    r = admin_b.post('/api/sub/retail/printer/kick', json={'sale_id': sale_id, 'printer': 'Any Printer'})

    assert r.status_code == 404, r.get_json()


# ── 4. No printer configured -> no kick, transport untouched ────────────────

def test_no_printer_configured_skips_kick_and_transport_not_invoked(shop, monkeypatch):
    admin, _, product_id = shop
    sale_id = _sale(admin, product_id, payment_method='cash')
    calls = []
    monkeypatch.setattr(escpos_transport, 'send_raw', lambda *a, **k: calls.append((a, k)))

    r = admin.post('/api/sub/retail/printer/kick', json={'sale_id': sale_id, 'printer': ''})

    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['kicked'] is False, d
    assert calls == []


# ── 5. Capability gate, both directions ──────────────────────────────────────
# The deny half is the obvious one; the allow half is what proves the gate is
# not simply refusing everyone (a decorator that always returns 403 would
# pass a deny-only test just as happily).

def test_capability_gate_both_directions(shop):
    admin, company_id, product_id = shop
    sale_id = _sale(admin, product_id, payment_method='cash')

    denied, _ = _make_user('cashier', company_id=company_id, capabilities={user_accounts.CAP_SELL: 'none'})
    r_denied = denied.post('/api/sub/retail/printer/kick', json={'sale_id': sale_id, 'printer': ''})
    assert r_denied.status_code == 403, r_denied.get_json()

    allowed, _ = _make_user('cashier', company_id=company_id)
    r_allowed = allowed.post('/api/sub/retail/printer/kick', json={'sale_id': sale_id, 'printer': ''})
    assert r_allowed.status_code == 200, r_allowed.get_json()


# ── 6. THE test that proves the feature works, not merely fails politely ────
# MUTATION-PROVEN (see the task's own verification step): breaking either the
# `send_raw` call or swapping `drawer_kick()` for a receipt render makes this
# go RED, restoring makes it GREEN -- both directions quoted in the final
# report.

def test_cash_sale_calls_transport_exactly_once_with_only_the_kick_bytes(shop, monkeypatch):
    """Forces the platform check to pass (`sys.platform` is patched to
    'win32' regardless of what this test happens to run on) and stubs
    `send_raw` so no real hardware is ever touched, then asserts the
    transport was called EXACTLY once with EXACTLY `drawer_kick()`'s bytes --
    a kick only, no receipt body, matching `render_receipt` never being
    called anywhere in printer_kick."""
    admin, _, product_id = shop
    sale_id = _sale(admin, product_id, payment_method='cash')
    monkeypatch.setattr(sys, 'platform', 'win32')
    calls = []
    monkeypatch.setattr(escpos_transport, 'send_raw', lambda printer, payload: calls.append((printer, payload)))

    r = admin.post('/api/sub/retail/printer/kick', json={'sale_id': sale_id, 'printer': 'Real Printer'})

    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['kicked'] is True, d
    assert len(calls) == 1, calls
    printer_name, payload = calls[0]
    assert printer_name == 'Real Printer'
    assert payload == escpos_receipt.drawer_kick(), payload
