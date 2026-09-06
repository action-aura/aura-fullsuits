# Sunday demo runbook — Aura Retail on a laptop and a phone

Written 2026-09-06 from the rehearsal that actually ran on this laptop and a
Mi Note 10 the night before (see `sellability-status.md`, "Multi-device" and
"What the owner asked for"). Every step below was executed at least once
against the real artefacts; where a step was only exercised through the API,
it says so.

## 0. Ten minutes before — bring the rehearsal up

From the project folder, one command, safe to run twice:

    powershell -File scripts\ops\rehearsal_up.ps1

It starts the Owner Control Center on `127.0.0.1:5551` (rehearsal database,
advertising itself as the sync relay), the laptop till on `:5010`, the second
till on `:5011`, opens the phone tunnel (`adb reverse tcp:5551`), starts the
phone watcher and the battery guard, and prints `port …: UP` for all three.

Then plug the phone in by USB and, if it asks, allow USB debugging. The
watcher installs the current app build (data kept), sets the currency to JOD
and relaunches the app. **The phone reaches the Owner server only through the
cable** in this rehearsal (the app is baked to `http://127.0.0.1:5551`); if
the phone was unplugged or the laptop slept, run the command above again —
the tunnel does not survive that, and the phone then backs off for minutes.

Accounts that exist on the rehearsal licence (`AURA-RET-1-5P2G-39EP-XQ9T-K8ZC-FEZG`):

| Who | Email | Where it works |
|---|---|---|
| Laptop owner (admin) | `desk-owner@rehearsal.local` | laptop till, second till **and the phone** |
| Second-till owner (admin) | `third-owner@rehearsal.local` | everywhere |
| Cashier | `synced-cashier-1788568313@rehearsal.local` | everywhere |

Passwords are in `scripts/ops/two_till_owner_login.py` — rehearsal only.

## 1. Staff: create an account on the laptop, sign in on the phone

Run on 2026-09-05 end to end, including on the phone's own screen.

1. Laptop till (`http://127.0.0.1:5010`), signed in as the owner: **Employees →
   Invite**. Email, role **Cashier**, Create. A setup link appears.
2. Open the setup link, set a password (20 seconds).
3. Phone: sign in with that email and password. The account arrives on the
   phone within about 5 seconds of step 1; the password within seconds of
   step 2.
4. If you want to show the reverse: create the cashier **on the phone**
   (Employees, same screen) and sign in on the laptop. Exercised through the
   phone's API on 2026-09-06 (`scripts/ops/phone_offline_and_staff.py`).

The phone no longer has an admin of its own: on the evening of 2026-09-06 it
was wiped and joined the shop through the new first-run door (licence key →
"Is your shop already set up on another device?" → Yes → "Connecting to your
shop…" → the ordinary sign-in), so the owner is `ADMIN-0001` on the handset
exactly as on the laptop. The laptop's first-run screen has the same door
("Already have a shop? Join it with your licence key"). If asked how a second
device is added: the key and your own password, nothing else.

## 2. Products: create on the laptop, find on the phone, sell on the phone

Run on 2026-09-05 (`scripts/ops/two_device_convergence.py`).

1. Laptop: **Products → Add**. Name, SKU, price (three decimals — the dinar
   has fils), then **Stock adjust** to put stock on the branch.
2. Phone: **Products** tab — same item, same price, same stock, within ~10 s.
3. Phone: **POS** → add the item → **Charge** (cash). The phone shows the
   subtotal before tax; the final total is what the server charges — that is
   deliberate (`sellability-status.md`, money path, "label made honest").
4. Laptop: **Sales** shows the phone's sale; the product's stock is down by
   the quantity sold. Under a minute.

Then Ahmed's part: barcode scanning (real scanner on the laptop, camera on the
phone), variants, import from a spreadsheet — all under `Products`.

## 3. One shop, one set of settings

Run 2026-09-05/06. Change the currency on the laptop (**Settings → Credit /
currency**); the phone shows the new symbol on its next screen load (~10 s).
Change it back. Same for tax mode, credit defaults, business day and branding
text. The logo does not travel (by design — it is a large blob).

## 4. Offline (optional, 3 minutes)

Exercised through the phone's API on 2026-09-06; the same thing through the
screen is the natural demo:

1. Unplug the phone (its only link to the server in this rehearsal).
2. Ring a sale on the phone. It completes; stock drops on the phone.
3. Plug it back in and run `scripts\ops\rehearsal_up.ps1` again (re-opens the
   tunnel). The sale appears on the laptop within about a minute — sooner if
   you restart the phone app, which resets its retry back-off.

## 5. The lead finder (Owner Control Center)

Owner CC at `http://127.0.0.1:5551`, **Leads → Discover**: type a segment and
an area, get real businesses with the phone numbers Google Maps lists —
never invented ones. **It is off until a Google Places key is configured**
(`scripts/ops/setup_places_key.ps1` after `gcloud auth login`); without the
key the screen explains that instead of failing.

## 6. Pricing, as the owner set it (2026-09-05)

Already in the rehearsal Owner's catalogue: a licence includes **two devices**
(the manager's phone + one till) at **250 JOD**; each extra device **50 JOD**
(sellable); a new branch **250 JOD** is in the catalogue but marked Planned
until the product enforces a branch limit. Open: whether 250 JOD is per year
or one-time.

## State of the code this runbook describes

As of 2026-09-06 05:45: the canonical retail runner passed **172 of 172**
files on the finished tree (`python products/run_all_tests.py retail`),
including the settings-sync, owner-login re-numbering, branch-limit and
branch-rename work from the same night; the full Android unit suite is green;
the Android debug APK at `android/aura-retail/app/build/outputs/apk/debug/
app-debug.apk` was built from that tree and carries the same backend. The full Owner suite the same night: 1262 passed, 3 failed — the
same three known failures as the day before (two tests that assume the day
does not change mid-run, and one that looks for a venv at the main checkout
path when run from a worktree), none from the night's changes.

## Test rows the rehearsal left behind

The night's proofs wrote real rows on the laptop till. Cleaned up on
2026-09-06 05:40 through the real routes, which doubled as one more proof:
the three `SYNC-0905*` products and the two "… 0905" customers were deleted
on the laptop (soft-deletes) and vanished from the second till within 5 s
(`scripts/ops/cleanup_test_rows.py`); the test branch was renamed to "Second
Branch" through the new Edit control (a branch cannot be *retired* yet, only
edited). What remains is honest history: the sales `SALE-000004…` and the
offline one, the customer "tareq", "Second Branch", and the staff accounts.
The phone receives the same deletions on its next sync. The presentation
package under `aura-retail-demo/` has its own data folder and is unaffected.

## If something looks wrong

- "Access denied to retail" on a staff account → the app on that device is
  older than 2026-09-05; reinstall (the watcher does it) — this was a real
  bug, fixed at the root.
- Phone shows "$" → its currency setting; `python scripts\ops\phone_set_currency.py JOD`
  or Settings on the laptop (it syncs).
- Phone not syncing → the tunnel: `adb reverse tcp:5551 tcp:5551`, then
  restart the phone app.
- Nothing listening on 5551/5010 → `scripts\ops\rehearsal_up.ps1`.
