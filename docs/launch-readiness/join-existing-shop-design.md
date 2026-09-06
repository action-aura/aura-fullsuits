# "Join an existing shop" at first run — design, measured, not yet built

Written 2026-09-06. Deferred item from ROADMAP's "two nights on real hardware"
section; this note turns it from a sentence into a plan someone can execute in
a morning, with every premise checked against the code rather than assumed.

## The problem, as it looks to the owner

The owner installs the desktop till, creates their account, activates. They
then install the phone. Today the phone's first run insists on *creating an
admin* — there is no other door — so the owner invents a second account
("phone-admin@…", any password) just to reach the activation screen. Since
2026-09-05 their real account arrives on the phone a few seconds after
activation (one owner login everywhere), and the placeholder admin lingers as
`ADMIN-0001` on that device forever: harmless, confusing, and visible on the
Employees screen of every device (it syncs).

## What the code already allows (verified 2026-09-06)

1. **Activation needs no signed-in user.** `POST /api/licensing/activate`
   (`commercial_runtime/licensing_contracts/routes.py:138`) carries no
   `mt_login_required`; it takes the licence key, mints the device key if
   needed, and talks to Owner. A fresh install with zero users can activate.
2. **First run stops demanding setup as soon as ANY admin with a real
   password exists locally.** `onboarding_status`
   (`commercial_runtime/identity/onboarding_routes.py:~200`) answers
   `needs_setup: true` only when no admin row exists or its hash is
   `PENDING`/blank. An owner row that ARRIVED BY SYNC carries its
   `pbkdf2_sha256` hash, so once it lands, `needs_setup` is already `false`
   and the app shows the ordinary sign-in.
3. **The owner's account does arrive.** Proven on the Mi Note 10 and on two
   desktop tills (`sellability-status.md`, blocker 4): the registry stream
   applies the `user` row (re-numbering a colliding employee code) and its
   permission rows.
4. **The one wrinkle is on the desktop:** the relay address learned at
   activation "takes effect at next launch" (config is read at import;
   `sellability-status.md`, "A customer can turn sync on"). Android reads
   `sync_relay_base_url` off `/api/licensing/status` and starts syncing
   immediately.

5. **One real gap, found by reading the resolver rather than assuming:** the
   sync applies a pulled `user` row under THIS device's own company id, and
   `local_company_id_from_registry()` (`commercial_runtime/sync/
   sync_service.py:~345`) takes it from `company_settings`, falling back to
   the admin row, and returns `None` when neither exists — which
   `_apply_event` treats as "cannot apply yet". `create_admin` is what
   writes `company_settings` today. So on a device that joined WITHOUT
   creating an admin, the owner's row could never land: the receiver has
   no company id until an admin exists, and the admin arrives by sync.
   Activation does not seed it either: the identity rebind
   (`company_rebind.rebind_company_id_after_activation`) MOVES rows onto the
   Owner-issued key and answers `already_bound` for an install with none.

   The fix is small and principled: **once activated, the tenant key IS the
   licence id.** In `products/retail/backend/app.py::_on_licence_activated`
   (and Clinic's twin), after the rebind: if `company_settings` has no row
   and no admin exists, insert `company_settings.company_id =
   owner_issued_company_id()` (`company_rebind.py:92`, the same value the
   rebind targets). Then the resolver answers the licence id, the owner's
   row applies under it, and `needs_setup` flips to `false`. A device that
   *did* create an admin is untouched (the row exists; the rebind moved it).

   **Built 2026-09-06** (`company_rebind.seed_company_settings_for_joining_
   device`, called from `rebind_company_id_after_activation`; five tests,
   including "a pulled owner row applies on the seeded device"; disabling
   the seed turns three red). The remaining half is the door on each client.

So the missing pieces are a *door* and one seed row, not a mechanism.

## The flow

First-run screen gains a second choice next to "Set up a new shop":

> **Join an existing shop** — enter the licence key your shop already uses.

1. `POST /api/licensing/activate {license_key}` (no session; already so).
2. Show "Joining your shop…" and poll `GET /api/onboarding/status` until
   `needs_setup` is `false` — that is the owner's account arriving — with a
   90-second ceiling and a plain message if it does not ("Could not reach your
   shop's server. Check the connection and try again."), never a silent spin.
   - Desktop: because the relay address only takes effect on the next launch,
     the desktop path either (a) restarts the app after activation — the
     launcher already knows how — or (b) starts the sync services in-process
     right after activation. (b) is the better product; (a) is the honest
     minimum if (b) turns out to touch `init_app()` in ways the tests do not
     cover. Decide by reading `products/retail/backend/app.py`'s
     `_SYNC_RELAY_URL_IS_USABLE` block before starting.
3. Show the ordinary sign-in. The owner signs in with the account they
   already have.

Nothing is created on the joining device: no placeholder admin, no
`ADMIN-0001` collision, no extra row on anyone's Employees screen.

## The desktop screen, as it actually is (read 2026-09-06)

`products/retail/frontend/app-shell.js`: `checkAuthAndSetup()` (~1243) asks
`/api/onboarding/status` and shows `showSetupModal()` (~1265) when
`needs_setup` is true. That modal already collects the licence key next to
the account fields when `_needsActivation(lic)` says so, and `_setupSubmit()`
(~1354) creates the admin and THEN activates. So "Join" is a **mode of the
same modal**, not a new screen: a link under the title — "Already have a
shop? Join it with your licence key" — hides name/company/email/password,
keeps the key field, and submits to `/api/licensing/activate` alone.

Two states follow, and both need words on screen:

- **Activation failed** → stay in join mode, show the server's reason (the
  activation screen's existing mapping), offer "Set up a new shop instead".
- **Activation succeeded** → on Android the coordinator starts on the next
  status read and the owner's row lands in seconds, so polling
  `/api/onboarding/status` until `needs_setup` is `false` (90 s ceiling) and
  then showing sign-in is enough. On the desktop the discovered relay
  address only takes effect at the **next launch**, so the honest sequence
  is: "Connected to your shop. Restart Aura to finish joining." — and, on
  that next launch, a device that is ACTIVE but still has no admin must show
  "Connecting to your shop…" (polling `needs_setup`) instead of the setup
  modal again. Without that waiting state the user would be offered setup a
  second time while their account is seconds away. Starting the sync
  services in-process after activation would remove the restart; read
  `app.py`'s `_SYNC_RELAY_URL_IS_USABLE` block before deciding, and prefer
  the restart if that block resists.

## What it must NOT do

- Must not weaken `create_admin`'s gate ("no valid admin exists yet") — the
  join path never calls it.
- Must not let a device with a *wrong* key linger half-joined: a failed
  activation returns to the first-run choice with the server's reason
  (`LICENSE_NOT_FOUND`, `DEVICE_LIMIT_REACHED`, …), the same codes the
  activation screen already renders.
- Must not skip the device-limit rule: joining consumes a device slot exactly
  as activating does today (it IS activating).

## Files it touches (estimate: one day — the seed row and its tests are the half that must be adversarially verified)

| Layer | File | Change |
|---|---|---|
| Activation hook — **built** | `commercial_runtime/identity/company_rebind.py::seed_company_settings_for_joining_device`, called from `rebind_company_id_after_activation` (shared by Retail and Clinic) | premise 5: seed `company_settings` with the Owner-issued id when no company and no admin exist; five tests in `test_registry_v4_company_rebind.py`, an install WITH an admin proven untouched |
| Desktop first run — **built** | `products/retail/frontend/app-shell.js`: `_toggleJoinMode`, `_joinSubmit`, `_openFirstRun`, `_isJoinedDevice`, `_waitForShopAccount` | the join mode of the setup modal, the restart message, the next-launch wait; both `init()` and `checkAuthAndSetup()` go through `_openFirstRun()` |
| Desktop sync start | `products/retail/backend/app.py` | relies on the restart (the "Connected to your shop. Restart Aura" screen); in-process start not attempted |
| Android first run — **built 2026-09-06, not yet run on the handset** | `ui/FirstRunDecision.kt` (pure decision), `ui/screens/JoinShopScreens.kt` (choice + waiting screens), `AppRoot.kt` phases `JOIN_CHOICE`/`JOINING` | on Android the key comes BEFORE the account, so after activation the app cannot tell a new shop's first device from a joining one: it asks once ("Is your shop already set up on another device?"); the waiting screen polls `needs_setup` every 2 s with a 120 s ceiling and never calls `createAdmin`. 303 unit tests green; the installation-id term of the decision is mutation-proved (three checks go red without it). APK assembled; the phone runner installs it when the handset is next plugged in |
| Tests — **built** | `products/retail/tests/retail_join_shop_modal_test.js` (15 checks, drives `init()` itself); `FirstRunDecisionTest.kt` + `JoinShopWiringContractTest.kt` (Android) | the desktop checks include the no-Owner, pending-approval and boot-path cases the first cut missed; `create_admin` stays gated (never called by the join path, measured in the browser) |
| Proof drivers | `scripts/ops/join_e2e.py` (API, fourth till), `scripts/ops/join_door_e2e.py` (Chromium, fifth and sixth tills) | run against a fresh till on `:5013`/`:5014`; a device slot per till through the audited `add_devices` op (`scripts/ops/owner_rehearsal_add_one_device.py`) |
| Docs | `sellability-status.md` (row added), `sunday-demo-runbook.md` §1 (caveat reworded) | the phone's own admin caveat stays until the Android door is run on the handset |

## Measured on a real fresh till, 2026-09-06 13:50

`scripts/ops/join_e2e.py` against a fourth desktop till (`:5012`, empty data
directory, no relay URL configured, one extra device slot added through the
audited `add_devices` op): `needs_setup: true` with zero users → `POST
/api/licensing/activate` with the shop's key and **no session** → `SUCCESS`,
and `company_settings` on the till read back as the licence id → restart
(the discovered relay address takes effect at launch) → on the very first
status read after boot `needs_setup` was already `false`, the registry held
the shop's four accounts (both owners as `ADMIN-0001`/`ADMIN-0002`, both
cashiers, all under the licence id) → the desktop's owner signed in on the
fresh till: `200`, `ADMIN-0001`, `admin`. No placeholder admin anywhere.

## The desktop door, measured in a real browser, 2026-09-06 13:45

`scripts/ops/join_door_e2e.py` drives the real page in Chromium (Playwright)
on a fresh till, recording every `/api/` request the page makes:

- **join phase** (fifth and sixth tills): first-run modal → "Already have a
  shop? Join it with your licence key" → title becomes "Join your shop",
  name/email/password hidden, key field kept → "Join shop" → "Connected to
  your shop. Restart Aura to finish joining." `create-admin` calls: **0**;
  `activate` calls: **1**; `company_settings` on the till = the licence id;
  `users` empty.
- **after-restart phase**, first attempt (fifth till): **caught a real
  defect.** The page opened the setup modal AGAIN. The join-aware branch
  had been wired into `checkAuthAndSetup()` (the 401 path) only, and its
  unit test called that function directly, so it passed — while on a real
  launch `init()` reads `needs_setup` first and opened the setup modal
  itself. Fixed by making `_openFirstRun()` the one decision both callers
  go through; `retail_join_shop_modal_test.js` now drives `init()` itself,
  and re-creating the defect turns that check red with the exact symptom.
- **after-restart phase**, second attempt (sixth till, fixed build): the
  next launch came up straight on the ordinary sign-in screen — the owner's
  account had synced before the page finished loading — and the desktop's
  owner signed in there through the real form; the shell rendered; zero
  `create-admin` calls across both launches. The "Connecting to your shop…"
  wait was not observed live for that reason; it is pinned by the unit test
  driving `init()` with `needs_setup: true` and an `ACTIVE_ONLINE` payload.

One more thing the first cut got wrong, caught by reading the guard rather
than the test: it entered the wait for every state that was not
pre-activation — so a build with no Owner wired up (`NOT_CONFIGURED` with a
`detail`) would have sat on "Connecting to your shop…" for the full 120 s
before being offered setup. The guard now keys off the status payload's
Owner-issued `installation_id`, minus the pending-approval state
(`_isJoinedDevice()`); both halves are mutation-proved.

The Android door is built the same afternoon (see the files table) and waits
for the handset.

## What to measure before calling it done

Run it on the real phone: wipe the app's data (or a fresh device), choose
Join, enter the rehearsal key, and read the verdict off the handset's
`registry.db users` (one row: the owner, `ADMIN-0001` from the desktop) and
`licensing_events` (`ACTIVATION_SUCCEEDED`). If the owner has to type anything
other than the key and their own password, it is not done.
