"""Phase 9R M11 -- private authorized release distribution.

Required flow (governing instruction, M11):
  1. an authenticated installation requests a specific published release
     (device-signature-authenticated, same primitives as check-in --
     app/licensing_service/checkin.py -- not a new auth mechanism);
  2. Owner verifies Customer/Subscription/License/Product/Platform/
     entitlement/release-channel/publication-state;
  3. Owner creates a short-lived, single-use authorized download (this
     module);
  4. fetch_download() re-verifies expiry/use-count/scope at the moment of
     actual retrieval, not just at authorization time;
  5. the artifact is read from private local/test storage (never a public
     URL -- app/releases/storage.py) and served without ever exposing a
     storage credential to the client;
  6. every authorization and every fetch is audited.

Deliberate scoping decision: the caller must already know which
product_version_id it wants. A "what's the latest release for my product/
platform/channel" discovery endpoint is real, useful, future work but is a
distinct concern from authorization -- not built here, to keep this
milestone's surface reviewable. Documented in private-distribution-contract.md.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.audit.services import record as audit_record
from app.extensions import db_session
from app.licensing_service import device_identity, replay
from app.licensing_service.canonical import canonicalize_bytes
from app.models.catalog import ProductVersion
from app.models.installations import Installation
from app.models.release_distribution import ReleaseDownloadAuthorization
from app.releases import storage
from app.security.tokens import generate_token, hash_token


_NOT_FOUND_CODES = frozenset({"TOKEN_NOT_FOUND"})
_EXPIRED_OR_USED_CODES = frozenset({"TOKEN_EXPIRED", "TOKEN_ALREADY_USED", "TOKEN_REVOKED"})
# RELEASE_NOT_FOUND deliberately shares its HTTP status (400, the default
# below) with RELEASE_NOT_PUBLISHED, not a distinct 404 -- anti-enumeration
# must hold for the status code too, not just the JSON reason_code body;
# a real client can observe the status code even easier than the body.


class DownloadAuthorizationRejected(Exception):
    def __init__(self, internal_reason_code: str):
        self.internal_reason_code = internal_reason_code
        if internal_reason_code in _NOT_FOUND_CODES:
            self.http_status = 404
        elif internal_reason_code in _EXPIRED_OR_USED_CODES:
            self.http_status = 410  # Gone -- the token existed, it's just no longer usable
        else:
            self.http_status = 400
        super().__init__(internal_reason_code)


def _validate_shape(body: dict) -> None:
    required = ("installation_id", "product_version_id", "timestamp", "nonce", "signature", "request_id")
    if not all(k in body for k in required):
        raise DownloadAuthorizationRejected("INVALID_REQUEST")


def authorize_download(body: dict, *, source_ip: str | None, config: dict) -> tuple[ReleaseDownloadAuthorization, str]:
    """Returns (authorization_row, raw_token). raw_token is never persisted
    (only its hash is, on the row) -- this is the one and only place it's
    ever available; the caller (route handler) must return it to the
    client immediately and not log it."""
    _validate_shape(body)

    try:
        installation_id = uuid.UUID(str(body["installation_id"]))
        product_version_id = uuid.UUID(str(body["product_version_id"]))
    except (ValueError, TypeError):
        raise DownloadAuthorizationRejected("INVALID_REQUEST")

    try:
        request_timestamp = replay.parse_request_timestamp(body["timestamp"])
    except (ValueError, TypeError, AttributeError):
        raise DownloadAuthorizationRejected("INVALID_TIMESTAMP")
    try:
        replay.validate_timestamp(request_timestamp, config["timestamp_skew_seconds"])
    except replay.ReplayError as exc:
        raise DownloadAuthorizationRejected(str(exc))
    try:
        replay.consume_nonce(body["nonce"], scope="release_download", ttl_seconds=config["nonce_ttl_seconds"])
    except replay.ReplayError as exc:
        raise DownloadAuthorizationRejected(str(exc))

    installation = db_session.get(Installation, installation_id)
    if installation is None:
        raise DownloadAuthorizationRejected("INSTALLATION_NOT_FOUND")

    device_key = device_identity.get_active_device_key(installation.id)
    if device_key is None:
        raise DownloadAuthorizationRejected("DEVICE_KEY_REVOKED")
    public_key = device_identity.load_public_key_or_none(device_key.public_key)
    signable = {k: v for k, v in body.items() if k != "signature"}
    canonical_bytes = canonicalize_bytes(signable)
    if public_key is None or not device_identity.verify_signature(public_key, canonical_bytes, body["signature"]):
        raise DownloadAuthorizationRejected("INVALID_SIGNATURE")

    if installation.status in ("SUSPENDED", "DEACTIVATED", "REPLACED"):
        raise DownloadAuthorizationRejected(f"INSTALLATION_{installation.status}")

    license_row = installation.license
    if license_row is None:
        raise DownloadAuthorizationRejected("INSTALLATION_NOT_FOUND")
    # Deny-list, matching process_checkin()'s exact pattern
    # (app/licensing_service/checkin.py) -- License's real operating status
    # after issuance is "ISSUED", not "ACTIVE" ("ACTIVE" is a Subscription
    # status, not a License one); checkin.py correctly rejects only the
    # known-bad statuses rather than requiring an exact good one.
    if license_row.status == "SUSPENDED":
        raise DownloadAuthorizationRejected("LICENSE_SUSPENDED")
    if license_row.status == "REVOKED":
        raise DownloadAuthorizationRejected("LICENSE_REVOKED")
    if license_row.status == "EXPIRED":
        raise DownloadAuthorizationRejected("LICENSE_EXPIRED")

    subscription = license_row.subscription
    if subscription is not None and subscription.status in ("SUSPENDED", "EXPIRED", "CANCELLED"):
        raise DownloadAuthorizationRejected(f"SUBSCRIPTION_{subscription.status}")

    release = db_session.get(ProductVersion, product_version_id)
    if release is None:
        raise DownloadAuthorizationRejected("RELEASE_NOT_FOUND")
    if release.publication_state != "PUBLISHED":
        raise DownloadAuthorizationRejected("RELEASE_NOT_PUBLISHED")
    if release.product_id != license_row.product_id:
        raise DownloadAuthorizationRejected("PRODUCT_MISMATCH")
    if release.platform_id != installation.platform_id:
        raise DownloadAuthorizationRejected("PLATFORM_NOT_ALLOWED")
    if license_row.allowed_release_channel_id is not None and release.release_channel_id != license_row.allowed_release_channel_id:
        raise DownloadAuthorizationRejected("RELEASE_CHANNEL_NOT_ALLOWED")

    raw_token = generate_token()
    now = datetime.now(timezone.utc)
    authorization = ReleaseDownloadAuthorization(
        product_version_id=release.id, license_id=license_row.id, installation_id=installation.id,
        token_hash=hash_token(raw_token), expires_at=now + timedelta(seconds=config["download_token_ttl_seconds"]),
        max_uses=1, use_count=0,
    )
    db_session.add(authorization)
    db_session.commit()

    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="RELEASE_DOWNLOAD_AUTHORIZED",
        entity_type="release_download_authorization", entity_public_id=str(authorization.id),
        after_state={
            "installation_id": str(installation.id), "license_id": str(license_row.id),
            "product_version": release.version, "expires_at": authorization.expires_at.isoformat(),
        },
    )
    return authorization, raw_token


def fetch_download(raw_token: str) -> tuple[bytes, ProductVersion]:
    token_hash = hash_token(raw_token)
    authorization = db_session.execute(
        select(ReleaseDownloadAuthorization).where(ReleaseDownloadAuthorization.token_hash == token_hash)
    ).scalars().first()
    if authorization is None:
        raise DownloadAuthorizationRejected("TOKEN_NOT_FOUND")
    if authorization.revoked_at is not None:
        raise DownloadAuthorizationRejected("TOKEN_REVOKED")
    now = datetime.now(timezone.utc)
    if now > authorization.expires_at:
        raise DownloadAuthorizationRejected("TOKEN_EXPIRED")
    if authorization.use_count >= authorization.max_uses:
        raise DownloadAuthorizationRejected("TOKEN_ALREADY_USED")

    release = db_session.get(ProductVersion, authorization.product_version_id)
    if release is None or release.publication_state != "PUBLISHED":
        # Re-checked at fetch time, not just at authorization time -- a
        # release withdrawn between authorization and fetch (e.g. a
        # just-discovered vulnerability) must stop being servable
        # immediately, even for an already-issued token.
        raise DownloadAuthorizationRejected("RELEASE_NOT_PUBLISHED")

    try:
        content = storage.read_artifact_bytes(release.artifact_path)
    except storage.ArtifactNotFound:
        raise DownloadAuthorizationRejected("ARTIFACT_UNAVAILABLE")
    actual_checksum = hashlib.sha256(content).hexdigest()
    if release.artifact_checksum_sha256 and actual_checksum != release.artifact_checksum_sha256:
        # Deliberately the same public code as a missing artifact -- a
        # checksum mismatch could mean tampering; the specific reason is
        # audited (below, via the exception's context in the caller) but
        # not handed to the client as a distinct signal.
        raise DownloadAuthorizationRejected("ARTIFACT_UNAVAILABLE")

    authorization.use_count += 1
    authorization.used_at = now
    db_session.commit()

    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="RELEASE_DOWNLOAD_FETCHED",
        entity_type="release_download_authorization", entity_public_id=str(authorization.id),
        after_state={"product_version": release.version, "use_count": authorization.use_count},
    )
    return content, release
