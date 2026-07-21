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

    WTF_CSRF_TIME_LIMIT = None

    @classmethod
    def validate(cls) -> None:
        if cls.ENV == "development" or cls.TESTING:
            return
        missing = [name for name in REQUIRED_PRODUCTION_SECRETS if not os.environ.get(name)]
        if missing:
            raise ConfigError(
                f"Refusing to start: missing required secret(s) for OWNER_ENV={cls.ENV!r}: {', '.join(missing)}"
            )


class DevelopmentConfig(BaseConfig):
    ENV = "development"
    DEBUG = True
    SECRET_KEY = os.environ.get("OWNER_SECRET_KEY", "dev-only-insecure-key-do-not-use-in-production")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "OWNER_DATABASE_URL", "postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_dev"
    )
    LICENSE_PEPPER = os.environ.get("OWNER_LICENSE_PEPPER", "dev-only-insecure-pepper-do-not-use-in-production")


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


class ProductionConfig(BaseConfig):
    ENV = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = True


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None = None):
    name = name or os.environ.get("OWNER_ENV", "development")
    return CONFIG_MAP.get(name, DevelopmentConfig)
