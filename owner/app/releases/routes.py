"""Release/version routes: read-only views over owner_product_versions /
owner_release_channels (Part I)."""
from __future__ import annotations

from flask import Blueprint, render_template, request
from sqlalchemy import select

from app.extensions import db_session
from app.models.catalog import ProductVersion, ReleaseChannel
from app.security.rbac import require_permission

bp = Blueprint("releases", __name__, url_prefix="/releases")


@bp.route("/versions", methods=["GET"])
@require_permission("catalog.view")
def versions():
    product_filter = request.args.get("product_id")
    stmt = select(ProductVersion).order_by(ProductVersion.imported_at.desc())
    if product_filter:
        stmt = stmt.where(ProductVersion.product_id == product_filter)
    version_rows = db_session.execute(stmt).scalars().all()
    return render_template("catalog/versions.html", versions=version_rows)


@bp.route("/channels", methods=["GET"])
@require_permission("catalog.view")
def channels():
    channel_rows = db_session.execute(select(ReleaseChannel)).scalars().all()
    return render_template("catalog/channels.html", channels=channel_rows)
