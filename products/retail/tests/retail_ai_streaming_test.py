"""Aura Retail -- AI Assistant streaming suite (2026-08-13 speed pass).

Covers the streaming half of ai_chat() (products/retail/backend/api/
retail_api.py): `stream: true` on the request body opts into an NDJSON
reply (`{"delta":"..."}` lines, a final `{"done":true}`, or a mid-stream
`{"error":"..."}`) proxied from Ollama's own streaming /api/generate
response, instead of the original one-shot `{success, data:{reply}}` JSON.

This is OPT-IN, not the default -- the entire point is that
retail_ai_rag_multitenant_test.py's existing non-streaming assertions stay
green with ZERO edits to that file (verified: see this branch's own test
run). Test 1 below pins that regression lock explicitly in this file too.

The upstream fake here (_FakeStreamResponse) simulates Ollama's real
streaming wire shape: iter_lines() yields raw bytes, one JSON object per
line, each carrying a `response` fragment until a final line with
`"done": true`. Real, on-droplet testing (SSH, direct curl with
`stream:true`) confirmed tokens actually arrive incrementally through the
Caddy-fronted endpoint (not buffered) before this feature was built on top
of that assumption -- see this branch's commit message for the timestamped
proof. This suite tests the PROXY logic (retail_api.py's own generator,
NDJSON envelope, and failure handling), not the droplet itself.

Run:
    pytest products/retail/tests/retail_ai_streaming_test.py -v
"""
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
import requests

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_ai_stream_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402

seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from commercial_runtime.security.passwords import hash_password  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_user():
    email = f'ai-stream-{uuid.uuid4().hex[:8]}@test.local'
    password = 'AiStreamTestPW1'
    company_id = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (str(uuid.uuid4()), company_id, 'EMP-0001', email, hash_password(password), 'admin', 'active'),
    )
    conn.commit()
    conn.close()
    client = app.test_client()
    r = client.post('/api/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.get_json()
    return client


@pytest.fixture
def client():
    return _make_user()


ENGLISH_MSG = 'How do I add a new product?'


# ── Fakes ────────────────────────────────────────────────────────────────
class _FakeOllamaJSONResponse:
    """Non-streaming shape -- same as retail_ai_rag_multitenant_test.py's
    _FakeOllamaResponse, duplicated locally (not imported) so this file
    stays self-contained per this suite's existing convention (no shared
    conftest.py -- confirmed by inspection, and run_all_tests.py's
    per-file-subprocess isolation depends on that staying true)."""
    def __init__(self, prompt):
        self._prompt = prompt
        self.status_code = 200

    def json(self):
        return {'response': self._prompt}


class _FakeStreamResponse:
    """Simulates requests.Response when called with stream=True against
    Ollama's real streaming /api/generate: status_code up front (headers
    arrive before iter_lines() is ever pulled, mirroring real `requests`
    behavior), iter_lines() yields raw bytes lines, close() is tracked so
    tests can assert the upstream connection is always released."""
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        for i, line in enumerate(self._lines):
            if self._raise_after is not None and i == self._raise_after:
                raise self._raise_exc
            yield line.encode('utf-8') if not decode_unicode else line

    def close(self):
        self.closed = True

    _raise_after = None
    _raise_exc = None


def _ollama_lines(fragments, eval_count=None, done_reason='stop'):
    lines = [json.dumps({'response': f, 'done': False}) for f in fragments]
    done_obj = {'done': True, 'done_reason': done_reason}
    if eval_count is not None:
        done_obj['eval_count'] = eval_count
        done_obj['eval_duration'] = 1_000_000_000
    lines.append(json.dumps(done_obj))
    return lines


class _FakePostRouter:
    """One fake requests.post that branches on the `stream` kwarg, matching
    ai_chat()'s two real call shapes exactly: the non-streaming branch calls
    requests.post(url, headers=, json=, timeout=) with NO stream kwarg at
    all (this is the exact signature retail_ai_rag_multitenant_test.py's
    fake pins -- keeping it identical here is itself a regression check);
    the streaming branch adds stream=True. A response object is queued via
    `queue_response()`."""
    def __init__(self):
        self.calls = []
        self._response = None
        self._raise = None

    def queue_response(self, response):
        self._response = response

    def queue_exception(self, exc):
        self._raise = exc

    def __call__(self, url, headers=None, json=None, timeout=None, stream=False):
        self.calls.append({'url': url, 'headers': headers, 'json': json, 'timeout': timeout, 'stream': stream})
        if self._raise is not None:
            raise self._raise
        return self._response


@pytest.fixture
def router(monkeypatch):
    r = _FakePostRouter()
    monkeypatch.setattr(requests, 'post', r)
    return r


def _stream_lines(resp):
    """Parses a Flask test-client streaming response body into a list of
    decoded JSON objects, one per NDJSON line. Uses get_data() (the
    Werkzeug test client fully materializes the generator's output) rather
    than trying to read incrementally -- incremental timing is a real-server
    concern (see this branch's manual `requests`-based benchmarking against
    the live dev server), not something the Werkzeug test client can
    meaningfully measure."""
    text = resp.get_data(as_text=True)
    return [json.loads(line) for line in text.split('\n') if line.strip()]


# ═════════════════════════════════════════════════════════════════════════
# 1. Regression lock -- no `stream` key behaves exactly as before
# ═════════════════════════════════════════════════════════════════════════

def test_no_stream_key_returns_original_json_shape(client, router):
    router.queue_response(_FakeOllamaJSONResponse('a real reply'))
    r = client.post('/api/sub/retail/ai/chat', json={'message': ENGLISH_MSG})
    assert r.status_code == 200
    assert r.mimetype == 'application/json'
    body = r.get_json()
    assert body == {'success': True, 'data': {'reply': 'a real reply'}}
    assert router.calls[-1]['stream'] is False


# ═════════════════════════════════════════════════════════════════════════
# 2. stream:true -- NDJSON envelope, correct concatenation
# ═════════════════════════════════════════════════════════════════════════

def test_stream_true_returns_ndjson_with_correct_deltas(client, router):
    router.queue_response(_FakeStreamResponse(_ollama_lines(['To ', 'add ', 'a product...'])))
    r = client.post('/api/sub/retail/ai/chat', json={'message': ENGLISH_MSG, 'stream': True})
    assert r.status_code == 200
    assert r.mimetype == 'application/x-ndjson'
    lines = _stream_lines(r)
    deltas = [l['delta'] for l in lines if 'delta' in l]
    assert deltas == ['To ', 'add ', 'a product...']
    assert lines[-1] == {'done': True}
    assert router.calls[-1]['stream'] is True


# ═════════════════════════════════════════════════════════════════════════
# 3. Arabic fragments survive the round trip byte-exact
# ═════════════════════════════════════════════════════════════════════════

def test_stream_arabic_fragments_round_trip_exact(client, router):
    fragments = ['كيف ', 'أضيف ', 'منتج جديد؟']
    router.queue_response(_FakeStreamResponse(_ollama_lines(fragments)))
    r = client.post('/api/sub/retail/ai/chat', json={'message': ENGLISH_MSG, 'stream': True})
    assert r.status_code == 200
    lines = _stream_lines(r)
    deltas = [l['delta'] for l in lines if 'delta' in l]
    assert deltas == fragments
    assert ''.join(deltas) == 'كيف أضيف منتج جديد؟'


# ═════════════════════════════════════════════════════════════════════════
# 4. Pre-stream connection failure -- still the original 503 JSON contract
# ═════════════════════════════════════════════════════════════════════════

def test_stream_connection_error_before_headers_returns_503_json(client, router):
    router.queue_exception(requests.exceptions.ConnectionError('boom'))
    r = client.post('/api/sub/retail/ai/chat', json={'message': ENGLISH_MSG, 'stream': True})
    assert r.status_code == 503
    assert r.mimetype == 'application/json'
    assert r.get_json() == {'success': False, 'error': 'AI assistant is temporarily unavailable.'}


# ═════════════════════════════════════════════════════════════════════════
# 5. Non-200 upstream status (headers arrived, e.g. a 401) -- 503 JSON,
#    and the upstream connection must be released (resp.close() called)
# ═════════════════════════════════════════════════════════════════════════

def test_stream_non_200_upstream_status_returns_503_and_closes_connection(client, router):
    fake = _FakeStreamResponse([], status_code=401)
    router.queue_response(fake)
    r = client.post('/api/sub/retail/ai/chat', json={'message': ENGLISH_MSG, 'stream': True})
    assert r.status_code == 503
    assert r.mimetype == 'application/json'
    assert r.get_json() == {'success': False, 'error': 'AI assistant is temporarily unavailable.'}
    assert fake.closed is True


# ═════════════════════════════════════════════════════════════════════════
# 6. Mid-stream failure -- the new failure mode streaming introduces. Two
#    good fragments must reach the browser before the failure, followed by
#    an in-band {"error":...} line -- NEVER a done line, NEVER a raw 500.
# ═════════════════════════════════════════════════════════════════════════

def test_stream_mid_stream_failure_keeps_good_deltas_then_emits_error(client, router):
    fake = _FakeStreamResponse(_ollama_lines(['To ', 'add ', 'never reached']))
    fake._raise_after = 2  # raise while trying to yield the 3rd line
    fake._raise_exc = requests.exceptions.ChunkedEncodingError('connection dropped')
    router.queue_response(fake)

    r = client.post('/api/sub/retail/ai/chat', json={'message': ENGLISH_MSG, 'stream': True})
    assert r.status_code == 200   # headers were already committed as 200 before the failure
    lines = _stream_lines(r)
    deltas = [l['delta'] for l in lines if 'delta' in l]
    assert deltas == ['To ', 'add ']
    assert 'error' in lines[-1]
    assert not any('done' in l for l in lines)
    assert fake.closed is True


# ═════════════════════════════════════════════════════════════════════════
# 7. Zero fragments (upstream yields only a bare done) -- single error line
# ═════════════════════════════════════════════════════════════════════════

def test_stream_zero_fragments_yields_single_error_line(client, router):
    router.queue_response(_FakeStreamResponse(_ollama_lines([])))
    r = client.post('/api/sub/retail/ai/chat', json={'message': ENGLISH_MSG, 'stream': True})
    assert r.status_code == 200
    lines = _stream_lines(r)
    assert len(lines) == 1
    assert 'error' in lines[0]


# ═════════════════════════════════════════════════════════════════════════
# 8. A malformed non-JSON line mid-stream is skipped, not fatal
# ═════════════════════════════════════════════════════════════════════════

def test_stream_malformed_line_is_skipped_not_fatal(client, router):
    raw_lines = [
        json.dumps({'response': 'To ', 'done': False}),
        'not valid json at all {{{',
        json.dumps({'response': 'add it.', 'done': False}),
        json.dumps({'done': True, 'done_reason': 'stop'}),
    ]
    router.queue_response(_FakeStreamResponse(raw_lines))
    r = client.post('/api/sub/retail/ai/chat', json={'message': ENGLISH_MSG, 'stream': True})
    assert r.status_code == 200
    lines = _stream_lines(r)
    deltas = [l['delta'] for l in lines if 'delta' in l]
    assert deltas == ['To ', 'add it.']
    assert lines[-1] == {'done': True}


# ═════════════════════════════════════════════════════════════════════════
# 9. Strict identity check: stream:"true" (string) must NOT be treated as
#    streaming -- only the literal boolean True opts in.
# ═════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize('sloppy_value', ['true', 1, 'yes', 'True'])
def test_stream_non_boolean_truthy_value_stays_non_streaming(client, router, sloppy_value):
    router.queue_response(_FakeOllamaJSONResponse('a real reply'))
    r = client.post('/api/sub/retail/ai/chat', json={'message': ENGLISH_MSG, 'stream': sloppy_value})
    assert r.status_code == 200
    assert r.mimetype == 'application/json'
    assert router.calls[-1]['stream'] is False


# ═════════════════════════════════════════════════════════════════════════
# 10. stream:True really does flip the upstream call AND the payload body
# ═════════════════════════════════════════════════════════════════════════

def test_stream_true_flips_fake_call_and_payload_body(client, router):
    router.queue_response(_FakeStreamResponse(_ollama_lines(['hi'])))
    r = client.post('/api/sub/retail/ai/chat', json={'message': ENGLISH_MSG, 'stream': True})
    assert r.status_code == 200
    call = router.calls[-1]
    assert call['stream'] is True
    assert call['json']['stream'] is True
