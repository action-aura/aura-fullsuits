"""Future product-integration API (Part R). This blueprint is ONLY imported and
registered by create_app() when OWNER_EXTERNAL_API_ENABLED=true (default: false).
When disabled, this module is never imported and none of these routes exist --
not merely disabled inside the view function. Prototype-only: no live product
calls this in Phase 5; no authentication/signature scheme is wired yet, which is
exactly why it must stay off by default outside a throwaway dev sandbox."""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import select

from app.api.serializers import serialize_product_version_check_response
from app.extensions import db_session
from app.models.catalog import Platform, Product, ProductVersion

bp = Blueprint("external_api", __name__, url_prefix="/api/v1")


@bp.route("/product-version-check", methods=["GET"])
def product_version_check():
    product_code = request.args.get("product_code")
    platform_code = request.args.get("platform")
    product = db_session.execute(select(Product).where(Product.product_code == product_code)).scalars().first()
    platform = db_session.execute(select(Platform).where(Platform.platform_code == platform_code)).scalars().first()
    if product is None or platform is None:
        return jsonify({"error": "unknown_product_or_platform"}), 404
    version_row = db_session.execute(
        select(ProductVersion)
        .where(ProductVersion.product_id == product.id, ProductVersion.platform_id == platform.id, ProductVersion.is_current_stable.is_(True))
        .order_by(ProductVersion.created_at.desc())
    ).scalars().first()
    if version_row is None:
        return jsonify({"error": "no_stable_version_registered"}), 404
    return jsonify(serialize_product_version_check_response(version_row))


@bp.route("/installation-registration", methods=["POST"])
def installation_registration():
    body = request.get_json(silent=True) or {}
    required = ("product_code", "platform", "installation_label")
    if any(field not in body for field in required):
        return jsonify({"error": "missing_required_field"}), 400
    return jsonify({"error": "not_implemented_in_phase_5"}), 501
