"""24-month purge of unconverted WEBSITE leads (app/leads/website_retention.py).

Every guard is proven in both directions in the same database: the purge-able
lead is gone AND the keep-lead is still there, so "deletes everything" and
"deletes nothing" both fail. Leads are created through the real
ingest_website_lead(), so the notes and the idempotency key row exist exactly
as in production, then backdated with a direct UPDATE."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

NAME = "Retention Tester"
PHONE = "+962791234567"
BUSINESS = "Retention Market"


def _seed_actor(app):
    with app.app_context():
        from app.leads.website_intake import seed_website_intake_actor
        return seed_website_intake_actor()


def _make_lead(app, lead_id, *, age_days, source="WEBSITE", converted=False):
    """Create via the real intake, then backdate and optionally re-source/convert."""
    from app.extensions import db_session
    from app.leads.website_intake import ingest_website_lead, parse_website_lead_payload
    from app.models.leads import Lead

    with app.app_context():
        lead, _ = ingest_website_lead(
            parse_website_lead_payload(
                {"lead_id": lead_id, "name": NAME, "phone": PHONE, "business_type": "shop",
                 "business_name": BUSINESS, "branches": "1", "message": "private message text", "lang": "en"}
            )
        )
        values = {"created_at": datetime.now(timezone.utc) - timedelta(days=age_days), "source": source}
        if converted:
            values["converted_at"] = datetime.now(timezone.utc)
        db_session.execute(update(Lead).where(Lead.id == lead.id).values(**values))
        db_session.commit()
        return lead.id


def _count(app, model, *conds):
    from app.extensions import db_session
    with app.app_context():
        return db_session.execute(select(func.count()).select_from(model).where(*conds)).scalar_one()


def _purge(app, **kwargs):
    from app.leads.website_retention import purge_old_website_leads
    with app.app_context():
        return purge_old_website_leads(**kwargs)


def _exists(app, lead_id) -> bool:
    from app.models.leads import Lead
    return _count(app, Lead, Lead.id == lead_id) == 1


def test_old_unconverted_website_lead_is_deleted_with_notes_and_idempotency_key(app):
    from app.leads.services import LEAD_CREATION_OPERATION_CODE
    from app.models.commercial_sales import CommercialOperationsIdempotencyKey as Key
    from app.models.leads import Lead, LeadNote, LeadStatusHistory

    _seed_actor(app)
    old = _make_lead(app, "old1", age_days=731)
    assert _count(app, LeadNote) == 1 and _count(app, LeadStatusHistory) == 1
    result = _purge(app, apply=True)
    assert result["dry_run"] is False
    assert result["candidates"] == 1 and result["deleted"] == 1
    assert _count(app, Lead, Lead.id == old) == 0
    assert _count(app, LeadNote) == 0
    assert _count(app, LeadStatusHistory) == 0
    assert _count(app, Key, Key.operation_code == LEAD_CREATION_OPERATION_CODE) == 0


def test_dry_run_deletes_nothing_and_reports_the_candidate(app):
    from app.models.leads import LeadNote

    _seed_actor(app)
    old = _make_lead(app, "old1", age_days=731)
    result = _purge(app)  # apply defaults to False
    assert result["dry_run"] is True
    assert result["candidates"] == 1 and result["deleted"] == 0
    assert _exists(app, old)
    assert _count(app, LeadNote) == 1


def _keep_young(app):
    return _make_lead(app, "keep", age_days=729)


def _keep_referral(app):
    return _make_lead(app, "keep", age_days=900, source="REFERRAL")


def _keep_converted(app):
    return _make_lead(app, "keep", age_days=900, converted=True)


def _keep_customer_linked(app):
    from app.extensions import db_session
    from app.models.customers import Customer

    lead_id = _make_lead(app, "keep", age_days=900)
    with app.app_context():
        db_session.add(Customer(legal_name="Converted Co", converted_from_lead_id=lead_id))
        db_session.commit()
    return lead_id


@pytest.mark.parametrize("make_keep", [_keep_young, _keep_referral, _keep_converted, _keep_customer_linked])
def test_keep_cases_survive_while_the_purgeable_lead_goes(app, make_keep):
    # Both directions in one DB: allow (old1 gone) and deny (keep stays).
    _seed_actor(app)
    purge_me = _make_lead(app, "old1", age_days=731)
    keep = make_keep(app)
    result = _purge(app, apply=True)
    assert result["deleted"] == 1
    assert not _exists(app, purge_me)
    assert _exists(app, keep)


def test_quote_linked_lead_is_kept(app):
    from app.extensions import db_session
    from app.models.commercial_sales import Quote

    _seed_actor(app)
    keep = _make_lead(app, "keep", age_days=900)
    purge_me = _make_lead(app, "old1", age_days=900)
    with app.app_context():
        from app.leads.website_intake import get_website_intake_actor

        _staff_id, profile_id = get_website_intake_actor()
        db_session.add(
            Quote(lead_id=keep, created_by_employee_profile_id=profile_id, quote_number="Q-RETENTION-1", currency="JOD")
        )
        db_session.commit()
    result = _purge(app, apply=True)
    assert result["deleted"] == 1
    assert _exists(app, keep)
    assert not _exists(app, purge_me)


def test_audit_row_holds_counts_only(app):
    from app.extensions import db_session
    from app.models.audit import AuditLog

    _seed_actor(app)
    _make_lead(app, "old1", age_days=731)
    _purge(app, apply=True)
    with app.app_context():
        rows = db_session.execute(select(AuditLog).where(AuditLog.action_code == "WEBSITE_LEADS_PURGED")).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.after_state_redacted["deleted"] == 1
        assert row.after_state_redacted["older_than_days"] == 730
        dumped = json.dumps(
            [row.after_state_redacted, row.before_state_redacted, row.reason, row.entity_public_id], default=str
        )
        for secret in (PHONE, NAME, BUSINESS, "private message text"):
            assert secret not in dumped


def test_no_audit_row_when_nothing_was_deleted(app):
    from app.models.audit import AuditLog

    _seed_actor(app)
    _make_lead(app, "keep", age_days=10)
    assert _purge(app, apply=True)["deleted"] == 0
    assert _count(app, AuditLog, AuditLog.action_code == "WEBSITE_LEADS_PURGED") == 0


def test_cli_dry_run_then_apply(app):
    _seed_actor(app)
    old = _make_lead(app, "old1", age_days=731)
    runner = app.test_cli_runner()

    dry = runner.invoke(args=["leads", "purge-website"])
    assert dry.exit_code == 0, dry.output
    dry_data = json.loads(dry.output[dry.output.index("{"):])
    assert dry_data["dry_run"] is True and dry_data["candidates"] == 1 and dry_data["deleted"] == 0
    assert _exists(app, old)

    wet = runner.invoke(args=["leads", "purge-website", "--apply"])
    assert wet.exit_code == 0, wet.output
    wet_data = json.loads(wet.output[wet.output.index("{"):])
    assert wet_data["dry_run"] is False and wet_data["deleted"] == 1
    assert not _exists(app, old)


def test_scheduled_ops_script_runs_the_purge_with_apply():
    script = Path(__file__).resolve().parents[1] / "deploy" / "oracle-free" / "run_scheduled_ops.sh"
    text = script.read_text(encoding="utf-8")
    assert 'run_job "leads purge-website --apply" leads purge-website --apply' in text


@pytest.mark.parametrize("days", [0, -5])
def test_non_positive_retention_is_refused(app, days):
    _seed_actor(app)
    old = _make_lead(app, "old1", age_days=731)
    with pytest.raises(ValueError):
        _purge(app, older_than_days=days, apply=True)
    assert _exists(app, old)
