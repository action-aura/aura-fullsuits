"""Aura Retail -- launch-readiness (2026-09-03): SYNC_RELAY_BASE_URL
actually gets discovered from a real licensing.db at config.py import time,
via the real LicenseStateRepository -- the end-to-end counterpart of
retail_sync_relay_precedence_test.py's pure-function precedence tests.

Own subprocess/module (see retail_sync_inert_when_unconfigured_test.py's
docstring): config.py resolves this at import time, so the licensing.db
must exist BEFORE config/app is ever imported in this process.

Run:
    pytest products/retail/tests/retail_sync_relay_persisted_discovery_test.py -v
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_sync_relay_discovery_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
# The condition under test: no explicit operator override at all -- the
# persisted value must be the ONLY thing that turns sync on.
os.environ.pop("AURA_SYNC_RELAY_URL", None)

# Seed licensing.db with a persisted sync_relay_base_url BEFORE importing
# config -- exactly as if this device had already activated in a previous
# process/launch and Owner had told it where its shop syncs (see
# commercial_runtime/licensing_contracts/activation.py's
# ingest_activation_response()). config.py only picks this up on the next
# launch by design -- this test IS that "next launch".
from commercial_runtime.licensing_contracts.state_repository import (  # noqa: E402
    LICENSING_SCHEMA_VERSION,
    LicenseStateRecord,
    LicenseStateRepository,
)

_repo = LicenseStateRepository(DATA / "database" / "subsystems" / "licensing.db")
_repo.save(
    LicenseStateRecord(
        licensing_schema_version=LICENSING_SCHEMA_VERSION,
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        current_state="ACTIVE_ONLINE",
        owner_installation_id="discovery-test-inst",
        sync_relay_base_url="https://owner-issued-relay.actionaura.example",
    )
)

import config as _config_module  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def test_persisted_sync_relay_base_url_is_discovered_with_no_env_var_set():
    assert _config_module.SYNC_RELAY_BASE_URL == "https://owner-issued-relay.actionaura.example"


def test_no_validation_problems_for_the_discovered_value():
    assert _config_module.SYNC_RELAY_URL_PROBLEMS == []
