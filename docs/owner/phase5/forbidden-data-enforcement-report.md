# Phase 5 -- Forbidden Data Enforcement Report (Part W)

## Method
Three independent, automated checks, not documentation-only claims:

1. **Structural column/table scan** (`test_no_model_column_matches_a_forbidden_term`, `test_no_model_table_name_matches_a_forbidden_term`): iterates every table and column in `Base.metadata` (all ~42 Owner tables) and asserts none match any of 13 forbidden substrings (`patient`, `diagnosis`, `prescription`, `medical_note`, `clinical_note`, `sale_line`, `invoice_line`, `inventory_quantity`, `customer_purchase`, `local_database`, `card_number`, `bank_account`, `appointment`).
2. **Serializer construction guard** (`test_serializers_reject_forbidden_field_at_construction_time`): directly attempts to construct 3 different forbidden payloads through `app.api.serializers._guard()` and asserts each raises `ForbiddenFieldError`; also asserts a legitimate payload passes through unchanged.
3. **Fixed-allowlist output proof** (`test_license_check_serializer_output_is_a_fixed_allowlist`): builds a real `License` row against the real database and asserts the live serializer's output key set is exactly the documented 9-key allowlist -- not a superset, not a subset.
4. **Cross-package import scan** (`test_no_import_path_from_owner_into_products_or_commercial_runtime`): greps every `.py` file under `owner/app/` for a top-level `from products...` / `import products...` / `from commercial_runtime...` / `import commercial_runtime...` statement. Zero found.
5. **External API default-off proof** (`test_external_api_blueprint_not_registered_by_default`): asserts `app.config["EXTERNAL_API_ENABLED"] is False` under the default test config and that zero `/api/v1/*` routes exist in `app.url_map` when disabled.

## Result
All 6 tests in `owner/tests/test_data_boundary.py` pass. Zero forbidden terms found in any of the ~42 tables/~250 columns. Zero cross-package imports found. Zero external API routes registered by default.

## What this proves and what it doesn't
Proves: as of this commit, no product business/medical data model exists in Owner, no serializer can silently leak one, and no import coupling exists to the products whose data is forbidden. Does not prove: that a future contributor cannot add a violation later -- that's why these are automated tests re-run on every CI/test invocation, not a one-time manual audit.
