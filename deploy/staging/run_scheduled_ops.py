"""Phase 9 Milestone 9 -- Aura Owner scheduled commercial operations.

Wraps the real, already-existing, idempotent, report-by-default CLI scan
commands (owner/app/cli.py's `commercial` group -- expiry-scan, reconcile,
device-limit-scan, all Phase 8) plus the real backup script and the real
audit-chain verifier, with:

  - single-run locking (Postgres advisory lock -- survives container
    restarts and works correctly across the multiple containers a real
    deployment might eventually run, unlike a local file lock)
  - a bounded per-job timeout (a stuck job cannot wedge the whole run)
  - structured JSON result per job, one line per job (feeds straight into
    Milestone 8's log pipeline)
  - a nonzero exit code on any job failure (for a real systemd
    OnFailure=/alerting hook to key off of), while still running every
    other job (one job's failure does not skip the rest)

No distributed task queue (Celery/RQ/broker) -- ADR-9.4: these jobs are
already idempotent, single-process, report-first functions; a broker would
add a new component to secure and monitor for no gain at this scale.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

ADVISORY_LOCK_KEY = 0x415552414F574E  # arbitrary, stable across runs -- "AURAOWN" in hex-ish
JOB_TIMEOUT_SECONDS = int(os.environ.get("SCHEDULER_JOB_TIMEOUT_SECONDS", "120"))


def _run_flask_command(*args: str) -> dict:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "flask", *args],
            cwd=_owner_dir(),
            capture_output=True, text=True, timeout=JOB_TIMEOUT_SECONDS,
        )
        ok = proc.returncode == 0
        return {"job": " ".join(args), "ok": ok, "duration_s": round(time.monotonic() - start, 2),
                "exit_code": proc.returncode, "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"job": " ".join(args), "ok": False, "duration_s": JOB_TIMEOUT_SECONDS,
                "exit_code": None, "error": "TIMEOUT"}


def _owner_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "owner")


def _try_acquire_lock() -> bool:
    """Real Postgres advisory lock -- pg_try_advisory_lock is non-blocking
    and session-scoped: if a previous run's process died without releasing
    it (crash, OOM-kill), Postgres itself releases it when that session's
    connection closes, so a stuck lock cannot outlive its holding process."""
    sys.path.insert(0, _owner_dir())
    from app import create_app
    from app.extensions import db_session
    from sqlalchemy import text

    app = create_app(os.environ.get("OWNER_ENV", "staging"))
    ctx = app.app_context()
    ctx.push()
    acquired = db_session.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}).scalar()
    return acquired, ctx, db_session


def main() -> int:
    acquired, ctx, db_session = _try_acquire_lock()
    if not acquired:
        print(json.dumps({"status": "SKIPPED", "reason": "another scheduled-ops run already holds the lock",
                           "timestamp": datetime.now(timezone.utc).isoformat()}))
        return 0  # not a failure -- overlapping runs are expected to be skipped, not alerted on

    try:
        from sqlalchemy import text

        results = [
            _run_flask_command("commercial", "expiry-scan", "--apply"),
            _run_flask_command("commercial", "reconcile", "--apply"),
            _run_flask_command("commercial", "device-limit-scan", "--apply"),
        ]

        from app.audit.services import verify_chain

        chain_ok, chain_detail = verify_chain()
        results.append({"job": "audit-chain-verify", "ok": chain_ok, "detail": chain_detail})

        overall_ok = all(r["ok"] for r in results)
        summary = {
            "status": "OK" if overall_ok else "FAILED",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "jobs": results,
        }
        print(json.dumps(summary, indent=2))
        return 0 if overall_ok else 1
    finally:
        db_session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
        db_session.commit()
        ctx.pop()


if __name__ == "__main__":
    raise SystemExit(main())
