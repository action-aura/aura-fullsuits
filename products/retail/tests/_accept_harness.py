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
import subprocess
from pathlib import Path
from typing import Any

from _stock_sync_harness import FileRelay  # noqa: F401  (re-exported for callers)

_DEVICE_SCRIPT = Path(__file__).resolve().parent / "_accept_device.py"


def run_accept_device(
    python_exe: str,
    app_data_dir: Path,
    relay_db_path: Path,
    actions: list[dict],
    timeout: float = 60.0,
) -> list[Any]:
    app_data_dir = Path(app_data_dir)
    app_data_dir.mkdir(parents=True, exist_ok=True)
    actions_path = app_data_dir / "_actions_in.json"
    output_path = app_data_dir / "_actions_out.json"
    actions_path.write_text(json.dumps(actions), encoding="utf-8")
    if output_path.exists():
        output_path.unlink()

    proc = subprocess.run(
        [python_exe, str(_DEVICE_SCRIPT), str(app_data_dir),
         str(relay_db_path), str(actions_path), str(output_path)],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0 or not output_path.exists():
        raise RuntimeError(
            f"accept device subprocess failed (exit={proc.returncode}) for app_data_dir={app_data_dir}\n"
            f"actions={actions!r}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return json.loads(output_path.read_text(encoding="utf-8"))
