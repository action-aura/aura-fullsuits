# UI sweep — photograph the product, then look at it

Boots a product for real, drives it with a real browser, and saves a
screenshot of every screen at several widths.

## Why this exists

Everything in this repo had been verified by reading code or running tests.
Nobody had looked at the running product. On 2026-09-01 the first sweep found
four defects in about an hour, while **43 JS suites, 113 Python files and 238
Android tests were all green**:

| Found | Why no test could see it |
|---|---|
| POS totals read `$0.00` beside a `Charge — JD 0.000` button | The spans were seeded with a literal `"$0.00"` in the template and only corrected by `_recalc()`, which runs on cart mutations. An empty cart never mutates, so the till **at rest** was in the wrong currency. |
| All four chart axes hardcoded `'$' + v` | The axis callback is data Chart.js owns. It is never a value this codebase renders into the DOM. |
| `step="0.01"` on cash tendered | Makes a fils amount a step mismatch on a three-decimal currency. Invisible in markup assertions. |
| The admin-device banner filled ~45% of a 390px screen, one word per line | A consequence of a flex **basis**, not of markup. Only a rendered layout shows it. |

The lesson worth keeping: **a money surface is not covered because the money
function is covered.** The formatter was correct and unit-tested throughout.

## Retail

```sh
UI_SHOT_DIR=ui-shots py -3.14 scripts/ui-sweep/retail_ui_sweep.py
```

Self-contained: creates a temp `AURA_APP_DATA`, completes first-run
onboarding through the real signup form, **then** seeds an active licence
(never before signup — see "Gotchas" below), seeds a small catalogue with
real `category_id`s, creates a real cashier account through the product's
own invite/self-setup routes, then walks every sidebar destination at
1440x900 / 1366x768 / 768x1024 / 390x844:

* the **owner** pass, full `SECTIONS` list, at all four widths, **plus** its
  own theme x language matrix (below);
* a **cashier** pass, same section list, one width (1440x900) — proves the
  capability-gated nav actually restricts what a cashier can reach — **plus
  its own theme x language matrix**, same `THEME_MATRIX_SECTIONS`, same
  checks (round 2, item #0: the matrix used to run as the owner only, so a
  theme/RTL bug reachable only through the cashier's permission-message
  branch — its own markup — was never exercised in any theme but light/en);
* a **joined-computer** pass — a device that already holds an Owner-issued
  `installation_id` on its very first boot, which legitimately shows
  "Connecting to your shop…" rather than the signup form. Runs as its own
  subprocess against its own throwaway `AURA_APP_DATA` (see the script's
  `_run_joined_computer_pass` docstring for why it cannot run in-process).
  **Also (round 2) drives THEMES x LANGS on that one pre-auth screen** —
  there is no `SECTIONS` walk here, nothing has joined yet — proving
  `ThemeEngine.apply()` / `AuraI18n.setLang()` do not crash when called
  before `SubsystemApp` exists, a state only this pass ever reaches;
* a **theme x language** matrix — `dashboard` / `pos` / `trial-balance`
  (`THEME_MATRIX_SECTIONS`) across all 5 themes (`light`, `sand`, `dark`,
  `night`, `dusk`) and both languages (`en`, `ar`) at 1440x900, run for the
  **owner and the cashier** (shared by `run_theme_matrix()`, the same
  reason `walk_sections()` is already shared between the owner and cashier
  NAV passes — the two cannot drift apart on how a themed shot is taken or
  checked). Not the full section list at every width for either — see the
  script's own docstring for why that would be ~3000 screenshots.

Every shot is checked, not just saved: `#sub-content` must have rendered a
non-zero box, and (outside the joined-computer pass, where it is the point)
no shot may be sitting on the licensing "Connecting to your shop…" wait
screen. **The process exits 1 if anything failed** — check the exit code,
not just the screenshot count; a broken run used to still exit 0.

A non-zero box alone does not prove the RIGHT screen rendered — the app's own
unknown-section fallback (`subsystem-retail.js`'s "Coming soon.") has one too,
so a mistyped or removed nav id used to pass silently. Every section walked by
`walk_sections()` (owner, cashier and theme passes alike) is additionally
checked against `systems.retail.nav`, read live from the page: the section id
must have a matching nav entry, `#sub-content` must not contain the "Coming
soon." fallback, and `#sub-header-section` must read that entry's real,
translated label. Uncaught JS exceptions (`page.on('pageerror')`) are also
recorded on every page now, not only the owner width loop — the one-time
setup page, the cashier pass, both theme x language passes, and the
joined-computer subprocess's own page all carry the same listener. A
pageerror during setup is recorded and the sweep still runs (matching every
other pass's "record and continue"); an outright exception during setup
(onboarding never completing, seeding failing) is fatal and reported as a
clean `PROBLEMS` line — round 2 closed the one path that used to crash with
a raw Python traceback instead.

Neither guard above can tell a section that loaded its own data from one
whose data fetch failed and got quietly swallowed — the app catches a failed
load in its own `console.error`, which never raises `pageerror`, and renders
its own empty state, which has a real box and the right header. Round 2
(final pass) closed that gap: `page.on('response')` is now wired the same way
`pageerror` is, on every page this file drives, and any `/api/` response
whose status is `>= 500`, or a 4xx other than 401/403, is recorded as
`"<pass> HTTP <status> <method> <path>"`. Proven live: routing
`/api/sub/retail/products` to a real HTTP 500 screenshotted the correct
-looking "No products yet" empty state against a seeded catalogue of six real
rows, and no check before this one said so. 401/403 are allowed only for the
cashier pass and the joined-computer pass (a capability-gated denial there is
the point, not a bug), except `GET /api/auth/session`, which is allowed to
401 on every pass — that route's own docstring says an anonymous caller
getting a plain 401 is its documented contract, and `init()` calls it
unconditionally before every login this file ever does.

Set `UI_SHOT_QUICK=1` for a fast smoke pass (one width, three sections, no
cashier/joined-computer/theme passes) when you only need to prove the
onboarding path itself still works, not the full visual sweep. Set
`UI_SHOT_MUTATION_INJECT_500` to a Playwright URL glob (e.g.
`**/api/sub/retail/products*`) to make every matching request, on every page,
fail with a real HTTP 500 — reproduces the response-watcher mutation proof on
demand. Never set it outside a mutation proof.

Needs `playwright` plus `playwright install chromium` in the interpreter you
run it with. Ports: 5700 (main), 5701 (joined-computer subprocess) —
`UI_SHOT_PORT` / `UI_SHOT_JOINED_PORT` to change either.

## Owner

Owner runs on its own interpreter (`.venv-owner`) and Postgres, so the server
is a subprocess and the browser is driven from the system interpreter.

```sh
sh scripts/ui-sweep/owner_bootstrap.sh          # throwaway DB, migrations, seed, superadmin
py -3.14 scripts/ui-sweep/owner_ui_sweep.py
```

`owner_bootstrap.sh` uses its **own** database (`aura_owner_uishots`) and
drops it first. It deliberately does not touch `aura_owner_dev`, which carries
the demo customer, its subscriptions and the live licensing signing key.

Owner requires TOTP enrolment on first login with no skip — correct, and a
hard gate for automation. The sweep computes the code from the secret the
enrolment page prints (RFC 6238, stdlib only), against its own throwaway
account in its own throwaway database. It will **not** work against an account
that is already enrolled, because the secret is encrypted at rest and is only
ever shown once; re-run `owner_bootstrap.sh` for a fresh one.

## Reading the results

Screenshots are just evidence — the point is to open them. Two habits that
paid off immediately:

* **Compare a value against its neighbour.** The dollar/dinar bug is obvious
  the moment the totals column and the Charge button are in one frame, and
  invisible in any assertion that looks at one of them alone.
* **Count what repeats.** `owner_dupe_check`-style label counting turned "the
  dashboard feels noisy" into "eight KPI labels each render twice on one
  page", which is actionable.

## Gotchas that cost time

* Screenshot on a **selector**, never on `networkidle`. Both apps resolve i18n
  and a status fetch *after* networkidle, so every early frame came out blank
  and looked like a boot failure.
* Take screenshots with `animations="disabled"` and a bounded `timeout`, and
  let a failed frame be recorded rather than kill the sweep — one page with a
  running animation otherwise photographs nothing at all.
* Owner's login form posts to `/auth/login`; `GET /` only 302s there.
* Do not call `page.content()` while a navigation is in flight.
* **Seed the licence AFTER signup, never before** (Phase 0 item #0, found
  2026-09-27). `seed_active_license()` stamps an Owner-issued
  `installation_id` into `licensing.db`. app-shell.js's `_isJoinedDevice()`
  reads that as "this device already joined a shop" regardless of whether an
  admin account exists yet, so a FRESH database with the licence seeded
  first sends `_openFirstRun()` down the `_waitForShopAccount()` branch —
  the "Connecting to your shop…" wait screen — instead of the real signup
  form, and that wait never resolves because no real Owner server exists to
  sync an account down from. The whole run then times out waiting for
  `#su-btn` on every pass. Mutation-proved: reverting the seed to run before
  `onboard()` reproduces exactly this — `Page.wait_for_selector: Timeout
  20000ms exceeded ... waiting for locator("#su-btn")` — and the process
  now exits 1 instead of silently continuing.
* **`create_product` reads `category_id`, not `category`.** A seeding
  fixture that posts a category NAME (as `evidence/capture.py` briefly did)
  is silently ignored by the backend — every product lands with
  `category_id` NULL, and every category shows `product_count: 0`. The
  fixture manufactures the exact "every category is empty" finding it then
  goes on to report. `retail_ui_sweep.py`'s `seed_shop_data()` posts the
  real id and then reads categories back to check `product_count` itself,
  specifically so this cannot regress silently again.
* **The brand intro overlay (`#aura-intro-overlay`) plays once ever**, gated
  on a localStorage key, and sits at `z-index: 100000` over the signup form
  on a fresh browser profile. It tears itself down after ~6.5s on its own,
  so a slow enough onboarding flow would eventually get past it by luck —
  but any screenshot taken before that timer fires shows the intro card
  instead of the screen it claims to capture. Dismiss it explicitly
  (click, or wait for it to detach) rather than relying on the timeout.
* **`products/retail/backend/config.py` reads `AURA_APP_DATA` into a
  module-level global at import time.** Once one `AURA_STANDALONE` backend
  has been imported in a process, changing the environment variable and
  re-importing does nothing — CPython caches the module. A second,
  independently-configured backend (the joined-computer pass here) needs
  its own subprocess, not a second call to `init_app()`.

## Android — ATTEMPTED 2026-09-02, NOT obtained

The Android app remains the one surface in this repo with **no usable visual
evidence**. This is written down so the next attempt starts from where this one
stopped instead of rediscovering the same walls.

What worked:

```sh
F:/Android/Sdk/emulator/emulator.exe -avd Aura_Retail_x86_64 \
    -no-snapshot-load -no-boot-anim -gpu host -memory 3072
./gradlew.bat :app:assembleDebug           # from android/aura-retail
adb install -r -t app/build/outputs/apk/debug/app-debug.apk
adb shell am start -W -n com.actionaura.retail.debug/com.actionaura.retail.MainActivity
```

The app installs, launches, becomes `topResumedActivity`, and renders its
first-run screen ("Welcome to Action Aura / Create your administrator
account"). `am start -W` reported `WaitTime: 12710` — a ~12.7s cold launch,
though an emulator under load inflates that and it is NOT a measurement of the
app on real hardware.

What blocked it: the emulator's own **SystemUI** ANRed continuously
("System UI isn't responding", and earlier "Process system isn't responding"),
on both `-gpu swiftshader_indirect` and `-gpu host`, and could not be dismissed
by tap or by `KEYCODE_BACK`. This machine was simultaneously running Postgres,
Gradle and browser sweeps.

**Why no conclusions were drawn from the screenshots anyway.** The ANR dialog
paints a dimming **scrim** over the whole screen. Every captured frame has it.
The app's first-run screen therefore *appears* very dark, with a dark red
heading and barely-visible field outlines — but that appearance cannot be
separated from the scrim, so it is NOT reported as a finding. Judging a colour
scheme through a modal dim would be exactly the kind of confident-but-wrong
claim the rest of this README exists to prevent.

Two shell traps cost time and are worth keeping:

* `adb exec-out screencap -p > file.png` produced **0 bytes** through Git Bash.
  Use `adb shell screencap -p /sdcard/x.png` then `adb pull`.
* Git Bash rewrites a leading `/sdcard/...` into `C:/Program Files/Git/sdcard/...`
  (MSYS path conversion). Export `MSYS_NO_PATHCONV=1` and
  `MSYS2_ARG_CONV_EXCL="*"` before any `adb` call that takes a device path.

Next attempt should use a **physical device over USB** (`adb devices` with the
phone plugged in), or an otherwise-idle machine. The SDK is at `F:/Android/Sdk`
per `android/aura-retail/local.properties`; the AVD is `Aura_Retail_x86_64`.
