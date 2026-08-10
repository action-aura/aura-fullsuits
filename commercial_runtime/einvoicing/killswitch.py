"""JoFotara e-invoicing -- file-based kill switch.

Deliberately a clone of scripts/sync/lib/Guards.ps1::Test-PauseGuard /
scripts/sync/aura-sync.ps1's `.autosync/PAUSED` flag file -- same content
format, same auto-expiry behavior, same "malformed file counts as active"
fail-safe direction -- so this repo has one operational pattern for "stop an
automated background thing right now" instead of two.

This is the FASTEST of the three disable layers (see settings.py for the
other two: the AURA_EINVOICING_DISABLED env var, and the per-company DB
`enabled` setting). Dropping/removing this one file takes effect on the
worker's very next tick -- no request round trip, no app restart.

File lives at <app_data_dir>/einvoicing/DISABLED. Format:

    reason=<reason>
    setAtUtc=<iso8601>
    expiresAtUtc=<iso8601>      # only present when a duration was given
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import NamedTuple, Optional


class KillswitchStatus(NamedTuple):
    disabled: bool
    reason: Optional[str]


def _flag_path(app_data_dir: str) -> str:
    d = os.path.join(app_data_dir, 'einvoicing')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'DISABLED')


def set_disabled(app_data_dir: str, reason: str, minutes: Optional[int] = None) -> None:
    path = _flag_path(app_data_dir)
    lines = [f"reason={reason}", f"setAtUtc={datetime.now(timezone.utc).isoformat()}"]
    if minutes and minutes > 0:
        from datetime import timedelta
        expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        lines.append(f"expiresAtUtc={expires.isoformat()}")
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    os.replace(tmp_path, path)


def clear_disabled(app_data_dir: str) -> None:
    try:
        os.remove(_flag_path(app_data_dir))
    except FileNotFoundError:
        pass


def is_disabled(app_data_dir: str) -> KillswitchStatus:
    """Mirrors Test-PauseGuard exactly: an expired flag is auto-removed and
    treated as not-disabled; a present-but-unparseable/unreadable flag
    counts as disabled (fail safe, not fail open) rather than being ignored."""
    path = _flag_path(app_data_dir)
    if not os.path.exists(path):
        return KillswitchStatus(False, None)

    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError:
        return KillswitchStatus(True, 'unreadable-flag-file')

    reason = 'manual'
    expires = None
    for line in content.splitlines():
        if line.startswith('reason='):
            reason = line[len('reason='):].strip() or 'manual'
        elif line.startswith('expiresAtUtc='):
            raw = line[len('expiresAtUtc='):].strip()
            try:
                expires = datetime.fromisoformat(raw)
                if expires.tzinfo is None:
                    expires = expires.replace(tzinfo=timezone.utc)
            except ValueError:
                return KillswitchStatus(True, 'malformed-flag-file')

    if expires is not None and expires < datetime.now(timezone.utc):
        clear_disabled(app_data_dir)
        return KillswitchStatus(False, None)

    return KillswitchStatus(True, reason)
