"""Command Palette (Ctrl+K) web route.

`@require_login` only -- there is no single permission that gates "the
command palette" as a feature, same reasoning as attention/routes.py:
the endpoint's real content is already independently permission- and
ownership-filtered per entity type inside
`service.search_entities()`. An employee with none of the relevant
permissions still reaches this endpoint and gets a real, honest empty
result set, not a 403 -- the endpoint itself exposes nothing; only the
already-permission-checked results inside it do.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth.session import load_current_staff
from app.command_palette.service import search_entities, search_results_as_dicts
from app.security.rbac import require_login

bp = Blueprint("command_palette", __name__, url_prefix="/api/command-palette")


@bp.route("/search", methods=["GET"])
@require_login
def search():
    staff = load_current_staff()
    query = request.args.get("q", "", type=str) or ""
    results = search_entities(staff, query)
    response = jsonify({"query": query, "results": search_results_as_dicts(results)})
    # Never cached: results are permission- and ownership-scoped per actor
    # and per request; a shared/proxy cache keyed only on the URL could
    # otherwise leak one employee's results to another.
    response.headers["Cache-Control"] = "no-store"
    return response
