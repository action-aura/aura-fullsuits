"""
Aura FullSuits -- Multi-Tenant Auth Middleware (Standalone Safe)

The SINGLE canonical implementation of:
  - registry-based (multi-tenant / standalone-product) authentication
    (`authenticate_registry_user`);
  - session creation (`create_session`);
  - login-attempt throttling / lockout;
  - the session-checking decorators used by every product route
    (`mt_login_required`, `mt_require_subsystem`, `require_clinic_role`).

Shared by Aura Retail and Aura Clinic. Extracted from Action Aura Enterprise's
api/mt_auth.py -- see docs/migration/dependency-map.md §1. Retargeted at
commercial_runtime.security / commercial_runtime.identity.registry_db instead
of the platform-wide core.security / database.registry_db modules.
"""
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import session, jsonify

from commercial_runtime.security.passwords import authenticate_and_maybe_upgrade
from commercial_runtime.security.audit import (
    record as _audit, LOGIN_SUCCESS, LOGIN_FAILED, ACCOUNT_LOCKOUT,
    PASSWORD_HASH_UPGRADED, SESSION_REVOKED,
)

log = logging.getLogger('aura.security.identity')

# ── Database path ─────────────────────────────────────────────────────────────
# Must match commercial_runtime/identity/registry_db.py so login and session
# checks read the SAME database.
_APP_DATA = os.environ.get('AURA_APP_DATA') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY_DB = os.path.join(_APP_DATA, 'database', 'registry.db')

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60  # 15 minutes


def _get_registry_conn():
    conn = sqlite3.connect(REGISTRY_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_module_enabled(company_id, module_code):
    """Check if a company has a valid license for a module.

    Enforcement rule (fail-closed for provisioned tenants, soft fallback for
    un-provisioned standalone installs):

      1. If `company_modules` has ANY row at all for this company, the
         tenant has been explicitly provisioned. A missing row for the
         requested module then means "not licensed" -- FAILS CLOSED.
      2. If `company_modules` has NO rows for this company at all (the
         standalone single-tenant product's actual state), fall back to the
         local config.json license file. If config.json exists and lists the
         module, allow; if it exists and does not list the module, deny; if
         it does not exist at all (first run / dev), allow only so first-run
         setup isn't blocked before config.json is written.
    """
    try:
        conn = _get_registry_conn()
        try:
            any_row = conn.execute(
                "SELECT COUNT(*) FROM company_modules WHERE company_id=?", (company_id,)
            ).fetchone()[0]
            if any_row:
                row = conn.execute(
                    "SELECT status, enabled FROM company_modules WHERE company_id=? AND (module_code=? OR module_name=?)",
                    (company_id, module_code, module_code)
                ).fetchone()
                if not row:
                    return False  # provisioned tenant, module never enabled -> fail closed
                status = row['status'] if row['status'] else None
                if status:
                    return status in ('enabled', 'trial')
                return bool(row['enabled'])
        finally:
            conn.close()
    except Exception:
        pass  # registry DB unreadable -- fall through to the config.json check below

    return _is_module_enabled_via_local_config(module_code)


def _is_module_enabled_via_local_config(module_code) -> bool:
    """Standalone-product fallback: read this installation's own local license
    bundle (config.json, written by onboarding / seed_config on first run).
    Intentionally soft (no signature, no server call) -- see
    LICENSE-POLICY.md. It is not a security control, only a UX gate.
    """
    try:
        import json
        cfg_path = os.path.join(_APP_DATA, 'config.json')
        if not os.path.exists(cfg_path):
            return True  # no license bundle yet (first run) -- don't block setup
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        modules = cfg.get('modules')
        if not modules:
            return True  # bundle predates the modules field -- don't retroactively lock customers out
        return module_code in modules
    except Exception:
        return True  # unreadable config -- do not block first-run/offline setup


def create_session(user: dict) -> None:
    """The one place every registry-based login sets session state, so every
    caller ends up with the same minimum required fields."""
    session.clear()
    session['mt_user_id'] = user['id']
    session['company_id'] = user['company_id']
    session['employee_id'] = user['employee_id']
    session['mt_role'] = user['role']
    session['clinic_role'] = user.get('clinic_role') or ''
    session['mt_session_version'] = user.get('session_version', 1)
    session['require_password_change'] = bool(user.get('require_password_change'))
    session['email'] = user['email']
    session.permanent = True


def authenticate_registry_user(email: str, password: str) -> dict:
    """THE single canonical multi-tenant / standalone-product login check.

    Returns {'ok': bool, 'user': dict|None, 'error': str|None, 'locked': bool}.
    Handles: case-insensitive email lookup, legacy-SHA256-to-modern-hash
    migration (transparent, one time), login-attempt throttling/lockout, and
    disabled-account rejection.
    """
    email = (email or '').strip().lower()
    password = password or ''
    result = {'ok': False, 'user': None, 'error': 'Invalid email or password.', 'locked': False}
    if not email or not password:
        return result

    conn = _get_registry_conn()
    try:
        row = conn.execute("SELECT * FROM users WHERE LOWER(email)=?", (email,)).fetchone()
        if not row:
            _audit(None, None, LOGIN_FAILED, context={'email': email, 'reason': 'no_such_user'})
            return result

        user = dict(row)
        result['user'] = user
        locked_until = user.get('locked_until')
        if locked_until:
            try:
                still_locked = datetime.fromisoformat(locked_until) > datetime.now(timezone.utc)
            except Exception:
                still_locked = False
            if still_locked:
                _audit(user['company_id'], user['id'], LOGIN_FAILED,
                       context={'email': email, 'reason': 'locked_out'})
                result['error'] = 'Too many failed attempts. Please try again later.'
                result['locked'] = True
                return result

        ok, new_hash = authenticate_and_maybe_upgrade(password, user.get('password_hash') or '')

        if not ok:
            # Phase 5 wave B2 (Decision 3): `failed_login_count`/`locked_until`
            # are DEVICE-LOCAL security state, deliberately absent from the
            # sync allowlist -- never bumped, never emitted, on any of the
            # three writes in this function. See sync_service.py's `user`
            # branch for the full reasoning (a lockout describes THIS device
            # being attacked; syncing it would let an idle till clear a live
            # one).
            failed = int(user.get('failed_login_count') or 0) + 1
            if failed >= MAX_FAILED_ATTEMPTS:
                locked_until_ts = (datetime.now(timezone.utc) + timedelta(seconds=LOCKOUT_SECONDS)).isoformat()
                conn.execute(
                    "UPDATE users SET failed_login_count=?, locked_until=? WHERE id=?",
                    (failed, locked_until_ts, user['id'])
                )
                conn.commit()
                _audit(user['company_id'], user['id'], ACCOUNT_LOCKOUT,
                       context={'email': email, 'failed_attempts': failed})
            else:
                conn.execute("UPDATE users SET failed_login_count=? WHERE id=?", (failed, user['id']))
                conn.commit()
                _audit(user['company_id'], user['id'], LOGIN_FAILED,
                       context={'email': email, 'reason': 'bad_password', 'failed_attempts': failed})
            return result

        conn.execute(
            "UPDATE users SET failed_login_count=0, locked_until=NULL WHERE id=?", (user['id'],)
        )
        if new_hash:
            # Phase 5 wave B2 stage 2b -- JUDGMENT CALL, decided and recorded
            # here rather than left silent: this write changes `password_hash`,
            # an allowlisted field, but it deliberately does NOT bump
            # `row_version` and does NOT queue a sync event.
            #
            # `new_hash` is a re-encoding of the SAME password under the
            # current hash scheme (`authenticate_and_maybe_upgrade` only
            # returns one after verifying the OLD hash against the password
            # just typed) -- not a new password. That distinction is the
            # whole of the reasoning:
            #
            #   DIVERGENCE if this stays local: none. Two devices holding
            #   different hash *representations* of the identical password
            #   both still authenticate that password correctly -- each
            #   device runs this exact upgrade independently, the first time
            #   THAT device sees a login against the legacy hash. Nothing a
            #   user or an admin can observe differs.
            #
            #   RISK if this emitted instead: this write has no knowledge of
            #   whether a NEWER password exists on another device. Bumping
            #   row_version and emitting here would race a genuine
            #   `reset_password`/`update_role`-style change made elsewhere at
            #   the same wall-clock moment -- and because both changes touch
            #   `password_hash`, an unlucky ordering could let a mere
            #   re-hash of the OLD password win the `row_version` compare and
            #   overwrite a real new one. That is EXACTLY the resurrection
            #   scenario design Decision 2 exists to prevent ("a device that
            #   was offline while a password was changed elsewhere would...
            #   push its stale row and resurrect the old password"), reached
            #   here via an ordinary login instead of a reconnect.
            #
            #   CHURN if this emitted instead: every account still holding a
            #   legacy hash would queue one event per device on its first
            #   post-upgrade login, purely to re-transmit a hash of a
            #   password that has not changed -- volume with no information
            #   for a peer to converge on.
            #
            # Conclusion: leave this write exactly as it already was --
            # unbumped, unemitted. Each device self-heals its own hash
            # representation independently and correctly without ever
            # needing to hear from another one.
            conn.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, user['id']))
            _audit(user['company_id'], user['id'], PASSWORD_HASH_UPGRADED,
                   context={'email': email})
        conn.commit()

        if user.get('status') == 'disabled':
            _audit(user['company_id'], user['id'], LOGIN_FAILED,
                   context={'email': email, 'reason': 'account_disabled'})
            result['error'] = 'Account is disabled by your Administrator.'
            return result

        _audit(user['company_id'], user['id'], LOGIN_SUCCESS, context={'email': email})
        result['ok'] = True
        result['error'] = None
        return result
    finally:
        conn.close()


def _unauthenticated_response():
    """THE refusal shape for "this request carries no usable identity".

    Deliberately identical for a request with no session at all and for one
    whose session could not be verified because the registry read failed. A
    caller must not be able to tell those two apart: if the broken case had
    its own message or status, that message would be a free oracle telling
    anyone who asks whether this shop's registry database is currently
    unhealthy -- which is exactly the moment an attacker would want to keep
    retrying.
    """
    return jsonify({'error': 'Authentication required', 'code': 401}), 401


def _revoked_response():
    """Refusal for a session that WAS valid and no longer is -- the account
    was deleted, or its `session_version` moved past this session's. Distinct
    from `_unauthenticated_response` on purpose: this one tells a real,
    already-identified user why they are suddenly being asked to sign in
    again, which is the difference between a product that looks broken and
    one that looks deliberate."""
    return jsonify({'error': 'Session expired or revoked. Please log in again.', 'code': 401}), 401


def _session_version_is_stale(row) -> bool:
    """Has this session been revoked by a `session_version` bump?

    `create_session` stamps the account's `session_version` into the cookie
    (`mt_session_version`), and `onboarding_routes.py` increments the column
    on password reset (:299), enable/disable (:437), clinic-role change
    (:462) and permission change (:490) -- four places whose comments all
    claim the bump invalidates every session issued before it. Until this
    function existed, nothing ever compared the two values, so all four
    bumps revoked nothing at all and a cookie captured before a password
    reset kept working after it.

    STRICTLY OLDER is the rejection condition, not "different". A cookie
    claiming a HIGHER version than the database can only really mean the
    database went backwards -- a restore from a pre-migration backup, say --
    and logging every till in the shop out because the owner restored a
    backup would be a self-inflicted outage, not a security win. That case is
    also not an escalation: a cookie cannot grant itself anything by naming a
    version number, it can only fail to keep up with one.

    A cookie with no `mt_session_version` at all reads as version 1, matching
    both the column's DEFAULT and `create_session`'s own fallback -- so a
    session issued before this field existed is revoked by the first bump,
    which is the correct outcome, rather than being either grandfathered in
    forever or logged out for no reason.
    """
    try:
        stored = int(row['session_version'] if row['session_version'] is not None else 1)
        presented = int(session.get('mt_session_version') or 1)
    except (TypeError, ValueError):
        # An unparseable version on either side is not something to guess
        # about -- treat it as stale and make the user log in again.
        return True
    return presented < stored


def mt_login_required(f):
    """Require a valid multi-tenant session.

    FAILS CLOSED. This used to swallow every exception from the lookup below
    with `except Exception: pass`, commented "Fail-open only for a transient
    local-SQLite read error, not for a missing account." The reasoning does
    not survive contact with a real till: `database is locked` is the
    ordinary outcome of a write holding the lock while a report query runs,
    it is transient in duration only, and for as long as one lasted this
    decorator served every protected route in the product to any request
    carrying any session cookie -- including one belonging to an account that
    had since been disabled. An authorization check that cannot reach its
    facts has not learned that the request is fine; it has learned nothing,
    and "nothing" must refuse.

    The refusal does NOT clear the session. Clearing would turn two seconds
    of lock contention into "every till in the shop was logged out
    mid-sale", and buys no security: this check re-runs on every single
    request, so refusing THIS one is the entire requirement. The branches
    that DO clear are the two where the finding is permanent -- the account
    is gone, or the session has been explicitly revoked.

    DEVICE BINDING is deliberately NOT enforced here yet -- see the note in
    `device_registry.allowed_on_device`'s docstring and
    docs/launch-readiness/multi-device-design.md §3. Nothing in production
    writes a `user_devices` grant, so a fail-closed device check added today
    would lock every existing install out on upgrade, and the only
    non-lockout wiring (grant-on-first-login) would be an authorization check
    that hands out the privilege it is checking for -- precisely the bug
    `device_context.local_device_is_admin`'s docstring records as the
    audit-log hole of 2026-08-20. The missing piece is an admin-facing
    terminal-enrolment surface, which is a later slice.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('is_demo_mode'):
            return f(*args, **kwargs)

        if 'mt_user_id' not in session:
            return _unauthenticated_response()

        try:
            conn = _get_registry_conn()
            try:
                row = conn.execute(
                    "SELECT status, session_version FROM users WHERE id=?",
                    (session['mt_user_id'],)
                ).fetchone()
            finally:
                conn.close()
        except Exception as exc:
            # Logged, not silently swallowed: a fail-closed decorator that
            # says nothing turns a database fault into an unexplained wave of
            # 401s, and the support call that follows has nothing to work
            # from. The user id is safe to log (it is not a secret); the
            # session contents are not logged at all.
            log.warning(
                "mt_login_required: refusing request -- registry lookup for user %s failed: %s: %s",
                session.get('mt_user_id'), type(exc).__name__, exc,
            )
            return _unauthenticated_response()

        if not row:
            session.clear()
            return _revoked_response()
        if row['status'] == 'disabled':
            session.clear()
            return jsonify({'error': 'Your account has been disabled. Contact your administrator.', 'code': 401}), 401
        if _session_version_is_stale(row):
            # Cleared, so a stolen cookie cannot simply be replayed until the
            # next bump -- and so this audit row is written once per revoked
            # session rather than once per retry.
            user_id = session.get('mt_user_id')
            company_id = session.get('company_id')
            session.clear()
            _audit(company_id, user_id, SESSION_REVOKED,
                   context={'reason': 'session_version_bumped'})
            return _revoked_response()

        return f(*args, **kwargs)
    return decorated


def require_clinic_role(*allowed_roles):
    """Gate a clinic endpoint to specific clinic roles (e.g. 'doctor'). The
    global company admin (role='admin') and an active demo session always
    pass. Fail-closed for clinic staff, fail-open only when the DB is
    unreachable."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('is_demo_mode'):
                return f(*args, **kwargs)
            if session.get('mt_role') == 'admin':
                return f(*args, **kwargs)

            clinic_role = session.get('clinic_role')
            if clinic_role is None:
                try:
                    conn = _get_registry_conn()
                    row = conn.execute(
                        "SELECT clinic_role FROM users WHERE id=?",
                        (session.get('mt_user_id'),)
                    ).fetchone()
                    conn.close()
                    clinic_role = (row['clinic_role'] if row else '') or ''
                except Exception:
                    return f(*args, **kwargs)  # fail-open only if DB unreachable

            if clinic_role in allowed_roles:
                return f(*args, **kwargs)
            return jsonify({
                'error': 'This area is restricted to: ' + ', '.join(allowed_roles) + '.',
                'code': 403
            }), 403
        return decorated
    return decorator


# ── Capability codes ─────────────────────────────────────────────────────────
#
# The finer half of authorization, and the thing `mt_require_subsystem` below
# has never been able to express. That decorator answers ONE question -- "may
# this account touch Retail at all?" -- so a shop that answered yes for a
# cashier answered yes for adjusting stock, reading the debtor book, changing
# the tax mode and wiping the company, because all ~80 routes carried the same
# single literal. Capability codes (design §3) split that one answer into
# eight, stored as rows in the same `user_permissions` table the subsystem
# grant already lives in -- see commercial_runtime/identity/user_accounts.py
# for the codes, the per-role defaults, and why the table was reused rather
# than a ninth one invented.
#
# The two are complementary and BOTH run: the subsystem gate is the licence /
# module check, the capability gate is the person check. A mutation route
# needs both.


class CapabilityLookupError(RuntimeError):
    """The registry could not be consulted, so this request's capability is
    UNKNOWN -- which is not the same as absent, and must not be reported as
    either 'granted' or 'plainly denied' without saying so."""


def _read_capability(user_id, code) -> bool:
    """Does `user_id` hold `code`? Raises CapabilityLookupError if the
    registry cannot be read.

    Delegates the actual rule to `user_accounts.user_has_capability` rather
    than re-typing the SQL, so "a missing row means denied" is stated in
    exactly one place. Imported inside the function on purpose:
    `user_accounts` imports `commercial_runtime.security.passwords`, and a
    module-level import here would put that on the critical path of every
    process that merely wants a login decorator.
    """
    from commercial_runtime.identity import user_accounts
    try:
        conn = _get_registry_conn()
        try:
            return user_accounts.user_has_capability(conn, user_id, code)
        finally:
            conn.close()
    except Exception as exc:
        raise CapabilityLookupError(str(exc)) from exc


#: The one route-level refusal string. A fixed literal, not an f-string
#: naming the code: 'retail.stock.adjust' is a developer-facing identifier, it
#: is not in the translation catalogs, and interpolating it would both leak
#: the internal permission vocabulary to every user and guarantee the sentence
#: can never be translated. This exact string is a key in BOTH
#: products/retail/frontend/locales/en.json and ar.json -- asserted against
#: the string a real route really returns by
#: retail_route_capability_matrix_test.py, so code and catalog cannot drift.
CAPABILITY_DENIED_MESSAGE = 'You do not have permission for this action. Ask your store administrator.'


def _capability_denied_response():
    """THE refusal shape for "you are signed in, but not for this".

    Carries the message under BOTH `error` and `message`. That is not
    belt-and-braces sloppiness: this module's own refusals have always used
    `error` (see `_unauthenticated_response`), while every route in
    products/retail/backend/api/retail_api.py answers with `message`, so the
    retail frontend reads `message` and would render a bare `error` payload
    as an empty toast. A refusal the user cannot read is a refusal they will
    file as "the app froze".
    """
    text = CAPABILITY_DENIED_MESSAGE
    return jsonify({'status': 'error', 'error': text, 'message': text, 'code': 403}), 403


def session_has_capability(code: str) -> bool:
    """Non-decorator form, for the checks a decorator cannot express.

    Some authorities are not a whole route -- a discount is a FIELD on a sale,
    and credit terms are two fields on a customer update. Gating the route
    would refuse the whole sale to a cashier who is allowed to sell; gating
    nothing would mean `retail.discount` is a code no code path reads. So the
    handler asks this, on the specific request that carries the specific
    field.

    FAILS CLOSED, including when the registry cannot be read, and for the same
    reason `mt_login_required` now does: a check that could not reach its
    facts has not learned that the request is fine, it has learned nothing.
    Callers get a plain bool, so the failure is logged here rather than being
    silently indistinguishable from an ordinary denial.
    """
    if session.get('is_demo_mode'):
        return True
    if session.get('mt_role') == 'admin':
        return True
    user_id = session.get('mt_user_id')
    if not user_id:
        return False
    try:
        return _read_capability(user_id, code)
    except CapabilityLookupError as exc:
        log.warning(
            "session_has_capability(%s): refusing -- registry lookup for user %s failed: %s",
            code, user_id, exc,
        )
        return False


#: The branch a scoped user is confined to -- launch-readiness account-
#: hierarchy design §3.3/§4.2 D6. Placed here, next to `_read_capability`, on
#: purpose: it is that function's sibling, not its replacement -- a
#: capability answers "may this account do X at all"; scope answers "over
#: which company DATA".


class BranchScopeLookupError(RuntimeError):
    """The registry could not be consulted, so this caller's branch scope is
    UNKNOWN -- deliberately never swallowed into a return value the way
    `session_has_capability` swallows `CapabilityLookupError` into `False`.

    `False` is a safe default for a capability (an unknown grant reads as
    "not granted"). `None` is NOT a safe default for a scope: `None` is the
    single MOST PERMISSIVE value `session_branch_scope()` can return -- it
    means "every branch". A caller that could not determine scope and
    quietly defaulted to `None` would fail OPEN at exactly the moment it
    needed to fail closed, handing a scoped account every branch's data the
    instant the registry hiccups. So this raises, matching `_read_capability`
    (which also raises rather than defaulting) -- NOT
    `session_has_capability` (which is the right shape for a boolean, and
    the wrong one here). Every caller (D7's data-plane enforcement in
    `products/retail/backend/api/retail_api.py`) must treat this as "refuse
    the request", never as "treat as unscoped".
    """


def session_branch_scope():
    """The branch this session is confined to, or `None` for "every branch".

    - The owner (`mt_role == 'admin'`) is NEVER scoped -- always `None`,
      regardless of whatever (if anything) is stored on the admin's own row.
      Structural, matching D5's refusal to let anyone set the admin's own
      `branch_scope_uid` in the first place -- see design §3.3 ("branch_
      scope_uid... admin is always effectively NULL") and onboarding_
      routes.py's `update_branch_scope`.
    - A demo session reads the same way as admin, matching every other
      admin-shaped bypass already in this module
      (`session_has_capability`, `mt_require_capability`) -- a walkthrough
      must not appear to be the one account in the whole product that is
      always scoped.
    - Everyone else resolves to their stored `users.branch_scope_uid`, read
      FRESH from the registry on every call -- never from the session
      cookie, which carries no scope field and must not grow one (design §4.1
      G6: "the delegated gate resolves the creator's role, scope... at
      request time... never from the session cookie").

    Raises `BranchScopeLookupError` on a registry read failure or a missing
    user row. Does NOT catch and default to `None` -- see that class's
    docstring for why `None` is never a safe fallback here. Callers must
    handle the exception themselves and refuse (reads: coerce to a value
    that matches nothing; mutations: refuse the write) rather than let a
    lookup failure silently read as "unscoped".
    """
    if session.get('is_demo_mode'):
        return None
    if session.get('mt_role') == 'admin':
        return None
    user_id = session.get('mt_user_id')
    if not user_id:
        # No identified caller at all -- reachable only ahead of
        # mt_login_required (a caller-ordering bug, not a registry fault).
        # Still not `None`: an unidentified caller must never be handed
        # "every branch" by default.
        raise BranchScopeLookupError('session_branch_scope: no mt_user_id in session')
    try:
        conn = _get_registry_conn()
        try:
            row = conn.execute(
                "SELECT branch_scope_uid FROM users WHERE id=?", (user_id,)
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        raise BranchScopeLookupError(str(exc)) from exc
    if row is None:
        # The row this session names is gone (or the registry answered
        # something malformed). `mt_login_required` will refuse this
        # session's very next request once it re-reads the same missing
        # row, but THIS function must not paper over the gap by reading it
        # as "unscoped" in the meantime.
        raise BranchScopeLookupError(f'session_branch_scope: no such user {user_id!r}')
    return row['branch_scope_uid']


def mt_require_capability(code):
    """Require one capability code (design §3) on this route.

    One refusal string for every route, deliberately: a per-route message
    would be one more translation key per route, and 60 near-identical
    sentences is how a catalog stops being maintained. The two places that DO
    need their own wording are the in-handler checks
    (`session_has_capability`), because "you may not discount" is genuinely
    different information from "you may not do this".

    Written BELOW `@mt_login_required` at every call site, so the login check
    runs first and an anonymous request is answered 401, not a 403 that would
    confirm the route exists and name the capability guarding it.
    Order is asserted, not merely documented, by
    retail_route_capability_matrix_test.py.

    ADMIN BYPASS is unchanged from `mt_require_subsystem` below -- design §3
    says it stays -- and reads the session's role, exactly as its sibling
    does. That is safe here rather than a stale-privilege hole because
    `mt_login_required` has already re-read this account on THIS request and
    refused it if the row is gone, the account is disabled, or
    `session_version` has moved past the cookie; every route that changes a
    role or a permission bumps that version (onboarding_routes.py), so a
    demoted admin's next request is refused before it ever reaches here.

    FAILS CLOSED on a registry read failure. This is deliberately NOT the
    `except Exception: pass` that `mt_require_subsystem` still has forty lines
    below -- that one is a known fail-open which this slice was not scoped to
    change, and repeating it here would have made the finer gate weaker than
    the blanket one it exists to tighten.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('is_demo_mode'):
                return f(*args, **kwargs)
            if session.get('mt_role') == 'admin':
                return f(*args, **kwargs)

            user_id = session.get('mt_user_id')
            if not user_id:
                # Reachable only if this decorator is ever used without
                # mt_login_required above it. Refuse rather than assume.
                return _unauthenticated_response()

            try:
                allowed = _read_capability(user_id, code)
            except CapabilityLookupError as exc:
                log.warning(
                    "mt_require_capability(%s): refusing request -- registry lookup "
                    "for user %s failed: %s", code, user_id, exc,
                )
                return _capability_denied_response()

            if not allowed:
                return _capability_denied_response()
            return f(*args, **kwargs)
        return decorated
    return decorator


def _subsystem_denied_response(subsystem):
    """THE refusal shape for 'signed in, but not granted this subsystem' --
    and, because `mt_require_subsystem` fails closed (below), also the shape
    for a registry read failure. Deliberately identical for both, for the
    same reason `_unauthenticated_response` is deliberately identical for a
    missing session and a broken read: the response must not become a health
    oracle for whether this shop's registry database is currently healthy.
    """
    return jsonify({'error': f'Access denied to {subsystem}. Contact your Admin.', 'code': 403}), 403


def mt_require_subsystem(subsystem):
    """Require a valid license AND employee permission for a specific subsystem.

    FAILS CLOSED on a registry read failure, matching the fix
    `mt_login_required` and `mt_require_capability` already carry. This was
    the one decorator still carrying the exact fault `mt_login_required`
    used to: `except Exception: pass`, "Fail-open only for a transient
    local-SQLite read error, not for a missing account." That reasoning does
    not survive contact with a real till any better here than it did there --
    `database is locked` is the ordinary outcome of a write holding the lock
    while a report query runs, and for as long as one lasted this decorator
    served every request carrying any session, regardless of whether the
    account actually held the subsystem. Worse, it made this BLANKET gate
    weaker than the FINE-GRAINED `mt_require_capability` gate stacked right
    on top of it on every mutating retail route: the same fault that this
    decorator waved through used to be correctly refused one decorator later.

    The refusal on a read failure reuses `_subsystem_denied_response` --
    exactly the shape an ordinary "you don't hold this subsystem" denial
    already returns -- so a caller cannot use the response to distinguish
    "the registry is unhealthy" from "you were never granted this" and probe
    for the former.

    Does NOT call `session.clear()`, for the same reason `mt_login_required`
    doesn't for its own transient-fault branch: this check re-runs on every
    request, so refusing THIS one is the whole of the requirement, and
    clearing the session would turn a couple of seconds of lock contention
    into every till in the shop being logged out mid-sale for nothing.
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if session.get('is_demo_mode'):
                return f(*args, **kwargs)

            company_id = session.get('company_id')
            user_id    = session.get('mt_user_id')
            role       = session.get('mt_role')

            if not company_id:
                return jsonify({'error': 'Tenant context missing.'}), 403

            if not _is_module_enabled(company_id, subsystem):
                return jsonify({'error': f'License for {subsystem} is missing or expired.', 'code': 402}), 402

            if role == 'admin':
                return f(*args, **kwargs)

            try:
                conn = _get_registry_conn()
                try:
                    row = conn.execute(
                        "SELECT access_level FROM user_permissions WHERE user_id=? AND subsystem=?",
                        (user_id, subsystem)
                    ).fetchone()
                finally:
                    conn.close()
            except Exception as exc:
                # Logged, not silently swallowed -- see mt_login_required's
                # identical reasoning: a fail-closed decorator that says
                # nothing turns a database fault into an unexplained wave of
                # 403s with nothing to debug from.
                log.warning(
                    "mt_require_subsystem(%s): refusing request -- registry lookup "
                    "for user %s failed: %s: %s", subsystem, user_id, type(exc).__name__, exc,
                )
                return _subsystem_denied_response(subsystem)

            if not row or row['access_level'] == 'none':
                return _subsystem_denied_response(subsystem)

            return f(*args, **kwargs)
        return decorated
    return decorator
