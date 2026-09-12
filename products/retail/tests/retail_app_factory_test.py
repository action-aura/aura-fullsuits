"""Aura Retail -- `create_app()`, the application factory added to close the
`flask --app app run` trap (retail-hardware-viewports Fix 1).

Before this, `app.py` built its Flask `app` at MODULE level but only ran the
real startup work (`init_app()` -- schema migrations, the split-state boot
guard, background worker/sync startup) from the `if __name__ == '__main__':`
block. Any other launch path that imports the module without going through
that block (most plausibly `flask --app app run`) produced a process that
"looked perfectly healthy" -- it bound its port and answered `GET /api/health`
with 200 -- and then failed the first real request with
`sqlite3.OperationalError: no such table: users`. See `_INIT_APP_HAS_RUN`'s
own docstring in app.py for the full story.

`create_app()` is now the one path that both builds (already done at import
-- see its own docstring) AND initialises. It is SINGLE-SHOT: the first call
in a process runs `init_app()`, every later call is a no-op that returns the
same already-initialised `app` without re-running `init_app()` -- see
`create_app()`'s docstring in app.py for exactly why (several background
workers are not provably safe to start twice).

This file does NOT re-test Fix 2 (the packaged-build dev-server refusal) --
that requires monkeypatching `sys.frozen`/`commercial_runtime.security.modes
.IS_FROZEN` and forcing `waitress`'s import to fail, verified separately as
part of this change's manual verification pass (see the task report), kept
out of the automated suite here to stay in scope for this one factory.

One file per process (AUDIT-010) -- run standalone:
    pytest products/retail/tests/retail_app_factory_test.py -v
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_app_factory_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402

# Deliberately NOT calling _app_module.create_app() here at module import
# time -- each test below calls it itself, so test 2 (the module-level `app`
# backward-compat pin) genuinely observes what import alone produces, before
# any test has triggered init_app().


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# A bare `Flask(__name__)` (no blueprints registered) has only the implicit
# static-file rule -- 1 route. This app registers auth, onboarding, retail,
# import, backup, device, licensing, einvoicing, notifications and WhatsApp
# blueprints: hundreds of routes. Set well above what a bare app could ever
# produce, so tests 1-3 cannot be fooled by a factory that returns an empty
# Flask app (test 5, anti-vacuity).
_MIN_REAL_ROUTE_COUNT = 50


def _route_rules(flask_app):
    return {r.rule for r in flask_app.url_map.iter_rules()}


# ═════════════════════════════════════════════════════════════════════════════
# 1. create_app() returns a fully-routed app
# ═════════════════════════════════════════════════════════════════════════════

def test_create_app_returns_app_with_known_route_registered():
    app = _app_module.create_app()
    rules = _route_rules(app)
    assert '/api/health' in rules, "the health route (registered via blueprints at import) must survive create_app()"
    # Anti-vacuity (test 5): a bare Flask(__name__) could never clear this.
    assert len(rules) > _MIN_REAL_ROUTE_COUNT, (
        f"only {len(rules)} routes registered -- looks like a bare Flask app, not the real one"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2. Backward compatibility -- the module-level `app` is untouched
# ═════════════════════════════════════════════════════════════════════════════

def test_module_level_app_still_exists_with_same_route():
    """The pin that stops a future refactor breaking the ~140 existing test
    files that do `import app as _app_module; app = _app_module.init_app()`
    (or use `_app_module.app` directly) silently."""
    assert hasattr(_app_module, 'app'), "module-level `app` must still exist -- ~140 test files import it"
    rules = _route_rules(_app_module.app)
    assert '/api/health' in rules
    assert len(rules) > _MIN_REAL_ROUTE_COUNT

    # create_app() must hand back THIS SAME object, never a second Flask app.
    factory_app = _app_module.create_app()
    assert factory_app is _app_module.app, "create_app() must not construct a second Flask app"


# ═════════════════════════════════════════════════════════════════════════════
# 3. create_app() actually runs init_app() -- an observable, real effect
# ═════════════════════════════════════════════════════════════════════════════

def test_create_app_runs_init_app_retail_schema_is_created():
    """`init_registry_db()`/`init_retail()` (inside `init_app()`) are what
    create retail.db's tables -- nothing at import time does. `products` is
    created unconditionally by `_init_retail` (database/schema.py). This is
    NOT "it did not raise": it is a real row in `sqlite_master` that can only
    exist if the migration genuinely ran."""
    _app_module.create_app()
    conn = get_retail_conn()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "create_app() must run init_app(), which creates retail.db's schema"


# ═════════════════════════════════════════════════════════════════════════════
# 4. Single-shot: a second call must not re-run init_app()
# ═════════════════════════════════════════════════════════════════════════════

def test_create_app_second_call_does_not_rerun_init_app(monkeypatch):
    """create_app() is single-shot (see its own docstring in app.py for why:
    several background workers -- e.g. SyncService.start(),
    EmailOutboxWorker.start() -- are not provably safe to start twice).
    First call latches `_INIT_APP_HAS_RUN`; every later call must skip
    `init_app()` entirely rather than re-running it. Poison `init_app` after
    the guard is latched: if create_app() ever calls it again, this test
    fails with the poison's own AssertionError, not a vague symptom."""
    _app_module.create_app()
    assert _app_module._INIT_APP_HAS_RUN is True, "guard must be latched after the first real call"

    def _poisoned():
        raise AssertionError("init_app() must not run again once _INIT_APP_HAS_RUN is True")

    monkeypatch.setattr(_app_module, 'init_app', _poisoned)

    result = _app_module.create_app()  # must NOT call the poisoned init_app
    assert result is _app_module.app


# ═════════════════════════════════════════════════════════════════════════════
# 5. Anti-vacuity floor (also exercised inline in tests 1-2 above)
# ═════════════════════════════════════════════════════════════════════════════

def test_route_count_floor_rules_out_a_bare_flask_app():
    app = _app_module.create_app()
    rules = _route_rules(app)
    assert len(rules) > _MIN_REAL_ROUTE_COUNT, (
        f"only {len(rules)} routes -- a bare Flask(__name__) factory would pass tests "
        "asserting merely 'it returned something' but not this floor"
    )
