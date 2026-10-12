"""UpdateService: check, download, verify, install.

Every refusal test asserts that the thing it guards DID NOT happen by counting
(launcher calls, client calls) -- an outcome assertion alone cannot tell
'refused before acting' from 'acted, then failed for another reason'.
"""
import hashlib
import json
import logging
import os
import time

import pytest

from commercial_runtime.licensing_contracts.client import NetworkError
from commercial_runtime.updates.descriptor import UpdateError
from commercial_runtime.updates.service import (
    INSTALLER_ARGS,
    INSTALL_RETRY_AFTER_SECONDS,
    UpdateService,
    installer_filename,
)
from commercial_runtime.updates.tests._fakes import (
    FakeOwnerClient,
    FakeStateRepository,
    Launcher,
    OwnerKey,
    make_trust_store,
)

CURRENT = "1.0.0-rc.8"


class Rig:
    """One service wired to fakes, with every collaborator countable."""

    def __init__(self, tmp_path, *, activated=True, installable=True, launcher=None, clock=None,
                 pre_install_backup=None, **client_kw):
        self.key = OwnerKey()
        self.owner = FakeOwnerClient(self.key, **client_kw)
        self.client_factory_calls = 0
        self.launcher = launcher or Launcher()
        self.app_data = tmp_path / "appdata"
        self.app_data.mkdir()
        self.install_dir = tmp_path / "install"
        self.install_dir.mkdir()
        trust = make_trust_store(tmp_path, self.key)

        def client_factory():
            self.client_factory_calls += 1
            return self.owner

        kwargs = {}
        if clock is not None:
            kwargs["clock"] = clock
        if pre_install_backup is not None:
            kwargs["pre_install_backup"] = pre_install_backup
        self.service = UpdateService(
            app_data_dir=self.app_data,
            client_factory=client_factory,
            trust_store_factory=lambda: trust,
            signer_factory=lambda: object(),
            state_repository_factory=lambda: FakeStateRepository(None if not activated else "inst-1"),
            app_version=CURRENT,
            release_channel="rc",
            installer_launcher=self.launcher,
            is_installable_build=lambda: installable,
            install_dir=str(self.install_dir),
            **kwargs,
        )

    @property
    def updates_dir(self):
        return self.app_data / "updates"

    def installer(self, version="1.0.0-rc.9"):
        return self.updates_dir / installer_filename(version)

    def to_ready(self):
        self.service.check()
        self.service.download()
        self.service.wait_for_download()
        assert self.service.status()["phase"] == "ready"


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


# ── check ──────────────────────────────────────────────────────────────────
def test_check_finds_the_offered_version(rig):
    status = rig.service.check()
    assert len(rig.owner.find_calls) == 1          # the check RAN
    assert rig.owner.find_calls[0]["app_version"] == CURRENT
    assert rig.owner.find_calls[0]["release_channel"] == "rc"
    assert status["available"]["version"] == "1.0.0-rc.9"
    assert status["available"]["size_bytes"] == len(rig.owner.payload_bytes)
    assert status["phase"] == "idle" and status["checked_at"]


def test_check_when_nothing_newer_clears_the_offer(tmp_path):
    rig = Rig(tmp_path, update_available=False)
    status = rig.service.check()
    assert len(rig.owner.find_calls) == 1
    assert status["available"] is None and status["phase"] == "idle"


def test_an_install_that_never_activated_cannot_check_and_never_builds_a_client(tmp_path):
    rig = Rig(tmp_path, activated=False)
    assert rig.service.disabled_reason() == "NOT_ACTIVATED"
    with pytest.raises(UpdateError) as exc:
        rig.service.check()
    assert exc.value.code == "NOT_ACTIVATED"
    assert rig.client_factory_calls == 0 and rig.owner.find_calls == []


def test_a_signature_failure_in_check_is_surfaced_not_swallowed(tmp_path):
    rig = Rig(tmp_path, descriptor_overrides={"installation_id": "someone-else"})
    with pytest.raises(UpdateError) as exc:
        rig.service.check()
    assert exc.value.code == "DESCRIPTOR_MISMATCH"
    assert rig.service.status()["phase"] == "error"
    assert rig.service.status()["available"] is None


def test_an_unreachable_owner_is_a_plain_error_code(tmp_path):
    rig = Rig(tmp_path, transport_error=NetworkError("NETWORK_UNAVAILABLE", "down"))
    with pytest.raises(UpdateError) as exc:
        rig.service.check()
    assert exc.value.code == "OWNER_UNREACHABLE"
    assert rig.service.status()["error_code"] == "OWNER_UNREACHABLE"


def test_an_owner_refusal_without_a_descriptor_is_reported(tmp_path):
    rig = Rig(tmp_path)
    rig.owner.find_latest_release = lambda **kw: {"result": "FAILURE", "reason_code": "LICENSE_SUSPENDED"}
    with pytest.raises(UpdateError) as exc:
        rig.service.check()
    assert exc.value.code == "OWNER_REFUSED"


def test_a_failed_check_does_not_take_a_ready_installer_away(tmp_path):
    rig = Rig(tmp_path)
    rig.to_ready()
    rig.owner.transport_error = NetworkError("NETWORK_UNAVAILABLE", "down")
    with pytest.raises(UpdateError):
        rig.service.check()
    assert rig.service.status()["phase"] == "ready"
    assert rig.installer().exists()


def test_a_check_that_sees_a_withdrawn_release_deletes_the_stale_installer(tmp_path):
    rig = Rig(tmp_path)
    rig.to_ready()
    rig.owner.update_available = False
    rig.service.check()
    assert not rig.installer().exists()
    assert rig.service.status()["available"] is None


def test_a_repeat_check_for_the_same_release_keeps_ready(tmp_path):
    rig = Rig(tmp_path)
    rig.to_ready()
    rig.service.check()
    assert rig.service.status()["phase"] == "ready"


def test_decisions_are_logged_not_silent(tmp_path, caplog):
    rig = Rig(tmp_path, activated=False)
    with caplog.at_level(logging.INFO, logger="aura.updates"):
        with pytest.raises(UpdateError):
            rig.service.check()
    assert any("not activated" in r.getMessage() for r in caplog.records)


# ── download ───────────────────────────────────────────────────────────────
def test_a_good_download_becomes_ready_and_lands_under_its_final_name(rig):
    rig.service.check()
    rig.service.download()
    rig.service.wait_for_download()
    status = rig.service.status()
    assert status["phase"] == "ready" and status["error_code"] is None
    assert rig.installer().read_bytes() == rig.owner.payload_bytes
    assert list(rig.updates_dir.glob("*.part")) == []
    assert len(rig.owner.authorize_calls) == 1 and len(rig.owner.download_calls) == 1
    assert rig.owner.download_calls[0]["expected_size"] == len(rig.owner.payload_bytes)


def test_wrong_bytes_are_corrupt_and_the_file_is_deleted(tmp_path):
    good = b"the real installer" * 50
    rig = Rig(tmp_path, payload_bytes=good, serve_bytes=b"X" * len(good))   # same size, different bytes
    rig.service.check()
    rig.service.download()
    rig.service.wait_for_download()
    status = rig.service.status()
    assert status["phase"] == "error" and status["error_code"] == "DOWNLOAD_CORRUPT"
    assert len(rig.owner.download_calls) == 1       # the download RAN, then was refused
    assert not rig.installer().exists()
    assert list(rig.updates_dir.glob("*.part")) == []
    assert status["can_install"] is False


def test_a_short_file_is_corrupt_even_if_it_would_hash_the_same_prefix(tmp_path):
    good = b"abcdefgh" * 100
    rig = Rig(tmp_path, payload_bytes=good, serve_bytes=good[:-1])
    rig.service.check()
    rig.service.download()
    rig.service.wait_for_download()
    assert rig.service.status()["error_code"] == "DOWNLOAD_CORRUPT"
    assert not rig.installer().exists()


def test_the_signed_checksum_wins_over_the_authorize_response(tmp_path):
    # The authorize-download reply carries a checksum too, but it is NOT signed.
    # A hostile reply that agrees with the hostile bytes must not rescue them.
    good = b"signed-good" * 40
    evil = b"evil-bytes!" * 40
    rig = Rig(tmp_path, payload_bytes=good, serve_bytes=evil, authorize_response={
        "download_token": "t", "artifact_checksum_sha256": hashlib.sha256(evil).hexdigest(),
        "artifact_size_bytes": len(evil)})
    rig.service.check()
    rig.service.download()
    rig.service.wait_for_download()
    assert rig.service.status()["error_code"] == "DOWNLOAD_CORRUPT"
    assert not rig.installer().exists()


def test_an_authorization_refusal_is_reported_and_nothing_is_downloaded(tmp_path):
    rig = Rig(tmp_path, authorize_response={"result": "FAILURE", "reason_code": "LICENSE_SUSPENDED"})
    rig.service.check()
    rig.service.download()
    rig.service.wait_for_download()
    assert rig.service.status()["error_code"] == "DOWNLOAD_NOT_AUTHORIZED"
    assert rig.owner.download_calls == []


def test_download_without_an_offer_is_refused(rig):
    with pytest.raises(UpdateError) as exc:
        rig.service.download()
    assert exc.value.code == "NOTHING_TO_DOWNLOAD"
    assert rig.owner.authorize_calls == []


def test_only_one_download_runs_at_a_time(tmp_path):
    rig = Rig(tmp_path)
    rig.service.check()
    import threading
    gate = threading.Event()
    original = rig.owner.download_release

    def slow(*a, **k):
        gate.wait(5)
        return original(*a, **k)

    rig.owner.download_release = slow
    rig.service.download()
    try:
        with pytest.raises(UpdateError) as exc:
            rig.service.download()
        assert exc.value.code == "BUSY"
    finally:
        gate.set()
        rig.service.wait_for_download()
    assert len(rig.owner.authorize_calls) == 1


def test_a_download_failure_from_the_transport_is_a_retryable_error(tmp_path):
    rig = Rig(tmp_path)
    rig.service.check()

    def boom(*a, **k):
        raise NetworkError("NETWORK_UNAVAILABLE", "dropped")

    rig.owner.download_release = boom
    rig.service.download()
    rig.service.wait_for_download()
    assert rig.service.status()["error_code"] == "DOWNLOAD_FAILED"
    # The offer survives, so the person can simply press the button again.
    assert rig.service.status()["available"] is not None
    rig.owner.download_release = FakeOwnerClient.download_release.__get__(rig.owner)
    rig.service.download()
    rig.service.wait_for_download()
    assert rig.service.status()["phase"] == "ready"


def test_progress_is_visible_while_downloading(tmp_path):
    rig = Rig(tmp_path)
    rig.service.check()
    import threading
    gate = threading.Event()
    reached = threading.Event()

    def staged(token, dest, *, expected_size, chunk=0, progress=None):
        progress(10)
        reached.set()
        gate.wait(5)
        raise NetworkError("NETWORK_UNAVAILABLE", "stop here")

    rig.owner.download_release = staged
    rig.service.download()
    try:
        assert reached.wait(5)
        st = rig.service.status()
        assert st["phase"] == "downloading"
        assert st["progress"] == {"received": 10, "total": len(rig.owner.payload_bytes)}
    finally:
        gate.set()
        rig.service.wait_for_download()


# ── install ────────────────────────────────────────────────────────────────
def test_the_good_path_launches_the_installer_exactly_once_with_the_exact_args(tmp_path):
    rig = Rig(tmp_path)
    rig.to_ready()
    rig.service.install()
    assert len(rig.launcher.calls) == 1
    path, args = rig.launcher.calls[0]
    assert path == str(rig.installer())
    assert args == ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/FORCECLOSEAPPLICATIONS", "/RELAUNCH=1"]
    assert tuple(args) == INSTALLER_ARGS
    assert rig.service.status()["phase"] == "installing"


def test_a_file_swapped_after_ready_is_refused_deleted_and_never_launched(tmp_path):
    rig = Rig(tmp_path)
    rig.to_ready()
    rig.installer().write_bytes(b"tampered after download")
    with pytest.raises(UpdateError) as exc:
        rig.service.install()
    assert exc.value.code == "INSTALLER_TAMPERED"
    assert rig.launcher.calls == []                 # ZERO launches
    assert not rig.installer().exists()
    assert rig.service.status()["error_code"] == "INSTALLER_TAMPERED"


def test_a_file_that_vanished_after_ready_is_refused_and_never_launched(tmp_path):
    rig = Rig(tmp_path)
    rig.to_ready()
    os.remove(rig.installer())
    with pytest.raises(UpdateError) as exc:
        rig.service.install()
    assert exc.value.code == "INSTALLER_TAMPERED"
    assert rig.launcher.calls == []


def test_a_non_installable_build_refuses_and_never_launches(tmp_path):
    rig = Rig(tmp_path, installable=False)
    rig.to_ready()
    assert rig.service.status()["can_install"] is False
    with pytest.raises(UpdateError) as exc:
        rig.service.install()
    assert exc.value.code == "UNSUPPORTED_PLATFORM"
    assert rig.launcher.calls == []
    assert rig.installer().exists()   # a dev run must not eat the downloaded file


def test_install_before_a_verified_download_is_refused(rig):
    rig.service.check()
    with pytest.raises(UpdateError) as exc:
        rig.service.install()
    assert exc.value.code == "NOT_READY"
    assert rig.launcher.calls == []


def test_a_second_install_click_does_not_launch_twice(tmp_path):
    rig = Rig(tmp_path)
    rig.to_ready()
    rig.service.install()
    with pytest.raises(UpdateError) as exc:
        rig.service.install()
    assert exc.value.code == "NOT_READY"
    assert len(rig.launcher.calls) == 1


def test_an_install_that_produced_no_result_can_be_retried_later(tmp_path):
    now = [1000.0]
    rig = Rig(tmp_path, clock=lambda: now[0])
    rig.to_ready()
    rig.service.install()
    now[0] += INSTALL_RETRY_AFTER_SECONDS + 1     # e.g. the person declined the UAC prompt
    rig.service.install()
    assert len(rig.launcher.calls) == 2


def test_all_users_flag_is_added_only_when_the_install_directory_is_not_writable(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    writable = Rig(tmp_path / "a")
    writable.to_ready()
    writable.service.install()
    assert "/ALLUSERS" not in writable.launcher.calls[0][1]

    locked = Rig(tmp_path / "b")
    locked.service._install_dir = str(tmp_path / "does-not-exist")   # creating a file there fails
    locked.to_ready()
    locked.service.install()
    assert locked.launcher.calls[0][1][-1] == "/ALLUSERS"
    assert locked.launcher.calls[0][1][:-1] == list(INSTALLER_ARGS)


def test_a_launcher_that_cannot_start_is_reported_and_leaves_the_file_for_a_retry(tmp_path):
    rig = Rig(tmp_path, launcher=Launcher(raises=OSError("blocked by antivirus")))
    rig.to_ready()
    with pytest.raises(UpdateError) as exc:
        rig.service.install()
    assert exc.value.code == "INSTALL_LAUNCH_FAILED"
    assert len(rig.launcher.calls) == 1
    assert rig.installer().exists()


def test_the_installer_file_name_only_comes_from_a_valid_version():
    assert installer_filename("1.0.0-rc.9") == "AuraRetail-Setup-1.0.0-rc.9.exe"
    assert installer_filename("1.0.0+meta") == "AuraRetail-Setup-1.0.0.exe"
    for bad in ("../x", "1.0.0/../../evil", "a\\b", ""):
        with pytest.raises(ValueError):
            installer_filename(bad)


# ── startup ────────────────────────────────────────────────────────────────
def test_startup_sweeps_part_files_and_installers_that_are_not_newer(tmp_path):
    rig = Rig(tmp_path)
    rig.updates_dir.mkdir(parents=True)
    keep = rig.updates_dir / installer_filename("1.0.0-rc.9")
    for name in ("AuraRetail-Setup-1.0.0-rc.9.exe.part", installer_filename("1.0.0-rc.7"),
                 installer_filename(CURRENT), "AuraRetail-Setup-garbage.exe"):
        (rig.updates_dir / name).write_bytes(b"x")
    keep.write_bytes(b"x")
    rig.service.startup()
    assert sorted(p.name for p in rig.updates_dir.glob("*.exe*")) == [keep.name]


def test_startup_after_the_update_landed_clears_the_offer(tmp_path):
    rig = Rig(tmp_path)
    rig.to_ready()
    rig.service.install()
    # The new build starts: its own version is now the offered one.
    new_service = UpdateService(
        app_data_dir=rig.app_data, client_factory=lambda: rig.owner, trust_store_factory=lambda: None,
        signer_factory=lambda: None, state_repository_factory=lambda: FakeStateRepository(),
        app_version="1.0.0-rc.9", release_channel="rc", installer_launcher=rig.launcher,
        is_installable_build=lambda: True)
    new_service.startup()
    st = new_service.status()
    assert st["phase"] == "idle" and st["available"] is None
    assert list(rig.updates_dir.glob("*.exe")) == []


def test_startup_after_a_crash_mid_download_goes_back_to_idle(tmp_path):
    rig = Rig(tmp_path)
    rig.service.check()
    state = json.loads((rig.updates_dir / "state.json").read_text())
    state["phase"] = "downloading"
    (rig.updates_dir / "state.json").write_text(json.dumps(state))
    again = UpdateService(
        app_data_dir=rig.app_data, client_factory=lambda: rig.owner, trust_store_factory=lambda: None,
        signer_factory=lambda: None, state_repository_factory=lambda: FakeStateRepository(),
        app_version=CURRENT, release_channel="rc", is_installable_build=lambda: True)
    again.startup()
    assert again.status()["phase"] == "idle"
    assert again.status()["available"]["version"] == "1.0.0-rc.9"


def test_a_corrupt_state_file_starts_clean_instead_of_crashing(tmp_path):
    rig = Rig(tmp_path)
    rig.updates_dir.mkdir(parents=True)
    (rig.updates_dir / "state.json").write_text("{not json")
    again = UpdateService(
        app_data_dir=rig.app_data, client_factory=lambda: rig.owner, trust_store_factory=lambda: None,
        signer_factory=lambda: None, state_repository_factory=lambda: FakeStateRepository(),
        app_version=CURRENT, release_channel="rc")
    assert again.status()["phase"] == "idle"


def test_state_is_written_atomically_and_survives_a_restart(tmp_path):
    rig = Rig(tmp_path)
    rig.service.check()
    assert not (rig.updates_dir / "state.json.tmp").exists()
    again = UpdateService(
        app_data_dir=rig.app_data, client_factory=lambda: rig.owner, trust_store_factory=lambda: None,
        signer_factory=lambda: None, state_repository_factory=lambda: FakeStateRepository(),
        app_version=CURRENT, release_channel="rc")
    assert again.status()["available"]["version"] == "1.0.0-rc.9"


def test_a_tampered_state_file_cannot_smuggle_a_path_into_install(tmp_path):
    rig = Rig(tmp_path)
    rig.to_ready()
    state = json.loads((rig.updates_dir / "state.json").read_text())
    state["available"]["version"] = "../../../evil"
    (rig.updates_dir / "state.json").write_text(json.dumps(state))
    again = UpdateService(
        app_data_dir=rig.app_data, client_factory=lambda: rig.owner, trust_store_factory=lambda: None,
        signer_factory=lambda: None, state_repository_factory=lambda: FakeStateRepository(),
        app_version=CURRENT, release_channel="rc", installer_launcher=rig.launcher,
        is_installable_build=lambda: True, install_dir=str(rig.install_dir))
    again._state["phase"] = "ready"
    with pytest.raises(UpdateError) as exc:
        again.install()
    assert exc.value.code == "NOT_READY"
    assert rig.launcher.calls == []
