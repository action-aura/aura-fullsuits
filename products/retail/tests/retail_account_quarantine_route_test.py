"""
Aura Retail -- the registry `sync_apply_quarantine` READ route
(`GET /api/sub/retail/account-quarantine`).

Launch-readiness account-hierarchy design §2.4/§4.2, wave D: "the quarantine
screen is a PREREQUISITE, not a nice-to-have". registry.db's OWN
`sync_apply_quarantine` (v6, registry_quarantine_schema.py) parks `user`/
`user_permission` sync-apply events the registry-configured SyncService
instance could not apply -- `duplicate_email`, `duplicate_employee_id`
(design §2.2's silent-fork failure, which delegation turns from rare into
routine) and `missing_parent:user`. Before this route existed nothing could
read it back at all -- the identical "written to the database, invisible
through every interface" gap retail_sync_conflicts_route_test.py's own
`sync_conflicts` route closed for the catalogue side, mirrored here for the
identity side (see that file's own docstring for the shared history: "the
two exception queues both need ONE screen, not two", ROADMAP.md 2026-08-29).

This file proves the read half only, using the same technique
retail_sync_conflicts_route_test.py and retail_oversell_exception_test.py
use for their own tables: rows are hand-inserted directly into
`sync_apply_quarantine`, never produced by actually forcing a real sync
collision. The WRITER (`SyncService._quarantine_apply_event` and its two
`user`/`user_permission` call sites in commercial_runtime/sync/
sync_service.py) is that module's own concern and is exercised there.

WHAT THIS FILE PROVES, AND WHY THERE IS NO "OTHER COMPANY" TEST:

`sync_apply_quarantine` carries NO `company_id` column at all (registry_
quarantine_schema.py's own CREATE TABLE) -- unlike `sync_conflicts`
(retail.db, always company-scoped). The route's own docstring
(retail_api.py::list_account_quarantine) states the reasoning this file
pins instead: registry.db is structurally single-company per install,
because `create_admin` gates on `role='admin'` with no company_id predicate
at all -- "no valid admin exists yet" means globally, not per company. A
"does not leak another company's row" test would therefore be testing a
scenario this product's real onboarding flow cannot produce; what this file
proves instead is the two properties that ARE real regardless of company
count: the raw `payload` column (which DOES carry whatever the colliding
event's writer put in it, including in principle a credential field) never
reaches the response, and the capability gate is real.

Run:
    pytest products/retail/tests/retail_account_quarantine_route_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_acctquarantine_"))
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


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention, same
#    as retail_sync_conflicts_route_test.py -- no shared conftest.py here) ──

def _new_company(role="admin"):
    email = f"acctq-{uuid.uuid4().hex[:10]}@test.local"
    password = "AcctQuarantinePW1"
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


def _clear_quarantine():
    """`sync_apply_quarantine` carries no `company_id` (see the module
    docstring's "WHAT THIS FILE PROVES" section) -- rows accumulate GLOBALLY
    across this whole test module's shared registry.db, exactly as they
    would across a real single-company install. Without this, an earlier
    test's row is still sitting there when a later test asserts an exact
    `count`, which is a property of THIS test's own fixtures, not of the
    table's real, unscoped lifetime."""
    conn = registry_conn()
    try:
        conn.execute("DELETE FROM sync_apply_quarantine")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def owner():
    _clear_quarantine()
    company_id, email, password = _new_company()
    return company_id, _login(email, password)


def _make_cashier(company_id):
    """A real cashier account, seeded through the SAME production
    capability-seeding helper account creation uses (matching
    retail_sync_conflicts_route_test.py's own `_make_cashier`) --
    ROLE_CASHIER does not hold retail.reports, so this is a real, live
    denial rather than a hand-rolled session that could drift from what
    the product actually gives a cashier."""
    email = f"acctq-cashier-{uuid.uuid4().hex[:10]}@test.local"
    password = "AcctQuarantineCashierPW1"
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-CASH01", email, hash_password(password), "cashier", "active"),
    )
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, "cashier")
    conn.commit()
    conn.close()
    return _login(email, password)


def _insert_quarantine(*, entity_type='user', entity_id=None, event_type='create',
                        reason='duplicate_email', detail='email=...',
                        payload=None, quarantined_at=None):
    """Writes ONE `sync_apply_quarantine` row directly into registry.db --
    this route's own read scope, never retail.db (contrast
    retail_sync_conflicts_route_test.py's `_insert_conflict`, which targets
    retail.db's identically-named-in-spirit table). Mirrors the exact shape
    `SyncService._quarantine_apply_event` writes."""
    row_entity_id = entity_id or str(uuid.uuid4())
    conn = registry_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO sync_apply_quarantine "
            "(entity_id, entity_type, event_type, payload, reason, detail, quarantined_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (row_entity_id, entity_type, event_type, json.dumps(payload or {}), reason, detail,
             quarantined_at or '2026-08-26T09:15:00'),
        )
        conn.commit()
    finally:
        conn.close()
    return row_entity_id


# A payload shaped like a real `user` create event -- carries fields that
# must NEVER reach the response, the same trust boundary
# retail_sync_conflicts_route_test.py's MALICIOUS_NAME pins for the
# catalogue side. `password_hash`/`pin_hash` are named explicitly because
# `_quarantine_apply_event`'s own docstring promises `detail` never carries
# either -- this proves the ROUTE holds that promise too, by never reading
# the `payload` column at all.
SENSITIVE_PAYLOAD = {
    'uid': 'wire-uid-1', 'email': 'ghost@shop.jo', 'employee_id': 'EMP-0005',
    'password_hash': 'THIS_MUST_NEVER_LEAK', 'pin_hash': 'THIS_MUST_NEVER_LEAK_EITHER',
}


# ═════════════════════════════════════════════════════════════════════════
# 1. The route lists quarantined user/user_permission rows
# ═════════════════════════════════════════════════════════════════════════

def test_the_route_lists_a_quarantined_user_row(owner):
    _company_id, client = owner
    entity_id = _insert_quarantine(
        entity_type='user', entity_id='wire-uid-1', event_type='create',
        reason='duplicate_email', detail="email='ghost@shop.jo' already registered to a different account locally",
        payload=SENSITIVE_PAYLOAD,
    )

    r = client.get('/api/sub/retail/account-quarantine')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['count'] == 1, data
    item = data['items'][0]
    assert item['entity_type'] == 'user'
    assert item['entity_id'] == entity_id
    assert item['event_type'] == 'create'
    assert item['reason'] == 'duplicate_email'
    assert item['detail'] and 'ghost@shop.jo' in item['detail']
    assert item['quarantined_at']


def test_the_route_lists_a_quarantined_user_permission_row(owner):
    _company_id, client = owner
    entity_id = _insert_quarantine(
        entity_type='user_permission', entity_id='wire-uid-2:retail.employees', event_type='create',
        reason='missing_parent:user', detail="user_uid='wire-uid-2' not found locally",
    )

    r = client.get('/api/sub/retail/account-quarantine')
    assert r.status_code == 200, r.get_json()
    items = r.get_json()['data']['items']
    assert any(i['entity_id'] == entity_id and i['reason'] == 'missing_parent:user' for i in items), items


def test_the_route_orders_most_recently_quarantined_first(owner):
    _company_id, client = owner
    older = _insert_quarantine(reason='duplicate_email', detail='older', quarantined_at='2026-08-20T00:00:00')
    newer = _insert_quarantine(reason='duplicate_email', detail='newer', quarantined_at='2026-08-25T00:00:00')

    items = client.get('/api/sub/retail/account-quarantine').get_json()['data']['items']
    ids = [i['entity_id'] for i in items]
    assert ids.index(newer) < ids.index(older), items


# ═════════════════════════════════════════════════════════════════════════
# 2. The raw payload column never reaches the response
# ═════════════════════════════════════════════════════════════════════════

def test_the_route_never_returns_the_raw_payload_column(owner):
    _company_id, client = owner
    _insert_quarantine(
        entity_type='user', event_type='create', reason='duplicate_email',
        detail="email='ghost@shop.jo' already registered", payload=SENSITIVE_PAYLOAD,
    )

    r = client.get('/api/sub/retail/account-quarantine')
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['count'] == 1, data

    item = data['items'][0]
    assert 'payload' not in item, "the raw payload column name leaked into the response shape"

    body_text = json.dumps(data)
    assert 'THIS_MUST_NEVER_LEAK' not in body_text, \
        f"a credential field from the quarantined payload leaked into the response: {body_text}"
    assert 'THIS_MUST_NEVER_LEAK_EITHER' not in body_text


# ═════════════════════════════════════════════════════════════════════════
# 3. Entity-type filter -- this route surfaces the account/permission
#    quarantine only, never any other entity type that might (in
#    principle) share this table
# ═════════════════════════════════════════════════════════════════════════

def test_the_route_filters_to_user_and_user_permission_entity_types(owner):
    _company_id, client = owner
    _insert_quarantine(entity_type='user', reason='duplicate_email', detail='kept-1')
    _insert_quarantine(entity_type='user_permission', reason='missing_parent:user', detail='kept-2')
    _insert_quarantine(entity_type='sale_item', event_type='create', reason='missing_parent:sale', detail='not-ours')

    items = client.get('/api/sub/retail/account-quarantine').get_json()['data']['items']
    assert len(items) == 2, items
    assert {'user', 'user_permission'} == {i['entity_type'] for i in items}


# ═════════════════════════════════════════════════════════════════════════
# 4. Capability gate -- CAP_REPORTS, refused without it
# ═════════════════════════════════════════════════════════════════════════

def test_a_user_without_reports_capability_is_refused(owner):
    company_id, _owner_client = owner
    _insert_quarantine(reason='duplicate_email', detail='x')

    cashier = _make_cashier(company_id)
    r = cashier.get('/api/sub/retail/account-quarantine')
    assert r.status_code == 403, r.get_json()


def test_an_anonymous_caller_is_refused(owner):
    _company_id, _client = owner
    anonymous = app.test_client()
    r = anonymous.get('/api/sub/retail/account-quarantine')
    assert r.status_code == 401
