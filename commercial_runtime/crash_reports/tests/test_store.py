"""Crash reports -- the local queue: counting, and the hard 200-row cap."""
import sqlite3

import pytest

from commercial_runtime.crash_reports import store as store_module
from commercial_runtime.crash_reports.store import CrashStore


def report(fp="a" * 32, version="1.0.0", **over):
    base = {
        "fingerprint": fp, "kind": "server", "exception_type": "ValueError", "message": "boom",
        "frames": [{"file": "x.py", "function": "f", "line": 1}], "route": "/r", "app_version": version,
        "platform": "WINDOWS",
    }
    base.update(over)
    return base


@pytest.fixture
def store(tmp_path):
    return CrashStore(tmp_path / "database" / "subsystems" / "crash_reports.db")


def test_a_repeat_of_the_same_bug_on_the_same_version_increments_one_row(store):
    assert store.record(report()) == 1
    assert store.record(report()) == 2
    assert store.record(report()) == 3
    rows = store.all_rows()
    assert len(rows) == 1 and rows[0]["count"] == 3
    assert rows[0]["first_seen_at"] <= rows[0]["last_seen_at"]
    assert rows[0]["frames"] == [{"file": "x.py", "function": "f", "line": 1}]


def test_a_new_version_or_a_new_fingerprint_is_a_new_row(store):
    store.record(report())
    store.record(report(version="1.0.1"))
    store.record(report(fp="b" * 32))
    assert len(store.all_rows()) == 3


def test_a_repeat_flips_a_sent_row_back_to_pending_with_the_new_total(store):
    store.record(report())
    row = store.pending(10)[0]
    assert store.mark_sent([(row["id"], row["count"])]) == 1
    assert store.pending(10) == []
    store.record(report())
    again = store.pending(10)
    assert len(again) == 1 and again[0]["count"] == 2 and again[0]["sent_count"] == 1


def test_mark_sent_ignores_a_row_whose_count_moved_while_it_was_in_flight(store):
    store.record(report())
    row = store.pending(10)[0]
    store.record(report())                                   # same crash again, mid-send
    assert store.mark_sent([(row["id"], row["count"])]) == 0
    assert store.pending(10)[0]["count"] == 2                # still owed to Owner


def test_the_cap_of_200_deletes_the_oldest_sent_rows_first(store):
    for i in range(200):
        store.record(report(fp="%032d" % i))
    rows = store.all_rows()
    assert len(rows) == 200
    # Mark an OLD pending row's neighbours sent: rows 100..109 are sent, the
    # oldest rows 0..99 stay pending. Oldest-pending must NOT go first.
    store.mark_sent([(r["id"], r["count"]) for r in rows[100:110]])
    store.record(report(fp="f" * 32))                         # 201st row -> exactly one deletion
    after = store.all_rows()
    assert len(after) == 200
    fps = {r["fingerprint"] for r in after}
    assert "%032d" % 100 not in fps, "the oldest SENT row should have been deleted first"
    assert "%032d" % 0 in fps, "an older but still-pending row must outlive a sent one"
    assert "f" * 32 in fps, "the row just written must survive"


def test_when_nothing_is_sent_the_cap_deletes_the_oldest_pending_row(store):
    for i in range(200):
        store.record(report(fp="%032d" % i))
    store.record(report(fp="e" * 32))
    fps = {r["fingerprint"] for r in store.all_rows()}
    assert len(fps) == 200
    assert "%032d" % 0 not in fps and "%032d" % 1 in fps and "e" * 32 in fps


def test_summary_counts_and_last_sent(store):
    assert store.summary() == {"pending": 0, "sent": 0, "last_sent_at": None}
    store.record(report())
    store.record(report(fp="b" * 32))
    row = store.pending(1)[0]
    store.mark_sent([(row["id"], row["count"])])
    summary = store.summary()
    assert summary["pending"] == 1 and summary["sent"] == 1 and summary["last_sent_at"]


def test_the_store_lives_in_its_own_file_and_never_touches_retail_db(tmp_path):
    path = store_module.default_store_path(tmp_path)
    assert path == tmp_path / "database" / "subsystems" / "crash_reports.db"
    opened = store_module.open_default_store(tmp_path)
    assert opened is not None and path.exists()
    assert not (tmp_path / "database" / "retail.db").exists()
    conn = sqlite3.connect(str(path))
    assert [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")] == ["crash_outbox"]
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_open_default_store_returns_none_instead_of_raising(tmp_path):
    blocker = tmp_path / "database"
    blocker.write_text("a file where a folder is needed")
    assert store_module.open_default_store(tmp_path) is None
