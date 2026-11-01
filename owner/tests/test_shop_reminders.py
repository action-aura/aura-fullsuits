"""feat/shop-reminders: the clock-driven mails to a shop (trial ending / ended, renewal due,
grace started, selling paused) and the nightly command that queues them.

Time is frozen by passing `as_of` (the service's own parameter; production reads Jordan's
today). Every assertion is on what was WRITTEN (mail rows in the outbox, their keys and
recipients), so "the check ran" is shown by counts, not by an exit code. Each rule is proven in
both directions: the day it fires AND the days it must not.
"""
from __future__ import annotations

import re
import shlex
import uuid
from datetime import date, timedelta
from decimal import Decimal  # noqa: F401  (kept for readers extending the fixtures)
from pathlib import Path

import pytest
from sqlalchemy import func, select

from tests.conftest import jordan_today

END = date(2026, 12, 20)  # an arbitrary fixed end day; every test moves `as_of` around it
IBAN = "JO93ARAB1620000000162291164500"
PHONE = "+962 78 006 9485"
ACCOUNT_NAME = "شركة الذئب الاسود لتصميم وتشغيل التطبيقات وبرامج الاعمال"
KINDS = ("customer_trial_ending", "customer_trial_ended", "customer_renewal_due", "customer_grace_started",
         "customer_locked")


@pytest.fixture()
def plans(app, seeded):
    with app.app_context():
        from app.extensions import db_session
        from app.models.catalog import Plan, Product

        product = db_session.execute(select(Product).where(Product.product_code == "AURA_RETAIL")).scalars().one()
        made = {}
        for key, code, model, interval in (("year", "T_YEAR", "ANNUAL", 12), ("month", "T_MONTH", "MONTHLY", 1)):
            plan = Plan(plan_code=code, product_id=product.id, name=code, billing_model=model,
                        billing_interval_months=interval, currency="JOD", lifecycle_status="AVAILABLE")
            db_session.add(plan)
            db_session.flush()
            made[key] = plan.id
        db_session.commit()
        return {"product": product.id, **made}


def _shop(app, plans, *, status="ACTIVE", end=END, plan="year", email="shop@example.com", language="ar",
          account_status="INVITED", archived=False, with_account=True, branch=None):
    """One customer (+ account) with one subscription. Returns ids."""
    with app.app_context():
        from datetime import datetime, timezone

        from app.extensions import db_session
        from app.models.customer_accounts import CustomerAccount
        from app.models.customers import Customer
        from app.models.subscriptions import Subscription

        customer = Customer(legal_name=f"Shop {uuid.uuid4().hex[:6]}", primary_language=language)
        if archived:
            customer.archived_at = datetime.now(timezone.utc)
        db_session.add(customer)
        db_session.flush()
        if with_account:
            account = CustomerAccount(customer_id=customer.id, email=email, language=language, status=account_status)
            if account_status == "DISABLED":
                account.disabled_at = datetime.now(timezone.utc)
            db_session.add(account)
        sub = Subscription(customer_id=customer.id, product_id=plans["product"], plan_id=plans[plan], status=status,
                           end_date=end, branch_label=branch)
        db_session.add(sub)
        db_session.commit()
        return {"customer": customer.id, "sub": sub.id}


def _mail_on(app):
    app.config.update(SMTP_HOST="smtp.test", CUSTOMER_MAIL_FROM="", CUSTOMER_MAIL_REPLY_TO="")


def _run(app, as_of, *, apply=True):
    with app.app_context():
        from app.extensions import db_session
        from app.shop_reminders import service

        result = service.run(apply=apply, as_of=as_of)
        db_session.remove()
        return result


def _mails(app, kind=None):
    with app.app_context():
        from app.extensions import db_session
        from app.models.email_outbox import EmailOutbox

        db_session.expire_all()
        stmt = select(EmailOutbox).order_by(EmailOutbox.created_at)
        if kind:
            stmt = stmt.where(EmailOutbox.kind == kind)
        found = db_session.execute(stmt).scalars().all()
        db_session.expunge_all()
        return found


def _count(app):
    return len(_mails(app))


def _set(app, sub_id, **fields):
    with app.app_context():
        from app.extensions import db_session
        from app.models.subscriptions import Subscription

        row = db_session.get(Subscription, sub_id)
        for name, value in fields.items():
            setattr(row, name, value)
        db_session.commit()


# ------------------------------------------------------------------ TRIAL_ENDING

def test_trial_ending_fires_two_days_before_the_end_and_only_in_its_window(app, plans):
    _mail_on(app)
    shop = _shop(app, plans, status="PILOT")
    assert _run(app, END - timedelta(days=3))["due"] == 0 and _count(app) == 0, "three days left: too early"
    result = _run(app, END - timedelta(days=2))
    assert result["queued"] == 1 and [m.kind for m in _mails(app)] == ["customer_trial_ending"]
    (mail,) = _mails(app)
    assert mail.to_address == "shop@example.com"
    assert mail.dedupe_key == f"shop_reminder:customer_trial_ending:{shop['sub']}:{END.isoformat()}:-"
    for needed in (END.isoformat(), IBAN, PHONE, ACCOUNT_NAME, "Arab Bank", "CliQ", "your seller"):
        assert needed in mail.body_text, needed
    # the end day itself still sells: the 'ending' mail is still the right one, no 'ended' mail yet
    assert _run(app, END)["queued"] == 0 and _count(app) == 1, "already mailed for this end date: not again"


def test_trial_ending_is_not_sent_once_the_trial_has_ended_or_before_the_clock_started(app, plans):
    _mail_on(app)
    _shop(app, plans, status="PILOT", end=None, email="a@example.com")  # trial clock not started: no date
    assert _run(app, END)["due"] == 0 and _count(app) == 0
    _shop(app, plans, status="PILOT", email="b@example.com")
    assert _run(app, END + timedelta(days=1))["queued"] == 1
    assert [m.kind for m in _mails(app)] == ["customer_trial_ended"], "after the end only the 'ended' mail, never 'ending'"


# ------------------------------------------------------------------ TRIAL_ENDED

def test_trial_ended_fires_the_first_night_it_is_locked_with_a_short_catch_up_and_never_on_the_end_day(app, plans):
    _mail_on(app)
    _shop(app, plans, status="PILOT", email="ended@example.com")
    on_the_end_day = _run(app, END, apply=False)["items"]
    assert [i["kind"] for i in on_the_end_day] == ["customer_trial_ending"], \
        "on the end day the shop can still sell all day: only the 'ending' mail is due, never 'ended'"
    assert _run(app, END + timedelta(days=1))["queued"] == 1
    (ended,) = _mails(app, "customer_trial_ended")
    assert "paused" in ended.body_text and "nothing was deleted" in ended.body_text and IBAN in ended.body_text
    assert _run(app, END + timedelta(days=1))["queued"] == 0, "once"
    assert _run(app, END + timedelta(days=4))["due"] == 0, "outside the catch-up window: a stale mail is never sent"


def test_a_missed_night_is_caught_up_the_next_day_without_a_duplicate(app, plans):
    _mail_on(app)
    _shop(app, plans, status="PILOT")
    # the job did not run on END-2 (server down); it runs on END-1
    assert _run(app, END - timedelta(days=1))["queued"] == 1
    assert _run(app, END)["queued"] == 0 and _count(app) == 1


def test_an_extended_trial_has_a_new_end_date_and_is_reminded_again(app, plans):
    _mail_on(app)
    shop = _shop(app, plans, status="PILOT")
    _run(app, END - timedelta(days=2))
    new_end = END + timedelta(days=7)
    _set(app, shop["sub"], end_date=new_end)
    assert _run(app, END)["due"] == 0, "the old end date no longer applies"
    assert _run(app, new_end - timedelta(days=2))["queued"] == 1
    assert len(_mails(app, "customer_trial_ending")) == 2


# ------------------------------------------------------------------ RENEWAL_DUE

def test_yearly_renewal_is_mailed_at_30_and_at_7_days_and_not_between(app, plans):
    _mail_on(app)
    shop = _shop(app, plans, plan="year")
    for days_before in (45, 31, 27, 20, 8, 4, 0):
        assert _run(app, END - timedelta(days=days_before))["due"] == 0, days_before
    assert _count(app) == 0
    assert _run(app, END - timedelta(days=30))["queued"] == 1
    assert _run(app, END - timedelta(days=29))["queued"] == 0, "same step, already mailed"
    assert _run(app, END - timedelta(days=15))["due"] == 0 and _count(app) == 1
    assert _run(app, END - timedelta(days=7))["queued"] == 1
    assert _run(app, END - timedelta(days=6))["queued"] == 0
    mails = _mails(app, "customer_renewal_due")
    assert [m.dedupe_key for m in mails] == [
        f"shop_reminder:customer_renewal_due:{shop['sub']}:{END.isoformat()}:30",
        f"shop_reminder:customer_renewal_due:{shop['sub']}:{END.isoformat()}:7"]
    assert "Days left: 30" in mails[0].body_text and "Days left: 7" in mails[1].body_text
    assert IBAN in mails[0].body_text and END.isoformat() in mails[0].body_text


def test_monthly_renewal_is_mailed_five_days_before_only(app, plans):
    _mail_on(app)
    _shop(app, plans, plan="month")
    for days_before in (30, 7, 6, 2, 0):
        assert _run(app, END - timedelta(days=days_before))["due"] == 0, days_before
    assert _run(app, END - timedelta(days=5))["queued"] == 1 and _count(app) == 1
    assert _run(app, END - timedelta(days=4))["queued"] == 0


def test_a_renewed_subscription_starts_a_new_cycle_with_new_keys(app, plans):
    _mail_on(app)
    shop = _shop(app, plans, plan="month")
    _run(app, END - timedelta(days=5))
    new_end = END + timedelta(days=30)  # the renewal pipeline moves end_date
    _set(app, shop["sub"], end_date=new_end)
    assert _run(app, END - timedelta(days=5))["due"] == 0, "the renewed end date is far away"
    assert _run(app, new_end - timedelta(days=5))["queued"] == 1 and _count(app) == 2


def test_renewal_is_only_for_active_subscriptions_with_a_dated_term(app, plans):
    _mail_on(app)
    _shop(app, plans, plan="month", end=None, email="perpetual@example.com")
    _shop(app, plans, plan="month", status="SUSPENDED", email="suspended@example.com")
    _shop(app, plans, plan="month", status="DRAFT", email="draft@example.com")
    assert _run(app, END - timedelta(days=5))["due"] == 0 and _count(app) == 0


# ------------------------------------------------------------------ GRACE_STARTED / LOCKED

def test_grace_started_is_for_past_due_in_its_first_days_only(app, plans):
    _mail_on(app)
    _shop(app, plans, status="PAST_DUE")
    assert _run(app, END)["due"] == 0, "still the paid-until day: the scan has not moved it yet"
    assert _run(app, END + timedelta(days=1))["queued"] == 1
    (mail,) = _mails(app, "customer_grace_started")
    assert "7 days of grace" in mail.body_text and IBAN in mail.body_text
    assert (END + timedelta(days=7)).isoformat() in mail.body_text, "the last day the shop can pay in time"
    assert _run(app, END + timedelta(days=2))["queued"] == 0
    assert _run(app, END + timedelta(days=6))["due"] == 0, "a late first run does not mail a long-stale grace notice"


def test_locked_is_for_expired_from_day_eight_with_a_short_catch_up(app, plans):
    _mail_on(app)
    _shop(app, plans, status="EXPIRED")
    assert _run(app, END + timedelta(days=7))["due"] == 0
    assert _run(app, END + timedelta(days=8))["queued"] == 1
    (mail,) = _mails(app, "customer_locked")
    assert "paused" in mail.body_text and "nothing was deleted" in mail.body_text and PHONE in mail.body_text
    assert _run(app, END + timedelta(days=9))["queued"] == 0 and _count(app) == 1
    assert _run(app, END + timedelta(days=12))["due"] == 0


def test_one_subscription_never_gets_two_kinds_on_one_day_and_the_status_decides_the_kind(app, plans):
    _mail_on(app)
    _shop(app, plans, status="ACTIVE", email="active@example.com")  # an ACTIVE row past its end: the scan has not run
    assert _run(app, END + timedelta(days=1))["due"] == 0, "ACTIVE past its end is not a grace notice: the scan decides"


# ------------------------------------------------------------------ who is mailed

def test_cancelled_archived_disabled_and_account_less_shops_get_nothing(app, plans):
    _mail_on(app)
    _shop(app, plans, status="CANCELLED", email="cancelled@example.com", plan="month")
    _shop(app, plans, archived=True, email="archived@example.com", plan="month")
    _shop(app, plans, account_status="DISABLED", email="disabled@example.com", plan="month")
    _shop(app, plans, with_account=False, plan="month")
    assert _run(app, END - timedelta(days=5))["due"] == 0 and _count(app) == 0
    # ... and the same date for a shop that IS mailable does fire (so the zero above is not "nothing runs")
    _shop(app, plans, email="ok@example.com", plan="month")
    assert _run(app, END - timedelta(days=5))["queued"] == 1
    assert [m.to_address for m in _mails(app)] == ["ok@example.com"]


def test_several_shops_one_customer_each_get_their_own_mail_labelled(app, plans):
    _mail_on(app)
    _shop(app, plans, plan="month", email="one@example.com", branch="Dabouq")
    _shop(app, plans, plan="month", email="two@example.com")
    assert _run(app, END - timedelta(days=5))["queued"] == 2
    by_to = {m.to_address: m for m in _mails(app)}
    assert "(Dabouq)" in by_to["one@example.com"].body_text and "(Dabouq)" not in by_to["two@example.com"].body_text


# ------------------------------------------------------------------ the mail itself

def test_the_mail_follows_the_account_language_and_is_always_both(app, plans):
    _mail_on(app)
    _shop(app, plans, status="PILOT", email="ar@example.com", language="ar")
    _shop(app, plans, status="PILOT", email="en@example.com", language="en")
    _run(app, END - timedelta(days=2))
    by_to = {m.to_address: m for m in _mails(app)}
    ar, en = by_to["ar@example.com"], by_to["en@example.com"]
    assert ar.subject == "Action Aura: تقترب نهاية تجربتك المجانية"
    assert en.subject == "Action Aura: your free trial ends soon"
    assert ar.body_text.index("تنتهي قريباً") < ar.body_text.index("ends soon")
    assert en.body_text.index("ends soon") < en.body_text.index("تنتهي قريباً")
    for mail in (ar, en):
        assert ACCOUNT_NAME in mail.body_text and IBAN in mail.body_text and PHONE in mail.body_text
        assert "info@actionaura.me" in mail.body_text, "a notice: it invites questions"
        assert 'dir="rtl"' in mail.body_html
        assert "--" not in "".join(re.findall(r"[؀-ۿ][^\n]*", mail.body_text)), "no double dash in Arabic"


def test_every_kind_has_both_languages_the_bank_details_and_fits_the_kind_column(app, plans):
    from app.mail import templates
    from app.models.email_outbox import EmailOutbox
    from app.shop_reminders import service

    width = EmailOutbox.__table__.c.kind.type.length
    for kind in service.KINDS:
        assert len(kind) <= width, kind
        assert kind in templates.NOTICE_KINDS and kind.startswith(templates.CUSTOMER_KIND_PREFIX)
        lines = service._lines(kind, shop="S", end=END, today=END - timedelta(days=3))
        for lang in ("ar", "en"):
            subject, text, _html = templates.render(kind, lang=lang, lines=lines)
            assert subject and IBAN in text and PHONE in text and "S" in text


# ------------------------------------------------------------------ dry run, email off, CLI

def test_a_dry_run_writes_nothing_and_apply_then_sends_exactly_that(app, plans):
    _mail_on(app)
    _shop(app, plans, status="PILOT")
    dry = _run(app, END - timedelta(days=2), apply=False)
    assert dry["would_queue"] == 1 and dry["queued"] == 0 and _count(app) == 0
    real = _run(app, END - timedelta(days=2))
    assert real["queued"] == 1 and _count(app) == 1
    again = _run(app, END - timedelta(days=2), apply=False)
    assert again["already_queued"] == 1 and again["would_queue"] == 0


def test_with_email_off_nothing_is_marked_sent_and_a_later_night_with_email_on_still_sends(app, plans):
    app.config.update(SMTP_HOST="")
    _shop(app, plans, status="PILOT")
    off = _run(app, END - timedelta(days=2))
    assert off["not_sent"] == 1 and off["queued"] == 0 and _count(app) == 0
    _mail_on(app)
    assert _run(app, END - timedelta(days=1))["queued"] == 1 and _count(app) == 1


def test_default_today_is_jordans_business_date(app, plans):
    _mail_on(app)
    today = jordan_today()
    _shop(app, plans, status="PILOT", end=today + timedelta(days=2))
    with app.app_context():
        from app.extensions import db_session
        from app.shop_reminders import service

        result = service.run(apply=True)
        db_session.remove()
    assert result["queued"] == 1 and _count(app) == 1


def test_the_command_is_dry_by_default_prints_no_address_and_apply_queues(app, plans):
    _mail_on(app)
    _shop(app, plans, status="PILOT", email="private.person@example.com")
    runner = app.test_cli_runner()
    args = ["notices", "shop-reminders", "--as-of", (END - timedelta(days=2)).isoformat()]
    dry = runner.invoke(args=args)
    assert dry.exit_code == 0 and "DRY RUN" in dry.output and "WOULD_QUEUE" in dry.output
    assert "private.person" not in dry.output and _count(app) == 0
    real = runner.invoke(args=args + ["--apply"])
    assert real.exit_code == 0 and "QUEUED" in real.output and "queued: 1" in real.output and _count(app) == 1
    assert "private.person" not in real.output
    assert "already queued: 1" in runner.invoke(args=args).output


def test_the_nightly_script_runs_the_command_after_the_expiry_scan_with_valid_arguments(app):
    import click

    script = Path(__file__).resolve().parents[1] / "deploy" / "oracle-free" / "run_scheduled_ops.sh"
    text = re.sub(r"\\\n\s*", " ", script.read_text(encoding="utf-8"))
    calls = re.findall(r'run_job\s+"[^"]*"\s+([^\n]+)', text)
    wanted = [c for c in calls if c.startswith("notices shop-reminders")]
    assert len(wanted) == 1, calls
    assert calls.index("commercial expiry-scan --apply") < calls.index(wanted[0]), "the scan moves PAST_DUE first"
    argv = shlex.split(wanted[0])
    command = app.cli.commands[argv[0]].commands[argv[1]]
    assert isinstance(command, click.Command)
    ctx = command.make_context(argv[1], argv[2:], resilient_parsing=False)  # raises on a typo'd option
    assert ctx.params["apply_"] is True
