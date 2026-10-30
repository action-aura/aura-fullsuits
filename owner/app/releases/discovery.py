"""Update-channel wave -- release DISCOVERY ("is there a newer build for me?").

private distribution (app/releases/distribution.py) already lets a device
download a release it names. What was missing is the question that comes
first: which release should this install be offered? This module answers it
for a device-signed request and returns a descriptor SIGNED BY OWNER, so the
client can prove the offer (version, checksum, size) came from Owner and was
not edited by whatever sits between the app and the internet. The client later
checks the downloaded file against THIS signed checksum, not against anything
the download response says.

Authentication is not reimplemented: authenticate_device_request() is the same
function authorize_download() uses (own nonce scope), so the two endpoints
cannot drift apart on timestamp / replay / signature / licence rules.

Selection is by real semantic-version precedence (app/releases/versioning.py),
never by string order -- "1.0.0-rc.10" must beat "1.0.0-rc.9", and "1.0.0" must
beat "1.0.0-rc.99".

Deliberately NOT audit-logged per call: clients poll this once a day per
install, and an audit row per poll would drown the real events. One log line
per call instead (installation, current version, offered version or none), so a
"why did nobody get the update" question is answerable from the logs.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.commercial_ops.unpaid import subscription_is_unpaid
from app.extensions import db_session
from app.licensing_service.canonical import canonicalize_bytes
from app.licensing_service.signing import SigningKeyError, get_active_signing_key, load_private_key
from app.models.catalog import ProductVersion, ReleaseChannel
from app.releases import versioning
from app.releases.distribution import DownloadAuthorizationRejected, authenticate_device_request

logger = logging.getLogger(__name__)

DESCRIPTOR_VERSION = 1
NONCE_SCOPE = "release_latest"
STABLE_CHANNEL_CODE = "STABLE"
_REQUIRED_FIELDS = ("installation_id", "timestamp", "nonce", "signature", "request_id", "app_version")


def _validate_shape(body: dict) -> str:
    """Returns the validated app_version. An unparseable app_version is
    INVALID_REQUEST: comparing against garbage would either offer everything or
    nothing, and both are silent wrong answers."""
    if not isinstance(body, dict) or not all(k in body for k in _REQUIRED_FIELDS):
        raise DownloadAuthorizationRejected("INVALID_REQUEST")
    app_version = body["app_version"]
    if not isinstance(app_version, str) or not versioning.is_valid_semver(app_version):
        raise DownloadAuthorizationRejected("INVALID_REQUEST")
    channel = body.get("release_channel")
    if channel is not None and not isinstance(channel, str):
        raise DownloadAuthorizationRejected("INVALID_REQUEST")
    return app_version


def _allowed_channel_ids(license_row, requested_channel: str | None) -> list:
    """Which release channels this install may be offered.

    A licence pinned to one channel gets ONLY that channel -- the pin is a
    commercial promise (e.g. a pilot customer stays on pilot builds), so a
    client asking for "rc" must not widen it. An unpinned licence always gets
    STABLE, plus the channel the client says it follows when that channel
    exists (an rc tester sees rc builds and stable ones; a stable client never
    sees rc)."""
    if license_row.allowed_release_channel_id is not None:
        return [license_row.allowed_release_channel_id]
    wanted = {STABLE_CHANNEL_CODE}
    if requested_channel:
        wanted.add(requested_channel.upper())
    rows = db_session.execute(select(ReleaseChannel).where(ReleaseChannel.channel_code.in_(wanted))).scalars().all()
    return [r.id for r in rows]


def _pick_latest(candidates, current_key):
    """Highest semver wins; equal semver -> higher build_number. Rows whose
    stored version is not valid semver are skipped with a warning -- one bad
    row must never take discovery down for every install."""
    best = None
    best_sort = None
    for row in candidates:
        try:
            key = versioning.parse_semver(row.version)
        except ValueError:
            logger.warning("release discovery: skipping product_version %s with non-semver version %r", row.id, row.version)
            continue
        sort_key = (key, row.build_number if row.build_number is not None else -1)
        if best_sort is None or sort_key > best_sort:
            best, best_sort = row, sort_key
    if best is None or best_sort[0] <= current_key:
        return None
    return best


def _version_below(current: str, threshold: str | None, *, what: str, release_id) -> bool:
    if not threshold:
        return False
    try:
        return versioning.compare_semver(current, threshold) < 0
    except ValueError:
        logger.warning("release discovery: product_version %s has non-semver %s %r; ignoring it", release_id, what, threshold)
        return False


def _release_block(row: ProductVersion) -> dict:
    return {
        "product_version_id": str(row.id),
        "version": row.version,
        "build_number": row.build_number,
        "release_channel": row.release_channel.channel_code,
        "artifact_checksum_sha256": row.artifact_checksum_sha256,
        "artifact_size_bytes": row.artifact_size_bytes,
        "release_notes": row.release_notes,
        "published_at": row.published_at.isoformat() if row.published_at else None,
    }


def sign_payload(payload: dict, key_directory: str) -> dict:
    """Signs an arbitrary payload with the active Owner key, in the same
    envelope family as sign_assertion() (assertions.py) but with the
    descriptor's own, smaller shape. Kept here rather than changing
    sign_assertion(), whose envelope has a fixed assertion_version field that
    existing clients validate."""
    active_key = get_active_signing_key()
    if active_key is None:
        raise SigningKeyError("SIGNING_KEY_UNAVAILABLE")
    private_key = load_private_key(key_directory, active_key.key_id)
    signature = private_key.sign(canonicalize_bytes(payload))
    return {
        "payload": payload,
        "signing_key_id": active_key.key_id,
        "algorithm": active_key.algorithm,
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def find_latest_release(body: dict, *, config: dict) -> dict:
    """The full 200 response body. Raises DownloadAuthorizationRejected for any
    auth/shape failure and SigningKeyError if Owner cannot sign."""
    app_version = _validate_shape(body)
    installation, license_row = authenticate_device_request(
        body, nonce_scope=NONCE_SCOPE, config=config, allow_unpaid=True
    )
    unpaid = subscription_is_unpaid(license_row.subscription, license_row=license_row)

    current_key = versioning.parse_semver(app_version)
    channel_ids = _allowed_channel_ids(license_row, body.get("release_channel"))
    candidates = []
    if channel_ids:
        candidates = db_session.execute(
            select(ProductVersion).where(
                ProductVersion.product_id == license_row.product_id,
                ProductVersion.platform_id == installation.platform_id,
                ProductVersion.publication_state == "PUBLISHED",
                ProductVersion.artifact_checksum_sha256.is_not(None),
                ProductVersion.artifact_size_bytes.is_not(None),
                ProductVersion.release_channel_id.in_(channel_ids),
            )
        ).scalars().all()

    if unpaid:
        # Keep-selling: filter to SAFETY BEFORE picking the highest version. Picking
        # the newest first and then dropping it if it is a FEATURE release would
        # hide an older SAFETY fix behind a newer feature build -- the unpaid shop
        # would be offered nothing at all.
        candidates = [row for row in candidates if row.release_kind == "SAFETY"]
    offered = _pick_latest(candidates, current_key)
    if offered is None:
        update_required = below_min = False
        release_block = None
    else:
        update_required = _version_below(app_version, offered.forced_upgrade_threshold, what="forced_upgrade_threshold", release_id=offered.id)
        below_min = _version_below(app_version, offered.min_supported_version, what="min_supported_version", release_id=offered.id)
        release_block = _release_block(offered)

    payload = {
        "descriptor_version": DESCRIPTOR_VERSION,
        "installation_id": str(body["installation_id"]),
        "request_id": str(body["request_id"]),
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "current_version": app_version,
        "update_available": offered is not None,
        "update_required": update_required,
        "below_min_supported": below_min,
        "release": release_block,
    }
    envelope = sign_payload(payload, config["signing_key_directory"])
    logger.info(
        "release discovery: installation=%s current=%s offered=%s",
        installation.id, app_version, offered.version if offered is not None else "none",
    )
    return {"contract_version": "v1", "release_descriptor": envelope}
