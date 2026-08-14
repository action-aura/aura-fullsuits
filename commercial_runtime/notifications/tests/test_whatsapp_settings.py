import sqlite3

import pytest

from commercial_runtime.notifications import whatsapp_settings
from commercial_runtime.notifications.schema import apply_notifications_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    apply_notifications_schema(c)
    return c


def test_default_enabled_is_off(conn):
    assert whatsapp_settings.get_setting(conn, 1, 'enabled') == '0'


def test_get_all_settings_returns_defaults_when_nothing_stored(conn):
    assert whatsapp_settings.get_all_settings(conn, 1) == whatsapp_settings.DEFAULTS


def test_set_then_get_setting_round_trips(conn):
    whatsapp_settings.set_setting(conn, 1, 'low_stock_recipient_phone', '+15551234567')
    assert whatsapp_settings.get_setting(conn, 1, 'low_stock_recipient_phone') == '+15551234567'
    # unrelated company unaffected
    assert whatsapp_settings.get_setting(conn, 2, 'low_stock_recipient_phone') == ''


def test_unknown_setting_key_rejected_on_read(conn):
    with pytest.raises(whatsapp_settings.UnknownSettingError):
        whatsapp_settings.get_setting(conn, 1, 'not_a_real_setting')


def test_unknown_setting_key_rejected_on_write(conn):
    with pytest.raises(whatsapp_settings.UnknownSettingError):
        whatsapp_settings.set_setting(conn, 1, 'not_a_real_setting', 'x')


def test_recipient_phone_for_returns_none_when_blank(conn):
    assert whatsapp_settings.recipient_phone_for(conn, 1, 'low_stock_recipient_phone') is None
    whatsapp_settings.set_setting(conn, 1, 'low_stock_recipient_phone', '+15551234567')
    assert whatsapp_settings.recipient_phone_for(conn, 1, 'low_stock_recipient_phone') == '+15551234567'


# ── is_enabled precedence ────────────────────────────────────────────────

def test_is_enabled_false_when_phone_number_id_unset_even_if_company_opted_in(conn, monkeypatch):
    monkeypatch.delenv('AURA_WHATSAPP_PHONE_NUMBER_ID', raising=False)
    whatsapp_settings.set_setting(conn, 1, 'enabled', '1')
    assert whatsapp_settings.is_enabled(conn, 1) is False


def test_is_enabled_false_when_company_has_not_opted_in_even_with_transport_configured(conn, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '123456789')
    assert whatsapp_settings.is_enabled(conn, 1) is False  # default enabled='0'


def test_is_enabled_true_when_both_layers_permit(conn, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '123456789')
    whatsapp_settings.set_setting(conn, 1, 'enabled', '1')
    assert whatsapp_settings.is_enabled(conn, 1) is True


def test_is_enabled_scoped_per_company(conn, monkeypatch):
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '123456789')
    whatsapp_settings.set_setting(conn, 1, 'enabled', '1')
    assert whatsapp_settings.is_enabled(conn, 1) is True
    assert whatsapp_settings.is_enabled(conn, 2) is False


# ── numeric setting validation (mirrors email's own settings.py -- same
#    real gap, fixed here from the start instead of reintroduced) ──────────

@pytest.mark.parametrize('key', ['max_attempts', 'submit_interval_seconds'])
@pytest.mark.parametrize('bad_value', ['', 'abc', None, '1.5', '0', '-3'])
def test_numeric_setting_rejects_non_positive_integer_on_write(conn, key, bad_value):
    with pytest.raises(whatsapp_settings.InvalidSettingValueError):
        whatsapp_settings.set_setting(conn, 1, key, bad_value)
    assert whatsapp_settings.get_setting(conn, 1, key) == whatsapp_settings.DEFAULTS[key]


@pytest.mark.parametrize('key', ['max_attempts', 'submit_interval_seconds'])
def test_numeric_setting_accepts_positive_integer_on_write(conn, key):
    whatsapp_settings.set_setting(conn, 1, key, '30')
    assert whatsapp_settings.get_setting(conn, 1, key) == '30'
    assert int(whatsapp_settings.get_setting(conn, 1, key)) == 30


def test_garbage_enabled_value_fails_closed(conn, monkeypatch):
    """Only the literal '1' turns the feature on -- matches email/
    einvoicing's identical fail-closed guarantee."""
    monkeypatch.setenv('AURA_WHATSAPP_PHONE_NUMBER_ID', '123456789')
    conn.execute(
        "INSERT INTO whatsapp_settings (company_id, skey, svalue, updated_at) VALUES (1, 'enabled', 'yes-please', '2026-01-01')"
    )
    assert whatsapp_settings.is_enabled(conn, 1) is False
