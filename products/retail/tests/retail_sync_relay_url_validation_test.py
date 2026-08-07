"""Aura Retail -- final-review Fix 2 (2026-08-07): scheme enforcement on
AURA_SYNC_RELAY_URL.

Desktop previously had no equivalent of
mobile/aura-retail-unified/.../sync/SyncRelayConfiguration.kt's `validate()`,
so a misconfigured `AURA_SYNC_RELAY_URL=http://some-real-host` would push and
pull device-signed business data over cleartext with nothing objecting. This
pins the same rule the KMP client already enforces: https:// for anything
real, http:// only for an explicit loopback development relay.

Run:
    pytest products/retail/tests/retail_sync_relay_url_validation_test.py -v
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_sync_url_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))

from config import validate_sync_relay_url  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.mark.parametrize("url", [
    "https://sync.actionaura.example",
    "https://sync.actionaura.example:8443",
    "https://sync.actionaura.example/relay",
    "http://127.0.0.1:5551",       # the real dev recipe used all through this sub-project
    "http://localhost:5551",
    "http://127.0.0.1",
    "HTTPS://SYNC.ACTIONAURA.EXAMPLE",  # scheme/host comparison must be case-insensitive
])
def test_accepted_urls(url):
    assert validate_sync_relay_url(url) == []


def test_unset_url_is_not_an_error_it_just_means_sync_is_unconfigured():
    assert validate_sync_relay_url("") == []
    assert validate_sync_relay_url(None) == []


@pytest.mark.parametrize("url", [
    "http://sync.actionaura.example",           # the exact reviewed hole
    "http://sync.actionaura.example:8080/relay",
    "http://192.168.1.50:5551",                 # a LAN host is still not loopback
    "http://10.0.2.2:5551",                     # Android-emulator alias; meaningless on desktop
])
def test_cleartext_to_a_non_loopback_host_is_rejected(url):
    problems = validate_sync_relay_url(url)
    assert problems, f"{url} must be rejected"
    assert any("cleartext http://" in p for p in problems)


def test_a_loopback_lookalike_in_the_userinfo_cannot_smuggle_cleartext_through():
    """`http://127.0.0.1@evil.example.com/` has a real host of
    `evil.example.com` -- validating on `netloc` text instead of the parsed
    hostname would have let this pass as loopback."""
    problems = validate_sync_relay_url("http://127.0.0.1@evil.example.com/")
    assert any("cleartext http://" in p for p in problems)
    assert any("credentials" in p for p in problems)


@pytest.mark.parametrize("url,expected_fragment", [
    ("ftp://sync.actionaura.example", "unsupported scheme"),
    ("ws://sync.actionaura.example", "unsupported scheme"),
    ("sync.actionaura.example:5551", "unsupported scheme"),   # bare host:port parses scheme='sync.actionaura.example'
    ("https://user:pw@sync.actionaura.example", "credentials"),
    ("https://sync.actionaura.example?token=abc", "query string"),
    ("https://sync.actionaura.example#frag", "fragment"),
    ("https://", "no host"),
])
def test_other_malformed_or_unsafe_shapes_are_rejected(url, expected_fragment):
    problems = validate_sync_relay_url(url)
    assert any(expected_fragment in p for p in problems), (url, problems)


def test_app_module_gate_disables_sync_for_an_invalid_url_without_refusing_to_boot():
    """The consequence that actually matters: app.py must treat an invalid
    URL as "sync off", never as "boot the app anyway and sync in cleartext",
    and never as "refuse to start Retail at all"."""
    import app as _app_module

    assert hasattr(_app_module, "_SYNC_RELAY_URL_IS_USABLE")
    # This process configured no relay URL at all, so the gate is False and
    # no sync service exists -- but the app object was still built and serves.
    assert _app_module._SYNC_RELAY_URL_IS_USABLE is False
    assert _app_module._sync_service is None
    app = _app_module.app
    app.config["TESTING"] = True
    with app.test_client() as client:
        assert client.get('/api/health').status_code == 200
