from __future__ import annotations

from datetime import date

from tests.conftest import make_staff


def test_employee_domain_checks_pass_on_a_healthy_seeded_database(app, seeded, signing_key):
    admin_id = make_staff(app, "pf1@example.com", super_admin=True)
    with app.app_context():
        from app.commercial_ops.preflight import run_preflight
        from app.employees.services import activate_employee, create_employee_profile

        profile = create_employee_profile(
            {"staff_user_id": admin_id, "employee_number": "EMP-PF1", "full_name": "Preflight One", "employment_start_date": date(2026, 1, 1)},
            actor_staff_user_id=admin_id,
        )
        activate_employee(profile, actor_staff_user_id=admin_id)

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        names = {c.name: c for c in result.checks}
        assert names["no_duplicate_employee_numbers"].status == "OK"
        assert names["no_orphan_employee_profiles"].status == "OK"
        assert names["at_least_one_usable_super_admin"].status == "OK"
        assert names["presence_thresholds_valid"].status == "OK"


def test_zero_usable_super_admin_fails_preflight(app, seeded, signing_key):
    admin_id = make_staff(app, "pf2@example.com", super_admin=True)
    with app.app_context():
        from app.commercial_ops.preflight import run_preflight
        from app.extensions import db_session
        from app.models.staff import StaffUser

        staff = db_session.get(StaffUser, admin_id)
        staff.is_active = False  # simulate the only Super Admin becoming unusable outside the normal guarded path
        db_session.commit()

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        names = {c.name: c for c in result.checks}
        assert names["at_least_one_usable_super_admin"].status == "FAIL"
        assert result.ok is False
