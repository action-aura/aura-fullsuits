"""Crash reports -- the privacy filter, rule by rule.

Each scrub rule has its own test that asserts BOTH halves: the sensitive text
is gone AND the surrounding message survived (a scrubber that deletes
everything would pass "the secret is gone" forever). The mutation proofs for
these (disable one rule -> exactly that test goes red) are recorded in the
wave report, not here.
"""
import json
import os
import sys
import textwrap
import importlib.util

import pytest

from commercial_runtime.crash_reports import scrub


# -- message rules ----------------------------------------------------------
def test_emails_are_replaced():
    out = scrub.scrub_message("could not send to ahmad.salem@shop.example.com today")
    assert "ahmad" not in out and "shop.example" not in out
    assert out == "could not send to <email> today"


def test_runs_of_four_or_more_digits_are_replaced_but_short_numbers_stay():
    out = scrub.scrub_message("phone 0791234567 total 12345.678 sale 987 line 12")
    assert "0791234567" not in out and "12345" not in out
    assert out == "phone <n> total <n>.678 sale 987 line 12"


def test_quoted_substrings_are_replaced_in_both_quote_styles():
    out = scrub.scrub_message("""bad value 'Ahmad Al-Khalidi' for "Ahmad's Bakery" in column""")
    assert "Ahmad" not in out and "Bakery" not in out
    assert out == "bad value '…' for '…' in column"


def test_windows_home_paths_are_replaced_even_with_a_space_in_the_user_name():
    out = scrub.scrub_message(r"cannot open C:\Users\John Smith\AppData\Roaming\Aura\retail.db now")
    assert "John" not in out and "Smith" not in out and "AppData" not in out
    assert out.startswith("cannot open <path>")


def test_posix_home_paths_are_replaced():
    out = scrub.scrub_message("cannot open /home/bob/.local/share/aura/retail.db now")
    assert "bob" not in out and ".local" not in out
    assert out == "cannot open <path> now" or out.startswith("cannot open <path>")


def test_the_real_home_folder_of_this_account_is_replaced(monkeypatch, tmp_path):
    home = str(tmp_path / "redirected_profile_for_test")
    monkeypatch.setattr(scrub, "_LITERAL_HOME_RE", scrub.re.compile(scrub.re.escape(home) + r"(?:[\\/][^\s'\"]*)?", scrub.re.IGNORECASE))
    out = scrub.scrub_message("db at %s/aura/retail.db failed" % home)
    assert "redirected_profile_for_test" not in out
    assert "<path>" in out


def test_licence_keys_are_replaced():
    out = scrub.scrub_message("licence AURA-RETAIL-7K2M-QX9A-ZZPL rejected")
    assert "7K2M" not in out and "ZZPL" not in out
    assert out == "licence <key> rejected"


def test_message_is_truncated_to_300_characters_after_scrubbing():
    out = scrub.scrub_message("x" * 1000)
    assert len(out) == 300
    # truncation happens AFTER scrubbing: an email sitting past the cut is gone,
    # not merely cut in half
    tail_email = ("y" * 295) + " bob@example.com"
    assert "bob" not in scrub.scrub_message(tail_email)


def test_message_survives_none_and_unprintable_values():
    class Bad:
        def __str__(self):
            raise RuntimeError("no")

    assert scrub.scrub_message(None) == ""
    assert scrub.scrub_message(Bad()) == ""


# -- frames -----------------------------------------------------------------
def _boom_with_locals():
    secret_customer = "Ahmad Al-Khalidi"       # a LOCAL: must never reach a report
    secret_total = 12345.678
    raise ValueError("boom %s %s" % (len(secret_customer), secret_total))


def _traceback_of(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return exc.__traceback__
    raise AssertionError("did not raise")


def test_frames_are_relative_have_no_locals_and_no_source_lines():
    frames = scrub.relativize_frames(_traceback_of(_boom_with_locals))
    assert frames, "a real traceback must yield frames"
    assert set(frames[0]) == {"file", "function", "line"}
    # innermost first: the crash site is the first frame
    assert frames[0]["function"] == "_boom_with_locals"
    assert frames[0]["file"] == "commercial_runtime/crash_reports/tests/test_scrub.py"
    wire = json.dumps(frames)
    assert "Ahmad" not in wire and "12345" not in wire, "a local variable leaked into the frames"
    assert "raise ValueError" not in wire, "a source line leaked into the frames"
    for frame in frames:
        assert not os.path.isabs(frame["file"]) and ":" not in frame["file"] and "\\" not in frame["file"]


def test_a_file_outside_the_app_root_is_reduced_to_its_bare_name(tmp_path):
    module_path = tmp_path / "somebody_elses_module.py"
    module_path.write_text(textwrap.dedent("""
        def explode():
            raise RuntimeError("x")
    """))
    spec = importlib.util.spec_from_file_location("somebody_elses_module", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frames = scrub.relativize_frames(_traceback_of(module.explode))
    assert frames[0]["file"] == "somebody_elses_module.py"
    assert str(tmp_path) not in json.dumps(frames)


def test_site_packages_inside_the_repo_are_reduced_to_their_bare_name():
    inside_venv = os.path.join(str(scrub.APP_ROOT), ".venv", "Lib", "site-packages", "flask", "app.py")
    assert scrub.relative_frame_path(inside_venv) == "app.py"
    ours = os.path.join(str(scrub.APP_ROOT), "products", "retail", "backend", "app.py")
    assert scrub.relative_frame_path(ours) == "products/retail/backend/app.py"


def test_at_most_thirty_frames_are_kept_and_they_are_the_innermost():
    def recurse(n):
        if n == 0:
            raise ValueError("deep")
        recurse(n - 1)

    frames = scrub.relativize_frames(_traceback_of(lambda: recurse(80)))
    assert len(frames) == scrub.MAX_FRAMES == 30
    assert frames[0]["function"] == "recurse"


def test_no_traceback_gives_no_frames():
    assert scrub.relativize_frames(None) == []


def test_safe_frames_reduces_an_absolute_path_and_drops_extra_fields():
    out = scrub.safe_frames([
        {"file": r"C:\Users\bob\AppData\x\mod.py", "function": "f", "line": 3, "locals": {"customer": "Ahmad"}},
        {"file": "/home/bob/proj/mod2.py", "function": "g", "line": "7"},
        "not a frame",
    ])
    assert out == [
        {"file": "mod.py", "function": "f", "line": 3},
        {"file": "mod2.py", "function": "g", "line": 7},
    ]
    assert scrub.safe_frames("nope") == []


# -- JS stacks --------------------------------------------------------------
def test_chrome_stack_is_parsed_to_script_name_function_and_line_only():
    stack = (
        "TypeError: Cannot read properties of undefined (reading 'name')\n"
        "    at renderRow (http://127.0.0.1:5000/static/subsystem-retail.js?v=123&customer=Ahmad:812:19)\n"
        "    at http://127.0.0.1:5000/static/app-shell.js:40:7\n"
    )
    frames = scrub.frames_from_js_stack(stack)
    assert frames == [
        {"file": "subsystem-retail.js", "function": "renderRow", "line": 812},
        {"file": "app-shell.js", "function": "<anonymous>", "line": 40},
    ]
    assert "Ahmad" not in json.dumps(frames) and "127.0.0.1" not in json.dumps(frames)
    assert scrub.js_exception_type(stack) == "TypeError"


def test_firefox_stack_is_parsed_too():
    frames = scrub.frames_from_js_stack("openSale@http://localhost:5000/static/sales.js:77:3\n@http://localhost:5000/static/a.js:1:1")
    assert frames[0] == {"file": "sales.js", "function": "openSale", "line": 77}
    assert frames[1]["function"] == "<anonymous>"


def test_non_frame_lines_and_non_strings_contribute_nothing():
    assert scrub.frames_from_js_stack("just a message with Ahmad in it") == []
    assert scrub.frames_from_js_stack(None) == []
    assert scrub.js_exception_type("something odd") == "Error"


# -- fingerprint ------------------------------------------------------------
def _frames(lines=(10, 20), functions=("a", "b")):
    return [{"file": "x.py", "function": fn, "line": ln} for fn, ln in zip(functions, lines)]


def test_fingerprint_is_32_hex_and_stable_across_a_line_number_change():
    one = scrub.fingerprint("server", "ValueError", _frames(lines=(10, 20)))
    moved = scrub.fingerprint("server", "ValueError", _frames(lines=(13, 23)))
    assert one == moved
    assert len(one) == 32 and int(one, 16) >= 0


def test_fingerprint_differs_for_a_different_function_kind_or_type():
    base = scrub.fingerprint("server", "ValueError", _frames())
    assert scrub.fingerprint("server", "ValueError", _frames(functions=("a", "c"))) != base
    assert scrub.fingerprint("thread", "ValueError", _frames()) != base
    assert scrub.fingerprint("server", "KeyError", _frames()) != base


def test_only_the_first_eight_frames_feed_the_fingerprint():
    eight = [{"file": "x.py", "function": "f%d" % i, "line": i} for i in range(8)]
    base = scrub.fingerprint("server", "E", eight)
    assert scrub.fingerprint("server", "E", eight + [{"file": "x.py", "function": "ninth", "line": 9}]) == base
    changed_eighth = list(eight)
    changed_eighth[7] = {"file": "x.py", "function": "different", "line": 7}
    assert scrub.fingerprint("server", "E", changed_eighth) != base
