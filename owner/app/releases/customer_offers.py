"""Which installers a signed-in website customer may download (new customer flow, S4).

The website's Downloads page asks Owner (POST /api/website/v1/account/summary) what
this customer may fetch; this module is the answer. It never decides who the
customer is (the account session did) and never touches a device.

WHO GETS AN OFFER: a customer with at least one listable licence -- a trial (PILOT),
a paid shop (ACTIVE), or one whose term lapsed (PAST_DUE / EXPIRED): a locked shop
must still be able to reinstall (owner, 2026-10-31). Suspended, cancelled and revoked
ones get nothing (views.licence_rows).

WHICH BUILD: per product and platform, the highest-precedence PUBLISHED release that
has an artifact, a checksum and a size, in a channel the customer may see:
  * a licence pinned to a release channel sees ONLY that channel (the pin is a
    commercial promise, same rule as update discovery);
  * an unpinned licence sees the channels listed in OWNER_WEBSITE_DOWNLOAD_CHANNELS
    (default STABLE only).
An installer is a reinstall of the product, not an "update", so the keep-selling rule
that withholds FEATURE updates from an unpaid shop does not apply here.

offer_id is the release's id: opaque to the website, validated here at authorize time
against the CURRENT offers (a release withdrawn after the page was drawn stops being
downloadable).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from flask import current_app
from sqlalchemy import select

from app.customer_accounts import views
from app.extensions import db_session
from app.models.catalog import Platform, ProductVersion, ReleaseChannel
from app.releases import versioning

PLATFORM_LABEL = {"WINDOWS": "windows", "ANDROID": "android"}
_EXT_FOR = {"windows": (".exe", ".msi"), "android": (".apk",)}
_DEFAULT_EXT = {"windows": ".exe", "android": ".apk"}
CONTENT_TYPE = {"windows": "application/x-msdownload", "android": "application/vnd.android.package-archive"}


@dataclass(frozen=True)
class Offer:
    offer_id: str
    platform: str  # "windows" | "android"
    version: str
    size_bytes: int
    filename: str
    release: ProductVersion

    def public_view(self) -> dict:
        return {"offer_id": self.offer_id, "platform": self.platform, "version": self.version,
                "size_bytes": self.size_bytes, "filename": self.filename}


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", text or "")


def filename_for(product_name: str, platform: str, release: ProductVersion) -> str:
    """AuraRetail-1.0.0-rc.17.exe -- built from our own data, never from a stored
    path, and always inside the [A-Za-z0-9._ ()-]{1,120} set the website accepts."""
    suffix = ""
    path = (release.artifact_path or "").lower()
    for ext in _EXT_FOR[platform]:
        if path.endswith(ext):
            suffix = ext
    base = _safe(product_name.replace(" ", "")) or "Aura"
    name = f"{base}-{_safe(release.version)}{suffix or _DEFAULT_EXT[platform]}"
    return name[:120]


def _channel_ids_for(licences) -> list:
    pinned = {row.license.allowed_release_channel_id for row in licences if row.license.allowed_release_channel_id}
    if pinned and all(row.license.allowed_release_channel_id for row in licences):
        return list(pinned)
    wanted = {c.strip().upper() for c in (current_app.config.get("WEBSITE_DOWNLOAD_CHANNELS") or "STABLE").split(",") if c.strip()}
    ids = set(db_session.execute(select(ReleaseChannel.id).where(ReleaseChannel.channel_code.in_(wanted))).scalars())
    return list(ids | pinned)


def offers_for_customer(customer_id) -> list[Offer]:
    licences = views.licence_rows(customer_id)
    if not licences:
        return []
    channel_ids = _channel_ids_for(licences)
    if not channel_ids:
        return []
    out: list[Offer] = []
    for product_id in dict.fromkeys(row.product.id for row in licences):
        product = next(row.product for row in licences if row.product.id == product_id)
        allowed_platforms = {p for row in licences if row.product.id == product_id
                             for p in (row.license.allowed_platforms or "").split(",")}
        for platform_row in db_session.execute(select(Platform).order_by(Platform.platform_code)).scalars():
            label = PLATFORM_LABEL.get(platform_row.platform_code)
            if label is None or platform_row.platform_code not in allowed_platforms:
                continue
            candidates = db_session.execute(
                select(ProductVersion).where(
                    ProductVersion.product_id == product_id, ProductVersion.platform_id == platform_row.id,
                    ProductVersion.publication_state == "PUBLISHED", ProductVersion.artifact_path.is_not(None),
                    ProductVersion.artifact_checksum_sha256.is_not(None), ProductVersion.artifact_size_bytes.is_not(None),
                    ProductVersion.release_channel_id.in_(channel_ids),
                )
            ).scalars().all()
            best, best_key = None, None
            for row in candidates:
                try:
                    key = (versioning.parse_semver(row.version), row.build_number if row.build_number is not None else -1)
                except ValueError:
                    continue  # one malformed stored version must not hide the others
                if best_key is None or key > best_key:
                    best, best_key = row, key
            if best is not None:
                out.append(Offer(offer_id=str(best.id), platform=label, version=best.version,
                                 size_bytes=int(best.artifact_size_bytes), filename=filename_for(product.name, label, best),
                                 release=best))
    return out


def find_offer(customer_id, offer_id: str) -> Offer | None:
    return next((o for o in offers_for_customer(customer_id) if o.offer_id == offer_id), None)
