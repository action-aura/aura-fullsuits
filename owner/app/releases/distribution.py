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

Scoping: authorize_download() needs the caller to already know which
product_version_id it wants. The "what's the latest release for my product/
platform/channel" question is answered by app/releases/discovery.py (update
channel wave), which reuses authenticate_device_request() below so both
endpoints authenticate identically. Documented in
private-distribution-contract.md.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from app.audit.services import record as audit_record
from app.commercial_ops.unpaid import subscription_is_unpaid
from app.extensions import db_session
from app.licensing_service import device_identity, replay
from app.licensing_service.canonical import canonicalize_bytes
from app.models.catalog import ProductVersion
from app.models.installations import Installation
from app.models.release_distribution import ReleaseDownloadAuthorization
from app.releases import storage
from app.security.tokens import generate_token, hash_token


# Licence statuses a device request is refused for, whatever else is true. The
# multi-device sync relay (sync/routes.py) reuses this list so the two cannot drift.
# EXPIRED is deliberately NOT here: it is refused separately below, and the sync
# relay handles a lapsed licence through the keep-selling "unpaid" rule instead.
LICENSE_REFUSED_STATUSES = ("SUSPENDED", "REVOKED")

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


def authenticate_device_request(
    body: dict, *, nonce_scope: str, config: dict, allow_unpaid: bool = False
) -> tuple[Installation, "License"]:  # noqa: F821
    """Proves a request really comes from a live, licensed installation.

    Extracted verbatim from authorize_download() so release DISCOVERY
    (app/releases/discovery.py) authenticates with the exact same checks, in the
    exact same order, producing the exact same reason codes -- a second copy of
    this block would drift the first time one of them gained a check. Order
    matters and is preserved: timestamp window, nonce (single use), installation
    exists, active device key, signature over the body, installation status,
    licence status, subscription status.

    `nonce_scope` keeps nonces of different endpoints in separate namespaces
    (replay.consume_nonce is keyed on scope+nonce), so a nonce spent on a
    discovery call cannot be used up -- or replayed -- against a download.

    `allow_unpaid` (keep-selling, owner decision 2026-10-24): when True an EXPIRED
    subscription is NOT refused here, because a shop that lapsed unpaid may still
    receive SAFETY fixes. The CALLER then owns the restriction (it must offer /
    authorize SAFETY releases only, via commercial_ops/unpaid.py). Default False
    keeps every other caller -- crash reports, the mail relay -- refusing an
    EXPIRED subscription exactly as before: they are online extras an unpaid shop
    does not get. SUSPENDED and CANCELLED are refused regardless.

    The caller must already have checked that installation_id, timestamp, nonce
    and signature are present (each endpoint has its own required-field list).
    Returns (installation, license_row); raises DownloadAuthorizationRejected.
    """
    try:
        installation_id = uuid.UUID(str(body["installation_id"]))
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
        replay.consume_nonce(body["nonce"], scope=nonce_scope, ttl_seconds=config["nonce_ttl_seconds"])
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

    # Step 0 fix round (2026-10-25): a refund's stored stop ("the licence
    # keeps working 2 days, then stops", licensing/scheduled_stops.py) is
    # applied HERE, before the licence status is read -- the same as check-in
    # and activation do. Without it a refunded shop past its 2 days could
    # still download updates until something else applied the stop.
    if installation.license_id is not None:
        from app.licensing.scheduled_stops import apply_due_licence_stops

        if apply_due_licence_stops(license_id=installation.license_id):
            db_session.expire_all()

    license_row = installation.license
    if license_row is None:
        raise DownloadAuthorizationRejected("INSTALLATION_NOT_FOUND")
    # Deny-list, matching process_checkin()'s exact pattern
    # (app/licensing_service/checkin.py) -- License's real operating status
    # after issuance is "ISSUED", not "ACTIVE" ("ACTIVE" is a Subscription
    # status, not a License one); checkin.py correctly rejects only the
    # known-bad statuses rather than requiring an exact good one.
    if license_row.status in LICENSE_REFUSED_STATUSES:
        raise DownloadAuthorizationRejected(f"LICENSE_{license_row.status}")
    if license_row.status == "EXPIRED":
        raise DownloadAuthorizationRejected("LICENSE_EXPIRED")

    subscription = license_row.subscription
    if subscription is not None and subscription.status in ("SUSPENDED", "EXPIRED", "CANCELLED"):
        if not (allow_unpaid and subscription.status == "EXPIRED"):
            raise DownloadAuthorizationRejected(f"SUBSCRIPTION_{subscription.status}")

    return installation, license_row


def authorize_download(body: dict, *, source_ip: str | None, config: dict) -> tuple[ReleaseDownloadAuthorization, str]:
    """Returns (authorization_row, raw_token). raw_token is never persisted
    (only its hash is, on the row) -- this is the one and only place it's
    ever available; the caller (route handler) must return it to the
    client immediately and not log it."""
    _validate_shape(body)

    # Both ids are parsed up front, exactly as before the extraction, so a
    # malformed product_version_id is still rejected BEFORE the nonce is
    # consumed (a bad request must not burn a nonce).
    try:
        uuid.UUID(str(body["installation_id"]))
        product_version_id = uuid.UUID(str(body["product_version_id"]))
    except (ValueError, TypeError):
        raise DownloadAuthorizationRejected("INVALID_REQUEST")

    installation, license_row = authenticate_device_request(
        body, nonce_scope="release_download", config=config, allow_unpaid=True
    )

    release = db_session.get(ProductVersion, product_version_id)
    if release is None:
        raise DownloadAuthorizationRejected("RELEASE_NOT_FOUND")
    if release.publication_state != "PUBLISHED":
        raise DownloadAuthorizationRejected("RELEASE_NOT_PUBLISHED")
    # Keep-selling: an unpaid shop may download SAFETY releases only. Checked on
    # the release's own kind (not on "is it the newest"), so a hand-crafted
    # request naming a FEATURE release id is refused too -- loosening the
    # subscription check above alone would have opened every feature update.
    if release.release_kind != "SAFETY" and subscription_is_unpaid(license_row.subscription, license_row=license_row):
        raise DownloadAuthorizationRejected("RELEASE_NOT_ALLOWED_UNPAID")
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
    if authorization is None or authorization.installation_id is None:
        # A website customer's link (customer_account_id) is not a device link: it is
        # redeemed only through the website API (open_customer_download).
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

    # Take the use in ONE conditional UPDATE, not `use_count += 1`: the
    # check above is a read, and the ORM writes `+= 1` as an absolute value,
    # so concurrent fetches of one token all passed it and all downloaded
    # (measured 8 of 8 on a max_uses=1 token,
    # tests/test_licensing_ratelimit_atomic.py). The WHERE is re-evaluated
    # under the row lock, so only max_uses fetches can ever get a row back.
    # Taken after the artifact is read and verified, so a storage failure
    # never burns the token.
    used = db_session.execute(
        update(ReleaseDownloadAuthorization)
        .where(
            ReleaseDownloadAuthorization.id == authorization.id,
            ReleaseDownloadAuthorization.use_count < ReleaseDownloadAuthorization.max_uses,
        )
        .values(use_count=ReleaseDownloadAuthorization.use_count + 1, used_at=now)
        .returning(ReleaseDownloadAuthorization.use_count)
        .execution_options(synchronize_session=False)
    ).first()
    if used is None:
        db_session.rollback()
        raise DownloadAuthorizationRejected("TOKEN_ALREADY_USED")
    authorization_id = authorization.id
    db_session.commit()

    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="RELEASE_DOWNLOAD_FETCHED",
        entity_type="release_download_authorization", entity_public_id=str(authorization_id),
        after_state={"product_version": release.version, "use_count": used.use_count},
    )
    return content, release


# --------------------------------------------------------------------------
# New customer flow (S4): the website's installer download for a signed-in customer.
# A link is made for an ACCOUNT (customer_account_id), before any device exists:
# 10 minutes, 3 uses (a phone reconnects; a download manager retries). It can only be
# redeemed through the website API, and a device link can only be redeemed through the
# device API (the table CHECK makes the two kinds exclusive).

@dataclass(frozen=True)
class CustomerDownload:
    chunks: object  # an iterator of bytes; closes the file when finished or dropped
    size_bytes: int
    filename: str
    content_type: str


def authorize_customer_download(account, offer, *, ttl_seconds: int, max_uses: int) -> tuple[ReleaseDownloadAuthorization, str]:
    """The caller has checked: session valid, password changed, offer currently
    offered to this customer. Returns (row, raw_token); the raw token is shown once."""
    raw_token = generate_token()
    row = ReleaseDownloadAuthorization(
        product_version_id=offer.release.id, license_id=None, installation_id=None, customer_account_id=account.id,
        token_hash=hash_token(raw_token), expires_at=datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
        max_uses=max_uses, use_count=0,
    )
    db_session.add(row)
    db_session.commit()
    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="WEBSITE_DOWNLOAD_AUTHORIZED",
        entity_type="release_download_authorization", entity_public_id=str(row.id),
        after_state={"customer_account_id": str(account.id), "product_version": offer.release.version, "platform": offer.platform},
    )
    return row, raw_token


def open_customer_download(raw_token: str) -> CustomerDownload:
    """Checks a website link, verifies the stored file, takes ONE use, and returns
    the byte stream. The use is counted HERE -- after every refusal could still
    happen (unknown / expired / used up / withdrawn / the file missing or not matching
    its published checksum) and before the first byte is sent -- so a fetch that fails
    for any of those reasons never burns a use, and a fetch whose stream starts always
    has. Raises DownloadAuthorizationRejected."""
    from app.models.customer_accounts import CustomerAccount
    from app.releases import customer_offers

    row = db_session.execute(
        select(ReleaseDownloadAuthorization).where(ReleaseDownloadAuthorization.token_hash == hash_token(raw_token or ""))
    ).scalars().first()
    if row is None or row.customer_account_id is None:
        raise DownloadAuthorizationRejected("TOKEN_NOT_FOUND")
    if row.revoked_at is not None:
        raise DownloadAuthorizationRejected("TOKEN_REVOKED")
    now = datetime.now(timezone.utc)
    if now > row.expires_at:
        raise DownloadAuthorizationRejected("TOKEN_EXPIRED")
    if row.use_count >= row.max_uses:
        raise DownloadAuthorizationRejected("TOKEN_ALREADY_USED")
    # The link is bound to the account: a disabled account, or one made to choose a new
    # password again, stops downloading at once, not when the link would have expired.
    account = db_session.get(CustomerAccount, row.customer_account_id)
    if account is None or account.status != "ACTIVE" or account.password_must_change:
        raise DownloadAuthorizationRejected("TOKEN_REVOKED")
    release = db_session.get(ProductVersion, row.product_version_id)
    if release is None or release.publication_state != "PUBLISHED":
        raise DownloadAuthorizationRejected("RELEASE_NOT_PUBLISHED")
    offer = customer_offers.find_offer(account.customer_id, str(release.id))
    if offer is None:
        # Still published, but this customer is no longer offered it (licence
        # suspended / cancelled since the link was made).
        raise DownloadAuthorizationRejected("TOKEN_REVOKED")
    try:
        actual, size = storage.artifact_sha256(release.artifact_path)
    except storage.ArtifactNotFound:
        raise DownloadAuthorizationRejected("ARTIFACT_UNAVAILABLE")
    if actual != release.artifact_checksum_sha256 or size != release.artifact_size_bytes:
        raise DownloadAuthorizationRejected("ARTIFACT_UNAVAILABLE")

    used = db_session.execute(
        update(ReleaseDownloadAuthorization)
        .where(ReleaseDownloadAuthorization.id == row.id, ReleaseDownloadAuthorization.use_count < ReleaseDownloadAuthorization.max_uses)
        .values(use_count=ReleaseDownloadAuthorization.use_count + 1, used_at=now)
        .returning(ReleaseDownloadAuthorization.use_count)
        .execution_options(synchronize_session=False)
    ).first()
    if used is None:
        db_session.rollback()
        raise DownloadAuthorizationRejected("TOKEN_ALREADY_USED")
    authorization_id = row.id
    version = release.version
    artifact_path = release.artifact_path
    db_session.commit()
    audit_record(
        actor_staff_user_id=None, actor_role_snapshot=None, action_code="WEBSITE_DOWNLOAD_STARTED",
        entity_type="release_download_authorization", entity_public_id=str(authorization_id),
        after_state={"product_version": version, "use_count": used.use_count},
    )
    return CustomerDownload(
        chunks=storage.iter_artifact(artifact_path), size_bytes=size, filename=offer.filename,
        content_type=customer_offers.CONTENT_TYPE[offer.platform],
    )
