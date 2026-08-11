"""SyncRelayClient -- transport to Owner's multi-device sync relay only
(`/api/sync/v1/push`, `/api/sync/v1/pull`). Deliberately mirrors
`commercial_runtime/licensing_contracts/client.py`'s `LicensingClient`
structure: same retry policy (`_RETRYABLE_STATUS_CODES`, exponential backoff
with jitter, injectable `sleep_fn` purely for tests), same timeout/TLS-verify
handling, same canonicalization helper (imported, never reimplemented -- the
signer and Owner's verifier must derive byte-identical canonical strings from
the same payload).

Wire contract note (2026-08-06, this task): the plan's own draft client code
predates Task 2's replay-protection pass on the relay routes. The ACTUAL
contract, read directly from `owner/app/sync/routes.py`:
  - every push/pull body carries `installation_id`, a fresh `timestamp`,
    a fresh unique `nonce`, and a `signature` computed over
    canonicalize_bytes(body minus "signature") -- the exact same shape
    `LicensingClient._post_signed` already uses for check-in.
  - pull is a GET request that ALSO carries a signed JSON body (Flask's
    `request.get_json()` does not care about HTTP method); `since` lives
    INSIDE that signed body, never as a `?since=` query parameter (a free
    query param would let a captured pull request be replayed with a
    different `since` and walk more history than the original request
    asked for -- see routes.py's module docstring).
  - on any auth/validation rejection the relay returns HTTP 400 (not 401)
    with `{"reason_code": "..."}` -- this codebase's convention, matching
    checkin.py's CheckInRejected. A 500 means a genuine server-side fault.
"""
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

import requests

from commercial_runtime.licensing_contracts.canonical import canonicalize_bytes

NONCE_LENGTH = 32  # matches licensing_contracts/client.py -- within Owner's replay.py 16-64 char range

# Retry policy for transport-level failures only -- never for a real,
# successfully-exchanged Owner rejection (bad signature, unknown
# installation, oversized batch, etc.), which is a business decision, not a
# transport failure to retry. Identical set to LicensingClient's.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class DeviceSigner(Protocol):
    def sign(self, canonical_bytes: bytes) -> str: ...


class SyncRelayClientError(Exception):
    """Base class. reason_code is either an Owner-issued reason_code (a real
    rejection) or one of this client's own transport-failure codes below."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class NetworkError(SyncRelayClientError):
    """Transport-level failure: could not complete the exchange at all
    (timeout, connection refused, TLS failure, or the retry budget was
    exhausted against a retryable HTTP status). Genuinely offline/unreachable
    -- the caller's outbox/cursor are left untouched."""


class MalformedResponseError(SyncRelayClientError):
    pass


class RelayRejected(SyncRelayClientError):
    """Owner reached, request understood, and Owner said no (bad signature,
    replayed nonce, unknown installation, oversized batch, ...). The
    reason_code is exactly what Owner returned -- never invented here."""


def _new_nonce() -> str:
    return secrets.token_urlsafe(NONCE_LENGTH)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SyncRelayClientConfig:
    base_url: str
    timeout_seconds: float = 10.0
    verify_tls: bool = True
    max_retries: int = 4
    retry_base_backoff_seconds: float = 1.0
    retry_max_backoff_seconds: float = 30.0


class SyncRelayClient:
    def __init__(
        self,
        base_url: str,
        signer: DeviceSigner,
        installation_id: str,
        timeout_seconds: float = 10.0,
        verify_tls: bool = True,
        *,
        max_retries: int = 4,
        retry_base_backoff_seconds: float = 1.0,
        retry_max_backoff_seconds: float = 30.0,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        self._config = SyncRelayClientConfig(
            base_url=base_url.rstrip("/"),
            timeout_seconds=timeout_seconds,
            verify_tls=verify_tls,
            max_retries=max_retries,
            retry_base_backoff_seconds=retry_base_backoff_seconds,
            retry_max_backoff_seconds=retry_max_backoff_seconds,
        )
        self._signer = signer
        self._installation_id = installation_id
        self._session = session or requests.Session()
        # Injectable purely so tests never sleep through a real
        # production-shaped backoff schedule -- production always uses the
        # real time.sleep default. Same rationale as LicensingClient.
        self._sleep = sleep_fn

    def push(self, events: list) -> dict:
        body = self._signed_body({"events": events})
        result = self._request("POST", "/api/sync/v1/push", body)
        self._raise_if_rejected(result)
        return result

    def pull(self, since: int) -> dict:
        body = self._signed_body({"since": since})
        # POST, not GET -- see owner/app/sync/routes.py's pull() docstring
        # (2026-08-12): a frozen PyInstaller build of this exact client
        # reproducibly got an empty response body on GET-with-body, isolated
        # via a non-frozen interpreter hitting the same route successfully.
        # The server accepts both; Android's own GET-with-body client is
        # unaffected and unchanged.
        result = self._request("POST", "/api/sync/v1/pull", body)
        self._raise_if_rejected(result)
        return result

    def _signed_body(self, extra: dict) -> dict:
        # A fresh nonce and current timestamp on EVERY call -- never reused,
        # never cached -- exactly like LicensingClient's _post_signed sites.
        body = {
            "installation_id": self._installation_id,
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            **extra,
        }
        canonical_bytes = canonicalize_bytes(body)
        return {**body, "signature": self._signer.sign(canonical_bytes)}

    @staticmethod
    def _raise_if_rejected(result: dict) -> None:
        if isinstance(result, dict) and "reason_code" in result:
            raise RelayRejected(result["reason_code"], f"Sync relay rejected the request: {result['reason_code']}")

    def _request(self, method: str, path: str, json_body: dict) -> dict:
        url = self._config.base_url + path
        last_error: Optional[Exception] = None

        for attempt in range(self._config.max_retries + 1):
            if attempt:
                delay = self._config.retry_base_backoff_seconds * (2 ** (attempt - 1))
                jitter = delay * 0.25 * secrets.randbelow(100) / 100.0
                self._sleep(min(delay + jitter, self._config.retry_max_backoff_seconds))
            headers = {"Content-Type": "application/json"}
            try:
                response = self._session.request(
                    method,
                    url,
                    json=json_body,
                    timeout=self._config.timeout_seconds,
                    verify=self._config.verify_tls,
                    headers=headers,
                )
            except requests.exceptions.Timeout as exc:
                last_error = NetworkError("REQUEST_TIMED_OUT", str(exc))
                continue
            except requests.exceptions.SSLError as exc:
                # Never retried -- a TLS failure is not transient in the
                # sense that matters here (see LicensingClient._request).
                raise NetworkError("TLS_VERIFICATION_FAILED", str(exc)) from exc
            except requests.exceptions.RequestException as exc:
                last_error = NetworkError("NETWORK_UNAVAILABLE", str(exc))
                continue

            if response.status_code in _RETRYABLE_STATUS_CODES:
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        self._sleep(min(float(retry_after), self._config.retry_max_backoff_seconds))
                    except ValueError:
                        pass
                last_error = NetworkError(
                    "SERVICE_TEMPORARILY_UNAVAILABLE" if response.status_code != 429 else "RATE_LIMITED",
                    f"HTTP {response.status_code}",
                )
                continue

            try:
                return response.json()
            except ValueError as exc:
                raise MalformedResponseError("MALFORMED_RESPONSE", f"Response was not valid JSON: {exc}") from exc

        raise last_error or NetworkError("NETWORK_UNAVAILABLE", "Request failed with no captured error.")
