from __future__ import annotations

import os
import subprocess
import sys

import pyotp
import pytest
from sqlalchemy import text

TEST_DB_URL = os.environ.get(
    "OWNER_TEST_DATABASE_URL", "postgresql+psycopg://aura_owner:aura_owner_dev@localhost:5432/aura_owner_test"
)

os.environ.setdefault("OWNER_TEST_DATABASE_URL", TEST_DB_URL)
os.environ["OWNER_ENV"] = "testing"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session", autouse=True)
def _migrated_schema():
    owner_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "OWNER_DATABASE_URL": TEST_DB_URL}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=owner_dir, env=env, check=True)
    yield


@pytest.fixture()
def app():
    from app import create_app
    from app.extensions import Base, db_session

    application = create_app("testing")

    with application.app_context():
        # Truncate every owner_ table between tests -- fast, exact, and proves
        # the schema truly has no FK ordering surprises (CASCADE handles it).
        table_names = [t.name for t in Base.metadata.sorted_tables]
        db_session.execute(text(f"TRUNCATE TABLE {', '.join(table_names)} RESTART IDENTITY CASCADE"))
        db_session.commit()

    yield application

    with application.app_context():
        db_session.remove()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def seeded(app):
    with app.app_context():
        from app.catalog.services import seed_canonical_catalog
        from app.staff.seed_data import PERMISSIONS, ROLES
        from app.extensions import db_session
        from app.models.staff import Permission, Role, RolePermission

        perms = {}
        for code, category, description in PERMISSIONS:
            p = Permission(code=code, category=category, description=description)
            db_session.add(p)
            db_session.flush()
            perms[code] = p
        for code, definition in ROLES.items():
            role = Role(code=code, name=definition["name"], description=definition["description"], is_system_role=True)
            db_session.add(role)
            db_session.flush()
            wanted = set(perms.keys()) if definition["permissions"] == "*" else set(definition["permissions"])
            for wanted_code in wanted:
                db_session.add(RolePermission(role_id=role.id, permission_id=perms[wanted_code].id))
        db_session.commit()
        result = seed_canonical_catalog()
    return result


TOTP_SECRET = pyotp.random_base32()


def make_staff(app, email="staff@example.com", *, super_admin=False, role_codes=None, password="Sup3r-Str0ng-Pass!", mfa=False):
    with app.app_context():
        from app.extensions import db_session
        from app.models.staff import MfaCredential, Role, StaffRoleAssignment, StaffUser
        from app.security.mfa import encrypt_totp_secret
        from app.security.passwords import hash_password
        from sqlalchemy import select

        staff = StaffUser(
            email=email, display_name="Test Staff", password_hash=hash_password(password),
            is_super_admin=super_admin, mfa_required=super_admin,
        )
        db_session.add(staff)
        db_session.flush()
        for code in role_codes or []:
            role = db_session.execute(select(Role).where(Role.code == code)).scalars().first()
            if role is not None:
                db_session.add(StaffRoleAssignment(staff_user_id=staff.id, role_id=role.id))
        if mfa:
            db_session.add(
                MfaCredential(
                    staff_user_id=staff.id,
                    totp_secret_encrypted=encrypt_totp_secret(TOTP_SECRET, app.config["SECRET_KEY"]),
                    confirmed=True,
                )
            )
        db_session.commit()
        return staff.id


def totp_code() -> str:
    return pyotp.TOTP(TOTP_SECRET).now()


def get_csrf(html: str) -> str:
    import re

    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    return match.group(1) if match else ""


def login(client, email, password="Sup3r-Str0ng-Pass!"):
    page = client.get("/auth/login")
    csrf = get_csrf(page.get_data(as_text=True))
    return client.post("/auth/login", data={"csrf_token": csrf, "email": email, "password": password})


def force_login(client, app, staff_id):
    """Directly establishes a valid server-side session for staff_id, bypassing
    the login/MFA HTTP flow -- for tests that exercise downstream authorization
    logic rather than the login flow itself."""
    with app.test_request_context():
        from app.auth.session import COOKIE_NAME, create_session
        from app.extensions import db_session
        from app.models.staff import StaffUser

        staff = db_session.get(StaffUser, staff_id)
        raw_token = create_session(staff)
    client.set_cookie(key=COOKIE_NAME, value=raw_token, domain="localhost")


def login_and_verify_mfa(client, email, password="Sup3r-Str0ng-Pass!"):
    login(client, email, password)
    page = client.get("/auth/mfa-verify")
    csrf = get_csrf(page.get_data(as_text=True))
    return client.post("/auth/mfa-verify", data={"csrf_token": csrf, "code": totp_code()})
