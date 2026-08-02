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


class BaseConfig:
    ENV = os.environ.get("OWNER_ENV", "development")
    DEBUG = False
    TESTING = False

    SECRET_KEY = os.environ.get("OWNER_SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = os.environ.get("OWNER_DATABASE_URL", "")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

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
    MAX_CONTENT_LENGTH = MAX_REQUEST_BYTES

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
        missing = [name for name in REQUIRED_PRODUCTION_SECRETS if not os.environ.get(name)]
        if missing:
            raise ConfigError(
                f"Refusing to start: missing required secret(s) for OWNER_ENV={cls.ENV!r}: {', '.join(missing)}"
            )
        if cls.EXTERNAL_API_ENABLED:
            cls.validate_external_api_production()

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
