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
    prove the four disable layers still each independently win over this
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


# ─── tax_regime: the fourth disable layer ───────────────────────────────
#
# Two cases that look alike and are NOT: a MISSING tax_regime key falls back
# through get_setting() to DEFAULTS['tax_regime'] == 'jordan' (an existing
# Jordanian install that never touched settings must stay compliant), while a
# PRESENT-but-unrecognised value is not trusted as an opt-in to any regime and
# resolves off. Both directions are pinned below.


def test_default_tax_regime_is_jordan_and_feature_resolves_enabled(conn, tmp_path, monkeypatch):
    """THE NO-REGRESSION-FOR-JORDAN CASE, and the most important test in this
    file. A company with no einvoice_settings row at all -- every install that
    exists today -- must read 'jordan' and must still resolve ENABLED. If this
    ever goes red, a shipped patch has switched a live legal obligation off."""
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    assert settings.get_setting(conn, company_id=1, key='tax_regime') == 'jordan'
    assert settings.is_enabled(conn, str(tmp_path), 1) is True


def test_tax_regime_none_forces_the_feature_off_even_with_enabled_stored(conn, tmp_path, monkeypatch):
    """'none' is not a pause. With enabled explicitly '1' -- the loudest
    possible statement that the shop wants this on -- a shop that files with
    no tax authority still resolves off."""
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    settings.set_setting(conn, 1, 'enabled', '1')
    settings.set_setting(conn, 1, 'tax_regime', 'none')
    assert settings.get_setting(conn, 1, 'enabled') == '1', "precondition: the toggle really is on"
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


def test_garbage_tax_regime_fails_closed_and_never_auto_enables(conn, tmp_path, monkeypatch):
    """A present-but-unrecognised regime -- an operator typo through
    POST /api/einvoicing/settings (routes.py stores the string without
    validating it), a corrupt row, a value from a future build -- is NOT
    evidence that Jordan's mandate applies. Only the exact literal 'jordan'
    is. This is the case DEFAULTS' fallback deliberately does NOT cover."""
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    settings.set_setting(conn, 1, 'enabled', '1')
    for garbage in ('Jordan', 'JORDAN', 'jordan ', 'uae', 'yes-please', ''):
        settings.set_setting(conn, 1, 'tax_regime', garbage)
        assert settings.is_enabled(conn, str(tmp_path), 1) is False, (
            f"an unrecognised tax_regime {garbage!r} resolved the feature ON"
        )


def test_env_var_still_disables_under_the_jordan_regime(conn, tmp_path, monkeypatch):
    """Layer 1 unbroken by the new layer 3."""
    settings.set_setting(conn, 1, 'tax_regime', 'jordan')
    settings.set_setting(conn, 1, 'enabled', '1')
    monkeypatch.setenv('AURA_EINVOICING_DISABLED', '1')
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


def test_killswitch_still_disables_under_the_jordan_regime(conn, tmp_path, monkeypatch):
    """Layer 2 unbroken by the new layer 3."""
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    settings.set_setting(conn, 1, 'tax_regime', 'jordan')
    settings.set_setting(conn, 1, 'enabled', '1')
    killswitch.set_disabled(str(tmp_path), reason='regime-regression-drill')
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


def test_per_company_enabled_zero_still_disables_under_the_jordan_regime(conn, tmp_path, monkeypatch):
    """Layer 4 unbroken by the new layer 3 -- a Jordanian shop that explicitly
    turned the feature off must stay off."""
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    settings.set_setting(conn, 1, 'tax_regime', 'jordan')
    settings.set_setting(conn, 1, 'enabled', '0')
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


def test_tax_regime_is_checked_before_enabled_and_short_circuits(conn, tmp_path, monkeypatch):
    """The ORDER of layers 3 and 4, which no input/output assertion can prove:
    both are ANDs, so swapping them yields an identical truth table. What IS
    observable is the short circuit -- with no regime, `enabled` must never be
    read at all. Spy on get_setting and assert on the keys it was asked for."""
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    settings.set_setting(conn, 1, 'tax_regime', 'none')
    settings.set_setting(conn, 1, 'enabled', '1')

    real_get_setting = settings.get_setting
    keys_read = []

    def _spy(c, cid, key):
        keys_read.append(key)
        return real_get_setting(c, cid, key)

    monkeypatch.setattr(settings, 'get_setting', _spy)
    assert settings.is_enabled(conn, str(tmp_path), 1) is False
    assert 'tax_regime' in keys_read, "the regime layer did not run at all"
    assert 'enabled' not in keys_read, (
        "is_enabled() read the `enabled` toggle after the regime had already "
        "resolved the feature off -- the regime check is no longer ordered "
        "ahead of it"
    )


def test_tax_regime_round_trips_through_set_setting(conn):
    """The key is a first-class setting: writable, readable, and an unrelated
    company is untouched -- written as the literal 'jordan', not
    settings.DEFAULTS['tax_regime'], so this can still catch the default
    drifting (same reasoning as test_set_then_get_setting_round_trips)."""
    settings.set_setting(conn, 1, 'tax_regime', 'none')
    assert settings.get_setting(conn, 1, 'tax_regime') == 'none'
    assert settings.get_setting(conn, 2, 'tax_regime') == 'jordan'
