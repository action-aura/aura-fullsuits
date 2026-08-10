import json
import os
import sqlite3

import pytest

from commercial_runtime.einvoicing import audit
from commercial_runtime.einvoicing.schema import apply_einvoicing_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    apply_einvoicing_schema(c)
    return c


def test_record_inserts_one_row_per_attempt(conn, tmp_path):
    audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_STARTED', invoice_ref='ref-1', attempt_no=1)
    audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_CLEARED', invoice_ref='ref-1', attempt_no=1)
    rows = audit.recent(conn, company_id=1)
    assert len(rows) == 2


def test_unknown_event_type_rejected(conn, tmp_path):
    with pytest.raises(audit.EInvoiceAuditError):
        audit.record(conn, str(tmp_path), company_id=1, event='MADE_UP_EVENT')


def test_forbidden_marker_in_details_rejected(conn, tmp_path):
    with pytest.raises(audit.EInvoiceAuditError):
        audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_STARTED',
                      details={'note': 'client_secret leaked here'})


def test_forbidden_marker_case_insensitive(conn, tmp_path):
    with pytest.raises(audit.EInvoiceAuditError):
        audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_STARTED',
                      details={'note': 'CLIENT_SECRET=xyz'})


def test_clean_details_are_persisted(conn, tmp_path):
    audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_STARTED', invoice_ref='ref-1',
                 details={'attempt': 1, 'note': 'ordinary retry'})
    rows = audit.recent(conn, company_id=1)
    assert json.loads(rows[0]['details_json']) == {'attempt': 1, 'note': 'ordinary retry'}


def test_recent_filters_by_invoice_ref(conn, tmp_path):
    audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_STARTED', invoice_ref='ref-1')
    audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_STARTED', invoice_ref='ref-2')
    rows = audit.recent(conn, company_id=1, invoice_ref='ref-1')
    assert len(rows) == 1
    assert rows[0]['invoice_ref'] == 'ref-1'


def test_recent_scoped_per_company(conn, tmp_path):
    audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_STARTED')
    audit.record(conn, str(tmp_path), company_id=2, event='SUBMIT_STARTED')
    assert len(audit.recent(conn, company_id=1)) == 1
    assert len(audit.recent(conn, company_id=2)) == 1


def test_jsonl_mirror_written_with_expected_shape(conn, tmp_path):
    audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_CLEARED', invoice_ref='ref-1',
                 outcome='CLEARED', reason_code='MOCK_CLEARED', provider='mock', http_status=200,
                 details={'attempt': 1})
    log_path = os.path.join(str(tmp_path), 'einvoicing', 'logs', 'audit.jsonl')
    assert os.path.exists(log_path)
    with open(log_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry['event'] == 'SUBMIT_CLEARED'
    assert entry['invoice_ref'] == 'ref-1'
    assert entry['outcome'] == 'CLEARED'
    assert entry['provider'] == 'mock'
    assert entry['data'] == {'attempt': 1}
    assert 'timestamp' in entry


def test_jsonl_mirror_appends_across_calls(conn, tmp_path):
    audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_STARTED')
    audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_CLEARED')
    log_path = os.path.join(str(tmp_path), 'einvoicing', 'logs', 'audit.jsonl')
    with open(log_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    assert len(lines) == 2


def test_no_secret_ever_reaches_the_jsonl_file_even_when_rejected(conn, tmp_path):
    with pytest.raises(audit.EInvoiceAuditError):
        audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_STARTED',
                      details={'client_secret': 'super-secret-value'})
    log_path = os.path.join(str(tmp_path), 'einvoicing', 'logs', 'audit.jsonl')
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'super-secret-value' not in content


def test_prune_older_than_removes_stale_rows(conn, tmp_path):
    conn.execute(
        "INSERT INTO einvoice_audit (company_id, event, occurred_at) VALUES (1, 'SUBMIT_STARTED', '2000-01-01T00:00:00+00:00')"
    )
    audit.record(conn, str(tmp_path), company_id=1, event='SUBMIT_STARTED')
    removed = audit.prune_older_than(conn, days=400)
    assert removed == 1
    remaining = audit.recent(conn, company_id=1)
    assert len(remaining) == 1
