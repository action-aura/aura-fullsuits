"""Phase 9.5B-R Milestone 13/16 -- locale-aware date/number formatting,
Arabic-digit policy verification."""
from __future__ import annotations

from datetime import date, datetime, timezone


def test_format_owner_date_none_is_safe(app):
    with app.test_request_context():
        from app.i18n_format import format_owner_date

        assert format_owner_date(None) == "-"


def test_format_owner_date_uses_western_digits_in_arabic(app):
    with app.test_request_context():
        from flask import session

        session["locale"] = "ar"
        from app.i18n_format import format_owner_date

        rendered = format_owner_date(date(2026, 8, 1))
        digits = {ch for ch in rendered if ch.isdigit() or "٠" <= ch <= "٩"}
        eastern_arabic_digits = {ch for ch in digits if "٠" <= ch <= "٩"}
        assert eastern_arabic_digits == set(), f"found Eastern Arabic-Indic digits in {rendered!r}"
        assert any(ch in "0123456789" for ch in rendered)


def test_format_owner_datetime_none_is_safe(app):
    with app.test_request_context():
        from app.i18n_format import format_owner_datetime

        assert format_owner_datetime(None) == "-"


def test_format_owner_number_none_is_safe(app):
    with app.test_request_context():
        from app.i18n_format import format_owner_number

        assert format_owner_number(None) == "-"


def test_format_owner_number_uses_western_digits_in_arabic(app):
    with app.test_request_context():
        from flask import session

        session["locale"] = "ar"
        from app.i18n_format import format_owner_number

        rendered = format_owner_number(12345)
        eastern_arabic_digits = {ch for ch in rendered if "٠" <= ch <= "٩"}
        assert eastern_arabic_digits == set()


def test_format_owner_datetime_does_not_mutate_input(app):
    with app.test_request_context():
        from app.i18n_format import format_owner_datetime

        value = datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc)
        format_owner_datetime(value)
        assert value == datetime(2026, 8, 1, 12, 30, tzinfo=timezone.utc)  # unchanged, presentation-only
