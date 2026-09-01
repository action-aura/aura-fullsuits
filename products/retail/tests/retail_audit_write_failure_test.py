"""A failed audit write must be DISCOVERABLE without becoming FATAL.

ROADMAP.md, 2026-09-01: `_audit()` and three `_sec_audit` call sites wrapped
their write in a bare `except Exception: pass`. Nothing was logged, counted
or surfaced.

The swallow itself is right and stays: a sale must not fail because the
audit table is full, locked or corrupt, and refusing to sell in order to
record that you sold is the wrong answer in a shop. What was wrong is that
the failure left no trace at all -- so a shop whose audit writes had started
failing would keep operating and keep believing it had a trail, and the gap
would be found by somebody looking for an entry that was never written.

BOTH DIRECTIONS ARE TESTED HERE, deliberately.

A test that only proves "a failure gets logged" is satisfied by an
implementation that logs unconditionally, or by one that raises. So this
file also proves that a SUCCESSFUL write stays silent and actually lands a
row -- the allow-half. That half is the one that quietly destroys the till
if it breaks, and it is the half a failure-only test cannot see.

Self-booting, matching the convention of the other files here (there is no
shared conftest.py for products/retail/tests/). Run on its own:

    py -3.14 -m pytest products/retail/tests/retail_audit_write_failure_test.py -q
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

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_audit_fail_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR),
                  AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from api import retail_api  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


#: A value that must never reach the log. `details` carries real business
#: content -- prices, customer identifiers, variance amounts -- and the log
#: has a wider audience and a longer life than the audit table the value was
#: bound for.
SECRET_DETAILS = 'customer=Fatima phone=0790000000 variance=-42.750'


@pytest.fixture
def identified(monkeypatch):
    """`_audit` reads the caller's company/user from the request context;
    pinned here so these tests exercise the write path itself rather than
    Flask plumbing."""
    monkeypatch.setattr(retail_api, '_cid', lambda: 'company-1')
    monkeypatch.setattr(retail_api, '_uid', lambda: 'user-1')


class _RefusingConnection:
    """A connection whose INSERT always fails, the way a full, locked or
    schema-drifted audit table does."""

    def __init__(self):
        self.attempts = 0

    def execute(self, *args, **kwargs):
        self.attempts += 1
        raise sqlite3.OperationalError('no such table: audit_log')


def _audit_records(caplog):
    return [r for r in caplog.records if 'audit write FAILED' in r.getMessage()]


# ── The failure half ─────────────────────────────────────────────────────────

def test_a_failed_audit_write_is_logged_and_does_not_raise(identified, caplog):
    conn = _RefusingConnection()
    with caplog.at_level(logging.ERROR, logger=retail_api.log.name):
        # Reaching the next line at all IS the swallow assertion: if _audit
        # propagated, this test errors rather than fails, and the till it
        # stands in for would have refused a sale to record one.
        retail_api._audit(conn, 'PRODUCT_UPDATED', 'products', 42, SECRET_DETAILS)

    assert conn.attempts == 1, 'the write must actually have been attempted'
    records = _audit_records(caplog)
    assert records, (
        'a swallowed audit failure must leave a trace. Without one, a shop '
        'whose audit writes have started failing keeps believing it has a '
        'trail, and finds out when somebody goes looking for an entry that '
        'was never written -- the most expensive possible moment.'
    )


def test_the_log_line_carries_a_traceback_and_names_the_row(identified, caplog):
    conn = _RefusingConnection()
    with caplog.at_level(logging.ERROR, logger=retail_api.log.name):
        retail_api._audit(conn, 'PRODUCT_UPDATED', 'products', 42, SECRET_DETAILS)

    record = _audit_records(caplog)[0]
    # log.exception, not log.warning: the traceback is what separates a
    # locked database from schema drift, and those need different answers.
    assert record.exc_info is not None, (
        'the exception must be attached -- "an audit write failed" without a '
        'traceback cannot tell a locked database from a drifted schema'
    )
    message = record.getMessage()
    for needle in ('PRODUCT_UPDATED', 'products', '42'):
        assert needle in message, (
            'the log line must identify the row that was lost; missing: ' + needle)


def test_the_details_payload_is_never_written_to_the_log(identified, caplog):
    conn = _RefusingConnection()
    with caplog.at_level(logging.ERROR, logger=retail_api.log.name):
        retail_api._audit(conn, 'SALE_REFUNDED', 'sales', 7, SECRET_DETAILS)

    blob = '\n'.join(r.getMessage() for r in caplog.records)
    assert SECRET_DETAILS not in blob, (
        '`details` must not be logged. It carries business content (prices, '
        'customer identifiers, variance amounts) and the log has a wider '
        'audience and a longer life than the audit table it was bound for. '
        'Action/entity/id are enough to find the missing row.'
    )
    assert '0790000000' not in blob


# ── The success half: the one a failure-only test cannot see ─────────────────

def test_a_successful_audit_write_lands_a_row_and_logs_nothing(identified, caplog):
    conn = sqlite3.connect(':memory:')
    conn.execute(
        'CREATE TABLE audit_log (company_id TEXT, user_id TEXT, action TEXT, '
        'entity TEXT, entity_id TEXT, details TEXT)')

    with caplog.at_level(logging.DEBUG, logger=retail_api.log.name):
        retail_api._audit(conn, 'PRODUCT_UPDATED', 'products', 42, SECRET_DETAILS)

    row = conn.execute(
        'SELECT company_id, user_id, action, entity, entity_id, details '
        'FROM audit_log').fetchone()
    assert row is not None, 'the ordinary path must still write the row'
    assert row[0] == 'company-1'
    assert row[1] == 'user-1'
    assert row[2] == 'PRODUCT_UPDATED'
    assert row[5] == SECRET_DETAILS, (
        'details belongs IN the audit table -- it is only the LOG that must '
        'not carry it')

    assert not _audit_records(caplog), (
        'a write that succeeded must stay silent. Without this, an '
        'implementation that logs unconditionally -- or one that logs and '
        'then swallows a real failure it never had -- passes the failure '
        'tests above while telling the operator nothing useful.'
    )
    conn.close()
