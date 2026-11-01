"""Shared set-up for the seller-rules tests (cash hand-in, customer ownership, sanctions, termination).

Not a test file (no test_ prefix). Needs the `seeded` fixture so the SALES / SALES_MANAGER / FINANCE roles exist."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from tests.conftest import force_login, jordan_today, make_staff


def world(app) -> dict:
    """admin (Super Admin), two sellers (SALES), a Sales Manager and a Finance manager (both hold payments.confirm)."""
    tag = uuid.uuid4().hex[:5]
    return {
        "admin": make_staff(app, f"adm-{tag}@example.com", super_admin=True, mfa=True),
        "seller": make_staff(app, f"sel1-{tag}@example.com", role_codes=["SALES"]),
        "seller2": make_staff(app, f"sel2-{tag}@example.com", role_codes=["SALES"]),
        "sm": make_staff(app, f"sm-{tag}@example.com", role_codes=["SALES_MANAGER"], mfa=True),
        "finance": make_staff(app, f"fin-{tag}@example.com", role_codes=["FINANCE"], mfa=True),
    }


def client_for(app, staff_id):
    app.config.update(WTF_CSRF_ENABLED=False)  # the question in these tests is the rule, not the CSRF token
    client = app.test_client()
    force_login(client, app, staff_id)
    return client


def make_customer(app, owner=None, name=None):
    with app.app_context():
        from app.customers.services import create_customer
        from app.extensions import db_session
        from app.models.staff import StaffUser

        actor = owner or db_session.execute(select(StaffUser.id)).scalars().first()
        return create_customer({"legal_name": name or f"Shop {uuid.uuid4().hex[:6]}",
                                "assigned_sales_staff_id": owner}, actor).id


def record(app, actor, customer_id, *, method="CASH", amount="100.00", currency="JOD"):
    """The one writer of payments, as `actor`: returns the payment id."""
    with app.app_context():
        from app.subscriptions.services import record_payment

        return record_payment(
            {"customer_id": customer_id, "amount": Decimal(amount), "currency": currency, "method": method,
             "payment_date": jordan_today(), "status": "PENDING"}, actor,
        ).id


def count(app, model, *where):
    with app.app_context():
        from app.extensions import db_session

        return db_session.execute(select(func.count()).select_from(model).where(*where)).scalar_one()


def queued_mail(app, kind):
    with app.app_context():
        from app.extensions import db_session
        from app.models.email_outbox import EmailOutbox

        return db_session.execute(select(EmailOutbox.to_address).where(EmailOutbox.kind == kind)).scalars().all()
