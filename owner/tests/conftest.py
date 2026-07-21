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


# -- Phase 6 helpers -----------------------------------------------------

@pytest.fixture()
def signing_key(app):
    """Generates and activates a real Ed25519 signing key for this test,
    under the isolated OWNER_TEST_SIGNING_KEY_DIRECTORY (never the dev/prod
    directory)."""
    with app.app_context():
        from app.licensing_service.signing import activate_signing_key, generate_signing_key

        row = generate_signing_key(app.config["SIGNING_KEY_DIRECTORY"])
        activate_signing_key(app.config["SIGNING_KEY_DIRECTORY"], row.key_id)
        return row.key_id


def make_license(app, actor_id, *, device_limit=1, product_code="AURA_CLINIC", plan_code=None):
    """Creates a real customer -> subscription(ACTIVE) -> issued license chain
    and returns (license_id, full_key)."""
    with app.app_context():
        import uuid as uuid_mod

        from app.extensions import db_session
        from app.licensing.services import create_license, issue_license_key
        from app.models.catalog import Plan, Product
        from app.models.customers import Customer
        from app.subscriptions.services import create_subscription, transition_subscription
        from sqlalchemy import select

        product = db_session.execute(select(Product).where(Product.product_code == product_code)).scalars().first()
        plan_code = plan_code or f"TESTPLAN-{uuid_mod.uuid4().hex[:8]}"
        plan = Plan(plan_code=plan_code, product_id=product.id, name=plan_code, billing_model="PILOT", currency="USD")
        customer = Customer(legal_name=f"Test Co {uuid_mod.uuid4().hex[:8]}")
        db_session.add_all([plan, customer])
        db_session.commit()

        sub = create_subscription({"customer_id": customer.id, "product_id": product.id, "plan_id": plan.id}, actor_id)
        transition_subscription(sub, "ACTIVE", actor_id)

        lic = create_license(
            {"customer_id": customer.id, "subscription_id": sub.id, "product_id": product.id, "plan_id": plan.id,
             "allowed_platforms": "WINDOWS,ANDROID", "device_limit": device_limit},
            actor_id,
        )
        lic, full_key = issue_license_key(lic, app.config["LICENSE_PEPPER"], str(uuid_mod.uuid4()), actor_id)
        return lic.id, full_key


def make_device_keypair():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    return Ed25519PrivateKey.generate()


def public_key_b64(private_key) -> str:
    import base64

    from cryptography.hazmat.primitives import serialization

    return base64.b64encode(
        private_key.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    ).decode("ascii")


def sign_body(private_key, body: dict) -> dict:
    import base64

    from app.licensing_service.canonical import canonicalize_bytes

    signable = {k: v for k, v in body.items() if k != "signature"}
    signature = private_key.sign(canonicalize_bytes(signable))
    return {**body, "signature": base64.b64encode(signature).decode("ascii")}


def build_activation_body(private_key, *, full_key: str, installation_id: str, product_code="AURA_CLINIC", platform="WINDOWS", **overrides) -> dict:
    import uuid as uuid_mod
    from datetime import datetime, timezone

    body = {
        "contract_version": "v1",
        "request_id": str(uuid_mod.uuid4()),
        "correlation_id": str(uuid_mod.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nonce": uuid_mod.uuid4().hex,
        "product_code": product_code,
        "platform": platform,
        "app_version": "1.0.0-test",
        "installation_id": installation_id,
        "device_public_key": public_key_b64(private_key),
        "device_public_key_algorithm": "ed25519",
        "license_key": full_key,
        "idempotency_key": str(uuid_mod.uuid4()),
    }
    body.update(overrides)
    return sign_body(private_key, body)


def build_checkin_body(private_key, *, installation_id: str, **overrides) -> dict:
    import uuid as uuid_mod
    from datetime import datetime, timezone

    body = {
        "contract_version": "v1",
        "request_id": str(uuid_mod.uuid4()),
        "correlation_id": str(uuid_mod.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "nonce": uuid_mod.uuid4().hex,
        "installation_id": installation_id,
    }
    body.update(overrides)
    return sign_body(private_key, body)
