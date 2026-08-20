# Wave 1 audit findings — 2026-08-20

Six parallel senior-engineer audits, read-only, each required to cite `file:line`
and label findings CONFIRMED or SUSPECTED. Paths are repo-relative.

Status key: **OPEN** · **IN PROGRESS** · **FIXED** · **ACCEPTED** (understood, deliberately not changing) · **OWNER ACTION** (not a code problem)

---

## The three root causes behind the owner's complaints

These are the answers to what was actually asked. Everything else is detail.

### A. "The AI in the phone is not working"

The Android AI assistant **has never worked**. It is a UI stub:
`android/aura-retail/.../ui/AppRoot.kt:307` dismisses the sheet and shows a
"coming soon" snackbar. Everything downstream already exists — the Flask route
`POST /api/sub/retail/ai/chat` (`products/retail/backend/api/retail_api.py:3834`),
the Chaquopy bridge, and the bearer-token plumbing
(`build.gradle:77` → `ServerBootstrap.kt:58` → `main.py:96` → `config.py:194`).
The Retrofit interface (`net/AuraApi.kt`) simply has no AI endpoint. The pipe was
built end to end except the last inch. — **IN PROGRESS**

Two follow-ons: `AURA_AI_BEARER_TOKEN` defaults to `""` unless `-PaiBearerToken`
is passed at build time, and no build script or doc passes it, so a rebuilt APK
sends `Bearer ` and gets 401 → 503 even once the UI is wired (**OPEN** — this
must be wired into the build, or every future APK ships with AI dead).

**The AI infrastructure itself is alive** — verified 2026-08-20, not assumed.
`POST https://104-248-35-215.sslip.io/api/generate` answers in ~1.5 s with
**401**, i.e. reachable and demanding auth, not down. That host is the
DigitalOcean droplet `aura-llm-demo` (8 GB / 4 vCPU, fra1), which runs
`ollama.service` behind `caddy.service`; the Caddyfile gates the Ollama
reverse-proxy (`127.0.0.1:11434`) on an `Authorization: Bearer …` match. The
required token value is recoverable from `/etc/caddy/Caddyfile` on that droplet
— it is deliberately not reproduced here.

So once the Compose sheet is wired and the APK is rebuilt passing
`-PaiBearerToken=<value from that Caddyfile>`, the assistant should work
end to end. Nothing needs to be provisioned.

Cost note, correcting an earlier recommendation: `aura-llm-demo` was previously
flagged as the obvious droplet to downsize for savings. It is not — it is the
LLM host. `phi3.5:3.8b` needs roughly 4 GB resident, so 8 GB is headroom rather
than waste; downsizing to 2 GB would kill the assistant outright, and 4 GB would
leave no margin. That droplet also, unexpectedly, runs a second
`aura-owner-deptnav.service` instance — worth reconciling, since the review
instance was believed to live only on the retail droplet.

### B. "The phone is not working properly as it should"

Largely **not a code defect**. The Owner subscription is in a terminal
CANCELLED state, which `commercial_runtime/licensing_contracts/policy_evaluator.py:124-125`
maps to `LicenseState.RESTRICTED`; the allowlist then permits only reads,
returns and customer payments. Every new sale, product create, stock adjustment,
purchase order and settings write is refused with HTTP 403. — **OWNER ACTION**:
the subscription must be reactivated.

The code defect on top of it: that 403 is disguised as a network error.
`android/.../ui/screens/RetailScreens.kt:423` wraps checkout in
`catch (e: Exception) { snackbar.showSnackbar(tr("Couldn't reach the server")) }`,
so the merchant is sent to debug their wifi when their subscription is the real
cause. The same blanket pattern repeats across nearly every screen.
— **IN PROGRESS**

### C. "Reports/charts/dashboard don't display each other's info" and "the stock is not accurate"

Three structural causes, not a collection of small bugs:

1. **There is no single revenue definition.** Roughly seven hand-written SQL
   variants exist. The "net out refunds" correction and the branch filter were
   each retrofitted onto only *some* of them — dashboard, summary and by-branch
   net out returns; sales-trend, payment-methods and top-products stay gross. So
   any refund, or any branch selection, makes screens on the same page
   contradict each other. — **OPEN**
2. **`inventory_balances` is a mutable stored column written by five independent
   paths, with nothing ever reconciling it against the `inventory_movements`
   ledger.** An unguarded purchase-order-receive race and an import path that
   overwrites stock absolutely both corrupt it silently. — **OPEN**
3. **Desktop and phone are structurally incapable of agreeing.** Sync relays
   only catalogue entities (category, product, customer, supplier,
   reorder_request); sales, returns and stock movements are never queued at all
   — and the phone additionally drops everything except categories
   (`mobile/.../sync/SyncOrchestrator.kt:235`:
   `if (ev.entityType != "category") return`, while the cursor still advances,
   so those events are skipped forever). — **OPEN**

---

## Desktop retail — reports, money and stock

| Sev | Location | Defect |
|---|---|---|
| CRITICAL | `mobile/.../sync/SyncOrchestrator.kt:235` | Phone drops every inbound event except categories; cursor advances anyway, so they are skipped permanently |
| CRITICAL | `retail_api.py:308-1333`, `sync_service.py:277` | Sales, returns and stock movements are never queued to `sync_outbox` |
| HIGH | `retail_api.py:1043-1067` | `receive_purchase_order` has no `BEGIN IMMEDIATE` and no `AND status!='received'` guard — double-click adds stock twice |
| HIGH | `import_api.py:1092-1095` | Product re-import sets `quantity_on_hand` absolutely and writes no movement row — sold units resurrect, ledger and balance diverge |
| HIGH | `retail_api.py:202-204` vs `222-237` | Dashboard `today_sales` nets refunds; the hourly chart and payment-method breakdown in the *same response* are gross |
| HIGH | `retail_api.py:2429-2435` vs `2523-2528` | Reports: trend and payment-methods never subtract returns; summary and by-branch do |
| HIGH | `subsystem-retail.js:3380-3384` vs `retail_api.py:2489,2559` | Branch selector reaches only trend and top-products; summary, payment-methods and by-branch take no `branch_id` |
| MEDIUM | `retail_api.py:2461-2476` | `top-products` has no date filter (all-time) while every sibling widget is `?days=`-scoped |
| MEDIUM | `retail_api.py:3186-3190` vs `2154-2158` | Daily-cash reads the payments ledger, but returns never write a payment row; Z-report subtracts them directly |
| MEDIUM | `retail_api.py:2465-2466,2531-2535` | Profit/COGS use *current* `cost_price`, not cost at time of sale — past months' margins change retroactively |
| MEDIUM | `retail_api.py:560-568` | `adjust_stock` always targets the first branch by id, never the branch in view; no product-existence check; can go negative |
| MEDIUM | `retail_api.py:2433` vs `2552` | Two different definitions of "average ticket" on one page |
| MEDIUM | `schema.py:1483-1490,1403` | Money and quantity columns are REAL (float); server computes `Decimal` then stores float; phone stores money as TEXT decimal |
| LOW | `retail_api.py:1373` | Sales idempotency lookup is not company-scoped (the returns twin at `:1932` is) |
| LOW | `retail_api.py:3241-3247` | Voiding a walk-in cash receipt reverses nothing on the sale or stock |
| LOW | `retail_api.py:2083-2088` | `create_return` success path never closes the connection — SUSPECTED contributor to intermittent `SQLITE_BUSY` |

## Multi-device sync

The transport layer is sound — signed replay-proof relay, seq-ordered append-only
log, advisory-lock commit ordering, per-id idempotent push, all-or-nothing pull
apply. `/push` idempotency on retry-after-commit was specifically verified
**correct** on both platforms. The `pg_advisory_xact_lock` fix is correctly
placed and nothing found undermines it.

The **replication layer above it** is what needs work:

| Sev | Location | Defect |
|---|---|---|
| CRITICAL | `sync_service.py:287-352` | No conflict resolution of any kind — not even last-write-wins. Blind full-row upsert. Two devices editing the same row diverge permanently and silently |
| CRITICAL | `import_api.py:1051,1077,1125,1144` | Bulk import never enqueues sync events — a 500-item catalogue import reaches no other device, with no error |
| CRITICAL | `retail_api.py:514-515` | Update events carry a full-row snapshot, so a one-field edit clobbers every other field fleet-wide |
| HIGH | `sync_service.py:293,326,340,354` | No tombstones: a concurrent update resurrects a deleted row on every device |
| HIGH | `sync_service.py:196`, `owner/app/sync/routes.py:57,324` | Client pushes the entire outbox unchunked against a server cap of 200 — a device with >200 offline edits can never push again |
| HIGH | `sync_service.py:196`, `retail_api.py:81` | Outbox ordered by `created_at` (local-clock text, no tiebreaker); a clock step-back inverts parent→child, the FK apply raises, and every device then re-pulls the same failing batch forever |
| MEDIUM | `owner/app/sync/routes.py:392` | `device_id != installation.id` makes a device's own history unrecoverable after a restore-from-backup |
| MEDIUM | `owner/app/sync/routes.py:381-385,411` | A future or corrupt cursor is accepted and echoed back, never validated against `max(seq)` |
| MEDIUM | `relay_client.py:83,151` | Device wall clock is trusted for the auth timestamp; outside the skew window the device silently stops syncing |
| LOW | `owner/app/sync/routes.py:205` | Idempotency fast-path is a global-PK get, not scoped to `license_id` |

Deliberate, not a bug: only category/product/customer/supplier/reorder_request
sync. Sales, inventory balances, purchase orders and cash sessions are per-device
**by design** — but that design is exactly why the owner sees stock and reports
that never match between devices, so it needs a product decision, not just a fix.

## Android app

| Sev | Location | Defect |
|---|---|---|
| CRITICAL | `ui/AppRoot.kt:307` | AI send button is a stub |
| CRITICAL | `net/AuraApi.kt:11-214` | No AI endpoint in the Retrofit interface |
| HIGH | `RetailScreens.kt:423` | Licensing 403 shown as "Couldn't reach the server" |
| MEDIUM | `server/ServerBootstrap.kt:61` | `wait_until_ready` result discarded; `AppRoot.kt:103` then swallows any Chaquopy exception, stranding the user on a login that cannot succeed, with no diagnostics |
| MEDIUM | `build.gradle:77-78` | `AURA_AI_BEARER_TOKEN` silently defaults to empty |
| MEDIUM | `sync/SyncCoordinator.kt:166-170` | Sync health is logcat-only; a merchant cannot see that their data is not reaching the relay |
| LOW | `ui/screens/SettingsScreen.kt` | Entire screen is dead code, never registered in the nav graph; contains stubs and a fake version string "2.5.0-beta.1" (real: 1.0.0-rc.5) |
| LOW | `net/AuraApi.kt:27-74` | ~20 `api/sub/clinic/*` endpoints declared against a retail-only backend |

Verified **not** broken: INTERNET permission present; cleartext correctly scoped
to loopback only; no main-thread I/O (suspend + `Dispatchers.IO` throughout);
lists are `LazyColumn`; `!!` uses are null-guarded; every `navigate()` target
exists; offline licensing degrades to a grace period rather than hard-blocking.

## Licensing and first-run / login scenarios

Verified **correct** first, so later work does not re-open them: the activation
response signature *is* independently verified client-side
(`assertion_verifier.py:173`, enforced at `activation.py:154-169` — "never
activate on an unverifiable response"). No secrets are committed
(`trust_anchor.json` holds only a public key; `secret.key` is per-install and
gitignored; Owner refuses to boot without real env secrets). Offline grace
degrades properly (WARNING → GRACE → RESTRICTED, data preserved). Desktop
session expiry mid-use is clean — a global 401 guard raises a re-login modal.

| Sev | Location | Defect |
|---|---|---|
| CRITICAL | `owner/app/licensing_service/signing.py:228-234` + `trust_store.py:82-83` | **Signing-key rotation can never propagate.** The keyset manifest is signed by the *currently active* key, but `admit_manifest()` only accepts manifests signed by an *already-trusted* key. After a rotation no fielded client trusts the new signer, so every activation and check-in fails `UNKNOWN_SIGNING_KEY` permanently — while Owner still marks the install ACTIVE and burns a paid slot. Only a new app build recovers it. A stale bundled `trust_anchor.json` has already been hit on the live droplet |
| CRITICAL | `android/.../LicensingScreen.kt:158` | PENDING tells the user "We'll keep checking automatically — no action needed", but **nothing on Android polls**. Re-entering the key loops 202 forever |
| HIGH | `android/.../LicensingScreen.kt:33-47,161` | `REASON_MESSAGES` omits `UNKNOWN_SIGNING_KEY`, `ASSERTION_*`, `INSTALLATION_DEACTIVATED`, `INSTALLATION_REPLACED`; all fall back to "Double-check the key and try again" — the exact wrong advice, and retyping reproduces it forever |
| HIGH | `activation.py:240-242`, `deactivation.py:65-71`, `licensing_admin/routes.py:101-116` | A wiped or dead device's slot stays consumed, because self-deactivation requires the *old* device's private key. Reinstall-after-wipe hits `DEVICE_LIMIT_REACHED` and recovery **always** needs an admin |
| HIGH | `checkin_scheduler.py:98-100`, `licensing.js:786` | A *rejected* check-in (suspended/revoked/expired) is indistinguishable from "no envelope", so a suspended customer is told "Could not reach the licensing service. Your current status is unchanged." The service *was* reached and said no |
| MEDIUM | `licensing.js:116-118` | `TIMESTAMP_OUTSIDE_ALLOWED_WINDOW`, `NONCE_REUSED`, `DEVICE_ALREADY_REGISTERED`, `DEVICE_KEY_MISMATCH` and others are unmapped → generic "Double-check the key". A client clock more than 5 minutes off is told its key is wrong; nothing ever says "fix your clock" |
| MEDIUM | `app-shell.js:981` | The **primary** first-run registration modal falls back to raw `a.detail`, which is always the fixed string "Owner rejected the activation request." — so first-run loses every reason-specific message `licensing.js` already has |
| MEDIUM | `LicensingCoordinator.kt` / `AppRoot.kt` | No background check-in on Android (desktop polls every 5 min). Revocation never lands mid-use; weeks later one manual "Check Now" can drop the user straight to RESTRICTED with no warning phase |
| MEDIUM | `reason_codes.py:102` | Deliberate anti-enumeration collapses `LICENSE_EXPIRED` / `SUSPENDED` / `NOT_ISSUED` into `ACTIVATION_REJECTED`, so a genuinely expired paying customer is told to check for a typo and has no way to learn that renewal is the fix. Security-motivated, but it costs a real customer a support call — worth an explicit product decision |
| LOW | `licensing.js:750-759` | `CLOCK_REVIEW_REQUIRED` and `LOCAL_STATE_CORRUPT` get a badge but no guidance paragraph; fixing the clock does self-heal, but nothing tells the user that |

**The three worst first-run experiences**, in order: (1) a stale or rotated
signing key is unrecoverable without a new installer, while still consuming a
paid slot; (2) the Android manual-approval loop promises automatic polling that
does not exist; (3) at the single moment a new customer first fails — clock
skew, an already-registered device, an expired licence — the product gives its
least accurate advice.

## Release packaging (found by the lead, not an auditor)

The APK currently published for testing is a **debug** build
(`app-debug.apk`, `applicationIdSuffix ".debug"`, `debuggable true`). Release
signing is fully configured in `android/aura-retail/app/build.gradle:131-170`
but is inert because `keystore.properties` does not exist — so
`signingConfig` is left null and no release-signed artifact can be produced.
A signed release build is a launch blocker. Version is `versionCode 6`,
`versionName 1.0.0-rc.5`; ABIs are `arm64-v8a` and `x86_64`.
— **OPEN**, needs a keystore from the owner.

## Test baseline (measured, 2026-08-20)

`python products/run_all_tests.py` on the production lineage — one process per
test file: **99 files run, 98 passed, 1 failed.**

The single failure is not flaky, it is a genuine authorization hole:

```
products/retail/tests/retail_audit_log_test.py::test_audit_log_403s_for_a_non_admin_device
assert 200 == 403
```

A non-admin device can read the audit log; the route answers `200` with data
where the test correctly demands `403`. This lines up with a known gap that
nothing in the codebase ever sets `is_admin_device`, which also leaves the
desktop Settings screen permanently hidden — one unset flag with two visible
consequences, one of them a security problem. — **OPEN**

For contrast, the Owner CC suite has a much worse standing baseline: 25 failures
out of 1100 at last full run, the large majority pre-existing
(`DetachedInstanceError` in fixtures, CSRF-in-test issues) rather than product
defects. That suite takes over two hours, so it is not part of the fast loop.

## Design

Three unrelated brand identities ship today:

- **Owner web** — a genuinely strong token system (`static/css/tokens.css`:
  full light/dark, RTL logical properties, reduced motion) applied in one flat
  visual register. The dashboard reads as ~10 undifferentiated bordered boxes;
  only the KPI hero band has any depth. Login is an unbranded bare form. No
  loading or skeleton styles exist anywhere in any stylesheet.
- **Desktop** — the inverse: overdesigned chrome, underdesigned product. A
  neon/HUD splash (hex grid, corner brackets, orange→magenta) that is alien to a
  business suite, while the actual working screens live as HTML strings inside a
  206 KB JavaScript file with CSS embedded in JS and ~60 inline hardcoded hex
  colours.
- **Android** — best fundamentals (real shimmer skeletons, a proper empty
  state), but `AuraTheme` ignores its own `darkTheme` parameter and is dark-only,
  colour constants are misnamed (`AuroraTeal` is actually rose), and
  `LicensingScreen.kt:264-265` hardcodes light-theme chip colours inside the
  dark theme.

Proposed direction — **"Operational Calm"** (precision-enterprise register).
Rationale: eight-hour daily use argues for low-chroma surfaces with chroma
reserved strictly for status meaning. Keep `tokens.css` as the single source of
truth and extend it to desktop and Android rather than inventing a parallel
system; promote the existing bundled Plus Jakarta Sans to a display face for
titles and KPI numerals; borders-first depth with shadows only on overlays and
interactive raise; motion limited to the existing 90–240 ms transform/opacity
tokens.

Accessibility debt is concentrated on the desktop app: twelve `outline:none`
rules against four `:focus-visible`, three ARIA attributes in the entire
206 KB screen file, and an RTL stylesheet fighting inline styles with
`!important`. Owner web is the strongest of the three (skip link,
`:focus-visible`, `<bdi>` isolation).
