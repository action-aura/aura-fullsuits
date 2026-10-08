"""Crash reports -- capture must record the crash and change NOTHING else.

The two things that matter, each proved against a control:

  * the HTTP response of a crashing route is byte-identical with and without
    capture installed (compared against a second app that has no capture --
    not against a remembered string, which would drift with Flask);
  * a failure INSIDE the reporter (a store that raises) is swallowed: the
    response is still identical to the baseline and the error is logged once.
"""
import logging
import threading

import pytest
from flask import Flask

from commercial_runtime.crash_reports import capture
from commercial_runtime.crash_reports.store import CrashStore

SECRET_LINE = "total 12345.678 for 'Ahmad Al-Khalidi' ahmad@shop.example"


def build_app(with_capture=False, store=None, version="9.9.9"):
    app = Flask(__name__)
    app.config["PROPAGATE_EXCEPTIONS"] = False    # behave like production: a 500, not a re-raise

    @app.route("/boom/<int:n>")
    def boom(n):
        raise ValueError(SECRET_LINE)

    @app.route("/fine")
    def fine():
        return "ok"

    handle = capture.install(app, store, app_version=version, platform="WINDOWS") if with_capture else None
    return app, handle


@pytest.fixture
def store(tmp_path):
    return CrashStore(tmp_path / "crash_reports.db")


def test_a_crashing_route_is_recorded_once_and_the_response_is_byte_identical(store):
    baseline_app, _ = build_app(with_capture=False)
    captured_app, handle = build_app(with_capture=True, store=store)
    try:
        calls = {"n": 0}
        real_record = handle.record

        def counting(report):
            calls["n"] += 1
            return real_record(report)

        handle.record = counting

        expected = baseline_app.test_client().get("/boom/4242")
        actual = captured_app.test_client().get("/boom/4242")

        assert calls["n"] == 1, "the capture path must have RUN exactly once"
        assert expected.status_code == actual.status_code == 500
        assert expected.get_data() == actual.get_data()
        assert expected.headers.get("Content-Type") == actual.headers.get("Content-Type")

        rows = store.all_rows()
        assert len(rows) == 1
        row = rows[0]
        assert row["kind"] == "server" and row["exception_type"] == "ValueError"
        assert row["route"] == "/boom/<int:n>", "the route PATTERN, never the real URL"
        assert row["app_version"] == "9.9.9" and row["platform"] == "WINDOWS"
        assert row["frames"][0]["function"] == "boom"
        # scrubbed before it ever touched disk
        assert row["message"] == "total <n>.678 for '…' <email>"
        assert "Ahmad" not in str(row) and "12345" not in str(row) and "4242" not in str(row)
    finally:
        handle.remove()


def test_a_request_that_does_not_crash_records_nothing(store):
    app, handle = build_app(with_capture=True, store=store)
    try:
        assert app.test_client().get("/fine").status_code == 200
        assert store.all_rows() == []
    finally:
        handle.remove()


def test_a_failure_inside_the_reporter_is_swallowed_and_changes_nothing(store, caplog):
    baseline_app, _ = build_app(with_capture=False)
    captured_app, handle = build_app(with_capture=True, store=store)
    attempts = {"n": 0}

    def broken(report):
        attempts["n"] += 1
        raise RuntimeError("disk full")

    handle.record = broken
    try:
        expected = baseline_app.test_client().get("/boom/1")
        with caplog.at_level(logging.DEBUG, logger="aura.crash_reports"):
            first = captured_app.test_client().get("/boom/1")
            second = captured_app.test_client().get("/boom/1")
        assert attempts["n"] == 2, "the broken reporter must actually have been called both times"
        assert first.status_code == second.status_code == expected.status_code
        assert first.get_data() == second.get_data() == expected.get_data()
        warnings = [r for r in caplog.records if r.name == "aura.crash_reports" and r.levelno == logging.WARNING]
        assert len(warnings) == 1, "a broken reporter is logged once, not on every crash"
    finally:
        handle.remove()


def test_an_uncaught_exception_in_a_thread_is_recorded_and_the_previous_hook_still_runs(store, monkeypatch):
    seen_by_previous = []
    monkeypatch.setattr(threading, "excepthook", lambda args: seen_by_previous.append(args.exc_type.__name__))
    app, handle = build_app(with_capture=True, store=store)
    try:
        def worker():
            raise KeyError("customer 'Ahmad' 0791234567")

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        rows = store.all_rows()
        assert len(rows) == 1 and rows[0]["kind"] == "thread" and rows[0]["route"] is None
        assert rows[0]["exception_type"] == "KeyError"
        assert "Ahmad" not in rows[0]["message"] and "0791234567" not in rows[0]["message"]
        assert seen_by_previous == ["KeyError"], "the original hook must still see the thread's death"
    finally:
        handle.remove()


def test_system_exit_in_a_thread_is_not_a_crash(store, monkeypatch):
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    app, handle = build_app(with_capture=True, store=store)
    try:
        thread = threading.Thread(target=lambda: (_ for _ in ()).throw(SystemExit()))
        thread.start()
        thread.join()
        assert store.all_rows() == []
    finally:
        handle.remove()


def test_a_failure_in_the_thread_reporter_is_swallowed_and_the_previous_hook_still_runs(store, monkeypatch):
    seen = []
    monkeypatch.setattr(threading, "excepthook", lambda args: seen.append(1))
    app, handle = build_app(with_capture=True, store=store)
    handle.record = lambda report: (_ for _ in ()).throw(RuntimeError("nope"))
    try:
        thread = threading.Thread(target=lambda: 1 / 0)
        thread.start()
        thread.join()
        assert seen == [1]
    finally:
        handle.remove()


def test_remove_restores_the_thread_hook_and_stops_capturing(store, monkeypatch):
    sentinel = lambda args: None  # noqa: E731
    monkeypatch.setattr(threading, "excepthook", sentinel)
    app, handle = build_app(with_capture=True, store=store)
    assert threading.excepthook is not sentinel
    handle.remove()
    assert threading.excepthook is sentinel
    assert app.test_client().get("/boom/1").status_code == 500
    assert store.all_rows() == []


def test_client_report_keeps_only_a_plain_screen_id_and_scrubs_the_message():
    stack = "TypeError: x\n    at f (http://127.0.0.1:5000/static/a.js?q=Ahmad:5:6)"
    good = capture.report_from_client(message="bad 'Ahmad' 0791234567", stack=stack, screen="sales",
                                      app_version="1", platform="WINDOWS")
    assert good["kind"] == "client" and good["route"] == "sales" and good["exception_type"] == "TypeError"
    assert good["message"] == "bad '…' <n>"
    assert good["frames"] == [{"file": "a.js", "function": "f", "line": 5}]
    href = capture.report_from_client(message="m", stack="", screen="http://127.0.0.1:5000/#/sales?customer=Ahmad",
                                      app_version="1", platform="WINDOWS")
    assert href["route"] is None, "a URL is not a screen id"
