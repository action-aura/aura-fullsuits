from __future__ import annotations


def test_preflight_fails_with_no_active_signing_key(app, seeded):
    with app.app_context():
        from app.commercial_ops.preflight import run_preflight

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        assert result.ok is False
        names = {c.name: c.status for c in result.checks}
        assert names["active_signing_key_exists"] == "FAIL"


def test_preflight_permission_seed_ok_by_default(app, seeded, signing_key, tmp_path):
    # `seeded` runs seed-rbac equivalent setup, so the freshly-migrated test
    # database should already be in sync with app/staff/seed_data.py --
    # this is the regression guard against the exact class of drift the
    # Phase 8V-P session found by hand (see environment-readiness-report.md).
    # Trust anchor pointed at an isolated empty path (this test's freshly
    # generated key was never written to the real, developer-machine
    # trust_anchor.json, so checking against it here would be a false
    # negative unrelated to what this test verifies).
    with app.app_context():
        import os

        from app.commercial_ops.preflight import run_preflight

        os.environ["COMMERCIAL_TRUST_ANCHOR_PATH"] = str(tmp_path / "does-not-exist.json")
        try:
            result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        finally:
            del os.environ["COMMERCIAL_TRUST_ANCHOR_PATH"]
        names = {c.name: c.status for c in result.checks}
        assert names["all_permissions_seeded"] == "OK"
        assert names["role_permissions_synced"] == "OK"
        assert result.ok is True


def test_preflight_detects_missing_permission(app, seeded, signing_key):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.staff import Permission, RolePermission
        from app.commercial_ops.preflight import run_preflight

        row = db_session.query(Permission).filter_by(code="pilots.manage").first()
        assert row is not None
        for rp in db_session.execute(select(RolePermission).where(RolePermission.permission_id == row.id)).scalars().all():
            db_session.delete(rp)
        db_session.delete(row)
        db_session.commit()

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        assert result.ok is False
        names = {c.name: c.detail for c in result.checks}
        assert "pilots.manage" in names["all_permissions_seeded"]


def test_preflight_detects_role_missing_permission_assignment(app, seeded, signing_key):
    with app.app_context():
        from sqlalchemy import select

        from app.extensions import db_session
        from app.models.staff import Permission, Role, RolePermission
        from app.commercial_ops.preflight import run_preflight

        role = db_session.execute(select(Role).where(Role.code == "SALES")).scalars().first()
        perm = db_session.execute(select(Permission).where(Permission.code == "pilots.manage")).scalars().first()
        assert role is not None and perm is not None
        rp = db_session.execute(
            select(RolePermission).where(RolePermission.role_id == role.id, RolePermission.permission_id == perm.id)
        ).scalars().first()
        assert rp is not None
        db_session.delete(rp)
        db_session.commit()

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        assert result.ok is False
        fail_names = [c.name for c in result.checks if c.status == "FAIL"]
        assert any(n.startswith("role_permissions_synced:SALES") for n in fail_names)


def test_preflight_trust_anchor_missing_is_warning_not_failure(app, seeded, signing_key, tmp_path):
    with app.app_context():
        from app.commercial_ops.preflight import run_preflight

        missing_path = tmp_path / "does-not-exist" / "trust_anchor.json"
        import os

        os.environ["COMMERCIAL_TRUST_ANCHOR_PATH"] = str(missing_path)
        try:
            result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        finally:
            del os.environ["COMMERCIAL_TRUST_ANCHOR_PATH"]
        names = {c.name: c.status for c in result.checks}
        assert names["trust_anchor_matches_active_key"] == "WARNING"
        assert result.ok is True  # missing anchor alone must not block preflight


def test_preflight_trust_anchor_stale_key_fails(app, seeded, signing_key, tmp_path):
    with app.app_context():
        import json
        import os

        from app.commercial_ops.preflight import run_preflight

        stale_path = tmp_path / "trust_anchor.json"
        stale_path.write_text(json.dumps({"keys": [{"key_id": "some-other-key-that-is-not-active", "public_key": "x", "algorithm": "ed25519"}]}))
        os.environ["COMMERCIAL_TRUST_ANCHOR_PATH"] = str(stale_path)
        try:
            result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        finally:
            del os.environ["COMMERCIAL_TRUST_ANCHOR_PATH"]
        assert result.ok is False
        names = {c.name: c.status for c in result.checks}
        assert names["trust_anchor_matches_active_key"] == "FAIL"


def test_preflight_trust_anchor_matching_key_passes(app, seeded, signing_key, tmp_path):
    with app.app_context():
        import json
        import os

        from app.commercial_ops.preflight import run_preflight
        from app.licensing_service.signing import get_active_signing_key

        active = get_active_signing_key()
        good_path = tmp_path / "trust_anchor.json"
        good_path.write_text(json.dumps({"keys": [{"key_id": active.key_id, "public_key": active.public_key, "algorithm": active.algorithm}]}))
        os.environ["COMMERCIAL_TRUST_ANCHOR_PATH"] = str(good_path)
        try:
            result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        finally:
            del os.environ["COMMERCIAL_TRUST_ANCHOR_PATH"]
        names = {c.name: c.status for c in result.checks}
        assert names["trust_anchor_matches_active_key"] == "OK"
        assert result.ok is True


# -- Phase 8V-P9: license-pepper preflight -----------------------------------

def test_preflight_pepper_ok_by_default(app, seeded, signing_key):
    # Test config sets LICENSE_PEPPER = "test-license-pepper" (config.py) --
    # non-empty, not the dev-insecure placeholder, so the real self-test
    # round-trip should pass cleanly.
    with app.app_context():
        from app.commercial_ops.preflight import run_preflight

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        names = {c.name: c.status for c in result.checks}
        assert names["license_pepper_configured"] == "OK"
        assert names["license_pepper_self_test_roundtrip"] == "OK"
        assert names["license_pepper_no_stray_aliases"] == "OK"


def test_preflight_pepper_empty_fails(app, seeded, signing_key):
    with app.app_context():
        from app.commercial_ops.preflight import run_preflight

        original = app.config["LICENSE_PEPPER"]
        app.config["LICENSE_PEPPER"] = ""
        try:
            result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        finally:
            app.config["LICENSE_PEPPER"] = original
        assert result.ok is False
        names = {c.name: c.status for c in result.checks}
        assert names["license_pepper_configured"] == "FAIL"


def test_preflight_pepper_dev_placeholder_is_warning_not_failure(app, seeded, signing_key):
    with app.app_context():
        from app.commercial_ops.preflight import run_preflight

        original = app.config["LICENSE_PEPPER"]
        app.config["LICENSE_PEPPER"] = "dev-only-insecure-pepper-do-not-use-in-production"
        try:
            result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        finally:
            app.config["LICENSE_PEPPER"] = original
        names = {c.name: c.status for c in result.checks}
        assert names["license_pepper_configured"] == "WARNING"
        # The dev placeholder alone must not block preflight -- real production
        # startup is separately, unconditionally blocked by
        # validate_external_api_production() regardless of this check.
        assert names["license_pepper_self_test_roundtrip"] == "OK"


def test_preflight_pepper_roundtrip_never_logs_the_pepper_value(app, seeded, signing_key):
    with app.app_context():
        from app.commercial_ops.preflight import run_preflight

        original = app.config["LICENSE_PEPPER"]
        app.config["LICENSE_PEPPER"] = "a-very-distinctive-real-pepper-value-12345"
        try:
            result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        finally:
            app.config["LICENSE_PEPPER"] = original
        for check in result.checks:
            assert "a-very-distinctive-real-pepper-value-12345" not in check.detail


def test_preflight_pepper_stray_alias_env_var_is_warning(app, seeded, signing_key):
    with app.app_context():
        import os

        from app.commercial_ops.preflight import run_preflight

        os.environ["LICENSE_KEY_PEPPER"] = "some-mistaken-value-nobody-reads"
        try:
            result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        finally:
            del os.environ["LICENSE_KEY_PEPPER"]
        names = {c.name: c.status for c in result.checks}
        assert names["license_pepper_no_stray_aliases"] == "WARNING"
        # A stray alias alone (canonical pepper is still fine) must not block.
        assert names["license_pepper_configured"] == "OK"


def test_preflight_super_admin_without_mfa_is_warning_not_failure(app, seeded, signing_key):
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import StaffUser
        from app.security.passwords import hash_password
        from app.commercial_ops.preflight import run_preflight

        # make_staff() always ties mfa_required to super_admin, so build this
        # deliberately-inconsistent row (super admin, MFA not required)
        # directly to exercise the warning path.
        staff = StaffUser(
            email="preflight-admin@example.com", display_name="Preflight Test Admin",
            password_hash=hash_password("Sup3r-Str0ng-Pass!"), is_super_admin=True, mfa_required=False,
        )
        db_session.add(staff)
        db_session.commit()

        result = run_preflight(key_directory=app.config["SIGNING_KEY_DIRECTORY"])
        names = {c.name: c.status for c in result.checks}
        assert names["super_admin_mfa_required"] == "WARNING"
        # A missing-MFA super admin alone must not fail preflight -- it's a
        # real environment risk to flag, not a validation-blocking defect.
