"""
Aura Retail -- the `sync_conflicts` READ route: launch-readiness 2026-08-29
"the two exception queues both need ONE screen, not two" (ROADMAP.md).

Phase 6 stage 6a-ii (docs/launch-readiness/phase6-catalogue-correctness.md
Task B; database/schema.py's
`_migrate_add_sync_conflicts_and_drop_quantity_reserved`, retail v17) writes
a `sync_conflicts` row every time an incoming catalogue write is discarded
as stale -- a visible landing spot instead of the silent drop design §6
forbids. Until this route existed nothing could read it back: grep found
zero references in retail_api.py and zero in the frontend. This file proves
the read half only -- the WRITER (`SyncService._record_sync_conflict`) is
`commercial_runtime/sync/sync_service.py`'s own concern and is exercised
directly here via hand-inserted rows, the same technique
retail_oversell_exception_test.py's own "read path" test uses for
`stock_exceptions` (a RESOLVED row inserted directly to prove the read
route's own filter, not the writer).

FOUR THINGS THIS FILE PINS:
1. the route lists THIS company's conflicts;
2. it does NOT list another company's -- company scoping, the same class of
   bug just fixed in 6b79b5a (a purchase order leaking another company's
   supplier name through an unscoped join);
3. it never returns `incoming_payload` raw -- that column is a whole
   discarded record as JSON, arbitrary operator-entered data from another
   device, and is exactly the trust boundary retail_pos_name_xss_test.js /
   retail_customer_modal_xss_test.js already guard on this device's own
   catalogue;
4. a user without CAP_REPORTS is refused -- built as a real cashier account
   through the SAME production capability-seeding helper
   retail_route_capability_matrix_test.py's own `_make_user` uses, because a
   `role='admin'` session short-circuits the capability check entirely and
   cannot exercise a denial.

Self-contained bootstrap, matching retail_oversell_exception_test.py and
retail_route_capability_matrix_test.py (no shared conftest.py exists here):
one Flask app for the whole module, one company per test via the `company`
fixture so conflict rows from one test can never leak into another's
assertions.

Run:
    pytest products/retail/tests/retail_sync_conflicts_route_test.py -v
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_syncconflicts_"))
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


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    the module docstrings this file cites for why nothing here is shared
#    via a conftest.py) ───────────────────────────────────────────────────

def _new_company(role="admin"):
    email = f"syncconf-{uuid.uuid4().hex[:10]}@test.local"
    password = "SyncConflictPW1"
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, hash_password(password), role, "active"),
    )
    conn.commit()
    conn.close()
    return company_id, email, password


def _login(email, password):
    c = app.test_client()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.get_json()
    c.get("/api/sub/retail/settings/tax")   # forces the lazy schema helpers, same as its siblings
    return c


@pytest.fixture
def company():
    """One company per test -- conflict rows are company-scoped, and a
    shared company would make one test's row visible to another's 'not
    listed' assertion."""
    company_id, email, password = _new_company()
    return company_id, _login(email, password)


def _make_cashier(company_id):
    """A real cashier account in the SAME company, seeded through the SAME
    production capability-seeding helper account creation uses (matching
    retail_route_capability_matrix_test.py's own `_make_user`) -- ROLE_
    CASHIER holds retail.sell/retail.refund/retail.cash.close and NOT
    retail.reports (commercial_runtime/identity/user_accounts.py's
    ROLE_CAPABILITIES), so this is a real, live denial rather than a
    hand-rolled session that could drift from what the product actually
    gives a cashier. `role='admin'` short-circuits `session_has_capability`
    entirely -- a cashier is the only role that can genuinely exercise a
    403 here."""
    email = f"syncconf-cashier-{uuid.uuid4().hex[:10]}@test.local"
    password = "SyncConflictCashierPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-CASH01", email, hash_password(password), "cashier", "active"),
    )
    # mt_require_subsystem still demands the legacy un-namespaced grant --
    # see retail_route_capability_matrix_test.py's own `_make_user`, same
    # reasoning verbatim.
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, "cashier")
    conn.commit()
    conn.close()
    return _login(email, password)


def _insert_conflict(company_id, *, entity_type='product', entity_id=None, event_type='update',
                      local_row_version=5, incoming_row_version=3, payload=None, detected_at_utc=None):
    """Writes ONE `sync_conflicts` row directly -- the same technique
    retail_oversell_exception_test.py's own read-path test uses for
    `stock_exceptions`. This file's scope is the READ route; the writer
    (`SyncService._record_sync_conflict`) is exercised in
    commercial_runtime/sync/sync_service.py's own apply-path coverage, not
    here."""
    conflict_id = str(uuid.uuid4())
    conn = get_retail_conn()
    try:
        conn.execute(
            "INSERT INTO sync_conflicts (id, company_id, entity_type, entity_id, event_type, "
            "local_row_version, incoming_row_version, incoming_payload, detected_at_utc) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (conflict_id, company_id, entity_type, entity_id or str(uuid.uuid4()), event_type,
             local_row_version, incoming_row_version, json.dumps(payload or {}),
             detected_at_utc or '2026-08-25T10:00:00+00:00'),
        )
        conn.commit()
    finally:
        conn.close()
    return conflict_id


# A payload shaped exactly like a REAL update_product outbox event (see
# retail_api.py's update_product, `_queue_sync_event(cur, 'product', pid,
# 'update', dict(row) | {'id': pid, '_changed_fields': sorted(changed_fields)})`)
# -- `_changed_fields` present, plus a deliberately malicious `name` value so
# test 3 below has something concrete to prove never reaches the response.
MALICIOUS_NAME = '<img src=x onerror=alert(1)>'
PRODUCT_PAYLOAD = {
    'id': 'placeholder', 'sku': 'SKU-CONFLICT-1', 'name': MALICIOUS_NAME,
    'sell_price': 19.5, 'row_version': 3, 'updated_at_utc': '2026-08-24T00:00:00+00:00',
    '_changed_fields': ['name', 'sell_price'],
}


# ═════════════════════════════════════════════════════════════════════════
# 1 & 2. Company scoping -- lists THIS company's rows, never another's
# ═════════════════════════════════════════════════════════════════════════

def test_the_route_lists_this_companys_conflicts(company):
    company_id, client = company
    entity_id = str(uuid.uuid4())
    conflict_id = _insert_conflict(
        company_id, entity_type='product', entity_id=entity_id,
        event_type='update', local_row_version=5, incoming_row_version=3,
        payload=dict(PRODUCT_PAYLOAD, id=entity_id),
    )

    r = client.get('/api/sub/retail/inventory/sync-conflicts')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['count'] == 1, data
    assert len(data['conflicts']) == 1, data

    row = data['conflicts'][0]
    assert row['id'] == conflict_id
    assert row['entity_type'] == 'product'
    assert row['entity_id'] == entity_id
    assert row['event_type'] == 'update'
    assert row['local_row_version'] == 5
    assert row['incoming_row_version'] == 3
    assert row['detected_at_utc']
    assert set(row['changed_fields']) == {'name', 'sell_price'}, row['changed_fields']


def test_the_route_does_not_list_another_companys_conflicts(company):
    """The same class of bug just fixed in 6b79b5a (a purchase order
    leaking another company's supplier name through an unscoped join) --
    here the exposure would be a whole discarded catalogue edit, from a
    company this session has no relationship to at all."""
    company_id, client = company
    other_company_id, _email, _pw = _new_company()

    mine_id = _insert_conflict(company_id, entity_type='product', payload=PRODUCT_PAYLOAD)
    _insert_conflict(other_company_id, entity_type='customer', payload={
        'id': 'x', 'name': 'Someone Else Ltd', '_changed_fields': ['name'],
    })

    r = client.get('/api/sub/retail/inventory/sync-conflicts')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['count'] == 1, (
        f"expected exactly this company's own row, got {data['count']}: {data['conflicts']}"
    )
    assert data['conflicts'][0]['id'] == mine_id
    assert 'Someone Else Ltd' not in json.dumps(data), (
        "another company's conflict payload leaked into this company's response"
    )


# ═════════════════════════════════════════════════════════════════════════
# 3. incoming_payload is never returned raw
# ═════════════════════════════════════════════════════════════════════════

def test_the_route_never_returns_incoming_payload_raw(company):
    company_id, client = company
    entity_id = str(uuid.uuid4())
    _insert_conflict(
        company_id, entity_type='product', entity_id=entity_id,
        payload=dict(PRODUCT_PAYLOAD, id=entity_id),
    )

    r = client.get('/api/sub/retail/inventory/sync-conflicts')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['count'] == 1, data

    row = data['conflicts'][0]
    assert 'incoming_payload' not in row, (
        "the raw incoming_payload column name leaked into the response shape"
    )

    # Belt and braces: the whole HTTP body, not just the one key, must never
    # contain the operator-entered value that was discarded -- an attacker
    # could rename the field carrying it and this assertion would still
    # catch the value itself.
    body_text = json.dumps(data)
    assert MALICIOUS_NAME not in body_text, (
        f"the discarded payload's raw value leaked into the response body: {body_text}"
    )
    # And the field NAME the discarded edit touched IS expected to be
    # present -- this route summarises rather than omits everything.
    assert 'name' in row['changed_fields']


# ═════════════════════════════════════════════════════════════════════════
# 4. Capability gate -- CAP_REPORTS, refused without it
# ═════════════════════════════════════════════════════════════════════════

def test_a_user_without_reports_capability_is_refused(company):
    company_id, _admin_client = company
    _insert_conflict(company_id, entity_type='product', payload=PRODUCT_PAYLOAD)

    cashier = _make_cashier(company_id)
    r = cashier.get('/api/sub/retail/inventory/sync-conflicts')
    assert r.status_code == 403, r.get_json()
