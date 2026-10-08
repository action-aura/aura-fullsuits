"""Crash reports -- the staff page.

  GET /crash-reports               one row per BUG (fingerprint), newest first
  GET /crash-reports/<fingerprint> that bug's stack and the per-install rows

Permission: "system.view". The seeded RBAC has no permission named for
support/diagnostics; "system.view" ("View system settings") is the closest
fit and, today, is held only by Super Admin -- the same people who see Backups
and Reconciliation, the other operational-health pages. That is the most
conservative reading: a crash report can name a route and an exception on a
customer's machine, so it should not widen to every role that merely has
"catalog.view" (which the releases pages use, and which Sales/Support/
Finance/Viewer all hold). Widening it to Support later is a one-line change
here plus a seed_data.py grant -- and a deliberate one.
"""
from __future__ import annotations

import re

from flask import Blueprint, abort, render_template, request
from sqlalchemy import func, select

from app.extensions import db_session
from app.models.crash_reports import CrashReport
from app.security.rbac import require_permission

bp = Blueprint("crash_reports", __name__, url_prefix="/crash-reports")

PERMISSION = "system.view"
PAGE_SIZE = 50
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{32}$")


def _first_frame(frames) -> str:
    """"file:function" of the innermost frame -- where it actually broke."""
    if not frames:
        return ""
    top = frames[0]
    return "%s:%s" % (top.get("file", ""), top.get("function", ""))


@bp.route("", methods=["GET"])
@require_permission(PERMISSION)
def list_reports():
    page = max(request.args.get("page", 1, type=int) or 1, 1)

    total = db_session.execute(select(func.count(func.distinct(CrashReport.fingerprint)))).scalar_one()
    groups = db_session.execute(
        select(
            CrashReport.fingerprint,
            func.max(CrashReport.exception_type).label("exception_type"),
            func.sum(CrashReport.count).label("total_count"),
            func.count(func.distinct(CrashReport.installation_id)).label("installs"),
            func.array_agg(func.distinct(CrashReport.app_version)).label("versions"),
            func.min(CrashReport.first_seen_at).label("first_seen"),
            func.max(CrashReport.last_seen_at).label("last_seen"),
        )
        .group_by(CrashReport.fingerprint)
        .order_by(func.max(CrashReport.last_seen_at).desc(), CrashReport.fingerprint)
        .limit(PAGE_SIZE)
        .offset((page - 1) * PAGE_SIZE)
    ).all()

    # The top frame lives on the rows, not in the aggregate: take it from the
    # most recently seen row of each fingerprint on this page.
    latest = {}
    fingerprints = [g.fingerprint for g in groups]
    if fingerprints:
        rows = db_session.execute(
            select(CrashReport).where(CrashReport.fingerprint.in_(fingerprints)).order_by(CrashReport.last_seen_at.desc())
        ).scalars().all()
        for row in rows:
            latest.setdefault(row.fingerprint, row)

    entries = [
        {
            "fingerprint": g.fingerprint,
            "exception_type": g.exception_type,
            "first_frame": _first_frame(latest[g.fingerprint].frames) if g.fingerprint in latest else "",
            "kind": latest[g.fingerprint].kind if g.fingerprint in latest else "",
            "total_count": int(g.total_count or 0),
            "installs": int(g.installs or 0),
            "versions": sorted(g.versions or []),
            "last_seen": g.last_seen,
        }
        for g in groups
    ]
    total_pages = max((total + PAGE_SIZE - 1) // PAGE_SIZE, 1)
    result = {
        "rows": entries, "page": page, "page_size": PAGE_SIZE, "total": total, "total_pages": total_pages,
        "has_prev": page > 1, "has_next": page < total_pages,
    }
    return render_template("crash_reports/list.html", result=result)


@bp.route("/<fingerprint>", methods=["GET"])
@require_permission(PERMISSION)
def detail(fingerprint):
    if not _FINGERPRINT_RE.match(fingerprint):
        abort(404)
    rows = db_session.execute(
        select(CrashReport).where(CrashReport.fingerprint == fingerprint).order_by(CrashReport.last_seen_at.desc())
    ).scalars().all()
    if not rows:
        abort(404)
    newest = rows[0]
    summary = {
        "fingerprint": fingerprint,
        "exception_type": newest.exception_type,
        "kind": newest.kind,
        "message": newest.message,
        "route": newest.route,
        "total_count": sum(r.count for r in rows),
        "installs": len({r.installation_id for r in rows}),
        "versions": sorted({r.app_version for r in rows}),
    }
    return render_template("crash_reports/detail.html", summary=summary, frames=newest.frames, rows=rows)
