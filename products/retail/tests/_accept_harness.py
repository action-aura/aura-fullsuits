"""Adversarial-ACCEPTANCE-pass harness glue: spawns `_accept_device.py`
subprocesses instead of `_stock_sync_device.py`.

Not a modification of `_stock_sync_harness.py` -- that module's own
`run_device()` hardcodes `_DEVICE_SCRIPT = .../"_stock_sync_device.py"`, so
it cannot be pointed at `_accept_device.py` without editing a tracked file.
This module is a thin, standalone re-implementation of the same subprocess
contract (spawn, write actions JSON in, read results JSON out, raise with
full stdout+stderr on any non-zero exit) aimed at `_accept_device.py`
instead. `FileRelay` itself is imported UNCHANGED from `_stock_sync_harness`
-- it is a pure cross-process SQLite relay double with no dependency on
which device script is on the other end of it, so reusing it carries no risk
of masking anything this acceptance pass is trying to prove.
"""
from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from _stock_sync_harness import FileRelay  # noqa: F401  (re-exported for callers)

_DEVICE_SCRIPT = Path(__file__).resolve().parent / "_accept_device.py"

# The last commit BEFORE Track S / registry v9: retail schema v41, registry v8,
# no `device`/`admin_device` entities, no `display_name`/`role_preset`, a role
# change that reseeds only the eight pre-v9 capability codes. It is what a shop
# still running last month's build looks like, which is the "other half" of
# every mixed-build test. Pinned by SHA so it cannot drift under a test.
OLD_BUILD_COMMIT = "1d15b35e"
_OLD_BUILD_PATHS = ("commercial_runtime", "products/retail")


def export_old_build(commit: str = OLD_BUILD_COMMIT) -> Path:
    """A read-only checkout of `commit` (product code only) usable as a
    device's `code_root`. Built once per machine under the temp dir and reused.

    `AURA_OLD_BUILD_TREE` overrides it with a ready-made tree. Otherwise the
    tree is produced with `git archive`, which only READS the repository. A
    missing git, or a commit that is not in this clone, RAISES: a mixed-build
    test that quietly fell back to the new code would prove nothing about
    mixing builds, so there is deliberately no skip path here.
    """
    override = os.environ.get("AURA_OLD_BUILD_TREE")
    if override:
        return Path(override)
    dest = Path(tempfile.gettempdir()) / f"aura_old_build_{commit}"
    marker = dest / ".export_complete"
    if marker.exists():
        return dest
    repo_root = Path(__file__).resolve().parents[3]
    if dest.exists():
        # A previous export died half way; a partial tree is worse than none.
        import shutil
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    proc = subprocess.Popen(
        ["git", "-C", str(repo_root), "archive", "--format=tar", commit, *_OLD_BUILD_PATHS],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
        tar.extractall(dest)
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError(f"git archive {commit} failed: {err}")
    marker.write_text(commit, encoding="utf-8")
    return dest


def run_accept_device(
    python_exe: str,
    app_data_dir: Path,
    relay_db_path: Path,
    actions: list[dict],
    timeout: float = 60.0,
    options: dict | None = None,
) -> list[Any]:
    """`options` is opt-in and passed to the device script as argv[5] (JSON):
    `code_root` (run the device from ANOTHER checkout, e.g. an old-build tree),
    `registry_relay` (a FileRelay db path for the registry stream), and
    `arm_provider` (stamp/guard retail events with the schema version). Absent
    options leave behaviour byte-for-byte as before."""
    app_data_dir = Path(app_data_dir)
    app_data_dir.mkdir(parents=True, exist_ok=True)
    actions_path = app_data_dir / "_actions_in.json"
    output_path = app_data_dir / "_actions_out.json"
    actions_path.write_text(json.dumps(actions), encoding="utf-8")
    if output_path.exists():
        output_path.unlink()

    argv = [python_exe, str(_DEVICE_SCRIPT), str(app_data_dir),
            str(relay_db_path), str(actions_path), str(output_path)]
    if options:
        argv.append(json.dumps(options))
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            f"accept device subprocess failed (exit={proc.returncode}) for app_data_dir={app_data_dir}\n"
            f"actions={actions!r}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return json.loads(output_path.read_text(encoding="utf-8"))
