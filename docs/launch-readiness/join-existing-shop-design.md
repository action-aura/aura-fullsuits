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
| Activation hook | `products/retail/backend/app.py::_on_licence_activated` (+ Clinic's) | premise 5: seed `company_settings` with the Owner-issued id when no company and no admin exist; mutation-prove that an install WITH an admin is untouched |
| Desktop first run | `products/retail/frontend/onboarding.js` (or wherever `needs_setup` is rendered — grep `needs_setup`) | the second choice, the key form, the polling state, the two messages |
| Desktop sync start | `products/retail/backend/app.py` | (b) above, or rely on the restart |
| Android first run | `android/aura-retail/app/src/main/java/com/actionaura/retail/ui/AppRoot.kt` | the same second choice on the setup phase; the coordinator already starts on the status read |
| Tests | `products/retail/tests/retail_join_existing_shop_test.py`; a Kotlin contract test for the AppRoot phase | activate with no users → status flips to `needs_setup:false` once a synced admin row is applied (seed it through `SyncService._apply_event`); a wrong key returns the reason; `create_admin` stays gated |
| Docs | `sellability-status.md` blocker 4 paragraph, `sunday-demo-runbook.md` §1 | remove the "each device still creates its own admin" caveat once run on the phone |

## What to measure before calling it done

Run it on the real phone: wipe the app's data (or a fresh device), choose
Join, enter the rehearsal key, and read the verdict off the handset's
`registry.db users` (one row: the owner, `ADMIN-0001` from the desktop) and
`licensing_events` (`ACTIVATION_SUCCEEDED`). If the owner has to type anything
other than the key and their own password, it is not done.
