"""The 7-day trial lock (new customer flow, owner 2026-10-30).

A trial is a signed PILOT subscription. Once its term_end DAY is over (Asia/Amman),
judged by TRUSTED time, the till moves to TRIAL_ENDED: selling stops, the data
(read / reports / backup / export) stays reachable, sync stays on, and the next
check-in that carries a paid lease unlocks it.

These tests are written so each guard has a failing twin; the mutation proofs in
the slice's progress file break the code and show these go red.
"""
import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from flask import Flask, jsonify

from commercial_runtime.licensing_contracts.assertion_verifier import verify_assertion
from commercial_runtime.licensing_contracts.capability_guard import (
    DENIAL_REASON_LICENSE_INACTIVE,
    evaluate_capability,
)
from commercial_runtime.licensing_contracts.checkin_scheduler import LicenseCheckInScheduler  # noqa: F401
from commercial_runtime.licensing_contracts.client import LicensingClientError
from commercial_runtime.licensing_contracts.flask_guard import make_capability_guard
from commercial_runtime.licensing_contracts.policy_evaluator import (
    AssertionEvidence,
    OfflinePolicy,
    evaluate,
    trial_cutoff,
    trial_days_left,
)
from commercial_runtime.licensing_contracts.state_machine import (
    DATA_PRESERVED_FAMILY,
    SELLING_FAMILY,
    LicenseState,
    licence_pauses_sync,
)
from commercial_runtime.licensing_contracts.state_repository import (
    LICENSING_SCHEMA_VERSION,
    LicenseStateRecord,
    LicenseStateRepository,
)
from commercial_runtime.licensing_contracts.status_presenter import present_status
from commercial_runtime.licensing_contracts.trusted_time import TrustedTimeAnchor

# Reuse the scheduler suite's signing helpers + fixtures (real Ed25519 signatures,
# real trust store) instead of re-implementing them: a second copy could drift.
from commercial_runtime.licensing_contracts.tests.test_checkin_scheduler import (  # noqa: F401
    FakeClient,
    INSTALLATION_ID,
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

UTC = timezone.utc
# Jordan is UTC+3 all year (no DST since 2022). 5 Nov 2026 is the LAST day of this trial.
TERM_END = date(2026, 11, 5)
CUTOFF_UTC = datetime(2026, 11, 5, 21, 0, tzinfo=UTC)  # = 00:00 on 6 Nov in Amman

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


def _evidence(status="PILOT", term_end=TERM_END, policy=POLICY, installation_status="ACTIVE",
              license_status="ACTIVE", pilot_status="ACTIVE"):
    base = datetime(2026, 11, 1, tzinfo=UTC)
    return AssertionEvidence(
        not_before=base - timedelta(days=1),
        expires_at=base + timedelta(days=60),
        license_status=license_status,
        installation_status=installation_status,
        subscription_status=status,
        offline_policy=policy,
        term_end=term_end,
        pilot_status=pilot_status,
    )


def _eval(evidence, trusted_at, *, wall=None, elapsed_since_anchor=0.0, last_checkin=None,
          attempt_ok=True, floor=None):
    """Trusted time = trusted_at + elapsed_since_anchor (simulated monotonic time).
    The wall clock defaults to the anchor instant, i.e. NOT rolled back."""
    anchor = TrustedTimeAnchor(
        server_time=trusted_at, monotonic_at_anchor=time.monotonic() - elapsed_since_anchor
    )
    return evaluate(
        evidence=evidence,
        anchor=anchor,
        local_wall_clock_now=wall if wall is not None else trusted_at,
        last_successful_checkin_at=last_checkin if last_checkin is not None else trusted_at,
        last_checkin_attempt_ok=attempt_ok,
        offline_elapsed_floor_seconds=floor,
    )


# ── the rule, on the evaluator ────────────────────────────────────────────

def test_pilot_with_term_end_tomorrow_sells():
    trusted = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
    state = _eval(_evidence(), trusted)
    assert state == LicenseState.ACTIVE_ONLINE
    assert state in SELLING_FAMILY


def test_last_trial_day_2359_amman_still_sells_and_0001_next_day_is_locked():
    just_inside = datetime(2026, 11, 5, 20, 59, tzinfo=UTC)   # 23:59 Amman, 5 Nov
    just_after = datetime(2026, 11, 5, 21, 1, tzinfo=UTC)     # 00:01 Amman, 6 Nov
    assert _eval(_evidence(), just_inside) == LicenseState.ACTIVE_ONLINE
    assert _eval(_evidence(), just_after) == LicenseState.TRIAL_ENDED


def test_boundary_is_amman_midnight_not_utc_midnight():
    # 00:30 in Amman on 6 Nov is still 5 Nov 21:30 UTC. A UTC-day boundary would
    # let the till sell for three more hours; Amman midnight must lock it.
    assert _eval(_evidence(), datetime(2026, 11, 5, 21, 30, tzinfo=UTC)) == LicenseState.TRIAL_ENDED
    # And the other side: 02:30 UTC on the 5th is 05:30 Amman on the 5th -> sells.
    assert _eval(_evidence(), datetime(2026, 11, 5, 2, 30, tzinfo=UTC)) == LicenseState.ACTIVE_ONLINE


def test_trial_cutoff_and_days_left_arithmetic():
    assert trial_cutoff(TERM_END) == CUTOFF_UTC
    # Last day counts as one day left; the instant after it is zero.
    assert trial_days_left(TERM_END, datetime(2026, 11, 5, 8, 0, tzinfo=UTC)) == 1
    assert trial_days_left(TERM_END, datetime(2026, 11, 2, 21, 0, tzinfo=UTC)) == 3
    assert trial_days_left(TERM_END, CUTOFF_UTC) == 0
    assert trial_days_left(TERM_END, CUTOFF_UTC + timedelta(days=3)) == 0


def test_a_seven_day_trial_shows_seven_on_day_zero_not_eight():
    """rc.19, dress rehearsal 2026-10-31: the banner said '8 days left' on day 0 of a 7-day trial.
    VERDICT: the client is right. It counts the term_end day itself (Amman end of day), so the number
    is decided by the end date Owner signs. A 7-day trial created on Amman day D is D..D+6, i.e.
    term_end = D+6 -> 7 on day 0 (at ANY hour of D). The rehearsal's Owner signed D+7 (8 calendar days
    inclusive), and the client correctly said 8. Boundary pinned on both sides so neither half drifts:
    changing the client to say 7 for D+7 would break the last-day check below (term_end day = 1 left)."""
    from datetime import date
    start = date(2026, 10, 31)                     # day 0, Amman
    good_end = start + timedelta(days=6)           # 2026-11-06: what a 7-day trial must carry
    amman_midnight = datetime(2026, 10, 30, 21, 0, tzinfo=UTC)     # 00:00 on D in Amman (UTC+3)
    assert trial_days_left(good_end, amman_midnight + timedelta(seconds=1)) == 7     # first second of day 0
    assert trial_days_left(good_end, amman_midnight + timedelta(hours=23, minutes=59)) == 7   # last minute of day 0
    assert trial_days_left(good_end, amman_midnight + timedelta(days=1, seconds=1)) == 6       # day 1
    assert trial_days_left(good_end, amman_midnight + timedelta(days=6, hours=12)) == 1        # the last day
    assert trial_days_left(good_end, amman_midnight + timedelta(days=7)) == 0                  # over at 00:00 on D+7
    # What the rehearsal's Owner signed (term_end = D+7) is an 8-day trial and is shown as 8: that is the
    # server's end date being a day late, not the client counting wrong.
    assert trial_days_left(start + timedelta(days=7), amman_midnight + timedelta(hours=13)) == 8


def test_cutoff_survives_a_machine_with_no_timezone_database(monkeypatch):
    # Windows without tzdata / Chaquopy: the zone lookup fails; the lock must
    # still land on Amman midnight, never raise on the selling path.
    import sys
    monkeypatch.setitem(sys.modules, "zoneinfo", None)
    assert trial_cutoff(TERM_END) == CUTOFF_UTC


def test_trial_is_judged_by_trusted_time_not_the_wall_clock():
    # Anchor says 20:00Z (inside the trial); 3 hours of monotonic time have
    # passed since, so trusted 'now' is 23:00Z = after the cutoff. The wall clock
    # still reads 20:00Z (not rolled back, so no CLOCK_REVIEW). A wall-clock
    # implementation would say the trial is still running.
    anchor_time = datetime(2026, 11, 5, 20, 0, tzinfo=UTC)
    state = _eval(_evidence(), anchor_time, wall=anchor_time, elapsed_since_anchor=3 * 3600)
    assert state == LicenseState.TRIAL_ENDED


def test_offline_floor_moves_the_trial_clock_forward_after_a_restart():
    # A restart re-pins the anchor to the last check-in (5 Nov 10:00Z); the
    # scheduler hands in a wall-clock FLOOR for the offline stretch (2 days).
    last = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    wall = last + timedelta(days=2)
    without = _eval(_evidence(), last, wall=wall, last_checkin=last, attempt_ok=False)
    with_floor = _eval(_evidence(), last, wall=wall, last_checkin=last, attempt_ok=False,
                       floor=2 * 86400)
    assert without != LicenseState.TRIAL_ENDED, "premise: the bare anchor has seen no time pass"
    assert with_floor == LicenseState.TRIAL_ENDED


def test_a_floor_can_never_extend_a_trial():
    # max(): a small / zero floor leaves the trusted value in charge.
    last = datetime(2026, 11, 5, 21, 30, tzinfo=UTC)  # already past the cutoff
    assert _eval(_evidence(), last, floor=0.0) == LicenseState.TRIAL_ENDED


def test_clock_rolled_back_past_the_end_is_review_required_not_unlocked():
    anchor_time = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)          # Owner-verified, after the end
    rolled_back_wall = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)     # shop owner winds the clock back
    # Premise: with an honest clock the same inputs are TRIAL_ENDED.
    assert _eval(_evidence(), anchor_time) == LicenseState.TRIAL_ENDED
    assert _eval(_evidence(), anchor_time, wall=rolled_back_wall) == LicenseState.CLOCK_REVIEW_REQUIRED


def test_completed_is_locked_whatever_the_dates_say():
    inside = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    for term_end in (TERM_END, date(2027, 1, 1), None):
        assert _eval(_evidence(status="COMPLETED", term_end=term_end), inside) == LicenseState.TRIAL_ENDED


def test_pilot_without_a_term_end_never_locks_on_its_own():
    # Absence of a date must not stop a till (Owner defect != customer's problem).
    assert _eval(_evidence(term_end=None), datetime(2030, 1, 1, tzinfo=UTC)) == LicenseState.ACTIVE_ONLINE


def test_emergency_extension_does_not_unlock_a_trial():
    ended = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
    policy = _policy_with_extension(ended + timedelta(days=3))
    assert _eval(_evidence(policy=policy), ended) == LicenseState.TRIAL_ENDED


def test_extension_still_relieves_a_paid_expired_subscription():
    # The sibling: the extension keeps its existing meaning for PAID shops.
    now = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
    policy = _policy_with_extension(now + timedelta(days=3))
    state = _eval(_evidence(status="EXPIRED", policy=policy), now)
    assert state == LicenseState.ACTIVE_ONLINE


def test_paid_expired_subscription_is_the_unpaid_lock_not_the_trial_lock():
    # CHANGED 2026-10-30 (was: ..._stays_lapsed_and_keeps_selling, SUBSCRIPTION_LAPSED,
    # 2026-10-24 keep-selling rule, now overridden by the owner). A PAID subscription
    # that is EXPIRED locks selling, but as UNPAID_LOCKED -- never TRIAL_ENDED, so the
    # till says "payment overdue" and not "free trial has ended". Can no longer catch:
    # "an expired paid shop keeps selling". Still catches: "expired is ignored" and
    # "expired is reported as an ended trial".
    after = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    state = _eval(_evidence(status="EXPIRED"), after)
    assert state == LicenseState.UNPAID_LOCKED
    assert state != LicenseState.TRIAL_ENDED
    assert state not in SELLING_FAMILY


def test_paid_active_subscription_with_a_past_term_end_is_untouched():
    after = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    assert _eval(_evidence(status="ACTIVE"), after) == LicenseState.ACTIVE_ONLINE


def test_explicit_suspension_wins_over_the_trial_rule():
    ended = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
    assert _eval(_evidence(installation_status="SUSPENDED"), ended) == LicenseState.SUSPENDED
    assert _eval(_evidence(installation_status="REVOKED"), ended) == LicenseState.REVOKED


# ── what TRIAL_ENDED means for the till ───────────────────────────────────

def test_trial_ended_belongs_to_data_preserved_not_selling_and_does_not_pause_sync():
    assert LicenseState.TRIAL_ENDED in DATA_PRESERVED_FAMILY
    assert LicenseState.TRIAL_ENDED not in SELLING_FAMILY
    assert licence_pauses_sync(LicenseState.TRIAL_ENDED) is False
    assert licence_pauses_sync("TRIAL_ENDED") is False
    # The sibling that DOES pause, so the False above is not vacuous.
    assert licence_pauses_sync(LicenseState.SUBSCRIPTION_LAPSED) is True


RETAIL_LIKE_ALLOWLIST = frozenset({
    "retail.records.read", "retail.report.view", "retail.backup.create",
    "retail.backup.restore", "retail.data.export",
})


@pytest.mark.parametrize("capability", sorted(RETAIL_LIKE_ALLOWLIST))
def test_locked_trial_keeps_read_reports_backup_export(capability):
    decision = evaluate_capability(
        capability_code=capability, current_state=LicenseState.TRIAL_ENDED,
        restricted_mode_allowlist=RETAIL_LIKE_ALLOWLIST,
    )
    assert decision.allowed is True


def test_locked_trial_refuses_a_sale():
    decision = evaluate_capability(
        capability_code="retail.sale.create", current_state=LicenseState.TRIAL_ENDED,
        restricted_mode_allowlist=RETAIL_LIKE_ALLOWLIST,
    )
    assert decision.allowed is False
    assert decision.denial_reason == DENIAL_REASON_LICENSE_INACTIVE


# Owner decision 2026-10-30: money OUT (a return/refund) is blocked while the trial is over, money IN
# (a customer paying down debt) is not -- and ONLY for TRIAL_ENDED: a restricted PAID shop keeps its refunds.
RETURN_AND_PAYMENT_ALLOWLIST = RETAIL_LIKE_ALLOWLIST | frozenset({
    "retail.return.create", "retail.customer.payment.record"})


def _decide(capability, state):
    return evaluate_capability(
        capability_code=capability, current_state=state,
        restricted_mode_allowlist=RETURN_AND_PAYMENT_ALLOWLIST)


def test_trial_ended_blocks_a_return_but_allows_a_customer_debt_payment():
    refused = _decide("retail.return.create", LicenseState.TRIAL_ENDED)
    assert refused.allowed is False and refused.denial_reason == DENIAL_REASON_LICENSE_INACTIVE
    assert _decide("retail.customer.payment.record", LicenseState.TRIAL_ENDED).allowed is True


def test_every_other_locked_state_keeps_allowing_a_return():
    # The twin that makes the deny above scoped: it must not leak into any other restricted state.
    for state in (LicenseState.RESTRICTED, LicenseState.EXPIRED, LicenseState.SUSPENDED,
                  LicenseState.REVOKED, LicenseState.CLOCK_REVIEW_REQUIRED, LicenseState.LOCAL_STATE_CORRUPT):
        assert _decide("retail.return.create", state).allowed is True, state
        assert _decide("retail.customer.payment.record", state).allowed is True, state


def test_a_selling_state_still_allows_a_return():
    assert _decide("retail.return.create", LicenseState.ACTIVE_ONLINE).allowed is True
    assert _decide("retail.return.create", LicenseState.SUBSCRIPTION_LAPSED).allowed is True


def test_flask_guard_returns_403_for_a_sale_post_and_200_for_backup_and_reads(tmp_path):
    app_data_dir = str(tmp_path / "appdata")
    guard = make_capability_guard(app_data_dir)
    app = Flask(__name__)

    @app.route("/sales", methods=["POST"])
    @guard("retail.sale.create", restricted_mode_allowlist=RETAIL_LIKE_ALLOWLIST)
    def sale():
        return jsonify({"status": "success"})

    @app.route("/backup", methods=["POST"])
    @guard("retail.backup.create", restricted_mode_allowlist=RETAIL_LIKE_ALLOWLIST)
    def backup():
        return jsonify({"status": "success"})

    @app.route("/export", methods=["GET"])
    @guard("retail.data.export", restricted_mode_allowlist=RETAIL_LIKE_ALLOWLIST)
    def export():
        return jsonify({"status": "success"})

    @app.route("/records", methods=["GET"])
    @guard("retail.records.read", restricted_mode_allowlist=RETAIL_LIKE_ALLOWLIST)
    def records():
        return jsonify({"status": "success"})

    LicenseStateRepository(
        tmp_path / "appdata" / "database" / "subsystems" / "licensing.db"
    ).save(LicenseStateRecord(
        licensing_schema_version=LICENSING_SCHEMA_VERSION, product_code="AURA_RETAIL",
        platform="WINDOWS", current_state="TRIAL_ENDED", subscription_status="PILOT",
    ))
    client = app.test_client()
    refused = client.post("/sales", json={})
    assert refused.status_code == 403
    assert refused.get_json()["reason_code"] == DENIAL_REASON_LICENSE_INACTIVE
    assert client.post("/backup", json={}).status_code == 200
    assert client.get("/export").status_code == 200
    assert client.get("/records").status_code == 200


# ── parsing the signed fields ─────────────────────────────────────────────

def _verify(owner_key, trust_store, **payload_overrides):
    env = _envelope(owner_key, "owner-1", _payload(**payload_overrides))
    return verify_assertion(
        env, trust_store=trust_store, expected_product_code="AURA_RETAIL",
        expected_platform="WINDOWS", expected_installation_id=INSTALLATION_ID,
        expected_device_key_fingerprint="fp-device-1", trusted_now=datetime.now(UTC),
    )


def test_verifier_parses_term_end_and_pilot_status_into_the_evidence(owner_key, trust_store):
    result = _verify(owner_key, trust_store, subscription_status="PILOT",
                     term_end="2026-11-05", pilot_status="ACTIVE")
    assert result.evidence.term_end == date(2026, 11, 5)
    assert result.evidence.pilot_status == "ACTIVE"


def test_verifier_tolerates_a_datetime_term_end_and_never_rejects_over_a_bad_one(owner_key, trust_store):
    assert _verify(owner_key, trust_store, term_end="2026-11-05T00:00:00+00:00").evidence.term_end == TERM_END
    assert _verify(owner_key, trust_store, term_end="garbage").evidence.term_end is None
    assert _verify(owner_key, trust_store, term_end=None).evidence.term_end is None
    assert _verify(owner_key, trust_store).evidence.term_end is None


# ── presenter ─────────────────────────────────────────────────────────────

def _record(state, status, term_end, anchor=None):
    envelope = {"payload": {"term_end": term_end, "pilot_status": "ACTIVE", "plan_code": "RETAIL_TRIAL"}}
    return LicenseStateRecord(
        licensing_schema_version=LICENSING_SCHEMA_VERSION, product_code="AURA_RETAIL",
        platform="WINDOWS", current_state=state, subscription_status=status,
        assertion_envelope_json=json.dumps(envelope), trusted_time_anchor_server_time=anchor,
    )


def test_presenter_reports_days_left_for_a_running_trial():
    end = (datetime.now(UTC) + timedelta(days=3)).astimezone(UTC).date().isoformat()
    trial = present_status(_record("ACTIVE_ONLINE", "PILOT", end))["trial"]
    assert trial["is_trial"] is True and trial["ended"] is False
    assert trial["days_left"] in (3, 4)  # depends on the hour in Amman; never 0, never past 4
    assert trial["term_end"] == end


def test_presenter_marks_an_ended_trial_and_hides_the_day_count():
    trial = present_status(_record("TRIAL_ENDED", "PILOT", "2026-11-05"))["trial"]
    assert trial["is_trial"] is True and trial["ended"] is True and trial["days_left"] is None


def test_presenter_does_not_call_a_paid_subscription_a_trial():
    trial = present_status(_record("ACTIVE_ONLINE", "ACTIVE", "2026-11-05"))["trial"]
    assert trial["is_trial"] is False and trial["days_left"] is None and trial["ended"] is False


def test_presenter_day_count_is_not_generous_when_the_clock_is_rolled_back():
    # Anchor (last Owner-verified time) is already past the end; a wall clock
    # that reads earlier must not resurrect days.
    future_anchor = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    end = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
    trial = present_status(_record("ACTIVE_ONLINE", "PILOT", end, anchor=future_anchor))["trial"]
    assert trial["days_left"] == 0


# ── end to end through the real scheduler (signed lease, trust store, SQLite) ──

NOW = datetime.now(UTC)


def _trial_envelope(owner_key, *, term_end, status="PILOT", days_ago=5, **extra):
    issued = NOW - timedelta(days=days_ago)
    return _envelope(owner_key, "owner-1", _payload(
        issued_at=issued.isoformat(), not_before=(issued - timedelta(minutes=5)).isoformat(),
        expires_at=(issued + timedelta(days=1)).isoformat(),  # the real 24h TTL: long expired
        subscription_status=status, term_end=term_end.isoformat(), pilot_status="ACTIVE", **extra,
    )), issued


def _seed_trial(state_repo, envelope, issued, *, state="ACTIVE_OFFLINE", status="PILOT"):
    return _seed_activated_record(
        state_repo, current_state=state, subscription_status=status,
        assertion_envelope_json=json.dumps(envelope),
        trusted_time_anchor_server_time=issued.isoformat(),
        last_successful_checkin_at=issued.isoformat(),
    )


def test_offline_till_past_the_trial_end_locks_through_the_real_scheduler(owner_key, trust_store, state_repo, events):
    envelope, issued = _trial_envelope(owner_key, term_end=(NOW - timedelta(days=2)).date())
    _seed_trial(state_repo, envelope, issued)
    client = FakeClient(checkin_responses=[LicensingClientError("NETWORK_UNAVAILABLE", "down")])
    result = _scheduler(client, trust_store, state_repo, events).run_once()
    assert client.checkin_calls == 1, "the offline attempt must really have been made"
    assert result == LicenseState.TRIAL_ENDED
    assert state_repo.load().current_state == "TRIAL_ENDED"


def test_offline_till_inside_the_trial_keeps_selling_through_the_real_scheduler(owner_key, trust_store, state_repo, events):
    envelope, issued = _trial_envelope(owner_key, term_end=(NOW + timedelta(days=2)).date())
    _seed_trial(state_repo, envelope, issued)
    client = FakeClient(checkin_responses=[LicensingClientError("NETWORK_UNAVAILABLE", "down")])
    result = _scheduler(client, trust_store, state_repo, events).run_once()
    assert result in SELLING_FAMILY


def test_wall_clock_floor_stops_a_restart_from_resetting_the_trial(owner_key, trust_store, state_repo, events):
    # The stored anchor == issued_at (5 days ago) and the process is fresh, so the
    # bare trusted clock thinks ~0 time has passed since then and the trial (which
    # ended 2 days ago) would look unfinished. Only the offline floor catches it.
    envelope, issued = _trial_envelope(owner_key, term_end=(NOW - timedelta(days=2)).date(), days_ago=5)
    _seed_trial(state_repo, envelope, issued)
    client = FakeClient(checkin_responses=[LicensingClientError("NETWORK_UNAVAILABLE", "down")])
    assert _scheduler(client, trust_store, state_repo, events).run_once() == LicenseState.TRIAL_ENDED


def test_paying_unlocks_on_the_next_checkin_with_no_restart(owner_key, trust_store, state_repo, events):
    envelope, issued = _trial_envelope(owner_key, term_end=(NOW - timedelta(days=2)).date())
    _seed_trial(state_repo, envelope, issued, state="TRIAL_ENDED")
    paid = _envelope(owner_key, "owner-1", _payload(
        assertion_id="a-paid", subscription_status="ACTIVE", term_end=(NOW + timedelta(days=365)).date().isoformat(),
        issued_at=(NOW + timedelta(seconds=5)).isoformat(),
    ))
    client = FakeClient(checkin_responses=[{"result": "SUCCESS", "signed_assertion": paid}])
    result = _scheduler(client, trust_store, state_repo, events).run_once()
    assert client.checkin_calls == 1, "a locked trial must keep calling Owner -- that is how payment is noticed"
    assert result == LicenseState.ACTIVE_ONLINE
    assert state_repo.load().current_state == "ACTIVE_ONLINE"


def test_a_locked_trial_stays_locked_while_owner_still_says_pilot(owner_key, trust_store, state_repo, events):
    envelope, issued = _trial_envelope(owner_key, term_end=(NOW - timedelta(days=2)).date())
    _seed_trial(state_repo, envelope, issued, state="TRIAL_ENDED")
    again = _envelope(owner_key, "owner-1", _payload(
        assertion_id="a-2", subscription_status="PILOT", term_end=(NOW - timedelta(days=2)).date().isoformat(),
        pilot_status="ACTIVE", issued_at=(NOW + timedelta(seconds=5)).isoformat(),
    ))
    client = FakeClient(checkin_responses=[{"result": "SUCCESS", "signed_assertion": again}])
    assert _scheduler(client, trust_store, state_repo, events).run_once() == LicenseState.TRIAL_ENDED


def test_paid_expired_through_the_real_scheduler_is_unpaid_locked_and_does_not_sell(owner_key, trust_store, state_repo, events):
    envelope, issued = _trial_envelope(owner_key, term_end=(NOW - timedelta(days=30)).date(), status="EXPIRED")
    _seed_trial(state_repo, envelope, issued, status="EXPIRED")
    client = FakeClient(checkin_responses=[LicensingClientError("NETWORK_UNAVAILABLE", "down")])
    result = _scheduler(client, trust_store, state_repo, events).run_once()
    # CHANGED 2026-10-30: was SUBSCRIPTION_LAPSED + "in SELLING_FAMILY" (keep-selling, 10-24).
    assert result == LicenseState.UNPAID_LOCKED
    assert result not in SELLING_FAMILY
