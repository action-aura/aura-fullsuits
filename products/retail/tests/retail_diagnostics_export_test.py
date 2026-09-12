"""
Aura Retail -- diagnostics export (retail-hardware-viewports).

THE GAP this closes: app.py configured NO file logging at all -- no
`basicConfig`, no `FileHandler`, no `RotatingFileHandler` anywhere -- and a
shop reporting "it stopped working yesterday" had nothing to send and
nothing to read. See app.py's `_configure_file_logging()` for the log file
itself, and retail_api.py's `diagnostics_export` route (the other half) for
the bundle this file proves:

    GET /api/sub/retail/diagnostics/export

THE PART THAT MATTERS MOST is redaction (tests 3/4/5 below). This bundle is
designed to be emailed to a vendor by a shopkeeper, so it must never carry
customer PII or secrets -- the log tail is the dangerous part, because it is
free text and anything could have been logged into it. See
`_redact_diagnostics_log_line`'s own docstring in retail_api.py for why this
is a best-effort net, not a guarantee.

Self-contained bootstrap, matching retail_printer_kick_test.py /
retail_route_capability_matrix_test.py (no shared conftest.py exists here).
One file per process (AUDIT-010): these boot an app at import.

Run:
    pytest products/retail/tests/retail_diagnostics_export_test.py -v
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_diagnostics_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity import user_accounts  # noqa: E402
from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402
from database.schema import RETAIL_SCHEMA_VERSION  # noqa: E402
from config import APP_VERSION  # noqa: E402

# Must match app.py's `_configure_file_logging()` / retail_api.py's own
# `_DIAGNOSTICS_LOG_PATH` -- all three derive the same app-data-relative
# 'logs/backend.log' location.
LOG_PATH = DATA / 'logs' / 'backend.log'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


# ── Fixtures / helpers (deliberately duplicated per-file convention -- see
#    retail_printer_kick_test.py's own docstring for why nothing here is
#    shared via a conftest.py) ────────────────────────────────────────────

def _make_user(role, *, company_id=None, capabilities=None):
    """A real account with the legacy subsystem grant plus real capability
    rows, seeded through the SAME production helper account creation uses --
    matches retail_printer_kick_test.py's `_make_user`.

    `capabilities`, when given, overrides individual seeded rows AFTER the
    role default (e.g. `{user_accounts.CAP_EMPLOYEES: 'full'}`) -- this is
    how test 2 proves the ALLOW half without inventing a second admin
    account: `seed_capabilities_for_user` writes a row for every one of the
    eight codes for every role (granting the ones the role implies, denying
    the rest), so a cashier already holds a (denied) CAP_EMPLOYEES row this
    UPDATE can flip.
    """
    email = f"diag-{role}-{uuid.uuid4().hex[:10]}@test.local"
    password = "DiagnosticsPW1"  # pragma: allowlist secret -- throwaway test fixture, not a real credential
    company_id = company_id or str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, f"EMP-{uuid.uuid4().hex[:6]}", email, hash_password(password), role, "active"),
    )
    # mt_require_subsystem still demands the legacy un-namespaced grant, so
    # without this the request never reaches the capability check at all.
    conn.execute(
        "INSERT INTO user_permissions (id, user_id, subsystem, access_level) VALUES (?,?,?,?)",
        (str(uuid.uuid4()), user_id, "retail", "full"),
    )
    user_accounts.seed_capabilities_for_user(conn, user_id, role)
    if capabilities:
        for code, level in capabilities.items():
            conn.execute(
                "UPDATE user_permissions SET access_level=? WHERE user_id=? AND subsystem=?",
                (level, user_id, code),
            )
    conn.commit()
    conn.close()

    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client, company_id


@pytest.fixture(scope='module')
def admin():
    return _make_user('admin')


def _append_log_lines(*lines):
    """Writes straight to the SAME file `diagnostics_export` reads its tail
    from -- a real file, not a mock -- so the redaction pass under test runs
    on real bytes off disk exactly like it does in production."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, 'a', encoding='utf-8') as fh:
        for line in lines:
            fh.write(line.rstrip('\n') + '\n')


# ── 1. The route returns 200 and the bundle carries the expected shape ──────

def test_bundle_contains_schema_version_and_row_counts(admin):
    client, _company_id = admin
    r = client.get('/api/sub/retail/diagnostics/export')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['retail_schema_version'] == RETAIL_SCHEMA_VERSION, d
    assert d['app_version'] == APP_VERSION, d
    assert set(d['row_counts']) == {'sales', 'products', 'customers', 'users'}, d
    for key in ('sales', 'products', 'customers', 'users'):
        assert isinstance(d['row_counts'][key], int), d
    assert d['platform'], d
    assert d['python_version'], d
    assert 'sync_health' in d, d


# ── 2. Capability gate, both directions ──────────────────────────────────────
# The deny half is the obvious one; the allow half is what proves the gate is
# not simply refusing everyone (a decorator that always returns 403 would
# pass a deny-only test just as happily) -- same discipline
# retail_printer_kick_test.py's identical test documents.

def test_capability_gate_both_directions(admin):
    _admin_client, company_id = admin

    denied, _ = _make_user('cashier', company_id=company_id)
    r_denied = denied.get('/api/sub/retail/diagnostics/export')
    assert r_denied.status_code == 403, r_denied.get_json()

    allowed, _ = _make_user(
        'cashier', company_id=company_id,
        capabilities={user_accounts.CAP_EMPLOYEES: 'full'},
    )
    r_allowed = allowed.get('/api/sub/retail/diagnostics/export')
    assert r_allowed.status_code == 200, r_allowed.get_json()


# ── 3. THE redaction test -- made real, not vacuous ──────────────────────────
# Seeds a real customer with a distinctive name/phone/email, writes those
# EXACT values (plus a licence-key-shaped string) straight into the log file
# the route reads, then asserts on the FULL serialized bundle text -- a leak
# in a field nobody thought to check by name is exactly the failure mode a
# field-by-field assertion would miss.
#
# MUTATION-PROVEN (see the task's own verification step): disabling
# `_redact_diagnostics_log_line`'s substitution calls makes this go RED,
# naming the leaked literal value in the assertion failure; restoring it
# makes it GREEN again -- both directions quoted in the final report.

CANARY_NAME = "Zylstra Quibblewood"
CANARY_PHONE = "0791234567"
CANARY_EMAIL = "zylstra.quibblewood.canary@example.com"
CANARY_LICENSE_KEY = "AURA-RET-1-23AB-CDEF-GH2J-KM3N-PQRS"


def test_redaction_removes_pii_and_license_key_from_bundle(admin):
    client, _company_id = admin
    r = client.post('/api/sub/retail/customers', json={
        'name': CANARY_NAME, 'phone': CANARY_PHONE, 'email': CANARY_EMAIL,
    })
    assert r.status_code == 200, r.get_json()

    _append_log_lines(
        f"2026-09-11 12:00:00 INFO  retail.customers: contact updated for "
        f"{CANARY_NAME} <{CANARY_EMAIL}> phone {CANARY_PHONE}",
        f"2026-09-11 12:00:01 INFO  retail.licensing: activation attempted with {CANARY_LICENSE_KEY}",
    )

    r = client.get('/api/sub/retail/diagnostics/export')
    assert r.status_code == 200, r.get_json()
    bundle_text = json.dumps(r.get_json())

    for leaked in (CANARY_NAME, CANARY_PHONE, CANARY_EMAIL, CANARY_LICENSE_KEY):
        assert leaked not in bundle_text, (
            f"{leaked!r} leaked into the diagnostics bundle:\n{bundle_text}"
        )
    assert '[REDACTED:email]' in bundle_text, bundle_text
    assert '[REDACTED:phone]' in bundle_text, bundle_text
    assert '[REDACTED:license_key]' in bundle_text, bundle_text
    assert '[REDACTED:customer]' in bundle_text, bundle_text


# ── 4. ANTI-VACUITY: the log tail must be non-empty in the first place ──────
# Without this, test 3 above passes trivially the moment the log tail is
# empty for ANY reason (wrong path, log rotated away, a swallowed read
# failure) -- the single most likely way this whole guard silently stops
# working.
#
# MUTATION-PROVEN: forcing `_diagnostics_log_tail()` to return `[]`
# unconditionally makes this go RED; restoring it makes it GREEN again --
# both directions quoted in the final report.

CANARY_BENIGN_LINE = "DIAGNOSTICS_TEST_CANARY_LINE_should_survive_redaction_untouched"  # pragma: allowlist secret -- not a secret, just a long uppercase token that trips the entropy heuristic


def test_log_tail_is_non_empty_and_contains_a_known_line(admin):
    client, _company_id = admin
    _append_log_lines(CANARY_BENIGN_LINE)

    r = client.get('/api/sub/retail/diagnostics/export')
    assert r.status_code == 200, r.get_json()
    tail = r.get_json()['data']['log_tail']
    assert tail, "log_tail is empty -- the anti-vacuity floor test 3 depends on has failed"
    assert any(CANARY_BENIGN_LINE in line for line in tail), tail


# ── 5. The bundle contains NO licence key ────────────────────────────────────
# `seed_active_license` (module bootstrap above) never stores a real key at
# all -- only the state -- so this pins the STRUCTURE of the licence field
# itself: exactly {'state': ...}, never `present_status()`'s full dict
# (installation_id/entitlements/sync_relay_base_url), and never any key
# string, generated or otherwise.

def test_bundle_contains_no_licence_key(admin):
    client, _company_id = admin
    r = client.get('/api/sub/retail/diagnostics/export')
    assert r.status_code == 200, r.get_json()
    d = r.get_json()['data']
    assert d['licence'] == {'state': 'ACTIVE_ONLINE'}, d
    bundle_text = json.dumps(r.get_json())
    assert CANARY_LICENSE_KEY not in bundle_text, bundle_text
    assert 'test-fixture-installation' not in bundle_text, (
        "seed_active_license's own owner_installation_id leaked -- the "
        "licence field must be STATE ONLY"
    )
