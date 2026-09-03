"""Aura Retail -- launch-readiness (2026-09-03): SYNC_RELAY_BASE_URL
precedence between an operator's explicit AURA_SYNC_RELAY_URL and the
sync_relay_base_url Owner persisted to licensing.db at activation.

See config.py's _resolve_effective_sync_relay_base_url() docstring for the
full rule this pins: an operator's explicit env var always wins; a
persisted value is used only as a fallback, and only if it passes the same
validate_sync_relay_url() check a typed env var gets.

Pure-function tests -- no real licensing.db needed (see
retail_sync_relay_persisted_discovery_test.py for the real end-to-end
DB-read wiring, in its own process per this suite's one-pytest-process-
per-file convention -- config.py / app.py resolve environment variables
once at import time, see products/run_all_tests.py's docstring).

Run:
    pytest products/retail/tests/retail_sync_relay_precedence_test.py -v
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_sync_relay_precedence_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_SYNC_RELAY_URL", None)

from config import _resolve_effective_sync_relay_base_url  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def test_env_var_wins_when_both_are_set():
    result = _resolve_effective_sync_relay_base_url(
        "https://operator-chosen-relay.example",
        "https://owner-discovered-relay.example",
    )
    assert result == "https://operator-chosen-relay.example"


def test_persisted_value_used_when_env_var_is_empty():
    result = _resolve_effective_sync_relay_base_url(
        "",
        "https://owner-discovered-relay.example",
    )
    assert result == "https://owner-discovered-relay.example"


def test_both_empty_yields_empty():
    assert _resolve_effective_sync_relay_base_url("", "") == ""


def test_invalid_persisted_value_is_rejected_and_sync_stays_off():
    # Same shape as the real hole validate_sync_relay_url() exists to
    # close -- http:// against a non-loopback host -- but arriving from
    # Owner's activation response rather than a human operator's typed env
    # var. Must be silently discarded, not used.
    result = _resolve_effective_sync_relay_base_url("", "http://evil.example.com")
    assert result == ""


def test_env_var_wins_even_when_the_env_var_itself_is_invalid():
    """Precedence, not validity, decides the winner: an operator's explicit
    (even broken) env var must never be silently replaced by a discovered
    fallback -- SYNC_RELAY_URL_PROBLEMS is what surfaces the breakage, not
    a silent substitution."""
    result = _resolve_effective_sync_relay_base_url(
        "http://not-loopback.example",
        "https://owner-discovered-relay.example",
    )
    assert result == "http://not-loopback.example"
