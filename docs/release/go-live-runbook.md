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
    flask seed-catalog                   # products / platforms / release channels / entitlement defs / DRAFT add-ons
    flask seed-plans                     # ONE placeholder $99 plan per product -- run AFTER seed-catalog
    flask seed-offline-policy            # how long an install may run unreachable

Correction (launch-readiness W0.3, 2026-09-03): the line above used to read
`flask seed-catalog # plans / prices / add-ons` — that is wrong.
`seed-catalog` seeds products, platforms, release channels, entitlement
definitions, and add-ons left DRAFT/PLANNED; it seeds **zero plans**. A
licence is issued against a plan (`issue_license_direct(plan_id=...)`), so
without a plan a fresh Owner cannot cut its very first key, and there was no
`seed-plans` command to fix that — the only way through was building a plan
by hand through the UI, with nothing telling you that step existed. Run
`flask seed-plans` right after `seed-catalog`; it is idempotent and prints a
JSON summary like the other seed commands. Its price ($99.00 flat, ONE_TIME,
per product) is a **placeholder** — set the real price from the Owner UI
(catalog → plan → add price) before selling anything for real.

### 1.3 Signing keys and the trust anchor

    flask licensing verify-signing-key-health
    flask licensing generate-signing-key   # first boot only, if no key exists yet
    flask licensing activate-signing-key <key_id>
    flask licensing export-public-keys
    python scripts/generate_trust_anchor.py --owner-url https://owner.actionaura.me/api/licensing/v1 \
        --out commercial_runtime/licensing_contracts/trust_anchor.json

`export-public-keys` is what produces the material the PRODUCTS trust, but
printing it to stdout is not enough — a product installer only ever reads
`commercial_runtime/licensing_contracts/trust_anchor.json`, a gitignored,
build-time-only file that **nothing generates automatically.**
`scripts/generate_trust_anchor.py` is the step that writes it, and it must be
run **once per Owner instance**, before the first product build. Skip it and
the failure is silent until the worst possible moment: activation reaches
Owner, Owner signs the assertion correctly, and the client rejects its own
Owner's answer with `UNKNOWN_SIGNING_KEY` — a signature-shaped error for a
"you forgot a file" problem. `flask commercial preflight`'s
`trust_anchor_matches_active_key` check exists specifically to catch this
before it reaches a customer: WARNING if the file is simply missing (names
the exact command above), FAIL if it exists but references a different key
than the one Owner is currently signing with. Confirm the exported keys and
the anchor file agree before issuing a single licence.

If a key must be rotated: `generate-signing-key` → `activate-signing-key` →
re-export → **re-run `scripts/generate_trust_anchor.py`** → rebuild the
installer. Rotation without regenerating and re-shipping the anchor breaks
every install that has not checked in yet — preflight will FAIL on
`trust_anchor_matches_active_key` in exactly this state, which is the signal
to regenerate before building anything.

### 1.4 The commercial model

The owner's price list (WhatsApp, 2026-09-05): a licence includes **two
devices** (the manager's phone + one till) at **250 JOD**; each extra device
**50 JOD**; a new **branch 250 JOD**. Both meters are enforced — devices
server-side at activation (`DEVICE_LIMIT_REACHED`), branches on the till
through the `max_branches` entitlement (`403 BRANCH_LIMIT`) — and every
number is **catalogue data, not code**. On a fresh Owner the seed gives you
the definitions and inert add-ons only; the values are set once, in this
order (the rehearsal did it through the same services with
`scripts/ops/owner_rehearsal_pricing.py` and `owner_rehearsal_branch_entitlements.py`):

1. Plan: `included_device_count = 2`, current price **250 JOD** (the
   placeholder plan seeds 2 devices and a 99 USD price — re-price it or
   create the real plan from the catalogue UI). Confirm the billing period
   with the owner (annual vs one-time is an open question).
2. Plan entitlement `max_branches = 1`.
3. Add-on `EXTRA_DEVICE`: price 50 JOD, status **AVAILABLE**.
4. Add-on `EXTRA_BRANCH`: price 250 JOD, entitlement `max_branches = 2`,
   status **AVAILABLE**. A customer with more branches gets a per-licence
   `max_branches` override. Attaching the add-on to the subscription is what
   raises the till's limit at its next check-in — verified end to end on
   2026-09-06 (`scripts/ops/branch_limit_e2e.py`).

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

**Steps 1 and 2 are now closed. Steps 3 to 10 are not.** Recorded here on
2026-09-08 so nobody re-does the easy half or assumes the hard half is done.

What was actually measured: the installer was built from a `git archive HEAD`
snapshot rather than the working tree, installed silently to a fresh prefix
(exit 0, 920 files, 93.3 MB), and the INSTALLED copy launched with every
inherited `AURA_*` variable stripped and `AURA_APP_DATA` pointed at a directory
that had never existed. It served `GET /api/health` and `GET /` -> 200, and
`retail.db` migrated v0 -> v26 with `ensure_schema_version()` taking its
integrity-checked backup first. Silent uninstall exit 0, program files gone,
business data kept. The guard that keeps a scripted uninstall from deleting
that data was mutation-proved: removed, the uninstall blocks on a hidden modal
until killed at 180s; present, it exits 0 in 4.6s.

What that does NOT establish: this removed the dev ENVIRONMENT from the
picture, not the dev MACHINE. It still ran on the build laptop, which has
WebView2, VC++ runtimes and a display stack a customer's machine may not. So
the sentence above — "not a dev box" — is still owed, and steps 3 to 10 have
never been run anywhere. Expect step 4 to be the first thing that breaks.

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
