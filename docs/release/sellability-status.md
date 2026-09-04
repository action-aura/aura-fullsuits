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
| Payments outside the drawer | **PARTIAL** | ~34 `_money()` call sites (customer, supplier and PO payments) still quantize to 2dp. Recorded in ROADMAP, being converted one tested call site at a time |
| Both notification channels report the same figure | **TESTED** | 2026-09-03. WhatsApp hardcoded `:.2f` while email was currency-aware, so one drawer read `JD 12.350` by email and `12.35` by WhatsApp. Now share `core/retail/money_format.py`. The parity test's first version was a false green and was tightened, not accepted |
| A till close reaches the owner by email | **RUN — WORKS** | 2026-09-03. Driven on the live rehearsal install against a local SMTP catcher, so this is the delivered message, not an outbox row. Opened a drawer at 12.345, closed it at 12.340, and the worker drained it on its own timer with no prompting. Caught verbatim: `Expected cash: JD 12.345 / Counted: JD 12.340 / Variance: JD -0.005` followed by "This drawer did NOT balance." One message validates four separate pieces of this cycle at once — the automatic trigger (email previously had only the low-stock one), currency-aware formatting (**JD**, not `$`), fils precision end to end, and the outbox worker. **A five-fils discrepancy is now visible; under the old 2dp code it was not.** |
| **Phone till QUOTES a different number than it CHARGES** | **RUN — DEFECT** | Android preview is `sell_price × qty`: no tax, no discount, **no promotions**. Server applies all three. Cashier reads one price, receipt prints another. Recorded in ROADMAP; the fix is a product decision |
| Promotions resolve identically on both clients | **TESTED** | The parity test above. Android is NOT covered — it has no promotion logic at all |

## Licensing and the commercial model

| Item | State | How |
|---|---|---|
| Device limit enforced | **TESTED** | Server-side, `owner/tests/test_commercial_ops_add_devices.py`, `DEVICE_LIMIT_REACHED` |
| Unlicensed install is read-only | **TESTED** | `test_pre_activation_states_deny_new_mutation`; documented in CLAUDE.md |
| `POST /api/admin/employees` has NO licence guard | **RUN — GAP** | Zero `require_license_capability` in `onboarding_routes.py`. A restricted licence can still add staff. Shared with Clinic, so the fix needs a scoping decision |
| `EXTRA_DEVICE` add-on is sellable | **RUN — NOT READY** | Exists in the catalog but sits at `PLANNED`, not `AVAILABLE`. This is the 50 JOD one-time fee |
| Licence activation end to end, on a clean machine | **UNVERIFIED** | Needs the clean-machine rehearsal in `go-live-runbook.md` Part 2 |

## Sync (multi-device)

| Item | State | How |
|---|---|---|
| Outbox, cursors, quarantine, pruning | **TESTED** | Owner suite; `owner/app/sync/pruning.py` has its own tests |
| `flask sync prune-events` scheduled | **BLOCKED** | Code and tests exist. Nothing schedules it. Needs a systemd timer on the droplet |
| Two real devices converging on one shop | **UNVERIFIED** | Never exercised with two physical installs |
| Business day is configurable | **RUN** | Was unreachable — no client could set it, so every install bucketed on each writing device's own clock. Now a card in Admin Center, proved against the real route |

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
| Accounts sync between devices | **DESKTOP ONLY** | Two-stream design; Android builds only the retail stream, and `RETAIL_SYNC_ENTITY_TYPES` deliberately excludes `user`. A cashier made on the desktop cannot log in on the phone |
| Two real devices syncing end to end | **RUN — WORKS** | 2026-09-03, first time ever. Two independent desktop installs (separate `AURA_APP_DATA`, ports 5101/5102) activated on the SAME licence key, each receiving its own `installation_id`. Catalogue crossed A→B (product `c0394392…`, same UUID both sides); a sale rung on B crossed to A (`SALE-000001-7e50f8eb-e3a325c0`) **with fils intact — subtotal 12.345, tax 1.975, total 14.320**, which is also this cycle's money work validated on a running system rather than in a test. No pairing step: activation on a shared key is the whole grouping mechanism |
| Owner relay is live | **RUN — verified properly this time** | Owner started from source against a fresh Postgres database. Discriminating evidence, which the retracted line above lacked: a nonsense path returns **404** while `/api/sync/v1/push` returns **400** with Owner's own `{"reason_code":"INVALID_REQUEST"}` — different responses, so the route genuinely exists and is handling the request |
| Clean-machine install and activation | **RUN — WORKS** | Fresh Owner (venv, migrations to head, RBAC 138 permissions + 5 roles, catalogue, offline policy, signing key generated and activated with a real sign/verify round-trip, `commercial preflight` reporting 51 OK and one WARNING — **corrected: this originally said "fully clean", which was wrong.** The single warning was the missing trust anchor, and it was filtered out by a bad regex in the check meant to surface it. See step 3 of the section below). Fresh till: `needs_setup:true` → first admin → licence `ACTIVE_ONLINE`. Confirmed the gate is real: creating a product was refused before activation and succeeded after |

## Reachability — features with no doorway

| Surface | State | How |
|---|---|---|
| Retail | **TESTED, list empty** | `retail_route_reachability_test.py`. Six defects found and closed this cycle; `KNOWN_GAPS` is now empty |
| Owner | **TESTED, 12 open** | `owner/tests/test_route_reachability.py`. 402 routes; 12 complete backends with no UI — including `customers.update`, three lead actions, and `subscriptions.correct_payment` |

## Operations — all BLOCKED on the owner

- Droplet bring-up and the clean-machine rehearsal (`go-live-runbook.md` Parts 1–2)
- `EXTRA_DEVICE` priced and set `AVAILABLE`
- A systemd timer for `flask sync prune-events`
- A real WhatsApp number — the configured one is Meta **test tier** and can only
  message pre-verified recipients
- The demo Owner subscription was CANCELLED as of 2026-08-17; re-confirm before demoing
- iOS needs a macOS host

---

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
   its next launch pulled the shop's catalogue by itself. **Remaining half:
   Android still takes its relay address as a build-time Gradle property, so
   a phone cannot yet learn it from activation.**

3. **A PHONE CANNOT BE LICENSED AT ALL.** Found 2026-09-04 on a real Mi Note
   10, and it is now the top blocker — it outranks everything below it,
   because an Android till that cannot activate cannot legally do anything.

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

   **What has NOT been checked**, and is where to look next: the specific
   reason code returned by `/_internal/sync-activation`. It is not in logcat
   and is not persisted (the failed activation leaves `licensing_state`
   empty), so it needs temporary instrumentation on the Python verify path.
   The candidates left after the eliminations above are
   `ASSERTION_INSTALLATION_MISMATCH`, `ASSERTION_DEVICE_MISMATCH` and a plain
   `ASSERTION_VERIFICATION_FAILED`.

   Worth noting how long this hid: three desktop installs activated cleanly
   against the same Owner and the same key. Nothing short of a physical
   handset would have found it.

4. **Accounts do not sync to the phone.** Both halves are wired and
   unit-tested, and desktop-to-desktop sync is proven — including staff
   accounts, watched crossing between two tills. The phone leg is **blocked by
   (3)**: an unlicensed device never starts its sync loop, so this could not
   be tested rather than tested and failed.

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
