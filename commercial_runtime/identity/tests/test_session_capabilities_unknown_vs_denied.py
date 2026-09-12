"""AUDIT -- `/api/auth/session` reports "the registry read broke" and "this
user is denied everything" with the same value, and the frontend now believes
it.

`get_session()` initialised `capabilities = []` and wrapped the whole registry
read in a bare `except Exception: pass`. Any failure in that block -- a locked
database, a missing `user_permissions` table on a half-migrated registry, a
connection that could not be opened at all -- therefore returned HTTP 200 with
`"capabilities": []`, which is byte-identical to the legitimate response for a
user whose every capability has been switched off.

That was harmless for exactly as long as nothing read the value. It stopped
being harmless in the wave that fixed app-shell.js's `_adoptSessionCapabilities`
to read the top-level key: `hasCapability()` fails OPEN on `null` and CLOSED on
a list, so `[]` is now the single most restrictive answer the server can give.
An owner whose registry read hiccuped once lost the Reports and Audit Log nav
entries, got the cashier refusal copy and the cashier landing panel -- while
`GET /api/sub/retail/reports/summary` kept answering 200 for that same owner,
because `mt_require_capability` bypasses on the session role and never reads
this table at all. Locked out of their own shop by a transient error, with the
server still perfectly willing to serve them.

The fix is to make the two states distinguishable: `None` (rendered as JSON
`null`) means "could not compute", `[]` means "computed, and the answer is
none". Both clients already have the receiving half built --
`app-shell.js:_adoptSessionCapabilities` (`Array.isArray`, not truthiness) and
`android/.../net/Models.kt`'s `val capabilities: List<String>? = null`, whose
own comment names `"capabilities": null` as the value it is typed for.

── Why these tests are shaped the way they are ─────────────────────────────

Two tests in this programme have already shipped asserting an OUTCOME where
they should have asserted that a CHECK RAN. A test that stubs a failure and
then asserts `capabilities is None` proves nothing on its own: `None` is also
what you would get if the stub was never reached, if the route 500'd into a
different branch, or if the handler stopped emitting the key for an unrelated
reason. So every failure test below drives its failure through a
CALL-COUNTING stub and asserts the counter first. If the raise did not happen,
the test fails on the counter, not on the payload -- the assertion is "the
except branch was entered", and the payload check is what it entered into.

`test_a_deliberately_empty_grant_list_is_still_reported_as_empty` is the other
half and is not optional: without it, `return None` unconditionally would pass
every other test in this file while destroying the entire capability feature.

Run:
    pytest commercial_runtime/identity/tests/test_session_capabilities_unknown_vs_denied.py -v
"""
import logging
import sqlite3
import uuid

import pytest
from flask import Flask

from commercial_runtime.identity import (
    auth_routes,
    mt_auth,
    onboarding_routes,
    registry_db,
    user_accounts as _accounts,
)
from commercial_runtime.identity.auth_routes import auth_bp
from commercial_runtime.identity.onboarding_routes import onboarding_bp
from commercial_runtime.security.passwords import hash_password

#: The logger onboarding_routes shares with mt_auth -- see mt_auth.py:31.
#:
#: This name is ASSERTED ON, not merely passed to `caplog.at_level`. An
#: earlier version of the logging test below handed this constant to
#: `caplog.at_level(..., logger=IDENTITY_LOGGER)` -- which only sets a LEVEL
#: on that logger -- and then scanned `caplog.records` globally, which
#: collects records from every propagating logger in the process. Renaming
#: onboarding_routes' module logger to `some.unrelated.logger` left all eight
#: tests green, because the renamed logger still inherits root's WARNING level
#: and still propagates into caplog's handler. The "same logger as mt_auth"
#: claim in onboarding_routes.py:39 was therefore enforced by nothing.
#: `_identity_warnings` below filters on `record.name` so the claim is real.
IDENTITY_LOGGER = 'aura.security.identity'


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
    monkeypatch.setenv("AURA_APP_DATA", str(tmp_path))
    # @mt_login_required and /api/auth/login both resolve their OWN connection
    # through mt_auth.REGISTRY_DB, not through the get_conn patched above.
    # Without repointing it, every request is refused regardless of session
    # validity and every test here would pass for the wrong reason.
    monkeypatch.setattr(mt_auth, "REGISTRY_DB", str(db_path))

    flask_app = Flask(__name__)
    flask_app.secret_key = "test-secret"
    flask_app.testing = True
    flask_app.register_blueprint(onboarding_bp)
    # auth_bp too: the empty-grant test needs a NON-admin session, and the
    # admin short-circuit in _capabilities_for_session means the create-admin
    # client can never produce one.
    flask_app.register_blueprint(auth_bp)
    return flask_app


@pytest.fixture
def owner(app):
    """A logged-in owner, minted through the real create-admin route so the
    company_id, the ADMIN-0001 id and the seeded user_permissions rows are
    exactly what production would have written."""
    client = app.test_client()
    r = client.post('/api/onboarding/create-admin', json={
        'name': 'Owner', 'email': 'owner@test.local', 'password': 'OwnerPW11',
    })
    assert r.status_code == 200, r.get_json()
    return client


def _login_as(app, db_path, role, *, revoke_everything=False):
    """A logged-in client for `role`, seeded exactly as account creation and
    the registry v3 migration seed one. `revoke_everything` sets every
    capability row to 'none' -- a real, deliberate, fully-denied account, which
    is the state that must stay distinguishable from a failed read."""
    email = f"cap-{uuid.uuid4().hex[:10]}@test.local"
    password = "CapTestPW1"
    user_id = str(uuid.uuid4())
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(
            "INSERT INTO users (id, company_id, employee_id, email, password_hash, "
            "role, status, require_password_change) VALUES (?,?,?,?,?,?,?,0)",
            (user_id, str(uuid.uuid4()), "EMP-0002", email,
             hash_password(password), role, "active"),
        )
        _accounts.seed_capabilities_for_user(conn, user_id, role)
        if revoke_everything:
            conn.execute(
                "UPDATE user_permissions SET access_level=? WHERE user_id=?",
                (_accounts.ACCESS_NONE, user_id),
            )
        conn.commit()
    finally:
        conn.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


class _CountingRaiser:
    """A stub that records that it was CALLED and then fails. The counter is
    the assertion; the payload is only what the counter lets you interpret."""

    def __init__(self, exc):
        self.calls = 0
        self._exc = exc

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise self._exc


class _ConnWhoseQueriesFail:
    """A connection that opens fine and then fails on the first query --
    the shape of a locked or corrupted database, as opposed to one that could
    not be opened at all. Counts `close()` too, so the test can prove the
    `finally` arm still runs and the failure path does not leak a handle."""

    def __init__(self, exc):
        self.executes = 0
        self.closes = 0
        self._exc = exc

    def execute(self, *args, **kwargs):
        self.executes += 1
        raise self._exc

    def close(self):
        self.closes += 1


class _ConnWhoseCloseFails:
    """A REAL registry connection that answers every query normally and then
    fails on `close()` -- an unfinalized-statement or already-closed error, the
    one failure in this block that happens strictly AFTER the answer is known.

    Deliberately a thin proxy over a live `sqlite3` connection rather than a
    hand-built stub. The `language` row and the `user_permissions` rows come
    out of the real database through the real query path, so the capability
    list the test asserts on is the genuinely computed answer. A stub that
    manufactured that return value would be the second failure pattern this
    programme keeps shipping: a fixture that supplies the exact state that
    hides the bug -- here, "the body carries the codes" would be true because
    the fixture said so, not because the computation ran.

    `close()` closes the real handle before raising, so the test does not leak
    a live sqlite connection into the rest of the session.
    """

    def __init__(self, real, exc):
        self._real = real
        self._exc = exc
        self.closes = 0

    def execute(self, *args, **kwargs):
        return self._real.execute(*args, **kwargs)

    def close(self):
        self.closes += 1
        self._real.close()
        raise self._exc


def _identity_warnings(caplog):
    """WARNING-or-worse records that were emitted BY the identity logger.

    Filtering on `record.name` is the whole point: `caplog.records` holds
    records from every propagating logger in the process, so an unfiltered
    scan proves only that *something, somewhere* logged. See IDENTITY_LOGGER.
    """
    return [rec for rec in caplog.records
            if rec.name == IDENTITY_LOGGER and rec.levelno >= logging.WARNING]


def _logger_names_seen(caplog):
    """For failure messages: every logger that emitted anything at all, so a
    rename shows up as 'it went to X instead' rather than a bare 'no records'."""
    return sorted({rec.name for rec in caplog.records})


def _drive_a_capability_failure(client, monkeypatch, caplog, failure):
    """Make `_capabilities_for_session` raise `failure` on one session call.

    Captures at DEBUG across ALL loggers rather than raising the level on
    IDENTITY_LOGGER alone: if the module logger has been renamed, this still
    collects whatever it emitted, so the assertions below can fail with "it
    was logged on the wrong logger" instead of the much less useful "nothing
    was logged".

    Returns the call-counting stub so the caller can assert the except branch
    was actually entered before interpreting anything it logged.
    """
    boom = _CountingRaiser(failure)
    monkeypatch.setattr(onboarding_routes, "_capabilities_for_session", boom)
    caplog.set_level(logging.DEBUG)
    client.get('/api/auth/session')
    return boom


# ═════════════════════════════════════════════════════════════════════════════
# The precondition: a healthy owner really does get all eight
# ═════════════════════════════════════════════════════════════════════════════

def test_a_healthy_owner_session_reports_all_eight_capability_codes(owner):
    """The baseline the failure tests are measured against. Without it, a
    change that broke the happy path outright would make every assertion
    below pass -- `None` for everyone is trivially not `[]`."""
    r = owner.get('/api/auth/session')
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['authenticated'] is True
    assert body['capabilities'] == sorted(_accounts.CAPABILITY_CODES), \
        f"an owner must hold every capability code; got {body.get('capabilities')!r}"
    assert len(body['capabilities']) == 8


def test_capabilities_stays_a_top_level_key_beside_user(owner):
    """Contract guard. app-shell.js `_adoptSessionCapabilities` and the Android
    session model both read the TOP-LEVEL key; a client reading
    `user.capabilities` is the client that is wrong. Moving it here would
    disable every capability gate in the product silently, which is precisely
    how this feature spent its first release doing nothing."""
    body = owner.get('/api/auth/session').get_json()
    assert 'capabilities' in body
    assert 'capabilities' not in (body.get('user') or {})


# ═════════════════════════════════════════════════════════════════════════════
# The bug: a failed computation must not be reported as a denial
# ═════════════════════════════════════════════════════════════════════════════

def test_a_failed_capability_computation_is_reported_as_unknown_not_as_denial(
        owner, monkeypatch):
    """The exact live failure: an owner whose `user_permissions` read raises
    gets `capabilities: []`, the frontend believes it, and the owner loses
    Reports and the Audit Log in their own shop while the server keeps
    serving those very routes to them."""
    boom = _CountingRaiser(sqlite3.OperationalError("no such table: user_permissions"))
    monkeypatch.setattr(onboarding_routes, "_capabilities_for_session", boom)

    r = owner.get('/api/auth/session')

    # FIRST: prove the except branch was actually entered. If this is 0 the
    # test learned nothing about the handler and everything below is noise.
    assert boom.calls == 1, \
        f"the failure stub was called {boom.calls} times -- the except branch " \
        f"under test was never reached, so this test proves nothing"

    assert r.status_code == 200, "a broken capability read must not break the session route"
    body = r.get_json()
    assert body['authenticated'] is True, \
        "a capability read failure must not log the user out"
    assert body.get('capabilities') is None, \
        f"a FAILED computation must be reported as unknown (absent or null), " \
        f"never as an empty grant list -- got {body.get('capabilities')!r}"
    assert body.get('capabilities') != [], \
        "`[]` means 'computed, and the answer is none'. Returning it for a read " \
        "that never completed is what locks an owner out of their own shop."


def test_a_registry_connection_failure_is_reported_as_unknown_not_as_denial(
        owner, monkeypatch):
    """The other arm of the same try: the connection itself cannot be opened,
    so neither the language read nor the capability read ever runs."""
    boom = _CountingRaiser(sqlite3.OperationalError("unable to open database file"))
    monkeypatch.setattr(onboarding_routes, "get_conn", boom)

    r = owner.get('/api/auth/session')

    assert boom.calls == 1, \
        f"get_conn stub was called {boom.calls} times -- the except branch " \
        f"under test was never reached"

    assert r.status_code == 200
    body = r.get_json()
    assert body['authenticated'] is True
    assert body.get('capabilities') is None, \
        f"an unopenable registry must report unknown, not universal denial -- " \
        f"got {body.get('capabilities')!r}"


def test_an_owners_capabilities_survive_a_failure_in_the_UNRELATED_language_read(
        owner, monkeypatch):
    """The precise live repro, and the sharpest edge on this bug.

    `_capabilities_for_session` SHORT-CIRCUITS on role='admin' and returns
    `sorted(CAPABILITY_CODES)` from a module constant -- it never touches the
    database for an owner at all. So an owner's capability computation cannot
    fail on its own, and the reported "admin gets `capabilities: []`" can only
    have come from the `SELECT language` query that runs immediately BEFORE
    it inside the same try. An owner lost the Reports nav, the Audit Log and
    their whole non-cashier UI because a query about which LANGUAGE to render
    in fell over.

    That coupling is what makes the tri-state necessary rather than tidy: the
    thing that fails and the thing that gets downgraded need not be related at
    all, so `[]` can never be trusted to mean a decision was made about
    capabilities.
    """
    conn = _ConnWhoseQueriesFail(sqlite3.OperationalError("database disk image is malformed"))
    monkeypatch.setattr(onboarding_routes, "get_conn", lambda *a, **k: conn)

    r = owner.get('/api/auth/session')

    assert conn.executes == 1, \
        f"the language query ran {conn.executes} times -- the failure under " \
        f"test never happened, so this test proves nothing"
    assert conn.closes == 1, \
        "the `finally` arm did not close the connection on the failure path"

    assert r.status_code == 200
    body = r.get_json()
    assert body['authenticated'] is True
    assert body['language'] == 'en', "the language fallback is unaffected by this fix"
    assert body.get('capabilities') is None, \
        f"a failed LANGUAGE read must not report the owner as holding no " \
        f"capabilities -- got {body.get('capabilities')!r}"


def test_the_failure_is_logged_rather_than_swallowed(owner, monkeypatch, caplog):
    """`except Exception: pass` is why this shipped silent. A failure that
    downgrades what a client renders has to leave a trace, or the next person
    debugging 'the owner has no Reports tab' has nothing to go on."""
    boom = _drive_a_capability_failure(
        owner, monkeypatch, caplog,
        sqlite3.OperationalError("database is locked"))

    assert boom.calls == 1, "the except branch under test was never reached"
    records = _identity_warnings(caplog)
    assert records, \
        "a capability computation failure produced no WARNING/ERROR log record"
    assert any('capabilit' in rec.getMessage().lower() for rec in records), \
        f"the log record does not name what failed: " \
        f"{[r.getMessage() for r in records]!r}"


def test_the_failure_is_logged_on_the_identity_logger_not_a_private_one(
        owner, monkeypatch, caplog):
    """onboarding_routes.py:39 claims its logger is deliberately the SAME name
    mt_auth.py uses, so an operator grepping for one identity-layer security
    event finds both halves under one name. Nothing enforced that claim: the
    previous version of the test above passed IDENTITY_LOGGER to
    `caplog.at_level` (which only sets a level) and then scanned
    `caplog.records` globally, so renaming the module logger to
    `some.unrelated.logger` left all eight tests green.

    This asserts on `record.name` -- the logger that actually emitted -- which
    is the only thing that can hold that claim up.
    """
    boom = _drive_a_capability_failure(
        owner, monkeypatch, caplog,
        sqlite3.OperationalError("database is locked"))

    assert boom.calls == 1, "the except branch under test was never reached"
    assert _identity_warnings(caplog), (
        f"the capability-failure warning was not emitted on {IDENTITY_LOGGER!r}. "
        f"Loggers that DID emit during this request: {_logger_names_seen(caplog)!r}. "
        f"onboarding_routes.py's module logger must keep the name mt_auth.py "
        f"uses -- an operator greps one name for both halves of an "
        f"identity-layer security event."
    )


def test_the_failure_log_carries_the_traceback(owner, monkeypatch, caplog):
    """`exc_info=True` was untested: deleting it left all eight tests green.

    Without it the operator gets the sentence "could not compute capabilities"
    and no stack -- no file, no line, no exception type, no cause. For a
    failure whose entire purpose is to be debuggable after the fact (the
    swallowed-exception bug this whole file exists for), the traceback IS most
    of the value; the sentence alone just tells them what they already knew
    from the missing Reports tab.

    Asserts the attached exception is the exact instance that was raised, not
    merely that some traceback is present -- `exc_info=True` inside a nested
    handler can otherwise attach an unrelated in-flight exception.
    """
    failure = sqlite3.OperationalError("database is locked")
    boom = _drive_a_capability_failure(owner, monkeypatch, caplog, failure)

    assert boom.calls == 1, "the except branch under test was never reached"
    records = _identity_warnings(caplog)
    assert records, "no identity-logger warning to inspect for a traceback"

    with_exc = [rec for rec in records if rec.exc_info is not None]
    assert with_exc, (
        "the capability-failure warning carries no exception info -- the "
        "operator gets a message and no traceback. Restore `exc_info=True` on "
        "the log.warning(...) call in onboarding_routes.get_session()."
    )
    assert any(rec.exc_info[1] is failure for rec in with_exc), (
        f"the attached traceback is not the failure that actually happened; "
        f"got {[rec.exc_info[1] for rec in with_exc]!r}, expected {failure!r}"
    )
    assert 'Traceback (most recent call last)' in caplog.text, \
        "the rendered log output contains no traceback for the operator to read"


def test_a_close_failure_after_a_successful_computation_is_not_logged_as_unknown(
        app, db_path, monkeypatch, caplog):
    """`conn.close()` used to sit inside the guarded `try`, so a close failure
    AFTER a fully successful computation fell into the same `except` arm as a
    failed one.

    The handler then logged "could not compute capabilities ... reporting them
    as UNKNOWN (null)" while the response body actually carried the real,
    correctly computed codes -- because `capabilities` had already been
    assigned before the `finally` ran. The log lies in the direction that costs
    an operator the most time: it sends someone hunting a capability-read
    outage that never happened, past the real fault, which is that a connection
    would not close.

    Driven through a REAL connection (see `_ConnWhoseCloseFails`) so the codes
    asserted on below are computed from the real `user_permissions` rows, and
    against a CASHIER rather than an owner precisely because the owner path
    short-circuits on role and never reads the database at all -- an owner
    would prove the body was right without proving the read happened.
    """
    client = _login_as(app, db_path, _accounts.ROLE_CASHIER)
    expected = sorted(_accounts.capabilities_for_role(_accounts.ROLE_CASHIER))
    assert expected, "precondition: a cashier holds at least one capability code"

    proxies = []

    def _conn_that_cannot_close():
        real = sqlite3.connect(str(db_path), timeout=30)
        real.row_factory = sqlite3.Row
        proxy = _ConnWhoseCloseFails(
            real, sqlite3.OperationalError("unable to close due to unfinalized statements"))
        proxies.append(proxy)
        return proxy

    monkeypatch.setattr(onboarding_routes, "get_conn", _conn_that_cannot_close)
    caplog.set_level(logging.DEBUG)

    r = client.get('/api/auth/session')

    # FIRST: prove the failure under test actually happened. Without this the
    # rest of the test would pass just as happily against a handler that never
    # opened a connection at all.
    assert len(proxies) == 1, \
        f"the session route opened {len(proxies)} connections through the stub, expected 1"
    assert proxies[0].closes == 1, \
        "close() was never called, so the close failure under test never happened"

    assert r.status_code == 200
    body = r.get_json()
    assert body['capabilities'] == expected, (
        f"a close failure must not disturb an already-computed capability "
        f"list -- got {body.get('capabilities')!r}, expected {expected!r}"
    )

    lying = [rec for rec in caplog.records
             if 'could not compute capabilities' in rec.getMessage()]
    assert not lying, (
        f"the handler logged a capability-computation failure for a "
        f"computation that SUCCEEDED (the body carries {body['capabilities']!r}). "
        f"conn.close() must sit outside the guarded try. Offending record(s): "
        f"{[rec.getMessage() for rec in lying]!r}"
    )

    # ...and the close failure itself must still leave a trace. Moving the
    # close out of the guarded try must not turn it into a silent `pass` --
    # that is the very shape of bug this file was written about.
    close_records = _identity_warnings(caplog)
    assert close_records, (
        "a connection that failed to close produced no identity-logger "
        "warning at all -- the fix must relocate the close, not silence it"
    )
    assert any('clos' in rec.getMessage().lower() for rec in close_records), (
        f"the log record does not say what actually failed (the close): "
        f"{[rec.getMessage() for rec in close_records]!r}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# The other half: an EMPTY answer is still a real answer
# ═════════════════════════════════════════════════════════════════════════════

def test_a_deliberately_empty_grant_list_is_still_reported_as_empty(app, db_path):
    """Without this, `capabilities = None` unconditionally passes every test
    above and deletes the whole feature. A cashier with every code revoked is
    a real, supported state and the client must see `[]` for it -- that is the
    value `hasCapability()` fails CLOSED on, which is the entire point."""
    client = _login_as(app, db_path, _accounts.ROLE_CASHIER, revoke_everything=True)
    body = client.get('/api/auth/session').get_json()
    assert body['capabilities'] == [], \
        f"a fully-denied account must report an EMPTY LIST, not unknown -- " \
        f"got {body.get('capabilities')!r}"
    assert body['capabilities'] is not None


def test_a_normal_cashier_still_gets_their_three_codes(app, db_path):
    """Sanity anchor for the test above: proves `[]` there came from the
    revocation and not from a broken seed or a broken read path."""
    client = _login_as(app, db_path, _accounts.ROLE_CASHIER)
    body = client.get('/api/auth/session').get_json()
    assert body['capabilities'] == sorted(_accounts.capabilities_for_role(
        _accounts.ROLE_CASHIER))
    assert _accounts.CAP_SELL in body['capabilities']
    assert _accounts.CAP_REPORTS not in body['capabilities']
