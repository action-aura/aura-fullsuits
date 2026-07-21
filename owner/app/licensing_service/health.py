"""Service health checks (Part W). Internal detail is rich; public output is
minimal and leaks nothing useful to an attacker."""
from __future__ import annotations

from app.extensions import get_engine
from app.licensing_service.replay import check_replay_protection_available
from app.licensing_service.signing import get_active_signing_key, verify_signing_key_health


def _database_ok() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True
    except Exception:
        return False


def internal_health(signing_key_directory: str) -> dict:
    """Rich detail -- for the internal admin view and startup validation only,
    never returned to an external caller."""
    db_ok = _database_ok()
    replay_ok = check_replay_protection_available()
    signing = verify_signing_key_health(signing_key_directory) if db_ok else {"status": "FAILED", "detail": "database unavailable"}
    return {
        "database": "OK" if db_ok else "FAILED",
        "replay_store": "OK" if replay_ok else "FAILED",
        "signing_key": signing["status"],
        "signing_key_detail": signing["detail"],
        "overall": "OK" if (db_ok and replay_ok and signing["status"] == "OK") else "DEGRADED",
    }


def public_service_info(signing_key_directory: str) -> dict:
    """Minimal, safe. No hosts, paths, stack traces, table counts, or staff
    information -- ever (Part W's explicit list)."""
    health = internal_health(signing_key_directory)
    active_key = get_active_signing_key()
    return {
        "service": "aura-owner-licensing",
        "status": health["overall"],
        "supported_contract_versions": ["v1"],
        "active_signing_key_id": active_key.key_id if active_key and health["overall"] == "OK" else None,
    }


def is_service_ready(signing_key_directory: str, *, replay_protection_required: bool) -> tuple[bool, str | None]:
    """Fail-closed gate used before processing ANY external request -- a
    check-in must never receive an unsigned success response, and no request
    may be processed if replay protection is required but unavailable."""
    if not _database_ok():
        return False, "SERVICE_TEMPORARILY_UNAVAILABLE"
    if replay_protection_required and not check_replay_protection_available():
        return False, "SERVICE_TEMPORARILY_UNAVAILABLE"
    if get_active_signing_key() is None:
        return False, "SIGNING_KEY_UNAVAILABLE"
    return True, None
