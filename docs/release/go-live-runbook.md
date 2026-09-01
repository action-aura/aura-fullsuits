# Go-live runbook — droplet bring-up and the first real rehearsal

Written 2026-08-30. Every command below was verified to EXIST in this repo
(`owner/app/cli.py`) before being written here. What each does on the live host
is not verified — that is what running it tells you.

Two parts, in order. **Part 1 is the server. Part 2 is the rehearsal that
decides the launch date.** Do not skip Part 2 or shorten it: every defect found
in this codebase during the 2026-08-30 session was found by reading code, and
reading has now been exhausted. What remains will only surface by running.

---

## Part 0 — the one thing to understand first

`flask commercial preflight` is the diagnostic. It reports what is wrong rather
than guessing. **Run it before anything else and after every change below.** It
is designed to be run repeatedly and it names its own fixes in its output
(e.g. *"Fix: 'flask seed-rbac'"*).

All `flask` commands run on the droplet, from the Owner app directory, with the
production environment loaded — the same environment gunicorn uses. Running them
with the wrong env var set will configure the wrong database, silently.

---

## Part 1 — the droplet

Host: `aura-retail-demo`, 161.35.219.243 — Owner CC production (`aura-owner`,
gunicorn on 127.0.0.1:5551), served at `owner.actionaura.me`.

### 1.1 Diagnose

    flask commercial preflight

Read the whole output. It checks RBAC seeding, signing-key health and
role/permission drift. Two problems were recorded against this droplet
previously and both are things preflight reports:

* **RBAC was never seeded** — no genuine `is_super_admin` account.
* **`trust_anchor.json` was stale** — the products verify licence assertions
  against it, so a stale anchor means activation fails on the customer side
  with a signature error, not an obvious server error.

Treat those as *likely*, not certain. Preflight tells you the truth today.

### 1.2 Fix what it names

    flask seed-rbac                      # roles + permissions, the fix preflight names
    flask create-superadmin              # a real admin account, if none exists
    flask seed-catalog                   # plans / prices / add-ons
    flask seed-offline-policy            # how long an install may run unreachable

### 1.3 Signing keys and the trust anchor

    flask licensing verify-signing-key-health
    flask licensing export-public-keys

`export-public-keys` is what produces the material the PRODUCTS trust. If the
anchor shipped inside the installer does not match the key Owner is signing
with, **every activation fails and the failure looks like a customer problem.**
Confirm these agree before issuing a single licence.

If a key must be rotated: `generate-signing-key` → `activate-signing-key` →
re-export → rebuild the installer. Rotation without re-shipping the anchor
breaks every install that has not checked in yet.

### 1.4 The commercial model

The paid meter is **devices**: 2 included, 50 JOD per additional device. The
device limit is ALREADY enforced server-side at activation
(`DEVICE_LIMIT_REACHED`), and an `EXTRA_DEVICE` add-on already sits PLANNED in
the catalog. Pricing it is a catalog action, not a code change.

    flask commercial device-limit-scan   # who is at or over their limit

### 1.5 Sanity

    flask commercial preflight           # again — expect it clean
    flask commercial reconcile
    curl -s https://owner.actionaura.me/healthz

---

## Part 2 — the rehearsal

**This is the step that decides whether the product is sellable.** It has never
been done end to end.

### 2.1 Build the desktop artefact

From the repo root, on a Windows machine:

    pyinstaller products/retail/packaging/aura_retail.spec --noconfirm

PyInstaller 6.21 is present on this dev machine and the spec's own header still
says *"NOT YET BUILD-VERIFIED in this environment"* — that comment predates any
build. The exe HAS been produced and run before (the known desktop wiring gaps
were found by running it), so treat the comment as stale but confirm.

The installer step needs **Inno Setup**, which is NOT installed on this dev
machine:

    ISCC.exe products/retail/packaging/aura_retail_setup.iss

Install Inno Setup 6 first. Code signing is a separate step — see
`docs/release/windows-code-signing-guide.md`. An unsigned installer will make
Windows SmartScreen warn every customer at install time; decide deliberately
whether to sign before or after the first pilot.

### 2.2 The run, on a CLEAN machine

Not a dev box. A machine that has never had Python, the repo, or an app-data
directory. Half of what this catches is "it only worked because the dev machine
already had X".

1. **Install** from the installer. Not from source.
2. **Launch.** It should open its own window (pywebview, falling back to
   Edge/Chrome `--app`, then the default browser).
3. **First-run setup** — create the company and the owner account.
4. **Activate a licence** issued from Owner in Part 1.
   *Expect this to be the first thing that breaks.* Before activation the
   install is READ-ONLY by design: you can view products, but POSTing a sale or
   opening a cash session returns 403 `LICENSE_INACTIVE`. That is the licence
   gate working, not a bug — do not "fix" it.
5. **PIN THE BRANCH.** Admin Center → *This Device's Branch*.
   **Do this before ringing anything.** Until a till is pinned it files sales
   under the company's FIRST branch and decrements THAT branch's stock. For a
   single-branch shop this is invisible and harmless. For anyone with two
   branches it is silently wrong data, and no report will look odd.
6. **Open a cash session** with a float.
7. **Ring a sale.** Scan a barcode, take cash, print/inspect the receipt.
   Check the receipt carries the SHOP's name and logo, not Action Aura's.
8. **Ring a return** against that sale.
9. **Close the drawer.** Count it deliberately SHORT, so a variance is
   recorded, then approve it from an owner account — the cashier who counts
   must not be able to approve their own shortfall.
10. **Second device.** Install on a second machine, activate under the SAME
    licence, pin it to a DIFFERENT branch, ring a sale on each, and confirm
    each device eventually shows both sales, filed under the correct branch.

### 2.3 Watch for these specifically

Each is a real, verified property of this codebase — worth confirming rather
than assuming:

* **Sales filed under the right branch** after pinning (step 5). Check both the
  sale AND the stock movement.
* **Offline selling.** Pull the network mid-shift and keep selling. It is
  supposed to keep working. Reconnect and confirm it catches up.
* **Two devices adding staff.** Create an employee on each till at roughly the
  same moment. Employee numbers are minted from a LOCAL count against a UNIQUE
  constraint, so both tills can mint the same one; the loser is quarantined and
  **there is no screen that shows quarantine**, so the symptom is a new hire who
  exists on one till and nowhere else. This is a known, accepted gap on the
  owner-creates-staff path. Confirm whether it bites you in practice.
* **A cashier without discount permission selling a promoted item.** It must
  work. If it 403s, the promotion gate is judging the wrong number.
* **An expired or restricted licence.** Confirm it goes READ-ONLY and preserves
  data rather than locking the shop out of its own history.

### 2.4 Record failures properly

For each: what you did, what you expected, what happened, and the exact error
text. "It didn't work" cannot be acted on. A screenshot of the till plus the
line from the log is enough.

---

## Known gaps you will meet, so they are not surprises

* **Restaurants cannot use this yet** — no kitchen tickets, no split tender,
  and no modifier PICKER at the till. The modifier schema and config API did
  ship (retail v26), but the POS picker and the cart merge-key rework were
  deliberately cut to protect the money path, so a modifier can be configured
  and never sold. Do not pilot in a restaurant.
* **No inter-branch stock transfers.** Branches exist and stock is
  branch-scoped, but there is no way to move stock between them. (Verified
  2026-09-01: zero `transfer` routes in retail_api.py.)
* **Loyalty accrues but cannot be spent.** `customers.loyalty_points` goes up
  on every sale and nothing in the product can redeem it. Do not promise a
  customer a points scheme.
* **`POST /api/admin/employees` carries no licence guard**, so staff can be
  created on a restricted licence. Logged as an audit item, not yet fixed.
* **iOS needs a macOS host** that does not exist in this environment.
* **The AI assistant** runs on a $48/month droplet at roughly 30 seconds per
  answer, and is weakest at Arabic — the language that matters most here. See
  `docs/launch-readiness/infrastructure.md`.
