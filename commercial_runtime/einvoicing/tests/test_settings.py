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


def test_default_enabled_is_on(conn):
    """AUDIT: renamed from test_default_enabled_is_off. Jordan has mandated
    e-invoicing since 2024-05-31, so DEFAULTS['enabled'] flipped from '0'
    to '1' -- a fresh company now records the obligation without anyone
    touching a toggle (see this module's own docstring at the top of
    settings.py). This test alone can no longer catch an accidental flip
    BACK to a disabled default -- it must be paired with
    commercial_runtime/einvoicing/tests/test_default_on_and_unconfigured.py's
    test_env_var_disable_still_beats_the_new_default,
    test_killswitch_disable_still_beats_the_new_default and
    test_explicit_per_company_disable_still_beats_the_new_default, which
    prove the three disable layers still each independently win over this
    new default -- that pairing is what a bare "is it on" assertion here
    cannot provide on its own."""
    assert settings.get_setting(conn, company_id=1, key='enabled') == '1'
    assert settings.is_enabled(conn, app_data_dir='/tmp/unused', company_id=1) is True


def test_get_all_settings_returns_defaults_when_nothing_stored(conn):
    all_settings = settings.get_all_settings(conn, company_id=1)
    assert all_settings == settings.DEFAULTS


def test_set_then_get_setting_round_trips(conn):
    settings.set_setting(conn, 1, 'provider', 'direct_istd')
    assert settings.get_setting(conn, 1, 'provider') == 'direct_istd'
    # Unrelated company unaffected -- still resolves to the DEFAULT provider.
    # Written as the literal rather than settings.DEFAULTS['provider'] on
    # purpose: comparing the code against itself could no longer catch the
    # default drifting. It changed from 'mock' to 'unconfigured' on 2026-09-08
    # when the feature became enabled-by-default, because a shipped install
    # runs UnconfiguredProvider and a row stamped 'mock' would have claimed a
    # connection to a fake JoFotara that is not wired in.
    assert settings.get_setting(conn, 2, 'provider') == 'unconfigured'


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
