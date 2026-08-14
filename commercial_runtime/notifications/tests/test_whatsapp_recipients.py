import sqlite3

import pytest

from commercial_runtime.notifications import whatsapp_recipients as wr
from commercial_runtime.notifications.schema import apply_whatsapp_recipients_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    apply_whatsapp_recipients_schema(c)
    return c


def _make(conn, company_id=1, **overrides):
    kwargs = dict(
        display_name='Owner', phone_e164='+15551234567',
        role_label='Owner', branch_id=None, report_types=['daily_sales_summary'],
    )
    kwargs.update(overrides)
    return wr.create_recipient(conn, company_id, **kwargs)


# ── CRUD round-trip ──────────────────────────────────────────────────────

def test_create_then_list_round_trips(conn):
    rid = _make(conn)
    rows = wr.list_recipients(conn, 1)
    assert len(rows) == 1
    assert rows[0]['id'] == rid
    assert rows[0]['display_name'] == 'Owner'
    assert rows[0]['phone_e164'] == '+15551234567'
    assert rows[0]['report_types'] == ['daily_sales_summary']
    assert rows[0]['status'] == 'active'


def test_update_recipient_partial_fields(conn):
    rid = _make(conn)
    wr.update_recipient(conn, 1, rid, display_name='Downtown Manager', branch_id=2)
    row = wr.list_recipients(conn, 1)[0]
    assert row['display_name'] == 'Downtown Manager'
    assert row['branch_id'] == 2
    # untouched fields survive the partial update
    assert row['phone_e164'] == '+15551234567'


def test_update_unknown_recipient_raises(conn):
    with pytest.raises(wr.RecipientNotFoundError):
        wr.update_recipient(conn, 1, 'not-a-real-id', display_name='X')


def test_delete_recipient_removes_row(conn):
    rid = _make(conn)
    wr.delete_recipient(conn, 1, rid)
    assert wr.list_recipients(conn, 1) == []


def test_delete_unknown_recipient_raises(conn):
    with pytest.raises(wr.RecipientNotFoundError):
        wr.delete_recipient(conn, 1, 'not-a-real-id')


# ── company scoping ──────────────────────────────────────────────────────

def test_recipients_scoped_per_company(conn):
    _make(conn, company_id=1)
    _make(conn, company_id=2)
    assert len(wr.list_recipients(conn, 1)) == 1
    assert len(wr.list_recipients(conn, 2)) == 1


def test_update_cannot_cross_company_boundary(conn):
    rid = _make(conn, company_id=1)
    with pytest.raises(wr.RecipientNotFoundError):
        wr.update_recipient(conn, 2, rid, display_name='Hijacked')


def test_delete_cannot_cross_company_boundary(conn):
    rid = _make(conn, company_id=1)
    with pytest.raises(wr.RecipientNotFoundError):
        wr.delete_recipient(conn, 2, rid)
    # still there for the real owner
    assert len(wr.list_recipients(conn, 1)) == 1


# ── validation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize('bad_phone', ['', '0791234567', '+0123456', 'not a phone', '+1', None])
def test_invalid_phone_rejected_on_create(conn, bad_phone):
    with pytest.raises(wr.InvalidRecipientError):
        _make(conn, phone_e164=bad_phone)


def test_valid_e164_phone_accepted(conn):
    _make(conn, phone_e164='+962791234567')
    assert wr.list_recipients(conn, 1)[0]['phone_e164'] == '+962791234567'


def test_blank_display_name_rejected(conn):
    with pytest.raises(wr.InvalidRecipientError):
        _make(conn, display_name='   ')


def test_unknown_report_type_rejected_on_create(conn):
    with pytest.raises(wr.InvalidRecipientError):
        _make(conn, report_types=['not_a_real_report'])


def test_unknown_report_type_rejected_on_update(conn):
    rid = _make(conn)
    with pytest.raises(wr.InvalidRecipientError):
        wr.update_recipient(conn, 1, rid, report_types=['not_a_real_report'])


def test_non_integer_branch_id_rejected(conn):
    with pytest.raises(wr.InvalidRecipientError):
        _make(conn, branch_id='not-a-number')


# ── recipients_for() routing matrix ─────────────────────────────────────

def test_all_branches_recipient_matches_any_event_branch(conn):
    _make(conn, branch_id=None, report_types=['low_stock_alert'])
    assert len(wr.recipients_for(conn, 1, 'low_stock_alert', branch_id=1)) == 1
    assert len(wr.recipients_for(conn, 1, 'low_stock_alert', branch_id=2)) == 1
    assert len(wr.recipients_for(conn, 1, 'low_stock_alert', branch_id=None)) == 1


def test_branch_scoped_recipient_matches_only_its_own_branch(conn):
    _make(conn, branch_id=1, report_types=['low_stock_alert'])
    matched_same = wr.recipients_for(conn, 1, 'low_stock_alert', branch_id=1)
    matched_other = wr.recipients_for(conn, 1, 'low_stock_alert', branch_id=2)
    assert len(matched_same) == 1
    assert len(matched_other) == 0


def test_company_wide_event_matches_every_subscribed_recipient_regardless_of_branch(conn):
    _make(conn, branch_id=1, report_types=['ar_overdue_alert'])
    _make(conn, branch_id=2, report_types=['ar_overdue_alert'])
    _make(conn, branch_id=None, report_types=['ar_overdue_alert'])
    matched = wr.recipients_for(conn, 1, 'ar_overdue_alert', branch_id=None)
    assert len(matched) == 3


def test_inactive_recipient_excluded(conn):
    rid = _make(conn, report_types=['daily_sales_summary'])
    wr.update_recipient(conn, 1, rid, status='inactive')
    assert wr.recipients_for(conn, 1, 'daily_sales_summary') == []


def test_unsubscribed_recipient_excluded(conn):
    _make(conn, report_types=['low_stock_alert'])
    assert wr.recipients_for(conn, 1, 'daily_sales_summary') == []


def test_recipients_for_scoped_per_company(conn):
    _make(conn, company_id=1, report_types=['daily_sales_summary'])
    _make(conn, company_id=2, report_types=['daily_sales_summary'])
    assert len(wr.recipients_for(conn, 1, 'daily_sales_summary')) == 1
    assert len(wr.recipients_for(conn, 2, 'daily_sales_summary')) == 1
