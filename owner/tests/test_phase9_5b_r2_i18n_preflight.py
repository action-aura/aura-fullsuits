"""Phase 9.5B-R2 Milestone 13 -- i18n preflight extended to catch empty/fuzzy
catalog entries (the real bug class this wave found, see
rtl-defect-and-fix-log.md item 5), not just missing/empty .mo files."""
from __future__ import annotations

from app.commercial_ops.preflight import _check_i18n_configuration


def test_i18n_preflight_passes_with_the_real_current_catalog(app):
    with app.app_context():
        checks = []
        ok = _check_i18n_configuration(checks)
        assert ok is True
        codes = {c.name: c.status for c in checks}
        assert codes.get("i18n_catalog_complete_en") == "OK"
        assert codes.get("i18n_catalog_complete_ar") == "OK"


def test_i18n_preflight_check_codes_are_present_for_both_locales(app):
    with app.app_context():
        checks = []
        _check_i18n_configuration(checks)
        names = [c.name for c in checks]
        assert "i18n_catalog_complete_en" in names
        assert "i18n_catalog_complete_ar" in names
