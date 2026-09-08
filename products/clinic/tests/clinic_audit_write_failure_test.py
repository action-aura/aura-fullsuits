"""A failed clinic audit write must be DISCOVERABLE without becoming FATAL.

The Clinic mirror of products/retail/tests/retail_audit_write_failure_test.py
-- read that file's docstring for the full reasoning; the fix it pins was
made in Retail and never carried across, and `_audit()` here is the ONLY
audit path in the product, called from every patient, visit, note,
prescription, invoice, payment and lab-expense mutation.

It was one line: `except Exception: pass`. No logger, no counter, no trace.
Clinic handles medical records and cites
docs/privacy/clinic-sensitive-data-boundary.md three times in the same file,
so a silently-stopped trail costs more here than in a shop.

The swallow itself is right and stays: refusing to record a visit because
the audit table is full or locked is the wrong answer in a clinic.

BOTH DIRECTIONS ARE TESTED. A test that only proves "a failure gets logged"
is satisfied by an implementation that logs unconditionally, or by one that
raises. So this file also proves a SUCCESSFUL write stays silent and
actually lands a row -- the half that quietly destroys the record if it
breaks, and the half a failure-only test cannot see.

Self-booting, matching the convention of the other files here. Run on its
own:

    python -m pytest products/clinic/tests/clinic_audit_write_failure_test.py -q
"""
import logging
import os
import shutil
import sqlite3
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

DATA = Path(tempfile.mkdtemp(prefix="aura_clinic_audit_fail_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_CLINIC", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from api import clinic_api  # noqa: E402

CLINIC_LOGGER = 'aura.clinic'


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


#: A value that must never reach the log. In Clinic `details` can carry
#: clinical content -- a lab test name alone can reveal a condition -- and
#: the log has a wider audience and a longer life than the audit table the
#: value was bound for.
SECRET_DETAILS = 'patient=Fatima test=HIV-1/2 antibody result=reactive'


@pytest.fixture
def identified(monkeypatch):
    """`_audit` reads the caller's company/user from the request context;
    pinned here so these tests exercise the write path itself rather than
    Flask plumbing."""
    monkeypatch.setattr(clinic_api, '_cid', lambda: 'company-1')
    monkeypatch.setattr(clinic_api, '_uid', lambda: 'user-1')


class _RefusingConnection:
    """A connection whose INSERT always fails, the way a full, locked or
    schema-drifted audit table does."""

    def __init__(self):
        self.attempts = 0

    def execute(self, *args, **kwargs):
        self.attempts += 1
        raise sqlite3.OperationalError('no such table: clinic_audit_log')


def _audit_records(caplog):
    return [r for r in caplog.records if 'audit write FAILED' in r.getMessage()]


# ── The failure half ─────────────────────────────────────────────────────────

def test_a_failed_audit_write_is_logged_and_does_not_raise(identified, caplog):
    conn = _RefusingConnection()
    with caplog.at_level(logging.ERROR, logger=CLINIC_LOGGER):
        # Reaching the next line at all IS the swallow assertion: if _audit
        # propagated, this test errors rather than fails, and the clinic it
        # stands in for would have refused to record a visit in order to
        # record that it recorded one.
        clinic_api._audit(conn, 'PatientCreated', 'patient', 42, SECRET_DETAILS)

    assert conn.attempts == 1, 'the write must actually have been attempted'
    records = _audit_records(caplog)
    assert records, (
        'a swallowed audit failure must leave a trace. Without one, a clinic '
        'whose audit writes have started failing keeps believing it has a '
        'trail over medical records, and finds out when somebody goes '
        'looking for an entry that was never written.'
    )


def test_the_log_line_carries_a_traceback_and_names_the_row(identified, caplog):
    conn = _RefusingConnection()
    with caplog.at_level(logging.ERROR, logger=CLINIC_LOGGER):
        clinic_api._audit(conn, 'PatientCreated', 'patient', 42, SECRET_DETAILS)

    record = _audit_records(caplog)[0]
    assert record.levelno >= logging.ERROR, (
        'a lost audit row is not a debug detail -- it must be findable by an '
        'operator grepping for errors')
    # log.exception, not log.warning: the traceback is what separates a
    # locked database from schema drift, and those need different answers.
    assert record.exc_info is not None, (
        'the exception must be attached -- "an audit write failed" without a '
        'traceback cannot tell a locked database from a drifted schema')
    message = record.getMessage()
    for needle in ('PatientCreated', 'patient', '42'):
        assert needle in message, (
            'the log line must identify the row that was lost; missing: ' + needle)


def test_the_details_payload_is_never_written_to_the_log(identified, caplog):
    conn = _RefusingConnection()
    with caplog.at_level(logging.ERROR, logger=CLINIC_LOGGER):
        clinic_api._audit(conn, 'PrescriptionCreated', 'prescription', 7, SECRET_DETAILS)

    blob = '\n'.join(r.getMessage() for r in caplog.records)
    assert SECRET_DETAILS not in blob, (
        '`details` must not be logged. In a clinic it carries clinical '
        'content, and the log has a wider audience and a longer life than '
        'the audit table it was bound for. Action/entity/id are enough to '
        'find the missing row.'
    )
    assert 'reactive' not in blob


# ── The success half: the one a failure-only test cannot see ─────────────────

def test_a_successful_audit_write_lands_a_row_and_logs_nothing(identified, caplog):
    conn = sqlite3.connect(':memory:')
    conn.execute(
        'CREATE TABLE clinic_audit_log (company_id TEXT, user_id TEXT, action TEXT, '
        'entity TEXT, entity_id TEXT, details TEXT)')

    with caplog.at_level(logging.DEBUG, logger=CLINIC_LOGGER):
        clinic_api._audit(conn, 'PatientCreated', 'patient', 42, SECRET_DETAILS)

    row = conn.execute(
        'SELECT company_id, user_id, action, entity, entity_id, details '
        'FROM clinic_audit_log').fetchone()
    assert row is not None, 'the ordinary path must still write the row'
    assert row[0] == 'company-1'
    assert row[1] == 'user-1'
    assert row[2] == 'PatientCreated'
    assert row[5] == SECRET_DETAILS, (
        'details belongs IN the audit table -- it is only the LOG that must '
        'not carry it')

    assert not _audit_records(caplog), (
        'a write that succeeded must stay silent. Without this, an '
        'implementation that logs unconditionally passes the failure tests '
        'above while telling the operator nothing useful.'
    )
    conn.close()


# ── End to end: the route keeps working while the trail is broken ────────────

def test_a_patient_is_still_created_when_the_audit_table_is_gone(caplog):
    """The real artefact, not just the helper: drop clinic_audit_log, create
    a patient through the HTTP route, and assert the clinic still works AND
    the loss was reported at ERROR."""
    import uuid

    from commercial_runtime.identity.registry_db import get_conn as registry_conn
    from commercial_runtime.security.passwords import hash_password
    from database.schema import get_clinic_conn

    email = f'audit-{uuid.uuid4().hex[:10]}@test.local'
    company_id = str(uuid.uuid4())
    reg = registry_conn()
    reg.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, "
        "require_password_change) VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'ADMIN-0001', email, hash_password('AuditPW1'),
         'admin', 'active'))
    reg.commit()
    reg.close()

    client = app.test_client()
    assert client.post('/api/auth/login', json={'email': email, 'password': 'AuditPW1'}).status_code == 200

    conn = get_clinic_conn()
    conn.execute('DROP TABLE clinic_audit_log')
    conn.commit()
    conn.close()
    try:
        with caplog.at_level(logging.ERROR, logger=CLINIC_LOGGER):
            r = client.post('/api/sub/clinic/patients', json={'name': 'Audit Gap Patient'})
        assert r.status_code == 200, r.get_json()
        assert _audit_records(caplog), (
            'the clinic must keep working, and the operator must be able to '
            'find out that it stopped keeping a trail')
    finally:
        # Restore for any later test in this process -- schema.py owns the
        # real definition, so re-run the initializer rather than re-spelling
        # the DDL here where it could drift.
        from database.schema import init_clinic
        init_clinic()
