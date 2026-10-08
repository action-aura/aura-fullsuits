"""The three additive LicensingClient methods the update channel uses.

The download tests drive the REAL LicensingClient.download_release against a
fake streaming session, so the size ceiling, the cleanup and the no-retry rule
are the production code's, not a stub's.
"""
import json
import os

import pytest
import requests

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.client import (
    LicensingClient,
    LicensingClientConfig,
    LicensingClientError,
    NetworkError,
)

BASE = "https://owner.example/api/licensing/v1"


class FakeStreamResponse:
    def __init__(self, chunks, status_code=200, headers=None, fail_after=None):
        self._chunks = list(chunks)
        self.status_code = status_code
        self.headers = headers or {}
        self._fail_after = fail_after
        self.closed = False

    def iter_content(self, chunk_size=1):
        for i, c in enumerate(self._chunks):
            if self._fail_after is not None and i >= self._fail_after:
                raise requests.exceptions.ConnectionError("link dropped")
            yield c

    def json(self):
        return {"ok": True}

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class RecordingSigner:
    def __init__(self):
        self.signed = []

    def sign(self, canonical_bytes):
        self.signed.append(canonical_bytes)
        return "sig-b64"

    def get_public_key_b64(self):
        return "pk"


def client(session, **cfg):
    return LicensingClient(LicensingClientConfig(base_url=BASE, **cfg), session=session, sleep_fn=lambda s: None)


def test_find_latest_release_posts_a_signed_body_to_the_latest_route():
    session = FakeSession(FakeStreamResponse([]))
    signer = RecordingSigner()
    client(session).find_latest_release(installation_id="inst-1", app_version="1.0.0-rc.8",
                                        release_channel="rc", signer=signer, request_id="req-9")
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", BASE + "/releases/latest")
    body = kwargs["json"]
    assert body["signature"] == "sig-b64"
    assert body["request_id"] == "req-9"          # caller-chosen, so the echo can be checked
    assert body["app_version"] == "1.0.0-rc.8" and body["release_channel"] == "rc"
    assert body["installation_id"] == "inst-1"
    # The signature covers everything EXCEPT itself, exactly like check-in.
    unsigned = {k: v for k, v in body.items() if k != "signature"}
    assert signer.signed == [canonicalize_bytes(unsigned)]


def test_find_latest_release_without_a_request_id_makes_one():
    session = FakeSession(FakeStreamResponse([]))
    client(session).find_latest_release(installation_id="i", app_version="1.0.0", release_channel=None,
                                        signer=RecordingSigner())
    body = session.calls[0][2]["json"]
    assert body["request_id"] and body["release_channel"] is None


def test_authorize_release_download_posts_to_the_authorize_route():
    session = FakeSession(FakeStreamResponse([]))
    signer = RecordingSigner()
    client(session).authorize_release_download(installation_id="inst-1", product_version_id="pv-1", signer=signer)
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", BASE + "/releases/authorize-download")
    assert kwargs["json"]["product_version_id"] == "pv-1"
    assert len(signer.signed) == 1


def test_download_streams_to_the_destination_and_reports_progress(tmp_path):
    dest = tmp_path / "x.part"
    seen = []
    session = FakeSession(FakeStreamResponse([b"abc", b"def"], headers={"Content-Length": "6"}))
    client(session).download_release("tok", dest, expected_size=6, progress=seen.append)
    assert dest.read_bytes() == b"abcdef"
    assert seen == [3, 6]
    assert session.calls[0][2]["stream"] is True


def test_download_honours_verify_tls_and_never_puts_the_token_in_a_query_or_traversal(tmp_path):
    session = FakeSession(FakeStreamResponse([b"a"]))
    client(session, verify_tls=False).download_release("../evil?x=1", tmp_path / "x", expected_size=1)
    method, url, kwargs = session.calls[0]
    assert kwargs["verify"] is False
    assert url == BASE + "/releases/download/..%2Fevil%3Fx%3D1"


def test_more_bytes_than_the_signed_size_aborts_and_deletes_the_file(tmp_path):
    dest = tmp_path / "x.part"
    session = FakeSession(FakeStreamResponse([b"1234", b"5678"]))
    with pytest.raises(LicensingClientError) as exc:
        client(session).download_release("tok", dest, expected_size=6)
    assert exc.value.reason_code == "DOWNLOAD_TOO_LARGE"
    assert not dest.exists()


def test_an_announced_oversize_is_refused_before_a_byte_is_written(tmp_path):
    dest = tmp_path / "x.part"
    session = FakeSession(FakeStreamResponse([b"1"], headers={"Content-Length": "999999"}))
    with pytest.raises(LicensingClientError) as exc:
        client(session).download_release("tok", dest, expected_size=10)
    assert exc.value.reason_code == "DOWNLOAD_TOO_LARGE"
    assert not dest.exists()


def test_a_refused_token_is_one_attempt_not_a_retry_loop(tmp_path):
    session = FakeSession(FakeStreamResponse([], status_code=503))
    with pytest.raises(LicensingClientError) as exc:
        client(session, max_retries=4).download_release("tok", tmp_path / "x", expected_size=10)
    assert exc.value.reason_code == "DOWNLOAD_REFUSED"
    assert len(session.calls) == 1   # the token is single-use; a second try could only fail


def test_a_connection_that_drops_mid_stream_deletes_the_partial_file(tmp_path):
    dest = tmp_path / "x.part"
    session = FakeSession(FakeStreamResponse([b"aa", b"bb", b"cc"], fail_after=2))
    with pytest.raises(NetworkError):
        client(session).download_release("tok", dest, expected_size=100)
    assert not dest.exists()
    assert len(session.calls) == 1


def test_a_connect_failure_is_one_attempt(tmp_path):
    session = FakeSession(requests.exceptions.ConnectionError("down"))
    with pytest.raises(NetworkError):
        client(session).download_release("tok", tmp_path / "x", expected_size=10)
    assert len(session.calls) == 1


def test_the_response_is_always_closed(tmp_path):
    resp = FakeStreamResponse([b"a"])
    client(FakeSession(resp)).download_release("tok", tmp_path / "x", expected_size=1)
    assert resp.closed
