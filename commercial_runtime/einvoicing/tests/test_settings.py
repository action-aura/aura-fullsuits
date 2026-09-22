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


def test_default_enabled_key_is_on_but_feature_resolves_off(conn):
    """AUDIT 2026-09-22, second rename of this test (it was previously
    renamed from test_default_enabled_is_off to test_default_enabled_is_on
    when Jordan's mandate flipped DEFAULTS['enabled'] from '0' to '1' -- see
    that history below, still true). This wave's change is different: it is
    NOT about the `enabled` key (still '1', asserted below, unchanged), it is
    about DEFAULTS['tax_regime'] flipping from 'jordan' to 'none' (the
    owner's opt-in decision -- see settings.py's DEFAULTS comment). `enabled`
    is meaningless outside a regime (is_enabled() does not even read it once
    the regime layer has already resolved off), so a fresh company -- `enabled`
    defaulting ON but `tax_regime` defaulting to 'none' -- must now resolve
    the FEATURE off even though the toggle itself still reads '1'. This test
    alone cannot prove a Jordanian shop still gets to say yes -- that is
    test_tax_regime_default.py's
    test_explicit_jordan_after_the_flip_turns_it_back_on, and this file's own
    test_env_var_still_disables_under_the_jordan_regime /
    test_killswitch_still_disables_under_the_jordan_regime /
    test_per_company_enabled_zero_still_disables_under_the_jordan_regime,
    which prove the other three layers still each independently win once a
    company HAS opted into the 'jordan' regime -- that pairing is what a bare
    "is it enabled" assertion here cannot provide on its own.

    The pre-2026-09-22 history, kept because it is still accurate about the
    `enabled` key specifically: Jordan has mandated e-invoicing since
    2024-05-31, so DEFAULTS['enabled'] flipped from '0' to '1' -- a fresh
    company records that obligation on the `enabled` key without anyone
    touching a toggle. That part of the story is unchanged by this wave;
    only the regime layer above it now gates whether it actually matters."""
    assert settings.get_setting(conn, company_id=1, key='enabled') == '1'
    assert settings.is_enabled(conn, app_data_dir='/tmp/unused', company_id=1) is False, (
        "a fresh company's `enabled` key still defaults to '1', but the feature must "
        "resolve OFF overall because tax_regime now defaults to 'none' -- 'enabled' "
        "alone no longer turns filing on"
    )


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
    """AUDIT 2026-09-22: this test predates the tax_regime layer (it is named
    for the ORIGINAL three: env var, killswitch, enabled) and used to get a
    permitting regime for free from DEFAULTS['tax_regime']=='jordan'. Now that
    the default is 'none', "all layers permit" requires an explicit
    tax_regime='jordan' write too, or this would just be re-proving
    test_default_tax_regime_is_none_and_feature_resolves_disabled by
    accident. Content fix, not a weakened test -- the claim (env var absent +
    killswitch absent + enabled='1' + a real regime => enabled) is unchanged;
    only the fixture had to start saying what it used to get for free."""
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    settings.set_setting(conn, 1, 'tax_regime', 'jordan')
    settings.set_setting(conn, 1, 'enabled', '1')
    assert settings.is_enabled(conn, str(tmp_path), 1) is True


def test_garbage_enabled_value_fails_closed(conn, tmp_path, monkeypatch):
    """Only the literal '1' turns the feature on -- any other stored value
    (corruption, a future migration writing something unexpected) must be
    treated as OFF, never as an ambiguous truthy string.

    AUDIT 2026-09-22: this test must explicitly opt into tax_regime='jordan'
    first. Without it, DEFAULTS['tax_regime']=='none' would already resolve
    is_enabled() False at layer 3, BEFORE the garbage 'enabled' row below is
    ever read -- the assertion would still pass, but only by accident, and
    would no longer be proving that a garbage `enabled` value fails closed at
    all -- a "test still green, guard never exercised" trap: the pass
    condition would be indistinguishable from the guard being broken.
    Setting tax_regime='jordan' first forces is_enabled() to actually reach
    the `enabled` check this test is about."""
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    settings.set_setting(conn, 1, 'tax_regime', 'jordan')
    conn.execute(
        "INSERT INTO einvoice_settings (company_id, skey, svalue, updated_at) VALUES (1, 'enabled', 'yes-please', '2026-01-01')"
    )
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


# ─── tax_regime: the fourth disable layer ───────────────────────────────
#
# AUDIT 2026-09-22: DEFAULTS['tax_regime'] flipped from 'jordan' to 'none'
# (the owner's decision -- see settings.py's DEFAULTS comment for the full
# history and why the old default never actually preserved compliance).
# This section used to pin the opposite of both cases below; both are now
# updated to match.
#
# Two cases that still look alike and are still NOT the same thing: a
# MISSING tax_regime key falls back through get_setting() to
# DEFAULTS['tax_regime'] == 'none' (a fresh install, or any install that
# never had evidence of prior use at migration time, files with nobody by
# default), while a PRESENT-but-unrecognised value is not trusted as an
# opt-in to any regime and also resolves off -- same outcome today, but for
# a different reason, and the ABSENT case is the one a future default change
# would move. Both directions are pinned below.


def test_default_tax_regime_is_none_and_feature_resolves_disabled(conn, tmp_path, monkeypatch):
    """AUDIT 2026-09-22: renamed from
    test_default_tax_regime_is_jordan_and_feature_resolves_enabled, which
    asserted the exact opposite of both lines below -- that was correct for
    the OLD default and is now a stale claim, not a true one; see settings.py
    DEFAULTS['tax_regime']'s comment for why the owner reversed it (Jordan's
    default never actually preserved compliance -- direct_istd.py cannot
    submit -- so it only minted numbers and outbox rows nobody could file).

    THIS IS NOW THE NO-SURPRISE-FILING CASE, and the most important test in
    this file for the opposite reason the old one was: a company with no
    einvoice_settings row at all -- a fresh install, or any install with no
    evidence of prior use at the moment it upgraded past this change -- must
    read 'none' and must resolve DISABLED. If this ever goes red back to
    'jordan'/True, a shipped patch has started filing (or at least minting
    gapless numbers and outbox rows) for a shop the owner never turned it on
    for. The complementary claim -- that a shop already using the feature
    keeps working -- is proven separately in test_tax_regime_default.py,
    against schema.py's preservation migration, not here."""
    monkeypatch.delenv('AURA_EINVOICING_DISABLED', raising=False)
    assert settings.get_setting(conn, company_id=1, key='tax_regime') == 'none'
    assert settings.is_enabled(conn, str(tmp_path), 1) is False


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
    company is untouched -- written as the literal 'jordan'/'none', not
    settings.DEFAULTS['tax_regime'], so this can still catch the default
    drifting (same reasoning as test_set_then_get_setting_round_trips).
    AUDIT 2026-09-22: company 2's expected fallback flipped from 'jordan' to
    'none' along with DEFAULTS['tax_regime'] itself -- see settings.py's
    DEFAULTS comment. Company 1 is set to 'jordan' here (the opposite of
    company 2's untouched default) specifically so this test still exercises
    a real round trip either direction, not just two reads of the same
    value."""
    settings.set_setting(conn, 1, 'tax_regime', 'jordan')
    assert settings.get_setting(conn, 1, 'tax_regime') == 'jordan'
    assert settings.get_setting(conn, 2, 'tax_regime') == 'none'
