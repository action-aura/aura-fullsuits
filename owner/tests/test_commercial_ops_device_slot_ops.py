from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.conftest import make_license, make_staff


# -- release / replace ---------------------------------------------------

def test_release_device_slot_requires_reason_and_frees_slot(app, seeded):
    staff_id = make_staff(app, "dso1@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import DeviceSlotError, release_device_slot
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.licensing.services import transition_license
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)
        transition_license(lic, "ACTIVE", staff_id)
        installation = register_installation(
            {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": lic.product_id,
             "platform_id": _windows_platform_id(app), "installation_label": "dev-1"},
            staff_id,
        )
        installation.status = "ACTIVE"
        db_session.commit()

        with pytest.raises(DeviceSlotError):
            release_device_slot(installation, reason="", actor_staff_user_id=staff_id)
        release_device_slot(installation, reason="Customer decommissioned this laptop", actor_staff_user_id=staff_id)
        assert installation.status == "DEACTIVATED"


def test_replace_device_slot_requires_reason_and_marks_replaced(app, seeded):
    staff_id = make_staff(app, "dso2@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import DeviceSlotError, replace_device_slot
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.licensing.services import transition_license
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)
        transition_license(lic, "ACTIVE", staff_id)
        installation = register_installation(
            {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": lic.product_id,
             "platform_id": _windows_platform_id(app), "installation_label": "dev-2"},
            staff_id,
        )
        installation.status = "ACTIVE"
        db_session.commit()

        with pytest.raises(DeviceSlotError):
            replace_device_slot(installation, reason=" ", actor_staff_user_id=staff_id)
        replace_device_slot(installation, reason="Hardware swap, new laptop issued", actor_staff_user_id=staff_id)
        assert installation.status == "REPLACED"


def _windows_platform_id(app):
    from app.extensions import db_session
    from app.models.catalog import Platform

    return db_session.query(Platform).filter_by(platform_code="WINDOWS").first().id


# -- device slot exceptions ------------------------------------------------

def test_create_device_slot_exception_validates_inputs(app, seeded):
    staff_id = make_staff(app, "dso3@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import MAX_DEVICE_SLOT_EXCEPTION_DAYS, DeviceSlotError, create_device_slot_exception
        from app.extensions import db_session
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)
        now = datetime(2026, 7, 1, 12, 0, 0)

        with pytest.raises(DeviceSlotError):
            create_device_slot_exception(
                license_row=lic, extra_slots=0, reason="x", starts_at=now,
                expires_at=now + timedelta(days=1), actor_staff_user_id=staff_id,
            )
        with pytest.raises(DeviceSlotError):
            create_device_slot_exception(
                license_row=lic, extra_slots=1, reason="", starts_at=now,
                expires_at=now + timedelta(days=1), actor_staff_user_id=staff_id,
            )
        with pytest.raises(DeviceSlotError):
            create_device_slot_exception(
                license_row=lic, extra_slots=1, reason="x", starts_at=now,
                expires_at=now, actor_staff_user_id=staff_id,
            )
        with pytest.raises(DeviceSlotError):
            create_device_slot_exception(
                license_row=lic, extra_slots=1, reason="too long", starts_at=now,
                expires_at=now + timedelta(days=MAX_DEVICE_SLOT_EXCEPTION_DAYS + 1), actor_staff_user_id=staff_id,
            )


def test_resolve_effective_device_limit_sums_active_in_window_exceptions(app, seeded):
    staff_id = make_staff(app, "dso4@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import create_device_slot_exception, resolve_effective_device_limit
        from app.extensions import db_session
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=2)
        lic = db_session.get(License, lic_id)
        now = datetime(2026, 7, 1, 12, 0, 0)

        assert resolve_effective_device_limit(lic, as_of=now) == 2

        create_device_slot_exception(
            license_row=lic, extra_slots=3, reason="fleet swap", starts_at=now,
            expires_at=now + timedelta(days=14), actor_staff_user_id=staff_id,
        )
        assert resolve_effective_device_limit(lic, as_of=now + timedelta(days=1)) == 5
        # Outside the window -- not counted.
        assert resolve_effective_device_limit(lic, as_of=now + timedelta(days=20)) == 2


def test_revoked_exception_no_longer_counts(app, seeded):
    staff_id = make_staff(app, "dso5@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import (
            DeviceSlotError,
            create_device_slot_exception,
            resolve_effective_device_limit,
            revoke_device_slot_exception,
        )
        from app.extensions import db_session
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)
        now = datetime(2026, 7, 1, 12, 0, 0)
        exception = create_device_slot_exception(
            license_row=lic, extra_slots=2, reason="temp", starts_at=now,
            expires_at=now + timedelta(days=5), actor_staff_user_id=staff_id,
        )
        assert resolve_effective_device_limit(lic, as_of=now) == 3

        with pytest.raises(DeviceSlotError):
            revoke_device_slot_exception(exception, reason="", actor_staff_user_id=staff_id)
        revoke_device_slot_exception(exception, reason="No longer needed", actor_staff_user_id=staff_id)
        assert resolve_effective_device_limit(lic, as_of=now) == 1
        with pytest.raises(DeviceSlotError):
            revoke_device_slot_exception(exception, reason="again", actor_staff_user_id=staff_id)


# -- over-limit scan ---------------------------------------------------------

def test_scan_over_limit_licenses_dry_run_does_not_write(app, seeded):
    staff_id = make_staff(app, "dso6@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import scan_over_limit_licenses
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.licensing.services import transition_license
        from app.models.commercial_ops import InternalNotification
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)
        transition_license(lic, "ACTIVE", staff_id)
        platform_id = _windows_platform_id(app)
        for label in ("over-1", "over-2"):
            inst = register_installation(
                {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": lic.product_id,
                 "platform_id": platform_id, "installation_label": label},
                staff_id,
            )
            inst.status = "ACTIVE"
        db_session.commit()

        result = scan_over_limit_licenses(as_of=None, dry_run=True)
        assert any(f.license_id == str(lic.id) for f in result.findings)
        assert db_session.query(InternalNotification).count() == 0


def test_scan_over_limit_licenses_apply_creates_deduped_notification(app, seeded):
    staff_id = make_staff(app, "dso7@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import scan_over_limit_licenses
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.licensing.services import transition_license
        from app.models.commercial_ops import InternalNotification
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)
        transition_license(lic, "ACTIVE", staff_id)
        platform_id = _windows_platform_id(app)
        for label in ("over-a", "over-b"):
            inst = register_installation(
                {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": lic.product_id,
                 "platform_id": platform_id, "installation_label": label},
                staff_id,
            )
            inst.status = "ACTIVE"
        db_session.commit()

        first = scan_over_limit_licenses(as_of=None, dry_run=False)
        assert first.notifications_created == 1
        assert db_session.query(InternalNotification).count() == 1

        second = scan_over_limit_licenses(as_of=None, dry_run=False)
        assert second.notifications_deduped == 1
        assert db_session.query(InternalNotification).count() == 1


def test_scan_over_limit_licenses_never_touches_installations(app, seeded):
    staff_id = make_staff(app, "dso8@example.com")
    with app.app_context():
        from app.commercial_ops.device_slot_ops import scan_over_limit_licenses
        from app.extensions import db_session
        from app.installations.services import register_installation
        from app.licensing.services import transition_license
        from app.models.installations import Installation
        from app.models.licensing import License

        lic_id, _ = make_license(app, staff_id, device_limit=1)
        lic = db_session.get(License, lic_id)
        transition_license(lic, "ACTIVE", staff_id)
        platform_id = _windows_platform_id(app)
        installations = []
        for label in ("keep-a", "keep-b"):
            inst = register_installation(
                {"customer_id": lic.customer_id, "license_id": lic.id, "product_id": lic.product_id,
                 "platform_id": platform_id, "installation_label": label},
                staff_id,
            )
            inst.status = "ACTIVE"
            installations.append(inst)
        db_session.commit()

        scan_over_limit_licenses(as_of=None, dry_run=False)
        for inst in installations:
            refreshed = db_session.get(Installation, inst.id)
            assert refreshed.status == "ACTIVE"
