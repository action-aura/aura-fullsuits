"""LicensingClient.ai_shop_analysis -- the till -> Owner AI call.

What is pinned here, and why each one matters:
  * the path and the signed envelope (Owner authenticates the signature over
    the canonical body; a body field added AFTER signing would be a 401);
  * `question`/`history` ride ONLY on a chat -- a report must not carry them;
  * an AI refusal (429 / 503 with a JSON body) comes back as that BODY, not as
    a NetworkError and not retried: retrying would re-send the same signed
    nonce to a paid upstream, and swallowing the body would hide WHY.
  * ...while every OTHER method keeps the default retry policy (the allow
    half of the retry change: a 503 on /mail is still retried).
"""
from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes
from commercial_runtime.licensing_contracts.client import (
    LicensingClient,
    LicensingClientConfig,
    NetworkError,
)
import pytest


class _Resp:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body
        self.headers = {}

    def json(self):
        return self._body


class _Session:
    def __init__(self, queue):
        self._queue = list(queue)
        self.calls = []

    def request(self, method, url, json=None, timeout=None, verify=None, headers=None):
        self.calls.append({"method": method, "url": url, "json": json})
        return self._queue.pop(0)


class _Signer:
    def sign(self, canonical_bytes):
        self.signed = canonical_bytes
        return "sig-b64"

    def get_public_key_b64(self):
        return "pub"


def _client(session):
    cfg = LicensingClientConfig(base_url="https://owner.test/api/licensing/v1", max_retries=1)
    return LicensingClient(cfg, session=session, sleep_fn=lambda s: None)


def _call(client, signer, **kw):
    args = dict(
        installation_id="inst-1", signer=signer, app_version="1.0.0", platform="WINDOWS",
        kind="report", lang="ar", snapshot={"revenue": 1},
    )
    args.update(kw)
    return client.ai_shop_analysis(**args)


def test_report_body_shape_path_and_signature():
    session = _Session([_Resp(200, {"result": "ACCEPTED", "kind": "report"})])
    signer = _Signer()
    out = _call(_client(session), signer)
    assert out["result"] == "ACCEPTED"
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://owner.test/api/licensing/v1/ai/shop-analysis"
    body = call["json"]
    for key in ("contract_version", "request_id", "correlation_id", "timestamp", "nonce",
                "installation_id", "app_version", "platform", "kind", "lang", "snapshot"):
        assert key in body, key
    assert body["kind"] == "report" and body["lang"] == "ar"
    assert body["snapshot"] == {"revenue": 1}
    assert "question" not in body and "history" not in body
    assert body["signature"] == "sig-b64"
    # The signature covers everything except itself.
    unsigned = {k: v for k, v in body.items() if k != "signature"}
    assert signer.signed == canonicalize_bytes(unsigned)


def test_chat_body_carries_question_and_history():
    session = _Session([_Resp(200, {"result": "ACCEPTED", "kind": "chat", "reply": "x"})])
    hist = [{"role": "user", "content": "hi"}]
    _call(_client(session), _Signer(), kind="chat", question="why?", history=hist)
    body = session.calls[0]["json"]
    assert body["kind"] == "chat" and body["question"] == "why?" and body["history"] == hist


@pytest.mark.parametrize("status,code", [(429, "AI_BUDGET_EXHAUSTED"), (503, "AI_UPSTREAM_ERROR")])
def test_ai_refusal_returns_body_and_is_not_retried(status, code):
    session = _Session([_Resp(status, {"result": "FAILURE", "reason_code": code}), _Resp(200, {})])
    out = _call(_client(session), _Signer())
    assert out["reason_code"] == code
    assert len(session.calls) == 1, "an AI refusal must not re-send the signed request"


def test_other_methods_still_retry_a_503():
    # Allow half: the no-retry rule is specific to the AI call.
    session = _Session([_Resp(503, {}), _Resp(202, {"result": "QUEUED"})])
    out = _client(session).send_mail(
        installation_id="i", kind="k", params={}, signer=_Signer(), app_version="1", platform="WINDOWS")
    assert out["result"] == "QUEUED"
    assert len(session.calls) == 2


def test_other_methods_still_raise_on_exhausted_503():
    session = _Session([_Resp(503, {}), _Resp(503, {})])
    with pytest.raises(NetworkError):
        _client(session).send_mail(
            installation_id="i", kind="k", params={}, signer=_Signer(), app_version="1", platform="WINDOWS")
