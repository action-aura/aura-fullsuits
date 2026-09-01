"""GET/POST /settings/business-day, against the real Flask app.

WHY THIS EXISTS, SEPARATELY FROM THE JS TEST

retail_business_day_settings_test.js proves the Admin Center card SENDS the
right payloads. It cannot prove the server does the right thing with them --
it answers a stub.

One of those payloads matters more than the rest. The card omits
`business_timezone` entirely while the field is disabled (an install with no
tzdata), because sending null would CLEAR a zone the owner was never shown
and did not choose to remove. That fix is only correct if the route really
does accept a partial payload and really does leave the stored zone alone.
That was read out of the route's source, not measured -- and "I read it and
it looks right" is exactly the evidence this codebase has been burned by.

So this file measures it, on a real database, through the real route.

Self-booting, matching the convention here (no shared conftest.py). Run on
its own:

    py -3.14 -m pytest products/retail/tests/retail_business_day_route_test.py -q
"""
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_business_day_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402

URL = '/api/sub/retail/settings/business-day'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


@pytest.fixture
def client():
    """A logged-in owner for a fresh company. `admin`, because the route is
    gated on CAP_EMPLOYEES -- ROLE_MANAGER deliberately excludes it."""
    email = f'busday-admin-{uuid.uuid4().hex[:8]}@test.local'
    password = 'BusinessDayPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, "
        "role, status, require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email,
         hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()

    c = app.test_client()
    r = c.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    # retail_settings is created lazily -- same priming call the other
    # self-booting suites here make.
    c.get('/api/sub/retail/settings/tax')
    c.test_company_id = company_id
    return c


def _get(client):
    r = client.get(URL)
    assert r.status_code == 200, r.get_json()
    return r.get_json()['data']


def _tzdata(client):
    return _get(client).get('timezone_database_available')


# ── The contract the UI fix depends on ───────────────────────────────────────

def test_a_partial_payload_leaves_the_stored_timezone_alone(client):
    """THE reason this file exists.

    The Admin Center card omits `business_timezone` when its field is
    disabled, so that changing only the hour cannot wipe a declared zone.
    That is only safe if the route treats an ABSENT key as "leave it" rather
    than as "clear it".
    """
    if not _tzdata(client):
        pytest.skip('no timezone database on this machine; nothing to preserve')

    assert client.post(URL, json={'business_timezone': 'Asia/Amman',
                                  'business_day_start_hour': 0}).status_code == 200
    assert _get(client)['business_timezone'] == 'Asia/Amman'

    # Only the hour. No timezone key at all.
    r = client.post(URL, json={'business_day_start_hour': 6})
    assert r.status_code == 200, r.get_json()

    after = _get(client)
    assert after['business_day_start_hour'] == 6, 'the hour must have changed'
    assert after['business_timezone'] == 'Asia/Amman', (
        'a payload that never mentions the timezone must not clear it. If this '
        'fails, the Admin Center card wipes a shop\'s declared zone whenever an '
        'owner changes the hour on an install missing tzdata -- silently, and '
        'discovered later as "the reports moved".'
    )


def test_an_explicit_null_does_clear_the_timezone(client):
    """The other half: omitting is not the only way to send nothing, and
    `null` must still mean CLEAR -- otherwise a shop could never undeclare."""
    if not _tzdata(client):
        pytest.skip('no timezone database on this machine')

    client.post(URL, json={'business_timezone': 'Asia/Amman'})
    assert _get(client)['business_timezone'] == 'Asia/Amman'

    r = client.post(URL, json={'business_timezone': None})
    assert r.status_code == 200, r.get_json()
    assert _get(client)['business_timezone'] is None, (
        'explicit null must clear the key -- this is what the empty box in the '
        'card sends, and it is deliberately NOT the same as sending "UTC"')


def test_utc_is_stored_as_a_real_zone_not_as_a_synonym_for_unset(client):
    """Pins the distinction the route's docstring calls load-bearing, so a
    future 'simplification' that maps unset onto UTC fails here."""
    if not _tzdata(client):
        pytest.skip('no timezone database on this machine')

    assert client.post(URL, json={'business_timezone': 'UTC'}).status_code == 200
    assert _get(client)['business_timezone'] == 'UTC', (
        'UTC is a REAL zone and must round-trip as one. If unset and UTC were '
        'the same value, a shop that stopped declaring a zone would have its '
        'whole trading history silently re-filed.')


# ── Input handling ───────────────────────────────────────────────────────────

def test_an_empty_payload_is_refused(client):
    r = client.post(URL, json={})
    assert r.status_code == 400, r.get_json()


def test_an_unrecognised_zone_is_refused_without_changing_anything(client):
    if not _tzdata(client):
        pytest.skip('no timezone database on this machine')

    client.post(URL, json={'business_timezone': 'Asia/Amman'})
    r = client.post(URL, json={'business_timezone': 'Middle Earth/Shire'})
    assert r.status_code == 400, r.get_json()
    assert _get(client)['business_timezone'] == 'Asia/Amman', (
        'a refused write must not have partially applied')


@pytest.mark.parametrize('hour', [0, 6, 23])
def test_every_legal_hour_round_trips(client, hour):
    r = client.post(URL, json={'business_day_start_hour': hour})
    assert r.status_code == 200, r.get_json()
    assert _get(client)['business_day_start_hour'] == hour


def test_the_hour_select_cannot_send_a_value_the_route_rejects(client):
    """The card offers 0..23 and nothing else. This pins that the range the
    UI offers is exactly the range the server honours, so the two cannot
    drift into a control that silently does nothing."""
    for hour in (0, 23):
        assert client.post(URL, json={'business_day_start_hour': hour}).status_code == 200
    # And the value just outside it is not quietly accepted as something else.
    client.post(URL, json={'business_day_start_hour': 6})
    client.post(URL, json={'business_day_start_hour': 24})
    assert _get(client)['business_day_start_hour'] != 24, (
        'an out-of-range hour must not be stored as-is; the card never sends '
        'one, and the server must not start honouring it if that changes')
