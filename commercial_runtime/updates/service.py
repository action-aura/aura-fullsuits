"""UpdateService -- check, download, verify, install.

One object owns the whole flow so every safety rule lives in one place:

  * The answer to "what is the newest build?" is an Owner-SIGNED descriptor
    (descriptor.py). Nothing else is trusted for the checksum or size.
  * The download is hashed against THAT signed checksum, never against the
    checksum in the authorize-download response (which is not signed).
  * The file is hashed AGAIN right before it is launched. Between "ready" and
    "install" it sits in a user-writable folder for as long as the person
    takes to click, so "it was good when it landed" is not good enough.
  * Every decision logs one line. A silent early return here is undiagnosable
    -- "never started" and "ran and declined" would look identical in a log.

State lives in <app_data>/updates/state.json (atomic tmp + os.replace). It is
an index of what this process did, not a trust anchor: the checksum it holds
came from a verified descriptor, and a local attacker who can rewrite both the
installer and this file can already replace the application itself.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from commercial_runtime.licensing_contracts.client import LicensingClientError

from .descriptor import ReleaseOffer, UpdateError, verify_owner_release_response
from .versioning import is_newer, parse_semver

log = logging.getLogger("aura.updates")

PHASES = ("idle", "checking", "downloading", "ready", "installing", "error")

# Inno Setup switches. /FORCECLOSEAPPLICATIONS closes this running app so its
# files can be replaced; /RELAUNCH=1 is read by the .iss ([Run] ShouldRelaunch)
# to start the new build again, because a silent install skips the "Launch now"
# checkbox that a normal one shows.
INSTALLER_ARGS = (
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/FORCECLOSEAPPLICATIONS",
    "/RELAUNCH=1",
)

PROGRESS_PERSIST_SECONDS = 1.0
# An install that was launched but never replaced this process (UAC declined,
# antivirus held the file) must not leave the button dead forever.
INSTALL_RETRY_AFTER_SECONDS = 120.0
_HASH_BLOCK = 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def installer_filename(version: str) -> str:
    """The ONLY way an installer path is ever built. The version must already
    parse as semver (so it cannot contain a path separator or '..'); build
    metadata is dropped because '+' has no business in a file name."""
    parse_semver(version)
    return "AuraRetail-Setup-%s.exe" % version.split("+", 1)[0]


def sha256_of(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(_HASH_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def default_is_installable_build() -> bool:
    # Only the frozen Windows exe has an installed copy to replace. A dev run
    # (python app.py) must never launch an installer over a source checkout.
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def default_installer_launcher(path: str, args: Sequence[str]) -> None:
    """Starts the installer detached from this process, so it survives the
    app closing itself (the installer's whole job is to close it). A list, never
    shell=True: the path is ours but the habit is the guard."""
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [str(path), *args],
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _directory_is_writable(path: str) -> bool:
    # os.access(W_OK) lies on Windows directories (always True), so ask the
    # filesystem the only way that is honest: try to create a file.
    try:
        with tempfile.TemporaryFile(dir=path):
            return True
    except OSError:
        return False


def _fresh_state() -> dict:
    return {
        "phase": "idle",
        "checked_at": None,
        "available": None,
        "progress": None,
        "error_code": None,
        "installing_since": None,
        # File name of the safety copy taken just before the last installer
        # launch (BACKUP-1). Recorded so a later "go back" flow knows which
        # backup matches the version that was replaced.
        "pre_update_backup": None,
    }


class UpdateService:
    def __init__(
        self,
        *,
        app_data_dir,
        client_factory: Callable[[], object],
        trust_store_factory: Callable[[], object],
        signer_factory: Callable[[], object],
        state_repository_factory: Callable[[], object],
        app_version: str,
        release_channel: Optional[str],
        installer_launcher: Optional[Callable[[str, Sequence[str]], None]] = None,
        is_installable_build: Optional[Callable[[], bool]] = None,
        install_dir: Optional[str] = None,
        clock: Callable[[], float] = time.time,
        pre_install_backup: Optional[Callable[[], str]] = None,
    ):
        parse_semver(app_version)  # fail at construction, not on the first poll
        self._dir = Path(app_data_dir) / "updates"
        self._state_path = self._dir / "state.json"
        self._client_factory = client_factory
        self._trust_store_factory = trust_store_factory
        self._signer_factory = signer_factory
        self._state_repository_factory = state_repository_factory
        self._app_version = app_version
        self._release_channel = release_channel
        self._launch = installer_launcher or default_installer_launcher
        self._is_installable = is_installable_build or default_is_installable_build
        self._install_dir = install_dir
        self._clock = clock
        # BACKUP-1: called right before an installer is launched. Returns the
        # backup's file name; ANY exception means "no safety copy" and the
        # install is refused. None only in tests/dev that wire no backup.
        self._pre_install_backup = pre_install_backup
        self._lock = threading.RLock()
        self._worker: Optional[threading.Thread] = None
        self._last_persist = 0.0
        self._state = self._read_state()

    # ── state ──────────────────────────────────────────────────────────────
    def _read_state(self) -> dict:
        state = _fresh_state()
        try:
            loaded = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key in state:
                    if key in loaded:
                        state[key] = loaded[key]
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("updates: state.json unreadable, starting from a clean state", exc_info=True)
        if state["phase"] not in PHASES:
            state["phase"] = "idle"
        return state

    def _persist(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._state), encoding="utf-8")
            os.replace(tmp, self._state_path)
            self._last_persist = self._clock()
        except OSError:
            # Losing the index is survivable (startup() rebuilds from disk);
            # raising here would abort a download that is otherwise fine.
            log.warning("updates: could not write state.json", exc_info=True)

    def _set(self, **changes) -> None:
        with self._lock:
            self._state.update(changes)
            self._persist()

    def _offer(self) -> Optional[dict]:
        return self._state.get("available")

    def _installer_path(self, version: str) -> Path:
        return self._dir / installer_filename(version)

    def _delete_offer_file(self, offer: Optional[dict]) -> None:
        # state.json is an index, not a trust anchor: a version in it that no
        # longer parses simply has no file we would have written.
        if offer is None:
            return
        try:
            self._delete_quietly(self._installer_path(offer["version"]))
        except (KeyError, ValueError, TypeError):
            log.warning("updates: ignored an unreadable version in state.json")

    def _delete_quietly(self, path) -> None:
        try:
            os.remove(path)
            log.info("updates: deleted %s", Path(path).name)
        except FileNotFoundError:
            pass
        except OSError:
            log.warning("updates: could not delete %s", Path(path).name, exc_info=True)

    # ── startup ────────────────────────────────────────────────────────────
    def startup(self) -> None:
        """Run once when the app starts. A process that died mid-download or
        mid-install left the persisted phase pointing at work that is no longer
        happening; this puts it back to something true and sweeps stale files."""
        with self._lock:
            self._dir.mkdir(parents=True, exist_ok=True)
            for part in self._dir.glob("*.part"):
                self._delete_quietly(part)
            for exe in self._dir.glob("AuraRetail-Setup-*.exe"):
                version = exe.name[len("AuraRetail-Setup-"):-len(".exe")]
                try:
                    stale = not is_newer(version, self._app_version)
                except ValueError:
                    stale = True  # not a name we would ever have written
                if stale:
                    self._delete_quietly(exe)
            offer = self._offer()
            if offer is not None:
                try:
                    already_current = not is_newer(offer["version"], self._app_version)
                except (KeyError, ValueError, TypeError):
                    already_current = True
                if already_current:
                    log.info("updates: startup -- offered version is not newer than this build; clearing")
                    self._state.update(available=None, progress=None, error_code=None, phase="idle", installing_since=None)
                elif self._state["phase"] in ("checking", "downloading"):
                    log.info("updates: startup -- an interrupted %s was dropped", self._state["phase"])
                    self._state.update(phase="idle", progress=None)
                elif self._state["phase"] in ("ready", "installing"):
                    try:
                        present = self._installer_path(offer["version"]).exists()
                    except ValueError:
                        present = False
                    self._state.update(phase="ready" if present else "idle", installing_since=None)
                    log.info("updates: startup -- phase restored to %s", self._state["phase"])
            else:
                self._state.update(phase="idle", progress=None, installing_since=None)
            self._persist()

    # ── reading status ─────────────────────────────────────────────────────
    def disabled_reason(self) -> Optional[str]:
        """None when updates can run. NOT_ACTIVATED until this install holds an
        Owner-issued installation id: Owner's discovery is device-signed, so an
        install that never activated has nobody to sign as."""
        return None if self._installation_id() else "NOT_ACTIVATED"

    def _installation_id(self) -> Optional[str]:
        try:
            record = self._state_repository_factory().load()
        except Exception:
            log.warning("updates: could not read licence state", exc_info=True)
            return None
        return getattr(record, "owner_installation_id", None) or None

    def status(self) -> dict:
        with self._lock:
            offer = self._offer()
            available = None
            if offer is not None:
                available = {
                    "version": offer["version"],
                    "release_notes": offer.get("release_notes"),
                    "size_bytes": offer["size_bytes"],
                    "required": bool(offer.get("update_required")),
                    "below_min_supported": bool(offer.get("below_min_supported")),
                }
            phase = self._state["phase"]
            return {
                "current_version": self._app_version,
                "checked_at": self._state["checked_at"],
                "available": available,
                "phase": phase,
                "progress": self._state["progress"] if phase == "downloading" else None,
                "error_code": self._state["error_code"] if phase == "error" else None,
                "can_install": phase == "ready" and bool(self._is_installable()),
            }

    # ── check ──────────────────────────────────────────────────────────────
    def check(self) -> dict:
        installation_id = self._installation_id()
        if not installation_id:
            log.info("updates: check skipped -- this install is not activated")
            raise UpdateError("NOT_ACTIVATED", "This install has no Owner installation id yet.")
        with self._lock:
            if self._state["phase"] in ("downloading", "installing"):
                log.info("updates: check skipped -- phase is %s", self._state["phase"])
                return self.status()
            previous_phase = self._state["phase"]
            self._state["phase"] = "checking"

        request_id = str(uuid.uuid4())
        try:
            client = self._client_factory()
            response = client.find_latest_release(
                installation_id=installation_id,
                app_version=self._app_version,
                release_channel=self._release_channel,
                signer=self._signer_factory(),
                request_id=request_id,
            )
            offer = verify_owner_release_response(
                response,
                trust_store=self._trust_store_factory(),
                expected_installation_id=installation_id,
                expected_request_id=request_id,
                current_version=self._app_version,
            )
        except UpdateError as exc:
            self._check_failed(previous_phase, exc.code)
            raise
        except LicensingClientError as exc:
            log.warning("updates: check failed at transport (%s)", exc.reason_code)
            self._check_failed(previous_phase, "OWNER_UNREACHABLE")
            raise UpdateError("OWNER_UNREACHABLE", str(exc)) from exc
        except Exception:
            log.exception("updates: check failed unexpectedly")
            self._check_failed(previous_phase, "CHECK_FAILED")
            raise UpdateError("CHECK_FAILED", "Unexpected failure while checking for updates.")

        self._apply_check_result(offer, previous_phase)
        return self.status()

    def _check_failed(self, previous_phase: str, code: str) -> None:
        with self._lock:
            # A downloaded-and-verified installer stays installable: a flaky
            # network on a later poll must not take it away.
            if previous_phase == "ready":
                self._state["phase"] = "ready"
                self._persist()
            else:
                self._set(phase="error", error_code=code)
        log.info("updates: check failed with %s (phase kept as %s)", code, self._state["phase"])

    def _apply_check_result(self, offer: Optional[ReleaseOffer], previous_phase: str) -> None:
        with self._lock:
            old = self._offer()
            if offer is None:
                # Owner stopped offering it (withdrawn / pinned elsewhere): the
                # file we hold is no longer something Owner vouches for.
                self._delete_offer_file(old)
                self._state.update(available=None, progress=None, error_code=None, phase="idle", checked_at=_now_iso())
                self._persist()
                log.info("updates: check done -- up to date (running %s)", self._app_version)
                return
            same = old is not None and old.get("version") == offer.version and old.get("checksum_sha256") == offer.checksum_sha256
            keep_ready = same and previous_phase == "ready" and self._installer_path(offer.version).exists()
            if not same:
                self._delete_offer_file(old)
            self._state.update(
                available=_offer_to_dict(offer),
                progress=None,
                error_code=None,
                phase="ready" if keep_ready else "idle",
                checked_at=_now_iso(),
            )
            self._persist()
            log.info(
                "updates: check done -- %s is available (current %s, required=%s, ready=%s)",
                offer.version, self._app_version, offer.update_required, keep_ready,
            )

    # ── download ───────────────────────────────────────────────────────────
    def download(self) -> dict:
        """Starts the background download and returns at once."""
        installation_id = self._installation_id()
        if not installation_id:
            log.info("updates: download refused -- not activated")
            raise UpdateError("NOT_ACTIVATED")
        with self._lock:
            offer = self._offer()
            if offer is None:
                log.info("updates: download refused -- nothing is on offer")
                raise UpdateError("NOTHING_TO_DOWNLOAD")
            if self._state["phase"] in ("downloading", "installing"):
                log.info("updates: download refused -- already %s", self._state["phase"])
                raise UpdateError("BUSY")
            if self._state["phase"] == "ready":
                log.info("updates: download skipped -- installer already verified and ready")
                return self.status()
            self._state.update(phase="downloading", progress={"received": 0, "total": offer["size_bytes"]}, error_code=None)
            self._persist()
            worker = threading.Thread(
                target=self._download_worker, args=(dict(offer), installation_id), name="aura-update-download", daemon=True
            )
            self._worker = worker
        log.info("updates: download started for %s (%d bytes)", offer["version"], offer["size_bytes"])
        worker.start()
        return self.status()

    def wait_for_download(self, timeout: float = 30.0) -> None:
        """Test seam and shutdown courtesy: blocks until the worker is done."""
        worker = self._worker
        if worker is not None:
            worker.join(timeout)

    def _download_worker(self, offer: dict, installation_id: str) -> None:
        part = None
        try:
            version = offer["version"]
            final = self._installer_path(version)
            part = final.with_name(final.name + ".part")
            self._dir.mkdir(parents=True, exist_ok=True)
            client = self._client_factory()
            authorization = client.authorize_release_download(
                installation_id=installation_id,
                product_version_id=offer["product_version_id"],
                signer=self._signer_factory(),
            )
            token = authorization.get("download_token") if isinstance(authorization, dict) else None
            if not token:
                reason = authorization.get("reason_code") if isinstance(authorization, dict) else None
                log.warning("updates: Owner refused the download authorisation (reason=%s)", reason)
                raise UpdateError("DOWNLOAD_NOT_AUTHORIZED")
            client.download_release(
                token, str(part), expected_size=offer["size_bytes"], progress=self._on_progress(offer["size_bytes"])
            )
            # Verified against the SIGNED descriptor's values, held in `offer`.
            actual_size = os.path.getsize(part)
            actual_hash = sha256_of(part)
            if actual_size != offer["size_bytes"] or actual_hash != offer["checksum_sha256"]:
                log.error(
                    "updates: downloaded file does not match the signed descriptor (size %d vs %d, hash match=%s); deleted",
                    actual_size, offer["size_bytes"], actual_hash == offer["checksum_sha256"],
                )
                raise UpdateError("DOWNLOAD_CORRUPT")
            os.replace(part, final)
            self._set(phase="ready", progress=None, error_code=None)
            log.info("updates: %s downloaded and verified", version)
        except UpdateError as exc:
            self._download_failed(part, exc.code)
        except LicensingClientError as exc:
            log.warning("updates: download failed (%s)", exc.reason_code)
            code = "DOWNLOAD_TOO_LARGE" if exc.reason_code == "DOWNLOAD_TOO_LARGE" else "DOWNLOAD_FAILED"
            self._download_failed(part, code)
        except Exception:
            log.exception("updates: download failed unexpectedly")
            self._download_failed(part, "DOWNLOAD_FAILED")

    def _on_progress(self, total: int) -> Callable[[int], None]:
        def report(received: int) -> None:
            with self._lock:
                self._state["progress"] = {"received": received, "total": total}
                if self._clock() - self._last_persist >= PROGRESS_PERSIST_SECONDS:
                    self._persist()
        return report

    def _download_failed(self, part, code: str) -> None:
        if part is not None:
            self._delete_quietly(part)
        self._set(phase="error", progress=None, error_code=code)
        log.info("updates: download ended in error %s", code)

    # ── install ────────────────────────────────────────────────────────────
    def install(self) -> dict:
        with self._lock:
            phase = self._state["phase"]
            offer = self._offer()
            if phase == "installing":
                since = self._state.get("installing_since") or 0
                if self._clock() - since < INSTALL_RETRY_AFTER_SECONDS:
                    log.info("updates: install refused -- an installer was launched moments ago")
                    raise UpdateError("NOT_READY")
                log.info("updates: previous install launch got no result; allowing another try")
            elif phase != "ready":
                log.info("updates: install refused -- phase is %s, not ready", phase)
                raise UpdateError("NOT_READY")
            if offer is None:
                log.info("updates: install refused -- no offer on record")
                raise UpdateError("NOT_READY")
            if not self._is_installable():
                log.info("updates: install refused -- this is not an installed Windows build")
                raise UpdateError("UNSUPPORTED_PLATFORM")
            try:
                path = self._installer_path(offer["version"])
                newer = is_newer(offer["version"], self._app_version)
            except (KeyError, ValueError, TypeError):
                log.error("updates: install refused -- the recorded offer has no usable version")
                raise UpdateError("NOT_READY")
            if not newer:
                log.info("updates: install refused -- %s is not newer than %s", offer["version"], self._app_version)
                raise UpdateError("NOT_READY")
            # Hash again NOW: the file has been sitting in a user-writable
            # folder since the download, for as long as the person took to click.
            try:
                good = sha256_of(path) == offer["checksum_sha256"]
            except OSError:
                good = False
            if not good:
                log.error("updates: installer failed its re-check before launch; deleted, NOT launched")
                self._delete_quietly(path)
                self._state.update(phase="error", error_code="INSTALLER_TAMPERED", progress=None)
                self._persist()
                raise UpdateError("INSTALLER_TAMPERED")
            args = list(INSTALLER_ARGS)
            install_dir = self._install_dir or os.path.dirname(sys.executable)
            if not _directory_is_writable(install_dir):
                # A per-machine install (Program Files) needs the elevated path.
                args.append("/ALLUSERS")
            # An installer replaces the program, and a new build may migrate the
            # shop's database. If the migration goes wrong the only way back is
            # a copy taken BEFORE it ran -- so no copy, no install. This is
            # deliberately the last gate before the launch: a refused install
            # leaves phase 'ready' and the downloaded file in place, so the
            # person can fix the cause (disk full) and press Install again.
            backup_name = None
            if self._pre_install_backup is not None:
                try:
                    backup_name = self._pre_install_backup()
                except Exception:
                    log.exception("updates: pre-update backup failed; installer NOT launched")
                    raise UpdateError("PRE_UPDATE_BACKUP_FAILED", "Could not back up the shop before updating.")
                log.info("updates: pre-update backup %s taken", backup_name)
            self._state.update(phase="installing", installing_since=self._clock(), error_code=None,
                               pre_update_backup=backup_name)
            self._persist()
            try:
                self._launch(str(path), args)
            except Exception:
                log.exception("updates: could not start the installer")
                self._state.update(phase="error", error_code="INSTALL_LAUNCH_FAILED", installing_since=None)
                self._persist()
                raise UpdateError("INSTALL_LAUNCH_FAILED")
            log.info("updates: installer for %s launched (%d args)", offer["version"], len(args))
            return self.status()

    def check_quietly(self) -> None:
        """For the scheduler: a failed poll is logged, never raised."""
        try:
            self.check()
        except UpdateError as exc:
            log.info("updates: scheduled check ended with %s", exc.code)
        except Exception:
            log.exception("updates: scheduled check crashed")


def _offer_to_dict(offer: ReleaseOffer) -> dict:
    return {
        "product_version_id": offer.product_version_id,
        "version": offer.version,
        "build_number": offer.build_number,
        "release_channel": offer.release_channel,
        "checksum_sha256": offer.checksum_sha256,
        "size_bytes": offer.size_bytes,
        "release_notes": offer.release_notes,
        "published_at": offer.published_at,
        "update_required": offer.update_required,
        "below_min_supported": offer.below_min_supported,
    }
