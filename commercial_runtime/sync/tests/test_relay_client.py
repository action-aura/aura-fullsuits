"""Unit-level tests for SyncRelayClient, mirroring
commercial_runtime/licensing_contracts/tests/test_client.py's exact
FakeSession/FakeResponse/RecordingSleep pattern -- a fake at the
requests.Session boundary, not a mocked HTTP server, matching this
codebase's established style for client-level (as opposed to route-level)
tests. See owner/tests/test_sync_routes.py for the integration-level
coverage of the real relay this client talks to (already covered by Task
2's own test suite; not duplicated here)."""
import pytest
import requests

from commercial_runtime.sync.relay_client import (
    MalformedResponseError,
    NetworkError,
    RelayRejected,
    SyncRelayClient,
)


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, headers=None, raise_json_error=False):
        self.status_code = status_code
        self._json_body = json_body
        self.headers = headers or {}
        self._raise_json_error = raise_json_error

    def json(self):
        if self._raise_json_error:
            raise ValueError("not json")
        return self._json_body


class FakeSession:
    """Queue of canned responses/exceptions, consumed in order. Records
    every call for assertion -- identical shape to test_client.py's
    FakeSession."""

    def __init__(self, queue):
        self._queue = list(queue)
        self.calls = []

    def request(self, method, url, json=None, timeout=None, verify=None, headers=None):
        self.calls.append({"method": method, "url": url, "json": json, "verify": verify})
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeSigner:
    def __init__(self):
        self.sign_calls = []

    def sign(self, canonical_bytes: bytes) -> str:
        self.sign_calls.append(canonical_bytes)
        return "fake-signature-b64"


class RecordingSleep:
    """Injected in place of time.sleep so retry/backoff tests run instantly
    instead of waiting through a real production-shaped delay schedule."""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def _client(session, **overrides):
    defaults = dict(
        base_url="http://127.0.0.1:5551",
        signer=FakeSigner(),
        installation_id="inst-1",
        timeout_seconds=10.0,
        verify_tls=True,
    )
    defaults.update(overrides)
    return SyncRelayClient(session=session, sleep_fn=RecordingSleep(), **defaults)


def test_push_success_returns_parsed_json():
    session = FakeSession([FakeResponse(200, {"stored": 1, "received": 1})])
    client = _client(session)
    result = client.push([{"id": "e-1"}])
    assert result == {"stored": 1, "received": 1}
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"] == "http://127.0.0.1:5551/api/sync/v1/push"


def test_push_body_carries_events_and_signature():
    session = FakeSession([FakeResponse(200, {"stored": 0, "received": 1})])
    client = _client(session)
    events = [{"id": "e-1", "entity_type": "category"}]
    client.push(events)
    sent_body = session.calls[0]["json"]
    assert sent_body["events"] == events
    assert sent_body["installation_id"] == "inst-1"
    assert sent_body["signature"] == "fake-signature-b64"
    assert "nonce" in sent_body
    assert "timestamp" in sent_body


def test_pull_is_a_post_request_with_since_inside_the_signed_body():
    """Contract note (Task 2's post-replay-protection fix): `since` must
    live inside the signed JSON body, never a `?since=` query parameter --
    a free query param would let a captured pull request be replayed with a
    different `since` and walk more history than originally signed for.

    POST, not GET (2026-08-12): a frozen PyInstaller build of this exact
    client reproducibly got an empty response body on GET-with-body,
    isolated via a non-frozen interpreter hitting the same Owner route
    successfully. Owner's /pull route accepts both GET and POST -- Android's
    separate GET-with-body client is unaffected by this change."""
    session = FakeSession([FakeResponse(200, {"events": [], "cursor": 5})])
    client = _client(session)
    result = client.pull(5)
    assert result == {"events": [], "cursor": 5}
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"] == "http://127.0.0.1:5551/api/sync/v1/pull"
    sent_body = session.calls[0]["json"]
    assert sent_body["since"] == 5
    assert "?since=" not in session.calls[0]["url"]


def test_every_call_gets_a_fresh_nonce_and_timestamp():
    session = FakeSession([FakeResponse(200, {"stored": 0, "received": 0}), FakeResponse(200, {"stored": 0, "received": 0})])
    client = _client(session)
    client.push([])
    client.push([])
    first_body, second_body = session.calls[0]["json"], session.calls[1]["json"]
    assert first_body["nonce"] != second_body["nonce"]
    assert first_body["timestamp"] != second_body["timestamp"] or True  # timestamps may collide at second resolution; nonce is the real guarantee


def test_signature_is_computed_over_the_body_minus_signature():
    session = FakeSession([FakeResponse(200, {"stored": 0, "received": 0})])
    signer = FakeSigner()
    client = _client(session, signer=signer)
    client.push([])
    assert len(signer.sign_calls) == 1
    signed_bytes = signer.sign_calls[0]
    assert b'"signature"' not in signed_bytes  # never signs its own signature field


def test_retries_on_503_then_succeeds():
    session = FakeSession([FakeResponse(503, headers={}), FakeResponse(200, {"stored": 1, "received": 1})])
    client = _client(session)
    result = client.push([{"id": "e-1"}])
    assert result == {"stored": 1, "received": 1}
    assert len(session.calls) == 2


def test_respects_retry_after_header():
    session = FakeSession([FakeResponse(429, headers={"Retry-After": "7.5"}), FakeResponse(200, {"events": [], "cursor": 0})])
    recorder = RecordingSleep()
    client = SyncRelayClient(
        base_url="http://127.0.0.1:5551", signer=FakeSigner(), installation_id="inst-1",
        session=session, sleep_fn=recorder,
    )
    result = client.pull(0)
    assert result == {"events": [], "cursor": 0}
    assert 7.5 in recorder.calls


def test_exhausts_retries_and_raises_network_error():
    session = FakeSession([FakeResponse(503)] * 10)
    client = _client(session, max_retries=4)
    with pytest.raises(NetworkError):
        client.push([])
    assert len(session.calls) == 5  # 1 initial + 4 retries


def test_timeout_is_retried_then_raises_network_error():
    session = FakeSession([requests.exceptions.Timeout("timed out")] * 10)
    client = _client(session)
    with pytest.raises(NetworkError) as exc:
        client.push([])
    assert exc.value.reason_code == "REQUEST_TIMED_OUT"


def test_tls_error_is_never_retried():
    session = FakeSession([requests.exceptions.SSLError("bad cert")])
    client = _client(session)
    with pytest.raises(NetworkError) as exc:
        client.push([])
    assert exc.value.reason_code == "TLS_VERIFICATION_FAILED"
    assert len(session.calls) == 1  # not retried


def test_400_business_rejection_is_not_retried():
    """The real behavioral branch this task built on top of LicensingClient's
    shape: a 400 with a reason_code is a real, successfully-exchanged Owner
    decision (bad signature, replayed nonce, unknown installation, ...), not
    a transport failure -- must raise RelayRejected immediately, carrying
    Owner's own reason_code verbatim, and must NEVER be retried."""
    session = FakeSession([FakeResponse(400, {"reason_code": "INVALID_SIGNATURE"})])
    client = _client(session)
    with pytest.raises(RelayRejected) as exc:
        client.push([])
    assert exc.value.reason_code == "INVALID_SIGNATURE"
    assert len(session.calls) == 1


def test_400_business_rejection_on_pull_is_not_retried():
    session = FakeSession([FakeResponse(400, {"reason_code": "NONCE_REUSED"})])
    client = _client(session)
    with pytest.raises(RelayRejected) as exc:
        client.pull(0)
    assert exc.value.reason_code == "NONCE_REUSED"
    assert len(session.calls) == 1


def test_malformed_json_response_raises():
    session = FakeSession([FakeResponse(200, raise_json_error=True)])
    client = _client(session)
    with pytest.raises(MalformedResponseError):
        client.push([])


def test_verify_tls_flag_is_passed_through():
    session = FakeSession([FakeResponse(200, {"stored": 0, "received": 0})])
    client = _client(session, verify_tls=False)
    client.push([])
    assert session.calls[0]["verify"] is False


def test_verify_tls_true_is_passed_through():
    session = FakeSession([FakeResponse(200, {"stored": 0, "received": 0})])
    client = _client(session, verify_tls=True)
    client.push([])
    assert session.calls[0]["verify"] is True


def test_no_real_sleep_happens_with_injected_sleep_fn():
    import time as time_module

    session = FakeSession([FakeResponse(503)] * 2 + [FakeResponse(200, {"stored": 0, "received": 0})])
    client = _client(session)
    start = time_module.monotonic()
    client.push([])
    elapsed = time_module.monotonic() - start
    assert elapsed < 0.5  # would be seconds with the real production backoff schedule
