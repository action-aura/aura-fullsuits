# Phase 9R — Production PostgreSQL (M4, Executed Against Real Local PostgreSQL 17.10)

Every finding below is real evidence from the actual local PostgreSQL 17.10
instance this project's dev/test environment already uses — not simulated,
not assumed. No real staging/production Postgres exists yet
(`infrastructure-availability-audit.md`), so this milestone did everything
verifiable against a real (if dev-scale) Postgres instance, and named
exactly what still needs a production-scale instance to verify for real.

## Version

**PostgreSQL 17.10** — matches the M1 architecture decision exactly (no
drift between what was decided and what's actually running).

## Role privilege audit (real query against `pg_roles`)

```sql
SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolbypassrls
FROM pg_roles WHERE rolname = 'aura_owner';
-- aura_owner | f | f | t | f
```

| Privilege | Value | Assessment |
|---|---|---|
| Superuser | `false` | Correct — application connection is not a superuser |
| Create role | `false` | Correct — application cannot create/escalate other roles |
| Create database | **`true`** | **Gap** — not least-privilege. See disposition below |
| Bypass RLS | `false` | Correct (RLS not currently used by this schema, but the role can't bypass it if it ever is) |

**Disposition:** `rolcreatedb=true` is real and worth flagging, but this is
the *shared local development role* every developer's dev/test environment
already depends on — revoking it here would be a global, hard-to-reverse
change to shared local infrastructure outside this worktree's scope, not a
Phase 9R action. The actual requirement this milestone enforces is
structural: **the staging/production role must be provisioned from
scratch without `CREATEDB`, `CREATEROLE`, or superuser from day one** —
recorded here as a hard requirement for the M18 remote-staging provisioning
step (not yet executed, blocked on infrastructure), not something to retrofit
onto the local dev role.

## Connection-level hardening (implemented and tested)

Real gap found: `statement_timeout`, `lock_timeout`, and
`idle_in_transaction_session_timeout` were all `0` (unlimited) on this
connection. Fixed in `owner/app/extensions.py` (`init_db()`) — timeouts are
now set via libpq connection options (`-c statement_timeout=... -c
lock_timeout=... -c idle_in_transaction_session_timeout=...`), applied on
every connection the app opens, portable across whatever role/host the
connection string points at (no `ALTER ROLE` privilege required). Defaults:
30s / 10s / 120s, all configurable (`OWNER_DB_STATEMENT_TIMEOUT_MS` etc.,
`configuration-contract.md`).

Real evidence, not just configuration — a live `SELECT pg_sleep(2)` against
a 200ms `statement_timeout` was actually issued and actually terminated by
PostgreSQL (`tests/test_phase9r_postgresql_hardening.py::test_statement_timeout_actually_terminates_a_runaway_query`).

## Bounded connection pool (implemented and tested)

`init_db()` now also takes `pool_size`/`max_overflow`
(`OWNER_DB_POOL_SIZE`=5, `OWNER_DB_MAX_OVERFLOW`=10 by default — deliberately
small since it multiplies by Gunicorn worker count, M5). Verified live
against the running engine's actual pool object, not just the config value.

## Timezone and encoding

```sql
SHOW timezone;         -- Asia/Amman
SHOW server_encoding;  -- UTF8
```

Encoding matches the requirement (UTF-8) exactly. Timezone is the local
system's zone, not UTC — every timestamp column in this schema already
uses `TIMESTAMP WITH TIME ZONE` (confirmed in `installations.py` and
consistent with `TimestampMixin`), so stored values are correct regardless
of session timezone; only the *display* of `SHOW`/`NOW()` output is
zone-local rather than UTC. Recommend `timezone = 'UTC'` in the real
staging/production `postgresql.conf` for operational clarity (log
timestamps, ad-hoc `psql` queries) — a server configuration decision for
M18 provisioning, not a schema or application defect.

## Migration state (real, executed)

```
$ alembic current
f5959fdb9738 (head)

$ alembic check
No new upgrade operations detected.
```

Migration is current, zero schema drift, checked *before* this milestone's
own change was added — proving the Phase 9.5E baseline itself carried zero
drift into Phase 9R.

## Real index gap found and fixed

Inspected actual query patterns (not assumed) via `grep` against
`app/licensing_service/activation.py`: every license activation and
check-in/refresh request looks up `Installation` by
`(license_id, installation_label)`:

```python
select(Installation).where(
    Installation.license_id == locked_license.id,
    Installation.installation_label == body["installation_id"],
)
```

Cross-checked against `pg_indexes` — `owner_installations` had **no index
at all beyond its primary key**. Every activation/refresh request — the
single most frequent remote-licensing operation this whole phase is about
— was a full table scan. Fixed:

- `owner/app/models/installations.py`: added
  `Index("ix_owner_installations_license_id_installation_label", "license_id", "installation_label")`
- Migration `13944658bddf` (autogenerated, reviewed, purely additive —
  `create_index`/`drop_index` only)
- Applied to the real local database; `alembic check` re-run clean
  afterward (zero drift, confirms the migration produces exactly the
  schema the model now declares)
- 45 installation/activation/check-in-adjacent tests re-run clean after the
  model + migration change

Other candidate index gaps noted but not acted on this milestone (lower
traffic paths, not named in M4's own "critical remote paths" list):
`owner_subscriptions` has no index on `status` (used by dashboard filters)
or `sales_order_id`; `owner_customers` has only one non-PK index. Tracked
as index-monitoring candidates for M14/M22, not asserted as blockers here.

## EXPLAIN ANALYZE — honestly deferred, not faked

M4 asks for real `EXPLAIN ANALYZE` on critical remote paths (login,
customer/subscription/license lookup, activation, refresh, release lookup,
download authorization, dashboards). The local dev database has real but
tiny row counts (`owner_customers`: 18, `owner_subscriptions`: 14,
`owner_licenses`: 13) — running `EXPLAIN ANALYZE` against this would
produce a real query plan, but a meaningless one: PostgreSQL's planner
correctly prefers a sequential scan over an index scan on tables this
small regardless of indexing, so the result would not demonstrate anything
about production-scale behavior and would be misleading to present as
performance evidence. **Not executed here — explicitly deferred to M22**
("Performance and Capacity"), which already calls for a representative
synthetic dataset at real scale; that's where `EXPLAIN ANALYZE` against
these exact paths will produce evidence that actually means something.

## What remains genuinely NOT VERIFIED

- Real production-scale `EXPLAIN ANALYZE` (M22, needs real data volume)
- Storage/index/slow-query *monitoring* — as opposed to the one-time audit
  done here — is an ongoing observability concern (M14)
- Everything requiring an actual remote managed/self-managed production
  Postgres instance (encrypted transport across a real network boundary,
  real backup execution) — blocked on infrastructure
  (`infrastructure-availability-audit.md`)
