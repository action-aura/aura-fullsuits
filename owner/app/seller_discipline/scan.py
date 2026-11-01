"""The periodic pass for sanctions and terminations.

What is due without anyone pressing a button:
  * a SUSPENSION whose end date has passed re-opens the seller's account;
  * a 7-day notice whose day has come takes effect (account stopped, customers settled);
  * a former seller's trial credit whose trial is over is released (the customer returns).

WHO CALLS IT, in three independent ways, so a seller never stays locked out or on past their last day
because one of them is not running:
  1. the mail worker's tick (app/mail/worker.py) -- only exists on a server with email set up;
  2. `flask seller-discipline scan` -- for any scheduler (cron, a systemd timer, the platform's job runner);
  3. settle_due_for_staff(), called from the sign-in itself (auth/services.authenticate): the person
     signing in settles THEIR OWN due items first, so a suspension that has ended lets them in, and a
     notice that has run out refuses them, even if nothing else ever ran.
Every step is idempotent and row-locked: calling it twice, or racing it, changes nothing the second time.
"""
from __future__ import annotations

import logging
from datetime import datetime

from app.models.base import utcnow
from app.seller_discipline import sanctions, terminations

log = logging.getLogger(__name__)


def run_due(*, now: datetime | None = None) -> dict:
    now = now or utcnow()
    suspensions_ended = sanctions.end_due_suspensions(now=now)
    applied = sum(1 for tid in terminations.due_termination_ids(now) if terminations.apply_termination(tid, now=now))
    released = terminations.release_finished_trial_credits(now=now)
    result = {"suspensions_ended": suspensions_ended, "terminations_applied": applied, "trial_credits_released": released}
    if any(result.values()):
        log.info("seller discipline scan: %s", result)
    return result


def settle_due_for_staff(staff_user_id, *, now: datetime | None = None) -> None:
    now = now or utcnow()
    sanctions.end_due_suspensions(now=now, seller_staff_user_id=staff_user_id)
    for tid in terminations.due_termination_ids(now, staff_user_id):
        terminations.apply_termination(tid, now=now)
