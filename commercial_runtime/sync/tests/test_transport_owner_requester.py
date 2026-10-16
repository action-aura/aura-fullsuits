"""SYNC-BOOT Phase 3: the desktop requester's polling loop (`OwnerRequester.run`).

Pinned here: an Owner that keeps answering a terminal status (FAILED / EXPIRED) or "I do not know that
request" must not make the loop spin. Before the fix 'retry' did `continue` with no sleep and without
counting, so it minted a key pair and called Owner as fast as the CPU allowed, forever.
"""
from types import SimpleNamespace

from commercial_runtime.sync.bootstrap.transport_owner import OwnerRequester
from commercial_runtime.sync.relay_client import RelayRejected


class _Api:
    """A mailbox that always answers one fixed status."""

    def __init__(self, status="FAILED", not_found=False):
        self.status_answer = status
        self.not_found = not_found
        self.requests = 0
        self.statuses = 0

    def request(self, **kw):
        self.requests += 1
        return {"request_id": f"r{self.requests}", "status": self.status_answer, "manifest": {}}

    def status(self, request_id):
        self.statuses += 1
        if self.not_found:
            raise RelayRejected("BOOTSTRAP_NOT_FOUND", "gone", {})
        return {"request_id": request_id, "status": self.status_answer, "manifest": {}}


def _requester(api, tmp_path):
    return OwnerRequester(
        api, spec=SimpleNamespace(schema_version=56), app_data_dir=tmp_path, relay_url="https://owner.example",
        trust_store=None, own_licence=None, expected_company_id=None)


def test_an_owner_that_keeps_answering_failed_ends_gave_up_with_sleeps_and_bounded_calls(tmp_path):
    """MUTATION: put back `continue` for 'retry' (no sleep, no count) -> the fake sleep is never called and the
    calls are unbounded; the test's own safety valve (a sleep counter in the API) trips -> red."""
    api = _Api("FAILED")
    sleeps = []
    class _Valve:                                            # the loop under test must end by itself; this only
        calls = 0                                            # stops a regression from hanging the suite

        def is_set(self):
            self.calls += 1
            return self.calls > 60

    result = _requester(api, tmp_path).run(poll_seconds=1.0, sleep=sleeps.append, max_retries=4, stop=_Valve())
    assert result == "gave_up"
    assert api.requests + api.statuses <= 8                    # bounded: a few calls, not a spin
    assert len(sleeps) == 3 and sleeps == sorted(sleeps) and sleeps[-1] > sleeps[0]    # waited, and the waits grew


def test_a_forgotten_request_is_also_bounded(tmp_path):
    api = _Api("OPEN", not_found=True)
    sleeps = []
    # First step requests (OPEN -> 'requested'), later steps find the request gone -> 'retry' each time.
    class _Valve:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 60

    result = _requester(api, tmp_path).run(poll_seconds=1.0, sleep=sleeps.append, max_retries=3, stop=_Valve())
    assert result == "gave_up" and len(sleeps) >= 2


def test_waiting_in_between_does_not_hide_a_loop(tmp_path):
    """Owner answers 'waiting' every other time and 'forgotten' in between: still a loop, still ends. The
    retry count is only ever cleared by a staged copy."""
    api = _Api("FAILED")
    seen = {"n": 0}
    real_status = api.status

    def flip(request_id):
        seen["n"] += 1
        api.status_answer = "OPEN" if seen["n"] % 2 else "FAILED"      # waiting, retry, waiting, retry ...
        return real_status(request_id)

    api.status = flip

    class _Stop:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 60                               # safety valve: the test must not hang

    sleeps = []
    result = _requester(api, tmp_path).run(poll_seconds=1.0, sleep=sleeps.append, max_retries=3, stop=_Stop())
    assert result == "gave_up"
