import pytest
import requests

from commercial_runtime.licensing_contracts.client import (
    LicensingClient,
    LicensingClientConfig,
    MalformedResponseError,
    NetworkError,
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
    every call for assertion."""

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
    def sign(self, canonical_bytes: bytes) -> str:
        return "fake-signature-b64"

    def get_public_key_b64(self) -> str:
        return "fake-pub-b64"


class RecordingSleep:
    """Injected in place of time.sleep so retry/backoff tests run instantly
    instead of waiting through a real production-shaped delay schedule."""

    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def _config(**overrides):
    defaults = dict(base_url="https://licensing.example.internal/api/licensing/v1")
    defaults.update(overrides)
    return LicensingClientConfig(**defaults)


def _client(session, **config_overrides):
    return LicensingClient(_config(**config_overrides), session=session, sleep_fn=RecordingSleep())


def test_activate_success_returns_parsed_json():
    session = FakeSession([FakeResponse(200, {"result": "SUCCESS", "reason_code": "ACTIVATION_APPROVED"})])
    client = _client(session)
    result = client.activate(
        product_code="AURA_RETAIL",
        platform="WINDOWS",
        app_version="1.0.0-rc.2",
        release_channel="rc",
        installation_id="client-generated-id",
        device_public_key_b64="pub",
        license_key="AURA-RETAIL-XXXX-YYYY",
        idempotency_key="idem-1",
        signer=FakeSigner(),
    )
    assert result["reason_code"] == "ACTIVATION_APPROVED"
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"].endswith("/activations")


def test_activate_sends_license_key_only_in_the_activation_call():
    session = FakeSession([FakeResponse(200, {"result": "SUCCESS"})])
    client = _client(session)
    client.activate(
        product_code="AURA_RETAIL", platform="WINDOWS", app_version="1.0.0-rc.2", release_channel="rc",
        installation_id="id-1", device_public_key_b64="pub", license_key="SECRET-KEY-VALUE",
        idempotency_key="idem-1", signer=FakeSigner(),
    )
    sent_body = session.calls[0]["json"]
    assert sent_body["license_key"] == "SECRET-KEY-VALUE"


def test_checkin_body_has_no_license_key_field_at_all():
    session = FakeSession([FakeResponse(200, {"result": "SUCCESS"})])
    client = _client(session)
    client.check_in(installation_id="id-1", signer=FakeSigner())
    sent_body = session.calls[0]["json"]
    assert "license_key" not in sent_body


def test_request_is_signed():
    session = FakeSession([FakeResponse(200, {})])
    client = _client(session)
    client.check_in(installation_id="id-1", signer=FakeSigner())
    sent_body = session.calls[0]["json"]
    assert sent_body["signature"] == "fake-signature-b64"


def test_retries_on_503_then_succeeds():
    session = FakeSession([FakeResponse(503, headers={}), FakeResponse(200, {"result": "SUCCESS"})])
    client = _client(session)
    result = client.check_in(installation_id="id-1", signer=FakeSigner())
    assert result["result"] == "SUCCESS"
    assert len(session.calls) == 2


def test_respects_retry_after_header():
    session = FakeSession([FakeResponse(429, headers={"Retry-After": "7.5"}), FakeResponse(200, {"ok": True})])
    client = LicensingClient(_config(), session=session, sleep_fn=(recorder := RecordingSleep()))
    result = client.check_in(installation_id="id-1", signer=FakeSigner())
    assert result["ok"] is True
    assert 7.5 in recorder.calls


def test_exhausts_retries_and_raises_network_error():
    session = FakeSession([FakeResponse(503)] * 10)
    client = _client(session, max_retries=4)
    with pytest.raises(NetworkError):
        client.check_in(installation_id="id-1", signer=FakeSigner())
    assert len(session.calls) == 5  # 1 initial + 4 retries


def test_timeout_is_retried_then_raises_network_error():
    session = FakeSession([requests.exceptions.Timeout("timed out")] * 10)
    client = _client(session)
    with pytest.raises(NetworkError) as exc:
        client.check_in(installation_id="id-1", signer=FakeSigner())
    assert exc.value.reason_code == "REQUEST_TIMED_OUT"


def test_tls_error_is_never_retried():
    session = FakeSession([requests.exceptions.SSLError("bad cert")])
    client = _client(session)
    with pytest.raises(NetworkError) as exc:
        client.check_in(installation_id="id-1", signer=FakeSigner())
    assert exc.value.reason_code == "TLS_VERIFICATION_FAILED"
    assert len(session.calls) == 1  # not retried


def test_4xx_business_rejection_is_not_retried():
    session = FakeSession([FakeResponse(400, {"result": "REJECTED", "reason_code": "INVALID_SIGNATURE"})])
    client = _client(session)
    result = client.check_in(installation_id="id-1", signer=FakeSigner())
    assert result["reason_code"] == "INVALID_SIGNATURE"
    assert len(session.calls) == 1


def test_malformed_json_response_raises():
    session = FakeSession([FakeResponse(200, raise_json_error=True)])
    client = _client(session)
    with pytest.raises(MalformedResponseError):
        client.check_in(installation_id="id-1", signer=FakeSigner())


def test_fetch_signing_keys_is_a_plain_get():
    session = FakeSession([FakeResponse(200, {"keys": []})])
    client = _client(session)
    result = client.fetch_signing_keys()
    assert result == {"keys": []}
    assert session.calls[0]["method"] == "GET"
    assert session.calls[0]["json"] is None


def test_verify_tls_flag_is_passed_through():
    session = FakeSession([FakeResponse(200, {})])
    client = _client(session, verify_tls=False)
    client.fetch_service_info()
    assert session.calls[0]["verify"] is False


def test_no_real_sleep_happens_with_injected_sleep_fn():
    import time as time_module

    session = FakeSession([FakeResponse(503)] * 2 + [FakeResponse(200, {"ok": True})])
    client = _client(session)
    start = time_module.monotonic()
    client.check_in(installation_id="id-1", signer=FakeSigner())
    elapsed = time_module.monotonic() - start
    assert elapsed < 0.5  # would be seconds with the real production backoff schedule
