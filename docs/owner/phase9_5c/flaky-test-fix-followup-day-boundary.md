# Phase 9.5C — Real Flake Found and Fixed: Follow-up Day-Boundary Test

## What happened

`test_due_today_and_overdue_queries_scoped_to_actor`
(`tests/test_phase9_5c_engagement.py`) failed once, in the final 672-test
full-suite run, at real UTC time `22:08:46` — the test created a
follow-up due at `utcnow() + timedelta(hours=2)`, which lands *after*
UTC midnight (`~00:08` the next day). `list_own_lead_followups_due_
today()`'s window is `[today's midnight, tomorrow's midnight)` (a real,
documented UTC-calendar-day simplification — see `follow-up-timezone-
rules.md`), so a followup due 2 hours from a run that happens to start
within 2 hours of UTC midnight silently falls **outside** "today."

## Root cause, not a random flake

Confirmed deterministically: rerunning the exact same test at the exact
same real time (`22:08` UTC) reproduced the failure on demand, every
time — this was not a race condition or nondeterministic scheduling
issue, it was a **wall-clock-relative test fixture value that becomes
wrong for roughly 2 hours out of every 24**, a real, root-causable bug
in the test's own construction, not the service code
(`list_own_lead_followups_due_today()`'s logic is correct — it does
exactly what "due today" is documented to mean).

## Fix (no flake hiding — assertions unchanged, root cause addressed)

Replaced the wall-clock-relative `utcnow() + timedelta(hours=2)` with a
value computed to be simultaneously (a) within today's UTC calendar-day
window and (b) still in the future relative to "now," regardless of what
real time the test executes:

```python
now = utcnow()
day_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
due_today_at = min(now + timedelta(hours=1), day_end - timedelta(minutes=1))
```

Verified: the fixed test passes at the exact real time (`22:08 UTC`)
that previously reproduced the failure on demand, without touching
`app/leads/engagement.py`'s actual due-today/overdue query logic, and
without weakening, skipping, or removing any assertion.
