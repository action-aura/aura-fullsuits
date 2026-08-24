"""
Aura FullSuits -- identity-side `company_id` rebind (registry v4).

Launch-readiness Phase 5 prerequisite #1 (ROADMAP.md's 2026-08-21 reservation;
see docs/launch-readiness/phase5-prerequisites.md §1 for the full design and
docs/launch-readiness/multi-device-design.md for where this sits in the wider
sync programme). This is the SIBLING of
products/retail/backend/database/schema.py's v14 rebind
(`rebind_company_id`/`_migrate_rebind_company_id_to_owner_issued`), mirrored
deliberately rather than reinvented -- same structure, same refusal shape,
same comment style -- because the two exist for the same reason on opposite
sides of one boundary:

`session['company_id']` -- what `retail_api._cid()` filters essentially every
retail query on -- is populated from THIS database (`mt_auth.create_session`
reads it from registry.db's `users` row). Retail's rows are locally
`company_id`d as `md5(admin_email)` at onboarding, meaningless to Owner. Once
a licence exists, Owner's tenant key is `license_public_id` (confirmed by
reading Owner itself, not assumed -- `owner/app/sync/routes.py` scopes the
whole sync event stream by `SyncEvent.license_id`, and
`owner/app/licensing_service/assertions.py` signs that exact value into every
assertion as `"license_public_id": str(license_row.id)`). NOT
`installation_public_id` -- that identifies one DEVICE's installation, and
adopting it would give every till in one shop a different tenant key, which
is precisely the fragmentation this whole mechanism exists to end.

THE DIRECTION OF TRAVEL MATTERS. Retail's v14 is deliberately inert -- it
only ever CONVERGES onto a tenant key the identity layer has ALREADY adopted,
because rebinding retail.db first while registry.db still says the old key
would make every `WHERE company_id=?` match zero rows: a real shop's entire
history disappears from the UI with no exception, no failed
`integrity_check`, no log line. That is the worst failure shape this product
can have -- silent, total, and indistinguishable from data loss.

So IDENTITY MUST MOVE FIRST, and this module is that move. It does not wait
for anything -- it is the leader, not a follower. `products/retail/backend/
app.py::init_app()` is what makes "first" concrete: it calls
`commercial_runtime.identity.registry_db.init_registry_db()` (which runs this
module's migration step) BEFORE `database.schema.init_retail()` (whose v14
step converges), in that order, inside one synchronous call chain, before the
process ever starts serving a request.
"""
from __future__ import annotations

import os
import sqlite3


class CompanyRebindError(Exception):
    """Raised when `rebind_company_id` cannot identify, unambiguously, which
    tenant's rows it has been asked to move. Deliberately a refusal rather
    than a best guess -- see that function's docstring. Mirrors
    `database/schema.py::CompanyRebindError` in shape and intent; kept as a
    SEPARATE class (not imported from retail) because this module has to stay
    importable by both Retail and Clinic, and importing anything from either
    product's `database/schema.py` would violate that boundary."""


def _app_data_dir(app_data_dir=None):
    """Resolve `AURA_APP_DATA` FRESH on every call rather than once at import
    time. Matches `registry_db.py`'s own module-level formula exactly (this
    file lives in the same `commercial_runtime/identity/` directory, so the
    same `dirname(dirname(__file__))` fallback lands in the same place) --
    but computed per-call, the way `database/schema.py::owner_issued_
    company_id` and `::local_authoritative_company_id` do it, and for the
    identical reason documented there: a module-level constant caches
    whatever `AURA_APP_DATA` was set to at IMPORT time, which is fine for a
    real running process (imports once) but wrong for anything -- tests
    foremost -- that needs to resolve it freshly per call. See
    `products/run_all_tests.py`'s docstring and `device_context.py`'s
    `_resolve_app_data()` for the anti-pattern this avoids.
    """
    if app_data_dir:
        return app_data_dir
    return os.environ.get('AURA_APP_DATA') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def registry_db_path(app_data_dir=None):
    """registry.db's path, resolved fresh -- see `_app_data_dir` above."""
    return os.path.join(_app_data_dir(app_data_dir), 'database', 'registry.db')


def _licensing_db_path(app_data_dir=None):
    """licensing.db's path. Same `database/subsystems/` layout
    `database/schema.py::SUBSYS_DIR` uses for retail.db's sibling
    subsystem databases -- registry.db and retail.db share one
    `AURA_APP_DATA/database/` tree, `subsystems/` underneath it for
    everything that is not itself a product's primary table set."""
    return os.path.join(_app_data_dir(app_data_dir), 'database', 'subsystems', 'licensing.db')


def owner_issued_company_id(app_data_dir=None):
    """The Owner-issued tenant key for this install, or None.

    Deliberate near-duplicate of `database/schema.py::owner_issued_
    company_id` rather than a shared import: that function lives in a
    product's `database/` package, and this module has to stay importable
    from BOTH products (and from Android, where an eager import of
    `commercial_runtime.licensing_contracts` -- which this function
    pointedly does NOT import, for the same reason -- previously crashed the
    whole backend with `ModuleNotFoundError('cryptography')`). Read with
    plain sqlite3 + json only. See the retail sibling's docstring for the
    full reasoning on why every failure here reports the same honest
    outcome: None, never an exception.
    """
    path = _licensing_db_path(app_data_dir)
    if not os.path.exists(path):
        return None
    try:
        conn = sqlite3.connect(path, timeout=5)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            'SELECT assertion_envelope_json FROM licensing_state WHERE id=1'
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row or not row[0]:
        return None
    try:
        import json as _json
        envelope = _json.loads(row[0])
        if not isinstance(envelope, dict):
            return None
        payload = envelope.get('payload')
        if not isinstance(payload, dict):
            return None
        value = payload.get('license_public_id')
    except (ValueError, TypeError, AttributeError):
        return None
    if not value or not isinstance(value, str):
        return None
    return value


def company_scoped_tables(conn):
    """Every real table in registry.db that carries a `company_id` column,
    DISCOVERED from the live schema rather than hardcoded -- see
    `database/schema.py::company_scoped_tables`'s docstring for why a
    hardcoded list is a thing that silently goes stale (retail's own found
    30 tables where the design doc guessed ~13).

    `user_permissions` is correctly absent: it scopes entirely through
    `user_id`, the same shape retail's line-item tables (`sale_items`, etc.)
    use through their parent row. If it ever shows up here, it means
    somebody added a redundant second copy of the tenant key.
    """
    scoped = []
    for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        name = row[0]
        cols = {c[1] for c in conn.execute(f'PRAGMA table_info("{name}")').fetchall()}
        if 'company_id' in cols:
            scoped.append(name)
    return tuple(scoped)


def registry_tenant_ids(conn):
    """The set of distinct `company_id` values actually present, right now,
    across every scoped table in registry.db.

    Exactly one value means this registry is single-tenant (the
    overwhelmingly common case). More than one means CLAUDE.md's "one
    install *can* host more than one company" applies here for real --
    nothing in this module ever acts on an ambiguous registry (see
    `rebind_company_id`'s own refusal), and callers outside this module
    (`products/retail/backend/app.py`'s boot-time convergence guard) use this
    to tell a genuine multi-tenant install apart from a single-tenant one
    that has simply not converged yet, so they never mistake the former for
    the latter and refuse to serve an install that was never broken.
    """
    present = set()
    for table in company_scoped_tables(conn):
        for row in conn.execute(
            f'SELECT DISTINCT company_id FROM "{table}" WHERE company_id IS NOT NULL'
        ).fetchall():
            present.add(row[0])
    return present


def rebind_company_id(conn, new_company_id, old_company_id=None):
    """Move every tenant-scoped row in registry.db from one `company_id` to
    another, in ONE transaction, with row counts verified on both sides.

    Mirrors `database/schema.py::rebind_company_id` structurally -- same
    idempotent no-op/already_bound/rebound status shape, same
    derive-the-old-id-from-the-rows self-healing behaviour, same
    single-BEGIN-IMMEDIATE-with-in-transaction-count-verification safety --
    because it solves the identical problem (one tenant-scoped SQLite
    database, one atomic rewrite, no cross-database transaction available).
    See that function's docstring for the full reasoning; only the
    identity-specific differences are called out below.

    THE ONE STRUCTURAL DIFFERENCE: retail's version waits for something else
    to move first (`_rebind_to_owner_issued`'s deferral gate). This one does
    not -- identity is the LEADER of this two-database rebind, not a
    follower, so there is nothing to defer to. It moves as soon as it has an
    unambiguous old id and a real new one.

    Returns a status dict: `{'status': ..., 'rows': ..., 'old_company_id':
    ..., 'new_company_id': ..., 'tables': (...)}`, status one of 'skipped'
    (no `new_company_id` supplied -- the unlicensed install is the normal
    case), 'already_bound', or 'rebound'. Raises `CompanyRebindError` and
    changes NOTHING when the rows present span more than one non-target
    tenant -- the multi-tenant guard CLAUDE.md requires.

    TWO FIXES FOUND BY LAUNCH-READINESS PHASE 5 VERIFICATION, both folded
    into the same transaction rather than bolted on around it (see the
    inline comments at each site for the full reasoning):

    - Defect 1 (HIGH): a successful rebind now bumps every moved account's
      `users.session_version` in this SAME transaction, so `mt_auth.
      mt_login_required`'s existing stale-version check revokes every live
      session on this tenant together on its next request, instead of an
      already-logged-in admin watching their shop's history vanish because
      their session cookie still carries the OLD `company_id`.
    - Defect 3 (MEDIUM): the before/after row counts this function verifies
      against are now read AFTER `BEGIN IMMEDIATE`, not before it, so a
      concurrent write landing in that gap can no longer trip the
      in-transaction verification into a spurious `CompanyRebindError`.
    """
    if new_company_id is None or (isinstance(new_company_id, str) and not new_company_id.strip()):
        return {
            'status': 'skipped',
            'reason': 'no Owner-issued company_id available',
            'rows': 0,
            'old_company_id': old_company_id,
            'new_company_id': new_company_id,
            'tables': (),
        }

    tables = company_scoped_tables(conn)
    if not tables:
        return {
            'status': 'skipped',
            'reason': 'no company-scoped tables in this database',
            'rows': 0,
            'old_company_id': old_company_id,
            'new_company_id': new_company_id,
            'tables': (),
        }

    present = registry_tenant_ids(conn)

    if old_company_id is None:
        others = {value for value in present if value != new_company_id}
        if not others:
            return {
                'status': 'already_bound',
                'rows': 0,
                'old_company_id': None,
                'new_company_id': new_company_id,
                'tables': tables,
            }
        if len(others) > 1:
            raise CompanyRebindError(
                f'Refusing to rebind company_id: registry.db holds rows under '
                f'{len(others)} different tenant keys ({sorted(map(repr, others))}). '
                f'Guessing which one the licence belongs to would merge two companies '
                f'books into one, irreversibly. Pass old_company_id explicitly.'
            )
        old_company_id = next(iter(others))

    # Any in-flight implicit transaction is committed before BEGIN IMMEDIATE
    # -- Python's sqlite3 opens one automatically on DML, and BEGIN inside an
    # open transaction is an error. Matches `rebind_company_id`'s own note in
    # database/schema.py: this does not hold a transaction across the whole
    # migration chain, so committing what came before is safe.
    if conn.in_transaction:
        conn.commit()
    conn.execute('BEGIN IMMEDIATE')
    try:
        # AUDIT (Defect 3, launch-readiness Phase 5 verification, MEDIUM):
        # before_old/before_new/before_total used to be counted BEFORE this
        # BEGIN IMMEDIATE. IMMEDIATE already takes the write lock up front --
        # that is the whole point of using it instead of a deferred BEGIN --
        # but taking the lock and THEN reading a snapshot that was counted
        # before the lock existed throws that guarantee away: a single
        # concurrent INSERT landing in the gap between the old count and this
        # lock (a till ringing up a sale, ordinary and likely on a busy
        # store) changes what the UPDATE below actually moves, so the
        # in-transaction verification compares a stale "before" against a
        # real "after" and raises CompanyRebindError on a rebind that was
        # actually fine -- a SAFE failure, but exactly how a busy till reaches
        # the split state Defect 2 exists to catch. Counting HERE, after the
        # lock, is what makes the count and the update see the identical
        # snapshot; nothing else can commit a write against this database
        # between this line and the `conn.commit()` below.
        before_old = {
            table: conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (old_company_id,)
            ).fetchone()[0]
            for table in tables
        }
        before_new = {
            table: conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (new_company_id,)
            ).fetchone()[0]
            for table in tables
        }
        before_total = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }
        if sum(before_old.values()) == 0:
            # Nothing to move. The write lock BEGIN IMMEDIATE took is
            # released honestly via rollback (nothing was written, so
            # rollback and commit are equivalent here) rather than held
            # while this function returns -- a caller that goes on to use
            # `conn` for anything else must not inherit an open transaction
            # it never asked for.
            conn.rollback()
            return {
                'status': 'already_bound',
                'rows': 0,
                'old_company_id': old_company_id,
                'new_company_id': new_company_id,
                'tables': tables,
            }

        moved = 0
        for table in tables:
            cur = conn.execute(
                f'UPDATE "{table}" SET company_id=? WHERE company_id=?',
                (new_company_id, old_company_id),
            )
            moved += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        # Defect 1 (launch-readiness Phase 5 verification, HIGH): every
        # activation-time rebind was blanking the shop for anyone already
        # logged in. `mt_auth.create_session` stamps `session['company_id']`
        # into the browser's session cookie at LOGIN time, and
        # `retail_api._cid()` filters essentially every retail query on that
        # cached value. `mt_login_required` re-checks `session_version` on
        # every single request but never re-reads `company_id` itself -- so
        # the moment the UPDATE loop above lands `users.company_id` on
        # `new_company_id`, every session that logged in under the OLD key
        # keeps filtering on a tenant key that no longer matches a single
        # row it owns. That is not a race: it is the deterministic outcome
        # of every successful activation performed against a running
        # process, and it hits the admin who just paid -- the worst possible
        # moment for their shop's history to vanish from the screen.
        #
        # The fix is not to patch the activating admin's one session -- the
        # TENANT KEY changed, so every live session's cached identity is
        # stale, not only theirs; refreshing only the current request would
        # leave every OTHER till in the shop blank, and worse, with nobody
        # watching that request to notice. `mt_login_required` (mt_auth.py)
        # already knows how to reject a session whose `session_version` has
        # fallen behind the account row's -- see `_session_version_is_stale`
        # -- so bumping it HERE, inside the SAME transaction that moves the
        # rows, means either every session on this tenant is revoked
        # together on its very next request (the rebind committed and every
        # cookie is honestly stale) or none of them are (the rebind rolled
        # back and nothing changed) -- never the split outcome where some
        # sessions were "fixed" and others were quietly left pointing at a
        # tenant key with zero matching rows.
        #
        # `new_company_id`, not `old_company_id`: this UPDATE runs AFTER the
        # loop above has already moved `users.company_id` from old to new,
        # so filtering on the new value is what catches exactly the accounts
        # that were just moved. `'users' in tables` guards a database that
        # (hypothetically) has no `users` table at all rather than assuming
        # the column always exists -- `company_scoped_tables` discovers it
        # at runtime like everything else here.
        if 'users' in tables:
            conn.execute(
                'UPDATE "users" SET session_version = session_version + 1 '
                'WHERE company_id=?',
                (new_company_id,),
            )

        for table in tables:
            stranded = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (old_company_id,)
            ).fetchone()[0]
            landed = conn.execute(
                f'SELECT COUNT(*) FROM "{table}" WHERE company_id=?', (new_company_id,)
            ).fetchone()[0]
            total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if stranded or landed != before_old[table] + before_new[table] or total != before_total[table]:
                raise CompanyRebindError(
                    f'company_id rebind failed its own count check on {table!r}: '
                    f'{stranded} row(s) left on the old key, {landed} on the new '
                    f'(expected {before_old[table] + before_new[table]}), '
                    f'{total} rows total (expected {before_total[table]}). '
                    f'Rolled back -- the tenant key is unchanged.'
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return {
        'status': 'rebound',
        'rows': moved,
        'old_company_id': old_company_id,
        'new_company_id': new_company_id,
        'tables': tables,
    }


def _migrate_rebind_registry_company_id_to_owner_issued(conn):
    """One-time migration (registry v4): rebind `company_id` onto the
    Owner-issued tenant key. Wired into `registry_db.py::
    _migrate_registry_schema`, run via `ensure_schema_version`.

    v4 is about being ABLE to rebind, not about having done it. Licensing is
    OFF by default (config.py: with `OWNER_LICENSING_BASE_URL` unset the app
    runs fully unlocked and there IS no Owner-issued id), so the
    overwhelmingly common install reaches this step with nothing to rebind
    to -- a no-op, not an error, and `user_version` still advances, exactly
    mirroring `database/schema.py::_migrate_rebind_company_id_to_owner_
    issued`'s own reasoning.

    An install that activates a licence LATER does not get missed --
    `rebind_company_id_after_activation()` below reaches the same work from
    the licensing activation route, the same two-seams shape retail's v14
    uses.

    `CompanyRebindError` is CAUGHT HERE and only here, for the identical
    reason retail's v14 catches it in its own migration step: raising it out
    of a migration would leave a multi-tenant install unable to advance its
    schema version ever again -- every future migration blocked by a
    refusal that is itself correct and permanent. The direct
    `rebind_company_id()` API still raises, so a caller that asked for the
    rebind explicitly still hears about it.
    """
    new_company_id = owner_issued_company_id()
    if not new_company_id:
        return {
            'status': 'skipped',
            'reason': 'this install has no Owner-issued licence assertion',
            'rows': 0,
            'old_company_id': None,
            'new_company_id': None,
            'tables': (),
        }
    try:
        return rebind_company_id(conn, new_company_id)
    except CompanyRebindError as exc:
        return {
            'status': 'deferred',
            'reason': str(exc),
            'rows': 0,
            'old_company_id': None,
            'new_company_id': new_company_id,
            'tables': (),
        }


def rebind_company_id_after_activation():
    """Activation-time entry point: called after a licence activation
    succeeds, so an install that activates months after it migrated gets
    rebound at that moment instead of waiting for a next migration that may
    never come (licensing is OFF by default, so the common install migrates
    long before it ever activates).

    Opens its OWN connection -- the activation request has none scoped to
    registry.db, and this has to work whether it is called from the
    migration chain (which hands in a connection) or from the licensing
    blueprint's `on_activation_success` hook (which does not). Resolves
    registry.db's path FRESH via `registry_db_path()` rather than importing
    `registry_db.DB_PATH` -- that constant is cached at import time (see
    `_app_data_dir`'s docstring), which is exactly wrong for a function that
    has to find whatever `AURA_APP_DATA` currently points at.

    NEVER RAISES. A licence activation that genuinely succeeded at Owner
    must not be reported back to the customer as a failure because a local
    bookkeeping rewrite hit a locked database -- the customer would retry
    the activation, which is the one action that cannot fix it. Every
    outcome is a status dict; the work is idempotent and reachable again
    from the next activation, the next migration, or (see
    `products/retail/backend/app.py::init_app`) every subsequent boot.
    """
    try:
        new_company_id = owner_issued_company_id()
        if not new_company_id:
            return {
                'status': 'skipped',
                'reason': 'activation left no assertion carrying a license_public_id',
                'rows': 0,
                'old_company_id': None,
                'new_company_id': None,
                'tables': (),
            }
        conn = sqlite3.connect(registry_db_path(), timeout=30)
        try:
            result = rebind_company_id(conn, new_company_id)
            conn.commit()
            return result
        finally:
            conn.close()
    except Exception as exc:
        return {
            'status': 'failed',
            'reason': f'{type(exc).__name__}: {exc}',
            'rows': 0,
            'old_company_id': None,
            'new_company_id': None,
            'tables': (),
        }
