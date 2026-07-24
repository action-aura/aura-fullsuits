"""LicensingClient (Part C/I) -- transport to Owner only. Has no authority to
change local license state; every response it returns is raw and unverified
until AssertionVerifier has processed it. Implements the Phase 6 protocol
exactly (docs/owner/phase6/activation-protocol-v1.md) -- see
docs/licensing/phase7/product-integration-architecture.md for why this is
duplicated per-platform rather than shared.
"""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol

import requests

from .canonical import canonicalize_bytes

CONTRACT_VERSION = "v1"
NONCE_LENGTH = 32  # within Owner's 16-64 char allowed range (replay.py)

# Retry policy for transport-level failures only -- never for a legitimate
# Owner-issued rejection (invalid license, device limit, etc.), which is a
# successful HTTP exchange carrying a business decision, not a transport
# failure to retry.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class DeviceSigner(Protocol):
    def sign(self, canonical_bytes: bytes) -> str: ...
    def get_public_key_b64(self) -> str: ...


class LicensingClientError(Exception):
    """Base class. reason_code is one of reason_codes.py's LOCAL_REASON_CODES."""

    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


class NetworkError(LicensingClientError):
    pass


class MalformedResponseError(LicensingClientError):
    pass


def _new_nonce() -> str:
    return secrets.token_urlsafe(NONCE_LENGTH)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LicensingClientConfig:
    base_url: str  # e.g. "https://licensing.example.internal/api/licensing/v1"
    timeout_seconds: float = 10.0
    verify_tls: bool = True  # must stay True outside explicit local-dev builds (Part H)
    max_retries: int = 4
    retry_base_backoff_seconds: float = 1.0
    retry_max_backoff_seconds: float = 30.0


class LicensingClient:
    def __init__(
        self,
        config: LicensingClientConfig,
        session: Optional[requests.Session] = None,
        sleep_fn=time.sleep,
    ):
        self._config = config
        self._session = session or requests.Session()
        # Injectable purely so tests never have to sleep through a real
        # production-shaped backoff schedule (a real retry test previously
        # took 36 seconds for 13 tests before this was added) -- production
        # code always uses the real time.sleep default.
        self._sleep = sleep_fn

    def activate(
        self,
        *,
        product_code: str,
        platform: str,
        app_version: str,
        release_channel: Optional[str],
        installation_id: str,
        device_public_key_b64: str,
        license_key: str,
        idempotency_key: str,
        signer: DeviceSigner,
    ) -> dict:
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "product_code": product_code,
            "platform": platform,
            "app_version": app_version,
            "release_channel": release_channel,
            "installation_id": installation_id,
            "device_public_key": device_public_key_b64,
            "device_public_key_algorithm": "ed25519",
            "license_key": license_key,
            "idempotency_key": idempotency_key,
        }
        try:
            return self._post_signed("/activations", body, signer)
        finally:
            # The full license key must never linger in a local variable
            # longer than this call needs it (Part G) -- del is defense in
            # depth on top of the caller's own responsibility to discard it.
            body["license_key"] = None
            del body

    def check_in(self, *, installation_id: str, signer: DeviceSigner) -> dict:
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "installation_id": installation_id,
        }
        return self._post_signed("/check-ins", body, signer)

    def deactivate(self, *, installation_id: str, idempotency_key: str, signer: DeviceSigner) -> dict:
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "installation_id": installation_id,
            "idempotency_key": idempotency_key,
        }
        return self._post_signed("/deactivations", body, signer)

    def fetch_signing_keys(self) -> dict:
        return self._get("/signing-keys")

    def fetch_service_info(self) -> dict:
        return self._get("/service-info")

    def _post_signed(self, path: str, body: dict, signer: DeviceSigner) -> dict:
        canonical_bytes = canonicalize_bytes(body)
        body_with_signature = {**body, "signature": signer.sign(canonical_bytes)}
        return self._request("POST", path, json_body=body_with_signature)

    def _get(self, path: str) -> dict:
        return self._request("GET", path, json_body=None)

    def _request(self, method: str, path: str, json_body: Optional[dict]) -> dict:
        url = self._config.base_url.rstrip("/") + path
        last_error: Optional[Exception] = None

        for attempt in range(self._config.max_retries + 1):
            if attempt:
                delay = self._config.retry_base_backoff_seconds * (2 ** (attempt - 1))
                jitter = delay * 0.25 * secrets.randbelow(100) / 100.0
                self._sleep(min(delay + jitter, self._config.retry_max_backoff_seconds))
            try:
                response = self._session.request(
                    method,
                    url,
                    json=json_body,
                    timeout=self._config.timeout_seconds,
                    verify=self._config.verify_tls,
                    headers={"Content-Type": "application/json"} if json_body is not None else {},
                )
            except requests.exceptions.Timeout as exc:
                last_error = NetworkError("REQUEST_TIMED_OUT", str(exc))
                continue
            except requests.exceptions.SSLError as exc:
                # Never retried -- a TLS failure is not transient in the
                # sense that matters here, and retrying could mask a real
                # MITM attempt as "just try again."
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
