"""AUDIT 2026-09-24 gap G1: scripts/deploy/restore_signing_key.py's
trust-anchor cross-check failed OPEN (a WARNING/NOTE, then proceeded anyway)
whenever the trust-anchor file was missing, or existed but did not list the
key_id being restored -- the only net against registering the wrong key
under a trusted key_id was a human reading a warning line. It failed CLOSED
only on the third case (file exists, lists the key_id, PUBLIC KEY DISAGREES).

These tests exercise cross_check_trust_anchor() directly -- a pure function
with no Flask/DB dependency, loaded by file path so this suite needs neither
`app` nor Postgres for these particular tests (the module's own top-level
imports are stdlib-only; `from app import create_app` etc. happen inside
main(), never at import time).

Both directions matter here, per this repo's own testing doctrine: a fix
that must DENY the two open cases while still ALLOWING the one documented
override (--skip-trust-anchor-check) and the one case that always denied
(public-key mismatch) is half-tested if only the deny-half is checked.

TestMainExitsNonZeroAndWritesNothing (added by the adversarial verification
pass, 2026-09-26) is the exception to "no app, no Postgres": the pure-
function tests above cannot see main() itself. Mutating main() to print the
refusal and carry on (`print(str(exc))` in place of `_fail(str(exc))`) --
i.e. the exact fail-open this audit item is about, one layer up -- left all
five of the tests above GREEN. Those tests run the real main() against the
real test database and assert the check RAN (nothing registered, activate
never called, exit code 1), not merely that some function raised.
"""
from __future__ import annotations

import base64
import importlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "deploy" / "restore_signing_key.py"
_spec = importlib.util.spec_from_file_location("restore_signing_key", _SCRIPT_PATH)
restore_signing_key = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("restore_signing_key", restore_signing_key)
_spec.loader.exec_module(restore_signing_key)

cross_check_trust_anchor = restore_signing_key.cross_check_trust_anchor
TrustAnchorRefused = restore_signing_key.TrustAnchorRefused

KEY_ID = "owner-ed25519-20260903T160641Z-cdcaa65b"
DERIVED_PUBLIC_KEY = "the-real-derived-public-key-base64=="
OTHER_PUBLIC_KEY = "a-different-public-key-base64=="


def _write_anchor(path: Path, entries: list[dict]) -> None:
    path.write_text(json.dumps({"keys": entries}), encoding="utf-8")


class TestDenyHalf:
    """The two cases this audit found failing OPEN -- both must now REFUSE
    (raise TrustAnchorRefused), never silently proceed."""

    def test_missing_trust_anchor_file_refuses(self, tmp_path):
        missing_path = str(tmp_path / "does-not-exist.json")
        with pytest.raises(TrustAnchorRefused, match="not found"):
            cross_check_trust_anchor(missing_path, KEY_ID, DERIVED_PUBLIC_KEY, skip_check=False)

    def test_key_id_not_listed_in_an_existing_anchor_refuses(self, tmp_path):
        anchor_path = tmp_path / "trust_anchor.json"
        _write_anchor(anchor_path, [{"key_id": "some-other-key-entirely", "public_key": OTHER_PUBLIC_KEY}])
        with pytest.raises(TrustAnchorRefused, match="not listed"):
            cross_check_trust_anchor(str(anchor_path), KEY_ID, DERIVED_PUBLIC_KEY, skip_check=False)

    def test_public_key_mismatch_refuses(self, tmp_path):
        """Always failed closed -- pinned here so a future refactor can't
        regress it while fixing the other two."""
        anchor_path = tmp_path / "trust_anchor.json"
        _write_anchor(anchor_path, [{"key_id": KEY_ID, "public_key": OTHER_PUBLIC_KEY}])
        with pytest.raises(TrustAnchorRefused, match="does NOT match"):
            cross_check_trust_anchor(str(anchor_path), KEY_ID, DERIVED_PUBLIC_KEY, skip_check=False)


class TestAllowHalf:
    """The override, and the one case that should always succeed -- a
    fix that denies everything passes the deny-half trivially, so this
    half has to be checked independently."""

    def test_skip_check_bypasses_everything_even_a_missing_file(self, tmp_path):
        missing_path = str(tmp_path / "does-not-exist.json")
        status = cross_check_trust_anchor(missing_path, KEY_ID, DERIVED_PUBLIC_KEY, skip_check=True)
        assert "SKIPPED" in status

    def test_matching_key_id_and_public_key_succeeds(self, tmp_path):
        anchor_path = tmp_path / "trust_anchor.json"
        _write_anchor(anchor_path, [{"key_id": KEY_ID, "public_key": DERIVED_PUBLIC_KEY}])
        status = cross_check_trust_anchor(str(anchor_path), KEY_ID, DERIVED_PUBLIC_KEY, skip_check=False)
        assert "OK" in status
        assert KEY_ID in status


class TestUnreadableOrMalformedAnchorRefusesWithAClearMessage:
    """Added by the adversarial verification pass: these three always
    failed CLOSED (a raw traceback exits nonzero too), but as an unhandled
    IsADirectoryError/PermissionError/JSONDecodeError/AttributeError -- not
    the clear REFUSING: line the audit asked for. The PermissionError case is
    the realistic one: `docker cp` creates the file root-owned inside the
    container with the SOURCE file's mode, so a 0600 trust_anchor.json on the
    VM lands unreadable to the container's non-root `aura` user."""

    def test_unreadable_anchor_path_refuses(self, tmp_path):
        # A directory where the file should be: a real OS-level read error on
        # every platform (IsADirectoryError on Linux, PermissionError on
        # Windows), no monkeypatching needed.
        anchor_path = tmp_path / "trust_anchor.json"
        anchor_path.mkdir()
        with pytest.raises(TrustAnchorRefused, match="could not be read"):
            cross_check_trust_anchor(str(anchor_path), KEY_ID, DERIVED_PUBLIC_KEY, skip_check=False)

    def test_invalid_json_refuses(self, tmp_path):
        anchor_path = tmp_path / "trust_anchor.json"
        anchor_path.write_text("{not json", encoding="utf-8")
        with pytest.raises(TrustAnchorRefused, match="could not be read"):
            cross_check_trust_anchor(str(anchor_path), KEY_ID, DERIVED_PUBLIC_KEY, skip_check=False)

    @pytest.mark.parametrize("payload", [[], {"keys": "not-a-list"}, {"keys": ["not-a-dict"]}])
    def test_wrong_shape_refuses(self, tmp_path, payload):
        anchor_path = tmp_path / "trust_anchor.json"
        anchor_path.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(TrustAnchorRefused, match="malformed"):
            cross_check_trust_anchor(str(anchor_path), KEY_ID, DERIVED_PUBLIC_KEY, skip_check=False)


def _write_real_pem(directory: Path, key_id: str) -> str:
    """A real Ed25519 PEM exactly as signing.generate_signing_key() writes
    one; returns the base64 raw public key main() will derive from it."""
    private_key = Ed25519PrivateKey.generate()
    (directory / f"{key_id}.pem").write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    raw = private_key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


class TestMainExitsNonZeroAndWritesNothing:
    """Drives the script's real main() -- argument parsing, PEM load,
    cross-check, DB registration, real activate_signing_key() -- against the
    test database. create_app is pointed at the fixture's app only so the
    signing-key directory can be a tmp dir; nothing else is stubbed."""

    @pytest.fixture()
    def run_main(self, app, tmp_path, monkeypatch):
        from app.licensing_service import signing as signing_module

        key_dir = tmp_path / "signing-keys"
        key_dir.mkdir()
        monkeypatch.setitem(app.config, "SIGNING_KEY_DIRECTORY", str(key_dir))
        monkeypatch.setattr(importlib.import_module("app"), "create_app", lambda *a, **k: app)

        activate_calls: list[str] = []
        real_activate = signing_module.activate_signing_key

        def _counting_activate(key_directory, key_id, *args, **kwargs):
            activate_calls.append(key_id)
            return real_activate(key_directory, key_id, *args, **kwargs)

        monkeypatch.setattr(signing_module, "activate_signing_key", _counting_activate)

        def _run(*argv: str):
            monkeypatch.setattr(sys, "argv", ["restore_signing_key.py", *argv])
            restore_signing_key.main()

        def _registered_rows():
            from sqlalchemy import select

            from app.extensions import db_session
            from app.models.licensing_service import SigningKey

            with app.app_context():
                return [(r.key_id, r.status) for r in db_session.execute(select(SigningKey)).scalars().all()]

        return key_dir, _run, activate_calls, _registered_rows

    def test_missing_anchor_exits_1_and_registers_nothing(self, run_main, tmp_path, capsys):
        key_dir, run, activate_calls, registered_rows = run_main
        _write_real_pem(key_dir, KEY_ID)

        with pytest.raises(SystemExit) as excinfo:
            run(KEY_ID, "--trust-anchor", str(tmp_path / "absent.json"))

        assert excinfo.value.code == 1
        assert "REFUSING:" in capsys.readouterr().err
        assert activate_calls == []
        assert registered_rows() == []

    def test_unlisted_key_id_exits_1_and_registers_nothing(self, run_main, tmp_path, capsys):
        key_dir, run, activate_calls, registered_rows = run_main
        _write_real_pem(key_dir, KEY_ID)
        anchor_path = tmp_path / "trust_anchor.json"
        _write_anchor(anchor_path, [{"key_id": "some-other-key-entirely", "public_key": OTHER_PUBLIC_KEY}])

        with pytest.raises(SystemExit) as excinfo:
            run(KEY_ID, "--trust-anchor", str(anchor_path))

        assert excinfo.value.code == 1
        assert "REFUSING:" in capsys.readouterr().err
        assert activate_calls == []
        assert registered_rows() == []

    def test_matching_anchor_registers_and_activates(self, run_main, tmp_path):
        """Allow-half: a main() that refused everything would pass both tests
        above; this is the ordinary restore the script exists for."""
        key_dir, run, activate_calls, registered_rows = run_main
        public_key = _write_real_pem(key_dir, KEY_ID)
        anchor_path = tmp_path / "trust_anchor.json"
        _write_anchor(anchor_path, [{"key_id": KEY_ID, "public_key": public_key, "algorithm": "ed25519"}])

        run(KEY_ID, "--trust-anchor", str(anchor_path))

        assert activate_calls == [KEY_ID]
        assert registered_rows() == [(KEY_ID, "ACTIVE")]
