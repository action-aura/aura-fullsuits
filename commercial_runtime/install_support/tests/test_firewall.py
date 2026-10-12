"""INSTALL-1 item 4 (+ verifier M2/M3): the shop-network firewall rule, with every
Windows call stubbed. The stubs COUNT their calls: "the rule was not added
twice" has to be "the elevated step ran zero times", not "the status says
present".

Verifier M3 changes pinned here: the rule is looked up by PROGRAM PATH (a rule
with our name for another exe is not ours), Block rules left by a Cancel on
Windows' own prompt are reported and never touched, and the ask is also made
after a licence activation, with at most one prompt in flight across triggers.
"""
import sys
import threading

import pytest
from flask import Flask

from commercial_runtime.install_support import firewall

EXE = r"C:\Aura\AuraRetail.exe"
OTHER_EXE = r"C:\Users\Second\AppData\Local\Aura\AuraRetail.exe"


@pytest.fixture(autouse=True)
def _no_prompt_left_in_flight():
    firewall._in_flight.clear()
    yield
    firewall._in_flight.clear()


def rule(program=EXE, direction="Inbound", action="Allow", enabled="True", name="x"):
    return {"name": name, "direction": direction, "action": action, "enabled": enabled, "program": program}


class Env:
    """list_rules / run_elevated doubles. A successful elevated add appends
    `added_rule` to what the lookup returns."""

    def __init__(self, rules=None, outcome="ok", added_rule="default", lookup_error=None):
        self.rules = list(rules or [])
        self.outcome = outcome
        self.added_rule = rule() if added_rule == "default" else added_rule
        self.lookup_error = lookup_error
        self.lookups = []
        self.elevated = []

    def list_rules(self, exe):
        self.lookups.append(exe)
        if self.lookup_error:
            raise self.lookup_error
        return list(self.rules)

    def run_elevated(self, exe):
        self.elevated.append(exe)
        if self.outcome == "ok" and self.added_rule is not None:
            self.rules.append(self.added_rule)
        return self.outcome


def ensure(tmp_path, env, **kw):
    return firewall.ensure_firewall_rule(
        tmp_path, exe=kw.pop("exe", EXE),
        installed_build=kw.pop("installed_build", lambda: True),
        list_rules=env.list_rules, run_elevated=env.run_elevated, **kw)


def test_adds_the_rule_once_when_it_is_missing(tmp_path):
    env = Env()
    state = ensure(tmp_path, env)
    assert env.elevated == [EXE]                                # the UAC step RAN, once
    assert state["status"] == firewall.ADDED
    assert firewall.read_state(tmp_path)["status"] == firewall.ADDED
    assert state["rule_name"] == firewall.rule_name_for(EXE)


def test_skips_when_an_allow_rule_for_this_exe_is_already_there(tmp_path):
    env = Env(rules=[rule()])
    state = ensure(tmp_path, env)
    assert env.elevated == []                                   # no prompt, nothing added
    assert env.lookups == [EXE]                                 # and the lookup DID happen, for this exe
    assert state["status"] == firewall.PRESENT


def test_a_rule_for_another_exe_is_not_ours(tmp_path):
    # M3b: the old name-only lookup read "present" for a second user's install,
    # a reinstall to another folder or a leftover test install.
    env = Env(rules=[rule(program=OTHER_EXE, name=firewall.rule_name_for(OTHER_EXE))])
    state = ensure(tmp_path, env)
    assert env.elevated == [EXE]
    assert state["status"] == firewall.ADDED


def test_program_paths_compare_case_and_slash_insensitively(tmp_path):
    env = Env(rules=[rule(program=EXE.upper().replace("\\", "/"))])
    assert ensure(tmp_path, env)["status"] == firewall.PRESENT
    assert env.elevated == []


@pytest.mark.parametrize("not_a_working_allow", [
    rule(direction="Outbound"),
    rule(enabled="False"),
    rule(action="Block", direction="Outbound"),
])
def test_outbound_disabled_and_outbound_block_rules_do_not_count_either_way(tmp_path, not_a_working_allow):
    env = Env(rules=[not_a_working_allow])
    state = ensure(tmp_path, env)
    assert state["status"] == firewall.ADDED and env.elevated == [EXE]


def test_numeric_enum_values_are_understood(tmp_path):
    env = Env(rules=[rule(direction="1", action="2", enabled="1")])
    assert ensure(tmp_path, env)["status"] == firewall.PRESENT


# -- M3c: Cancel on Windows' own prompt ---------------------------------------------
def test_inbound_block_rules_are_reported_not_touched_and_not_prompted_over(tmp_path):
    block = rule(action="Block", name="Aura Retail")
    env = Env(rules=[block])
    state = ensure(tmp_path, env)
    assert state["status"] == firewall.BLOCKED == "blocked_by_windows_prompt"
    assert firewall.read_state(tmp_path)["status"] == "blocked_by_windows_prompt"
    assert env.elevated == []                                   # no UAC on top of a user's Block
    assert env.rules == [block]                                 # and nothing deleted


def test_a_block_wins_even_when_an_allow_also_exists(tmp_path):
    env = Env(rules=[rule(), rule(action="Block")])
    assert ensure(tmp_path, env)["status"] == firewall.BLOCKED


def test_a_block_that_appears_after_our_add_is_reported(tmp_path):
    env = Env(added_rule=rule(action="Block"))
    assert ensure(tmp_path, env)["status"] == firewall.BLOCKED


def test_a_block_for_another_exe_is_not_ours_to_report(tmp_path):
    env = Env(rules=[rule(program=OTHER_EXE, action="Block")])
    assert ensure(tmp_path, env)["status"] == firewall.ADDED


# -- decline / failure handling -----------------------------------------------------
def test_a_second_call_after_adding_does_not_prompt_again(tmp_path):
    env = Env()
    ensure(tmp_path, env)
    ensure(tmp_path, env)
    assert len(env.elevated) == 1


def test_declined_uac_is_recorded_and_not_asked_again(tmp_path):
    env = Env(outcome="declined")
    state = ensure(tmp_path, env)
    assert state["status"] == firewall.DECLINED
    assert firewall.read_state(tmp_path)["status"] == firewall.DECLINED
    ensure(tmp_path, env)
    assert len(env.elevated) == 1                               # not re-prompted on the next trigger


def test_a_decline_for_one_exe_does_not_silence_a_different_exe(tmp_path):
    env = Env(outcome="declined")
    ensure(tmp_path, env)
    env.outcome = "ok"
    env.added_rule = rule(program=OTHER_EXE)
    state = ensure(tmp_path, env, exe=OTHER_EXE)
    assert len(env.elevated) == 2 and state["status"] == firewall.ADDED


def test_an_explicit_retry_asks_again_after_a_decline(tmp_path):
    env = Env(outcome="declined")
    ensure(tmp_path, env)
    env.outcome = "ok"
    state = ensure(tmp_path, env, retry_after_decline=True)
    assert len(env.elevated) == 2 and state["status"] == firewall.ADDED


def test_netsh_success_without_a_rule_for_this_exe_is_a_failure_not_a_green_status(tmp_path):
    env = Env(outcome="ok", added_rule=None)
    assert ensure(tmp_path, env)["status"] == firewall.FAILED
    env = Env(outcome="ok", added_rule=rule(program=OTHER_EXE))   # the name exists, for the wrong program
    assert ensure(tmp_path, env)["status"] == firewall.FAILED


def test_an_elevated_failure_is_recorded_as_failed(tmp_path):
    assert ensure(tmp_path, Env(outcome="failed"))["status"] == firewall.FAILED


def test_a_failed_lookup_is_a_failure_and_never_prompts(tmp_path):
    env = Env(lookup_error=RuntimeError("no powershell"))
    assert ensure(tmp_path, env)["status"] == firewall.FAILED
    assert env.elevated == []


# -- never on a dev run -------------------------------------------------------------
def test_never_on_a_dev_run(tmp_path):
    env = Env()
    state = ensure(tmp_path, env, installed_build=lambda: False)
    assert env.elevated == [] and env.lookups == []
    assert state["status"] == firewall.SKIPPED_DEV
    assert firewall.read_state(tmp_path) == {}                  # nothing recorded either


def test_the_real_default_decides_never_on_a_dev_run(tmp_path):
    # M2/F10: the injected stub above cannot catch the DEFAULT being wrong.
    # This process is a dev run (pytest, not frozen): with nothing injected,
    # neither the lookup nor the UAC step may run.
    env = Env()
    state = firewall.ensure_firewall_rule(tmp_path, exe=EXE, list_rules=env.list_rules,
                                          run_elevated=env.run_elevated)
    assert state["status"] == firewall.SKIPPED_DEV
    assert env.lookups == [] and env.elevated == []


@pytest.mark.parametrize("platform,frozen,expected", [
    ("win32", True, True),
    ("win32", False, False),        # python app.py on Windows
    ("linux", True, False),         # a frozen non-Windows build has no netsh
    ("darwin", False, False),
])
def test_default_is_installed_build_truth_table(monkeypatch, platform, frozen, expected):
    monkeypatch.setattr(sys, "platform", platform)
    if frozen:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)
    assert firewall.default_is_installed_build() is expected


# -- the rule itself ----------------------------------------------------------------
def test_the_rule_is_inbound_private_and_domain_only_for_this_exe():
    params = firewall.add_rule_params(r"C:\Program Files\Aura\AuraRetail.exe")
    assert 'name="%s"' % firewall.rule_name_for(r"C:\Program Files\Aura\AuraRetail.exe") in params
    assert "dir=in" in params and "action=allow" in params and "enable=yes" in params
    assert 'program="C:\\Program Files\\Aura\\AuraRetail.exe"' in params
    assert "profile=private,domain" in params
    assert "public" not in params


def test_two_installs_get_two_different_rule_names():
    a, b = firewall.rule_name_for(EXE), firewall.rule_name_for(OTHER_EXE)
    assert a != b and a.startswith(firewall.RULE_NAME) and b.startswith(firewall.RULE_NAME)
    assert a == firewall.rule_name_for(EXE.lower().replace("\\", "/"))      # stable for one path


@pytest.mark.parametrize("bad", ['C:\\x" & calc & "', "C:\\x\nnet user"])
def test_an_exe_path_that_could_break_out_of_the_quotes_is_refused(bad):
    with pytest.raises(ValueError):
        firewall.add_rule_params(bad)


def test_the_lookup_script_filters_by_program_and_escapes_quotes():
    script = firewall._lookup_script("C:\\Bob's\\AuraRetail.exe")
    assert "-Program 'C:\\Bob''s\\AuraRetail.exe'" in script
    assert "Get-NetFirewallApplicationFilter" in script and "Get-NetFirewallRule" in script


# -- triggers: activation + connect-a-device, one prompt in flight ---------------------
class _SyncThread:
    """Runs the target immediately (a real thread would race the assertions)
    and records that it was started."""
    started = 0

    def __init__(self, target, **_):
        self._target = target

    def start(self):
        type(self).started += 1
        self._target()


def test_activation_hook_runs_the_product_hook_then_asks_once(tmp_path):
    order = []
    hook = firewall.with_firewall_ask_after_activation(
        lambda: order.append("product"), str(tmp_path),
        ask=lambda d: order.append(("ask", d)) or True)
    hook()
    assert order == ["product", ("ask", str(tmp_path))]


def test_activation_hook_still_asks_when_the_product_hook_fails_and_never_masks_an_ask_failure(tmp_path):
    asked = []

    def boom():
        raise RuntimeError("bookkeeping")

    with pytest.raises(RuntimeError):
        firewall.with_firewall_ask_after_activation(boom, str(tmp_path), ask=lambda d: asked.append(d))()
    assert asked == [str(tmp_path)]

    def bad_ask(d):
        raise OSError("thread limit")

    firewall.with_firewall_ask_after_activation(None, str(tmp_path), ask=bad_ask)()     # must not raise


def test_the_activation_ask_really_reaches_ensure_with_the_app_data_dir(tmp_path):
    calls = []
    hook = firewall.with_firewall_ask_after_activation(
        None, str(tmp_path),
        ask=lambda d: firewall.request_rule_async(d, ensure=lambda dd, **k: calls.append((dd, k)),
                                                  thread_factory=_SyncThread))
    hook()
    assert calls == [(str(tmp_path), {})]


def test_one_prompt_in_flight_across_both_triggers(tmp_path):
    gate = threading.Event()
    calls = []

    def slow(d, **k):
        calls.append(1)
        gate.wait(5)
        return {}

    assert firewall.request_rule_async(str(tmp_path), ensure=slow) is True       # e.g. the activation
    assert firewall.request_rule_async(str(tmp_path), ensure=slow) is False      # e.g. "Connect a device"
    gate.set()
    for _ in range(100):
        if not firewall._in_flight.is_set():
            break
        threading.Event().wait(0.05)
    assert calls == [1]
    assert firewall.request_rule_async(str(tmp_path), ensure=lambda d, **k: {}, thread_factory=_SyncThread) is True


def test_a_crashing_ensure_releases_the_in_flight_flag(tmp_path):
    def boom(d, **k):
        raise RuntimeError("x")

    assert firewall.request_rule_async(str(tmp_path), ensure=boom, thread_factory=_SyncThread) is True
    assert not firewall._in_flight.is_set()


def _client(tmp_path, ensure_fn):
    _SyncThread.started = 0
    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(firewall.make_firewall_blueprint(str(tmp_path), ensure=ensure_fn, thread_factory=_SyncThread))

    @app.route("/api/site-relay/pair-code", methods=["POST"])
    def pair():
        from flask import session
        return ({"success": False}, 400) if session.get("fail") else {"success": True}

    client = app.test_client()
    with client.session_transaction() as s:
        s["mt_user_id"] = 1
        s["mt_role"] = "admin"
    return client


def test_pressing_connect_a_device_asks_for_the_rule_once(tmp_path):
    calls = []
    client = _client(tmp_path, lambda d, **k: calls.append((d, k)) or {})
    assert client.post("/api/site-relay/pair-code").status_code == 200
    assert len(calls) == 1 and calls[0][1] == {}


def test_a_failed_or_non_admin_pair_code_press_asks_for_nothing(tmp_path):
    calls = []
    client = _client(tmp_path, lambda d, **k: calls.append(1) or {})
    with client.session_transaction() as s:
        s["fail"] = True
    assert client.post("/api/site-relay/pair-code").status_code == 400
    with client.session_transaction() as s:
        s["fail"] = False
        s["mt_role"] = "cashier"
    assert client.post("/api/site-relay/pair-code").status_code == 200
    assert calls == []


def test_status_route_reports_the_recorded_state_and_retry_is_admin_only(tmp_path):
    calls = []
    client = _client(tmp_path, lambda d, **k: calls.append(k) or {})
    firewall._write_state(tmp_path, firewall.BLOCKED, "x.exe")
    body = client.get("/api/site-relay/firewall").get_json()
    assert body["status"] == "blocked_by_windows_prompt"        # what a Settings line will read
    assert client.post("/api/site-relay/firewall/retry").get_json()["started"] is True
    assert calls == [{"retry_after_decline": True}]
    with client.session_transaction() as s:
        s["mt_role"] = "cashier"
    assert client.post("/api/site-relay/firewall/retry").status_code == 403
    assert len(calls) == 1


def test_two_quick_presses_cannot_open_two_prompts(tmp_path):
    gate = threading.Event()
    calls = []

    def slow(d, **k):
        calls.append(1)
        gate.wait(5)
        return {}

    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(firewall.make_firewall_blueprint(str(tmp_path), ensure=slow))
    app.add_url_rule("/api/site-relay/pair-code", "p", lambda: {"success": True}, methods=["POST"])
    client = app.test_client()
    with client.session_transaction() as s:
        s["mt_role"] = "admin"
    client.post("/api/site-relay/pair-code")
    client.post("/api/site-relay/pair-code")
    gate.set()
    assert len(calls) == 1
