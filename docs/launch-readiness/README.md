# Aura FullSuits — launch-readiness programme

Working branch: `feat/launch-readiness` (off `deploy/owner-ui-on-prod-lineage`,
which is itself the merge of the dept-nav UI into the production lineage
`feat/retail-mobile-build-baseline`).

## Scope, as set by the product owner

Everything ships launch-ready: Owner Control Center (web), Aura Retail desktop
(Windows), Aura Retail Android. Fix and enhance whatever needs it — UI, UX,
function, logic, or whole user scenarios. Redesign is explicitly authorised,
including removing existing UI.

**Out of scope (owner's explicit instruction, 2026-08-20): `products/clinic`.**
Do not spend effort there.

## Reported symptoms to chase to root cause

| # | Symptom (owner's words) | Surface |
|---|---|---|
| 1 | "the AI in the phone is not working" | Android |
| 2 | "the phone is not working properly as it should" | Android |
| 3 | "reports or charts or dashboard … they doesn't display eachothers inf" | Desktop + Owner web |
| 4 | "the stock is not accurate" | Desktop retail |
| 5 | "the license or the login scenario when installing new or logging in" | All surfaces |
| 6 | "it looks not that good it needs your touch as a designer" | All surfaces |

## Programme structure

- **Wave 1 — Discovery (audit).** Six parallel senior-engineer audits, read-only,
  each returning verified defects with `file:line` evidence and a CONFIRMED /
  SUSPECTED label. Areas: Android app + AI assistant; desktop reports + stock;
  licensing and login scenarios; Owner CC data consistency; UI/UX design; sync
  and multi-device data integrity.
- **Wave 2 — Fix.** Ranked by user impact. Correctness before cosmetics.
- **Wave 3 — Redesign.** Applies the agreed visual direction across surfaces.
- **Wave 4 — Verify and ship.** Tests, real-device checks, deploy.

## Load-bearing things that must not be broken

These were established at real cost and have to survive every future change:

- `owner/app/sync/routes.py` — `pg_advisory_xact_lock(hashtext(:license_id))`
  closes a sequence-visibility race. Do not remove or weaken it.
- `owner/app/security/headers.py` — `Referrer-Policy: same-origin`, NOT
  `no-referrer`. `no-referrer` suppressed the Referer on the app's own
  same-origin POSTs, which `WTF_CSRF_SSL_STRICT` requires under HTTPS; the
  result was a total login outage with no way to succeed.
- `owner/app/templates/licensing/detail.html` — the `recent_auth_ok` gate on
  license issuance.
- Owner CC ships under CSP `script-src 'self'`. **No inline JavaScript, ever** —
  it silently does not execute.
- `products/retail` uses raw `sqlite3` with `PRAGMA user_version` migrations.
  There is deliberately no ORM and no Alembic on that side.
- Production runs the `feat/retail-mobile-build-baseline` lineage.
  `origin/master` has **no** `owner/app/sync/` — never deploy master to
  production; it would break device activation.

## Findings

Wave 1 audit findings are recorded in `findings.md` in this directory as they
land, with the fix status of each.

## Current deployment state

| Thing | Where | Notes |
|---|---|---|
| Owner CC (live) | https://owner.actionaura.me | `/opt/aura-owner`, service `aura-owner`, gunicorn on 127.0.0.1:5551 |
| Owner CC (review) | https://deptnav-161-35-219-243.sslip.io | `/opt/aura-owner-deptnav`, separate DB — still on the OLD UI lineage, and its DB is on a different Alembic graph than production |
| Rollback of last deploy | `/opt/aura-owner.bak-20260820` on the droplet | |
| Android APK | https://owner.actionaura.me/downloads/AuraRetail.apk | |
| Windows portable | https://owner.actionaura.me/downloads/AuraRetail-Windows-Portable.zip | portable ZIP, not an installer — Inno Setup is not installed on the build machine |
