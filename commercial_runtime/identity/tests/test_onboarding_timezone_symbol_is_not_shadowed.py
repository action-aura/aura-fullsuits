"""AUDIT -- `create_admin()` binds a local named `timezone`, which shadows the
module-level `datetime.timezone` this file now depends on.

Not a live bug today, and this test does not claim one. It is a TRAP GUARD,
and the trap was laid by the fix immediately preceding it.

Retiring the deprecated `datetime.utcnow()` from this module added `timezone`
to its imports (`from datetime import datetime, timedelta, timezone`) and made
`datetime.now(timezone.utc)` the house idiom -- it is now the only way a
timestamp is produced anywhere in onboarding_routes.py (two call sites, in
`create_employee` and in the report-date helper). But `create_admin()` already
had its own `timezone` local, holding the customer's IANA zone name from the
onboarding form ('Asia/Amman'), destined for `company_settings.timezone` and
config.json.

So the module-level symbol is shadowed for the whole body of that function.
The next person to add the house idiom inside `create_admin` -- the obvious,
correct-looking thing to write, matching the two existing call sites verbatim
-- gets:

    AttributeError: 'str' object has no attribute 'utc'

...raised inside the broad `except Exception as e` at the bottom of the
function, which converts it to a 500 carrying the raw message. `create_admin`
is the ONE route that runs with no admin session and creates the shop, so
that failure lands on first-run onboarding, on a machine nobody has logged
into yet.

Renaming the local to `timezone_name` costs nothing and removes the trap.
This file pins BOTH halves so the rename cannot be silently reverted and
cannot silently break the feature it touches:

  - the STRUCTURAL half asserts no function in the module shadows the symbol
  - the BEHAVIOURAL half asserts the customer's timezone still reaches
    `company_settings` and config.json, because a rename that dropped a call
    site would otherwise pass the structural half perfectly

Run:
    pytest commercial_runtime/identity/tests/test_onboarding_timezone_symbol_is_not_shadowed.py -v
"""
import datetime as _datetime
import inspect
import json
import os
import sqlite3
import types

import pytest
from flask import Flask

from commercial_runtime.identity import onboarding_routes, registry_db
from commercial_runtime.identity.onboarding_routes import onboarding_bp


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "registry.db"
    monkeypatch.setattr(registry_db, "DB_PATH", str(path))
    monkeypatch.setattr(registry_db, "_db_dir", str(tmp_path))
    registry_db.init_registry_db()
    return path


@pytest.fixture
def app(db_path, tmp_path, monkeypatch):
    def _get_conn():
        c = sqlite3.connect(str(db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(onboarding_routes, "get_conn", _get_conn)
    # config.json is resolved from AURA_APP_DATA on every call (see
    # _config_path), so this genuinely redirects the write rather than
    # relying on import-time state.
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    return flask_app


def _module_functions():
    """Every function defined in onboarding_routes, UNWRAPPED.

    The unwrapping is the whole reason this is a helper. Flask's `@route`
    returns the function unchanged, but `@mt_login_required` is a
    `functools.wraps` decorator, and 12 of this module's 23 functions carry it
    -- including every admin route. For those, the name in `vars(module)` is
    bound to the WRAPPER, whose `co_varnames` is
    `('args', 'kwargs', 'conn', 'row')` -- the decorator's own locals. A guard
    reading that would have inspected mt_auth's session check twelve times
    over and reported the module clean no matter what those handlers actually
    bind.

    `inspect.unwrap` follows the `__wrapped__` chain `functools.wraps` sets,
    so this yields the real handler bodies. `test_the_shadow_guard_sees_
    through_the_login_decorator` below pins that it still does.
    """
    return [inspect.unwrap(obj) for obj in vars(onboarding_routes).values()
            if isinstance(obj, types.FunctionType)
            and obj.__module__ == onboarding_routes.__name__]


# ═════════════════════════════════════════════════════════════════════════════
# Structural: the symbol the module's own timestamp idiom depends on
# ═════════════════════════════════════════════════════════════════════════════

def test_the_module_level_timezone_symbol_is_datetimes_not_a_string():
    """Precondition. If this module ever stops importing `datetime.timezone`,
    the shadowing test below would pass vacuously -- nothing can shadow a name
    that isn't there -- while `datetime.now(timezone.utc)` at both live call
    sites would already be broken."""
    assert hasattr(onboarding_routes, 'timezone'), \
        "onboarding_routes no longer exposes `timezone`; its two " \
        "`datetime.now(timezone.utc)` call sites cannot work"
    assert onboarding_routes.timezone is _datetime.timezone, (
        f"the module-level `timezone` is {onboarding_routes.timezone!r}, not "
        f"datetime.timezone -- the aware-UTC idiom this module standardised on "
        f"is reading the wrong object"
    )


def test_the_shadow_guard_sees_through_the_login_decorator():
    """A guard on the guard. The shadow test below is only worth what its
    inspection reaches, and the first version of it reached about half the
    module without saying so.

    `@mt_login_required` is a `functools.wraps` decorator, so
    `onboarding_routes.create_employee.__code__.co_varnames` is
    `('args', 'kwargs', 'conn', 'row')` -- mt_auth's wrapper, not the handler.
    Twelve of this module's functions are bound that way, every admin route
    among them. Without unwrapping, the shadow test would have inspected the
    same decorator body twelve times and passed regardless of what those
    handlers bind, which is the most comfortable kind of wrong: green, fast,
    and covering nothing.

    Pins that `_module_functions()` yields real handler bodies, using a
    function known to be decorated and known to bind a distinctive local.
    """
    assert hasattr(onboarding_routes.create_employee, '__wrapped__'), (
        "create_employee is no longer a wrapped function -- pick another "
        "decorated handler for this check, or the unwrapping below is untested"
    )

    inspected = {fn.__name__: fn for fn in _module_functions()}
    assert 'create_employee' in inspected

    varnames = inspected['create_employee'].__code__.co_varnames
    assert 'args' not in varnames and 'kwargs' not in varnames, (
        f"the guard is still inspecting mt_auth's @mt_login_required wrapper "
        f"instead of the handler body: {varnames!r}. Every decorated route in "
        f"this module is invisible to the shadow test in that state."
    )
    assert 'clinic_role' in varnames, (
        f"create_employee's real locals were not reached; got {varnames!r}"
    )


def test_no_function_in_onboarding_routes_shadows_the_timezone_symbol():
    """The guard. `create_admin` used to bind `timezone` to the customer's
    IANA zone string, shadowing datetime.timezone for that entire function
    body, so the module's own house idiom `datetime.now(timezone.utc)` would
    raise `AttributeError: 'str' object has no attribute 'utc'` if added
    there -- and be converted to a 500 by that function's broad except arm,
    on first-run onboarding.

    Asserts on `co_varnames` (the locals the compiler actually bound), not on
    source text, so a rename that only edited the comment cannot pass.
    """
    shadowing = sorted(
        fn.__name__ for fn in _module_functions()
        if 'timezone' in fn.__code__.co_varnames
    )
    assert not shadowing, (
        f"these functions bind a LOCAL named `timezone`, shadowing the "
        f"module-level datetime.timezone that "
        f"onboarding_routes' own `datetime.now(timezone.utc)` idiom needs: "
        f"{shadowing}. Rename the local (e.g. `timezone_name`) -- the next "
        f"person to add a timestamp inside one of these functions gets "
        f"AttributeError: 'str' object has no attribute 'utc', surfaced as a "
        f"500 from the one route that runs with no admin session."
    )


def test_the_aware_utc_idiom_actually_evaluates_in_create_admins_namespace():
    """The structural test above is about a name; this is about the thing that
    name is for. Compiles and runs the module's exact house idiom against
    `create_admin`'s own globals + the locals it binds, which is precisely the
    namespace a future timestamp line inside that function would resolve in.

    This is the assertion that fails LOUDLY and for the right reason if the
    shadow comes back, rather than merely reporting a name collision.
    """
    fn = onboarding_routes.create_admin
    scope = dict(fn.__globals__)
    # Reproduce the shadow exactly as the function body would create it: the
    # IANA zone string off the onboarding form.
    for local in fn.__code__.co_varnames:
        if local == 'timezone':
            scope['timezone'] = 'Asia/Amman'

    result = eval("datetime.now(timezone.utc)", scope)  # noqa: S307 - fixed literal
    assert result.tzinfo is _datetime.timezone.utc, (
        "the module's own aware-UTC idiom does not produce a UTC-aware "
        "datetime inside create_admin's namespace"
    )


# ═════════════════════════════════════════════════════════════════════════════
# Behavioural: the local being renamed still does its job
# ═════════════════════════════════════════════════════════════════════════════

def test_create_admin_persists_the_customers_timezone_to_company_settings(
        app, db_path, tmp_path):
    """Not optional. Every assertion above is satisfied by simply DELETING the
    `timezone` local -- which would also stop the customer's zone from ever
    reaching the database. This is the half that makes the rename a rename.

    Asserts a NON-default value ('Asia/Amman'), because the column defaults
    would let 'UTC' pass without the request value being read at all.
    """
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11',
        'company_name': 'Shop', 'country': 'JO', 'timezone': 'Asia/Amman',
        'currency': 'JOD', 'business_type': 'retail', 'language': 'ar',
    })
    assert r.status_code == 200, r.get_json()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT timezone, country, currency, language FROM company_settings"
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, "create-admin wrote no company_settings row at all"
    assert row['timezone'] == 'Asia/Amman', (
        f"the customer's timezone did not reach company_settings -- got "
        f"{row['timezone']!r}. A rename that missed this call site would "
        f"otherwise satisfy every structural assertion in this file."
    )
    # Siblings of the same INSERT: if the parameter order were disturbed, the
    # timezone assertion alone could still pass while these silently swapped.
    assert row['country'] == 'JO'
    assert row['currency'] == 'JOD'
    assert row['language'] == 'ar'


def test_create_admin_persists_the_customers_timezone_to_config_json(
        app, tmp_path):
    """The second consumer of that same local. `_write_config` is a separate
    call site, so a partial rename could keep the database correct and quietly
    drop the value the desktop launcher reads back."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner2@test.local', 'password': 'OwnerPW11',
        'timezone': 'Asia/Amman', 'country': 'JO',
    })
    assert r.status_code == 200, r.get_json()

    cfg_path = os.path.join(str(tmp_path), 'config.json')
    assert os.path.exists(cfg_path), f"no config.json was written at {cfg_path}"
    with open(cfg_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    assert cfg.get('timezone') == 'Asia/Amman', (
        f"config.json's `timezone` key is {cfg.get('timezone')!r}, not the "
        f"submitted zone -- the _write_config call site was missed"
    )
    assert cfg.get('country') == 'JO'
