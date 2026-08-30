"""registry.db v7 -- ONE nullable column, `users.branch_scope_uid`.

ROADMAP.md's 2026-08-30 "registry schema v7 CLAIMED for branch-scoped users"
entry and docs/launch-readiness/account-hierarchy-design.md §3.3/§6 (Reading
A -- the owner creates every account, and each account is scoped to a
branch). Backs the chain-oversight ask: a chain of five stores needs a
branch manager who sees only their own store and a head office that sees
all of them.

    ALTER TABLE users ADD COLUMN branch_scope_uid TEXT   -- NULL = every branch

WHY NULLABLE WITH NO DEFAULT IS WHAT MAKES THIS SAFE FOR CLINIC.
`users` lives in `registry.db`, SHARED by Retail and Clinic (this file's
sibling `account_schema.py`/`registry_quarantine_schema.py` are the same
shared-table shape). A nullable column with no `DEFAULT` clause means SQLite
backfills every existing row -- Retail's and Clinic's alike -- with NULL, and
NULL is defined here to mean "every branch": i.e. exactly today's behaviour,
for both products, for every row that existed before this migration ever
ran. No backfill loop, no data rewrite, nothing to get wrong. Nothing in
Clinic's code path reads this column at all (see this module's own test file
for the migrate-and-behave-unchanged proof, not merely an assertion of it),
and enforcement of what a NON-NULL value means lives entirely in Retail's
branch-taking routes (`commercial_runtime/identity/mt_auth.py::
session_branch_scope`, `products/retail/backend/api/retail_api.py`) --
neither of which Clinic imports for this purpose. A NOT NULL column, or one
with a DEFAULT other than NULL, would have forced a value onto every existing
Clinic row and made "no branch dimension" impossible to express for a
product that has no branches at all.

Holds the branch **`uid`**, never the local integer `branches.id` --
deliberately, and for the identical reason `account_schema.py`'s own `uid`
column exists: an integer id is a plain per-device autoincrement (the SAME
physical branch carries a DIFFERENT id on every device that has ever
self-healed or re-seeded one -- see `retail_api.py::_default_branch`'s own
docstring), so pinning the integer would break the moment a device's branch
rows get renumbered. `uid` is the one identity that means the same thing on
every device.

OPAQUE TO THIS LAYER, ON PURPOSE. This module (and every other file under
`commercial_runtime/identity/`) never validates a `branch_scope_uid` value
against a `branches` table -- there is no `branches` table here to validate
against; that table lives in `retail.db`, a different product's database,
and `commercial_runtime.identity` must stay product-agnostic (it is imported
by both Retail and Clinic). The retail-facing caller
(`commercial_runtime/identity/onboarding_routes.py::update_branch_scope`,
called only from `products/retail/frontend/employees.js`, which only ever
offers the company's real branches in its picker) is where that validation
belongs, and where it lives.

Additive only, matching every migration in this package: a single `ALTER
TABLE ... ADD COLUMN`, guarded by a `PRAGMA table_info` check so re-running
this function against a database that already has the column (a re-run after
a partial failure, or one that started life at v7 or later) is a no-op
rather than an error.

Wired into registry_db.py's `_migrate_registry_schema` at
REGISTRY_SCHEMA_VERSION=7 and applied through
commercial_runtime.security.migration_safety.ensure_schema_version, which
takes a live backup and runs PRAGMA integrity_check before AND after, only
advancing `user_version` on full success.
"""
from __future__ import annotations

import sqlite3


def apply_branch_scope_schema(conn: sqlite3.Connection) -> None:
    """Idempotent -- inspects the live schema before altering, matching
    `account_schema.py::apply_account_schema`'s own column-by-column guard
    rather than `registry_quarantine_schema.py`'s bare `CREATE TABLE IF NOT
    EXISTS` (there is no `ADD COLUMN IF NOT EXISTS` in SQLite, so the guard
    has to be explicit)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "branch_scope_uid" not in cols:
        # No DEFAULT clause -- see the module docstring's "WHY NULLABLE WITH
        # NO DEFAULT" section for why that is precisely what makes every
        # existing row (Retail's and Clinic's alike) mean "every branch"
        # rather than requiring a backfill this migration would otherwise
        # have to get right.
        conn.execute("ALTER TABLE users ADD COLUMN branch_scope_uid TEXT")
    conn.commit()
