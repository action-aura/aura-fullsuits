# Phase 9.5C — Milestone 10: Follow-Up Timezone Rules

## Storage: UTC, timezone-aware, unchanged

`LeadFollowup.due_at`/`CustomerFollowup.due_at` (and `occurred_at` on the
interaction tables) are `DateTime(timezone=True)` columns — Postgres
stores these as `timestamptz` (UTC internally regardless of session
timezone), and every comparison in `app.leads.engagement` uses
`app.models.base.utcnow()` (already the project-wide convention for
"now," confirmed used consistently across `commercial_ops`,
`licensing_service`, and every other domain module). No new timezone
handling was introduced — this milestone reuses the existing pattern
exactly.

## Display: Asia/Amman for business-day framing

"Due today" / "overdue" in the **service layer**
(`list_own_lead_followups_due_today()`) currently computes a UTC
calendar-day window (`as_of.replace(hour=0, ...)` through `+1 day`) using
whatever `as_of` the caller passes (defaulting to `utcnow()`). This is a
real, documented simplification: a UTC calendar day and an Amman
(`UTC+3`, no DST) calendar day diverge by a fixed 3-hour offset, so a
follow-up due at 22:00 Amman time / 19:00 UTC could show as "due
tomorrow" in a naive UTC-day query run before 21:00 UTC.

**Decision for this milestone**: the service functions accept an
explicit `as_of` parameter precisely so the *caller* (a future
Milestone 16/17 route or dashboard) can pass `datetime.now(ZoneInfo(
"Asia/Amman"))`-derived day boundaries converted back to UTC instants,
rather than the service silently hardcoding one timezone. This keeps the
service layer itself business-timezone-agnostic (consistent with
Non-Negotiable Rule 10 — no hidden locale/timezone assumption baked into
domain code) while giving the presentation layer everything it needs to
render correctly for Amman-based staff. The route layer wiring itself
(computing the real Amman day boundary and passing it as `as_of`) is
Milestone 16/17 work, not yet built — tracked, not silently deferred.
