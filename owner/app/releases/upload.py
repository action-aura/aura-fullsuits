"""Staff "Publish a new version" -- uploading a release artifact from the Owner UI.

Before this, a new app version reached shops only through `flask
import-release-manifest` on the server plus copying the file into
OWNER_RELEASE_ARTIFACT_DIR by hand. This module is the same step done from the
Releases page: it takes the uploaded file, proves what it is, stores it under a
deterministic name and creates the DRAFT row through the SAME function the CLI
uses (app/catalog/services.py::import_release_manifest) -- the row-creation
logic is not copied here. Publishing stays a separate, later human act
(app/releases/authority.py), exactly as for imported releases.

Why the checks look like this:
  * The file type is decided by its first bytes (MZ for a Windows .exe,
    PK\\x03\\x04 for an Android .apk), not only by the file name -- a name is
    whatever the uploader typed, and this file is what every shop will run.
  * sha256 and size are computed WHILE copying, from the exact bytes that land on
    disk, so the recorded checksum is the checksum of the stored file (the client
    later verifies its download against the Owner-signed copy of this value).
  * Nothing is left behind on refusal: validation that needs only form fields
    runs before a byte is written, and the temp file is removed on every path.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass

from flask import request
from flask_babel import gettext as _
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit.services import record as audit_record
from app.catalog.services import import_release_manifest
from app.extensions import db_session
from app.models.catalog import Platform, Product, ProductVersion, ReleaseChannel
from app.releases import storage, versioning

logger = logging.getLogger(__name__)

# The ONE route allowed to receive a large body. Everything else keeps the
# global MAX_CONTENT_LENGTH (12 MB, app/config.py). The Caddy edge limit has a
# matching exception for this path (deploy/*/Caddyfile).
UPLOAD_ENDPOINT = "releases.upload_submit"
MAX_UPLOAD_BYTES = 250 * 1024 * 1024
DEFAULT_CHANNEL_CODE = "RC"  # what the shipped clients ask for (client.find_latest_release)
RELEASE_KINDS = ("FEATURE", "SAFETY")
MAX_NOTES_CHARS = 4000
_CHUNK = 1024 * 1024
_MAX_BUILD_NUMBER = 2_147_483_647  # the column is a 32-bit integer

# platform_code -> (file extension, magic bytes). Only platforms we can verify
# a file for are offered; adding one means adding its magic here.
PLATFORM_RULES = {
    "WINDOWS": (".exe", b"MZ"),
    "ANDROID": (".apk", b"PK\x03\x04"),
}


class UploadRejected(Exception):
    """A refusal with a plain, already-translated message for the staff member."""


def apply_request_size_limit() -> None:
    """before_request hook: raise the body ceiling for the upload POST only.

    Flask 3.1 lets a request carry its own `max_content_length`. It must be set
    BEFORE anything reads the body, and CSRF protection (a before_request that
    reads request.form) is that first reader -- so this hook is registered ahead
    of csrf.init_app() in create_app(). Scoped by endpoint name, so no other
    route's limit changes.
    """
    if request.method == "POST" and request.endpoint == UPLOAD_ENDPOINT:
        request.max_content_length = MAX_UPLOAD_BYTES


@dataclass(frozen=True)
class UploadForm:
    product_code: str
    platform_code: str
    channel_code: str
    version: str
    build_number: str
    release_notes: str
    min_supported_version: str
    update_required: bool
    # FEATURE (default) or SAFETY -- see ProductVersion.release_kind. Defaulted so
    # callers that predate the field keep working and cannot make a release SAFETY
    # by accident.
    release_kind: str = "FEATURE"


def form_choices() -> dict:
    products = db_session.execute(
        select(Product).where(Product.is_active.is_(True), Product.is_sellable.is_(True)).order_by(Product.product_code)
    ).scalars().all()
    platforms = db_session.execute(
        select(Platform).where(Platform.platform_code.in_(tuple(PLATFORM_RULES))).order_by(Platform.platform_code)
    ).scalars().all()
    channels = db_session.execute(select(ReleaseChannel).order_by(ReleaseChannel.channel_code)).scalars().all()
    return {"products": products, "platforms": platforms, "channels": channels}


def _parse_build_number(raw: str, platform_code: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        if platform_code == "ANDROID":
            raise UploadRejected(_("An Android release needs a build number: the phone compares it with its own."))
        return None
    if not raw.isascii() or not raw.isdigit() or not (1 <= int(raw) <= _MAX_BUILD_NUMBER):
        raise UploadRejected(_("The build number must be a whole number of 1 or more."))
    return int(raw)


def _validate_fields(form: UploadForm) -> tuple[Product, Platform, ReleaseChannel, int | None]:
    product = db_session.execute(select(Product).where(Product.product_code == form.product_code)).scalars().first()
    if product is None or not product.is_active or not product.is_sellable:
        raise UploadRejected(_("Choose a product from the list."))
    platform = None
    if form.platform_code in PLATFORM_RULES:
        platform = db_session.execute(select(Platform).where(Platform.platform_code == form.platform_code)).scalars().first()
    if platform is None:
        raise UploadRejected(_("Choose a platform from the list."))
    channel = db_session.execute(
        select(ReleaseChannel).where(ReleaseChannel.channel_code == form.channel_code)
    ).scalars().first()
    if channel is None:
        raise UploadRejected(_("Choose a release channel from the list."))
    if not versioning.is_valid_semver(form.version):
        raise UploadRejected(_("The version must look like 1.2.3 or 1.2.3-rc.4."))
    build_number = _parse_build_number(form.build_number, form.platform_code)
    if form.min_supported_version:
        if not versioning.is_valid_semver(form.min_supported_version):
            raise UploadRejected(_("The oldest supported version must look like 1.2.3 or 1.2.3-rc.4."))
        if versioning.compare_semver(form.min_supported_version, form.version) > 0:
            raise UploadRejected(_("The oldest supported version cannot be newer than this version."))
    if form.release_kind not in RELEASE_KINDS:
        raise UploadRejected(_("Choose whether this is a feature update or a safety fix."))
    if len(form.release_notes) > MAX_NOTES_CHARS:
        raise UploadRejected(_("The release notes are too long."))
    duplicate = db_session.execute(
        select(ProductVersion.id).where(
            ProductVersion.product_id == product.id,
            ProductVersion.platform_id == platform.id,
            ProductVersion.version == form.version,
        )
    ).first()
    if duplicate is not None:
        raise UploadRejected(_("This version already exists for this product and platform. Use a new version number."))
    return product, platform, channel, build_number


def _stream_to_temp(file_storage, magic: bytes, root: str) -> tuple[str, str, int]:
    """Copies the upload into a temp file INSIDE the artifact directory (same
    filesystem, so the final rename is atomic) while hashing it. Returns
    (temp_path, sha256_hex, size). Removes the temp file and raises on any
    problem."""
    stream = file_storage.stream
    head = stream.read(len(magic))
    if head != magic:
        raise UploadRejected(_("This file is not the right kind for the platform you chose."))
    digest = hashlib.sha256()
    size = 0
    fd, temp_path = tempfile.mkstemp(dir=root, prefix=".upload-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out:
            chunk = head
            while chunk:
                digest.update(chunk)
                out.write(chunk)
                size += len(chunk)
                chunk = stream.read(_CHUNK)
        if size > MAX_UPLOAD_BYTES:  # belt and braces; the request limit already refuses this
            raise UploadRejected(_("The file is too large."))
        return temp_path, digest.hexdigest(), size
    except BaseException:
        _remove_quietly(temp_path)
        raise


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("release upload: could not remove %s", path, exc_info=True)


def create_draft_from_upload(form: UploadForm, file_storage, *, actor_staff_user_id) -> ProductVersion:
    """Validates, stores the artifact and creates the DRAFT row. Raises
    UploadRejected (plain message) and leaves nothing behind on refusal."""
    product, platform, channel, build_number = _validate_fields(form)
    extension, magic = PLATFORM_RULES[form.platform_code]
    filename = (getattr(file_storage, "filename", "") or "").strip().lower() if file_storage is not None else ""
    if not filename:
        raise UploadRejected(_("Choose the file to upload."))
    if not filename.endswith(extension):
        raise UploadRejected(_("This file is not the right kind for the platform you chose."))

    root = os.path.realpath(storage._storage_root())
    temp_path, sha256_hex, size = _stream_to_temp(file_storage, magic, root)
    final_name = f"{product.product_code}-{platform.platform_code}-{form.version}-{sha256_hex[:8]}{extension}".lower()
    final_path = os.path.join(root, final_name)
    pre_existing = os.path.exists(final_path)
    try:
        os.replace(temp_path, final_path)  # atomic: a half-written file never has the final name
    except BaseException:
        _remove_quietly(temp_path)
        raise

    entry = {
        "product_code": product.product_code,
        "sha256": sha256_hex,
        "path": final_name,
        "size_bytes": size,
        "channel_code": channel.channel_code,
        "build_number": build_number,
        "release_notes": form.release_notes.strip() or None,
        "min_supported_version": form.min_supported_version or None,
        # "Update required": every client older than this version is told the
        # update is mandatory (discovery.py compares against this column).
        "forced_upgrade_threshold": form.version if form.update_required else None,
        "release_kind": form.release_kind,
    }
    key = "windows" if form.platform_code == "WINDOWS" else "android"
    try:
        result = import_release_manifest({"version": form.version, key: [entry]}, actor_staff_user_id)
    except IntegrityError:
        # Lost a race against another upload of the same version (the unique
        # constraint is the real arbiter; the pre-check above is the friendly path).
        db_session.rollback()
        if not pre_existing:
            _remove_quietly(final_path)
        raise UploadRejected(_("This version already exists for this product and platform. Use a new version number."))
    except BaseException:
        db_session.rollback()
        if not pre_existing:
            _remove_quietly(final_path)
        raise
    if not result["created"]:
        if not pre_existing:
            _remove_quietly(final_path)
        raise UploadRejected(_("This version already exists for this product and platform. Use a new version number."))

    row = db_session.execute(
        select(ProductVersion).where(
            ProductVersion.product_id == product.id,
            ProductVersion.platform_id == platform.id,
            ProductVersion.version == form.version,
        )
    ).scalars().one()
    audit_record(
        actor_staff_user_id=actor_staff_user_id,
        actor_role_snapshot=None,
        action_code="RELEASE_UPLOADED",
        entity_type="product_version",
        entity_public_id=str(row.id),
        after_state={
            "product_code": product.product_code, "platform": platform.platform_code,
            "channel": channel.channel_code, "version": form.version, "build_number": build_number,
            "artifact_path": final_name, "artifact_checksum_sha256": sha256_hex, "artifact_size_bytes": size,
            "update_required": form.update_required, "release_kind": form.release_kind, "publication_state": row.publication_state,
        },
    )
    return row
