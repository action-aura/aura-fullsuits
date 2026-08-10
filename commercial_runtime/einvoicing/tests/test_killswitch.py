from datetime import datetime, timedelta, timezone

from commercial_runtime.einvoicing import killswitch


def test_default_is_not_disabled(tmp_path):
    status = killswitch.is_disabled(str(tmp_path))
    assert status.disabled is False
    assert status.reason is None


def test_set_disabled_takes_effect_immediately(tmp_path):
    killswitch.set_disabled(str(tmp_path), reason='manual-test')
    status = killswitch.is_disabled(str(tmp_path))
    assert status.disabled is True
    assert status.reason == 'manual-test'


def test_clear_disabled_restores_enabled_state(tmp_path):
    killswitch.set_disabled(str(tmp_path), reason='manual-test')
    killswitch.clear_disabled(str(tmp_path))
    assert killswitch.is_disabled(str(tmp_path)).disabled is False


def test_clear_disabled_when_never_set_does_not_raise(tmp_path):
    killswitch.clear_disabled(str(tmp_path))  # must not raise


def test_expiry_auto_clears(tmp_path):
    killswitch.set_disabled(str(tmp_path), reason='temporary', minutes=30)
    status = killswitch.is_disabled(str(tmp_path))
    assert status.disabled is True

    # Simulate elapsed time by writing an already-expired flag directly.
    flag_path = tmp_path / 'einvoicing' / 'DISABLED'
    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    flag_path.write_text(f"reason=temporary\nsetAtUtc={expired.isoformat()}\nexpiresAtUtc={expired.isoformat()}\n")

    status_after = killswitch.is_disabled(str(tmp_path))
    assert status_after.disabled is False
    assert not flag_path.exists(), "expired flag file must be auto-removed"


def test_no_expiry_never_auto_clears(tmp_path):
    killswitch.set_disabled(str(tmp_path), reason='indefinite')
    status = killswitch.is_disabled(str(tmp_path))
    assert status.disabled is True
    flag_path = tmp_path / 'einvoicing' / 'DISABLED'
    assert flag_path.exists()


def test_malformed_expiry_counts_as_disabled_fail_safe(tmp_path):
    flag_path_dir = tmp_path / 'einvoicing'
    flag_path_dir.mkdir(parents=True, exist_ok=True)
    (flag_path_dir / 'DISABLED').write_text("reason=bad\nexpiresAtUtc=not-a-real-date\n")
    status = killswitch.is_disabled(str(tmp_path))
    assert status.disabled is True
    assert status.reason == 'malformed-flag-file'


def test_missing_reason_defaults_to_manual(tmp_path):
    flag_path_dir = tmp_path / 'einvoicing'
    flag_path_dir.mkdir(parents=True, exist_ok=True)
    (flag_path_dir / 'DISABLED').write_text("setAtUtc=2026-01-01T00:00:00+00:00\n")
    status = killswitch.is_disabled(str(tmp_path))
    assert status.disabled is True
    assert status.reason == 'manual'
