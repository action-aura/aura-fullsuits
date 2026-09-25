"""AUDIT 2026-09-24 gap G3: the signing-key PEM can exist on disk but be
unreadable by this process (wrong uid/gid after a `docker cp` restore into
owner/Dockerfile.staging's non-root container -- see
docs/ops/free-hosting-owner.md section 4.4a). Before the fix,
load_private_key() let a bare OSError/PermissionError escape -- every real
caller (verify_signing_key_health, scripts/deploy/restore_signing_key.py,
commercial preflight) only ever caught SigningKeyError, so this one gap
turned into a raw traceback at exactly the moment an operator most needed a
plain-English answer.

No real chmod-based reproduction here: Windows ACLs don't honor POSIX mode
bits the way the production container's Linux permissions do, so a
monkeypatched `open()` is the reliable, platform-independent way to force
the exact OSError this is guarding against -- it exercises the real
exception-handling branch in load_private_key(), not a substitute for it.
"""
from __future__ import annotations

import builtins
import os

import pytest

from app.licensing_service.signing import SigningKeyError, load_private_key, verify_signing_key_health
from app.licensing_service import signing as signing_service
from app.models.licensing_service import SigningKey


def _make_unreadable_pem(tmp_path, key_id: str, monkeypatch) -> None:
    """Writes a placeholder PEM file, then makes any open() of that exact
    path raise PermissionError -- everything else still opens for real, so
    logging/import machinery/etc. during the test are unaffected."""
    pem_path = tmp_path / f"{key_id}.pem"
    pem_path.write_bytes(b"placeholder -- never actually parsed, open() fails first")
    real_open = builtins.open

    def _raising_open(path, *args, **kwargs):
        if os.fspath(path) == str(pem_path):
            raise PermissionError(13, "Permission denied")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _raising_open)


class TestLoadPrivateKeyUnreadablePem:
    def test_raises_signing_key_error_not_a_raw_permission_error(self, tmp_path, monkeypatch):
        key_id = "owner-ed25519-20260101T000000Z-deadbeef"
        _make_unreadable_pem(tmp_path, key_id, monkeypatch)

        with pytest.raises(SigningKeyError) as excinfo:
            load_private_key(str(tmp_path), key_id)

        message = str(excinfo.value)
        assert key_id in message
        assert "could not be read" in message
        # The real OS error is preserved in the message, never swallowed.
        assert "Permission denied" in message

    def test_does_not_raise_a_bare_os_error(self, tmp_path, monkeypatch):
        """Mutation target: an implementation that lets PermissionError
        propagate unwrapped passes a bare `pytest.raises(Exception)` just as
        easily as this one does -- so the meaningful assertion is the type,
        not just that *something* raised."""
        key_id = "owner-ed25519-20260101T000000Z-deadbeef"
        _make_unreadable_pem(tmp_path, key_id, monkeypatch)

        try:
            load_private_key(str(tmp_path), key_id)
            pytest.fail("expected an exception")
        except SigningKeyError:
            pass  # exactly the domain exception every caller already handles
        except OSError:
            pytest.fail("a bare OSError/PermissionError escaped load_private_key() unwrapped")


class TestVerifySigningKeyHealthUnreadablePem:
    def test_reports_failed_status_instead_of_raising(self, app, tmp_path, monkeypatch):
        """The end-to-end path a real deploy hits: `flask commercial
        preflight` (owner/app/commercial_ops/preflight.py::
        _check_signing_key_health) calls this and only ever expected a
        dict back -- an uncaught exception here is the raw traceback G3
        is about, not a config problem it can report on."""
        key_id = "owner-ed25519-20260101T000000Z-deadbeef"
        row = SigningKey(key_id=key_id, algorithm="ed25519", public_key="unused-in-this-test", status="ACTIVE")
        signing_service.db_session.add(row)
        signing_service.db_session.commit()

        _make_unreadable_pem(tmp_path, key_id, monkeypatch)

        result = verify_signing_key_health(str(tmp_path))

        assert result["status"] == "FAILED"
        assert "could not be read" in result["detail"]


class TestRealOsErrorAndTheOperatorFacingCommand:
    """Added by the adversarial verification pass, 2026-09-26. The tests above
    force the error with a monkeypatched open(); these two use a REAL OS-level
    read failure instead (a directory sitting at <key_id>.pem: exists() is
    True, open() fails -- IsADirectoryError on Linux, PermissionError on
    Windows, both OSError), and drive the command an operator actually runs
    right after startup: bootstrap.sh step 6 and runbook 4.4a step 3 both run
    `flask commercial preflight`."""

    KEY_ID = "owner-ed25519-20260101T000000Z-deadbeef"

    def test_load_private_key_wraps_a_real_os_error(self, tmp_path):
        (tmp_path / f"{self.KEY_ID}.pem").mkdir()

        with pytest.raises(SigningKeyError, match="could not be read"):
            load_private_key(str(tmp_path), self.KEY_ID)

    def test_commercial_preflight_prints_a_clear_failure_not_a_traceback(self, app, tmp_path, monkeypatch):
        (tmp_path / f"{self.KEY_ID}.pem").mkdir()
        monkeypatch.setitem(app.config, "SIGNING_KEY_DIRECTORY", str(tmp_path))
        with app.app_context():
            signing_service.db_session.add(
                SigningKey(key_id=self.KEY_ID, algorithm="ed25519", public_key="unused-in-this-test", status="ACTIVE")
            )
            signing_service.db_session.commit()

        result = app.test_cli_runner().invoke(args=["commercial", "preflight"])

        assert not isinstance(result.exception, OSError), f"raw OS error escaped preflight: {result.exception!r}"
        assert result.exit_code == 1  # preflight's own "Preflight FAILED" ClickException, not a crash
        assert "Traceback" not in result.output
        assert "signing_key_sign_verify_roundtrip" in result.output
        assert "could not be read" in result.output
