"""Aura Retail -- Phase 5 wave B (stock-moving sync) multi-process harness.

WHY THIS FILE EXISTS, AND WHY IT IS NOT retail_money_sync_test.py's
`install_b` fixture again.

Wave A's own delivered suites were blind to all three of its shipped defects,
TWICE, because `install_b` (retail_two_install_roundtrip_test.py /
retail_money_sync_test.py) builds the second device as a bare temp SQLite
database seeded straight from `database.schema.init_retail()` -- never a
second real Flask app, never a device that rings a sale of its own. Its
`doc_sequences` stay at zero forever, nothing it does ever collides with
anything, and every route-level bug (a field the emit side forgets, a check
the apply side skips) that only bites when TWO devices are independently
WRITING through the real HTTP routes simply cannot occur in that fixture,
because only one side of the pair ever writes anything at all.

The fix both wave-A verifiers independently landed on: stand up N devices in
SEPARATE OS PROCESSES, each with its own `AURA_APP_DATA`, its own
`retail.db`, its own `local_device.json`, and its own Python process globals
-- because `database/schema.py`'s own connection helpers
(`get_retail_conn`/`_conn`/`_get_path`) close over MODULE-LEVEL
`SUBSYS_DIR`/`BASE_DIR` globals resolved ONCE at import time from
`AURA_APP_DATA` (see retail_two_install_roundtrip_test.py's own module
docstring, "the AURA_APP_DATA trap", for the fuller story). Two REAL,
route-driven Flask apps can never coexist correctly in one Python process for
exactly that reason -- the second one to boot would either silently reuse the
first one's already-imported database globals, or require reimporting
`database.schema`/`config` under a different name entirely. Neither is what
"two independent, real, selling-and-adjusting-stock installs" is supposed to
mean. A subprocess sidesteps the trap for free: a fresh Python process gets
fresh module-level globals, resolved fresh from ITS OWN `AURA_APP_DATA`.

This module supplies the two pieces every stock-sync test in
retail_stock_sync_test.py needs and none of them should have to rebuild:

  1. `FileRelay` -- a `.push(events)` / `.pull(since)` double with the exact
     surface `SyncService`'s `client_factory()` result is ever asked for
     (see retail_two_install_roundtrip_test.py's own `InMemoryRelay` for the
     in-process precedent this mirrors), but backed by a SHARED SQLite file
     rather than a shared Python object -- because a Python object cannot be
     shared between two independent OS processes, and this is a file the
     relay events genuinely need to cross. Owner's real Postgres-backed relay
     (owner/app/sync/routes.py) is not available in this dev environment
     (retail_two_install_roundtrip_test.py's own "Postgres trap"); this is a
     double, not a real transport, exactly like `InMemoryRelay` already was
     -- just one that works across a process boundary instead of within one.
  2. `run_device()` -- spawns `_stock_sync_device.py` as its own subprocess
     with a device's `AURA_APP_DATA` pointed at a caller-supplied directory,
     hands it a JSON list of actions to perform against the REAL Flask app
     booted fresh inside that subprocess (create a shop, ring a sale, adjust
     stock, create a branch, push, pull, read back a balance, compute
     drift, ...), and returns the parsed JSON results list. Each call is a
     fresh process -- exactly modelling a device that was relaunched -- but
     `AURA_APP_DATA` persists on disk between calls for the SAME device, so
     a device's own state (its sales, its outbox, its sync cursor) survives
     across as many `run_device()` calls as a test needs to make for it.

Run (as part of the full file):
    pytest products/retail/tests/retail_stock_sync_test.py -v
"""
from __future__ import annotations

import json
import subprocess
import sqlite3
import sys
from pathlib import Path
from typing import Any

_DEVICE_SCRIPT = Path(__file__).resolve().parent / "_stock_sync_device.py"


class FileRelay:
    """Cross-process double for Owner's real sync relay. Same `.push(events)
    -> dict` / `.pull(since) -> {"events": [...], "cursor": N}` surface as
    retail_two_install_roundtrip_test.py's `InMemoryRelay`, backed by one
    shared SQLite file instead of a shared Python list so that N independent
    OS processes (this harness's whole point) can all push to and pull from
    the SAME relay. WAL + a generous busy_timeout: multiple device
    subprocesses may legitimately push/pull in close succession (a test's own
    push-then-immediately-pull-on-another-device sequencing), and a writer
    must never be told the database is locked just because another device's
    subprocess is mid-transaction.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        conn = self._conn()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS relay_events ("
                "seq INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL)"
            )
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def push(self, events: list) -> dict:
        conn = self._conn()
        try:
            for ev in events:
                # `id`/`created_at` (sync_outbox's own local bookkeeping
                # columns) ride along harmlessly; only entity_type/entity_id/
                # event_type/payload are ever read back out by pull() below,
                # matching what SyncRelayClient.push() actually needs.
                conn.execute("INSERT INTO relay_events (payload) VALUES (?)", (json.dumps(ev),))
            conn.commit()
        finally:
            conn.close()
        return {"stored": len(events), "received": len(events)}

    def pull(self, since: int) -> dict:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT seq, payload FROM relay_events WHERE seq > ? ORDER BY seq", (since,)
            ).fetchall()
        finally:
            conn.close()
        events = []
        cursor = since
        for seq, payload in rows:
            ev = json.loads(payload)
            ev["seq"] = seq
            events.append(ev)
            cursor = seq
        return {"events": events, "cursor": cursor}


def run_device(
    python_exe: str,
    app_data_dir: Path,
    relay_db_path: Path,
    actions: list[dict],
    timeout: float = 60.0,
) -> list[Any]:
    """Spawns `_stock_sync_device.py` as its own OS process, hands it
    `actions` (a JSON list -- see that script's own module docstring for the
    op vocabulary), and returns the parsed JSON list of per-action results.

    `app_data_dir` need not exist yet on a device's FIRST call (the device
    script creates it) -- subsequent calls for the SAME device reuse
    whatever that directory already holds, which is the entire mechanism by
    which one device's state survives being "relaunched" between calls.

    Raises `RuntimeError` (with full stdout+stderr attached to the message)
    on any non-zero exit -- a subprocess failure must never be allowed to
    look like "the device did nothing and every downstream assertion
    quietly finds nothing to check", which is how a broken harness produces
    a green pytest run for the wrong reason.
    """
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
            f"device subprocess failed (exit={proc.returncode}) for app_data_dir={app_data_dir}\n"
            f"actions={actions!r}\n"
            f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
        )
    return json.loads(output_path.read_text(encoding="utf-8"))
