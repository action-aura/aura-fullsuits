"""
Aura Retail -- a page limit must be clamped at BOTH ends, for every spelling of
the query value, not just the one an earlier test happened to pick.

── THE DEFECT ────────────────────────────────────────────────────────────────
`GET /sales/recent` clamped with `min(limit, 200)`. That is not a ceiling.
SQLite reads a NEGATIVE LIMIT as UNBOUNDED, so:

    GET /sales/recent?limit=-1     ->  200, every sale the shop has ever rung

for a cashier holding retail.sell / retail.refund / retail.cash.close and NOT
retail.reports -- through the route that exists to answer a narrow till
question, while /dashboard/stats and /reports/summary both return 403 to the
same session. One character.

`int(request.args.get('limit'))` was also unguarded, so `?limit=abc` was a 500.

── WHY THE EXISTING TESTS DID NOT CATCH IT ───────────────────────────────────
Three tests sat green over this. Each asserted an OUTCOME for the single input
`limit=100000`, which the broken clamp handled correctly -- it is only the
NEGATIVE branch that is unbounded. One of them states in its own docstring that
"every response is capped", which was false at the time it was written.

That is this programme's most repeated failure shape: asserting an outcome for
one input where the PROPERTY is what needs asserting. So this file tests the
property, over a table of hostile spellings, and asserts the invariant rather
than any particular result.

The clamp was extracted into `retail_api.clamp_page_limit` for exactly this
reason: an inline expression can only ever be tested one call at a time, and
the second call site (`list_audit_log`) had the correct shape all along while
the first had half of it. Routing both through one function is what stops a
future third caller inheriting half again.
"""
import os
import shutil
import sys
import tempfile
import uuid

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'products', 'retail', 'backend'))

DATA = tempfile.mkdtemp(prefix='aura_limit_clamp_')
os.environ['AURA_APP_DATA'] = DATA
os.environ.pop('AURA_DEV', None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code='AURA_RETAIL', platform='WINDOWS')
os.environ.pop('AURA_RETAIL_DEMO_MODE', None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config['TESTING'] = True

import api.retail_api as retail_api  # noqa: E402
from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

API = '/api/sub/retail'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── the property, asserted directly ──────────────────────────────────────────
#
# Every one of these must come back inside [1, ceiling]. The table is
# deliberately about the SHAPE of the input rather than a few round numbers:
# each row is a way a real caller (or a real attacker) writes a page size.

HOSTILE_LIMITS = [
    '-1',                    # the shipped bug: SQLite reads this as UNBOUNDED
    '-2', '-999999',         # not a special case of -1
    '0',                     # SQLite returns nothing; a blank screen, not an error
    '',                      # ?limit= with no value
    'abc',                   # was a 500
    '1e9',                   # int() rejects this spelling of a big number
    '9' * 400,               # int() accepts it; the ceiling must still hold
    '  25  ',                # int() tolerates surrounding whitespace
    '100000',                # the ONE input the old tests used
    None,                    # parameter absent entirely
]


@pytest.mark.parametrize('raw', HOSTILE_LIMITS)
@pytest.mark.parametrize('ceiling', [1, 50, 200, 500])
def test_the_clamp_holds_for_every_spelling_of_a_page_size(raw, ceiling):
    """The invariant, not a result: whatever goes in, what comes out is a
    usable page size. Parametrised over the ceiling too, because a clamp that
    only works for one ceiling is a coincidence."""
    got = retail_api.clamp_page_limit(raw, 50, ceiling)
    assert isinstance(got, int), f'{raw!r} produced {got!r}'
    assert 1 <= got <= ceiling, f'{raw!r} with ceiling {ceiling} produced {got}'


def test_a_negative_limit_is_not_merely_survived_but_is_the_case_that_broke():
    """Pinned on its own, separately from the table above, so that shrinking
    the table can never quietly remove the one input this file exists for.

    `min(-1, 200)` is `-1`, and `LIMIT -1` in SQLite means no limit at all.
    That is the whole defect."""
    assert min(-1, 200) == -1, 'if this ever fails, the premise below is stale'
    assert retail_api.clamp_page_limit('-1', 50, 200) == 1


def test_an_unparseable_limit_falls_back_rather_than_raising():
    """A malformed page size is a caller mistake, not a server error. Raising
    here produced a 500, which tells an operator something is broken when
    nothing is."""
    assert retail_api.clamp_page_limit('abc', 50, 200) == 50
    assert retail_api.clamp_page_limit(None, 50, 200) == 50


def test_a_valid_limit_is_passed_through_untouched():
    """The anti-vacuity guard. Every assertion above is satisfied by a function
    that ignores its input and returns 1, so this pins that the clamp actually
    clamps rather than flattens."""
    assert retail_api.clamp_page_limit('37', 50, 200) == 37
    assert retail_api.clamp_page_limit(37, 50, 200) == 37


# ── and through the real route, end to end ───────────────────────────────────

def _make_user(role, company_id):
    email = f'clamp-{role}-{uuid.uuid4().hex[:10]}@test.local'
    password = 'ClampTestPW1'
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        'INSERT INTO users (id, uid, company_id, employee_id, email, password_hash, role, status, '
        'require_password_change) VALUES (?,?,?,?,?,?,?,?,0)',
        (user_id, str(uuid.uuid4()), company_id, f'EMP-{uuid.uuid4().hex[:6].upper()}', email,
         hash_password(password), role, 'active'))
    conn.execute('INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)',
                 (str(uuid.uuid4()), user_id, 'retail', 'full'))
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_data(as_text=True)
    return client


@pytest.fixture(scope='module')
def shop():
    company_id = str(uuid.uuid4())
    admin = _make_user('admin', company_id)
    admin.get(f'{API}/settings/tax')
    return {'company_id': company_id, 'admin': admin,
            'cashier': _make_user('cashier', company_id)}


@pytest.mark.parametrize('raw', ['-1', '0', 'abc', '99999999'])
def test_the_route_answers_rather_than_erroring_for_any_page_size(shop, raw):
    """Through the real route, for the caller who does not hold retail.reports.

    Asserting 200-not-500 rather than a row count on purpose: the row count
    depends on how many sales this shared fixture happens to hold, and a test
    whose result moves with unrelated fixtures is a test that gets deleted
    later. The row-count ceiling is the property test above; this one pins that
    no spelling reaches the database as a raw value."""
    r = shop['cashier'].get(f'{API}/sales/recent?limit={raw}')
    assert r.status_code == 200, f'?limit={raw} -> {r.status_code}: {r.get_data(as_text=True)[:300]}'
    body = r.get_json()
    rows = (body or {}).get('data') or []
    assert len(rows) <= retail_api.SALES_HISTORY_MAX_LIMIT
