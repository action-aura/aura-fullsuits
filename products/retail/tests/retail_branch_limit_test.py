"""Aura Retail -- the `max_branches` licence entitlement (2026-09-05 price
list: a new branch is a paid add-on, EXTRA_BRANCH, 250 JOD).

The licensing vehicle already carries per-licence entitlements end to end
with NO schema change: Owner's `resolve_entitlements()` merges plan -> add-on
-> per-licence override, the signed assertion carries `entitlements`, the
till stores it as `licensing_state.entitlements_json`, and
`flask_guard.make_entitlement_reader` reads it per request. This file is the
till-side enforcement: `create_branch` refuses once the company already has
`max_branches` active branches.

ABSENT or <= 0 must mean NO LIMIT, never zero branches: absent is a licence
issued before this entitlement existed, and 0 is Owner's deny-by-default
value for a plan that never set it -- either meaning "block every branch"
would lock an existing shop out of its own branches the day this shipped.
Only a positive integer is a real limit. A malformed value degrades to no
limit too, same posture as every other entitlement gate in this codebase.

Run:
    pytest products/retail/tests/retail_branch_limit_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_branch_limit_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from api.retail_api import BRANCH_LIMIT_MESSAGE  # noqa: E402

OWNER_EMAIL = 'branch-limit-owner@test.local'
OWNER_PASSWORD = 'BranchLimitPW1'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _owner():
    """The one admin this install allows -- same shape as
    retail_employee_management_test.py's `_owner()`."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': OWNER_EMAIL, 'password': OWNER_PASSWORD,
    })
    if r.status_code == 409:
        r = client.post('/api/auth/login', json={'email': OWNER_EMAIL, 'password': OWNER_PASSWORD})
    assert r.status_code == 200, r.get_json()
    return client


def _set_entitlements(d):
    """Re-seeds the licence record with `entitlements=d` -- the guard
    (`read_license_entitlements`) re-reads `licensing.db` on every request,
    so this takes effect on the very next call, no restart needed."""
    seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS", entitlements=d)


def _branches(owner):
    return owner.get('/api/sub/retail/branches').get_json()['data']


def _create_branch(owner, name=None):
    return owner.post('/api/sub/retail/branches', json={'name': name or f'Branch-{uuid.uuid4().hex[:8]}'})


# ═════════════════════════════════════════════════════════════════════════════
# Absent / zero -- no limit, never zero branches
# ═════════════════════════════════════════════════════════════════════════════

def test_no_entitlement_means_no_limit():
    owner = _owner()
    _set_entitlements({})
    assert _create_branch(owner).status_code == 200
    assert _create_branch(owner).status_code == 200


def test_zero_means_no_limit_not_zero_branches():
    """Pins the deliberate reading of Owner's deny-by-default 0: a plan that
    never set `max_branches` must not lock the shop out of ever adding one."""
    owner = _owner()
    _set_entitlements({"max_branches": 0})
    assert _create_branch(owner).status_code == 200


# ═════════════════════════════════════════════════════════════════════════════
# A real limit
# ═════════════════════════════════════════════════════════════════════════════

def test_limit_one_refuses_the_second_branch():
    owner = _owner()
    existing = _branches(owner)
    assert len(existing) >= 1, "premise failed -- no branches exist yet to be at the limit of"
    limit = len(existing)
    _set_entitlements({"max_branches": limit})

    before = _branches(owner)
    res = _create_branch(owner)
    assert res.status_code == 403
    body = res.get_json()
    assert body['reason_code'] == 'BRANCH_LIMIT'
    assert body['message'] == BRANCH_LIMIT_MESSAGE
    assert body['data']['limit'] == limit
    assert _branches(owner) == before, "a refused create must not change the branch count"


def test_limit_admits_exactly_up_to_the_limit():
    owner = _owner()
    current = len(_branches(owner))
    limit = current + 2
    _set_entitlements({"max_branches": limit})

    assert _create_branch(owner).status_code == 200
    assert _create_branch(owner).status_code == 200
    assert len(_branches(owner)) == limit

    res = _create_branch(owner)
    assert res.status_code == 403
    assert res.get_json()['data']['limit'] == limit
    assert len(_branches(owner)) == limit, "the refused third create must not land"


def test_junk_entitlement_degrades_to_no_limit():
    """A malformed entitlement (wrong type, unparseable) must never BLOCK --
    only a genuine positive integer is a real limit. Same posture as
    `_branch_limit`'s own docstring: degrade, never block."""
    owner = _owner()
    _set_entitlements({"max_branches": "lots"})
    assert _create_branch(owner).status_code == 200


def test_reading_branches_is_never_limited():
    owner = _owner()
    _set_entitlements({"max_branches": 1})
    assert owner.get('/api/sub/retail/branches').status_code == 200
