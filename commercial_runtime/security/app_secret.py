"""
Aura FullSuits -- per-installation Flask SECRET_KEY.

Generated once with a cryptographically secure random generator and persisted
under the writable app-data directory (the same directory registry.db and
config.json already live in). Reused on every subsequent start so existing
sessions survive a restart.

Never falls back to a hardcoded/shared value. If the stored secret is missing,
unreadable, or malformed, a fresh one is generated and persisted -- this
invalidates any active sessions (forces re-login) but never falls back to a
public default, and the failure is logged (sanitized -- the secret value
itself is never logged, only file paths and status).

Extracted verbatim from Action Aura Enterprise's core/security/app_secret.py
(the Retail Phase 1 security remediation) -- see docs/migration/risk-register.md R1.
"""
from __future__ import annotations

import logging
import os
import stat
import secrets as _secrets

log = logging.getLogger("aura.security.secret")

_SECRET_FILENAME = "secret.key"
_EXPECTED_HEX_LEN = 64  # 32 random bytes, hex-encoded
_HEX_DIGITS = set("0123456789abcdef")


def _secret_dir(app_data_dir: str) -> str:
    d = os.path.join(app_data_dir, "security")
    os.makedirs(d, exist_ok=True)
    return d


def _secret_path(app_data_dir: str) -> str:
    return os.path.join(_secret_dir(app_data_dir), _SECRET_FILENAME)


def _valid(value: str) -> bool:
    return bool(value) and len(value) == _EXPECTED_HEX_LEN and all(c in _HEX_DIGITS for c in value)


def _generate_and_persist(path: str) -> str:
    value = _secrets.token_hex(32)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="ascii") as f:
        f.write(value)
    os.replace(tmp_path, path)  # atomic on both POSIX and Windows
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return value


def get_or_create_secret_key(app_data_dir: str) -> str:
    """Return this installation's persistent secret key, creating it on first run.

    Contract:
      - Never returns a hardcoded/shared literal.
      - Never logs the secret value.
      - Reuses the same value across restarts as long as the file is intact.
      - On any read/corruption failure, generates and persists a NEW secret
        rather than falling back to a default -- fails safe, not open.
    """
    path = _secret_path(app_data_dir)

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="ascii") as f:
                value = f.read().strip()
        except OSError as exc:
            log.error(
                "Secret key file exists but could not be read (%s: %s). "
                "Generating a new per-installation secret; active sessions will be invalidated.",
                type(exc).__name__, path,
            )
            return _generate_and_persist(path)

        if _valid(value):
            log.info("Loaded existing per-installation secret key (%s).", path)
            return value

        log.error(
            "Secret key file at %s is corrupted or malformed (unexpected length/format). "
            "Generating a new per-installation secret; active sessions will be invalidated.",
            path,
        )
        return _generate_and_persist(path)

    log.info("No secret key found for this installation -- generating one now (%s).", path)
    return _generate_and_persist(path)
