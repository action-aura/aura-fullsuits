# Phase 5 -- Owner Database Schema (Part D)

PostgreSQL 17, SQLAlchemy 2.x models under `owner/app/models/`, single Alembic migration `owner/migrations/versions/62e4adb0a7b9_initial_owner_schema.py`, applied and verified against a real local Postgres instance (see `owner-local-development-guide.md`).

## Table count: 42 tables (+`alembic_version`)
Verified via `psql \dt` against `aura_owner_dev` after `alembic upgrade head` -- exact count matches `Base.metadata.sorted_tables`.

## Groups (mirrors Part D exactly)
- **Staff/security (10)**: `owner_staff_users`, `owner_roles`, `owner_permissions`, `owner_role_permissions`, `owner_staff_role_assignments`, `owner_staff_sessions`, `owner_staff_invitations`, `owner_mfa_credentials`, `owner_mfa_recovery_codes`, `owner_login_attempts`.
- **Commercial catalog (11)**: `owner_products`, `owner_platforms`, `owner_product_platforms`, `owner_product_versions`, `owner_release_channels`, `owner_plans`, `owner_plan_prices`, `owner_addons`, `owner_entitlement_definitions`, `owner_plan_entitlements`, `owner_addon_entitlements`.
- **Customers (4)**: `owner_customers`, `owner_customer_contacts`, `owner_customer_addresses`, `owner_customer_notes`.
- **Subscriptions/commercial records (6)**: `owner_subscriptions`, `owner_subscription_items`, `owner_subscription_addons`, `owner_subscription_status_history`, `owner_renewal_records`, `owner_payment_records`.
- **License domain (4)**: `owner_licenses`, `owner_license_status_history`, `owner_license_entitlements`, `owner_license_key_issuance_events`.
- **Installations/devices (4)**: `owner_installations`, `owner_installation_status_history`, `owner_device_records`, `owner_activation_events`.
- **Audit/operations (4)**: `owner_audit_log`, `owner_security_events`, `owner_system_settings`, `owner_database_backup_records`.

(`owner_audit_log_chain` from Part D's list is implemented as `previous_hash`/`current_hash` columns directly on `owner_audit_log` rather than a separate table -- ADR-8 -- since the chain is intrinsic to each row, not a separate joinable entity.)

## Design conventions applied uniformly
- Every table: `UUID` primary key (`uuid4()`, Python-side default), `created_at`/`updated_at` timestamps (`TimestampMixin`).
- Every FK-referencing table: real PostgreSQL foreign key constraints (79 total, `information_schema.table_constraints` verified).
- Historical/append-only tables (`*_status_history`, `owner_audit_log`, `owner_login_attempts`, `owner_license_key_issuance_events`): insert-only by construction -- no service function updates or deletes these rows.
- Price history (`owner_plan_prices`): never overwritten, only closed out via `effective_until` and superseded by a new row (`add_plan_price()`).
- No sequential integer ID is ever exposed externally (ADR-7).

## Verification performed this phase
`alembic upgrade head` from empty -> 42 tables created (manual + `owner/tests/test_database.py::test_migration_runs_clean_from_empty_database_and_rolls_back`, which runs this against a disposable scratch database, not the shared dev/test DB). `alembic downgrade base` -> 0 tables remain except `alembic_version` (same test). `compare_metadata()` schema-drift check (`test_no_schema_drift_between_models_and_migration`) confirms the live schema matches the SQLAlchemy models exactly, with zero pending diffs.
