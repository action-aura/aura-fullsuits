"""Registry v4 -- Defect 2 (launch-readiness Phase 5 verification, HIGH):
a half-failed activation-time rebind used to leave a RUNNING process serving
the split state for the rest of its lifetime.

`docs/launch-readiness/phase5-prerequisites.md` §1: "If retail's half fails,
identity's half must be rolled back or the app must refuse to serve." The
boot-time guard (`app.py::_converge_and_refuse_to_serve_if_stuck`, see
commercial_runtime/identity/tests/test_registry_v4_window.py) already
honours that. This file proves the ACTIVATION seam does too:
`_on_licence_activated()` (the real `on_activation_success` hook
`make_licensing_blueprint` wires into `/activate`) used to call both rebind
halves and discard both status dicts. If identity's half committed and
retail's half then failed -- a fabricated, irreconcilable two-tenant
retail.db is this file's stand-in for "retail genuinely cannot converge";
a locked retail.db under a concurrent sale is the ordinary real-world
trigger -- nothing noticed, and the process kept serving `200 OK` with an
apparently empty shop for as long as it stayed up. Only the NEXT restart's
boot-time guard would have caught it.

THE FIX: `_on_licence_activated` now runs the SAME predicate
(`_detect_split_state_reason`) the boot guard uses, and when it reports the
process is stuck, a `before_request` hook refuses every request (except
`/api/health`) with a `503` naming what happened, until convergence
succeeds -- on a later request or at the next boot, whichever comes first.

MUTATION PROOF. Reverting Defect 2's fix -- `_on_licence_activated` back to
calling both rebind halves and discarding both status dicts -- reproduces
the silent catastrophe over real HTTP, verbatim:

    LOGIN STATUS: 200
    PRODUCTS STATUS: 200
    PRODUCTS BODY: {"data": [], "status": "success"}

and with the fix restored, the same sequence against the same fixture:

    LOGIN STATUS: 503
    PRODUCTS STATUS: 503

`test_the_shop_is_never_served_empty_over_http_while_the_process_is_split`
below is the test that pins exactly that, and it asserts the HTTP outcome
FIRST, before it looks at any of `app.py`'s internal state. Its sibling
`test_a_login_during_the_split_state_gets_refused_not_a_silently_empty_shop`
checks the internal bookkeeping on the way past, which is useful for
diagnosis but means that under a reverted fix it fails on
`_split_state_refusal['reason'] is not None` and never reaches its own HTTP
assertion at all. A white-box tripwire firing first is fine; a white-box
tripwire firing INSTEAD is how a suite ends up unable to state, in its own
failure output, what a shop would actually have experienced.

Run (ONE TEST FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest products/retail/tests/retail_registry_v4_activation_split_state_test.py -v
"""
import ast
import json
import os
import shutil
import sqlite3
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_registry_v4_splitstate_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_SYNC_RELAY_URL", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

REGISTRY_DB = DATA / 'database' / 'registry.db'
RETAIL_DB = DATA / 'database' / 'subsystems' / 'retail.db'
LICENSING_DB = DATA / 'database' / 'subsystems' / 'licensing.db'

OWNER_COMPANY_ID = '22222222-3333-4444-5555-666666666666'
SECOND_TENANT_ID = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _write_licence_state(license_public_id):
    """Same helper/shape as commercial_runtime/identity/tests/
    test_registry_v4_window.py."""
    os.makedirs(LICENSING_DB.parent, exist_ok=True)
    conn = sqlite3.connect(str(LICENSING_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS licensing_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            licensing_schema_version INTEGER NOT NULL,
            product_code TEXT NOT NULL,
            platform TEXT NOT NULL,
            current_state TEXT NOT NULL,
            owner_installation_id TEXT,
            assertion_envelope_json TEXT,
            updated_at TEXT NOT NULL
        )
    """)
    payload = {
        'assertion_id': str(uuid.uuid4()),
        'product_code': 'AURA_RETAIL',
        'license_public_id': license_public_id,
        'installation_public_id': str(uuid.uuid4()),
    }
    conn.execute('DELETE FROM licensing_state')
    conn.execute(
        'INSERT INTO licensing_state (id, licensing_schema_version, product_code, platform, '
        'current_state, owner_installation_id, assertion_envelope_json, updated_at) '
        'VALUES (1,1,?,?,?,?,?,?)',
        ('AURA_RETAIL', 'WINDOWS', 'ACTIVE_ONLINE', payload['installation_public_id'],
         json.dumps({'payload': payload}), '2026-08-24T00:00:00'),
    )
    conn.commit()
    conn.close()


def _onboard_admin(email, password='SplitStatePW1', company_name='Split State Co'):
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': email, 'password': password, 'company_name': company_name,
    })
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _registry_company_id_for(email):
    conn = sqlite3.connect(str(REGISTRY_DB))
    row = conn.execute('SELECT company_id FROM users WHERE LOWER(email)=?', (email.lower(),)).fetchone()
    conn.close()
    assert row, f'onboarding did not create a users row for {email!r}'
    return row[0]


def _insert_product(company_id, sku, name='Split State Widget'):
    conn = sqlite3.connect(str(RETAIL_DB))
    conn.execute(
        "INSERT INTO products (company_id, sku, name, sell_price) VALUES (?,?,?,?)",
        (company_id, sku, name, 9.99),
    )
    conn.commit()
    conn.close()


def _reset_databases():
    for db_path in (REGISTRY_DB, RETAIL_DB, LICENSING_DB):
        for suffix in ('', '-wal', '-shm'):
            f = Path(str(db_path) + suffix)
            if f.exists():
                f.unlink()
    _app_module._split_state_refusal['reason'] = None  # clear leftover state between test functions
    _app_module.init_app()


def test_a_login_during_the_split_state_gets_refused_not_a_silently_empty_shop():
    """Drives the exact scenario Defect 2 describes: identity's rebind
    UNAMBIGUOUSLY succeeds (registry.db is genuinely single-tenant and
    matches the licence), retail's rebind organically FAILS because
    retail.db holds a second, irreconcilable tenant's rows -- the same
    fabrication technique test_registry_v4_window.py's boot-refusal test
    uses, here fired through the ACTIVATION seam instead of a restart.
    """
    _reset_databases()
    email = 'owner-splitstate-1@test.local'
    _onboard_admin(email)
    legacy_company_id = _registry_company_id_for(email)
    _insert_product(legacy_company_id, sku='WIDGET-SPLITSTATE-1')
    # A second, unrelated tenant's data in retail.db -- CLAUDE.md's "one
    # install can host more than one company" made real, but one identity
    # never adopted and never will, so retail's own rebind cannot pick a
    # tenant to move and reports 'failed'.
    _insert_product(SECOND_TENANT_ID, sku='WIDGET-SPLITSTATE-1-OTHER')

    _write_licence_state(OWNER_COMPANY_ID)

    assert _app_module._split_state_refusal['reason'] is None, 'fixture bug: already refusing before activation'
    _app_module._on_licence_activated()  # the REAL on_activation_success hook

    # Sanity: identity really did move (unambiguously, matching the
    # licence), and retail really did NOT -- otherwise this test would not
    # exercise Defect 2 at all.
    reg_conn = sqlite3.connect(str(REGISTRY_DB))
    assert reg_conn.execute(
        'SELECT company_id FROM users WHERE LOWER(email)=?', (email.lower(),)
    ).fetchone()[0] == OWNER_COMPANY_ID, 'fixture bug: identity did not adopt the Owner-issued id'
    reg_conn.close()
    retail_conn = sqlite3.connect(str(RETAIL_DB))
    assert retail_conn.execute(
        'SELECT COUNT(*) FROM products WHERE company_id=?', (OWNER_COMPANY_ID,)
    ).fetchone()[0] == 0, 'fixture bug: retail.db converged anyway -- the split state was never reached'
    retail_conn.close()

    assert _app_module._split_state_refusal['reason'] is not None, (
        '_on_licence_activated did not enter the split-state refusal despite a genuine '
        'split -- Defect 2 would still be reachable'
    )
    assert 'REFUSING TO SERVE' in _app_module._split_state_refusal['reason']

    # ── THE ASSERTION: a login attempt made WHILE the process is stuck must
    # be refused outright (503), not allowed through to succeed and then
    # silently serve an empty shop on the next call.
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': 'SplitStatePW1'})
    assert r.status_code == 503, (
        f'a request made during the split state was NOT refused -- status={r.status_code} '
        f'body={r.get_json()!r}. Without this refusal, the login above would succeed (200), '
        f'session[\'company_id\'] would be re-read as the NEW Owner-issued id (identity '
        f'converged), and every subsequent /api/sub/retail/products call would return '
        f'200 OK with an empty products list forever, because retail.db never followed.'
    )
    body = r.get_json()
    assert body['code'] == 503
    assert 'REFUSING TO SERVE' in body['reason']

    # /api/health stays reachable throughout -- the desktop launcher's
    # readiness probe must be able to tell "up but refusing" apart from
    # "never started".
    health = client.get('/api/health')
    assert health.status_code == 200, health.get_json()


def test_the_refusal_clears_once_convergence_succeeds_without_a_restart():
    """Defect 2's other half: a process stuck refusing FOREVER after a
    transient failure would be its own outage. Resolve the ambiguity the
    ordinary way (the extra tenant's rows are removed -- a real operator
    action) and prove the very next request heals it, with no restart."""
    _reset_databases()
    email = 'owner-splitstate-2@test.local'
    _onboard_admin(email, company_name='Split State Co 2')
    legacy_company_id = _registry_company_id_for(email)
    _insert_product(legacy_company_id, sku='WIDGET-SPLITSTATE-2')
    _insert_product(SECOND_TENANT_ID, sku='WIDGET-SPLITSTATE-2-OTHER')

    _write_licence_state(OWNER_COMPANY_ID)
    _app_module._on_licence_activated()
    assert _app_module._split_state_refusal['reason'] is not None, 'fixture bug: split state not reached'

    client = app.test_client()
    stuck = client.get('/api/health')  # health stays up
    assert stuck.status_code == 200

    blocked = client.post('/api/auth/login', json={'email': email, 'password': 'SplitStatePW1'})
    assert blocked.status_code == 503, blocked.get_json()

    # The ambiguity resolves the ordinary way: the extra tenant's row is
    # removed from retail.db (ONLY retail.db -- identity already converged
    # and stays converged; this fixture must not touch it, or it would be
    # simulating a different repair entirely).
    retail_conn = sqlite3.connect(str(RETAIL_DB))
    retail_conn.execute('DELETE FROM products WHERE company_id=?', (SECOND_TENANT_ID,))
    retail_conn.commit()
    retail_conn.close()

    # ── THE ASSERTION: the VERY NEXT request -- no restart, nobody called
    # init_app() again -- retries convergence via the before_request hook
    # and, finding it now succeeds, clears the refusal and serves normally.
    recovered = client.post('/api/auth/login', json={'email': email, 'password': 'SplitStatePW1'})
    assert recovered.status_code == 200, (
        f'the split-state refusal did not clear once retail.db could actually converge -- '
        f'status={recovered.status_code} body={recovered.get_json()!r}. A refusal that '
        f'never lifts after a transient failure resolves would be its own permanent outage.'
    )
    assert _app_module._split_state_refusal['reason'] is None, (
        '_split_state_refusal was not cleared even though the login above succeeded -- '
        'the NEXT request would still be refused for no reason'
    )

    products = client.get('/api/sub/retail/products').get_json()
    assert products['status'] == 'success'
    assert [p['sku'] for p in products['data']] == ['WIDGET-SPLITSTATE-2'], (
        'post-recovery request did not see the real, converged shop data'
    )


def _stage_the_split_state(email, sku, company_name):
    """Reach the split state through the REAL activation seam, and return
    `legacy_company_id`.

    Identity's rebind succeeds unambiguously (registry.db is genuinely
    single-tenant and matches the licence); retail's fails organically,
    because retail.db holds a second, irreconcilable tenant's rows and
    `rebind_company_id` correctly refuses to guess which one the licence
    belongs to. A locked retail.db under a concurrent sale is the ordinary
    real-world trigger; this fabrication is the deterministic stand-in.
    """
    _reset_databases()
    _onboard_admin(email, company_name=company_name)
    legacy_company_id = _registry_company_id_for(email)
    _insert_product(legacy_company_id, sku=sku)
    _insert_product(SECOND_TENANT_ID, sku=sku + '-OTHER')

    _write_licence_state(OWNER_COMPANY_ID)
    _app_module._on_licence_activated()  # the REAL on_activation_success hook
    return legacy_company_id


def test_the_shop_is_never_served_empty_over_http_while_the_process_is_split():
    """THE BLACK-BOX PROOF, asserted before anything white-box.

    What a shop actually experiences during Defect 2 is not "a module-level
    dict was not populated". It is: the licence is activated, the login
    still works, and the product list comes back `200 OK` with `[]`. That is
    what the reverted-fix mutation produces, and that is therefore what this
    test has to be able to say in its own failure output.

    So the ONLY assertions here are HTTP ones, and the first of them is the
    one that distinguishes "refused honestly" from "served an empty shop".
    The sibling test above checks `_split_state_refusal` on the way through,
    which is genuinely useful when diagnosing WHY -- but it means that under
    a reverted fix it stops at that internal check and never gets as far as
    issuing the request a till would have issued.
    """
    email = 'owner-splitstate-3@test.local'
    _stage_the_split_state(email, 'WIDGET-SPLITSTATE-3', 'Split State Co 3')

    client = app.test_client()

    login = client.post('/api/auth/login', json={'email': email, 'password': 'SplitStatePW1'})
    products = client.get('/api/sub/retail/products')
    body = products.get_json()

    assert products.status_code == 503, (
        f'/api/sub/retail/products returned {products.status_code} with body {body!r} '
        f'while identity had adopted the Owner-issued company_id and retail.db had not. '
        f'A 200 here means every till in the shop is being shown a catalogue, a sales '
        f'history and a customer list that all look simply EMPTY -- no error, no banner, '
        f'nothing in a log -- for the entire remaining lifetime of this process. '
        f'(login returned {login.status_code})'
    )
    assert body.get('data') is None, (
        f'the split-state refusal returned a data payload: {body!r}. A 503 that still '
        f'carries an empty `data` list is the same silent-empty-shop lie with a different '
        f'status code on it -- a client that renders whatever `data` it is given would '
        f'show a blank shop regardless.'
    )
    assert login.status_code == 503, (
        f'the login itself was allowed through ({login.status_code}). Letting it succeed '
        f'mints a session cookie stamped with the NEW Owner-issued company_id, which is '
        f'precisely the cookie that then matches zero rows in retail.db.'
    )

    # Only now, having established what a till sees, the internal bookkeeping
    # that explains it.
    assert _app_module._split_state_refusal['reason'] is not None
    assert 'REFUSING TO SERVE' in _app_module._split_state_refusal['reason']


# ── ONE PREDICATE, TWO SEAMS ────────────────────────────────────────────────
#
# The boot guard (`_converge_and_refuse_to_serve_if_stuck`) and the
# activation seam (`_on_licence_activated`, plus the `before_request` hook
# that keeps serving the refusal) have to answer the identical question:
# "is this process stuck in the split state?" If they ever answer it
# differently -- because someone tightened one and not the other, or fixed a
# false positive in one place -- then one seam will refuse an install the
# other happily serves, and the disagreement will show up as a shop that
# boots fine and goes blank after activation, or refuses to boot after
# working all day. Both are worse than either behaviour applied
# consistently.
#
# The two tests below pin that from opposite directions: one reads the
# source and proves no seam holds a second opinion, the other moves the
# single predicate and proves every seam moves with it.

_SPLIT_STATE_SEAMS = (
    '_converge_and_refuse_to_serve_if_stuck',
    '_on_licence_activated',
    '_refuse_while_split_state',
)

# The collaborators that, between them, CONSTITUTE the decision. Any seam
# that calls one of these directly is deriving its own answer.
_PREDICATE_INTERNALS = (
    'rebind_registry_company_id_after_activation',
    'rebind_company_id_after_activation',
    'registry_owner_issued_company_id',
    'registry_company_tenant_ids',
    'get_registry_conn',
)


def _direct_calls_by_function(source):
    """`{function name: {names it calls directly}}` for every function
    defined in `source`.

    Plain-name calls only (`foo(...)`), which is the only form any of these
    collaborators is used in: `app.py` imports them as module globals (with
    the identity-side ones aliased at import, see that import block), never
    as attributes of a module object. A future edit that reached for
    `company_rebind.rebind_company_id_after_activation(...)` instead would
    slip past this check -- but it would also have to add an import that
    does not currently exist, which is a visible change in a place the
    reviewer is already looking.
    """
    tree = ast.parse(source)
    return {
        node.name: {
            c.func.id for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
        }
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_detect_split_state_reason_is_the_only_place_the_decision_is_made():
    """Structural: every seam ASKS the predicate, and no seam DERIVES the
    answer itself.

    Deliberately asserted in both directions. "No seam calls the
    collaborators" on its own would also pass if `_detect_split_state_reason`
    stopped calling them too -- a predicate that decides nothing is
    trivially consistent with itself. So this also requires the predicate to
    still be the function that reaches for every one of them.
    """
    source = Path(_app_module.__file__).read_text(encoding='utf-8')
    calls = _direct_calls_by_function(source)

    for seam in _SPLIT_STATE_SEAMS:
        assert seam in calls, f'{seam} no longer exists in app.py'
        assert '_detect_split_state_reason' in calls[seam], (
            f'{seam}() no longer asks _detect_split_state_reason(). Whatever it asks '
            f'instead is a SECOND opinion about whether this process is stuck, and the '
            f'two will eventually disagree about a real shop\'s data.'
        )
        leaked = calls[seam] & set(_PREDICATE_INTERNALS)
        assert not leaked, (
            f'{seam}() calls {sorted(leaked)!r} directly instead of going through '
            f'_detect_split_state_reason(). That is how the boot guard and the '
            f'activation seam drift apart: each one re-deriving "are we stuck?" from '
            f'the same parts, in slightly different ways, until one refuses an install '
            f'the other serves.'
        )

    predicate = calls.get('_detect_split_state_reason')
    assert predicate is not None, 'app.py no longer defines _detect_split_state_reason'
    missing = set(_PREDICATE_INTERNALS) - predicate
    assert not missing, (
        f'_detect_split_state_reason() no longer calls {sorted(missing)!r}. The decision '
        f'has moved somewhere else, so the "nobody else calls these" check above has '
        f'quietly stopped meaning anything.'
    )


def test_moving_the_single_predicate_moves_every_seam_together(monkeypatch):
    """Behavioural: one lever, and BOTH seams follow it -- in both
    directions.

    This is the half a structural test cannot give. Source analysis proves
    nobody re-derives the answer today; this proves the seams are genuinely
    wired to the same answer, by substituting the predicate outright and
    watching the boot guard and the activation seam change behaviour
    together. If some seam had cached an earlier verdict, or consulted the
    refusal dict instead of re-asking, it would stay put here while the
    other moved.
    """
    _reset_databases()

    stuck = 'REFUSING TO SERVE: synthetic verdict from the single predicate'
    monkeypatch.setattr(_app_module, '_detect_split_state_reason', lambda: stuck)

    # Seam 1: boot. Refuses to start at all.
    with pytest.raises(RuntimeError, match='REFUSING TO SERVE: synthetic verdict'):
        _app_module._converge_and_refuse_to_serve_if_stuck()

    # Seam 2: activation. Enters the in-process refusal.
    _app_module._on_licence_activated()
    assert _app_module._split_state_refusal['reason'] == stuck

    # Seam 3: the before_request hook actually serves it.
    client = app.test_client()
    refused = client.get('/api/sub/retail/products')
    assert refused.status_code == 503, refused.get_json()
    assert refused.get_json()['reason'] == stuck

    # ── and now the same lever the other way ────────────────────────────
    monkeypatch.setattr(_app_module, '_detect_split_state_reason', lambda: None)

    _app_module._converge_and_refuse_to_serve_if_stuck()  # must not raise

    served = client.get('/api/health')
    assert served.status_code == 200
    # The hook re-asks on every refused request, so the FIRST ordinary
    # request after the verdict changes is the one that clears it -- no
    # restart, and no separate "clear" seam that could be forgotten.
    cleared = client.get('/api/sub/retail/products')
    assert cleared.status_code != 503, cleared.get_json()
    assert _app_module._split_state_refusal['reason'] is None, (
        'the refusal outlived the verdict that caused it -- some seam is holding a '
        'cached opinion rather than re-asking the one predicate'
    )

    _app_module._on_licence_activated()
    assert _app_module._split_state_refusal['reason'] is None
