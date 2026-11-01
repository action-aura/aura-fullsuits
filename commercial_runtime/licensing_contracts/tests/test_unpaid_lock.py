"""The unpaid lock (owner decision 2026-10-30, overriding the 2026-10-24 keep-selling rule).

A PAID subscription that runs out unpaid gets Owner's 7-day grace (PAST_DUE, signed as
commercial_grace_end). After the grace -- or the moment Owner says EXPIRED -- the till
STOPS SELLING exactly like the ended 7-day trial: data / reports / backup / export and customer
debt payments stay, returns stop, sync and the LAN hub keep running, and the next check-in that
carries a paid lease unlocks it. The state is UNPAID_LOCKED (not TRIAL_ENDED) so the till says
"payment overdue", not "free trial has ended".

Each guard has a failing twin; the mutation proofs are in the slice's progress file.
"""
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from flask import Flask, jsonify

from commercial_runtime.licensing_contracts.capability_guard import (
    DENIAL_REASON_LICENSE_INACTIVE,
    evaluate_capability,
)
from commercial_runtime.licensing_contracts.client import LicensingClientError
from commercial_runtime.licensing_contracts.flask_guard import make_capability_guard
from commercial_runtime.licensing_contracts.policy_evaluator import (
    AssertionEvidence,
    OfflinePolicy,
    evaluate,
)
from commercial_runtime.licensing_contracts.state_guidance import (
    UNPAID_LOCKED_MESSAGE_AR,
    UNPAID_LOCKED_MESSAGE_EN,
)
from commercial_runtime.licensing_contracts.state_machine import (
    DATA_PRESERVED_FAMILY,
    SELLING_FAMILY,
    UNPAID_LOCK_STATES,
    LicenseState,
    licence_pauses_sync,
    transition,
)
from commercial_runtime.licensing_contracts.state_repository import (
    LICENSING_SCHEMA_VERSION,
    LicenseStateRecord,
    LicenseStateRepository,
)
from commercial_runtime.licensing_contracts.trusted_time import TrustedTimeAnchor

# Reuse the scheduler suite's signing helpers (real Ed25519 signatures, real trust store).
from commercial_runtime.licensing_contracts.tests.test_checkin_scheduler import (  # noqa: F401
    FakeClient,
    _clear_anchor_cache,
    _envelope,
    _payload,
    _scheduler,
    _seed_activated_record,
    events,
    owner_key,
    state_repo,
    trust_store,
)

import json

UTC = timezone.utc
# A fixed "now" well inside the assertion's own validity window.
BASE = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
GRACE_END = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)

POLICY = OfflinePolicy(
    check_in_interval_seconds=86400,
    retry_interval_seconds=3600,
    offline_grace_seconds=14 * 86400,
    warning_start_seconds=10 * 86400,
    hard_expiry_behavior="RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
    clock_rollback_tolerance_seconds=300,
    assertion_refresh_threshold_seconds=86400,
)


def _policy_with_extension(until):
    return OfflinePolicy(
        check_in_interval_seconds=86400, retry_interval_seconds=3600,
        offline_grace_seconds=14 * 86400, warning_start_seconds=10 * 86400,
        hard_expiry_behavior="RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA",
        clock_rollback_tolerance_seconds=300, assertion_refresh_threshold_seconds=86400,
        emergency_extension_allowed=True, emergency_extension_until=until,
    )


def _evidence(status="PAST_DUE", grace_end=GRACE_END, policy=POLICY, term_end=None, pilot_status=None):
    return AssertionEvidence(
        not_before=BASE - timedelta(days=30),
        expires_at=BASE + timedelta(days=60),
        license_status="ACTIVE",
        installation_status="ACTIVE",
        subscription_status=status,
        offline_policy=policy,
        commercial_grace_end=grace_end,
        term_end=term_end,
        pilot_status=pilot_status,
    )


def _eval(evidence, trusted_at, *, wall=None, last_checkin=None, attempt_ok=True, floor=None):
    anchor = TrustedTimeAnchor(server_time=trusted_at, monotonic_at_anchor=time.monotonic())
    return evaluate(
        evidence=evidence,
        anchor=anchor,
        local_wall_clock_now=wall if wall is not None else trusted_at,
        last_successful_checkin_at=last_checkin if last_checkin is not None else trusted_at,
        last_checkin_attempt_ok=attempt_ok,
        offline_elapsed_floor_seconds=floor,
    )


# ── the rule, on the evaluator ────────────────────────────────────────────

def test_expired_subscription_locks_at_once():
    assert _eval(_evidence(status="EXPIRED", grace_end=None), BASE) == LicenseState.UNPAID_LOCKED


def test_past_due_past_the_grace_locks():
    assert _eval(_evidence(), GRACE_END + timedelta(seconds=1)) == LicenseState.UNPAID_LOCKED


def test_past_due_inside_the_grace_keeps_selling():
    # The failing twin of the test above: one second earlier the shop is still inside its 7 days.
    state = _eval(_evidence(), GRACE_END - timedelta(seconds=1))
    assert state == LicenseState.ACTIVE_ONLINE
    assert state in SELLING_FAMILY


def test_past_due_with_no_signed_grace_end_never_locks_on_status_alone():
    assert _eval(_evidence(grace_end=None), BASE) == LicenseState.ACTIVE_ONLINE


def test_a_paid_lease_unlocks_it():
    # What a payment looks like on the device: Owner flips the subscription back to ACTIVE.
    assert _eval(_evidence(status="ACTIVE", grace_end=None), BASE + timedelta(days=30)) == LicenseState.ACTIVE_ONLINE


def test_cancelled_and_suspended_stay_restricted_not_unpaid_locked():
    # Only an UNPAID lapse is the new lock; a deliberate end / refund keeps its old state.
    assert _eval(_evidence(status="CANCELLED"), BASE) == LicenseState.RESTRICTED
    assert _eval(_evidence(status="SUSPENDED"), BASE) == LicenseState.RESTRICTED


def test_a_running_trial_is_unaffected():
    term_end = date(2026, 11, 22)
    state = _eval(_evidence(status="PILOT", grace_end=None, term_end=term_end, pilot_status="ACTIVE"), BASE)
    assert state == LicenseState.ACTIVE_ONLINE


def test_an_ended_trial_is_still_the_trial_lock_not_the_unpaid_lock():
    # Two different words on the till: this one must stay "trial ended".
    term_end = date(2026, 11, 10)
    state = _eval(_evidence(status="PILOT", grace_end=None, term_end=term_end, pilot_status="ACTIVE"), BASE)
    assert state == LicenseState.TRIAL_ENDED
    assert state != LicenseState.UNPAID_LOCKED


def test_signed_suspension_still_wins_over_the_unpaid_lock():
    ev = AssertionEvidence(
        not_before=BASE - timedelta(days=30), expires_at=BASE + timedelta(days=60),
        license_status="ACTIVE", installation_status="SUSPENDED", subscription_status="EXPIRED",
        offline_policy=POLICY,
    )
    assert _eval(ev, BASE) == LicenseState.SUSPENDED


def test_an_emergency_extension_still_relieves_the_unpaid_lock():
    # Payment-timing relief keeps its existing meaning for a PAID shop (same as for RESTRICTED before).
    policy = _policy_with_extension(BASE + timedelta(days=3))
    assert _eval(_evidence(status="EXPIRED", grace_end=None, policy=policy), BASE) == LicenseState.ACTIVE_ONLINE


def test_offline_floor_locks_a_till_whose_grace_ran_out_while_it_sat_offline():
    # Restart re-pins the anchor to the last check-in (inside the grace); the scheduler hands in the
    # wall-clock offline stretch as a FLOOR. 2 days later the grace is over and the till must lock.
    last = GRACE_END - timedelta(hours=2)
    wall = last + timedelta(days=2)
    without = _eval(_evidence(), last, wall=wall, last_checkin=last, attempt_ok=False)
    with_floor = _eval(_evidence(), last, wall=wall, last_checkin=last, attempt_ok=False, floor=2 * 86400)
    assert without != LicenseState.UNPAID_LOCKED, "premise: the bare anchor has seen no time pass"
    assert with_floor == LicenseState.UNPAID_LOCKED


def test_a_floor_can_never_shorten_a_grace_that_is_still_running():
    last = GRACE_END - timedelta(days=3)
    assert _eval(_evidence(), last, last_checkin=last, attempt_ok=False, floor=3600.0) != LicenseState.UNPAID_LOCKED


def test_rolling_the_clock_back_does_not_buy_more_grace():
    anchor_time = GRACE_END + timedelta(days=2)             # Owner-verified, after the grace
    rolled_back = GRACE_END - timedelta(days=3)
    assert _eval(_evidence(), anchor_time) == LicenseState.UNPAID_LOCKED       # premise
    assert _eval(_evidence(), anchor_time, wall=rolled_back) == LicenseState.CLOCK_REVIEW_REQUIRED


# ── what UNPAID_LOCKED means for the till: identical to TRIAL_ENDED ──────

def test_unpaid_locked_is_a_data_preserved_non_selling_non_sync_pausing_state():
    assert LicenseState.UNPAID_LOCKED in DATA_PRESERVED_FAMILY
    assert LicenseState.UNPAID_LOCKED not in SELLING_FAMILY
    assert licence_pauses_sync(LicenseState.UNPAID_LOCKED) is False
    assert licence_pauses_sync("UNPAID_LOCKED") is False
    assert UNPAID_LOCK_STATES == {LicenseState.TRIAL_ENDED, LicenseState.UNPAID_LOCKED}
    assert transition(LicenseState.UNPAID_LOCKED, "checkin_succeeded") == LicenseState.ACTIVE_ONLINE


RETAIL_LIKE_ALLOWLIST = frozenset({
    "retail.records.read", "retail.report.view", "retail.backup.create", "retail.backup.restore",
    "retail.data.export", "retail.return.create", "retail.customer.payment.record",
})
EVERY_CAPABILITY = sorted(RETAIL_LIKE_ALLOWLIST | {"retail.sale.create", "retail.product.create"})


def _decide(capability, state):
    return evaluate_capability(
        capability_code=capability, current_state=state, restricted_mode_allowlist=RETAIL_LIKE_ALLOWLIST)


def test_a_sale_is_refused_when_unpaid_locked():
    d = _decide("retail.sale.create", LicenseState.UNPAID_LOCKED)
    assert d.allowed is False and d.denial_reason == DENIAL_REASON_LICENSE_INACTIVE


def test_a_return_is_refused_when_unpaid_locked():
    d = _decide("retail.return.create", LicenseState.UNPAID_LOCKED)
    assert d.allowed is False and d.denial_reason == DENIAL_REASON_LICENSE_INACTIVE


@pytest.mark.parametrize("capability", [
    "retail.customer.payment.record", "retail.records.read", "retail.report.view",
    "retail.backup.create", "retail.backup.restore", "retail.data.export"])
def test_debt_payment_data_reads_backup_and_export_still_work_when_unpaid_locked(capability):
    assert _decide(capability, LicenseState.UNPAID_LOCKED).allowed is True


@pytest.mark.parametrize("capability", EVERY_CAPABILITY)
def test_unpaid_locked_decides_every_capability_exactly_like_the_ended_trial(capability):
    assert _decide(capability, LicenseState.UNPAID_LOCKED) == _decide(capability, LicenseState.TRIAL_ENDED)


@pytest.mark.parametrize("capability", ["retail.sale.create", "retail.return.create", "retail.customer.payment.record"])
def test_a_paid_shop_sells_takes_returns_and_payments(capability):
    assert _decide(capability, LicenseState.ACTIVE_ONLINE).allowed is True


# ── the till's own words ──────────────────────────────────────────────────

def _guard_app(tmp_path):
    app_data_dir = str(tmp_path / "appdata")
    guard = make_capability_guard(app_data_dir)
    app = Flask(__name__)

    @app.route("/sales", methods=["POST"])
    @guard("retail.sale.create", restricted_mode_allowlist=RETAIL_LIKE_ALLOWLIST)
    def sale():
        return jsonify({"status": "success"})

    @app.route("/returns", methods=["POST"])
    @guard("retail.return.create", restricted_mode_allowlist=RETAIL_LIKE_ALLOWLIST)
    def ret():
        return jsonify({"status": "success"})

    @app.route("/payments", methods=["POST"])
    @guard("retail.customer.payment.record", restricted_mode_allowlist=RETAIL_LIKE_ALLOWLIST)
    def pay():
        return jsonify({"status": "success"})

    repo = LicenseStateRepository(tmp_path / "appdata" / "database" / "subsystems" / "licensing.db")

    def set_state(state, status="EXPIRED"):
        repo.save(LicenseStateRecord(
            licensing_schema_version=LICENSING_SCHEMA_VERSION, product_code="AURA_RETAIL",
            platform="WINDOWS", current_state=state, subscription_status=status))
    return app.test_client(), set_state


def test_the_403_on_a_sale_says_the_payment_is_overdue_in_both_languages(tmp_path):
    client, set_state = _guard_app(tmp_path)
    set_state("UNPAID_LOCKED")
    r = client.post("/sales", json={})
    body = r.get_json()
    assert r.status_code == 403
    assert body["reason_code"] == DENIAL_REASON_LICENSE_INACTIVE
    assert body["message"] == UNPAID_LOCKED_MESSAGE_EN == (
        "Your subscription payment is overdue. Selling is paused until the payment is confirmed. "
        "Your data is safe.")
    assert body["message_ar"] == UNPAID_LOCKED_MESSAGE_AR == (
        "دفعة اشتراكك متأخرة. البيع متوقف حتى تأكيد الدفعة. بياناتك محفوظة.")
    assert "trial" not in body["message"].lower()


def test_the_ended_trial_403_does_not_borrow_the_payment_overdue_words(tmp_path):
    client, set_state = _guard_app(tmp_path)
    set_state("TRIAL_ENDED", status="PILOT")
    body = client.post("/sales", json={}).get_json()
    assert body["message"] != UNPAID_LOCKED_MESSAGE_EN
    assert "message_ar" not in body


def test_over_http_sale_and_return_403_but_debt_payment_200_when_unpaid_locked(tmp_path):
    client, set_state = _guard_app(tmp_path)
    set_state("UNPAID_LOCKED")
    assert client.post("/sales", json={}).status_code == 403
    assert client.post("/returns", json={}).status_code == 403
    assert client.post("/payments", json={}).status_code == 200


def test_over_http_a_paid_lease_sells_again(tmp_path):
    client, set_state = _guard_app(tmp_path)
    set_state("UNPAID_LOCKED")
    assert client.post("/sales", json={}).status_code == 403
    set_state("ACTIVE_ONLINE", status="ACTIVE")
    assert client.post("/sales", json={}).status_code == 200


# ── end to end through the real scheduler (signed lease, trust store, SQLite) ──

NOW = datetime.now(UTC)


def _lease(owner_key, *, status, grace_end=None, days_ago=1, **extra):
    issued = NOW - timedelta(days=days_ago)
    payload = _payload(
        issued_at=issued.isoformat(), not_before=(issued - timedelta(minutes=5)).isoformat(),
        expires_at=(issued + timedelta(days=1)).isoformat(), subscription_status=status, **extra)
    if grace_end is not None:
        payload["commercial_grace_end"] = grace_end.isoformat()
    return _envelope(owner_key, "owner-1", payload), issued


def _seed(state_repo, envelope, issued, *, state="ACTIVE_OFFLINE", status="PAST_DUE"):
    return _seed_activated_record(
        state_repo, current_state=state, subscription_status=status,
        assertion_envelope_json=json.dumps(envelope),
        trusted_time_anchor_server_time=issued.isoformat(),
        last_successful_checkin_at=issued.isoformat())


def _offline():
    return FakeClient(checkin_responses=[LicensingClientError("NETWORK_UNAVAILABLE", "down")])


def test_scheduler_offline_past_due_past_grace_locks_and_inside_grace_sells(owner_key, trust_store, state_repo, events):
    env, issued = _lease(owner_key, status="PAST_DUE", grace_end=NOW - timedelta(hours=1), days_ago=2)
    _seed(state_repo, env, issued)
    client = _offline()
    assert _scheduler(client, trust_store, state_repo, events).run_once() == LicenseState.UNPAID_LOCKED
    assert client.checkin_calls == 1
    assert state_repo.load().current_state == "UNPAID_LOCKED"


def test_scheduler_offline_past_due_inside_grace_keeps_selling(owner_key, trust_store, state_repo, events):
    env, issued = _lease(owner_key, status="PAST_DUE", grace_end=NOW + timedelta(days=5), days_ago=2)
    _seed(state_repo, env, issued)
    result = _scheduler(_offline(), trust_store, state_repo, events).run_once()
    assert result in SELLING_FAMILY


def test_scheduler_locked_till_unlocks_on_the_next_checkin_that_carries_a_paid_lease(owner_key, trust_store, state_repo, events):
    env, issued = _lease(owner_key, status="EXPIRED", days_ago=3)
    _seed(state_repo, env, issued, state="UNPAID_LOCKED", status="EXPIRED")
    paid = _envelope(owner_key, "owner-1", _payload(
        assertion_id="a-paid", subscription_status="ACTIVE", issued_at=(NOW + timedelta(seconds=5)).isoformat()))
    client = FakeClient(checkin_responses=[{"result": "SUCCESS", "signed_assertion": paid}])
    result = _scheduler(client, trust_store, state_repo, events).run_once()
    assert client.checkin_calls == 1, "a locked till must keep calling Owner -- that is how payment is noticed"
    assert result == LicenseState.ACTIVE_ONLINE
    assert state_repo.load().current_state == "ACTIVE_ONLINE"


def test_scheduler_locked_till_stays_locked_while_owner_still_says_expired(owner_key, trust_store, state_repo, events):
    env, issued = _lease(owner_key, status="EXPIRED", days_ago=3)
    _seed(state_repo, env, issued, state="UNPAID_LOCKED", status="EXPIRED")
    again = _envelope(owner_key, "owner-1", _payload(
        assertion_id="a-2", subscription_status="EXPIRED", issued_at=(NOW + timedelta(seconds=5)).isoformat()))
    client = FakeClient(checkin_responses=[{"result": "SUCCESS", "signed_assertion": again}])
    assert _scheduler(client, trust_store, state_repo, events).run_once() == LicenseState.UNPAID_LOCKED
