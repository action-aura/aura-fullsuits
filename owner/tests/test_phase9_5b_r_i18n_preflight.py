"""Phase 9.5B-R Milestone 21 -- i18n preflight checks."""
from __future__ import annotations

from tests.conftest import make_staff


def test_i18n_checks_pass_on_a_healthy_environment(app, seeded, signing_key):
    with app.app_context():
        from app.commercial_ops.preflight import run_preflight

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        names = {c.name: c for c in result.checks}
        assert names["i18n_supported_locales_configured"].status == "OK"
        assert names["i18n_default_locale_valid"].status == "OK"
        assert names["i18n_catalog_compiled_en"].status == "OK"
        assert names["i18n_catalog_compiled_ar"].status == "OK"
        assert names["no_invalid_stored_staff_locale"].status == "OK"


def test_i18n_check_reports_no_personal_data_or_secrets(app, seeded, signing_key):
    staff_id = make_staff(app, "i18npf1@example.com")
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser

        staff = db_session.get(StaffUser, staff_id)
        staff.locale = "ar"
        db_session.commit()

        from app.commercial_ops.preflight import run_preflight

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        details = " ".join(c.detail for c in result.checks)
        assert "i18npf1@example.com" not in details
        assert staff_id.hex not in details.replace("-", "")
