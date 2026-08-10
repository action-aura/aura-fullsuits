from __future__ import annotations

from tests.conftest import make_staff


def test_phone_duplicate_detection_matches_normalized_variants(app, seeded):
    from app.customers.services import add_contact, create_customer, find_duplicate_candidates

    staff_id = make_staff(app, "dup1@example.com", role_codes=["SALES"])
    with app.app_context():
        customer = create_customer({"legal_name": "Phone Match Co"}, staff_id)
        # Same digits, different formatting only -- normalize_phone() is a
        # bounded digits-only comparator, not a real E.164 parser (it does
        # not know "+962 79..." and "079..." are the same real number).
        add_contact(customer, {"name": "Jane", "business_phone": "+962-79-123-4567"}, staff_id)

        candidates = find_duplicate_candidates("Different Name Inc", None, None, "962 79 123 4567")
        assert customer in candidates


def test_duplicate_candidate_invisible_to_unauthorized_actor_is_masked(app, seeded):
    """Milestone 5 privacy requirement: an employee who cannot otherwise see
    a matching Customer must never receive its name/UUID/contact info
    through the duplicate-detection response -- only a bounded marker."""
    from app.customers.services import (
        DUPLICATE_REVIEW_MARKER,
        create_customer,
        describe_duplicate_candidates_for_actor,
    )

    owner_staff_id = make_staff(app, "dup2owner@example.com", role_codes=["SALES"])
    other_staff_id = make_staff(app, "dup2other@example.com", role_codes=["SALES"])
    with app.app_context():
        customer = create_customer({"legal_name": "Secret Corp"}, owner_staff_id)

        described = describe_duplicate_candidates_for_actor([customer], other_staff_id, {"customers.view_own"})
        assert described == [{"visible": False, "marker": DUPLICATE_REVIEW_MARKER}]
        assert "Secret Corp" not in str(described)
        assert str(customer.id) not in str(described)


def test_duplicate_candidate_visible_to_owner_and_to_view_all_holder(app, seeded):
    from app.customers.services import create_customer, describe_duplicate_candidates_for_actor

    owner_staff_id = make_staff(app, "dup3owner@example.com", role_codes=["SALES"])
    mgmt_staff_id = make_staff(app, "dup3mgmt@example.com", role_codes=["VIEWER"])
    with app.app_context():
        customer = create_customer({"legal_name": "Visible Corp"}, owner_staff_id)

        described_owner = describe_duplicate_candidates_for_actor([customer], owner_staff_id, {"customers.view_own"})
        assert described_owner[0]["visible"] is True
        assert described_owner[0]["legal_name"] == "Visible Corp"

        described_mgmt = describe_duplicate_candidates_for_actor([customer], mgmt_staff_id, {"customers.view_all"})
        assert described_mgmt[0]["visible"] is True
