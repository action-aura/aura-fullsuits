"""Aura Owner -- configuration.

Refuses to start in non-development mode when required secrets are missing
(Part C). Never carries a default secret value for anything security-sensitive.
"""
from __future__ import annotations

import os


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


REQUIRED_PRODUCTION_SECRETS = (
    "OWNER_SECRET_KEY",
    "OWNER_DATABASE_URL",
    "OWNER_LICENSE_PEPPER",
)

# Phase 9R M2: every env-var name BaseConfig (or an environment subclass)
# actually reads. Used by validate_no_unknown_config() to catch typos --
# an operator setting OWNER_SESION_IDLE_SECONDS (missing an S) should fail
# loudly at startup, not silently fall back to the default while the typo'd
# value is ignored forever.
KNOWN_CONFIG_ENV_VARS = frozenset(
    {
        "OWNER_ENV",
        "OWNER_SECRET_KEY",
        "OWNER_DATABASE_URL",
        "OWNER_TEST_DATABASE_URL",
        "OWNER_LICENSE_PEPPER",
        "OWNER_DB_STATEMENT_TIMEOUT_MS",
        "OWNER_DB_LOCK_TIMEOUT_MS",
        "OWNER_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS",
        "OWNER_DB_POOL_SIZE",
        "OWNER_DB_MAX_OVERFLOW",
        "OWNER_SESSION_ABSOLUTE_SECONDS",
        "OWNER_SESSION_IDLE_SECONDS",
        "OWNER_RECENT_AUTH_SECONDS",
        "OWNER_LOGIN_MAX_ATTEMPTS",
        "OWNER_LOGIN_LOCKOUT_SECONDS",
        "OWNER_EXTERNAL_API_ENABLED",
        "OWNER_BACKUP_DIR",
        "OWNER_EXPENSE_ATTACHMENT_DIR",
        "OWNER_TEST_EXPENSE_ATTACHMENT_DIR",
        "OWNER_EXPENSE_ATTACHMENT_MAX_BYTES",
        "OWNER_EXTERNAL_API_BIND_HOST",
        "OWNER_EXTERNAL_API_ALLOWED_CONTRACTS",
        "OWNER_SIGNING_KEY_DIRECTORY",
        "OWNER_TEST_SIGNING_KEY_DIRECTORY",
        "OWNER_REPLAY_PROTECTION_REQUIRED",
        "OWNER_DISTRIBUTED_RATE_LIMIT_REQUIRED",
        "OWNER_ACTIVATION_TIMESTAMP_SKEW_SECONDS",
        "OWNER_NONCE_TTL_SECONDS",
        "OWNER_ASSERTION_TTL_SECONDS",
        "OWNER_DEFAULT_OFFLINE_GRACE_SECONDS",
        "OWNER_MAX_REQUEST_BYTES",
        "OWNER_MAX_CONTENT_LENGTH_BYTES",
        "OWNER_RELEASE_ARTIFACT_DIR",
        "OWNER_TEST_RELEASE_ARTIFACT_DIR",
        "OWNER_RELEASE_DOWNLOAD_TOKEN_TTL_SECONDS",
        # Phase 9R M2 additions
        "OWNER_TRUSTED_PROXY_COUNT",
        "OWNER_ALLOWED_HOSTS",
        "OWNER_BACKUP_TARGET_URL",
        "OWNER_SCHEDULER_ROLE",
        "OWNER_STRICT_CONFIG",
        # Read directly by other modules (not by this Config class), but
        # still legitimate OWNER_* variables -- must not trip the unknown-key
        # check in strict mode.
        "OWNER_PG_BIN_DIR",  # app/system/backup.py -- pg_dump/pg_restore location override
        "OWNER_BOOTSTRAP_PASSWORD",  # app/cli.py -- non-interactive create-superadmin
    }
)

# Values that must never be accepted as a real secret in staging/production --
# every default this module itself ships anywhere, plus obvious placeholders.
# Checked with a substring match ("insecure"/"dev-only"/"test") so a copy-paste
# of any dev/test default is caught even if someone edits it slightly.
_INSECURE_SECRET_MARKERS = ("insecure", "dev-only", "test-secret", "test-license-pepper", "changeme", "placeholder")

_MIN_SECRET_LENGTH = 32  # bytes of a real generated secret, not a guessable phrase

# Database identifiers that must never appear in a staging/production
# OWNER_DATABASE_URL -- if they do, the environment almost certainly points
# at a dev or test database by mistake (M2: "wrong environment"). Deliberately
# does NOT include "localhost"/"127.0.0.1" -- a self-managed PostgreSQL
# co-located with the app on the same host (ADR-3) is a legitimate production
# topology, not a misconfiguration.
_DEV_OR_TEST_DB_MARKERS = ("aura_owner_dev", "aura_owner_test")


class BaseConfig:
    ENV = os.environ.get("OWNER_ENV", "development")
    DEBUG = False
    TESTING = False

    SECRET_KEY = os.environ.get("OWNER_SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = os.environ.get("OWNER_DATABASE_URL", "")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # -- Phase 9R M4: PostgreSQL connection hardening --
    # Applied via libpq connection options (see app/extensions.py:init_db),
    # not an ALTER ROLE against the database -- portable across whatever
    # role/host the connection string points at, and doesn't require the
    # deploying operator to have privileges to alter roles. Defaults are
    # generous enough not to affect normal request/test latency but real
    # enough to catch a genuinely runaway query, a stuck lock wait, or a
    # leaked idle-in-transaction connection before it exhausts the pool.
    DB_STATEMENT_TIMEOUT_MS = int(os.environ.get("OWNER_DB_STATEMENT_TIMEOUT_MS", "30000"))  # 30s
    DB_LOCK_TIMEOUT_MS = int(os.environ.get("OWNER_DB_LOCK_TIMEOUT_MS", "10000"))  # 10s
    DB_IDLE_IN_TRANSACTION_TIMEOUT_MS = int(
        os.environ.get("OWNER_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS", "120000")
    )  # 120s
    # Bounded application connections (M4/M5): pool_size + max_overflow is
    # the hard ceiling this one process will ever hold open. Sized small by
    # default because it multiplies by Gunicorn worker count (M5) -- five
    # workers * (5+10) here is already 75 possible connections, which must
    # stay under PostgreSQL's own max_connections with headroom for other
    # processes (the scheduler, psql, backups).
    DB_POOL_SIZE = int(os.environ.get("OWNER_DB_POOL_SIZE", "5"))
    DB_MAX_OVERFLOW = int(os.environ.get("OWNER_DB_MAX_OVERFLOW", "10"))

    LICENSE_PEPPER = os.environ.get("OWNER_LICENSE_PEPPER", "")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("OWNER_ENV") == "production"
    PERMANENT_SESSION_LIFETIME_SECONDS = int(os.environ.get("OWNER_SESSION_ABSOLUTE_SECONDS", "28800"))  # 8h
    SESSION_IDLE_TIMEOUT_SECONDS = int(os.environ.get("OWNER_SESSION_IDLE_SECONDS", "1800"))  # 30m
    RECENT_AUTH_WINDOW_SECONDS = int(os.environ.get("OWNER_RECENT_AUTH_SECONDS", "600"))  # 10m

    LOGIN_MAX_ATTEMPTS = int(os.environ.get("OWNER_LOGIN_MAX_ATTEMPTS", "5"))
    LOGIN_LOCKOUT_SECONDS = int(os.environ.get("OWNER_LOGIN_LOCKOUT_SECONDS", "900"))  # 15m

    EXTERNAL_API_ENABLED = os.environ.get("OWNER_EXTERNAL_API_ENABLED", "false").lower() == "true"

    BACKUP_DIRECTORY = os.environ.get("OWNER_BACKUP_DIR", os.path.join(os.getcwd(), "var", "backups"))

    # -- Phase 9.5E: Expense attachments -- private, non-web-served storage.
    # Never under static/ or any Flask-served directory; access is always
    # mediated by an authorization-checked download route. See
    # docs/owner/phase9_5e/attachment-security-contract.md.
    EXPENSE_ATTACHMENT_DIRECTORY = os.environ.get(
        "OWNER_EXPENSE_ATTACHMENT_DIR", os.path.join(os.getcwd(), "var", "expense-attachments")
    )
    EXPENSE_ATTACHMENT_MAX_BYTES = int(os.environ.get("OWNER_EXPENSE_ATTACHMENT_MAX_BYTES", str(10 * 1024 * 1024)))  # 10MB

    # -- Phase 9R M11: private release artifact storage (local/test adapter
    # only -- see app/releases/storage.py's module docstring; a real
    # deployment uses external object storage instead, per
    # infrastructure-availability-audit.md #6). Never under static/ or any
    # Flask-served directory; access is always mediated by a validated
    # ReleaseDownloadAuthorization.
    RELEASE_ARTIFACT_DIRECTORY = os.environ.get(
        "OWNER_RELEASE_ARTIFACT_DIR", os.path.join(os.getcwd(), "var", "release-artifacts")
    )
    RELEASE_DOWNLOAD_TOKEN_TTL_SECONDS = int(os.environ.get("OWNER_RELEASE_DOWNLOAD_TOKEN_TTL_SECONDS", "300"))  # 5m

    WTF_CSRF_TIME_LIMIT = None

    # -- Phase 6: Licensing & Activation Service --
    EXTERNAL_API_BIND_HOST = os.environ.get("OWNER_EXTERNAL_API_BIND_HOST", "127.0.0.1")
    EXTERNAL_API_ALLOWED_CONTRACTS = os.environ.get("OWNER_EXTERNAL_API_ALLOWED_CONTRACTS", "v1").split(",")
    SIGNING_KEY_DIRECTORY = os.environ.get(
        "OWNER_SIGNING_KEY_DIRECTORY", os.path.join(os.getcwd(), "var", "signing-keys")
    )
    REPLAY_PROTECTION_REQUIRED = os.environ.get("OWNER_REPLAY_PROTECTION_REQUIRED", "true").lower() == "true"
    DISTRIBUTED_RATE_LIMIT_REQUIRED = (
        os.environ.get("OWNER_DISTRIBUTED_RATE_LIMIT_REQUIRED", "true").lower() == "true"
    )
    ACTIVATION_TIMESTAMP_SKEW_SECONDS = int(os.environ.get("OWNER_ACTIVATION_TIMESTAMP_SKEW_SECONDS", "300"))
    NONCE_TTL_SECONDS = int(os.environ.get("OWNER_NONCE_TTL_SECONDS", "600"))
    ASSERTION_TTL_SECONDS = int(os.environ.get("OWNER_ASSERTION_TTL_SECONDS", "86400"))
    DEFAULT_OFFLINE_GRACE_SECONDS = int(os.environ.get("OWNER_DEFAULT_OFFLINE_GRACE_SECONDS", str(14 * 86400)))
    MAX_REQUEST_BYTES = int(os.environ.get("OWNER_MAX_REQUEST_BYTES", str(64 * 1024)))
    # Phase 9R M6/M8: MAX_CONTENT_LENGTH is a Flask-GLOBAL ceiling (enforced
    # by Werkzeug before any view function runs) -- it must be large enough
    # for the biggest legitimate body any route accepts (expense attachment
    # uploads, EXPENSE_ATTACHMENT_MAX_BYTES=10MB, plus multipart overhead
    # headroom), never just the licensing API's own much smaller
    # MAX_REQUEST_BYTES. The licensing API enforces its own tighter 64KB
    # bound independently in app/api_external/routes.py's _bounded_payload()
    # -- real bug found and fixed this milestone: before this, any
    # multipart upload over 64KB (nearly every real expense attachment) was
    # silently rejected with a raw 413 before ever reaching the
    # attachment-specific size/type validation.
    MAX_CONTENT_LENGTH = int(os.environ.get("OWNER_MAX_CONTENT_LENGTH_BYTES", str(12 * 1024 * 1024)))  # 12MB

    # -- Phase 9R M2: environment separation --
    # How many hops of X-Forwarded-* to trust from the reverse proxy. 0 in
    # development/testing (no proxy in front); required explicit and >=1 in
    # staging/production once a reverse proxy (Caddy, M6) is in the request
    # path, so client IP / scheme detection doesn't blindly trust arbitrary
    # inbound headers.
    TRUSTED_PROXY_COUNT = int(os.environ.get("OWNER_TRUSTED_PROXY_COUNT", "0"))
    # Comma-separated allowlist of Host header values this deployment answers
    # to. Empty in development/testing; required non-empty in staging/
    # production so a spoofed Host header can't be used for cache poisoning
    # or bypassing origin checks.
    ALLOWED_HOSTS = tuple(h.strip() for h in os.environ.get("OWNER_ALLOWED_HOSTS", "").split(",") if h.strip())
    # Where encrypted backups are shipped (M12). Must be a remote URI
    # (s3://, b2://, https://, etc.), never a bare local filesystem path --
    # a local path defeats the entire point of an external backup.
    BACKUP_TARGET_URL = os.environ.get("OWNER_BACKUP_TARGET_URL", "")
    # Exactly one process per environment may own the report-snapshot
    # scheduler (M5). "owner" runs it; "worker" (the default, safe for every
    # Gunicorn worker process) does not. Staging/production must set this
    # explicitly for whichever single process is meant to own it -- no
    # environment may rely on the default silently deciding.
    SCHEDULER_ROLE = os.environ.get("OWNER_SCHEDULER_ROLE", "worker")
    # M2: when true, validate() also rejects any OWNER_* environment variable
    # that isn't in KNOWN_CONFIG_ENV_VARS (catches typos like a misspelled
    # override that would otherwise silently fall back to a default). Off by
    # default so an operator's unrelated OWNER_* variables from outside this
    # app don't break startup; staging/production should set it true.
    STRICT_CONFIG = os.environ.get("OWNER_STRICT_CONFIG", "false").lower() == "true"

    # -- Phase 9.5B-R: Owner-wide internationalization --
    # Strict allowlist -- app.i18n.select_locale() never trusts a client-
    # supplied locale string that isn't a key in this dict (Non-Negotiable
    # Principle 5). "ar" is real Modern Standard Arabic, RTL.
    LANGUAGES = {"en": "English", "ar": "العربية"}
    BABEL_DEFAULT_LOCALE = "en"
    BABEL_TRANSLATION_DIRECTORIES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "translations")

    @classmethod
    def validate(cls) -> None:
        if cls.ENV == "development" or cls.TESTING:
            return

        problems: list[str] = []

        # Resolved class attributes, not raw os.environ -- consistent with
        # every other check below, and correct for a Config subclass that
        # computes SECRET_KEY/SQLALCHEMY_DATABASE_URI/LICENSE_PEPPER by any
        # means other than a 1:1 env-var passthrough.
        resolved = {
            "OWNER_SECRET_KEY": cls.SECRET_KEY,
            "OWNER_DATABASE_URL": cls.SQLALCHEMY_DATABASE_URI,
            "OWNER_LICENSE_PEPPER": cls.LICENSE_PEPPER,
        }
        missing = [name for name in REQUIRED_PRODUCTION_SECRETS if not resolved.get(name)]
        if missing:
            # Missing secrets make every other check meaningless (there's
            # nothing to validate the strength/format of) -- fail fast here
            # rather than accumulating confusing downstream errors.
            raise ConfigError(
                f"Refusing to start: missing required secret(s) for OWNER_ENV={cls.ENV!r}: {', '.join(missing)}"
            )

        if not cls._looks_like_a_real_secret(cls.SECRET_KEY):
            problems.append(
                "OWNER_SECRET_KEY is missing, a known default/placeholder value, or shorter than "
                f"{_MIN_SECRET_LENGTH} characters -- generate a real random secret for this environment"
            )
        if not cls._looks_like_a_real_secret(cls.LICENSE_PEPPER):
            problems.append(
                "OWNER_LICENSE_PEPPER is missing, a known default/placeholder value, or shorter than "
                f"{_MIN_SECRET_LENGTH} characters"
            )
        if not cls._database_url_looks_valid(cls.SQLALCHEMY_DATABASE_URI):
            problems.append(
                "OWNER_DATABASE_URL is not a recognized PostgreSQL URL (expected a postgresql:// or "
                "postgresql+psycopg:// scheme)"
            )
        elif cls._database_url_points_at_dev_or_test(cls.SQLALCHEMY_DATABASE_URI):
            problems.append(
                f"OWNER_DATABASE_URL for OWNER_ENV={cls.ENV!r} looks like a development or test database "
                "(matches a known dev/test database name) -- staging and production must use their own "
                "dedicated database, never a copy of the dev/test connection string"
            )
        if not cls.SESSION_COOKIE_SECURE:
            problems.append(f"SESSION_COOKIE_SECURE must be true when OWNER_ENV={cls.ENV!r}")
        if cls.TRUSTED_PROXY_COUNT < 1:
            problems.append(
                "OWNER_TRUSTED_PROXY_COUNT must be >=1 in staging/production -- this deployment sits behind a "
                "reverse proxy (M6); without an explicit trusted-hop count, X-Forwarded-* headers cannot be "
                "trusted for client IP or scheme detection"
            )
        if not cls.ALLOWED_HOSTS:
            problems.append(
                "OWNER_ALLOWED_HOSTS must be set (comma-separated) in staging/production -- an empty host "
                "allowlist accepts any Host header"
            )
        if not cls.BACKUP_TARGET_URL:
            problems.append("OWNER_BACKUP_TARGET_URL must be set in staging/production (see docs/owner/phase9r/backup-policy.md)")
        elif cls._looks_like_a_local_path(cls.BACKUP_TARGET_URL):
            problems.append(
                "OWNER_BACKUP_TARGET_URL looks like a local filesystem path, not a remote URI -- backups "
                "stored only on the production host are not a real backup (M12)"
            )
        if cls.SCHEDULER_ROLE not in ("owner", "worker"):
            problems.append(
                f"OWNER_SCHEDULER_ROLE={cls.SCHEDULER_ROLE!r} is invalid -- must be exactly 'owner' or 'worker', "
                "set explicitly for every process in staging/production so scheduler ownership is never ambiguous"
            )
        if cls.STRICT_CONFIG:
            unknown = sorted(
                k for k in os.environ if k.startswith("OWNER_") and k not in KNOWN_CONFIG_ENV_VARS
            )
            if unknown:
                problems.append(
                    "OWNER_STRICT_CONFIG=true and unrecognized OWNER_* environment variable(s) are set "
                    f"(possible typo): {', '.join(unknown)}"
                )

        if problems:
            raise ConfigError(f"Refusing to start with OWNER_ENV={cls.ENV!r}: " + "; ".join(problems))

        if cls.EXTERNAL_API_ENABLED:
            cls.validate_external_api_production()

    @staticmethod
    def _looks_like_a_real_secret(value: str) -> bool:
        if not value or len(value) < _MIN_SECRET_LENGTH:
            return False
        lowered = value.lower()
        return not any(marker in lowered for marker in _INSECURE_SECRET_MARKERS)

    @staticmethod
    def _database_url_looks_valid(url: str) -> bool:
        return bool(url) and url.startswith(("postgresql://", "postgresql+psycopg://"))

    @staticmethod
    def _database_url_points_at_dev_or_test(url: str) -> bool:
        lowered = url.lower()
        return any(marker in lowered for marker in _DEV_OR_TEST_DB_MARKERS)

    @staticmethod
    def _looks_like_a_local_path(value: str) -> bool:
        # A real remote URI always has a "scheme://" prefix (s3://, b2://,
        # https://, ...). Anything else -- a bare path, a Windows drive
        # letter, a relative path -- is a local filesystem path.
        return "://" not in value

    @classmethod
    def validate_external_api_production(cls) -> None:
        """Part AB/W: production-like external API mode must fail closed, not
        merely log a warning, when any of these hold."""
        if cls.ENV == "development" or cls.TESTING:
            return
        problems = []
        if not cls.LICENSE_PEPPER or "insecure" in cls.LICENSE_PEPPER:
            problems.append("OWNER_LICENSE_PEPPER is missing or a known-insecure default value")
        if not os.path.isdir(cls.SIGNING_KEY_DIRECTORY):
            problems.append(f"OWNER_SIGNING_KEY_DIRECTORY does not exist: {cls.SIGNING_KEY_DIRECTORY}")
        if not cls.REPLAY_PROTECTION_REQUIRED:
            problems.append("OWNER_REPLAY_PROTECTION_REQUIRED must be true when the external API is enabled")
        if not cls.DISTRIBUTED_RATE_LIMIT_REQUIRED:
            problems.append("OWNER_DISTRIBUTED_RATE_LIMIT_REQUIRED must be true when the external API is enabled")
        if cls.DEBUG:
            problems.append("DEBUG must be false when the external API is enabled in a non-development environment")
        if not cls.SESSION_COOKIE_SECURE:
            problems.append("SESSION_COOKIE_SECURE must be true when the external API is enabled")
        if problems:
            raise ConfigError(
                "Refusing to start with OWNER_EXTERNAL_API_ENABLED=true: " + "; ".join(problems)
            )


class DevelopmentConfig(BaseConfig):
    ENV = "development"
    DEBUG = True
    SECRET_KEY = os.environ.get("OWNER_SECRET_KEY", "dev-only-insecure-key-do-not-use-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "OWNER_DATABASE_URL", "postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_dev"
    )
    LICENSE_PEPPER = os.environ.get("OWNER_LICENSE_PEPPER", "dev-only-insecure-pepper-do-not-use-in-production")
    SIGNING_KEY_DIRECTORY = os.environ.get(
        "OWNER_SIGNING_KEY_DIRECTORY", os.path.join(os.getcwd(), "var", "signing-keys")
    )
    # Flask-WTF's WTF_CSRF_SSL_STRICT defaults True, which over https://
    # requires a Referer header matching Host -- correct for a real
    # production TLS deployment, but a self-signed local-network cert (the
    # only kind a dev/demo instance can have) reliably makes browsers
    # withhold or alter Referer on the untrusted-cert warning flow, turning
    # every POST into "Bad Request: The referrer header is missing." Off
    # only here, never in Testing/Staging/Production.
    WTF_CSRF_SSL_STRICT = False


class TestingConfig(BaseConfig):
    ENV = "testing"
    TESTING = True
    DEBUG = True
    SECRET_KEY = "test-secret-key"
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "OWNER_TEST_DATABASE_URL", "postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test"
    )
    LICENSE_PEPPER = "test-license-pepper"
    LOGIN_MAX_ATTEMPTS = 5
    WTF_CSRF_ENABLED = True
    SIGNING_KEY_DIRECTORY = os.environ.get(
        "OWNER_TEST_SIGNING_KEY_DIRECTORY", os.path.join(os.getcwd(), "var", "signing-keys-test")
    )
    EXPENSE_ATTACHMENT_DIRECTORY = os.environ.get(
        "OWNER_TEST_EXPENSE_ATTACHMENT_DIR", os.path.join(os.getcwd(), "var", "expense-attachments-test")
    )
    RELEASE_ARTIFACT_DIRECTORY = os.environ.get(
        "OWNER_TEST_RELEASE_ARTIFACT_DIR", os.path.join(os.getcwd(), "var", "release-artifacts-test")
    )
    REPLAY_PROTECTION_REQUIRED = True
    DISTRIBUTED_RATE_LIMIT_REQUIRED = True
    # The external API is exercised directly by the test suite (Part Y) --
    # always on in TESTING regardless of the OWNER_EXTERNAL_API_ENABLED env
    # var, so `pytest` alone is sufficient to prove the whole surface works.
    EXTERNAL_API_ENABLED = True


class ProductionConfig(BaseConfig):
    ENV = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = True


class StagingConfig(BaseConfig):
    """Phase 9: same hardening posture as production (secret validation,
    SESSION_COOKIE_SECURE, external-API production checks all apply --
    staging is not exempt from any of them), but a distinct ENV value so
    logs/health responses/audit records are never mistakable for real
    production (Non-Negotiable Principle 2: "staging must be clearly labeled
    and operated as staging"). Requires its own OWNER_SECRET_KEY,
    OWNER_DATABASE_URL, OWNER_LICENSE_PEPPER, and signing-key directory --
    BaseConfig.validate() enforces this the same way it does for
    'production', and Milestone 5/staging-key-separation requires those
    values to never be copied from a real production environment (none
    exists yet) or from development."""

    ENV = "staging"
    DEBUG = False
    SESSION_COOKIE_SECURE = True


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "staging": StagingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    name = name or os.environ.get("OWNER_ENV", "development")
    return CONFIG_MAP.get(name, DevelopmentConfig)
