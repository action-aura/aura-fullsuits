from datetime import date

from app.commercial_ops.state_resolution import CommercialState, resolve_commercial_state

TODAY = date(2026, 7, 26)


def _decide(**overrides):
    fields = dict(
        subscription_status="ACTIVE",
        license_status="ACTIVE",
        subscription_end_date=date(2026, 12, 1),
        license_valid_until=date(2026, 12, 1),
        as_of=TODAY,
    )
    fields.update(overrides)
    return resolve_commercial_state(**fields)


# -- REVOKED always wins -----------------------------------------------------

def test_revoked_license_wins_over_active_subscription():
    decision = _decide(subscription_status="ACTIVE", license_status="REVOKED")
    assert decision.state == CommercialState.REVOKED
    assert decision.may_issue_assertion is False
    assert decision.may_activate_new_installation is False
    assert decision.may_check_in_existing_installation is False
    assert decision.governing_record == "license"


def test_revoked_license_wins_over_past_due_subscription():
    decision = _decide(subscription_status="PAST_DUE", license_status="REVOKED")
    assert decision.state == CommercialState.REVOKED


def test_revoked_license_wins_over_suspended_subscription():
    decision = _decide(subscription_status="SUSPENDED", license_status="REVOKED")
    assert decision.state == CommercialState.REVOKED


# -- ACTIVE / PILOT_ACTIVE ----------------------------------------------------

def test_active_subscription_active_license_is_commercially_active():
    decision = _decide(subscription_status="ACTIVE", license_status="ACTIVE")
    assert decision.state == CommercialState.ACTIVE
    assert decision.may_issue_assertion is True
    assert decision.may_activate_new_installation is True
    assert decision.may_check_in_existing_installation is True
    assert decision.required_action is None


def test_active_subscription_issued_license_is_commercially_active():
    decision = _decide(subscription_status="ACTIVE", license_status="ISSUED")
    assert decision.state == CommercialState.ACTIVE
    assert decision.may_issue_assertion is True


def test_pilot_subscription_active_license_is_pilot_active():
    decision = _decide(subscription_status="PILOT", license_status="ACTIVE")
    assert decision.state == CommercialState.PILOT_ACTIVE
    assert decision.may_issue_assertion is True
    assert decision.may_activate_new_installation is True


def test_active_subscription_no_license_yet():
    decision = _decide(subscription_status="ACTIVE", license_status=None)
    assert decision.state == CommercialState.NO_LICENSE
    assert decision.may_activate_new_installation is False
    assert decision.reason_code == "NO_LICENSE_ISSUED"


def test_active_subscription_with_inconsistent_license_state_is_invalid_not_active():
    # An ACTIVE subscription whose license is still DRAFT should never be
    # silently treated as commercially active -- deny-by-default backstop.
    decision = _decide(subscription_status="ACTIVE", license_status="DRAFT")
    assert decision.state == CommercialState.INVALID
    assert decision.may_issue_assertion is False
    assert decision.may_activate_new_installation is False


# -- PAST_DUE ------------------------------------------------------------------

def test_past_due_is_not_automatically_blocked():
    decision = _decide(subscription_status="PAST_DUE", license_status="ACTIVE")
    assert decision.state == CommercialState.PAST_DUE
    # Part I: past-due must not automatically mean revoked -- assertions
    # still issue while the license itself remains active.
    assert decision.may_issue_assertion is True
    assert decision.may_check_in_existing_installation is True
    # New activation is still not allowed on a past-due subscription.
    assert decision.may_activate_new_installation is False


def test_past_due_with_non_active_license_does_not_issue_assertion():
    decision = _decide(subscription_status="PAST_DUE", license_status="SUSPENDED")
    # SUSPENDED license wins via the earlier suspended-branch check, not
    # falling through to PAST_DUE's own branch.
    assert decision.state == CommercialState.SUSPENDED


# -- SUSPENDED -------------------------------------------------------------

def test_suspended_subscription():
    decision = _decide(subscription_status="SUSPENDED", license_status="ACTIVE")
    assert decision.state == CommercialState.SUSPENDED
    assert decision.governing_record == "subscription"
    assert decision.may_issue_assertion is False
    assert decision.may_check_in_existing_installation is True


def test_suspended_license_with_active_subscription():
    decision = _decide(subscription_status="ACTIVE", license_status="SUSPENDED")
    assert decision.state == CommercialState.SUSPENDED
    assert decision.governing_record == "license"


# -- EXPIRED -----------------------------------------------------------------

def test_expired_subscription():
    decision = _decide(subscription_status="EXPIRED", license_status="ACTIVE")
    assert decision.state == CommercialState.EXPIRED
    assert decision.may_issue_assertion is False
    assert decision.may_check_in_existing_installation is True


def test_expired_license_with_active_subscription():
    decision = _decide(subscription_status="ACTIVE", license_status="EXPIRED")
    assert decision.state == CommercialState.EXPIRED
    assert decision.governing_record == "license"


# -- CANCELLED, immediate vs end-of-term -------------------------------------

def test_cancelled_within_effective_period_still_usable():
    decision = _decide(
        subscription_status="CANCELLED",
        cancellation_effective_date=date(2026, 8, 1),  # still in the future relative to TODAY
    )
    assert decision.state == CommercialState.CANCELLED
    assert decision.may_issue_assertion is True
    assert decision.may_check_in_existing_installation is True
    assert decision.required_action is None


def test_cancelled_past_effective_period_is_no_longer_usable():
    decision = _decide(
        subscription_status="CANCELLED",
        cancellation_effective_date=date(2026, 7, 1),  # already passed relative to TODAY
    )
    assert decision.state == CommercialState.CANCELLED
    assert decision.may_issue_assertion is False
    assert decision.may_check_in_existing_installation is False
    assert decision.required_action is not None


def test_cancelled_with_no_effective_date_defaults_to_not_usable():
    # No effective date recorded -- must not default to "still usable"
    # (deny-by-default).
    decision = _decide(subscription_status="CANCELLED", cancellation_effective_date=None)
    assert decision.may_issue_assertion is False


def test_immediate_vs_end_of_term_cancellation_are_distinct():
    immediate = _decide(subscription_status="CANCELLED", cancellation_effective_date=TODAY)
    end_of_term = _decide(subscription_status="CANCELLED", cancellation_effective_date=date(2026, 12, 1))
    assert immediate.may_issue_assertion is True  # same-day cancellation still counts as within period
    assert end_of_term.may_issue_assertion is True
    assert immediate.effective_date != end_of_term.effective_date


# -- COMPLETED (pilot) ---------------------------------------------------------

def test_completed_pilot_requires_conversion_or_extension():
    decision = _decide(subscription_status="COMPLETED", license_status="ACTIVE")
    assert decision.state == CommercialState.PILOT_COMPLETED
    assert decision.may_issue_assertion is False
    assert decision.may_activate_new_installation is False
    assert "convert" in decision.required_action.lower() or "extension" in decision.required_action.lower()


# -- DRAFT / unrecognized deny-by-default -------------------------------------

def test_draft_subscription_denies_everything():
    decision = _decide(subscription_status="DRAFT", license_status=None)
    assert decision.state == CommercialState.INVALID
    assert decision.may_issue_assertion is False
    assert decision.may_activate_new_installation is False
    assert decision.may_check_in_existing_installation is False


def test_unrecognized_subscription_status_denies_everything():
    decision = _decide(subscription_status="SOME_FUTURE_STATUS", license_status="ACTIVE")
    assert decision.state == CommercialState.INVALID
    assert decision.reason_code == "UNRECOGNIZED_SUBSCRIPTION_STATE"
    assert decision.may_issue_assertion is False


def test_unrecognized_license_status_with_active_subscription_denies():
    decision = _decide(subscription_status="ACTIVE", license_status="SOME_FUTURE_LICENSE_STATUS")
    assert decision.state == CommercialState.INVALID


# -- audit context always present, no drift ----------------------------------

def test_every_decision_includes_audit_context():
    for sub_status in ("DRAFT", "PILOT", "ACTIVE", "PAST_DUE", "SUSPENDED", "EXPIRED", "CANCELLED", "COMPLETED"):
        decision = resolve_commercial_state(
            subscription_status=sub_status,
            license_status="ACTIVE",
            subscription_end_date=date(2026, 12, 1),
            license_valid_until=date(2026, 12, 1),
            as_of=TODAY,
            cancellation_effective_date=date(2026, 12, 1),
        )
        assert decision.audit_context
        assert "subscription_status" in decision.audit_context


def test_applicable_policy_code_defaults_to_none_until_milestone_3():
    decision = _decide()
    assert decision.applicable_policy_code is None
