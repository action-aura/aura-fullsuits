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

Wave 1 audit findings are recorded in `findings.md` in this directory, each with
a `file:line` citation and a CONFIRMED/SUSPECTED label.

## Wave 2 — the fix order, and why

Ranked by *what actually blocks a launch*, not by how interesting the bug is.

**Tier 0 — blocks launch outright**

1. **Signing-key rotation is unrecoverable.** `signing.py:228` + `trust_store.py:82`.
   Until this is fixed, rotating a key bricks every fielded client permanently
   and there is no recovery except shipping a new installer. This outranks
   everything else because it is unfixable *after* it happens.
2. **Release-signed build.** The published APK is a debug build; release signing
   is configured but inert because `keystore.properties` does not exist. Needs a
   keystore from the owner — nothing else can proceed without it.
3. **Audit log authorization hole.** A non-admin device reads the audit log
   (`200` where the suite demands `403`). One unset `is_admin_device` flag.
4. **Reactivate the Owner subscription.** Not code. Until it is done, every write
   on every device is 403-blocked and the product looks broken to any tester.
5. **Finish the CSP migration in Owner CC.** Under `script-src 'self'` inline
   handlers never run, and the leftovers fail *silently*: "Void expense" now
   submits with **no confirmation dialog at all**, add-on availability cannot be
   changed from the UI, and status filters on six list screens are dead. The
   correct pattern (`data-confirm` + `static/js/confirm.js`) already exists in
   this repo and is used by 10+ templates — this is finishing a job, not
   inventing one. The missing confirmation is a data-loss risk, which is why it
   sits in Tier 0 rather than with the other UI work.

**Tier 1 — the owner's stated complaints**

5. Wire the Android AI sheet to the existing backend route. *(in progress)*
6. Stop disguising licensing 403s as network errors. *(in progress)*
7. Outbox chunking, import sync-enqueue, deterministic push ordering. *(in progress)*
8. **One revenue definition.** Replace ~7 hand-written SQL variants with a single
   shared metric service that every screen calls, so a refund or a branch filter
   cannot make two widgets on one page disagree. This is the fix for "reports
   don't display each other's info" and it is a refactor, not a patch.
9. **Reconcile `inventory_balances` against `inventory_movements`**, and close the
   two paths that corrupt it: the unguarded purchase-order-receive race, and the
   importer's absolute stock overwrite.

**Tier 2 — product decisions, not just code**

10. **Decide what multi-device actually means.** Today sales, stock, purchase
    orders and cash sessions are per-device *by design*, so two devices can never
    show the same numbers. Either that is the product (and the UI must say so
    plainly, per device), or sync scope has to widen. The owner should decide;
    the code follows.
11. Conflict resolution and tombstones in the replication layer — currently there
    is no rule at all, so concurrent edits diverge silently and deletes resurrect.
12. Anti-enumeration vs. customer support: an expired paying customer is currently
    told to check for a typo. Security-motivated, but it costs a support call.

**Tier 3 — design**

13. Adopt "Operational Calm" and extend `tokens.css` to desktop and Android, so
    the suite stops shipping three unrelated brand identities. Highest single
    perception win is replacing the desktop neon/HUD identity.

## Working agreement for agents on this programme

- Production lineage is `feat/retail-mobile-build-baseline`. Never deploy
  `origin/master` — it has no `owner/app/sync/`.
- Fixes land on `feat/launch-readiness`, are committed by the lead after review,
  and every behavioural fix ships with a test that fails before and passes after.
- A finding is not "fixed" until someone has watched the test go from red to
  green. Do not weaken an assertion to make a suite pass.

## Current deployment state

| Thing | Where | Notes |
|---|---|---|
| Owner CC (live) | https://owner.actionaura.me | `/opt/aura-owner`, service `aura-owner`, gunicorn on 127.0.0.1:5551 |
| Owner CC (review) | https://deptnav-161-35-219-243.sslip.io | `/opt/aura-owner-deptnav`, separate DB — still on the OLD UI lineage, and its DB is on a different Alembic graph than production |
| Rollback of last deploy | `/opt/aura-owner.bak-20260820` on the droplet | |
| Android APK | https://owner.actionaura.me/downloads/AuraRetail.apk | |
| Windows portable | https://owner.actionaura.me/downloads/AuraRetail-Windows-Portable.zip | portable ZIP, not an installer — Inno Setup is not installed on the build machine |
