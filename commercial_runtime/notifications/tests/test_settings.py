import sqlite3

import pytest

from commercial_runtime.notifications import settings
from commercial_runtime.notifications.schema import apply_notifications_schema


@pytest.fixture
def conn():
    c = sqlite3.connect(':memory:')
    apply_notifications_schema(c)
    return c


def test_default_enabled_is_off(conn):
    assert settings.get_setting(conn, 1, 'enabled') == '0'


def test_get_all_settings_returns_defaults_when_nothing_stored(conn):
    assert settings.get_all_settings(conn, 1) == settings.DEFAULTS


def test_set_then_get_setting_round_trips(conn):
    settings.set_setting(conn, 1, 'low_stock_recipient', 'owner@shop.test')
    assert settings.get_setting(conn, 1, 'low_stock_recipient') == 'owner@shop.test'
    # unrelated company unaffected
    assert settings.get_setting(conn, 2, 'low_stock_recipient') == ''


def test_unknown_setting_key_rejected_on_read(conn):
    with pytest.raises(settings.UnknownSettingError):
        settings.get_setting(conn, 1, 'not_a_real_setting')


def test_unknown_setting_key_rejected_on_write(conn):
    with pytest.raises(settings.UnknownSettingError):
        settings.set_setting(conn, 1, 'not_a_real_setting', 'x')


def test_recipient_for_returns_none_when_blank(conn):
    assert settings.recipient_for(conn, 1, 'low_stock_recipient') is None
    settings.set_setting(conn, 1, 'low_stock_recipient', 'a@b.com')
    assert settings.recipient_for(conn, 1, 'low_stock_recipient') == 'a@b.com'


# ── is_enabled precedence ────────────────────────────────────────────────

def test_is_enabled_false_when_smtp_host_unset_even_if_company_opted_in(conn, monkeypatch):
    monkeypatch.delenv('AURA_SMTP_HOST', raising=False)
    settings.set_setting(conn, 1, 'enabled', '1')
    assert settings.is_enabled(conn, 1) is False


def test_is_enabled_false_when_company_has_not_opted_in_even_with_smtp_configured(conn, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.com')
    assert settings.is_enabled(conn, 1) is False  # default enabled='0'


def test_is_enabled_true_when_both_layers_permit(conn, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.com')
    settings.set_setting(conn, 1, 'enabled', '1')
    assert settings.is_enabled(conn, 1) is True


def test_is_enabled_scoped_per_company(conn, monkeypatch):
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.com')
    settings.set_setting(conn, 1, 'enabled', '1')
    assert settings.is_enabled(conn, 1) is True
    assert settings.is_enabled(conn, 2) is False


# ── numeric setting validation (AUDIT: HIGH -- max_attempts/
#    submit_interval_seconds were persisted with zero validation that they
#    were numeric, and every reader does a bare int() cast; a bad value
#    wedged the outbox worker forever and crashed init_app() on restart) ──

@pytest.mark.parametrize('key', ['max_attempts', 'submit_interval_seconds'])
@pytest.mark.parametrize('bad_value', ['', 'abc', None, '1.5', '0', '-3'])
def test_numeric_setting_rejects_non_positive_integer_on_write(conn, key, bad_value):
    with pytest.raises(settings.InvalidSettingValueError):
        settings.set_setting(conn, 1, key, bad_value)
    # rejected write must not have persisted -- the reader still sees the
    # documented default, never the bad value.
    assert settings.get_setting(conn, 1, key) == settings.DEFAULTS[key]


@pytest.mark.parametrize('key', ['max_attempts', 'submit_interval_seconds'])
def test_numeric_setting_accepts_positive_integer_on_write(conn, key):
    settings.set_setting(conn, 1, key, '30')
    assert settings.get_setting(conn, 1, key) == '30'
    # value must always be int()-castable by downstream callers
    # (worker.py::_apply_retry, app.py::_resume_notifications_workers).
    assert int(settings.get_setting(conn, 1, key)) == 30


def test_garbage_enabled_value_fails_closed(conn, monkeypatch):
    """Only the literal '1' turns the feature on -- matches
    einvoicing/settings.py's identical fail-closed guarantee."""
    monkeypatch.setenv('AURA_SMTP_HOST', 'smtp.example.com')
    conn.execute(
        "INSERT INTO email_settings (company_id, skey, svalue, updated_at) VALUES (1, 'enabled', 'yes-please', '2026-01-01')"
    )
    assert settings.is_enabled(conn, 1) is False
