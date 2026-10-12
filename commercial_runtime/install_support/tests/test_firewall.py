"""INSTALL-1 item 4: the shop-network firewall rule, with every Windows call
stubbed. The stubs COUNT their calls: "the rule was not added twice" has to be
"the elevated step ran zero times", not "the status says present".
"""
import threading

import pytest
from flask import Flask

from commercial_runtime.install_support import firewall


class Env:
    """rule_exists / run_elevated doubles. `exists_after` flips what the rule
    lookup answers once the elevated step has run (a successful add)."""

    def __init__(self, exists=False, outcome="ok", exists_after=True):
        self.exists = exists
        self.outcome = outcome
        self.exists_after = exists_after
        self.lookups = 0
        self.elevated = []

    def rule_exists(self):
        self.lookups += 1
        return self.exists

    def run_elevated(self, exe):
        self.elevated.append(exe)
        if self.outcome == "ok":
            self.exists = self.exists_after
        return self.outcome


def ensure(tmp_path, env, **kw):
    return firewall.ensure_firewall_rule(
        tmp_path, exe=kw.pop("exe", r"C:\Aura\AuraRetail.exe"),
        installed_build=kw.pop("installed_build", lambda: True),
        rule_exists=env.rule_exists, run_elevated=env.run_elevated, **kw)


def test_adds_the_rule_once_when_it_is_missing(tmp_path):
    env = Env(exists=False)
    state = ensure(tmp_path, env)
    assert env.elevated == [r"C:\Aura\AuraRetail.exe"]          # the UAC step RAN, once
    assert state["status"] == firewall.ADDED
    assert firewall.read_state(tmp_path)["status"] == firewall.ADDED


def test_skips_when_the_rule_is_already_there(tmp_path):
    env = Env(exists=True)
    state = ensure(tmp_path, env)
    assert env.elevated == []                                   # no prompt, nothing added
    assert env.lookups == 1                                     # and the lookup DID happen
    assert state["status"] == firewall.PRESENT


def test_a_second_call_after_adding_does_not_prompt_again(tmp_path):
    env = Env(exists=False)
    ensure(tmp_path, env)
    ensure(tmp_path, env)
    assert len(env.elevated) == 1


def test_declined_uac_is_recorded_and_not_asked_again(tmp_path):
    env = Env(exists=False, outcome="declined")
    state = ensure(tmp_path, env)
    assert state["status"] == firewall.DECLINED
    assert firewall.read_state(tmp_path)["status"] == firewall.DECLINED
    ensure(tmp_path, env)
    assert len(env.elevated) == 1                               # not re-prompted on the next press


def test_an_explicit_retry_asks_again_after_a_decline(tmp_path):
    env = Env(exists=False, outcome="declined")
    ensure(tmp_path, env)
    env.outcome = "ok"
    state = ensure(tmp_path, env, retry_after_decline=True)
    assert len(env.elevated) == 2 and state["status"] == firewall.ADDED


def test_netsh_success_without_the_rule_is_a_failure_not_a_green_status(tmp_path):
    env = Env(exists=False, outcome="ok", exists_after=False)
    assert ensure(tmp_path, env)["status"] == firewall.FAILED


def test_an_elevated_failure_is_recorded_as_failed(tmp_path):
    env = Env(exists=False, outcome="failed")
    assert ensure(tmp_path, env)["status"] == firewall.FAILED


def test_never_on_a_dev_run(tmp_path):
    env = Env(exists=False)
    state = ensure(tmp_path, env, installed_build=lambda: False)
    assert env.elevated == [] and env.lookups == 0
    assert state["status"] == firewall.SKIPPED_DEV
    assert firewall.read_state(tmp_path) == {}                  # nothing recorded either


def test_the_rule_is_inbound_private_and_domain_only_for_this_exe():
    params = firewall.add_rule_params(r"C:\Program Files\Aura\AuraRetail.exe")
    assert 'name="Aura Retail (shop network)"' in params
    assert "dir=in" in params and "action=allow" in params and "enable=yes" in params
    assert 'program="C:\\Program Files\\Aura\\AuraRetail.exe"' in params
    assert "profile=private,domain" in params
    assert "public" not in params


@pytest.mark.parametrize("bad", ['C:\\x" & calc & "', "C:\\x\nnet user"])
def test_an_exe_path_that_could_break_out_of_the_quotes_is_refused(bad):
    with pytest.raises(ValueError):
        firewall.add_rule_params(bad)


# -- the route + trigger ---------------------------------------------------------
class _SyncThread:
    """Runs the target immediately (a real thread would race the assertions)
    and records that it was started."""
    started = 0

    def __init__(self, target, kwargs=None, **_):
        self._target, self._kwargs = target, kwargs or {}

    def start(self):
        type(self).started += 1
        self._target(**self._kwargs)


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
    firewall._write_state(tmp_path, firewall.DECLINED, "x.exe")
    body = client.get("/api/site-relay/firewall").get_json()
    assert body["status"] == "declined"
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

    class _BgThread:
        def __init__(self, target, kwargs=None, **_):
            self._t = threading.Thread(target=target, kwargs=kwargs or {}, daemon=True)

        def start(self):
            self._t.start()

    app = Flask(__name__)
    app.secret_key = "t"
    app.register_blueprint(firewall.make_firewall_blueprint(str(tmp_path), ensure=slow, thread_factory=_BgThread))
    app.add_url_rule("/api/site-relay/pair-code", "p", lambda: {"success": True}, methods=["POST"])
    client = app.test_client()
    with client.session_transaction() as s:
        s["mt_role"] = "admin"
    client.post("/api/site-relay/pair-code")
    client.post("/api/site-relay/pair-code")
    gate.set()
    assert len(calls) == 1
