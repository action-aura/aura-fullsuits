"""
Aura Retail -- security regression suite.

Ported from Action Aura Enterprise's tests/retail_security_test.py (Retail
Phase 1 security remediation) -- see docs/migration/retail-extraction-report.md.
Assertions are unchanged; only the bootstrap and two module paths (below) are
adapted to this product's standalone layout.

Two tests from the source suite are intentionally NOT ported:
`test_demo_blueprint_not_registered_by_default_subprocess` and
`test_demo_blueprint_registered_when_explicitly_enabled_subprocess`. Those
verified a GENERIC, unauthenticated, multi-subsystem demo portal blueprint
(api/demo_api.py, seeds/wipes CRM+HR+Retail+... with no auth) that is
explicitly out of scope for a standalone Retail product -- see
docs/migration/dependency-map.md §5. Retail's OWN demo-seed/demo-wipe
endpoints (gated, authenticated, company-scoped) are still fully covered
below (see "Demo operations" section).

Run:
    pytest products/retail/tests/retail_security_test.py -v
"""
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

PRODUCT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = PRODUCT_DIR / 'backend'
SUITE_ROOT = PRODUCT_DIR.parent.parent
for _p in (str(SUITE_ROOT), str(BACKEND_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DATA = Path(tempfile.mkdtemp(prefix="aura_retail_security_"))
(DATA / "database" / "subsystems").mkdir(parents=True, exist_ok=True)
os.environ.update(AURA_STANDALONE="1", AURA_BUNDLE_DIR=str(BACKEND_DIR), AURA_APP_DATA=str(DATA))
os.environ.pop("AURA_DEV", None)

from commercial_runtime.licensing_contracts.test_support import seed_active_license  # noqa: E402
seed_active_license(str(DATA), product_code="AURA_RETAIL", platform="WINDOWS")
os.environ.pop("AURA_RETAIL_DEMO_MODE", None)

import app as _app_module  # noqa: E402

app = _app_module.init_app()
app.config["TESTING"] = True

from commercial_runtime.identity.registry_db import get_conn as registry_conn  # noqa: E402
from database.schema import get_retail_conn  # noqa: E402
from commercial_runtime.security.passwords import (  # noqa: E402
    hash_password, verify_password, is_legacy_sha256_hash, verify_legacy_sha256,
    needs_rehash, authenticate_and_maybe_upgrade, PasswordPolicyError,
)
from commercial_runtime.security.app_secret import get_or_create_secret_key  # noqa: E402
import commercial_runtime.security.modes as modes  # noqa: E402


def teardown_module(module):
    shutil.rmtree(DATA, ignore_errors=True)


def _make_company_user(email, password, role="admin", status="active", legacy=False):
    company_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    if legacy:
        import hashlib
        pw_hash = hashlib.sha256(password.encode()).hexdigest()
    else:
        pw_hash = hash_password(password)
    conn = registry_conn()
    conn.execute(
        "INSERT INTO users (id, company_id, employee_id, email, password_hash, role, status, require_password_change) "
        "VALUES (?,?,?,?,?,?,?,0)",
        (user_id, company_id, "EMP-0001", email, pw_hash, role, status),
    )
    conn.commit()
    conn.close()
    return user_id, company_id


# ═════════════════════════════════════════════════════════════════════════════
# 1. Unknown/invalid credential rejection
# ═════════════════════════════════════════════════════════════════════════════

def test_random_unknown_credential_rejected():
    with app.test_client() as c:
        r = c.post("/api/auth/login", json={"email": "nobody-xyz@nowhere.test", "password": "whatever123"})
        assert r.status_code == 401


def test_valid_stored_admin_can_login():
    email = "admin-valid@test.local"
    _make_company_user(email, "CorrectHorse123!", role="admin")
    with app.test_client() as c:
        r = c.post("/api/auth/login", json={"email": email, "password": "CorrectHorse123!"})
        assert r.status_code == 200
        assert r.get_json()["success"] is True


def test_valid_non_admin_cannot_gain_admin_permissions():
    email = "employee-nonadmin@test.local"
    _make_company_user(email, "EmployeePass123!", role="employee")
    with app.test_client() as c:
        c.post("/api/auth/login", json={"email": email, "password": "EmployeePass123!"})
        with c.session_transaction() as s:
            assert s.get("mt_role") != "admin"


def test_disabled_admin_cannot_login():
    email = "disabled-admin@test.local"
    _make_company_user(email, "DisabledPass123!", role="admin", status="disabled")
    with app.test_client() as c:
        r = c.post("/api/auth/login", json={"email": email, "password": "DisabledPass123!"})
        assert r.status_code == 403


def test_no_auth_path_grants_privilege_without_stored_account():
    with app.test_client() as c:
        r = c.get("/api/sub/retail/dashboard/stats")
        assert r.status_code == 401


def test_no_duplicate_login_route_registration():
    login_rules = [r for r in app.url_map.iter_rules() if r.rule == "/api/auth/login"]
    assert len(login_rules) == 1, [str(r) for r in login_rules]


# ═════════════════════════════════════════════════════════════════════════════
# 2. Password security (unit-level -- no app/DB needed)
# ═════════════════════════════════════════════════════════════════════════════

def test_hash_password_produces_modern_format():
    assert hash_password("Sup3rSecret!").startswith("pbkdf2_sha256$")


def test_hash_password_random_salt_different_hashes_same_password():
    assert hash_password("SamePassword1") != hash_password("SamePassword1")


def test_verify_correct_password():
    assert verify_password("CorrectPW1", hash_password("CorrectPW1")) is True


def test_verify_wrong_password():
    assert verify_password("WrongPW", hash_password("CorrectPW1")) is False


def test_empty_password_rejected_on_hash():
    with pytest.raises(PasswordPolicyError):
        hash_password("")


def test_verify_password_never_raises_on_malformed_hash():
    assert verify_password("whatever", "not-a-real-hash") is False
    assert verify_password("whatever", "") is False
    assert verify_password("", "pbkdf2_sha256$1$aa$bb") is False


def test_legacy_hash_detection():
    import hashlib
    legacy = hashlib.sha256(b"legacypw").hexdigest()
    assert is_legacy_sha256_hash(legacy) is True
    assert is_legacy_sha256_hash("pbkdf2_sha256$600000$x$y") is False
    assert is_legacy_sha256_hash("garbage") is False
    assert is_legacy_sha256_hash("") is False


def test_legacy_verify_correct_and_incorrect():
    import hashlib
    legacy = hashlib.sha256(b"legacypw").hexdigest()
    assert verify_legacy_sha256("legacypw", legacy) is True
    assert verify_legacy_sha256("wrongpw", legacy) is False


def test_needs_rehash():
    import hashlib
    legacy = hashlib.sha256(b"x").hexdigest()
    assert needs_rehash(legacy) is True
    assert needs_rehash("garbage") is True
    modern = hash_password("x")
    assert needs_rehash(modern) is False
    parts = modern.split("$")
    outdated = f"pbkdf2_sha256$1000${parts[2]}${parts[3]}"
    assert needs_rehash(outdated) is True


def test_authenticate_and_maybe_upgrade_modern_ok():
    h = hash_password("ModernPW1")
    ok, new_hash = authenticate_and_maybe_upgrade("ModernPW1", h)
    assert ok is True and new_hash is None


def test_authenticate_and_maybe_upgrade_legacy_migrates():
    import hashlib
    legacy = hashlib.sha256(b"LegacyPW1").hexdigest()
    ok, new_hash = authenticate_and_maybe_upgrade("LegacyPW1", legacy)
    assert ok is True
    assert new_hash is not None and new_hash.startswith("pbkdf2_sha256$")


def test_authenticate_and_maybe_upgrade_wrong_password():
    h = hash_password("RightPW1")
    ok, new_hash = authenticate_and_maybe_upgrade("WrongPW1", h)
    assert ok is False and new_hash is None


def test_authenticate_and_maybe_upgrade_malformed_hash_fails_safely():
    ok, new_hash = authenticate_and_maybe_upgrade("anything", "totally-not-a-hash")
    assert ok is False and new_hash is None


# ═════════════════════════════════════════════════════════════════════════════
# 3. Legacy migration on login (integration -- uses the shared app + registry DB)
# ═════════════════════════════════════════════════════════════════════════════

def test_legacy_account_migrates_on_login():
    import hashlib
    email = "legacy-user@test.local"
    legacy_hash = hashlib.sha256(b"LegacyLoginPW1").hexdigest()
    uid, _cid = _make_company_user(email, "LegacyLoginPW1", role="admin", legacy=True)

    conn = registry_conn()
    before = conn.execute("SELECT password_hash FROM users WHERE id=?", (uid,)).fetchone()["password_hash"]
    conn.close()
    assert before == legacy_hash

    with app.test_client() as c:
        r = c.post("/api/auth/login", json={"email": email, "password": "LegacyLoginPW1"})
        assert r.status_code == 200

    conn = registry_conn()
    after = conn.execute("SELECT password_hash FROM users WHERE id=?", (uid,)).fetchone()["password_hash"]
    conn.close()
    assert after != before
    assert after.startswith("pbkdf2_sha256$")

    with app.test_client() as c:
        r2 = c.post("/api/auth/login", json={"email": email, "password": "LegacyLoginPW1"})
        assert r2.status_code == 200


def test_legacy_migration_incorrect_password_does_not_migrate():
    import hashlib
    email = "legacy-badpw@test.local"
    legacy_hash = hashlib.sha256(b"RealPassword1").hexdigest()
    uid, _cid = _make_company_user(email, "RealPassword1", role="admin", legacy=True)

    with app.test_client() as c:
        r = c.post("/api/auth/login", json={"email": email, "password": "WrongGuess1"})
        assert r.status_code == 401

    conn = registry_conn()
    still = conn.execute("SELECT password_hash FROM users WHERE id=?", (uid,)).fetchone()["password_hash"]
    conn.close()
    assert still == legacy_hash


def test_malformed_stored_hash_fails_login_safely():
    email = "malformed-hash@test.local"
    uid, _cid = _make_company_user(email, "whatever", role="admin")
    conn = registry_conn()
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", ("###not-a-hash###", uid))
    conn.commit()
    conn.close()
    with app.test_client() as c:
        r = c.post("/api/auth/login", json={"email": email, "password": "whatever"})
        assert r.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# 4. Secret management (unit-level, isolated dirs)
# ═════════════════════════════════════════════════════════════════════════════

def test_secret_generated_on_first_run():
    d = tempfile.mkdtemp(prefix="aura_secret_")
    try:
        secret = get_or_create_secret_key(d)
        assert len(secret) == 64
        assert os.path.exists(os.path.join(d, "security", "secret.key"))
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_secret_persists_across_restarts():
    d = tempfile.mkdtemp(prefix="aura_secret_")
    try:
        s1 = get_or_create_secret_key(d)
        s2 = get_or_create_secret_key(d)
        assert s1 == s2
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_different_installations_produce_different_secrets():
    d1 = tempfile.mkdtemp(prefix="aura_secret_a_")
    d2 = tempfile.mkdtemp(prefix="aura_secret_b_")
    try:
        assert get_or_create_secret_key(d1) != get_or_create_secret_key(d2)
    finally:
        shutil.rmtree(d1, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)


def test_corrupted_secret_file_regenerates_not_falls_back():
    d = tempfile.mkdtemp(prefix="aura_secret_corrupt_")
    try:
        s1 = get_or_create_secret_key(d)
        path = os.path.join(d, "security", "secret.key")
        with open(path, "w") as f:
            f.write("not-valid-hex-!!!")
        s2 = get_or_create_secret_key(d)
        assert s2 != s1
        assert len(s2) == 64
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_no_hardcoded_fallback_literal_in_config():
    src = (BACKEND_DIR / "config.py").read_text(encoding="utf-8")
    assert "aura-enterprise-secret-2025-xK9mP2vL" not in src


# ═════════════════════════════════════════════════════════════════════════════
# 5. Runtime mode boundary
# ═════════════════════════════════════════════════════════════════════════════

def test_dev_mode_off_by_default():
    os.environ.pop("AURA_DEV", None)
    assert modes.dev_mode_enabled() is False


def test_demo_mode_off_by_default():
    os.environ.pop("AURA_RETAIL_DEMO_MODE", None)
    assert modes.retail_demo_mode_enabled() is False


def test_demo_mode_on_when_explicitly_set_non_frozen():
    os.environ["AURA_RETAIL_DEMO_MODE"] = "1"
    try:
        assert modes.retail_demo_mode_enabled() is True
    finally:
        os.environ.pop("AURA_RETAIL_DEMO_MODE", None)


def test_frozen_build_ignores_env_vars():
    os.environ["AURA_RETAIL_DEMO_MODE"] = "1"
    os.environ["AURA_DEV"] = "1"
    had_frozen = hasattr(sys, "frozen")
    old_frozen = getattr(sys, "frozen", None)
    sys.frozen = True
    try:
        importlib.reload(modes)
        assert modes.retail_demo_mode_enabled() is False
        assert modes.dev_mode_enabled() is False
    finally:
        if had_frozen:
            sys.frozen = old_frozen
        else:
            delattr(sys, "frozen")
        os.environ.pop("AURA_RETAIL_DEMO_MODE", None)
        os.environ.pop("AURA_DEV", None)
        importlib.reload(modes)


# ═════════════════════════════════════════════════════════════════════════════
# 6. Demo operations (retail demo-wipe / demo-seed -- Retail's own gated routes,
#    NOT the generic multi-subsystem demo portal -- see module docstring)
# ═════════════════════════════════════════════════════════════════════════════

def test_demo_routes_unavailable_when_demo_mode_off():
    os.environ.pop("AURA_RETAIL_DEMO_MODE", None)
    email = "demo-admin-off@test.local"
    _uid, cid = _make_company_user(email, "DemoAdminPW1", role="admin")
    with app.test_client() as c:
        c.post("/api/auth/login", json={"email": email, "password": "DemoAdminPW1"})
        r = c.delete("/api/sub/retail/demo-wipe", json={"confirm": f"WIPE-{cid}"})
        assert r.status_code == 404


def test_demo_wipe_denied_to_non_admin_even_in_demo_mode():
    os.environ["AURA_RETAIL_DEMO_MODE"] = "1"
    try:
        email = "demo-employee@test.local"
        _uid, cid = _make_company_user(email, "DemoEmployeePW1", role="employee")
        with app.test_client() as c:
            c.post("/api/auth/login", json={"email": email, "password": "DemoEmployeePW1"})
            r = c.delete("/api/sub/retail/demo-wipe", json={"confirm": f"WIPE-{cid}"})
            assert r.status_code == 403
    finally:
        os.environ.pop("AURA_RETAIL_DEMO_MODE", None)


def test_demo_wipe_requires_confirmation_token():
    os.environ["AURA_RETAIL_DEMO_MODE"] = "1"
    try:
        email = "demo-admin-noconfirm@test.local"
        _make_company_user(email, "DemoAdminPW2", role="admin")
        with app.test_client() as c:
            c.post("/api/auth/login", json={"email": email, "password": "DemoAdminPW2"})
            r = c.delete("/api/sub/retail/demo-wipe", json={})
            assert r.status_code == 400
    finally:
        os.environ.pop("AURA_RETAIL_DEMO_MODE", None)


def test_demo_wipe_scoped_to_own_company_only():
    os.environ["AURA_RETAIL_DEMO_MODE"] = "1"
    try:
        email_a = "company-a-admin@test.local"
        _uid_a, cid_a = _make_company_user(email_a, "CompanyAPW1", role="admin")
        email_b = "company-b-admin@test.local"
        _uid_b, cid_b = _make_company_user(email_b, "CompanyBPW1", role="admin")

        rconn = get_retail_conn()
        rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'A Branch')", (cid_a,))
        rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'B Branch')", (cid_b,))
        rconn.execute(
            "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price) VALUES (?,?,'A-SKU','A Product',1,2)",
            (str(uuid.uuid4()), cid_a),
        )
        rconn.execute(
            "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price) VALUES (?,?,'B-SKU','B Product',1,2)",
            (str(uuid.uuid4()), cid_b),
        )
        rconn.commit()
        rconn.close()

        with app.test_client() as c:
            c.post("/api/auth/login", json={"email": email_a, "password": "CompanyAPW1"})
            r = c.delete("/api/sub/retail/demo-wipe", json={"confirm": f"WIPE-{cid_a}"})
            assert r.status_code == 200, r.get_json()

        rconn = get_retail_conn()
        a_products = rconn.execute("SELECT COUNT(*) FROM products WHERE company_id=?", (cid_a,)).fetchone()[0]
        b_products = rconn.execute("SELECT COUNT(*) FROM products WHERE company_id=?", (cid_b,)).fetchone()[0]
        rconn.close()
        assert a_products == 0
        assert b_products == 1
    finally:
        os.environ.pop("AURA_RETAIL_DEMO_MODE", None)


def test_demo_wipe_rolls_back_on_failure(monkeypatch):
    os.environ["AURA_RETAIL_DEMO_MODE"] = "1"
    try:
        import api.retail_api as retail_api

        email = "rollback-admin@test.local"
        _uid, cid = _make_company_user(email, "RollbackPW1", role="admin")

        rconn = get_retail_conn()
        rconn.execute("INSERT INTO branches (company_id,name) VALUES (?, 'RB Branch')", (cid,))
        rconn.execute(
            "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price) VALUES (?,?,'RB-SKU','RB Product',1,2)",
            (str(uuid.uuid4()), cid),
        )
        rconn.commit()
        rconn.close()

        monkeypatch.setattr(
            retail_api, "_WIPE_STATEMENTS",
            (
                ("products", "DELETE FROM products WHERE company_id=?"),
                ("bogus", "DELETE FROM this_table_does_not_exist WHERE company_id=?"),
            ),
        )

        with app.test_client() as c:
            c.post("/api/auth/login", json={"email": email, "password": "RollbackPW1"})
            r = c.delete("/api/sub/retail/demo-wipe", json={"confirm": f"WIPE-{cid}"})
            assert r.status_code == 500

        rconn = get_retail_conn()
        remaining = rconn.execute("SELECT COUNT(*) FROM products WHERE company_id=?", (cid,)).fetchone()[0]
        rconn.close()
        assert remaining == 1
    finally:
        os.environ.pop("AURA_RETAIL_DEMO_MODE", None)


# ═════════════════════════════════════════════════════════════════════════════
# 7. Authorization
# ═════════════════════════════════════════════════════════════════════════════

def test_subsystem_access_denied_without_permission_row():
    email = "no-perm-employee@test.local"
    _make_company_user(email, "NoPermPW1", role="employee")
    with app.test_client() as c:
        c.post("/api/auth/login", json={"email": email, "password": "NoPermPW1"})
        r = c.get("/api/sub/retail/dashboard/stats")
        assert r.status_code == 403


def test_cross_company_data_isolation():
    email_a = "iso-a-admin@test.local"
    _uid_a, cid_a = _make_company_user(email_a, "IsoAPW1", role="admin")
    email_b = "iso-b-admin@test.local"
    _uid_b, cid_b = _make_company_user(email_b, "IsoBPW1", role="admin")

    rconn = get_retail_conn()
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price) VALUES (?,?,'ISO-A','A',1,2)", (str(uuid.uuid4()), cid_a)
    )
    rconn.execute(
        "INSERT INTO products (id,company_id,sku,name,cost_price,sell_price) VALUES (?,?,'ISO-B','B',1,2)", (str(uuid.uuid4()), cid_b)
    )
    rconn.commit()
    rconn.close()

    with app.test_client() as c:
        c.post("/api/auth/login", json={"email": email_a, "password": "IsoAPW1"})
        r = c.get("/api/sub/retail/products")
        skus = [p["sku"] for p in r.get_json().get("data", [])]
        assert "ISO-A" in skus
        assert "ISO-B" not in skus


def test_missing_registry_fails_closed_for_provisioned_tenant():
    from commercial_runtime.identity.mt_auth import _is_module_enabled

    cid = str(uuid.uuid4())
    conn = registry_conn()
    conn.execute(
        "INSERT INTO company_modules (id, company_id, module_code, status, enabled_at) VALUES (?,?,?,?,datetime('now'))",
        (str(uuid.uuid4()), cid, "hr", "enabled"),
    )
    conn.commit()
    conn.close()
    assert _is_module_enabled(cid, "retail") is False
    assert _is_module_enabled(cid, "hr") is True


# ═════════════════════════════════════════════════════════════════════════════
# 8. Login throttling / lockout
# ═════════════════════════════════════════════════════════════════════════════

def test_account_locks_after_max_failed_attempts():
    from commercial_runtime.identity.mt_auth import authenticate_registry_user, MAX_FAILED_ATTEMPTS

    email = "lockout-user@test.local"
    _make_company_user(email, "LockoutPW1", role="admin")
    for _ in range(MAX_FAILED_ATTEMPTS):
        result = authenticate_registry_user(email, "WrongPassword")
        assert result["ok"] is False
    result = authenticate_registry_user(email, "LockoutPW1")
    assert result["ok"] is False
    assert result["locked"] is True


def test_successful_login_resets_failed_attempt_counter():
    from commercial_runtime.identity.mt_auth import authenticate_registry_user

    email = "reset-counter-user@test.local"
    _make_company_user(email, "ResetCounterPW1", role="admin")
    authenticate_registry_user(email, "WrongPassword")
    authenticate_registry_user(email, "WrongPassword")
    ok_result = authenticate_registry_user(email, "ResetCounterPW1")
    assert ok_result["ok"] is True
    conn = registry_conn()
    row = conn.execute(
        "SELECT failed_login_count FROM users WHERE id=?", (ok_result["user"]["id"],)
    ).fetchone()
    conn.close()
    assert row["failed_login_count"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# 9. Sessions
# ═════════════════════════════════════════════════════════════════════════════

def test_login_creates_valid_session():
    email = "session-user@test.local"
    _make_company_user(email, "SessionPW1", role="admin")
    with app.test_client() as c:
        r = c.post("/api/auth/login", json={"email": email, "password": "SessionPW1"})
        assert r.status_code == 200
        with c.session_transaction() as s:
            assert s.get("mt_user_id") is not None
            assert s.get("email") == email


def test_logout_invalidates_session():
    email = "logout-user@test.local"
    _make_company_user(email, "LogoutPW1", role="admin")
    with app.test_client() as c:
        c.post("/api/auth/login", json={"email": email, "password": "LogoutPW1"})
        c.post("/api/auth/logout")
        with c.session_transaction() as s:
            assert "mt_user_id" not in s


def test_session_never_contains_plaintext_password_or_hash():
    email = "nohash-user@test.local"
    _make_company_user(email, "NoHashPW1", role="admin")
    with app.test_client() as c:
        c.post("/api/auth/login", json={"email": email, "password": "NoHashPW1"})
        with c.session_transaction() as s:
            blob = json.dumps(dict(s))
            assert "NoHashPW1" not in blob
            assert "password_hash" not in blob
            assert "pbkdf2_sha256$" not in blob


def test_rejected_missing_session():
    with app.test_client() as c:
        r = c.get("/api/sub/retail/dashboard/stats")
        assert r.status_code == 401


def test_session_has_bounded_lifetime():
    assert app.permanent_session_lifetime.total_seconds() > 0
    assert app.permanent_session_lifetime.total_seconds() <= 24 * 3600


def test_cookie_hardening_flags():
    assert app.config.get("SESSION_COOKIE_HTTPONLY") is True
    assert app.config.get("SESSION_COOKIE_SAMESITE") == "Lax"


# ═════════════════════════════════════════════════════════════════════════════
# 10. New routes (PO-preview-by-supplier foundation, Stream B) -- every new
#     route must reject an unauthenticated request (401, same shape as
#     test_no_auth_path_grants_privilege_without_stored_account /
#     test_rejected_missing_session above) and a request from a session with
#     no permission row for the retail subsystem (403, same shape as
#     test_subsystem_access_denied_without_permission_row above). Fake ids
#     are fine in the URLs below -- mt_require_subsystem runs before any
#     route body/lookup logic, same reasoning as
#     retail_capability_guard_test.py's test_restricted_blocks_supplier_payment.
# ═════════════════════════════════════════════════════════════════════════════

_NEW_ROUTES = [
    ("post", "/api/sub/retail/purchase-orders/split-preview", {"items": []}),
    ("get", "/api/sub/retail/suppliers/does-not-exist/contacts", None),
    ("post", "/api/sub/retail/suppliers/does-not-exist/contacts", {"name": "x"}),
    ("patch", "/api/sub/retail/suppliers/does-not-exist/contacts/also-fake", {"name": "x"}),
    ("delete", "/api/sub/retail/suppliers/does-not-exist/contacts/also-fake", None),
]


@pytest.mark.parametrize("method,url,body", _NEW_ROUTES)
def test_new_routes_reject_unauthenticated_requests(method, url, body):
    with app.test_client() as c:
        r = getattr(c, method)(url, json=body)
        assert r.status_code == 401, (method, url, r.status_code)


@pytest.mark.parametrize("method,url,body", _NEW_ROUTES)
def test_new_routes_reject_session_without_retail_permission(method, url, body):
    email = f"no-perm-{method}-{uuid.uuid4().hex[:8]}@test.local"
    _make_company_user(email, "NoPermPW1", role="employee")
    with app.test_client() as c:
        c.post("/api/auth/login", json={"email": email, "password": "NoPermPW1"})
        r = getattr(c, method)(url, json=body)
        assert r.status_code == 403, (method, url, r.status_code)
