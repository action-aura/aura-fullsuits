# Phase 5 -> Phase 6 Migration Report (Part V)

## Migration
`owner/migrations/versions/60f363ee66e8_phase_6_licensing_activation_service_.py`, revision `60f363ee66e8`, down-revision `62e4adb0a7b9` (the Phase 5 head).

## Added (12 new tables, 0 Phase 5 tables dropped/renamed)
`owner_signing_keys`, `owner_device_public_keys`, `owner_activation_requests`, `owner_signed_assertions`, `owner_entitlement_snapshots`, `owner_external_idempotency_records`, `owner_offline_policies`, `owner_license_offline_policy_assignments`, `owner_security_nonce_records`, `owner_key_rotation_events`, `owner_rate_limit_counters`, `owner_service_health_events`.

## One additive change to an existing Phase 5 table
`owner_licenses.key_secret_hmac`: added a `UNIQUE` constraint (`uq_owner_licenses_key_secret_hmac`). Required so activation can look up a license by its submitted key's HMAC via an indexed equality lookup rather than a table scan. Postgres permits multiple `NULL`s under a unique constraint, so DRAFT licenses with no issued key yet are unaffected. No column was renamed, retyped, or dropped.

## Verified
- **Upgrade on a populated database**: `test_phase6_migration.py::test_upgrade_on_populated_phase5_database_loses_no_records` -- a disposable scratch database is migrated to the Phase 5 revision, seeded with real rows, upgraded to Phase 6 head, and every Phase 5 row is confirmed byte-for-byte present afterward.
- **Downgrade**: the same test then downgrades back to the Phase 5 revision and confirms all 12 Phase 6 tables are gone and the original Phase 5 data is still intact.
- **Zero schema drift**: `test_no_schema_drift_after_phase6` -- `alembic`'s `compare_metadata()` against the live schema returns an empty diff.
- **Manual dev-database round-trip**: `alembic downgrade base && alembic upgrade head` performed twice during this phase's development on the real `aura_owner_dev` database (once immediately after generating the migration, once again after a mid-development model fix), both clean.
- **Development backup taken first**: `pg_dump` snapshot of `aura_owner_dev` taken via `pg_dump --format=custom` immediately before the first real application of this migration (Part V's explicit instruction), retained only transiently in the gitignored `owner/var/backups/` directory and removed after the phase's manual verification concluded.

## No data loss, no Phase 5 test regressions
Full Phase 5 suite (85 tests) re-run after the Phase 6 schema was applied -- all 85 still pass unmodified (one Phase 5 test, `test_external_api_blueprint_not_registered_by_default`, was *edited*, not because migration broke it, but because Phase 6's test harness deliberately enables the external API blueprint for its own HTTP-level tests; the edited version proves the same real invariant -- the flag defaults to false in every real config class -- more directly than the original did. See `phase6-test-report.md`).
