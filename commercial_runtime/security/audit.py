"""
Aura FullSuits -- security audit logging.

Thin, crash-proof wrapper around commercial_runtime.identity.registry_db.log_audit
for authentication/authorization events. Two rules enforced here:

  1. A logging failure must never affect the authentication/authorization
     decision it is describing -- every call is wrapped so an exception here
     is swallowed (after being printed to stderr for local diagnosis), not
     propagated into the caller's login/permission flow.
  2. Never pass a password, a password hash (legacy or modern), or the
     session secret into `context` -- only non-sensitive identifiers and
     outcome flags.

Every event is company-scoped so a company's log query can never surface
another company's rows.

Extracted from Action Aura Enterprise's core/security/audit.py, retargeted at
commercial_runtime's trimmed registry_db.
"""
from __future__ import annotations

SECURITY_MODULE = "SECURITY"

LOGIN_SUCCESS = "LOGIN_SUCCESS"
LOGIN_FAILED = "LOGIN_FAILED"
ACCOUNT_LOCKOUT = "ACCOUNT_LOCKOUT"
LOGOUT = "LOGOUT"
PASSWORD_HASH_UPGRADED = "PASSWORD_HASH_UPGRADED"
PASSWORD_CHANGED = "PASSWORD_CHANGED"
ADMIN_CREATED = "ADMIN_CREATED"
ROLE_CHANGED = "ROLE_CHANGED"
ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
ACCOUNT_REACTIVATED = "ACCOUNT_REACTIVATED"
DEMO_RESET_BLOCKED = "DEMO_RESET_BLOCKED"
DEMO_RESET_EXECUTED = "DEMO_RESET_EXECUTED"
SECURITY_CONFIG_FAILURE = "SECURITY_CONFIG_FAILURE"


def record(company_id, user_id, action: str, entity_type: str = "AUTH",
           entity_id: str = "-", outcome: str = "", context: dict | None = None) -> None:
    """Best-effort security audit write. Never raises.

    `context` must only contain non-sensitive fields (e.g. {'email': ...,
    'reason': 'bad_password'}). Never put a password, hash, or secret in it.
    """
    try:
        from commercial_runtime.identity.registry_db import log_audit
        safe_new_value = dict(context or {})
        if outcome:
            safe_new_value["outcome"] = outcome
        log_audit(
            company_id=str(company_id) if company_id is not None else "UNKNOWN",
            user_id=str(user_id) if user_id is not None else None,
            module_code=SECURITY_MODULE,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            new_value=safe_new_value,
        )
    except Exception as exc:  # pragma: no cover - defensive, must never break auth
        print(f"[security-audit] failed to record {action}: {type(exc).__name__}: {exc}")
