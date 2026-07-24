"""Test-only helper for seeding a valid ACTIVE_ONLINE license state directly
into a product's licensing.db, bypassing the real activation flow entirely.

Used by product functional/regression test suites (clinic_workflow_test.py,
retail_pricing_test.py, etc.) that exercise business-logic mutation routes
now guarded by @require_license_capability (Part T) -- those suites test
clinical/retail workflow correctness, not licensing enforcement (which has
its own dedicated test files, e.g. test_flask_guard.py and the future
clinic/retail capability-guard test suites), so they need a licensed state
to even reach the code under test. Never imported by product/runtime code,
only by tests -- deliberately lives in this package (not a products/*/tests
helper) so both Retail's and Clinic's test suites share one implementation.
"""
from __future__ import annotations

import json
from pathlib import Path

from .state_machine import LicenseState
from .state_repository import LICENSING_SCHEMA_VERSION, LicenseStateRecord, LicenseStateRepository


def seed_active_license(app_data_dir: str, *, product_code: str, platform: str = "WINDOWS") -> None:
    db_path = Path(app_data_dir) / "database" / "subsystems" / "licensing.db"
    repo = LicenseStateRepository(db_path)
    repo.save(
        LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION,
            product_code=product_code,
            platform=platform,
            current_state=LicenseState.ACTIVE_ONLINE.value,
            owner_installation_id="test-fixture-installation",
            license_status="ACTIVE",
            installation_status="ACTIVE",
            subscription_status="ACTIVE",
            entitlements_json=json.dumps({}),
        )
    )
