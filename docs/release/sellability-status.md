# Is Aura Retail sellable? — status, with how each line was checked

Written 2026-09-02. **Every line says how it was verified.** Where something
was not run, it says so rather than inheriting confidence from a green test
suite — this cycle produced a till that displayed US dollars in a dinar shop
while 43 JS suites, 113 Python files and 238 Android tests were all passing,
so "the tests pass" is not evidence that a thing works.

Legend:

- **RUN** — the real artefact was executed and the result observed.
- **TESTED** — covered by a suite that was executed, but the artefact itself
  was not driven end to end.
- **UNVERIFIED** — not checked this cycle. Not the same as broken.
- **BLOCKED** — needs the owner, or hardware/credentials not present here.

---

## The money path

| Item | State | How |
|---|---|---|
| Server computes every persisted total | **TESTED** | `retail_pricing_test.py`; `create_sale` ignores any client-submitted total (AUDIT-002/003) |
| Desktop till preview matches server pricing | **TESTED** | `retail_pricing_parity_test.py` — 17 scenarios through BOTH implementations, 4 mutations caught |
| Jordanian dinar renders at 3 decimals | **RUN** | Driven at 390/768/1440; POS totals, chart axes and cash-tendered step all measured after the dollar bug |
| Cash drawer persists fils, not cents | **TESTED** | 2026-09-03. `_money()` was hardcoded to 2dp, so a JOD drawer opened with 12.345 stored 12.35 while the sale total kept its fils. Fixed for opening float, counted float, variance, and the five figures inside `_cash_session_report` — expected cash had been rounded coarser than the counted float it is subtracted from, which invents a variance out of nothing. Mutation-proved both directions |
| A default install gets JOD precision | **TESTED** | 2026-09-03. The first fix only worked for shops that had EXPLICITLY chosen a currency; `_settings()` answers 'JOD' from its defaults dict while a raw SELECT answered None, and None meant 2dp. The test that missed it wrote a currency row in every fixture — the state in which both readers agree |
| Payments outside the drawer | **TESTED — complete 2026-09-05** | This row said "~34 call sites still 2dp" for two days after ROADMAP recorded that the 2026-09-03 waves had converted nearly all of them. A precise scan on 2026-09-05 found the real leftovers: `total_receivable`, `total_payable`, daily-cash in/out/net, the aging buckets, and `_export_money` behind the accounting CSVs — the summary figures a shop reads to know what it is owed. All now pass the company currency; `retail_report_totals_fils_test.py` seeds 12.345 and asserts 12.345 (a 2dp regression answers 12.35 — proven by mutating two sites). Zero currency-blind `_money()` calls remain |
| Both notification channels report the same figure | **TESTED** | 2026-09-03. WhatsApp hardcoded `:.2f` while email was currency-aware, so one drawer read `JD 12.350` by email and `12.35` by WhatsApp. Now share `core/retail/money_format.py`. The parity test's first version was a false green and was tightened, not accepted |
| A till close reaches the owner by email | **RUN — WORKS** | 2026-09-03. Driven on the live rehearsal install against a local SMTP catcher, so this is the delivered message, not an outbox row. Opened a drawer at 12.345, closed it at 12.340, and the worker drained it on its own timer with no prompting. Caught verbatim: `Expected cash: JD 12.345 / Counted: JD 12.340 / Variance: JD -0.005` followed by "This drawer did NOT balance." One message validates four separate pieces of this cycle at once — the automatic trigger (email previously had only the low-stock one), currency-aware formatting (**JD**, not `$`), fils precision end to end, and the outbox worker. **A five-fils discrepancy is now visible; under the old 2dp code it was not.** |
| ~~Phone till QUOTES a different number than it CHARGES~~ | **CLOSED — label made honest** | The Android preview is still `sell_price × qty` (no tax, discount or promotions — the server applies all three), but it no longer CLAIMS to be the total: it reads "Subtotal" with "Tax and discounts are applied at checkout", pinned by `CartTotalHonestyContractTest`. Fixed by making the label truthful rather than by adding a second pricing engine — that drift is what AUDIT-002 already cost once. The phone still cannot show the final figure before charging: a real limitation, now an admitted one. **This row said DEFECT for a day after the fix landed — re-measured 2026-09-04.** |
| Promotions resolve identically on both clients | **TESTED** | The parity test above. Android is NOT covered — it has no promotion logic at all |

## Licensing and the commercial model

| Item | State | How |
|---|---|---|
| Device limit enforced | **TESTED** | Server-side, `owner/tests/test_commercial_ops_add_devices.py`, `DEVICE_LIMIT_REACHED` |
| Unlicensed install is read-only | **TESTED** | `test_pre_activation_states_deny_new_mutation`; documented in CLAUDE.md |
| ~~`POST /api/admin/employees` has NO licence guard~~ | **STALE — the guard exists, RUN 2026-09-05** | This line said "zero `require_license_capability` in `onboarding_routes.py`". There are nine now, `create_employee` included. Measured on a fresh, unactivated third till: creating a cashier answered `403 {"reason_code": "LICENSE_INACTIVE"}`; after activation the same call succeeds. Staff cannot be added on an install that has no licence |
| `EXTRA_DEVICE` add-on is sellable | **RUN — NOT READY** | Exists in the catalog but sits at `PLANNED`, not `AVAILABLE`. This is the 50 JOD one-time fee |
| Licence activation end to end, on a clean machine | **UNVERIFIED** | Needs the clean-machine rehearsal in `go-live-runbook.md` Part 2 |

## Sync (multi-device)

| Item | State | How |
|---|---|---|
| Outbox, cursors, quarantine, pruning | **TESTED** | Owner suite; `owner/app/sync/pruning.py` has its own tests |
| `flask sync prune-events` scheduled | **BLOCKED** | Code and tests exist. Nothing schedules it. Needs a systemd timer on the droplet |
| Two real devices converging on one shop | **RUN — WORKS: accounts, customers, catalogue, stock and sales** | 2026-09-05, a real Mi Note 10 and a desktop till on one licence, every step through the real HTTP API of the real artefact: a cashier created on the desktop signed in on the phone; a customer created on each device reached the other; a product created on the desktop with stock 10 appeared on the phone with the **same UUID** and stock 10; that cashier opened a drawer and rang a sale of 2 **on the phone** (`SALE-000004-69e67d3f-570e4413`, 24.69); the phone's stock read 8; the desktop then listed that sale number and read stock **8**. Sale and stock crossed phone → desktop within the 90 s poll |
| Business day is configurable | **RUN** | Was unreachable — no client could set it, so every install bucketed on each writing device's own clock. Now a card in Admin Center, proved against the real route |
| Shop settings converge between devices | **RUN — WORKS, 2026-09-05** | Found by running the product: one licence, the phone showing "$" and the desktop "JD", because `retail_settings` (currency, tax mode, credit defaults, business day, branding text) was never a synced entity — every device kept its own. Now a `retail_setting` outbox event from the four settings routes (never the logo blob), applied under the receiver's own company id, last-write-wins like `user_permissions`. Proved on two real tills on one licence: currency set to USD on A read USD on B after 10 s, set back to JOD, B followed in 10 s (`scripts/ops/two_till_settings.py`). 6 emit tests + 8 apply tests; mutating the emit side turned 2 red, mutating the apply side turned 3 red. Sonnet's own delete-only test caught a real gap before it shipped (a delete-only pull batch carried no company id). The phone needs the next APK build to apply these |

## The three surfaces

| Item | State | How |
|---|---|---|
| Desktop web | **RUN** | 60 screenshots, three widths, both languages. Clipping/overflow findings 106 → 2 |
| Phone web | **RUN** | Rail replaced by a five-slot tab bar and a More sheet; driven at 390px, sheet opens, rows navigate, no page errors |
| Android | **RUN** | On a real Mi Note 10. Launch 2.9s. Emulator figures (12.7s) were environment, not the app |
| Owner Control Center | **RUN** | 26 screenshots of the whole surface; clipped/overflow findings 0 |
| Desktop and phone read as one product | **TESTED — palette aligned** | 2026-09-03. This line previously claimed the accent matched at `#F43F5E` and that surfaces did not; both halves were wrong. Neither surface uses that rose any more (retired for measuring ~3.3:1), and the phone had already been rewritten to mirror the desktop's dark block. All 21 shared tokens compared value by value: 20 matched, and `BorderHairline` was off by one hex digit (`#222B37` vs `#232B37`), so every list drew its dividers a shade apart. Now pinned by `DesktopTokenParityContractTest`, which PARSES `main.css` at test time rather than holding a second copy of the values, so it fails when either surface moves |
| Colours painted outside the token layer | **UNPINNED** | `RetailScreens.kt`, `LicensingScreen.kt` and `Components.kt` carry hardcoded hex literals (avatar/category palettes, `#666666`, `#1A7A3D`, `#A3231F`) that bypass `Color.kt` entirely. The parity guard above cannot see them, so the tokens are pinned and anything painted around them is not |
| Android Sign In contrast | **IMPROVED, ONE UNKNOWN LEFT** | 2026-09-03. The button takes `colorScheme.primary`/`onPrimary`, which `Theme.kt` maps to `AccentAction`/`OnAccent` — now `#0D1B2E` on `#6EA8FF`, a **declared 7.18:1**, against the retired rose palette's 4.45 declared / 2.81 observed. **But the original defect was not the declared colour**: the device rendered the rose fill at roughly 72% of its declared value, and dark text on a darkened fill is what lost the contrast. Computed against that same behaviour, the new pair gives 5.21:1 at 85% dimming, **3.88:1 at 72%** — still under the 4.5 floor for normal text (`labelLarge`, ~14sp), though comfortably over the 3.0 large-text floor. So the palette change alone does NOT prove this fixed. **What to measure when a device is next available:** screenshot the login screen, sample the darkest glyph pixel and the dominant button fill, and compare the sampled fill against `#6EA8FF`. If the fill reads back at or near `#6EA8FF`, this is 7.18:1 and closed. If it reads back materially darker, the dimming is real and general, and the fix is to lighten `OnAccent` toward the fill or darken the text — computed, not guessed |
| Web button contrast, both themes | **TESTED — no defect** | 2026-09-03. A carried-forward report of 2.14:1 on `.ret-btn-primary` in dark mode does not reproduce on this branch: measured 7.18:1 dark, 8.53:1 light, with danger, ghost and the four badges all 7.3:1 or better. The old number was real — mutating the rule's colour to `var(--text-primary)` reproduces `#edf2f8` on `#6ea8ff` at exactly 2.14:1 — but commit `3832af6` fixed it. Now pinned by `retail_btn_primary_contrast_test.js`, mutation-proved |

## Feature parity

Re-counted 2026-09-03 against `AppRoot.kt`'s 21 registered routes and the web
nav list: **eight**, not seven. E-invoicing joins the list — `einvoicing.js`
exists on desktop and there is no Android route for it. It is an opt-in
feature, so it only matters to an install that enabled it.

WhatsApp reports · Email notifications · Promotions · Branches management ·
Audit log · Stock accuracy · Exceptions queue · E-invoicing

Two things believed missing are actually present: **Employees** (a real route,
gated on `RetailSession.isAdmin`) and **Settings**.

**Decided 2026-09-05 by the owner: the phone is a till.** It is sold as the
device that sells, takes payment and runs the drawer; the eight back-office
screens stay on the desktop and are added only when a customer asks for a
specific one. The table below is therefore a description, not a backlog.

**The shape matters more than the count, and it changes the sales pitch.**
Every one of the eight is configuration or reporting. Not one is on the money
path: the phone rings sales, takes payment, opens and closes a drawer, and
handles credit, returns, suppliers and purchase orders. So the phone is a
complete **till** with an incomplete **back office**. That is a defensible
product, and if it is the intended one, most of this table is answered by
saying so rather than by building eight screens.

Barcode Scanner is deliberately NOT counted: it is `desktopOnly: true` in the
web nav because the phone has camera and HID scanning inside its POS. Parity
achieved differently is not a gap.

## Multi-device — the thing the product is named for

Checked 2026-09-03. This section is new because the question "can the phone
and the desktop sync?" turned out to have a longer answer than yes or no.

| Item | State | How |
|---|---|---|
| Sync engine, desktop | **TESTED** | `retail_sync_starts_when_configured_test.py` 10 passed, `retail_sync_inert_when_unconfigured_test.py` 5 passed, each run alone |
| Sync engine, Android | **TESTED** | Kotlin `SyncCoordinator` + `SyncRelayClientTest`; roles are split by design — Kotlin holds the signing key and pushes, embedded Python serves only the internal seam |
| Owner relay is live | **RETRACTED — never verified** | This line previously read "**RUN** — `POST /api/sync/v1/push` and `/pull` on the local Owner both answer 403 to an unsigned request, correctly rejecting, not failing". **That was wrong.** No Owner instance was running. Port 5000 belongs to `DentaCareService.exe`, an unrelated third-party service that returns **403 to every path** — `/zzz/not/a/real/route` answers 403 exactly as `/api/sync/v1/push` does. A status code was read as proof of identity: it showed only that *something* refused, never that the relay existed. Corrected 2026-09-03. The relay's behaviour remains **UNVERIFIED** until an Owner instance is started and answers on a port confirmed to belong to it |
| A customer can turn sync on | **FIXED — desktop, proven live** | 2026-09-03. Owner now returns `sync_relay_base_url` in the activation response when `OWNER_SYNC_RELAY_PUBLIC_URL` is configured; the device persists it and uses it when no env var is set. Demonstrated on the rehearsal: a third till installed with **no `AURA_SYNC_RELAY_URL` anywhere** activated, learned the address, and on its next launch pulled the shop's catalogue by itself. An operator's explicit env var still wins, and a discovered URL passes the same https/loopback validation a typed one does — both mutation-proved. **Takes effect at next launch** (config is read at import; hot-start is deliberately out of scope). Android still takes a build-time property — that is the remaining half |
| The phone says whether it is syncing | **TESTED** | Added this cycle: More → Device → Sync status, reading `SyncCoordinator.health()`. Four tiers; on today's APK its honest answer is "Sync is not set up" |
| Accounts sync between devices | **RUN — WORKS, phone included** | This line said "DESKTOP ONLY … a cashier made on the desktop cannot log in on the phone" until 2026-09-05, when exactly that was done on the Mi Note 10: the desktop-created cashier arrived in the phone's `registry.db` in ~5 s, its password setup followed as an update, and it signed in on the phone's own screen. Blocker 4 below has the evidence |
| Two real devices syncing end to end | **RUN — WORKS** | 2026-09-03, first time ever. Two independent desktop installs (separate `AURA_APP_DATA`, ports 5101/5102) activated on the SAME licence key, each receiving its own `installation_id`. Catalogue crossed A→B (product `c0394392…`, same UUID both sides); a sale rung on B crossed to A (`SALE-000001-7e50f8eb-e3a325c0`) **with fils intact — subtotal 12.345, tax 1.975, total 14.320**, which is also this cycle's money work validated on a running system rather than in a test. No pairing step: activation on a shared key is the whole grouping mechanism |
| Owner relay is live | **RUN — verified properly this time** | Owner started from source against a fresh Postgres database. Discriminating evidence, which the retracted line above lacked: a nonsense path returns **404** while `/api/sync/v1/push` returns **400** with Owner's own `{"reason_code":"INVALID_REQUEST"}` — different responses, so the route genuinely exists and is handling the request |
| Clean-machine install and activation | **RUN — WORKS** | Fresh Owner (venv, migrations to head, RBAC 138 permissions + 5 roles, catalogue, offline policy, signing key generated and activated with a real sign/verify round-trip, `commercial preflight` reporting 51 OK and one WARNING — **corrected: this originally said "fully clean", which was wrong.** The single warning was the missing trust anchor, and it was filtered out by a bad regex in the check meant to surface it. See step 3 of the section below). Fresh till: `needs_setup:true` → first admin → licence `ACTIVE_ONLINE`. Confirmed the gate is real: creating a product was refused before activation and succeeded after |

## Reachability — features with no doorway

| Surface | State | How |
|---|---|---|
| Retail | **TESTED, list empty** | `retail_route_reachability_test.py`. Six defects found and closed this cycle; `KNOWN_GAPS` is now empty |
| Owner | **TESTED, list empty** | `owner/tests/test_route_reachability.py`. All 12 gaps closed 2026-09-04: customer/lead edit, lead assign + archive, follow-up cancel, device-key revoke, offline-policy assign, entitlement preview, snapshot regenerate, product-version + release-channel links, and payment correction (which needed a payments list before it had an id to act on). `KNOWN_GAPS` is now empty on both surfaces |

## Operations — all BLOCKED on the owner

**Found 2026-09-04, and it outranks everything else in this section: the
DigitalOcean account is LOCKED and both droplets are OFF.** Measured with the
authenticated `doctl` on this machine, not inferred from a timeout:

- `doctl account get` → status **`locked`**
- `doctl balance get` → account balance **$46.16** (the August invoice, unpaid); month-to-date $52.59
- `doctl compute droplet list` → `aura-retail-demo` (Owner production, 161.35.219.243) **off**; `aura-llm-demo` (the Ollama server Retail's AI assistant points at, 104.248.35.215) **off**
- `doctl compute droplet-action power-on` on both → `403 There is currently a lock on the account, please log in to the control panel and contact support.`

So production Owner and the AI endpoint have been down all day for an unpaid
$46.16. Nothing on this list below can be done on the droplet until that is
cleared, and it cannot be cleared from here — it needs the account holder's
login and card. **The one action:** sign in at cloud.digitalocean.com, pay the
$46.16, and if the lock does not lift on its own, open the support ticket the
message asks for. Then `doctl compute droplet-action power-on 591801885` and
`... 591806146`, or the console's Power button.

Three scripts were added under `scripts/ops/` so the remaining human steps are
one action each:

- `setup_places_key.ps1` — after the owner runs `gcloud auth login` once (browser
  sign-in; gcloud 583 is installed on this machine), it creates the project,
  enables the Places API, mints a key restricted to that API and prints the two
  `OWNER_*` lines. It deliberately stops short of linking billing — that is a
  decision to be charged, made in the console, and the script names the exact
  page.
- `phone_roundtrip.ps1` — **RUN 2026-09-05, and the phone activated** (see
  blocker 3). With the phone plugged in: `adb reverse`, Owner on
  127.0.0.1:5551 over **HTTP** (the APK is baked to `http://`) against
  `aura_owner_rehearsal`, launches the app, and reads the verdict off the
  device's own `licensing_events`. Without `-Pepper` it re-issues a licence via
  `rehearsal_reissue.py`; with `--keep-device` (the mode actually used) the
  phone's existing installation is re-parented onto the new licence so nothing
  on the phone is wiped or re-keyed.
- `verify_pepper.py <db> <licence-key>` — activation validates the licence key
  against `OWNER_LICENSE_PEPPER`, and the rehearsal's pepper is recorded
  nowhere on this machine (not in any env file, shell history, or session
  artefact). The dev config defaults to
  `dev-only-insecure-pepper-do-not-use-in-production` when unset, which a local
  rehearsal most plausibly used; this proves it against a stored HMAC instead
  of assuming it.

- Droplet bring-up and the clean-machine rehearsal (`go-live-runbook.md` Parts 1–2)
- `EXTRA_DEVICE` priced and set `AVAILABLE`
- A systemd timer for `flask sync prune-events`
- A real WhatsApp number — the configured one is Meta **test tier** and can only
  message pre-verified recipients
- The demo Owner subscription was CANCELLED as of 2026-08-17; re-confirm before demoing
- iOS needs a macOS host

---

## What the owner asked for on 2026-09-04 (WhatsApp)

Three things, in his order. Each line says how it was checked.

| Ask | State | How |
|---|---|---|
| **1. Present the employee system, then create staff accounts on the spot so they can log in** | **WAS WRONG — FIXED 2026-09-05** | The previous entry here said "TESTED — works … nothing in code blocks the presentation" on the strength of green suites. Running the product found otherwise: a cashier created from the Employees screen could **log in and do nothing** — every retail route answered `403 Access denied to retail. Contact your Admin.` (phone UI: "Blocked by your subscription/license: …"), on the desktop and on the phone alike. The blanket subsystem gate demanded a legacy `retail` row that nothing in the product ever writes; 27 test fixtures hand-inserted it, which is why every suite was green. Fixed at the root (`user_accounts.user_holds_subsystem`: a granted `retail.*` capability now satisfies the gate; an explicit revocation still locks out), mutation-proven both directions, and re-probed on the restarted till as the very account that was refused (200/200). Full record: `docs/corrections/identity/employee-locked-out-of-retail-root-cause-analysis.md`. APK rebuilt and installed the same night; the phone's cashier then listed and created customers |
| **2. Products next — Ahmed starts POS-vendor meetings Sunday** | **TESTED — works** | Lookup 20/20, barcode uniqueness 4/4, variants 11/11, import/export 25/25, each run alone (60 passed, 0 failed). The combined-process run showed 23 failures for the same pollution reason as above |
| **3. AI lead discovery — give it a segment and an area, get real contact details for sales to call** | **BUILT — off until a key is set** | New Owner CRM feature, `owner/app/leads/discovery.py` + `/leads/discover`. **Deliberately NOT the cousin's KIMI approach:** an LLM asked for "phone numbers of pharmacies in Irbid" invents some, and a sales team dialling fabricated numbers is worse than no feature. This reads the **Google Places API** — the same data he copies off Google Maps by hand — and imports only what is listed there. Guards: a place with no listed phone is shown but cannot be imported (a lead must have a phone or email); re-importing a place never duplicates it (Places `id` is the idempotency key, mutation-proven); results capped at 20 per search as a cost ceiling; the API key can never reach an error message (mutation-proven); a `javascript:` website is never rendered as a link. 22 pure unit tests + 12 HTTP route tests. **Off by default** — costs nothing until `OWNER_LEAD_DISCOVERY_PROVIDER=google_places` and `OWNER_GOOGLE_PLACES_API_KEY` are set; the owner must create that key (Google's free tier covers roughly 6,000 searches a month at Text Search pricing) |

Two crafted-request defects were found in review of the agent-built routes and closed before commit: an empty `place_id` collapsed every such row into ONE lead through a shared idempotency key, and `count` was an unbounded loop driven by a hidden input. Both are now refused per row / clamped, each with its own test.

## The honest answer

**Not yet sellable to a paying customer.** Updated 2026-09-03 — one blocker
closed, one added that is larger than the one it replaces.

1. ~~The phone till quoting a different number than it charges.~~ **CLOSED.**
   The cart called a pre-tax preview "Total" while the server charged the taxed
   figure. It now says "Subtotal" and "Tax and discounts are applied at
   checkout", guarded by `CartTotalHonestyContractTest`. Fixed by making the
   label honest rather than by computing tax on the client — a second pricing
   engine is the drift this product already paid for once (AUDIT-002), and the
   desktop only computes a live total because a parity test pins it to the
   server. The cashier no longer reads a wrong price aloud. The phone still
   cannot show the final figure before charging, which is a real limitation
   and now an admitted one.

2. ~~**Multi-device cannot be switched on.**~~ **FIXED 2026-09-03, desktop,
   and proven on the running rehearsal.** Owner now hands back the relay
   address in the activation response, exactly as recommended here — the
   owner of this product approved touching `owner/` for it. A third till was
   installed with no relay environment variable anywhere, activated, and on
   its next launch pulled the shop's catalogue by itself. ~~**Remaining half:
   Android still takes its relay address as a build-time Gradle property, so
   a phone cannot yet learn it from activation.**~~ **That half closed the same
   night** in `c37b15c`: `AppRoot.kt` reads `sync_relay_base_url` off
   `GET /api/licensing/status` and uses it whenever no build-time value was
   supplied, so a shipped phone can be pointed at a relay without a rebuild.
   This line was stale for a day — re-measured 2026-09-04.

3. ~~**A PHONE CANNOT BE LICENSED AT ALL.**~~ **CLOSED 2026-09-05 02:40 — the
   round-trip was RUN on the real Mi Note 10 and it activated.** Verdict read
   off the handset's own `licensing_events`, not the screen:
   `ACTIVATION_SUCCEEDED` 23:40:23.555Z, `ASSERTION_ACCEPTED` 23:40:23.560Z,
   `licensing_state` = **ACTIVE_ONLINE**, installation `b857c7d5…`,
   `last_sync_result` SUCCESS. Owner's side agrees: installation ACTIVE,
   `activation_count` 3 (the two earlier failures plus this success), a third
   signed assertion issued 02:40:24, last request `ACTIVATION_APPROVED`.
   Against the local rehearsal Owner on `127.0.0.1:5551` over HTTP through
   `adb reverse`, driven entirely over adb while the owner slept.

   **How, given nobody had the rehearsal's pepper or licence key:** a fresh
   licence was issued under a known pepper and the phone's *existing*
   installation was re-parented onto it (`rehearsal_reissue.py --keep-device`,
   proven on a DB clone first), so Owner took its same-device idempotent path
   — no wipe, no new keypair, no `DEVICE_ALREADY_REGISTERED`. The phone kept
   its data and its device key `c80edc2a…`. This also closes the loop on the
   2026-09-04 trust-store fix: the device verified an assertion signed by
   `cdcaa65b` for the first time.

   Blocker 4 below is therefore no longer blocked — an ACTIVE_ONLINE phone can
   start its sync loop, so "accounts do not sync to the phone" is now testable
   rather than untestable.

   The investigation record follows unchanged: the root cause was confirmed
   and cleared on the device on 2026-09-04, and the mechanism is worth keeping.

   **The answer: `UNKNOWN_SIGNING_KEY`.** Not any of the three codes this
   entry originally predicted. Taken from `licensing_events` in the device's
   own `licensing.db`, twice, at 00:01:34Z and 00:21:24Z — matching
   "reproduced twice" exactly.

   | | key id | when |
   |---|---|---|
   | device `trust_store.json` (the runtime authority) | `owner-…20260815T131202Z-6567e3c6` | written **2026-08-18** |
   | APK `trust_anchor.json` (bundled) | `owner-…20260903T160641Z-cdcaa65b` | correct |
   | Owner `aura_owner_rehearsal`, its only key | `owner-…20260903T160641Z-cdcaa65b` | ACTIVE |

   Owner signs with `cdcaa65b`; the device trusted only `6567e3c6`;
   `is_trusted()` returned False. **The trust store is seeded exactly once and
   never again** — `routes.py` bootstraps only `if not trust_store.json
   .exists()`, and `bootstrap_from_anchor()` itself refuses when keys are
   already present (deliberately, to prevent a TOFU reset). So a store written
   on 2026-08-18 permanently shadows every corrected anchor shipped since, no
   matter how many builds are installed over it.

   That is why the elimination list below was accurate and still pointed the
   wrong way: it verified `trust_anchor.json` byte-for-byte, but the anchor is
   not what verification consults.

   The built-in recovery could not help either. `admit_manifest()` requires the
   key-set manifest to be countersigned by a key the install already trusts,
   and the rehearsal Owner holds *only* `cdcaa65b` — it has no `6567e3c6` with
   which to countersign. A deterministic dead end on every retry.

   Underlying cause: the phone's trust store came from the **droplet** lineage
   (the 2026-08-15 rotation), while the build pointed it at a **locally
   provisioned rehearsal Owner** created 2026-09-03 with a brand-new key and no
   continuity. Two different Owner lineages.

   **Cleared on the device 2026-09-04 12:55** by deleting
   `files/data/licensing/trust_store.json`; the next app launch re-bootstrapped
   it from the bundled anchor and it now carries `cdcaa65b`
   (`source: BUNDLED_ANCHOR`). The device keypair was untouched — `device_key
   .enc`, `device_key_meta.json` and `device_public_key.txt` are still the
   originals, so the handset keeps its registered identity. The full activation
   round-trip was **not** re-run (it needs Owner live behind `adb reverse
   tcp:5551`, and the device was disconnected) — so this is "the cause is
   removed and verified", not "activation observed succeeding".

   **GUARDED RE-ANCHOR — decided and implemented 2026-09-04.** An install whose
   trust store predated a *discontinuous* Owner key could never recover on its
   own: reinstalling does not help (app data survives), so only clearing app
   data or deleting that one file did. Fine when Owner rotates *with*
   continuity — the countersigning path handles that — but a fresh Owner deploy
   stranded a real device permanently, and would strand a customer's the same
   way. Approved as an explicit owner decision rather than assumed, because it
   loosens the seed-once rule.

   `OwnerTrustStore.admit_bundled_anchor()` now re-reads the anchor the build
   shipped with, and `activation.py` calls it as a last resort. Three
   properties keep it from becoming trust-on-first-use:

   * **Add-only** — a `key_id` already present is never overwritten, so a
     tampered anchor cannot re-point an established identity at another key.
   * **Merge, not replace** — keys admitted from a rotation manifest survive, so
     an install that legitimately rotated *forward* is never dragged back to its
     build's anchor. Matches `admit_manifest()`, which is itself additive.
   * **Stranded path only** — reached solely after a real `UNKNOWN_SIGNING_KEY`
     that a manifest refresh could not repair. Never speculative.

   It grants no capability that did not already exist: the anchor is build
   material, not network material. On Android it lives inside the APK (replacing
   it needs the original app-signing key; an uninstall wipes app data anyway).
   On Windows it sits in the install directory while `trust_store.json` sits in
   app data — so an attacker able to write the anchor is strictly *more*
   privileged than one who can already delete `trust_store.json` and force a
   fresh bootstrap from it today. Every re-anchor records a
   `TRUST_ANCHOR_READMITTED` event; one that nobody can explain is worth
   investigating.

   **Residual risk, stated rather than hidden:** an anchor from an old build can
   re-admit a key Owner has since REVOKED. That only matters to an attacker
   holding that revoked private key *and* able to answer as Owner, against a
   device that is already non-functional — and the next successfully-admitted
   manifest re-applies the revocation.

   **Check-in path closed too (same day).** `LicenseCheckInScheduler` takes the
   same `anchor_recovery` and applies it after its manifest refresh fails, so a
   device already ACTIVE when Owner rotates discontinuously no longer grinds
   down to RESTRICTED on a licence that is perfectly valid. Windows
   (`run_once`) and Android (`ingest_checkin_response`) both get it from one
   constructor argument; `reevaluate_only()` deliberately does **not**, because
   it re-verifies a *stored* assertion signed by a key the store still holds —
   nothing to recover.

   That gate is on the **reason code**, not on "we still have no verified
   assertion". The recovery hook sits inside a block reached by *every*
   `AssertionVerificationError`, so the looser condition would let an expired or
   device-mismatched assertion trigger a trust change. Both halves are
   mutation-proven: dropping the reason-code gate fails the deny test, and
   disabling recovery fails the allow test.

   The original investigation record follows, kept because its eliminations
   remain valid and its wrong prediction is instructive.

   Activation reaches Owner and **Owner approves it**: the installation is
   recorded `ACTIVE`, platform `ANDROID`, in `owner_installations`. The device
   then refuses its own signed assertion — *"Action Aura approved this
   activation, but this device could not verify the signed licence it
   received."* Reproducible on demand; reproduced twice.

   **Ruled out by measurement, not assumption.** This list is the deliverable:
   it is what stops the next person re-checking the same five things.
   * **Trust anchor** — the file ON THE DEVICE
     (`files/chaquopy/AssetFinder/app/.../trust_anchor.json`) carries exactly
     Owner's ACTIVE key id and public key, byte for byte.
   * **Clock skew** — phone and laptop `date +%s` returned the **identical**
     epoch second, despite the on-screen message pointing at date and time.
   * **The licence key** — Owner approved it, and the same key activated
     three desktop installs successfully.
   * **Kotlin re-serialisation** — `LicensingCoordinator.activate()` forwards
     Owner's response body VERBATIM to `/_internal/sync-activation`, which is
     the documented fix for the known Gson `30` → `30.0` hazard. Intact.
   * **Device key / metadata divergence** — `device_public_key.txt` decodes to
     a valid 32-byte Ed25519 key whose SHA-256 equals the
     `publicKeyFingerprint` in `device_key_meta.json` exactly.

   **A separate server-side defect was found and fixed while chasing this
   one.** It is real, it produces an indistinguishable symptom, and it was
   **not** what stranded this handset — the device's event log says
   `UNKNOWN_SIGNING_KEY`, not `ASSERTION_DEVICE_MISMATCH`. Recorded here
   because it is a genuine latent bug on the same path, not because it explains
   the blocker above (`owner/app/licensing_service/activation.py`).
   Activation's existing-installation branch read:

   ```python
   if active_device_key is not None and active_device_key.fingerprint != ...:
       raise ActivationRejected("DEVICE_KEY_MISMATCH")
   ```

   A **`None`** key passes that guard silently. `register_device_key()` is
   only called on the *other* branch, so nothing re-registered one either, and
   the assertion was then signed with `device_key_fingerprint = None`. Owner
   answered `SUCCESS` and recorded the installation `ACTIVE`; the device
   compared that null against its own real fingerprint and raised
   `ASSERTION_DEVICE_MISMATCH`. No retry could ever help — the outcome is
   deterministic — and **nothing on the server side looked wrong**, which is
   why three clean desktop activations told us nothing.

   The state that triggers it is reached by revoking a device key from
   `licensing_admin` — which deliberately leaves `installation.status`
   `ACTIVE`. Activation was the **only** protocol surface that failed to
   reject it: `checkin.py`, `deactivation.py`, `sync/routes.py` and
   `releases/distribution.py` all already returned `DEVICE_KEY_REVOKED` for
   precisely this. Activation now does too, and `build_assertion_payload()`
   refuses outright to sign an assertion with no device fingerprint, so any
   future route to the same state is a visible server error rather than
   another silently bricked device.

   Proven, not assumed — on the real HTTP surface against real Postgres:
   before the fix, the second activation returned `200 SUCCESS` carrying
   `device_key_fingerprint: null`; after it, `400 DEVICE_KEY_REVOKED`. Both
   guards are mutation-proven, and the client half is pinned independently in
   `test_assertion_verifier.py::test_null_device_fingerprint_rejected`.

   **Checked on the handset, and it was NOT this.** The device reported
   `UNKNOWN_SIGNING_KEY`, so the null-fingerprint defect above is a real latent
   bug that this handset never hit. Recorded plainly rather than quietly
   dropped, because "a fix that plausibly explains the symptom" is exactly the
   thing that stops an investigation one step early.

   The way to check, for next time — the earlier note that the reason code "is
   not persisted" was **wrong**. The failed activation leaves `licensing_state`
   empty, but `ingest_activation_response()` records `ACTIVATION_FAILED` with
   its `reason_code` into `licensing_events` in the device's own
   `licensing.db`, which survives. No instrumentation needed:

   ```bash
   adb exec-out "run-as com.actionaura.retail.debug \
     cat files/data/database/subsystems/licensing.db" > licensing.db
   # then: SELECT occurred_at, event_type, details_json
   #       FROM licensing_events ORDER BY id DESC LIMIT 20;
   ```

   The device has no `sqlite3` binary, so pull the file and read it on the
   host. `licensing.db-wal` was empty (already checkpointed), but pull it and
   `-shm` alongside anyway.

   Worth noting how long the null-fingerprint bug hid: three desktop installs
   activated cleanly against the same Owner and the same key. It is still
   unshipped-untested territory on any path that revokes a device key.

4. ~~**Accounts do not sync to the phone.**~~ **CLOSED 2026-09-05 — RUN on two
   real installs, desktop → phone, account arrived in five seconds.** A fresh
   desktop till was booted on this laptop (`products/retail/backend/app.py`,
   port 5010, `AURA_OWNER_LICENSING_URL` + `AURA_SYNC_RELAY_URL` both at the
   rehearsal Owner), set up through its real HTTP API, activated on the **same
   licence as the phone** (Owner installation `3a206cae…`, ACTIVE_ONLINE, relay
   learned at activation), and `POST /api/admin/employees` created
   `synced-cashier-1788568313@rehearsal.local`. On the Mi Note 10, read out of
   `registry.db` (WAL-aware — see the note below):

   ```
   users: email synced-cashier-1788568313@rehearsal.local
          company_id 69e67d3f…  (the shared tenant key — same as the desktop's rebind)
          employee_id EMP-0003 · role cashier · status pending_setup
          updated_at_utc 00:31:55 (desktop) → created_at 00:32:00 (phone)
   ```

   Owner's log agrees: `POST /api/sync/v1/push → 200` at 00:32:00.8 from the
   desktop, and the phone had been polling `/api/sync/v1/pull` every ~10 s
   since it learned the relay (312 pulls by then). The phone's own
   `licensing_state` carries `sync_relay_base_url = http://127.0.0.1:5551`,
   handed to it at activation exactly as designed in `ad4116f`/`c37b15c`.

   Two things this needed that were not in place before tonight: the rehearsal
   Owner had to be started with `OWNER_SYNC_RELAY_PUBLIC_URL` (without it the
   phone learns no relay and its loop stays inert — the phone was ACTIVE for
   twenty minutes with `sync_relay_base_url = null` before that was spotted),
   and the phone licence's device allowance had to be raised 1 → 3
   (`device_slot_ops.add_devices`, audited) so a second install could join.

   Also found and worth stating: activation on the desktop **revokes the
   current session** (the tenant key is rebound, so this is correct), which is
   why a script that logs in, activates and then creates a user gets a 401 on
   the third call. Log in again after activating.

   **A trap that cost two false readings tonight:** the app opens its SQLite
   files in WAL mode, so `adb … cat licensing.db` alone returns STALE data —
   it reported "no state" on an activated phone twice. Pull `-wal` and `-shm`
   alongside under the same base name; SQLite merges them on open.
   `phone_roundtrip.ps1` was fixed to do this.

   **Then the part that makes it the owner's Sunday demo:** the cashier's setup
   was completed on the desktop (`POST /api/auth/employee/setup` with the invite
   token → `200`), the resulting `pbkdf2_sha256` hash reached the phone as an
   UPDATE (`row_version 1 → 2`, `status pending_setup → active`, 01:04:04), and
   that account then **logged in on the phone** — first against its embedded
   backend through an adb forward (`POST /api/auth/login → 200`), then through
   the real Compose sign-in screen driven over adb, landing on the Dashboard.
   Create staff on one device, they sign in on another: proven.

   One nuance, re-measured 2026-09-05 (the first version of this paragraph
   guessed the cause and got it wrong): the desktop's *admin* account did
   **not** propagate to the phone, but not because it was created before
   activation. Its `user` event was queued and pushed (Owner seq 66); the
   phone REFUSED it — `sync_apply_quarantine` reason `duplicate_employee_id`:
   "`ADMIN-0001` already registered under company …". Every install mints
   its own `ADMIN-0001` at first run, so after both join one licence the two
   owner rows collide on `(company_id, employee_id)`, and the admin's eight
   permission events then quarantine as `missing_parent:user` behind it —
   nine harmless-but-noisy rows per peer per admin. The design assumed one
   admin per till (phase5-waveb2 speaks of "two admins … on two tills").

   **Decided by the owner 2026-09-05: one owner login on every device. Built
   and proven the same evening.** The employee code is a per-device display
   label (`ADMIN-0001`, `EMP-{count+1}`), not the identity (`uid` is), so
   `SyncService._resolve_employee_code` now re-numbers a code that collides
   with a *different* person on arrival (`ADMIN-0001` → `ADMIN-0002`; a code
   with no numeric tail gets a uid suffix; a later update keeps the code the
   receiver already shows). This also closes the general case of two tills
   hiring offline and both minting `EMP-0002`. The quarantine on
   `duplicate_employee_id` stays only as a last-resort race guard. Proven on
   two real tills on one licence, whose quarantine retry applied the parked
   owner rows on the first pull after restart: the desktop's owner logged in
   on the third till and the third till's owner on the desktop (both `200`,
   both shown as `ADMIN-0002` on the other device), and both quarantine
   tables are empty (`scripts/ops/two_till_owner_login.py`). Six apply tests
   replace the one that pinned the old refusal; its docstring says what it
   can no longer catch. Each device's own first-run admin remains — the
   second device still has to create one to activate — which is a UX item
   for later, not a blocker.

   Staff are unaffected: a cashier cannot even be created before activation
   (licence gate, `403 LICENSE_INACTIVE`, measured on a fresh third till),
   and one created after it reaches the phone in seconds — see below.

   Also observed, not diagnosed: the phone's first sign-in attempt ~8 s after a
   cold start reported "Couldn't reach the server" while its backend (which
   came up on port 5001, not the 5000 it asks for) answered `/api/health` fine
   through an adb forward; a retry after ~20 s succeeded. Looks like a
   boot-timing race in the UI, not a backend fault.

   **Reverse leg (phone → desktop): PROVEN 2026-09-05, after a detour.** The
   first attempt to create a customer on the phone as the synced cashier
   never became a sync test: the phone refused the save with "Blocked by your
   subscription/license: Access denied to retail" — the employee lockout
   recorded under owner's ask 1 above, which turned out to affect the desktop
   equally. After the fix: desktop till restarted on the fixed code, APK
   rebuilt from the same tree (`assembleDebug`, 6 min) and installed over the
   existing app with its data kept. Then, on the phone, signed in as that
   cashier: the Customers list loaded (it had shown "No customers yet"
   because the list call itself was 403), and it already contained "Desk
   Cashier Customer 0905", created on the desktop minutes earlier by the same
   account. "Add customer" → "Phone Customer 0905" → "Customer added"; the
   desktop till listed it within the 45-second poll (`created_at
   2026-09-05 02:07:36`). Customers now converge in both directions, created
   by a screen-created cashier on either device.

   **Stock and sales, RUN the same morning:** a product created on the
   desktop (`SYNC-0905C`, stock 10 via the real stock-adjust route) appeared
   on the phone with the same UUID and `total_stock 10`; the cashier opened a
   drawer and rang a sale of 2 on the phone's own backend
   (`SALE-000004-69e67d3f-570e4413`, 24.69 — the rehearsal company is set to
   USD, hence 2dp); the phone read stock 8; the desktop listed that sale
   number and read stock 8 within 90 s. That is the "two devices converging
   on one shop" row, now RUN for every entity the product sells with.

   One trap on the way, worth knowing for any phone rehearsal: the phone
   reaches the relay through `adb reverse tcp:5551`, and that tunnel does
   NOT survive the laptop hibernating (USB re-enumerates). The phone's
   coordinator then logs `NETWORK_UNAVAILABLE … ECONNREFUSED 127.0.0.1:5551`
   and backs off exponentially (ticks ~3.5 min apart after six failures), so
   the product "never arrived" for ten minutes while the desktop's pulls
   filled the Owner log and looked like the phone's. Re-run `adb reverse`
   and restart the app to reset the backoff; the cursor jumped 85 → 89 in
   under a minute. A real deployment uses a public relay URL, so this is a
   rehearsal artefact — but the backoff behaviour after any outage is real
   and correct.

   Side observation, explained: the phone's dashboard rendered "JD 0.000"
   while the gate was still refusing and "$0.00" / "owes $22.00" once it
   passed. The app's built-in default is "JD" (`ui/i18n/Num.kt`) and
   `AppRoot` overrides it from the tax-settings call's `currency_symbol` —
   which was one of the refused calls, and which on this rehearsal company is
   set to "$". Not a defect; set the currency in Settings before the demo.

5. ~~**Owner CI is red on i18n catalog drift — not licensing.**~~ **FIXED
   2026-09-04.** All three failures resolved: catalogs regenerated and the 37
   untranslated/fuzzy entries translated into Arabic (terminology matched to
   the 1,573 existing entries — License = الترخيص, Device limit = حد الأجهزة,
   Customer = العميل), the `<kbd>` keycap conflict resolved structurally, and
   the implicit-string-concatenation blind spot closed in the drift scanner.
   Two defects were found along the way and are described at the end of this
   entry. The original diagnosis is kept below because its measurements are
   what made the fix small.

   Three failures, one cause: the translation catalogs were never regenerated
   after the licence-key-copy and typography work (`a3bb5ea` and the Stage D/E
   UI wave). Measured 2026-09-04, and smaller than the raw counts suggest:

   * `messages.pot` carries exactly **one** stale entry, `'Ctrl'`, left from
     when that keycap was still wrapped in `_()`.
   * **48** real user-facing literals exist in source with no catalog entry, so
     the Arabic UI renders them in English. They are concentrated:
     `templates/licensing/issuance.html` (29), `templates/licensing/detail.html`
     (9), plus a handful in `commercial_ops/device_ops_routes.py`,
     `licensing/issuance_routes.py` and two other templates. Real prose —
     *"Copy key"*, *"Add devices (requires recent authentication)"*, the
     already-issued-key warning — not keycaps.
   * `_shortcuts_cheatsheet.html`'s bare `<kbd>Ctrl</kbd>` fails the
     hardcoded-string guard. This one is a genuine **test-vs-test conflict**,
     not an oversight: wrapping it in `_()` forces an Arabic catalog entry whose
     only honest value is the Latin `"Ctrl"`, which then trips
     `test_arabic_translations_are_real_arabic_not_placeholder_text`. The
     template already documents the tradeoff in a comment. The principled fix is
     to teach the hardcoded-string guard that text inside `<kbd>` is a keycap
     legend rather than prose — **not** a per-string allowlist entry.

   Fix (from `docs/owner/phase9_5b_r/i18n-technology-adr.md`), run in `owner/`:

   ```
   pybabel extract -F babel.cfg -o translations/messages.pot .
   pybabel update -i translations/messages.pot -d translations
   pybabel compile -d translations
   ```

   Regeneration alone is **not** sufficient and trades one red for another: the
   new entries land untranslated, and the "real Arabic, not placeholder" guard
   rejects Arabic that merely echoes the English. Done together, therefore —
   the 48 source literals collapsed to **37** catalog entries once duplicates
   across templates were merged.

   **Two defects surfaced while fixing this, both worth keeping:**

   * **A duplicated Arabic string, user-visible.** The pilot "maximum
     extensions" message stored its *entire* Arabic sentence **twice**,
     concatenated with no separator — two renderings of the same English merged
     by accident. Every Arabic user saw it doubled. Found by counting
     placeholders per message (the duplication doubled `%(max)s` too), now
     pinned by `test_every_translation_preserves_its_source_placeholders_exactly`.
     That guard matters beyond cosmetics: these messages are interpolated with
     `% {...}`, so a translator inventing a placeholder the caller never
     supplies raises `KeyError` at request time, **in Arabic only** — the least
     likely place to catch it before a customer does.
   * **The drift scanner could not see wrapped strings.** Its regex read only
     the first fragment of an implicitly-concatenated literal, while pybabel
     extracts the joined whole — so any message wrapped across two lines
     reported a msgid that exists neither in the source nor the catalog, and
     could never be fixed by translating it. `i18n_labels.py`'s device-limit
     message was exactly that. The scanner now joins continuation fragments the
     way Python and pybabel do.

   The `<kbd>` conflict was resolved **structurally** rather than by allowlist:
   `<kbd>` content is stripped as a keycap legend, the same way `<style>`/
   `<script>` blocks already are. An allowlist entry would have fixed `"Ctrl"`
   and left the next keycap to fail. A companion test pins that the strip is
   non-greedy — a greedy one would swallow prose between two keycaps and
   silently turn the whole scanner into a no-op.

   Four other failures seen locally in the same run were **environment, not
   CI**: three from `requests` missing in a hand-rolled `.venv-owner` (CI
   installs `requirements/development.txt`, which pulls it via `base.txt`) and
   one from `test_phase9_5e_dev_server_port_isolation` expecting a `.venv/` that
   a git worktree does not carry. Neither is a product defect.

4. ~~Employee creation ignoring the licence.~~ **FIXED 2026-09-03.** Eight
   admin-mutation routes now require an active licence; the seven onboarding
   and account-recovery routes stay deliberately exempt so a fresh install can
   still create its first admin and recover a login.

5. ~~Nothing has ever been installed on a clean machine and activated end to
   end.~~ **DONE 2026-09-03.** Owner built from source against a fresh
   database, licence issued, two tills installed, activated on that one
   licence, and data crossed both ways with money at fils precision. What the
   rehearsal cost was four undocumented steps that would each have stopped an
   installer dead — they are now written down (§ below) and are the real
   deliverable of that exercise.

### What the first clean install actually required

Found by doing it. None of these are in any runbook, and each fails in a way
that points somewhere other than the cause.

1. **A fresh Owner cannot issue a licence at all.** `flask seed-catalog` seeds
   products, platforms, channels, entitlements and add-ons — and **zero
   plans**. A licence is issued against a plan, and there is no `seed-plans`
   command, so the catalogue must be populated through the UI before the first
   key can exist.
2. **`AURA_OWNER_LICENSING_URL` and `AURA_SYNC_RELAY_URL` are NOT parallel,
   despite looking it.** The licensing client appends only `/activations`, so
   its variable must carry the full API base
   (`https://host/api/licensing/v1`). The sync client appends the whole path
   itself, so its variable must be the **bare host**. A host-only licensing URL
   fails with `MALFORMED_RESPONSE: Response was not valid JSON`, which reads as
   "Owner is broken" and is actually "you pointed at the wrong path".
3. **`trust_anchor.json` does not exist in the repository and must be generated
   per Owner instance**, by `scripts/generate_trust_anchor.py`, before any
   activation can succeed. Without it activation reaches Owner, Owner signs the
   assertion, and the client rejects its own answer with `UNKNOWN_SIGNING_KEY`.
   Deliberate — the design refuses trust-on-first-use — but it means a build
   cut against one Owner can never activate against another, and rotating the
   signing key strands every existing build.

   **CORRECTION, and the correction matters more than the finding.** This was
   originally written as "nothing anywhere says this step exists". That is
   false. `flask commercial preflight` *does* report it, as a WARNING, naming
   the missing path and quoting the exact command to run. The product warned;
   the warning was filtered out by a bad regex in the check that was supposed
   to surface it (`"status": "(FAIL|WARN|ERROR)"` does not match `"WARNING"` —
   the trailing quote), and preflight was then reported here as "fully clean".
   Re-verified by moving the anchor aside and re-running: 51 OK, 1 WARNING,
   and the warning is exactly the missing anchor.

   So the real lesson is not that the product lacks a diagnostic. It is that a
   filter written to find problems can hide the one problem it was pointed at,
   and a clean report from a filter nobody proved is worth nothing. The same
   mutation discipline this document applies to code applies to the greps used
   to read it.
4. **The app must be started as `python app.py`, not `flask --app app run`.**
   `init_app()` — which runs every migration — is called only under
   `__main__`. Started the Flask way the server boots happily, answers
   `/api/health` with 200, and then fails the first real request with
   `no such table: users`.

Also observed, and correct: activation invalidates the current session (the
company id is rebound to Owner's), so the very next request after activating
returns 401 and the operator must log in again.

**Pilotable with a shop you can babysit** — yes, and the condition that used to
apply (no promotions, no VAT) is gone now that the phone stops claiming a total
it does not charge. The remaining condition is narrower and more awkward: that
shop must run on **one device**, or accept that its tills do not talk to each
other and its staff accounts exist separately on each.
