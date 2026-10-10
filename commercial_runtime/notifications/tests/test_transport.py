"""Real-email wave: the transport chooser, the relay payload, and the worker's
treatment of account mail versus shop alerts.

Why the assertions look the way they do (ENGINEERING 1): a row ending SENT
cannot tell WHICH sender sent it, so every test counts calls on the specific
sender (the fake SMTP send, the fake Owner client) and asserts the other one
was NOT called. The "no password ever leaves the till" test inspects the
serialised wire body of the REAL LicensingClient, not a hand-built dict.
"""
import json
import sqlite3

import pytest

from commercial_runtime.licensing_contracts.client import LicensingClient, LicensingClientConfig
from commercial_runtime.notifications import settings, smtp_client, transport
from commercial_runtime.notifications.identity_sink import IdentityMailSink
from commercial_runtime.notifications.outbox import EmailOutboxRepository
from commercial_runtime.notifications.schema import apply_notifications_schema
from commercial_runtime.notifications.worker import EmailOutboxWorker

SECRET = 'smtp-password-that-must-never-travel-9f3a'
VERIFY_BODY = 'Confirm:\n\nhttp://127.0.0.1:5001/#verify-email/' + 'a' * 32 + '\n\nThis link expires.'
LOW_BODY = 'Widget has dropped to 3 units on hand (reorder level 5).\n\nReview this.'


def _conn_factory(db_path):
    def _make():
        c = sqlite3.connect(db_path, timeout=30)
        c.row_factory = sqlite3.Row
        return c
    return _make


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.delenv('AURA_SMTP_HOST', raising=False)
    monkeypatch.setenv('AURA_SMTP_PASSWORD', SECRET)
    path = str(tmp_path / 'p.db')
    conn = sqlite3.connect(path)
    apply_notifications_schema(conn)
    conn.commit()
    conn.close()
    return path


def _enqueue(db_path, email_type, recipient='someone@shop.test', subject='s', body=VERIFY_BODY, company_id='c1'):
    conn = sqlite3.connect(db_path)
    row_id = EmailOutboxRepository(conn).enqueue(
        company_id=company_id, email_type=email_type, recipient=recipient, subject=subject, body_text=body)
    conn.commit()
    conn.close()
    return row_id


def _statuses(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute('SELECT email_type, status FROM email_outbox ORDER BY id').fetchall()
    conn.close()
    return rows


def _set_enabled(db_path, value, company_id='c1'):
    conn = sqlite3.connect(db_path)
    settings.set_setting(conn, company_id, 'enabled', value)
    conn.commit()
    conn.close()


class _Session:
    """requests.Session-shaped; records every POSTed JSON body."""

    def __init__(self, reply=None):
        self.posted = []
        self.reply = reply if reply is not None else {'result': 'QUEUED', 'message_id': 'm1'}

    def request(self, method, url, json=None, **_):
        self.posted.append((method, url, json))

        class _R:
            status_code = 202
            headers = {}

            def __init__(s, body):
                s._b = body

            def json(s):
                return s._b
        return _R(self.reply)


class _Signer:
    def sign(self, canonical_bytes):
        return 'sig'


class _Installation:
    def __init__(self, value=None):
        self.value = value

    def __call__(self):
        return self.value


def _relay(session, installation):
    return transport.RelayTransport(
        client_factory=lambda: LicensingClient(
            LicensingClientConfig(base_url='http://owner.test/api/licensing/v1', max_retries=0), session=session),
        signer_factory=_Signer,
        installation_id_provider=installation,
        app_version='1.0.0', platform='WINDOWS',
    )


def _worker(db_path, relay=None):
    return EmailOutboxWorker(
        conn_factory=_conn_factory(db_path), company_id='c1', rate_limit_seconds=0,
        transport_factory=lambda: transport.choose_transport(relay),
    )


@pytest.fixture
def smtp_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(smtp_client, 'send_email', lambda **kw: calls.append(kw))
    return calls


# -- chooser: each of the three branches, counting which sender ran ----------

def test_smtp_host_set_uses_direct_smtp_and_never_the_relay(db, monkeypatch, smtp_calls):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.test')
    session = _Session()
    _enqueue(db, 'verify_email')
    result = _worker(db, _relay(session, _Installation('inst-1'))).run_once()
    assert result['transport'] == 'smtp'
    assert len(smtp_calls) == 1 and session.posted == []
    assert _statuses(db) == [('verify_email', 'SENT')]


def test_no_smtp_but_activated_owner_relays_and_never_uses_smtp(db, smtp_calls):
    session = _Session()
    _enqueue(db, 'verify_email', recipient='boss@shop.test')
    result = _worker(db, _relay(session, _Installation('inst-1'))).run_once()
    assert result['transport'] == 'relay'
    assert smtp_calls == [] and len(session.posted) == 1
    assert _statuses(db) == [('verify_email', 'SENT')]


def test_neither_configured_is_not_configured_and_rows_stay_queued(db, smtp_calls):
    session = _Session()
    _enqueue(db, 'verify_email')
    assert transport.choose_transport(None) is None
    result = _worker(db, None).run_once()
    assert result == {'ran': False, 'reason': 'no_transport'}
    assert smtp_calls == [] and session.posted == []
    assert _statuses(db) == [('verify_email', 'QUEUED')]


def test_owner_configured_but_not_activated_waits_then_relays_exactly_once(db, smtp_calls):
    """The create-admin-before-activation case: queued now, goes out after."""
    session = _Session()
    installation = _Installation(None)
    worker = _worker(db, _relay(session, installation))
    _enqueue(db, 'verify_email')
    assert worker.run_once()['reason'] == 'no_transport'
    assert session.posted == [] and _statuses(db) == [('verify_email', 'QUEUED')]
    installation.value = 'inst-1'                      # activation happens
    assert worker.run_once()['outcomes']['sent'] == 1
    assert worker.run_once()['claimed'] == 0           # and never again
    assert len(session.posted) == 1 and smtp_calls == []
    assert _statuses(db) == [('verify_email', 'SENT')]


# -- the company toggle: identity kinds bypass it, shop kinds do not ---------

def test_company_toggle_off_still_sends_account_mail_but_holds_shop_alerts(db, smtp_calls):
    session = _Session()
    _enqueue(db, 'verify_email')
    _enqueue(db, 'password_reset', body=VERIFY_BODY.replace('verify-email', 'reset-password'))
    _enqueue(db, 'low_stock_alert', body=LOW_BODY)
    _enqueue(db, 'shift_close_report', body='Cash session closed.\nVariance: 0')
    # toggle never set -> default '0'
    _worker(db, _relay(session, _Installation('inst-1'))).run_once()
    assert _statuses(db) == [('verify_email', 'SENT'), ('password_reset', 'SENT'),
                             ('low_stock_alert', 'QUEUED'), ('shift_close_report', 'QUEUED')]
    assert [p[2]['kind'] for p in session.posted] == ['verify_email', 'password_reset']


def test_company_toggle_on_sends_shop_alerts_too(db, smtp_calls):
    session = _Session()
    _set_enabled(db, '1')
    _enqueue(db, 'low_stock_alert', body=LOW_BODY)
    _enqueue(db, 'shift_close_report', body='Cash session closed.\nVariance: 0')
    _enqueue(db, 'report_summary', body='Aura Retail -- 30-day summary report\nRevenue: JD 1.000')
    _worker(db, _relay(session, _Installation('inst-1'))).run_once()
    assert [p[2]['kind'] for p in session.posted] == ['low_stock', 'shift_close', 'report_summary']
    assert {s for _t, s in _statuses(db)} == {'SENT'}


# -- relay payload ------------------------------------------------------------

def _row(email_type, body, recipient='r@shop.test'):
    return {'email_type': email_type, 'recipient': recipient, 'body_text': body}


def test_payload_shapes():
    ident = transport.relay_payload_for(_row('verify_email', VERIFY_BODY, 'a@b.test'))
    assert ident == {'kind': 'verify_email', 'params': {'email': 'a@b.test', 'link': 'http://127.0.0.1:5001/#verify-email/' + 'a' * 32}}
    low = transport.relay_payload_for(_row('low_stock_alert', LOW_BODY))
    assert low['kind'] == 'low_stock' and low['params']['items'] == [{'name': 'Widget', 'qty': '3'}]
    shift = transport.relay_payload_for(_row('shift_close_report', 'a\n\nb\tc\n' + 'x' * 500))
    assert shift['kind'] == 'shift_close'
    assert all(len(line) <= 200 and '\t' not in line for line in shift['params']['lines'])
    unknown = transport.relay_payload_for(_row('something_else', 'one\ntwo'))
    assert unknown['kind'] == 'report_summary' and unknown['params']['lines'] == ['one', 'two']
    with pytest.raises(transport.RelayPayloadError):
        transport.relay_payload_for(_row('verify_email', 'no link here'))


def test_no_password_or_credential_field_is_ever_in_a_relay_body(db):
    """Inspects the SERIALISED body the real LicensingClient would POST, with an
    SMTP password set in the environment and a password_reset message queued."""
    session = _Session()
    _enqueue(db, 'verify_email')
    _enqueue(db, 'password_reset', body=VERIFY_BODY.replace('verify-email', 'reset-password'))
    _worker(db, _relay(session, _Installation('inst-1'))).run_once()
    assert len(session.posted) == 2

    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                yield k
                yield from keys(v)
        elif isinstance(node, list):
            for v in node:
                yield from keys(v)

    for _method, _url, body in session.posted:
        wire = json.dumps(body)
        assert SECRET not in wire
        assert not [k for k in keys(body) if any(w in k.lower() for w in ('pass', 'secret', 'credential', 'smtp'))]
        # and no way to name its own subject/body/sender
        assert not {'subject', 'body', 'body_text', 'body_html', 'html', 'from', 'to_address'} & set(body)


# -- the sink: account mail is queued, never sent inline ---------------------

def test_sink_queues_without_sending_and_reports_not_possible_when_off(db, smtp_calls):
    woken = []
    sink = IdentityMailSink(conn_factory=_conn_factory(db), possible=lambda: True, on_queued=woken.append)
    assert sink.enqueue(company_id='c1', email_type='verify_email', recipient='a@b.test',
                        subject='s', body_text=VERIFY_BODY, body_html='<p>x</p>') is True
    assert smtp_calls == [] and _statuses(db) == [('verify_email', 'QUEUED')] and woken == ['c1']

    off = IdentityMailSink(conn_factory=_conn_factory(db), possible=lambda: False, on_queued=woken.append)
    assert off.enqueue(company_id='c1', email_type='verify_email', recipient='a@b.test',
                       subject='s', body_text=VERIFY_BODY, body_html='<p>x</p>') is False
    assert len(_statuses(db)) == 1


def test_a_relay_refusal_is_a_retry_not_a_sent_row(db):
    session = _Session(reply={'result': 'FAILURE', 'reason_code': 'MAIL_LIMIT'})
    _enqueue(db, 'verify_email')
    out = _worker(db, _relay(session, _Installation('inst-1'))).run_once()
    assert out['outcomes'] == {'sent': 0, 'retry': 1, 'failed_permanent': 0}
    assert _statuses(db) == [('verify_email', 'QUEUED')]
    conn = sqlite3.connect(db)
    assert conn.execute('SELECT last_error FROM email_outbox').fetchone()[0] == 'RelayRefusedError'
    conn.close()


# -- the relay never carries a shop name (Owner refuses the key) -------------

def test_relay_wire_body_never_contains_shop_name_even_when_one_is_configured(db):
    # shop_name used to be an optional send_mail argument fed by a provider. Owner's
    # relay now refuses that key (400), so neither seam may exist any more.
    session = _Session()
    _enqueue(db, 'verify_email', recipient='boss@shop.test')
    result = _worker(db, _relay(session, _Installation('inst-1'))).run_once()
    assert result['outcomes']['sent'] == 1
    (_method, _url, body), = session.posted
    assert 'shop_name' not in body and 'shop_name' not in json.dumps(body)
    with pytest.raises(TypeError):
        transport.RelayTransport(
            client_factory=lambda: None, signer_factory=_Signer, installation_id_provider=_Installation('i'),
            app_version='1', platform='WINDOWS', shop_name_provider=lambda: 'Ahmad & Sons')
    with pytest.raises(TypeError):
        LicensingClient(LicensingClientConfig(base_url='http://owner.test/api/licensing/v1', max_retries=0),
                        session=session).send_mail(
            installation_id='i', kind='verify_email', params={}, signer=_Signer(), app_version='1',
            platform='WINDOWS', shop_name='Ahmad & Sons')
