"""Clause 8 screens.

  GET  /sellers/customers/open            customers whose owner's 15 days ran out -- any seller may claim one  (sellers.self_service)
  POST /sellers/customers/<id>/claim      take an OPEN customer; a customer somebody owns answers 403, nothing changes

The list shows a name and a city only: enough to recognise a shop, not its contacts, documents or sales. Those
open to the claimer once the customer is theirs.
"""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_babel import gettext as _

from app.auth.session import load_current_staff
from app.i18n_labels import localize_seller_error
from app.security.rbac import require_permission
from app.sellers import ownership
from app.sellers.errors import SellerRulesError

bp = Blueprint("seller_customers", __name__, url_prefix="/sellers/customers")


@bp.route("/open", methods=["GET"])
@require_permission("sellers.self_service")
def open_list():
    rows = [{"customer": c, "release": r} for c, r in ownership.open_customers()]
    return render_template("sellers/customers_open.html", rows=rows)


@bp.route("/<uuid:customer_id>/claim", methods=["POST"])
@require_permission("sellers.self_service")
def claim(customer_id):
    seller = load_current_staff()
    try:
        ownership.claim(customer_id, seller=seller)
    except SellerRulesError as exc:
        if exc.code == "CUSTOMER_NOT_FOUND":
            abort(404)
        if exc.code in ("CUSTOMER_NOT_OPEN", "CLAIM_SELLERS_ONLY"):
            abort(403)
        flash(localize_seller_error(exc.code, **exc.params), "error")
        return redirect(url_for("seller_customers.open_list"), code=303)
    flash(_("The customer is yours now. Your 15 days start today."), "success")
    return redirect(url_for("customers.detail", customer_id=customer_id), code=303)
