"""LicensingClient (Part C/I) -- transport to Owner only. Has no authority to
change local license state; every response it returns is raw and unverified
until AssertionVerifier has processed it. Implements the Phase 6 protocol
exactly (docs/owner/phase6/activation-protocol-v1.md) -- see
docs/licensing/phase7/product-integration-architecture.md for why this is
duplicated per-platform rather than shared.
"""
from __future__ import annotations

import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol
from urllib.parse import quote

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


class LicenseKey:
    """The full licence key a person typed. A credential object rather than a
    bare string so every code path that handles one has the same
    `body_fields()` / `clear()` contract (new customer flow, 2026-10-31)."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def body_fields(self) -> dict:
        return {"license_key": self._value}

    def clear(self) -> None:
        self._value = None

    def __repr__(self) -> str:  # never let a key reach a log through repr()
        return "LicenseKey(<redacted>)"


class AccountTicket:
    """A single-use Owner ticket proving 'this person signed in with their
    Aura account on THIS device', plus the licence they picked."""

    __slots__ = ("_ticket", "_license_public_id")

    def __init__(self, ticket: str, license_public_id: str):
        self._ticket = ticket
        self._license_public_id = license_public_id

    def body_fields(self) -> dict:
        return {"account_ticket": self._ticket, "license_public_id": self._license_public_id}

    def clear(self) -> None:
        self._ticket = None
        self._license_public_id = None

    def __repr__(self) -> str:
        return "AccountTicket(<redacted>)"


class ActivationCode:
    """A one-time activation code a staff member made for a customer."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def body_fields(self) -> dict:
        return {"activation_code": self._value}

    def clear(self) -> None:
        self._value = None

    def __repr__(self) -> str:
        return "ActivationCode(<redacted>)"


def _new_nonce() -> str:
    return secrets.token_urlsafe(NONCE_LENGTH)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remove_quietly(path) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        # Never let cleanup mask the real failure that got us here.
        pass


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
        idempotency_key: str,
        signer: DeviceSigner,
        license_key: Optional[str] = None,
        registry_device_id: Optional[str] = None,
        credential=None,
    ) -> dict:
        """Exactly ONE of `license_key` (a plain string, the original form) or
        `credential` (LicenseKey | AccountTicket | ActivationCode) is given.
        Owner accepts exactly one of `license_key` | `account_ticket` +
        `license_public_id` | `activation_code` in the body, so sending two is
        refused here before it can leave the device."""
        if (license_key is None) == (credential is None):
            raise ValueError("activate() needs exactly one of license_key or credential")
        if credential is None:
            credential = LicenseKey(license_key)
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
            **credential.body_fields(),
            "idempotency_key": idempotency_key,
        }
        # AUDIT-033 fix round (verifier V finding #1): ask Owner to pin and
        # attest this computer's shop-authority registry device id. Added to
        # the body BEFORE _post_signed so the device signature covers it (an
        # on-path attacker cannot swap it). Only when we have one: an old
        # Owner, or a build with no registry identity yet, sends exactly the
        # body it always sent.
        if registry_device_id:
            body["registry_device_id"] = registry_device_id
        try:
            return self._post_signed("/activations", body, signer)
        finally:
            # The full license key must never linger in a local variable
            # longer than this call needs it (Part G) -- del is defense in
            # depth on top of the caller's own responsibility to discard it.
            for secret_field in ("license_key", "account_ticket", "activation_code"):
                if secret_field in body:
                    body[secret_field] = None
            credential.clear()
            del body

    def check_in(
        self, *, installation_id: str, signer: DeviceSigner, registry_device_id: Optional[str] = None
    ) -> dict:
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "installation_id": installation_id,
        }
        # See activate(): signed, and only when known. A check-in is also how
        # an installation that existed before this release gets pinned.
        if registry_device_id:
            body["registry_device_id"] = registry_device_id
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

    # -- new customer flow (2026-10-31): sign in with an Aura account ---------
    #
    # All three are device-signed like every other call here, and all three go
    # through _post_signed_once: NEVER retried. A retry would re-send a
    # password (or burn a ticket) the person did not ask to re-send, and the
    # lockout counters on Owner's side count attempts, so a client that
    # silently re-tried would lock people out of their own account. A transport
    # failure goes back to the person, who presses the button again.
    #
    # Each returns Owner's JSON body plus `http_status`; like every method here
    # it does not raise on a business rejection, the CALLER decides from the
    # body (a `ticket` / `reason_code`).

    def account_sign_in(
        self,
        *,
        email: str,
        password: str,
        product_code: str,
        platform: str,
        app_version: str,
        device_public_key_b64: str,
        signer: DeviceSigner,
        installation_id: Optional[str] = None,
    ) -> dict:
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "email": email,
            "password": password,
            "product_code": product_code,
            "platform": platform,
            "app_version": app_version,
            "device_public_key": device_public_key_b64,
            "device_public_key_algorithm": "ed25519",
        }
        if installation_id:
            body["installation_id"] = installation_id
        try:
            return self._post_signed_once("/account-sign-ins", body, signer)
        finally:
            # The password lived in this dict for the length of one call and
            # must not outlive it (same rule as the licence key in activate()).
            body["password"] = None
            del body

    def change_password(
        self,
        *,
        ticket: str,
        new_password: str,
        product_code: str,
        platform: str,
        app_version: str,
        device_public_key_b64: str,
        signer: DeviceSigner,
    ) -> dict:
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "ticket": ticket,
            "new_password": new_password,
            "product_code": product_code,
            "platform": platform,
            "app_version": app_version,
            "device_public_key": device_public_key_b64,
            "device_public_key_algorithm": "ed25519",
        }
        try:
            return self._post_signed_once("/account-password-changes", body, signer)
        finally:
            body["new_password"] = None
            body["ticket"] = None
            del body

    def accept_contract(
        self,
        *,
        ticket: str,
        contract_version_id: str,
        text_sha256: str,
        language: str,
        product_code: str,
        platform: str,
        app_version: str,
        device_public_key_b64: str,
        signer: DeviceSigner,
        installation_id: Optional[str] = None,
    ) -> dict:
        """Records that THIS device's person accepted THIS exact text. The
        `text_sha256` is the hash of the text the screen showed (the client has
        already checked it against the body Owner sent), and the whole body is
        covered by the device signature, so the acceptance is evidence of the
        text, the person's account (via the ticket) and the device."""
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "ticket": ticket,
            "contract_version_id": contract_version_id,
            "text_sha256": text_sha256,
            "language": language,
            "product_code": product_code,
            "platform": platform,
            "app_version": app_version,
            "device_public_key": device_public_key_b64,
            "device_public_key_algorithm": "ed25519",
        }
        if installation_id:
            body["installation_id"] = installation_id
        try:
            return self._post_signed_once("/contract-acceptances", body, signer)
        finally:
            body["ticket"] = None
            del body

    def find_latest_release(
        self,
        *,
        installation_id: str,
        app_version: str,
        release_channel: Optional[str],
        signer: DeviceSigner,
        request_id: Optional[str] = None,
    ) -> dict:
        """Update channel: "what is the newest release I may install?".

        Device-signed like a check-in. `request_id` is optional ONLY so the
        caller can remember it: Owner echoes it inside the signed release
        descriptor, and the updates service refuses a descriptor whose echo
        does not match (a captured old answer cannot be replayed as a new one).
        """
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id or str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "installation_id": installation_id,
            "app_version": app_version,
            "release_channel": release_channel,
        }
        return self._post_signed("/releases/latest", body, signer)

    def send_crash_reports(
        self,
        *,
        installation_id: str,
        reports: list,
        signer: DeviceSigner,
        app_version: str,
        platform: str,
        request_id: Optional[str] = None,
    ) -> dict:
        """Crash reports: "here are the bugs this till hit". Device-signed like
        a check-in, so Owner attributes them to the installation it already
        knows and nobody else can file reports in its name.

        `reports` must already be privacy-scrubbed (commercial_runtime/
        crash_reports/scrub.py); this method only transports them. Returns
        Owner's JSON body as-is: like every method here it does not raise on a
        business rejection, so the CALLER decides success from
        `result == "ACCEPTED"`, not from the absence of an exception.
        """
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id or str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "installation_id": installation_id,
            "app_version": app_version,
            "platform": platform,
            "reports": reports,
        }
        return self._post_signed("/crash-reports", body, signer)

    def send_mail(
        self,
        *,
        installation_id: str,
        kind: str,
        params: dict,
        signer: DeviceSigner,
        app_version: str,
        platform: str,
        lang: str = "en",
        request_id: Optional[str] = None,
    ) -> dict:
        """Mail relay: "please send this templated message for me". Device-signed
        like a check-in, so Owner attributes it (and its per-installation caps)
        to the installation it already knows.

        The body names a TEMPLATE `kind` plus `params`; Owner renders the
        subject and the text itself, and refuses a request that carries its
        own. There is deliberately no field here for a subject, a body, a
        sender or a mailbox password: this client has none to send, and Owner
        holds the only credentials. Returns Owner's JSON body as-is (202
        `result == "QUEUED"` on success); like every method here it does not
        raise on a business rejection, so the CALLER decides success from
        `result`.
        """
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id or str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "installation_id": installation_id,
            "app_version": app_version,
            "platform": platform,
            "kind": kind,
            "lang": lang,
            "params": params,
        }
        # No shop_name: Owner's relay refuses it (free text in a mail from OUR
        # address was a phishing vector; found by adversarial verification
        # 2026-10-10), so sending one would make the whole request a 400.
        return self._post_signed("/mail", body, signer)

    def authorize_release_download(
        self,
        *,
        installation_id: str,
        product_version_id: str,
        signer: DeviceSigner,
        request_id: Optional[str] = None,
    ) -> dict:
        """Asks Owner for a short-lived, SINGLE-USE download token."""
        body = {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id or str(uuid.uuid4()),
            "correlation_id": str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "nonce": _new_nonce(),
            "installation_id": installation_id,
            "product_version_id": product_version_id,
        }
        return self._post_signed("/releases/authorize-download", body, signer)

    def download_release(
        self,
        token: str,
        dest_path,
        *,
        expected_size: int,
        chunk: int = 1024 * 1024,
        progress: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Streams the installer to `dest_path`.

        NO RETRY, deliberately, unlike every other call on this client: the
        token is single-use, so the moment Owner has started serving it a
        second attempt can only fail. A failed download goes back to the
        caller, who asks for a fresh token.

        `expected_size` is a ceiling, not a hint: a server that sends more than
        the signed descriptor promised is misbehaving (or being impersonated)
        and must not be allowed to fill the disk. The partial file is deleted
        on EVERY failure path, so a half-written installer is never left
        lying around looking like a candidate.
        """
        url = self._config.base_url.rstrip("/") + "/releases/download/" + quote(str(token), safe="")
        try:
            response = self._session.request(
                "GET",
                url,
                stream=True,
                timeout=(self._config.timeout_seconds, max(self._config.timeout_seconds, 60.0)),
                verify=self._config.verify_tls,
            )
        except requests.exceptions.SSLError as exc:
            raise NetworkError("TLS_VERIFICATION_FAILED", str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise NetworkError("NETWORK_UNAVAILABLE", str(exc)) from exc

        received = 0
        try:
            if response.status_code != 200:
                raise LicensingClientError("DOWNLOAD_REFUSED", f"HTTP {response.status_code}")
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > expected_size:
                raise LicensingClientError(
                    "DOWNLOAD_TOO_LARGE", f"server announced {declared} bytes, ceiling is {expected_size}"
                )
            with open(dest_path, "wb") as out:
                for block in response.iter_content(chunk_size=chunk):
                    if not block:
                        continue
                    received += len(block)
                    if received > expected_size:
                        raise LicensingClientError(
                            "DOWNLOAD_TOO_LARGE", f"more than {expected_size} bytes arrived"
                        )
                    out.write(block)
                    if progress is not None:
                        progress(received)
        except requests.exceptions.RequestException as exc:
            _remove_quietly(dest_path)
            raise NetworkError("NETWORK_UNAVAILABLE", str(exc)) from exc
        except BaseException:
            _remove_quietly(dest_path)
            raise
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()

    def fetch_signing_keys(self) -> dict:
        return self._get("/signing-keys")

    def fetch_service_info(self) -> dict:
        return self._get("/service-info")

    def _post_signed(self, path: str, body: dict, signer: DeviceSigner) -> dict:
        canonical_bytes = canonicalize_bytes(body)
        body_with_signature = {**body, "signature": signer.sign(canonical_bytes)}
        return self._request("POST", path, json_body=body_with_signature)

    def _post_signed_once(self, path: str, body: dict, signer: DeviceSigner) -> dict:
        """One attempt, no retry, and the HTTP status travels with the answer.

        Account calls need both: a 401/429 carries the business answer (wrong
        password, locked out) that the retrying `_request` would either retry
        (429) or flatten, and a 201/204 may carry no body at all."""
        canonical_bytes = canonicalize_bytes(body)
        payload = {**body, "signature": signer.sign(canonical_bytes)}
        canonical_bytes = None  # noqa: F841 -- the bytes hold the password; drop the reference
        url = self._config.base_url.rstrip("/") + path
        try:
            response = self._session.request(
                "POST",
                url,
                json=payload,
                timeout=self._config.timeout_seconds,
                verify=self._config.verify_tls,
                headers={"Content-Type": "application/json"},
            )
        except requests.exceptions.Timeout as exc:
            raise NetworkError("REQUEST_TIMED_OUT", str(exc)) from exc
        except requests.exceptions.SSLError as exc:
            raise NetworkError("TLS_VERIFICATION_FAILED", str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise NetworkError("NETWORK_UNAVAILABLE", str(exc)) from exc

        status = response.status_code
        if status >= 500:
            raise NetworkError("SERVICE_TEMPORARILY_UNAVAILABLE", f"HTTP {status}")
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            if 200 <= status < 300:
                parsed = {}
            elif status == 429:
                parsed = {"reason_code": "RATE_LIMITED"}
            else:
                raise MalformedResponseError("MALFORMED_RESPONSE", f"HTTP {status} without a JSON object body")
        answer = dict(parsed)
        answer["http_status"] = status
        if status == 429:
            answer.setdefault("reason_code", "RATE_LIMITED")
            retry_after = (response.headers or {}).get("Retry-After")
            if retry_after and str(retry_after).isdigit():
                answer["retry_after_seconds"] = int(retry_after)
        return answer

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
