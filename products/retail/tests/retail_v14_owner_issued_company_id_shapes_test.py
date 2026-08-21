"""
Aura Retail -- schema v14: `owner_issued_company_id()` must keep the promise
its own docstring makes. See database/schema.py::owner_issued_company_id.

That docstring says, of the licensing state it reads:

    "This function is a read-only consumer of somebody else's file, never
     its authority, so every failure (missing file, unreadable file, no
     assertion yet, malformed JSON) is reported the same honest way: None."

It was not true. The guard stopped one line short:

    try:
        payload = _json.loads(row[0]).get('payload') or {}
    except (ValueError, AttributeError):
        return None
    value = payload.get('license_public_id')      # <-- outside the try

An envelope whose `payload` is anything other than an object -- a list, a
string, a number, a bool -- survives `_json.loads(...).get('payload')`
perfectly happily (it is a legal JSON document), is truthy, and then blows up
on `.get` one line later with an uncaught AttributeError. A non-TEXT
`assertion_envelope_json` (an INTEGER or REAL written by some other writer)
raises TypeError out of `_json.loads` itself, which the except tuple never
listed at all.

Why either one is a launch blocker rather than a curiosity: this function is
called by `_migrate_rebind_company_id_to_owner_issued`, which runs inside
`_migrate_retail_schema`, which runs inside `init_retail()`, which
`app.py::init_app()` calls unconditionally at import. An exception here is
not "the tenant key could not be resolved" -- it is the app never starting
again, with `user_version` frozen and every future migration blocked behind
it. Exactly the same permanent wedge as the v13 duplicate-uid defect, from a
different direction.

This file is deliberately NOT written against a hand-built happy-path
envelope only. The positive control is here so the suite cannot pass by
having the function return None unconditionally -- which is the one "fix"
that would satisfy every negative case in this file and destroy the feature.

Run:
    pytest products/retail/tests/retail_v14_owner_issued_company_id_shapes_test.py -v
"""
import json
import os
import shutil
import sqlite3
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

OWNER_COMPANY_ID = '7c9e6679-7425-40de-944b-e07fc1f90ae7'

_TMP_DIRS = []


def teardown_module(module):
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)
    _TMP_DIRS.clear()


def _licence_dir(prefix='aura-retail-v14-shape-'):
    tmp = tempfile.mkdtemp(prefix=prefix)
    _TMP_DIRS.append(tmp)
    return tmp


def _write_envelope(app_data, raw_value, envelope_decl='TEXT'):
    """Put `raw_value` into `licensing_state.assertion_envelope_json` exactly
    as given -- no json.dumps, no str() -- so a test can store a genuine
    INTEGER, REAL or BLOB there and not merely a string that looks like one.

    `envelope_decl` exists because SQLite's column affinity is doing quiet
    work here that would otherwise hide half of this file's point. On the
    shipped `TEXT`-declared column, storing the integer 12345 silently
    converts it to the string '12345', so `json.loads` still gets a str. Give
    the same column no declared type (BLOB affinity -- what a hand-repaired
    or differently-versioned licensing.db can genuinely look like) and the
    integer is stored AS an integer, `json.loads` gets an int, and it raises
    TypeError, which the original `except (ValueError, AttributeError)` never
    listed.

    Written with plain sqlite3 rather than through LicenseStateRepository,
    mirroring how the reader itself is written: the reader must stay
    importable on Android, where importing licensing_contracts pulls in
    `cryptography` at module scope. A test that reached for the repository
    would exercise a different code path from the one that ships -- and,
    more to the point here, the repository would refuse to store most of
    these shapes, which is precisely why the reader must not assume it is
    the only writer that ever touched this file.
    """
    db_dir = os.path.join(app_data, 'database', 'subsystems')
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(db_dir, 'licensing.db'))
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS licensing_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            licensing_schema_version INTEGER NOT NULL,
            product_code TEXT NOT NULL,
            platform TEXT NOT NULL,
            current_state TEXT NOT NULL,
            owner_installation_id TEXT,
            assertion_envelope_json {envelope_decl},
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute('DELETE FROM licensing_state')
    conn.execute(
        'INSERT INTO licensing_state (id, licensing_schema_version, product_code, platform, '
        'current_state, owner_installation_id, assertion_envelope_json, updated_at) '
        'VALUES (1,1,?,?,?,?,?,?)',
        ('AURA_RETAIL', 'WINDOWS', 'ACTIVE_ONLINE', str(uuid.uuid4()),
         raw_value, '2026-08-21T00:00:00'),
    )
    conn.commit()
    conn.close()


# The shapes that reached `payload.get(...)` / `_json.loads(...)` and raised.
# SQLite columns are dynamically typed -- a TEXT affinity column happily
# stores an INTEGER, a REAL or a BLOB, so "the column is declared TEXT" is
# not a guarantee the reader may lean on.
MALFORMED_ENVELOPES = [
    pytest.param(json.dumps({'payload': ['license_public_id', OWNER_COMPANY_ID]}),
                 id='payload-is-a-list'),
    pytest.param(json.dumps({'payload': OWNER_COMPANY_ID}), id='payload-is-a-string'),
    pytest.param(json.dumps({'payload': 7}), id='payload-is-an-int'),
    pytest.param(json.dumps({'payload': 1.5}), id='payload-is-a-float'),
    pytest.param(json.dumps({'payload': True}), id='payload-is-a-bool'),
    pytest.param(json.dumps({'payload': {'license_public_id': ['a', 'b']}}),
                 id='license_public_id-is-a-list'),
    pytest.param(12345, id='column-holds-an-integer'),
    pytest.param(1.5, id='column-holds-a-real'),
    pytest.param(b'\x00\x01\x02 not json', id='column-holds-a-blob'),
    pytest.param(b'{"payload": ["a"]}', id='column-holds-a-blob-with-a-list-payload'),
    pytest.param('[1, 2, 3]', id='envelope-is-a-json-array'),
    pytest.param('null', id='envelope-is-json-null'),
    pytest.param('"just a string"', id='envelope-is-a-json-string'),
    pytest.param('{not json at all', id='envelope-is-not-json'),
    pytest.param('{}', id='envelope-has-no-payload-key'),
    pytest.param(json.dumps({'payload': {}}), id='payload-is-empty'),
]


@pytest.mark.parametrize('raw_value', MALFORMED_ENVELOPES)
def test_owner_issued_company_id_returns_none_and_never_raises_on_a_malformed_envelope(raw_value):
    """None, not an exception -- for every shape, including the ones the
    original two-name except tuple could not see.

    `pytest.raises` is deliberately NOT used here. The assertion is not "some
    specific exception is absent", it is "the function returned a value at
    all", so any exception -- AttributeError, TypeError, or whatever a future
    edit introduces -- fails this test by propagating out of the call.
    """
    tmp = _licence_dir()
    _write_envelope(tmp, raw_value)

    from database.schema import owner_issued_company_id

    assert owner_issued_company_id(tmp) is None, (
        f'a {type(raw_value).__name__} envelope produced a tenant key; the reader must '
        'not accept a shape it cannot verify'
    )


@pytest.mark.parametrize('raw_value', MALFORMED_ENVELOPES)
def test_a_malformed_envelope_does_not_stop_the_v14_migration_step(raw_value):
    """The consequence, not just the return value.

    `_migrate_rebind_company_id_to_owner_issued` -> `_migrate_retail_schema`
    -> `init_retail()` -> `app.py::init_app()`, all unconditional. This test
    exists because a version of the fix that made the FUNCTION safe while
    leaving some other path raising would still brick the install, and the
    assertion above could not tell the difference.
    """
    tmp = _licence_dir('aura-retail-v14-shape-mig-')
    os.environ['AURA_APP_DATA'] = tmp

    import database.schema as sch
    sch.BASE_DIR = os.path.join(tmp, 'database')
    sch.SUBSYS_DIR = os.path.join(sch.BASE_DIR, 'subsystems')
    sch.init_retail()
    _write_envelope(tmp, raw_value)

    conn = sqlite3.connect(os.path.join(sch.SUBSYS_DIR, 'retail.db'))
    conn.row_factory = sqlite3.Row
    try:
        result = sch._migrate_rebind_company_id_to_owner_issued(conn)
        assert isinstance(result, dict), 'the migration step must always report a status dict'
        assert result['status'] == 'skipped', result
        assert result['rows'] == 0
    finally:
        conn.close()


# ── the TypeError half: a column with no TEXT affinity ──────────────────────
# Kept separate from the parametrized list above because it needs a different
# table shape, and because it is the case the shipped column's TEXT affinity
# silently hides: on the declared-TEXT column an INTEGER is converted to a
# string on the way in, so `json.loads` still gets a str and the bug never
# surfaces. Affinity is a property of one particular CREATE TABLE, not a
# guarantee this reader is entitled to lean on -- it does not own this file.

@pytest.mark.parametrize('raw_value', [
    pytest.param(12345, id='genuine-integer'),
    pytest.param(1.5, id='genuine-real'),
])
def test_owner_issued_company_id_survives_a_non_text_envelope_column(raw_value):
    tmp = _licence_dir('aura-retail-v14-shape-affinity-')
    _write_envelope(tmp, raw_value, envelope_decl='')

    db_path = os.path.join(tmp, 'database', 'subsystems', 'licensing.db')
    probe = sqlite3.connect(db_path)
    try:
        stored = probe.execute(
            'SELECT typeof(assertion_envelope_json) FROM licensing_state WHERE id=1'
        ).fetchone()[0]
    finally:
        probe.close()
    assert stored in ('integer', 'real'), (
        f'fixture stored a {stored!r}, not a genuine non-text value -- affinity converted '
        'it and this test would prove nothing'
    )

    from database.schema import owner_issued_company_id

    assert owner_issued_company_id(tmp) is None


# ── the positive control ────────────────────────────────────────────────────

def test_a_well_formed_envelope_still_yields_the_license_public_id():
    """Without this, every assertion above is satisfied by
    `def owner_issued_company_id(...): return None` -- which passes the whole
    negative half of this file and silently removes the feature v14 exists
    for."""
    tmp = _licence_dir('aura-retail-v14-shape-good-')
    _write_envelope(tmp, json.dumps({
        'payload': {
            'assertion_id': str(uuid.uuid4()),
            'product_code': 'AURA_RETAIL',
            'license_public_id': OWNER_COMPANY_ID,
            'installation_public_id': str(uuid.uuid4()),
        },
    }))

    from database.schema import owner_issued_company_id

    assert owner_issued_company_id(tmp) == OWNER_COMPANY_ID


def test_installation_public_id_is_still_never_adopted_as_the_tenant_key():
    """The other half of the positive control, and the reason a
    "just take whatever id is in there" fix would be wrong.

    `installation_public_id` is Owner-issued too and sits in the same
    payload, but it identifies one DEVICE's installation -- adopting it would
    give every terminal in one shop a different tenant key, which is the
    fragmentation this rebind exists to end.
    """
    tmp = _licence_dir('aura-retail-v14-shape-inst-')
    installation_id = str(uuid.uuid4())
    _write_envelope(tmp, json.dumps({
        'payload': {
            'product_code': 'AURA_RETAIL',
            'installation_public_id': installation_id,
        },
    }))

    from database.schema import owner_issued_company_id

    assert owner_issued_company_id(tmp) is None, \
        'installation_public_id was adopted as the tenant key'
