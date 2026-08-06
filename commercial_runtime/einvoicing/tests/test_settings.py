import os
import sqlite3

import pytest

from commercial_runtime.einvoicing import killswitch, settings
from commercial_runtime.einvoicing.schema import apply_einvoicing_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    apply_einvoicing_schema(c)
    return c


def test_default_enabled_is_off(conn):
    assert settings.get_setting(conn, company_id=1, key='enabled') == '0'
    assert settings.is_enabled(conn, app_data_dir='/tmp/unused', company_id=1) is False


def test_get_all_settings_returns_defaults_when_nothing_stored(conn):
    all_settings = settings.get_all_settings(conn, company_id=1)
    assert all_settings == settings.DEFAULTS


def test_set_then_get_setting_round_trips(conn):
    settings.set_setting(conn, 1, 'provider', 'direct_istd')
    assert settings.get_setting(conn, 1, 'provider') == 'direct_istd'
    # unrelated company unaffected
    assert settings.get_setting(conn, 2, 'provider') == 'mock'


def test_unknown_setting_key_rejected_on_read(conn):
    with pytest.raises(settings.UnknownSettingError):
        settings.get_setting(conn, 1, 'not_a_real_setting')


def test_unknown_setting_key_rejected_on_write(conn):
    with pytest.raises(settings.UnknownSettingError):
        settings.set_setting(conn, 1, 'not_a_real_setting', 'x')


def test_enabling_stamps_enabled_at_once(conn):
    assert settings.enabled_at(conn, 1) is None
    settings.set_setting(conn, 1, 'enabled', '1')
    first_stamp = settings.enabled_at(conn, 1)
    assert first_stamp

    settings.set_setting(conn, 1, 'enabled', '0')
    settings.set_setting(conn, 1, 'enabled', '1')
    assert settings.enabled_at(conn, 1) == first_stamp, "re-enabling must not move the original enabled_at forward"


def test_precedence_env_var_wins_over_everything(conn, tmp_path, monkeypatch):
    settings.set_setting(conn, 1, 'enabled', '1')
    monkeypatch.setenv('AURA_EINVOICING_DISABLED', '1')
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


def test_precedence_killswitch_wins_over_db_setting(conn, tmp_path, monkeypatch):
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    settings.set_setting(conn, 1, 'enabled', '1')
    killswitch.set_disabled(str(tmp_path), reason='ops-pause')
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


def test_enabled_when_all_three_layers_permit(conn, tmp_path, monkeypatch):
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    settings.set_setting(conn, 1, 'enabled', '1')
    assert settings.is_enabled(conn, str(tmp_path), 1) is True


def test_garbage_enabled_value_fails_closed(conn, tmp_path, monkeypatch):
    """Only the literal '1' turns the feature on -- any other stored value
    (corruption, a future migration writing something unexpected) must be
    treated as OFF, never as an ambiguous truthy string."""
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    conn.execute(
        "INSERT INTO einvoice_settings (company_id, skey, svalue, updated_at) VALUES (1, 'enabled', 'yes-please', '2026-01-01')"
    )
    assert settings.is_enabled(conn, str(tmp_path), 1) is False
