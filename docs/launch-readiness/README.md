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

## Session log — 2026-08-20, paused 03:30 at the owner's request

**Landed and pushed on `feat/launch-readiness`:**

- Wave 1 discovery complete — six parallel audits, every finding carrying a
  `file:line` citation and a CONFIRMED/SUSPECTED label (`findings.md`).
- Measured test baseline: 99 files, 98 passed, 1 failed.
- **Sync fixes** (`13c1c92`): outbox chunking so a device with >200 offline
  edits can no longer wedge permanently; bulk import now enqueues sync events
  so an imported catalogue actually reaches other devices; push ordering moved
  off local wall-clock to insertion sequence so a clock step-back can no longer
  invert parent→child and freeze the whole fleet's cursor. 9 new tests, each
  seen RED before and GREEN after. Retail suite: 45 files, 45 passed.
- **Android fixes** (`3b650c3`): the AI assistant is wired to its backend route
  for the first time; licensing 403s no longer masquerade as network errors.
  Compiles — a fresh `app-debug.apk` was produced.

**Next session should start here**, in this order:

1. Get a **keystore** from the owner and produce a release-signed APK. Nothing
   about distribution is real until this exists.
2. Fix **signing-key rotation** (`signing.py:228` + `trust_store.py:82`) — the
   only finding that becomes unfixable after it happens.
3. Finish the **CSP migration**, starting with the missing void-expense
   confirmation.
4. Wire `-PaiBearerToken` into the build, then verify the assistant on a real
   handset end to end.
5. Begin the **single metric service** — it is the one change that resolves
   both "reports don't match" complaints, on Owner web and on desktop, and it
   is a refactor rather than a patch.

**Two decisions only the owner can make**, both already blocking work:

- Reactivate the cancelled subscription, or every write stays 403-blocked and
  the product looks broken to any tester.
- Decide what multi-device means: sales, stock, purchase orders and cash
  sessions are per-device *by design* today, so two devices are structurally
  incapable of showing the same numbers.

**Left deliberately untouched:** two worktrees still hold uncommitted work —
`feat/pos-hold-resume-sale` and `feat/retail-ui-ux-polish`. They were skipped
during disk cleanup rather than risk losing whatever is in them.

## Android release signing — set up 2026-08-20

Release signing was configured in `build.gradle:131-170` but **inert**, because
`keystore.properties` never existed, so `signingConfig` stayed null and no
release-signed artifact could be produced. Every APK shipped so far has been a
debug build. That is now resolved.

| | |
|---|---|
| Keystore | `C:\Users\MSI\.aura-signing\aura-release.jks` — deliberately **outside** the repository |
| Credentials | `C:\Users\MSI\.aura-signing\CREDENTIALS.txt` |
| Alias | `aura` |
| Key | RSA 4096, valid until 2054-01-05 |
| SHA-256 | `6F:E5:64:A1:D6:4F:CD:B1:E6:4F:23:56:BF:DD:9B:9B:6B:78:C4:00:15:3A:BA:AE:10:9D:81:6B:AB:EB:66:9A` |

The fingerprint above is public — it is what you register with Play and what you
compare a shipped APK against. The password is not recorded here, was never
passed on a command line, and exists only in `CREDENTIALS.txt`.
`keystore.properties` was written into both checkouts that build the app; both
it and `*.jks` are already covered by `.gitignore`.

RSA 4096 rather than the template's 2048: this key has a ~27-year validity and
signs a commercial product, and the extra cost is milliseconds per build.

> **This must be backed up off this machine.** Android only permits an update
> when it is signed with the same key. If the keystore or its password is lost,
> the app cannot be updated — only republished as a new application, which
> orphans every existing install's data and forces every user to reinstall by
> hand. This is the single least recoverable asset in the project.

## Wave 2 — results

Seven fixes, each reviewed by an independent skeptic instructed to refute rather
than approve. **Four passed review and are committed. Three were rejected** and
sent back. That ratio is the point of the exercise: every rejection was a real
defect that would otherwise have shipped.

### Committed

| Area | Commit | What it fixes |
|---|---|---|
| Signing-key rotation | `5e2983f` | Rotation now propagates via outgoing-key countersigning. This was the only defect that becomes unfixable *after* it happens |
| Owner CSP migration | `9735ee8` | 17 dead inline handlers removed; "Void expense" has its confirmation back; six status filters work again |
| Android licensing | `0353912` | Real activation polling, honest reason-code messages, periodic check-in, and a build that refuses to ship a tokenless assistant |
| Device identity | `8c82c51` | `is_admin_device` finally has a legitimate way to be set |

### Rejected by review, remediation in progress

| Area | Verdict | The objection |
|---|---|---|
| Stock import semantics | **BLOCKING** | The absolute-SET → delta change lets a downward re-import drive on-hand **negative** (reproduced end to end), and the column is still labelled "Current Stock Qty" to the operator while the backend now treats it as a cumulative opening declaration |
| Desktop revenue | **major** | `top_products` subtracts a tax-**inclusive** refund from a tax-**exclusive** sales base. Worse, the test that supposedly proves agreement passes only because its fixture seeds `tax_rate=0` — a test that certifies the bug as fixed |
| Owner metrics | **major** | Currency-scoping the duplicate-review queue silently **drops** cross-currency duplicate pairs — a fraud control failing invisibly. And the employee commission screen is now a single unlabelled number with a hidden currency, arguably worse than the visibly-wrong blended figure it replaced |

The consolidation and contract work underneath the two "major" rejections was
confirmed genuine — the seven duplicate SQL variants really are gone, and no
test assertion was weakened. The rejections are about defects the rewrites
*introduced*, not about the direction.

### Two follow-ups on committed work, also being fixed

- An Android **release build with an empty `ownerLicensingBaseUrl` enforces no
  licensing at all** — the activation gate is skipped and check-in returns
  immediately. The new build guard covers only the AI token, so this ships an
  APK that gives the product away.
- The rotation fix was applied to `/_internal/sync-activation` but not its twin
  `/_internal/sync-checkin`, so Android still cannot recover from a rotation
  that happens *after* activation.

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
