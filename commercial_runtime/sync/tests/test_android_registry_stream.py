"""Android registry-stream sync wiring (sync backend follow-up).

THE GAP this closes: a cashier account created on the desktop could not log
in on Android, and an account made on the phone never reached the desktop.
Windows already runs TWO `SyncService` instances -- one over retail.db
(`RETAIL_SYNC_ENTITY_TYPES`), one over registry.db
(`REGISTRY_SYNC_ENTITY_TYPES` = {"user", "user_permission"}; see that
constant's own comment in sync_service.py for the desktop version of this
exact wound, before `user_permission` joined the set). Android built only
ONE instance (`_android_sync_service`, products/retail/backend/app.py),
gated to `RETAIL_SYNC_ENTITY_TYPES` by default -- so a `user`/`user_permission`
event reaching Android's pull loop had no instance willing to apply it at
all: `_apply_event`'s gate silently skipped it, cursor still advancing past
(see that gate's own comment), with no error anywhere to point at.

This file proves the fix at the WIRING level: products/retail/backend/
app.py's ANDROID branch now builds a SECOND `SyncService` over registry.db
and registers a SECOND `/_internal/sync/*`-shaped blueprint for it (Task 1's
new `blueprint_name`/`url_prefix` parameters on `make_sync_internal_
blueprint` are what make two registrations possible on one Flask app at
all). `test_registry_user_sync_apply.py`/`test_registry_user_permission_
sync_apply.py` (this directory) already prove `_apply_event`'s `user`/
`user_permission` branches directly against a bare `SyncService` -- this
file does not re-derive that. It proves app.py's ACTUAL wiring: booting the
real module with `LICENSING_PLATFORM='ANDROID'` and driving BOTH blueprints
through a real Flask test client, exactly test_internal_routes.py's own
technique, so a wiring mistake (wrong entity-type set, wrong database
connection, a missing/colliding registration) is what this file is
positioned to catch -- not a hand-built stand-in that could stay green while
app.py itself is wrong.

SCOPE -- the BACKEND half only. Kotlin's own pull/push loop calling these
two routes is a separate, not-yet-built follow-up (mobile/aura-retail-
unified and android/aura-retail are untouched by this change). These tests
prove the Android backend can SERVE and APPLY a registry stream; they do
NOT prove an account created on one Android device is visible on another --
that end-to-end claim needs the Kotlin half, which does not exist yet.

Own subprocess/module -- config.py/app.py resolve environment variables
(AURA_PLATFORM, AURA_INTERNAL_SHARED_SECRET, AURA_APP_DATA, ...) exactly
once at import time, so this file must be the only one in its pytest
process asserting on this platform/secret combination -- identical
reasoning to products/retail/tests/retail_sync_starts_when_configured_
test.py's own docstring, and the same reason products/run_all_tests.py runs
one file per pytest process.

All tests in this file share ONE booted app/registry.db/retail.db (module-
level boot, matching retail_sync_starts_when_configured_test.py) -- unlike
test_internal_routes.py's per-test tmp_path isolation, which is not
available here because the platform/secret env vars this module depends on
are read once at import. Every test therefore uses a fresh uuid4 for any
row it creates, so tests never collide on identity; cursor assertions only
ever check "the value I just POSTed is the value a GET reads back
immediately after", never an absolute position, so they hold regardless of
what earlier tests in this file already advanced the cursor to.

Run (ONE FILE PER PYTEST PROCESS -- see products/run_all_tests.py):
    pytest commercial_runtime/sync/tests/test_android_registry_stream.py -v
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# This file lives at commercial_runtime/sync/tests/ -- parents[3] is the
# repo root (commercial_runtime/sync/tests -> commercial_runtime/sync ->
# commercial_runtime -> repo root), NOT parents[1] the way products/retail/
# tests/*_test.py computes it (those files live one level deeper under the
# product they test).
TESTS_DIR = Path(__file__).resolve().parent
SUITE_ROOT = TESTS_DIR.parents[2]
BACKEND_DIR = SUITE_ROOT / 'products' / 'retail' / 'backend'
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_android_registry_stream_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)

SECRET = "test-android-registry-internal-secret"
COMPANY_ID = "company-android-registry-1"

# Mirrors android/aura-retail's own main.py start_server() (the real Kotlin
# bootstrap): AURA_PLATFORM='ANDROID' + AURA_INTERNAL_SHARED_SECRET is
# exactly what flips products/retail/backend/app.py into its ANDROID
# `elif` branch (LICENSING_PLATFORM == 'ANDROID' and
# LICENSING_INTERNAL_SHARED_SECRET) rather than the Windows one.
os.environ.update(
    AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA),
    AURA_PLATFORM="ANDROID", AURA_INTERNAL_SHARED_SECRET=SECRET,
)
os.environ.pop("AURA_DEV", None)
# Android's branch never reads SYNC_RELAY_BASE_URL at all (see app.py's
# `elif LICENSING_PLATFORM == 'ANDROID' ...` -- it is gated purely on
# platform + shared secret), but popped explicitly anyway, matching every
# other test file in this suite's convention of never assuming this is
# unset in a shared test process.
os.environ.pop("AURA_SYNC_RELAY_URL", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True
client = app.test_client()


def teardown_module(module):
    # Neither `_android_sync_service` nor `_android_registry_sync_service`
    # is ever `.start()`-ed (see app.py's own comment on why -- their
    # `client_factory` is None, and Kotlin drives their push/pull loop, not
    # a Python timer), so unlike the Windows sibling tests there is no
    # timer/thread to `.stop()` here.
    shutil.rmtree(DATA, ignore_errors=True)


def _auth_headers():
    return {"X-Aura-Internal-Secret": SECRET}


def _seed_company_settings():
    """`local_company_id_from_registry` -- the REAL provider app.py wires
    BOTH Android instances with, never a test double -- returns None until
    onboarding has written a `company_settings` row (see that function's own
    docstring in sync_service.py). Without this, applying any `user`/
    `user_permission` event raises inside `_get_local_company_id` (no
    company_id) rather than exercising the branch under test. Mirrors
    commercial_runtime/identity/tests/test_registry_v4_company_rebind.py's
    own `_seed_company` helper -- same two required columns."""
    conn = _app_module._android_registry_sync_get_conn()
    try:
        conn.execute(
            "INSERT INTO company_settings (id, company_id) VALUES (?, ?)",
            (str(uuid.uuid4()), COMPANY_ID),
        )
        conn.commit()
    finally:
        conn.close()


_seed_company_settings()


def _user_event(uid=None, event_type="create", **overrides):
    uid = uid or str(uuid.uuid4())
    payload = {
        "uid": uid, "employee_id": f"E-{uid[:8]}", "email": f"{uid[:8]}@example.com",
        "role": "cashier", "status": "active", "require_password_change": 0,
        "language": "en", "password_hash": "H", "pin_hash": None,
        "row_version": 1, "updated_at_utc": "2026-01-01T00:00:00+00:00",
        "session_version": 1,
    }
    payload.update(overrides)
    return {"entity_type": "user", "entity_id": uid, "event_type": event_type, "payload": payload}, uid


def _permission_event(user_uid, subsystem="retail", access_level="full", event_type="create"):
    return {
        "entity_type": "user_permission", "entity_id": str(uuid.uuid4()), "event_type": event_type,
        "payload": {"user_uid": user_uid, "subsystem": subsystem, "access_level": access_level},
    }


def _product_event():
    pid = str(uuid.uuid4())
    return {
        "entity_type": "product", "entity_id": pid, "event_type": "create",
        "payload": {"id": pid, "company_id": COMPANY_ID, "name": "Widget", "sku": f"W-{pid[:6]}"},
    }


def _pull_apply(path, events, cursor):
    return client.post(path, headers=_auth_headers(), json={"events": events, "cursor": cursor})


def _fetch_registry_user(uid):
    conn = _app_module._android_registry_sync_get_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _fetch_registry_permission(local_user_id, subsystem):
    conn = _app_module._android_registry_sync_get_conn()
    try:
        row = conn.execute(
            "SELECT access_level FROM user_permissions WHERE user_id=? AND subsystem=?",
            (local_user_id, subsystem),
        ).fetchone()
        return row["access_level"] if row else None
    finally:
        conn.close()


# ── (a) the registry route applies a `user` event to registry.db ──────────

def test_user_create_event_via_registry_route_is_really_applied_to_registry_db():
    """Catches: `_android_registry_sync_service` wired with the wrong
    entity-type set (e.g. left at the module default, RETAIL_SYNC_ENTITY_
    TYPES, which excludes "user") -- `_apply_event`'s gate would silently
    skip the event, the route would still answer 200 SUCCESS (a skip is not
    an error), and this is the ONLY assertion in this file that would notice:
    the account simply never appears. MUTATION-PROVEN (see this task's own
    report)."""
    event, uid = _user_event(event_type="create", email="alice@example.com")
    resp = _pull_apply("/api/registry-sync/_internal/pull-apply", [event], cursor=101)
    assert resp.status_code == 200
    assert resp.get_json() == {"result": "SUCCESS"}

    row = _fetch_registry_user(uid)
    assert row is not None, "user event applied via the registry route never reached registry.db"
    assert row["email"] == "alice@example.com"
    assert row["company_id"] == COMPANY_ID

    cursor_resp = client.get("/api/registry-sync/_internal/cursor", headers=_auth_headers())
    assert cursor_resp.get_json()["since"] == 101


# ── (b) THE ALLOW-HALF -- each stream writes only the database it owns ────

def test_user_event_lands_only_in_registry_db_and_product_event_lands_in_neither():
    """The most important test in this file. A test that only proves (a)
    would pass just as happily if both Android SyncService instances wrote
    everything everywhere -- this is the one that would actually notice.

    Catches (MUTATION-PROVEN, see this task's own report for the verbatim
    pytest output both ways): the new registry-stream instance's `get_conn`
    pointed at retail.db instead of registry.db. Observed effect of that
    exact mutation: this whole FILE fails to even collect, because module-
    level `_seed_company_settings()` (every test's own setup, run once at
    import) opens its connection through `_app_module.
    _android_registry_sync_get_conn()` -- the same seam this mutation
    corrupts -- and retail.db has no `company_settings` table either, so the
    very first write of the test run raises `sqlite3.OperationalError: no
    such table: company_settings` before any test body runs at all. That
    still satisfies "this test must fail": a collection error means every
    test in this file, this one included, is reported failed. Had setup
    itself been unaffected, the assertions below would independently have
    caught it anyway -- the `user` event's `INSERT INTO users ...` would
    have landed against retail.db (no `users` table there either -- see the
    isolation check just above), raising the identical error inside
    `apply_pull_result`, caught by `internal_pull_apply`'s own try/except,
    turning the 200/SUCCESS assertion below into 400/APPLY_FAILED.
    """
    # retail.db never had a `users` table to begin with (identity lives
    # entirely in registry.db per CLAUDE.md) -- confirmed directly rather
    # than assumed, so this test fails loudly if that ever changes instead
    # of silently proving nothing.
    retail_conn = _app_module._sync_get_conn()
    try:
        assert retail_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone() is None, "retail.db unexpectedly has a users table; this test's isolation proof is stale"
    finally:
        retail_conn.close()

    event, uid = _user_event(event_type="create", email="isolated@example.com")
    resp = _pull_apply("/api/registry-sync/_internal/pull-apply", [event], cursor=202)
    assert resp.status_code == 200
    assert resp.get_json() == {"result": "SUCCESS"}
    assert _fetch_registry_user(uid) is not None, "user event did not reach registry.db"

    # retail.db is untouched by this apply -- still no `users` table at all
    # (not merely "no row with this uid"; the table itself never gets
    # created by this code path).
    retail_conn = _app_module._sync_get_conn()
    try:
        with pytest.raises(sqlite3.OperationalError):
            retail_conn.execute("SELECT * FROM users").fetchall()
    finally:
        retail_conn.close()

    # And the reverse direction: a `product` event sent to the REGISTRY
    # route must never be applied to registry.db -- `product` is not in
    # REGISTRY_SYNC_ENTITY_TYPES, so `_apply_event`'s gate skips it (not an
    # error; the route still answers 200/SUCCESS, and the cursor still
    # advances past it -- see that gate's own comment in sync_service.py).
    product_ev = _product_event()
    resp2 = _pull_apply("/api/registry-sync/_internal/pull-apply", [product_ev], cursor=203)
    assert resp2.status_code == 200
    assert resp2.get_json() == {"result": "SUCCESS"}

    registry_conn = _app_module._android_registry_sync_get_conn()
    try:
        assert registry_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='products'"
        ).fetchone() is None, "registry.db unexpectedly has a products table; a product event could have been applied there"
    finally:
        registry_conn.close()


# ── (c) both blueprints registered, distinct prefixes, both secret-guarded ─

@pytest.mark.parametrize("path", [
    "/api/sync/_internal/cursor",
    "/api/registry-sync/_internal/cursor",
])
def test_both_internal_routes_reject_missing_and_wrong_secret(path):
    resp_missing = client.get(path)
    assert resp_missing.status_code == 403
    assert resp_missing.get_json()["reason_code"] == "INVALID_REQUEST"

    resp_wrong = client.get(path, headers={"X-Aura-Internal-Secret": "wrong"})
    assert resp_wrong.status_code == 403


@pytest.mark.parametrize("path", [
    "/api/sync/_internal/cursor",
    "/api/registry-sync/_internal/cursor",
])
def test_both_internal_routes_are_reachable_with_the_correct_secret(path):
    """Catches: only one blueprint actually getting registered, or the two
    registrations colliding (Flask raises at registration time on a
    duplicate blueprint name or a duplicate URL rule, which would have
    already failed this whole module's import -- this is the runtime-
    reachability half of that same proof, for both prefixes independently)."""
    resp = client.get(path, headers=_auth_headers())
    assert resp.status_code == 200


def test_the_two_registrations_use_genuinely_distinct_prefixes():
    prefixes = {name: bp.url_prefix for name, bp in app.blueprints.items()
                if name in ("sync_internal", "registry_sync_internal")}
    assert prefixes == {"sync_internal": "/api/sync", "registry_sync_internal": "/api/registry-sync"}


# ── (d) user_permission rides the same registry stream ────────────────────

def test_user_permission_event_rides_the_same_registry_stream_as_its_owning_user():
    """The desktop already lived this exact failure (see REGISTRY_SYNC_
    ENTITY_TYPES's own comment: "a cashier synced to a second till arrived
    with NO permissions there"). Proves Android does not repeat it: a
    `user_permission` event, applied via the SAME registry route right after
    the `user` event that owns it, actually grants the permission -- not
    just that the account row exists."""
    user_ev, uid = _user_event(event_type="create", email="perms@example.com")
    resp = _pull_apply("/api/registry-sync/_internal/pull-apply", [user_ev], cursor=301)
    assert resp.status_code == 200

    row = _fetch_registry_user(uid)
    assert row is not None
    local_user_id = row["id"]

    perm_ev = _permission_event(uid, subsystem="retail", access_level="full")
    resp2 = _pull_apply("/api/registry-sync/_internal/pull-apply", [perm_ev], cursor=302)
    assert resp2.status_code == 200
    assert resp2.get_json() == {"result": "SUCCESS"}

    assert _fetch_registry_permission(local_user_id, "retail") == "full"


# ── (e) the original single-blueprint caller/tests are unaffected ─────────

def test_make_sync_internal_blueprint_default_name_and_prefix_are_unchanged():
    """Task 1's new `blueprint_name`/`url_prefix` parameters must default to
    exactly the values this function has ALWAYS used -- the ones the
    pre-existing Android call site (`_android_sync_service`'s own
    registration, unchanged by this task) and every existing test in
    test_internal_routes.py rely on implicitly, never passing either
    argument. (These internal routes are Android-only -- see internal_
    routes.py's own module docstring -- Windows never registers this
    blueprint at all; "the existing single-blueprint registration" this test
    protects is that original Android registration, from before this task
    added a second one.)

    Constructed directly here (bypassing app.py) so this assertion holds
    independent of which platform THIS test process itself booted as --
    mirrors test_internal_routes.py's own direct-construction technique.
    Running test_internal_routes.py itself unmodified (see this task's
    verification output) is the fuller proof: every existing test there
    still passes byte-for-byte."""
    from commercial_runtime.sync.internal_routes import make_sync_internal_blueprint
    from commercial_runtime.sync.sync_service import SyncService

    bp = make_sync_internal_blueprint(
        sync_service=SyncService(None, lambda: None, lambda: None),
        get_conn=lambda: None,
        state_repository=None,
        shared_secret="irrelevant-for-this-assertion",
    )
    assert bp.name == "sync_internal"
    assert bp.url_prefix == "/api/sync"


def test_app_still_boots_and_serves_health_on_android():
    resp = client.get('/api/health')
    assert resp.status_code == 200
