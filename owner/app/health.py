"""Phase 9 Milestone 3 -- liveness/readiness contract.

/health/live: process-responsive only, no dependency calls (a Gunicorn worker
that can execute Python at all answers this -- used for "should this worker
be killed and restarted", not "is the service usable").

/health/ready: real dependency checks (DB connectivity, migration revision,
signing-key/trust-anchor/RBAC-seed/pepper readiness via the same real
preflight checks Phase 8V-P9 hardened) -- used for "should the reverse proxy
route traffic here". Never returns a secret, a stack trace, or any
customer-domain data -- only check names and OK/WARNING/FAIL status, matching
the same redaction discipline as `flask commercial preflight`.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify

bp = Blueprint("health", __name__)


@bp.get("/health/live")
def live():
    return jsonify({"status": "ok"}), 200


@bp.get("/health/ready")
def ready():
    checks = []
    overall_ok = True

    db_ok, db_detail = _check_database()
    checks.append({"name": "database_connectivity", "status": "OK" if db_ok else "FAIL"})
    overall_ok &= db_ok

    if db_ok:
        migration_ok = _check_migration_head()
        checks.append({"name": "migration_at_head", "status": "OK" if migration_ok else "FAIL"})
        overall_ok &= migration_ok

    try:
        from app.commercial_ops.preflight import run_preflight

        preflight_result = run_preflight(key_directory=current_app.config["SIGNING_KEY_DIRECTORY"])
        for check in preflight_result.checks:
            checks.append({"name": check.name, "status": check.status})
        # WARNING (e.g. the dev pepper placeholder) does not fail readiness --
        # only a real FAIL does. Matches `flask commercial preflight`'s own
        # blocking/non-blocking distinction.
        overall_ok &= preflight_result.ok
    except Exception:
        checks.append({"name": "preflight", "status": "FAIL"})
        overall_ok = False

    status_code = 200 if overall_ok else 503
    return jsonify({"ready": overall_ok, "checks": checks}), status_code


def _check_database() -> tuple[bool, str]:
    try:
        from sqlalchemy import text

        from app.extensions import db_session

        db_session.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as exc:  # never leak the connection string or driver internals
        return False, type(exc).__name__


def _check_migration_head() -> bool:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import text

        from app.extensions import db_session

        from pathlib import Path

        owner_dir = Path(__file__).resolve().parents[1]
        cfg = Config(str(owner_dir / "alembic.ini"))
        # alembic.ini's script_location is relative to the process's cwd at
        # Config-resolution time, not to the ini file itself -- explicit here
        # so this check is correct regardless of what directory Gunicorn (or
        # a test runner) happens to have been started from.
        cfg.set_main_option("script_location", str(owner_dir / "migrations"))
        script = ScriptDirectory.from_config(cfg)
        head_revision = script.get_current_head()

        row = db_session.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        current_revision = row[0] if row else None

        return current_revision == head_revision
    except Exception:
        return False
