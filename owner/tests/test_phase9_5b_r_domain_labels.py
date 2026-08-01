"""Phase 9.5B-R Milestone 11/16 -- domain label mapping correctness and
safe fallback for unknown/future codes."""
from __future__ import annotations


def test_employment_status_labels_real_in_both_locales(app):
    with app.test_request_context():
        from app.i18n_labels import employment_status_label

        for code in ("PENDING", "ACTIVE", "SUSPENDED", "TERMINATED", "ARCHIVED"):
            en = employment_status_label(code)
            assert en and en != code  # every real code maps to an actual label, not itself


def test_unknown_code_falls_back_safely(app):
    with app.test_request_context():
        from app.i18n_labels import audit_action_label, employment_status_label, presence_label, role_label

        assert employment_status_label("SOME_FUTURE_STATUS") == "SOME_FUTURE_STATUS"
        assert presence_label("SOME_FUTURE_PRESENCE") == "SOME_FUTURE_PRESENCE"
        assert role_label("SOME_FUTURE_ROLE") == "SOME_FUTURE_ROLE"
        assert audit_action_label("SOME_FUTURE_ACTION_CODE") == "SOME_FUTURE_ACTION_CODE"


def test_labels_actually_change_in_arabic(app, client, seeded):
    client.get("/locale/ar?next=/")
    with app.test_request_context():
        from flask import session

        session["locale"] = "ar"
        from app.i18n_labels import employment_status_label, presence_label, role_label

        assert employment_status_label("ACTIVE") == "نشط"
        assert presence_label("RECENTLY_ACTIVE") == "نشط مؤخرًا"
        assert role_label("SUPER_ADMIN") == "مدير النظام الأعلى"


def test_stored_enum_values_never_mutated_by_label_lookup(app):
    """Calling a label function must never write anything -- pure functions,
    no DB access at all."""
    with app.app_context():
        from app import i18n_labels

        assert "db_session" not in dir(i18n_labels)
        import inspect

        source = inspect.getsource(i18n_labels)
        assert "db_session" not in source
        assert ".commit()" not in source
