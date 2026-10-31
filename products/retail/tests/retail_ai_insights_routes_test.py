"""Aura Retail -- AI "Smart analysis" routes: the opt-in gate, the Owner path,
the cache, and the legacy assistant left intact.

What is pinned, and which half of each guard is covered:

  DENY  -- opt-in OFF: POST /ai/insights and POST /ai/chat (remote available)
           answer 409 AI_NOT_ENABLED and the Owner client is NEVER called. The
           assertion is "the client was not called", not just "409": a 409 that
           still sent the figures first would be the exact leak this gate
           exists to stop (ENGINEERING.md shape 1 -- count the calls).
  ALLOW -- opt-in ON (written through the real owner-only POST
           /settings/credit): the same routes call the client exactly once,
           with the figures and WITHOUT any customer name, and return/cached
           the result.
  LEGACY-- opt-in off, no Owner, AURA_AI_ENDPOINT_URL set: the old Ollama path
           is still taken (requests.post called once) -- the new gate must not
           eat the feature that already shipped.
  STATUS-- `configured`/`enabled`/`remote` truth table.

The Owner client is replaced at ai_remote's own seam (`_make_client`), so
everything below it -- snapshot build, request shaping, error mapping, cache
write -- is the real code.

Run:
    pytest products/retail/tests/retail_ai_insights_routes_test.py -v
"""
import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

import retail_accountant_regate_test as H  # noqa: E402  (builds the isolated app + data dir)

import api.retail_api as retail_api  # noqa: E402
import config  # noqa: E402
from core.retail import ai_remote  # noqa: E402

API = H.API
CUSTOMER_NAME = 'Hanadi Abdelrahman-Testington'
CUSTOMER_PHONE = '0788765432'
OWNER_URL = 'https://owner.test/api/licensing/v1'


class FakeClient:
    """Stands in for LicensingClient. Records every call; `reply` is what Owner
    "answers" (a dict), or an Exception to raise."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def ai_shop_analysis(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


REPORT = {'headline': 'Sales are steady', 'performance': ['ok'], 'trends': [], 'customers': [],
          'suggestions': [{'title': 'Restock milk', 'why': 'low', 'action': 'order'}],
          'deep_dive': [], 'warnings': []}
REPORT_OK = {'contract_version': 'v1', 'result': 'ACCEPTED', 'kind': 'report', 'model': 'haiku-test',
             'usage': {'used': 1, 'cap': 120}, 'report': REPORT}
CHAT_OK = {'contract_version': 'v1', 'result': 'ACCEPTED', 'kind': 'chat', 'model': 'haiku-test',
           'usage': {'used': 1, 'cap': 120}, 'reply': 'Milk is selling best.'}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    H.device_context._cached_device = None
    monkeypatch.setattr(retail_api, '_sync_nudge', lambda *a, **k: None)
    # Default: no Owner, no legacy endpoint.
    monkeypatch.setattr(config, 'OWNER_LICENSING_BASE_URL', '')
    monkeypatch.setattr(retail_api, 'AURA_AI_ENDPOINT_URL', '')
    yield
    H.device_context._cached_device = None


def _owner(monkeypatch, reply):
    """Make Owner 'reachable in principle' and route the call to a fake."""
    monkeypatch.setattr(config, 'OWNER_LICENSING_BASE_URL', OWNER_URL)
    monkeypatch.setattr(config, 'LICENSING_PLATFORM', 'WINDOWS')
    monkeypatch.setattr(ai_remote, '_installation_id', lambda: 'inst-test')
    monkeypatch.setattr(ai_remote, '_make_signer', lambda: object())
    fake = FakeClient(reply)
    monkeypatch.setattr(ai_remote, '_make_client', lambda: fake)
    return fake


def _shop():
    admin, cid, _pid = H._new_shop()
    return admin, cid


def _enable(admin, value='1'):
    r = admin.post(f'{API}/settings/credit', json={'ai_analysis_enabled': value})
    assert r.status_code == 200, r.get_json()
    return r


def _seed_named_customer_sale(cid):
    conn = H.get_retail_conn()
    try:
        # Explicit TEXT uuid id (customers.id is TEXT; a NULL id would orphan the sale's customer_id
        # and make the "no name on the wire" assertion pass over an empty join).
        cust = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO customers (id,company_id,name,phone,email,total_spent) VALUES (?,?,?,?,?,9)",
            (cust, cid, CUSTOMER_NAME, CUSTOMER_PHONE, 'hanadi@secret.example'))
        when = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute(
            "INSERT INTO sales (company_id,sale_number,branch_id,customer_id,cashier,subtotal,discount_amount,"
            "tax_amount,total,amount_paid,payment_method,status,created_at,uid) "
            "VALUES (?,?,1,?,'POS',9,0,0,9,9,'cash','completed',?,?)",
            (cid, f'AIR-{uuid.uuid4().hex[:10]}', cust, when, str(uuid.uuid4())))
        conn.commit()
    finally:
        conn.close()


# ── status ───────────────────────────────────────────────────────────────────

def test_status_truth_table(monkeypatch):
    admin, _ = _shop()
    s = admin.get(f'{API}/ai/status').get_json()
    assert s == {'configured': False, 'enabled': False, 'remote': False}

    monkeypatch.setattr(retail_api, 'AURA_AI_ENDPOINT_URL', 'http://legacy.test/api/generate')
    s = admin.get(f'{API}/ai/status').get_json()
    assert s == {'configured': True, 'enabled': False, 'remote': False}

    monkeypatch.setattr(retail_api, 'AURA_AI_ENDPOINT_URL', '')
    _owner(monkeypatch, REPORT_OK)
    s = admin.get(f'{API}/ai/status').get_json()
    assert s == {'configured': True, 'enabled': False, 'remote': True}

    _enable(admin)
    assert admin.get(f'{API}/ai/status').get_json()['enabled'] is True


def test_remote_is_false_on_android_and_without_an_installation_id(monkeypatch):
    admin, _ = _shop()
    _owner(monkeypatch, REPORT_OK)
    monkeypatch.setattr(config, 'LICENSING_PLATFORM', 'ANDROID')
    assert admin.get(f'{API}/ai/status').get_json()['remote'] is False
    monkeypatch.setattr(config, 'LICENSING_PLATFORM', 'WINDOWS')
    monkeypatch.setattr(ai_remote, '_installation_id', lambda: None)
    assert admin.get(f'{API}/ai/status').get_json()['remote'] is False


# ── DENY: opt-in off ─────────────────────────────────────────────────────────

def test_insights_refused_when_opt_in_is_off_and_nothing_is_sent(monkeypatch):
    admin, cid = _shop()
    fake = _owner(monkeypatch, REPORT_OK)
    r = admin.post(f'{API}/ai/insights', json={'lang': 'en'})
    assert r.status_code == 409, r.get_json()
    assert r.get_json()['code'] == 'AI_NOT_ENABLED' and r.get_json()['success'] is False
    assert fake.calls == [], 'the figures must not leave the till when the shop has not opted in'


def test_insights_refusal_message_is_arabic_when_asked(monkeypatch):
    admin, _ = _shop()
    _owner(monkeypatch, REPORT_OK)
    err = admin.post(f'{API}/ai/insights', json={'lang': 'ar'}).get_json()['error']
    assert any('؀' <= ch <= 'ۿ' for ch in err), err


def test_chat_refused_when_opt_in_is_off_even_with_a_legacy_endpoint(monkeypatch):
    admin, _ = _shop()
    fake = _owner(monkeypatch, CHAT_OK)
    monkeypatch.setattr(retail_api, 'AURA_AI_ENDPOINT_URL', 'http://legacy.test/api/generate')
    posted = {'n': 0}
    monkeypatch.setattr(requests, 'post', lambda *a, **k: posted.__setitem__('n', posted['n'] + 1))
    r = admin.post(f'{API}/ai/chat', json={'message': 'how are sales?'})
    assert r.status_code == 409 and r.get_json()['code'] == 'AI_NOT_ENABLED', r.get_json()
    assert fake.calls == [] and posted['n'] == 0


def test_chat_stream_refusal_is_still_409_json(monkeypatch):
    admin, _ = _shop()
    fake = _owner(monkeypatch, CHAT_OK)
    r = admin.post(f'{API}/ai/chat', json={'message': 'hi', 'stream': True})
    assert r.status_code == 409 and fake.calls == []


# ── ALLOW: opt-in on ─────────────────────────────────────────────────────────

def test_insights_sends_figures_without_customer_names_and_caches(monkeypatch):
    admin, cid = _shop()
    _seed_named_customer_sale(cid)
    _enable(admin)
    fake = _owner(monkeypatch, REPORT_OK)

    assert admin.get(f'{API}/ai/insights/latest').get_json()['data'] == {'report': None}

    r = admin.post(f'{API}/ai/insights', json={'lang': 'ar'})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()['data']
    assert data['report']['headline'] == 'Sales are steady' and data['model'] == 'haiku-test'
    assert data['generated_at'].endswith('+00:00')

    assert len(fake.calls) == 1
    sent = fake.calls[0]
    assert sent['kind'] == 'report' and sent['lang'] == 'ar' and sent['installation_id'] == 'inst-test'
    assert sent['question'] is None
    snapshot = sent['snapshot']
    assert snapshot['summary']['revenue'] == 9.0
    wire = json.dumps(snapshot, ensure_ascii=False)
    assert CUSTOMER_NAME not in wire and CUSTOMER_PHONE not in wire and 'secret.example' not in wire
    assert snapshot['customers']['top'] == [{'label': 'C1', 'spend': 9.0, 'visits': 1}]

    cache = Path(H.DATA) / 'ai' / f'insights_{cid}.json'
    assert cache.exists(), 'the report must be saved under <app data>/ai/'
    assert not list(cache.parent.glob('*.tmp')), 'the atomic write must leave no temp file'
    assert json.loads(cache.read_text(encoding='utf-8'))['report']['headline'] == 'Sales are steady'

    latest = admin.get(f'{API}/ai/insights/latest').get_json()['data']
    assert latest['report']['headline'] == 'Sales are steady'
    assert latest['generated_at'] == data['generated_at']


def test_insights_failure_returns_friendly_error_and_writes_no_cache(monkeypatch):
    admin, cid = _shop()
    _enable(admin)
    _owner(monkeypatch, {'result': 'FAILURE', 'reason_code': 'AI_BUDGET_EXHAUSTED'})
    r = admin.post(f'{API}/ai/insights', json={'lang': 'en'})
    assert r.status_code == 429 and r.get_json()['code'] == 'AI_BUDGET_EXHAUSTED'
    assert 'limit' in r.get_json()['error'].lower()
    assert not (Path(H.DATA) / 'ai' / f'insights_{cid}.json').exists()


@pytest.mark.parametrize('reason,status,code', [
    ('AI_NOT_CONFIGURED', 503, 'AI_NOT_CONFIGURED'),
    ('AI_UPSTREAM_ERROR', 503, 'AI_UPSTREAM_ERROR'),
    ('AI_PRIVACY_REFUSED', 503, 'AI_PRIVACY_REFUSED'),
    ('INVALID_SIGNATURE', 503, 'AI_LICENCE_PROBLEM'),
    ('ACTIVATION_REJECTED', 503, 'AI_LICENCE_PROBLEM'),
])
def test_owner_codes_map_to_local_codes(monkeypatch, reason, status, code):
    admin, _ = _shop()
    _enable(admin)
    _owner(monkeypatch, {'result': 'FAILURE', 'reason_code': reason})
    r = admin.post(f'{API}/ai/insights', json={})
    assert (r.status_code, r.get_json()['code']) == (status, code)


def test_transport_failure_is_the_busy_message(monkeypatch):
    admin, _ = _shop()
    _enable(admin)
    _owner(monkeypatch, RuntimeError('boom'))
    r = admin.post(f'{API}/ai/insights', json={})
    assert r.status_code == 503 and r.get_json()['code'] == 'AI_UPSTREAM_ERROR'


def test_chat_via_owner_sends_question_history_and_snapshot(monkeypatch):
    admin, cid = _shop()
    _seed_named_customer_sale(cid)
    _enable(admin)
    fake = _owner(monkeypatch, CHAT_OK)
    history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'turn {i}'} for i in range(14)]
    history.append({'role': 'user', 'content': '   '})           # blank: dropped
    r = admin.post(f'{API}/ai/chat', json={'message': 'what sells best?', 'lang': 'en', 'history': history})
    assert r.status_code == 200, r.get_json()
    assert r.get_json() == {'success': True, 'data': {'reply': 'Milk is selling best.'}}
    sent = fake.calls[0]
    assert sent['kind'] == 'chat' and sent['question'] == 'what sells best?'
    assert len(sent['history']) == 10 and sent['history'][-1]['content'] == 'turn 13'
    assert all(set(t) == {'role', 'content'} for t in sent['history'])
    assert CUSTOMER_NAME not in json.dumps(sent['snapshot'])


def test_long_arabic_chat_on_a_big_shop_stays_under_owners_60kb_body_limit(monkeypatch):
    # Every per-turn bound held, yet 10 x 2,000 Arabic chars (~40 KB) plus a
    # ~38 KB snapshot is ~78 KB -- Owner would answer 400 to every later turn.
    admin, _ = _shop()
    _enable(admin)
    fake = _owner(monkeypatch, CHAT_OK)
    big_snapshot = {'currency': 'JOD', 'top_products': [{'name': 'منتج ' + 'ب' * 180, 'units': i}
                                                         for i in range(100)]}
    monkeypatch.setattr(retail_api.ai_snapshot, 'build_snapshot', lambda *a, **k: big_snapshot)
    history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'دور {i} ' + 'ع' * 1990}
               for i in range(10)]
    r = admin.post(f'{API}/ai/chat', json={'message': 'شو أكثر شي بيع؟', 'lang': 'ar', 'history': history})
    assert r.status_code == 200, r.get_json()
    sent = fake.calls[0]
    body = {'snapshot': sent['snapshot'], 'question': sent['question'], 'history': sent['history']}
    size = len(json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
    assert size <= 60 * 1024 - 2048, f'chat body {size} bytes would be refused by Owner (60 KB)'
    # The OLDEST turns gave way; the newest one -- what the question refers to -- survived.
    assert sent['history'] and sent['history'][-1]['content'].startswith('دور 9 ')
    assert len(sent['history']) < 10


def test_a_slow_owner_is_asked_exactly_once_never_replayed(monkeypatch):
    # A retry re-sends the SAME signed body (same nonce): Owner refuses it as
    # NONCE_REUSED while the first request still runs and still spends a cap
    # slot and a paid AI call. Drive the REAL LicensingClient (not the fake)
    # with a transport that always times out, and count the attempts.
    monkeypatch.setattr(config, 'OWNER_LICENSING_BASE_URL', OWNER_URL)
    monkeypatch.setattr(config, 'LICENSING_PLATFORM', 'WINDOWS')
    monkeypatch.setattr(ai_remote, '_installation_id', lambda: 'inst-test')

    class Signer:
        def sign(self, data):
            return 'sig'

    monkeypatch.setattr(ai_remote, '_make_signer', lambda: Signer())
    attempts = []
    real_make_client = ai_remote._make_client

    def make_client():
        client = real_make_client()

        class TimesOut:
            def request(self, *a, **k):
                attempts.append(k.get('timeout'))
                raise requests.exceptions.Timeout('slow report')

        client._session = TimesOut()
        client._sleep = lambda s: None
        return client

    monkeypatch.setattr(ai_remote, '_make_client', make_client)
    with pytest.raises(ai_remote.AiRemoteError) as err:
        ai_remote.request('report', 'ar', {'currency': 'JOD'})
    assert err.value.code == ai_remote.BUSY
    assert len(attempts) == 1, f'the signed AI request was sent {len(attempts)} times'
    # Owner's worst case is two 40 s provider attempts; the till must outwait it.
    assert attempts[0] >= 120, attempts


def test_a_replayed_nonce_reads_as_busy_not_as_a_licence_problem(monkeypatch):
    fake = _owner(monkeypatch, {'result': 'FAILURE', 'reason_code': 'NONCE_REUSED'})
    with pytest.raises(ai_remote.AiRemoteError) as err:
        ai_remote.request('report', 'en', {'currency': 'JOD'})
    assert err.value.code == ai_remote.BUSY and len(fake.calls) == 1


def test_chat_question_is_cut_to_owners_1000_char_limit(monkeypatch):
    admin, _ = _shop()
    _enable(admin)
    fake = _owner(monkeypatch, CHAT_OK)
    admin.post(f'{API}/ai/chat', json={'message': 'q' * 3000})
    assert len(fake.calls[0]['question']) == 1000


def test_chat_stream_via_owner_is_one_delta_then_done(monkeypatch):
    admin, _ = _shop()
    _enable(admin)
    _owner(monkeypatch, CHAT_OK)
    r = admin.post(f'{API}/ai/chat', json={'message': 'hi', 'stream': True})
    assert r.status_code == 200 and r.content_type.startswith('application/x-ndjson')
    lines = [json.loads(x) for x in r.get_data(as_text=True).splitlines() if x]
    assert lines == [{'delta': 'Milk is selling best.'}, {'done': True}]


def test_chat_stream_failure_is_an_error_line(monkeypatch):
    admin, _ = _shop()
    _enable(admin)
    _owner(monkeypatch, {'result': 'FAILURE', 'reason_code': 'AI_UPSTREAM_ERROR'})
    r = admin.post(f'{API}/ai/chat', json={'message': 'hi', 'stream': True})
    lines = [json.loads(x) for x in r.get_data(as_text=True).splitlines() if x]
    assert len(lines) == 1 and 'error' in lines[0]


def test_turning_it_back_off_closes_the_gate_again(monkeypatch):
    admin, _ = _shop()
    fake = _owner(monkeypatch, REPORT_OK)
    _enable(admin, '1')
    assert admin.post(f'{API}/ai/insights', json={}).status_code == 200
    _enable(admin, '0')
    assert admin.post(f'{API}/ai/insights', json={}).status_code == 409
    assert len(fake.calls) == 1


# ── LEGACY path untouched ────────────────────────────────────────────────────

def test_legacy_endpoint_still_used_when_there_is_no_owner(monkeypatch):
    admin, _ = _shop()
    calls = {'n': 0}

    class _Reply:
        status_code = 200

        @staticmethod
        def json():
            return {'response': 'legacy answer'}

    def _post(url, headers=None, json=None, timeout=None):
        calls['n'] += 1
        return _Reply()

    monkeypatch.setattr(requests, 'post', _post)
    monkeypatch.setattr(retail_api, 'AURA_AI_ENDPOINT_URL', 'http://legacy.test/api/generate')
    r = admin.post(f'{API}/ai/chat', json={'message': 'How do I add a product?'})
    assert r.status_code == 200 and r.get_json()['data']['reply'] == 'legacy answer'
    assert calls['n'] == 1


def test_nothing_configured_is_still_the_old_503(monkeypatch):
    admin, _ = _shop()
    r = admin.post(f'{API}/ai/chat', json={'message': 'hi'})
    assert r.status_code == 503 and 'not configured' in r.get_json()['error'].lower()


def test_insights_without_owner_says_not_available_when_opted_in(monkeypatch):
    admin, _ = _shop()
    _enable(admin)
    r = admin.post(f'{API}/ai/insights', json={})
    assert r.status_code == 503 and r.get_json()['code'] == 'AI_NOT_AVAILABLE'


def test_settings_reject_a_junk_opt_in_value():
    admin, _ = _shop()
    r = admin.post(f'{API}/settings/credit', json={'ai_analysis_enabled': 'maybe'})
    assert r.status_code == 400
    assert admin.get(f'{API}/settings/credit').get_json()['data']['ai_analysis_enabled'] == '0'
