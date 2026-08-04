"""Phase 9R M2 -- environment-separation fail-closed config validation.

BaseConfig.validate() must refuse to start (raise ConfigError) in staging/
production for every failure mode M2 lists. Config classes bake their
attributes at class-body-execution time (see test_security_headers.py's own
note on this), so rather than fighting os.environ re-import timing, these
tests build a minimal valid staging config as a dynamic subclass and mutate
exactly one attribute per test -- the same technique that isolates each
failure mode cleanly regardless of import order.
"""
from __future__ import annotations

import pytest

from app.config import BaseConfig, ConfigError


def _valid_staging_config(**overrides):
    """A dynamic BaseConfig subclass with every M2 requirement satisfied --
    the control case every failure-mode test starts from and mutates."""
    attrs = {
        "ENV": "staging",
        "TESTING": False,
        "DEBUG": False,
        "SECRET_KEY": "a" * 40,
        "LICENSE_PEPPER": "b" * 40,
        "SQLALCHEMY_DATABASE_URI": "postgresql+psycopg://aura_owner:realpass@db.internal:5432/aura_owner_staging",
        "SESSION_COOKIE_SECURE": True,
        "TRUSTED_PROXY_COUNT": 1,
        "ALLOWED_HOSTS": ("owner-staging.example.com",),
        "BACKUP_TARGET_URL": "s3://aura-owner-backups/staging/",
        "SCHEDULER_ROLE": "owner",
        "STRICT_CONFIG": False,
        "EXTERNAL_API_ENABLED": False,
    }
    attrs.update(overrides)
    return type("DynamicStagingConfig", (BaseConfig,), attrs)


class TestValidConfigPasses:
    def test_fully_valid_staging_config_does_not_raise(self):
        _valid_staging_config().validate()


class TestMissingOrWeakSecrets:
    def test_missing_secret_key_blocks_start(self):
        # validate() resolves the class attribute, not raw os.environ --
        # correct for a subclass that computes SECRET_KEY by any means
        # other than a 1:1 env-var passthrough (the real bug this fixed:
        # the original check read os.environ.get() directly while every
        # other check in validate() reads the resolved cls.* attribute).
        cfg = _valid_staging_config(SECRET_KEY="")
        with pytest.raises(ConfigError, match="missing required secret"):
            cfg.validate()

    def test_missing_database_url_blocks_start(self):
        cfg = _valid_staging_config(SQLALCHEMY_DATABASE_URI="")
        with pytest.raises(ConfigError, match="missing required secret"):
            cfg.validate()

    def test_missing_license_pepper_blocks_start(self):
        cfg = _valid_staging_config(LICENSE_PEPPER="")
        with pytest.raises(ConfigError, match="missing required secret"):
            cfg.validate()

    def test_default_placeholder_secret_key_rejected(self):
        cfg = _valid_staging_config(SECRET_KEY="dev-only-insecure-key-do-not-use-in-production" + "x" * 10)
        with pytest.raises(ConfigError, match="OWNER_SECRET_KEY"):
            cfg.validate()

    def test_short_secret_key_rejected(self):
        cfg = _valid_staging_config(SECRET_KEY="short")
        with pytest.raises(ConfigError, match="OWNER_SECRET_KEY"):
            cfg.validate()

    def test_weak_license_pepper_rejected(self):
        cfg = _valid_staging_config(LICENSE_PEPPER="test-license-pepper" + "x" * 20)
        with pytest.raises(ConfigError, match="OWNER_LICENSE_PEPPER"):
            cfg.validate()


class TestDatabaseUrl:
    def test_malformed_database_url_rejected(self):
        cfg = _valid_staging_config(SQLALCHEMY_DATABASE_URI="not-a-url-at-all")
        with pytest.raises(ConfigError, match="not a recognized PostgreSQL URL"):
            cfg.validate()

    def test_empty_database_url_is_caught_as_a_missing_secret_not_malformed(self):
        # Empty is legitimately "missing" (caught by the fail-fast secret
        # check before the malformed-URL check even runs) -- correct
        # behavior, not a gap: an empty string is never a valid URL to
        # report as merely "malformed."
        cfg = _valid_staging_config(SQLALCHEMY_DATABASE_URI="")
        with pytest.raises(ConfigError, match="missing required secret"):
            cfg.validate()

    def test_dev_database_url_rejected_in_staging(self):
        cfg = _valid_staging_config(
            SQLALCHEMY_DATABASE_URI="postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_dev"
        )
        with pytest.raises(ConfigError, match="development or test database"):
            cfg.validate()

    def test_test_database_url_rejected_in_staging(self):
        cfg = _valid_staging_config(
            SQLALCHEMY_DATABASE_URI="postgresql+psycopg://aura_owner:x@dbhost:5432/aura_owner_test"
        )
        with pytest.raises(ConfigError, match="development or test database"):
            cfg.validate()

    def test_colocated_localhost_production_database_is_not_penalized(self):
        # ADR-3 (docs/owner/phase9r/architecture-decision-record.md): a
        # self-managed PostgreSQL on the same host as the app is a legitimate
        # production topology, not a misconfiguration -- only the *database
        # name* markers (aura_owner_dev/test), never "localhost", may trigger
        # this check.
        cfg = _valid_staging_config(
            SQLALCHEMY_DATABASE_URI="postgresql+psycopg://aura_owner:realpass@localhost:5432/aura_owner_staging"
        )
        cfg.validate()  # must not raise


class TestCookieSecurity:
    def test_insecure_cookie_rejected_in_staging(self):
        cfg = _valid_staging_config(SESSION_COOKIE_SECURE=False)
        with pytest.raises(ConfigError, match="SESSION_COOKIE_SECURE"):
            cfg.validate()


class TestTrustedProxyAndHostAllowlist:
    def test_missing_trusted_proxy_config_rejected(self):
        cfg = _valid_staging_config(TRUSTED_PROXY_COUNT=0)
        with pytest.raises(ConfigError, match="OWNER_TRUSTED_PROXY_COUNT"):
            cfg.validate()

    def test_missing_host_allowlist_rejected(self):
        cfg = _valid_staging_config(ALLOWED_HOSTS=())
        with pytest.raises(ConfigError, match="OWNER_ALLOWED_HOSTS"):
            cfg.validate()


class TestBackupTarget:
    def test_missing_backup_target_rejected(self):
        cfg = _valid_staging_config(BACKUP_TARGET_URL="")
        with pytest.raises(ConfigError, match="OWNER_BACKUP_TARGET_URL"):
            cfg.validate()

    def test_local_path_backup_target_rejected(self):
        cfg = _valid_staging_config(BACKUP_TARGET_URL=r"C:\backups\owner")
        with pytest.raises(ConfigError, match="local filesystem path"):
            cfg.validate()

    def test_relative_path_backup_target_rejected(self):
        cfg = _valid_staging_config(BACKUP_TARGET_URL="var/backups")
        with pytest.raises(ConfigError, match="local filesystem path"):
            cfg.validate()


class TestSchedulerOwnership:
    def test_invalid_scheduler_role_rejected(self):
        cfg = _valid_staging_config(SCHEDULER_ROLE="both")
        with pytest.raises(ConfigError, match="OWNER_SCHEDULER_ROLE"):
            cfg.validate()

    def test_empty_scheduler_role_rejected(self):
        cfg = _valid_staging_config(SCHEDULER_ROLE="")
        with pytest.raises(ConfigError, match="OWNER_SCHEDULER_ROLE"):
            cfg.validate()

    def test_explicit_worker_role_is_valid(self):
        _valid_staging_config(SCHEDULER_ROLE="worker").validate()  # must not raise


class TestUnknownConfigKey:
    def test_unknown_owner_env_var_rejected_when_strict(self, monkeypatch):
        monkeypatch.setenv("OWNER_SESION_IDLE_SECONDS", "60")  # typo'd, missing an S
        cfg = _valid_staging_config(STRICT_CONFIG=True)
        with pytest.raises(ConfigError, match="OWNER_SESION_IDLE_SECONDS"):
            cfg.validate()

    def test_unknown_owner_env_var_allowed_when_not_strict(self, monkeypatch):
        monkeypatch.setenv("OWNER_SESION_IDLE_SECONDS", "60")
        _valid_staging_config(STRICT_CONFIG=False).validate()  # must not raise

    def test_known_config_keys_never_flagged_when_strict(self, monkeypatch):
        monkeypatch.setenv("OWNER_LOGIN_MAX_ATTEMPTS", "3")
        _valid_staging_config(STRICT_CONFIG=True).validate()  # must not raise


class TestDevelopmentAndTestingAreExempt:
    def test_development_env_skips_all_validation(self):
        cfg = _valid_staging_config(ENV="development", SECRET_KEY="", LICENSE_PEPPER="", SQLALCHEMY_DATABASE_URI="")
        cfg.validate()  # must not raise -- development is exempt by design

    def test_testing_flag_skips_all_validation(self):
        cfg = _valid_staging_config(ENV="staging", TESTING=True, SECRET_KEY="", LICENSE_PEPPER="")
        cfg.validate()  # must not raise -- TESTING=True is exempt by design


class TestDebugInProduction:
    def test_debug_true_combined_with_other_problems_still_reported(self):
        # DEBUG is validated by validate_external_api_production() when the
        # external API is enabled; verify the two validation layers compose
        # rather than one silently masking the other.
        cfg = _valid_staging_config(DEBUG=True, EXTERNAL_API_ENABLED=True)
        with pytest.raises(ConfigError, match="DEBUG must be false"):
            cfg.validate()
