"""OWNER-SEC item 6 (review D SEC-02): TOTP secrets used to be sealed under a
key derived from OWNER_SECRET_KEY alone, so rotating that key bricked every
MFA user. A dedicated OWNER_MFA_ENCRYPTION_KEY is now optional; unset keeps
the legacy derivation, set tries the dedicated key then the legacy one, and a
successful use / `flask rekey-mfa` moves secrets onto the dedicated key."""
from __future__ import annotations

from tests.conftest import TOTP_SECRET, login_and_verify_mfa, make_staff

MFA_KEY = "dedicated-mfa-key-0123456789-abcdefghijklmnopqrstuvwxyz"
ROTATED_APP_KEY = "rotated-app-secret-0123456789-abcdefghijklmnopqrstuvwxyz"


def _stored(app, staff_id):
    from app.extensions import db_session
    from app.models.staff import StaffUser

    with app.app_context():
        db_session.expire_all()
        return db_session.get(StaffUser, staff_id).mfa_credential.totp_secret_encrypted


def _reaches_dashboard(resp) -> bool:
    return resp.status_code == 302 and "/auth/mfa-verify" not in resp.headers["Location"] and "/auth/login" not in resp.headers["Location"]


def test_unset_dedicated_key_keeps_legacy_behaviour(app):
    sid = make_staff(app, "k0@example.com", mfa=True)
    app_key = app.config["SECRET_KEY"]
    from app.security.mfa import decrypt_totp_secret, encrypt_totp_secret

    assert not app.config["MFA_ENCRYPTION_KEY"]
    sealed = encrypt_totp_secret(TOTP_SECRET, app_key)
    assert decrypt_totp_secret(sealed, app_key) == TOTP_SECRET
    assert _stored(app, sid) != ""
    assert decrypt_totp_secret(_stored(app, sid), app_key) == TOTP_SECRET


def test_legacy_secret_still_verifies_once_dedicated_key_is_set_and_upgrades(app, client):
    sid = make_staff(app, "k1@example.com", mfa=True)  # sealed under the legacy key
    legacy_ct = _stored(app, sid)
    app.config["MFA_ENCRYPTION_KEY"] = MFA_KEY
    resp = login_and_verify_mfa(client, "k1@example.com")
    assert _reaches_dashboard(resp)

    from app.security.mfa import _try_decrypt

    upgraded = _stored(app, sid)
    assert upgraded != legacy_ct
    assert _try_decrypt(upgraded, MFA_KEY) == TOTP_SECRET            # now on the dedicated key
    assert _try_decrypt(upgraded, app.config["SECRET_KEY"]) is None  # and no longer on the legacy one


def test_after_rekey_rotating_the_app_key_no_longer_breaks_mfa(app):
    sid = make_staff(app, "k2@example.com", mfa=True)
    app.config["MFA_ENCRYPTION_KEY"] = MFA_KEY
    runner = app.test_cli_runner()
    result = runner.invoke(args=["rekey-mfa"])
    assert result.exit_code == 0, result.output
    assert "Re-encrypted 1;" in result.output

    app.config["SECRET_KEY"] = ROTATED_APP_KEY  # the rotation that used to lock everyone out
    resp = login_and_verify_mfa(app.test_client(), "k2@example.com")
    assert _reaches_dashboard(resp)


def test_without_rekey_rotation_still_locks_out_which_is_the_bug_being_closed(app):
    """Control: proves the previous test is not passing for an unrelated reason."""
    sid = make_staff(app, "k3@example.com", mfa=True)
    app.config["MFA_ENCRYPTION_KEY"] = MFA_KEY  # set, but rekey-mfa never run and never used
    app.config["SECRET_KEY"] = ROTATED_APP_KEY
    resp = login_and_verify_mfa(app.test_client(), "k3@example.com")
    assert not _reaches_dashboard(resp)


def test_rekey_dry_run_writes_nothing_and_rerun_is_idempotent(app):
    sid = make_staff(app, "k4@example.com", mfa=True)
    app.config["MFA_ENCRYPTION_KEY"] = MFA_KEY
    before = _stored(app, sid)
    runner = app.test_cli_runner()

    dry = runner.invoke(args=["rekey-mfa", "--dry-run"])
    assert dry.exit_code == 0 and "would re-encrypt 1" in dry.output
    assert _stored(app, sid) == before

    first = runner.invoke(args=["rekey-mfa"])
    assert "Re-encrypted 1;" in first.output
    after_first = _stored(app, sid)
    assert after_first != before

    second = runner.invoke(args=["rekey-mfa"])
    assert "Re-encrypted 0; already current 1;" in second.output
    assert _stored(app, sid) == after_first


def test_rekey_refuses_without_dedicated_key(app):
    make_staff(app, "k5@example.com", mfa=True)
    result = app.test_cli_runner().invoke(args=["rekey-mfa"])
    assert result.exit_code != 0
    assert "OWNER_MFA_ENCRYPTION_KEY is not set" in result.output
