# Per-seat licensing + chain oversight — design

**Status:** DESIGN ONLY. No source file changed. Written 2026-08-30 against
`feat/launch-readiness` (worktree `ci-hardening-w0.3-continue`), retail schema
v22 on disk, v23 claimed for promotions, registry schema v6 on disk.

**Question index:** (a) §2.1 · (b) §1.2 · (c) §2.6 · (d) §2.5 · (e) §2.7 ·
(f) §2.8 · (g) §2.9 · (h) §5.1 · (i) §5.2 · (j) §5.4 · (k) §5.3 · (l) §6.3

---

## 0. Corrections to the brief's "verified facts" — read this first, it changes the size of the job

Three of the given facts are wrong in the direction that **saves money**, and one
is wrong in the direction that hides a hole. All verified by reading code, file:line cited.

1. **"The lease carries no entitlement counts of any kind" — FALSE in the way
   that matters.** The signed assertion payload allowlist
   (`commercial_runtime/licensing_contracts/assertion_verifier.py:66-83`)
   already admits an open **`entitlements`** object *and* a top-level
   `allowed_device_count`. Activation persists it
   (`licensing_contracts/activation.py:242`), **every check-in refreshes it**
   (`checkin_scheduler.py:413`), it lands in `licensing_state.entitlements_json`
   (`state_repository.py:43`), `flask_guard.py:108-117` re-reads it **on every
   guarded request**, and `capability_guard.evaluate_capability` already takes a
   `required_entitlement` argument. On the Owner side there is a complete,
   tested resolution engine: `owner/app/licensing_service/entitlements.py::
   resolve_entitlements` (precedence plan → add-on → license override,
   deny-by-default typed defaults), fed verbatim into every signed assertion by
   `owner/app/licensing_service/assertions.py:59`, re-resolved at every check-in
   (`checkin.py:109`). The catalog even seeds an integer `max_devices`
   entitlement definition and a PLANNED `EXTRA_DEVICE` add-on
   (`owner/app/catalog/services.py:36-59`).
   **What is actually missing is exactly two things:** a `max_users` entitlement
   *definition/value* on the Owner side, and a *numeric reader + enforcement
   point* on the product side. The transport, signing, verification, storage,
   refresh, and per-request read all exist. This is a week of work, not a quarter.

2. **Devices are ALREADY a charged, enforced axis.** Owner refuses activation
   beyond the licence's device limit — `owner/app/licensing_service/
   activation.py:241-242` raises `DEVICE_LIMIT_REACHED` via
   `resolve_effective_device_limit`, and there is commercial machinery for
   device slots (`owner/app/commercial_ops/device_slot_ops.py`,
   `commercial_sales/device_policy.py`). "If I have 5 POS devices" is a limit
   the system already enforces server-side at activation. Nothing to build there.

3. **Sync already spans physically separate sites, not just one shop's LAN.**
   The relay is Owner-hosted (`owner/app/sync/routes.py`), reached over the
   internet by `commercial_runtime/sync/relay_client.py`, and the event stream
   is scoped **per licence**: "resolve license_id from the VERIFIED
   installation's own relationship — never from client input"
   (`owner/app/sync/routes.py:19`, pull unconditionally
   `.where(SyncEvent.license_id == license_id)`, routes.py:423). Five stores'
   devices activated under one licence converge into one dataset wherever they
   are. See §5.3.

4. **The hole:** the retail restriction matrix's claim that a restricted licence
   blocks "settings/staff changes" is only true for `retail_bp` routes.
   `POST /api/admin/employees` lives on the **identity blueprint**, and there is
   **no `require_license_capability` anywhere under `commercial_runtime/identity/`**
   (grep verified, zero matches). Today an expired/restricted install can still
   create users freely. The seat gate this design adds to `create_employee`
   closes most of that gap as a side effect; the residual (other admin routes)
   should be logged as an AUDIT item, not silently fixed in this scope.

Also, for calibration of "verify before claiming absent" (question i): CLAUDE.md's
"genuinely missing" list is stale on at least three items — notifications
infrastructure exists (`commercial_runtime/notifications/` outbox + WhatsApp
settings, imported by `retail_api.py:50-53`), RBAC is no longer "a bare role
string" (8-code capability system, `user_accounts.py`), and shift/cash-drawer
management exists (cash sessions with X/Z reports, variance approval,
`retail_api.py` ~5700). The two claims in the brief that DO survive
verification: **no seat/user limit anywhere** (true), and **no inter-branch
transfers** (`transfer` appears nowhere in retail schema or API — true).

---

## 1. Recommendation up front

**Build, in this order:**

1. **Seat entitlement + enforcement (1 short wave, no schema change).** Owner
   defines a `max_users` integer entitlement; the product enforces it at the
   two account-creating/enabling choke points in
   `commercial_runtime/identity/onboarding_routes.py`, inside `BEGIN IMMEDIATE`,
   reading the limit from the already-persisted signed-assertion snapshot.
   Rule: **absent or ≤ 0 means unenforced** — that one rule simultaneously
   grandfathers every existing lease, defuses Owner's deny-by-default integer 0,
   and keeps Clinic byte-identical.
2. **Device→branch pinning (chain wave C1, no schema change).** Today every
   till falls back to the company's *first* branch
   (`retail_api.py::_default_branch`, line 616). A chain's data is wrong at the
   point of capture until each device knows which branch it is standing in.
   Smallest genuinely-missing chain piece; do it first.
3. **Branch-scoped users (chain wave C2, registry v7).** One nullable
   `branch_scope_uid` column on `users` — NULL = all branches. Do **not** touch
   the fixed 8-code capability tuple.
4. **Head-office comparison screen (chain wave C3, screen work only).** The
   data already converges on every device; reports already take `?branch_id=`.
   What's missing is a screen, not a system.
5. **`max_branches` entitlement (rides wave C2/C3).** Same mechanism as seats,
   enforced at `POST /branches`. This is the "chain" commercial axis.

**Deliberately NOT built yet:** inter-branch stock transfers (real feature,
deserves its own phase + retail v24+ claim when scheduled — see §5.5), any
cloud/owner-side chain dashboard (the DRAFT `RETAIL_OWNER_DASHBOARD` add-on
already reserves that concept for later), per-branch pricing, multi-licence
federation, and any seat enforcement on Clinic.

**On the money question (the axis trap, short form — full argument §1.2):
charge devices as the primary axis; implement seats as the mechanism but price
them gently or not at all for cashiers.** Devices are already enforced
cryptographically; users priced hard at 50 JOD/head will be evaded by account
sharing, which destroys the attribution system (PINs, audit trail, cash-variance
accountability) that makes this product worth paying for.

### 1.2 THE AXIS TRAP (question b) — resolved, with a recommendation

The owner's "5 POS devices → 5 users" conflates two axes that this codebase
already keeps strictly apart:

- **Devices** are hardware-bound identities: Ed25519 device keys, per-device
  leases, `DEVICE_LIMIT_REACHED` enforced by Owner at activation. A device
  cannot be shared invisibly, cannot be minted offline, and is enforced at the
  one moment the customer must talk to Owner anyway. It is the *hard* axis.
- **Users** are rows in `registry.db` created by the customer's own admin,
  offline, on their own machine. A user limit is enforceable (this design does
  it), but it is enforceable only against honest customers: the dishonest one
  creates a user called "Cashier" and hands the password to twenty people.

What that substitution costs is not revenue — it is **the product**. The whole
PIN subsystem exists because "which human touched this drawer" matters
(`user_accounts.py`: "A PIN is ATTRIBUTION, never AUTHORIZATION"); cash-variance
approval deliberately separates the counter from the approver (AUDIT-032
reasoning in `ROLE_CAPABILITIES`); the audit log stamps `user_id` on every
mutation. Price users hard and a hypermarket with 20 cashiers on 5 tills —
20 users, 5 devices, the exact case in the brief — will collapse to 3 shared
accounts, and the first stock-shrinkage dispute becomes unresolvable. The
customer is then angry at the product for a hole his own pricing dug.

What real POS vendors do: the hardware-centric ones (Square, Lightspeed, Toast,
Clover) charge **per register/terminal/location** and give staff accounts away
generously (Square walked back per-staff pricing); Loyverse — the vendor most
present in this product's small-shop segment — charges per employee only as an
optional employee-management add-on. Nobody successful makes the cashier
headcount the primary meter on a till product.

**Recommendation (one answer, not a menu):** the paid meter is **devices** —
e.g. 2 devices included, 50 JOD per extra device, using the enforcement that
already exists and the `EXTRA_DEVICE` add-on already sitting PLANNED in the
catalog. Build `max_users` anyway (it is nearly free given §0.1) and set it
**generously and proportionally** — e.g. included users = 4 × device count, or
a flat high ceiling — as an anti-abuse backstop and as the mechanism the owner
can later price if he still wants to (e.g. for back-office/manager seats, where
sharing is less natural). Revenue tracks shop size either way; devices track it
honestly, users track it adversarially.

If the owner overrules this and prices users at 50 JOD anyway, everything below
still works unchanged — the mechanism is identical, only the numbers Owner puts
in the plan differ. But he should overrule it knowing the sharing dynamic, not
discover it.

---

## 2. The entitlement mechanism (seats)

### 2.1 Where the limit lives so it cannot be edited away (question a)

**In the Ed25519-signed assertion, as `entitlements.max_users`, persisted only
as a snapshot of a verified artifact.** The reasoning, stated fully because it
is the crux:

- A row in `registry.db` or `retail.db` is the customer's own file; anything
  written there un-signed is a suggestion, not a limit.
- The stored copy in `licensing_state.entitlements_json` is *also* a local
  SQLite row — but it is a **cache of something that was cryptographically
  verified on arrival** (`assertion_verifier.verify_assertion` → activation/
  check-in persist), it **expires** (`assertion_expires_at`, TTL signed by
  Owner), and it is **re-resolved from Owner truth at every check-in**
  (`checkin.py:109` → `checkin_scheduler.py:413`). Editing it buys a tamperer
  at most one check-in interval / assertion TTL, after which the next verified
  assertion overwrites the edit — or check-ins stop and the existing offline
  policy walks the install to WARNING → GRACE_PERIOD → RESTRICTED, where sales
  stop entirely. Clock rollback to stretch that window is already caught
  (`trusted_time.detect_rollback` → CLOCK_REVIEW_REQUIRED).
- This is **exactly the threat model the licence state itself already accepts**:
  `licensing_state.current_state` is an editable row too, trusted between
  check-ins for the same reasons. Being stricter for seats than for the licence
  state (e.g. re-verifying the stored envelope signature on every employee
  create) would add complexity to protect the *least* valuable gate on the
  board — a customer who can edit `licensing.db` can also edit the shipped
  Python source. Do not build a taller fence on one side of an open field.

**What has to change in the lease payload: nothing structural.** `entitlements`
is already inside the verifier's `ALLOWED_PAYLOAD_FIELDS`; a new key inside
that object sails through today's verifier untouched, is persisted untouched,
and is served to readers untouched. **What has to change in the verifier:
nothing.** The change is a new *reader* (§2.3).

### 2.2 The wire semantics — one rule that prevents three outages

> **`entitlements.max_users`: integer. Present and ≥ 1 → enforce as a ceiling
> on countable accounts. Absent, or present with value ≤ 0 → seat enforcement
> is OFF.**

Why "≤ 0 means off" is load-bearing and not a soft-heartedness:

1. **Old leases keep working with zero migration.** Assertions issued before
   Owner defines the entitlement simply lack the key → unenforced. No
   contract-version bump, no re-activation campaign.
2. **Owner's resolver is deny-by-default.** The moment someone creates the
   `max_users` `EntitlementDefinition`, `resolve_entitlements` starts emitting
   `max_users: 0` for **every licence whose plan/add-on/override doesn't set
   it** — including every existing Retail customer and all of Clinic
   (`entitlements.py`: `_TYPE_DEFAULTS = {"integer": 0, ...}`, definitions
   iterate globally). If the client read 0 as "zero seats allowed", the day the
   definition is created, **every shop in Jordan loses employee management at
   its next check-in.** With the ≤ 0 rule, that day is a no-op, and enforcement
   turns on per-licence only when Owner deliberately prices a value in.
3. **A 0-user licence is semantically nonsense** (nobody could log in to use
   it), so no legitimate meaning is lost by reserving 0 as "not configured".
   Per ENGINEERING.md's sentinel question — *can this value also arrive
   legitimately?* — no: Owner-side validation (§6) will refuse to store a
   configured 0 on purpose.

**Mutation-proof obligations for the implementer:** a test that `max_users: 0`
→ creation allowed (the allow-half; a "deny everything" mutation must fail it),
a test that absence → allowed, a test that `max_users: 2` with 2 countable
accounts → refused, and a test that the refusal names `SEAT_LIMIT_REACHED`
rather than a generic error.

### 2.3 The reader — new code, exact shape

New module `commercial_runtime/licensing_contracts/entitlement_reader.py`
(licensing owns reading licensing state; identity must not learn licensing's
storage layout):

```python
def read_numeric_entitlement(app_data_dir: str, code: str) -> int | None:
    """None => unenforced (no licensing record, no entitlements, key absent,
    value <= 0, or value not an int). Positive int => enforce as ceiling.
    NEVER raises: an unreadable licensing.db returns None -- see §2.4."""
```

Implementation: `LicenseStateRepository(Path(app_data_dir)/"database"/"subsystems"/"licensing.db").load()`,
parse `entitlements_json`, apply the ≤ 0 rule. Resolves `app_data_dir` the same
way the routes already do (`AURA_APP_DATA` env, matching
`onboarding_routes._config_path` and `make_capability_guard`'s dirname
convention). ~40 lines including the docstring this codebase expects.

### 2.4 Failure modes, decided (not deferred)

| Situation | Behaviour | Why |
|---|---|---|
| `licensing.db` missing / unreadable / corrupt | **unenforced** (reader returns None) | Seat limits are a commercial cap, not a security boundary. Failing closed here would let a broken subsystems file lock a shop out of hiring — the LOCAL_STATE_CORRUPT lesson (a guard must not block the cure). And the "cheat" of deleting `licensing.db` is self-defeating: no licence record → `NOT_CONFIGURED` → the whole install is read-only for sales (`test_pre_activation_states_deny_new_mutation`). Nobody trades their ability to sell for free user rows. |
| Entitlement key absent (old lease) | unenforced | §2.2 rule 1. |
| Value 0 (deny-by-default) | unenforced | §2.2 rule 2 — the critical one. |
| Value non-integer (string "5", float, bool) | unenforced, log a warning | Fail-safe against an Owner-side typing slip; Owner's `_validate_typed_value` should make this unreachable, but the client must not brick on it. |
| Licence RESTRICTED / EXPIRED / GRACE | enforce the last verified value if positive | The snapshot is the last thing Owner signed; a lapsed shop does not gain seats by lapsing. (Independently, §0.4's audit item may later block creation in restricted states entirely — out of scope here.) |
| Over-limit already (downgrade, or offline convergence §2.8) | block *new* create/enable only | §2.6. |

### 2.5 What counts toward the limit (question d)

**Counted: every account with `status IN ('active', 'pending_setup')` for the
session's `company_id` — the admin included.** Not counted: `'disabled'`.

- **`pending_setup` counts** because an invite is a seat someone will sit in;
  if pending were free, an admin could hold unlimited standing invites and
  activate them the day after a seat check, and `employee_setup` (the
  invite-redemption route) would need its own limit check with a worse UX
  (the new hire, not the admin, hits the wall). Counting at invite time puts
  the refusal in front of the person who can pay.
- **`disabled` does not count** because deactivate-to-free-a-seat is the humane
  downgrade path (§2.6) and matches the codebase philosophy that restriction
  preserves data: the account, its history and its attribution survive; only
  its seat is released. Technical shadow: **the enable direction of
  `update_status` must therefore be a checked enforcement point** (§3), or
  disable/enable becomes a seat-limit bypass.
- **The admin counts** (recommended — pricing call flagged in §8). Two seats
  included then means "owner + one employee", which matches the single-till
  shops this product actually sells to. Excluding the admin creates a phantom
  free seat that makes every capacity display off by one forever. The
  `status='pending_setup', password_hash='PENDING'` admin bootstrap row counts
  the same as any other — one vocabulary, no special cases.

### 2.6 Downgrade and over-limit (question c)

Shop paid for 5 seats, drops to 2, has 5 users. At the next check-in the
assertion carries `max_users: 2`. Then:

- **All 5 users keep logging in and working. Login is NEVER seat-gated.**
  Gating login would fire mid-shift on whichever cashier clocked in third — a
  person who did nothing wrong, standing in front of a queue. The enforcement
  point is account **creation/enabling**, an admin act, done calmly in a back
  office.
- **No account is ever auto-disabled, auto-deleted, or auto-picked.** The
  product has no authority to choose which three humans lose their names.
  Choosing is the owner's act (disable in the employees screen — frees the
  seat per §2.5); the product's act is refusing to add a sixth.
- **Creating or re-enabling is refused with `SEAT_LIMIT_REACHED`** and a
  message that states the numbers ("5 of 2 seats in use").
- **The over-limit condition is surfaced, not hidden**: `status_presenter.py`
  already exposes `entitlements` to the UI; the employees screen shows a
  persistent "N of M seats" line and, when N > M, a banner. An owner must never
  discover the situation for the first time inside a refusal dialog.

**What must never happen**, as a list the implementer can test against:
auto-deactivation; login refusal; deletion of anything; blocking the admin's
own account or session; blocking `update_status`'s *disable* direction (that is
the cure); and the enforcement helper treating "can't read the limit" as
"limit is zero" (§2.4 row 1 — the deny-everything mutation shape).

### 2.7 Offline (question e)

Seat enforcement is **a local read of the last verified assertion snapshot plus
a local COUNT(*) — zero network calls by construction**, because both inputs
already live on the device: `licensing_state.entitlements_json` (refreshed by
the background `LicenseCheckInScheduler` whenever the network exists) and
`registry.db`'s `users` table. The product keeps selling and keeps enforcing
seats with the cable cut, indefinitely, degrading only as the *licence itself*
degrades through the signed offline policy (WARNING → GRACE → RESTRICTED),
which is someone else's already-shipped machinery.

The one genuinely offline-shaped consequence is *upgrade latency*: a customer
who pays for a 6th seat gets it at the **next successful check-in**, not
instantly. Mitigation is a "refresh licence now" affordance in the licensing UI
if one doesn't already exist (the check-in scheduler can be nudged the way sync
has `nudge()`); flag for implementation, cheap either way. Do not "fix" this by
letting the create route consult Owner online — that would make hiring depend
on the network, which this product exists to avoid.

### 2.8 The concurrency hole (question f)

Two admin sessions POST `/api/admin/employees` simultaneously against
`max_users: 2` with 1 seat used. Without serialization both read COUNT=1, both
insert, 3 users exist. **The fix is the AUDIT-009 oversell pattern already used
eleven times in `retail_api.py`** (e.g. `create_sale`, retail_api.py:3576-3581:
"BEGIN IMMEDIATE takes the write lock up front so a concurrent sale ... rapid
repeated requests must not oversell"):

- `create_employee` and `update_status` open with `conn.execute("BEGIN
  IMMEDIATE")` on the registry connection (registry_db.get_conn already sets
  WAL + busy_timeout=30000, same as retail — the pattern transplants exactly),
- the seat COUNT, the email-uniqueness check, the `EMP-NNNN` counter read, and
  the INSERT all happen inside that one write transaction,
- commit at the end as today.

SQLite's single-writer lock makes the second request's COUNT wait for the
first's commit; it then reads 2 and refuses. Free side benefit: the existing
`EMP-{count+1:04d}` employee-number race and the check-then-insert email race
in the same function are closed by the same lock.

**The race BEGIN IMMEDIATE cannot close, stated honestly:** `users` sync
across devices (wave B2), and "admin-device single-writer" is *policy, not
property* (the design doc's own recorded debt,
`docs/launch-readiness/multi-device-design.md`). Two devices offline can each
locally pass the check and converge over-limit later. The sync **apply** side
must NOT enforce seats (rejecting an applied `user` event would fork the
dataset — quarantine is for malformed events, not commercial disputes). Instead
the converged over-limit state lands in exactly the §2.6 posture: everyone
works, nothing new fits, banner shows 3 of 2. Bounded, honest, non-destructive
— and it means one deliberately-crafted race buys a customer *one* extra
account that Owner can see at the next check-in, not an exploit worth
engineering against harder.

### 2.9 Clinic stays byte-identical (question g)

Three independent layers, any one of which suffices:

1. **The entitlement is never positive for Clinic.** Owner prices `max_users`
   into Retail plans/add-ons only. Clinic licences resolve `max_users: 0`
   (deny-by-default) → the ≤ 0 rule → unenforced. Turning Clinic on later is an
   Owner catalog act (set a value on a Clinic plan), no product deploy.
2. **The reader's absence path is the default path.** Until a Clinic install
   even *has* a licensing record with entitlements, `read_numeric_entitlement`
   returns None before any behavioural branch.
3. **The code change in the shared route is behaviour-gated, not
   product-gated.** No `if product == RETAIL` in shared identity code (which
   would be a layering smell); the gate is "is there a positive signed limit",
   which is false for Clinic by construction of (1).

"Byte-identical" precisely: for any request Clinic serves today, the response
bytes are unchanged, because the new branch short-circuits to today's path when
the limit is None. The only observable Clinic difference is one extra local
SQLite read per *admin employee-create* call — a corner no Clinic behaviour or
test observes. (The `BEGIN IMMEDIATE` serialization does apply to Clinic's
create route too — that is a pure race-bug fix with no success/failure
behaviour change, and it would be wrong to fork the transaction shape by
product.)

---

## 3. Enforcement points — file and function

| # | What | Where | Change |
|---|---|---|---|
| E1 | Seat ceiling on account creation | `commercial_runtime/identity/onboarding_routes.py::create_employee` | Wrap the existing body's DB work in `BEGIN IMMEDIATE`; before the INSERT, `read_numeric_entitlement(...)`; if positive and `COUNT(users WHERE company_id=? AND status IN ('active','pending_setup')) >= limit` → 403 `{'error': ..., 'reason_code': 'SEAT_LIMIT_REACHED', 'seats_used': N, 'seats_included': M}`. Audit-log the refusal like other admin acts. |
| E2 | Seat ceiling on re-enable | same file, `::update_status`, **enable direction only** (`status == 'active'` and the row is currently `'disabled'`) | Same check, same transaction discipline. The disable direction is never gated (it is the cure, §2.6). |
| E3 | Explicitly NO check | `::employee_setup` (invite redemption) | The seat was counted at invite time (`pending_setup` counts, §2.5). |
| E4 | Explicitly NO check | `commercial_runtime/sync/sync_service.py::_apply_event` `user`/`user_permission` branches | Origin-enforced only; apply-side rejection forks devices (§2.8). A comment in the apply branch must say so, or a future hand "fixes" it. |
| E5 | Branch ceiling (chain, later wave) | `products/retail/backend/api/retail_api.py` `POST /branches` handler (line ~6390) | Same reader, code `max_branches`, count `branches WHERE company_id=? AND status='active'`; `BEGIN IMMEDIATE` likewise. Also applies to the bulk branch import if one exists (verify at implementation). |
| E6 | Branch scope on data (chain wave C2) | `retail_api.py` in-handler, the `session_has_capability` pattern | §5.4 — reports coerce the filter, mutations refuse foreign `branch_id`. |
| E7 | Seat/branch usage display | `licensing_contracts/status_presenter.py` (already emits `entitlements`) + employees screen (vanilla JS) | "N of M seats"; banner when N > M. Read-only. |

New shared code: `licensing_contracts/entitlement_reader.py` (§2.3) and a small
`identity/seat_guard.py::count_seats(conn, company_id) -> int` so E1/E2 share
one COUNT and one vocabulary. The refusal string follows the fixed-literal i18n
convention (`create_employee`'s existing comment about locale catalogs — no
runtime-assembled sentences).

---

## 4. Schema changes — versioned-migration style

**Seats need NO schema change anywhere.** The limit lives in the signed
assertion; the count is a query over existing tables; the refusal is an HTTP
response. This is a feature of the design, not an omission.

**Registry v7 (chain wave C2) — the only DDL this design requires.** House
style per `registry_db.py`'s chained `apply_*` steps and
`account_schema.py`'s pattern; drafted ROADMAP claim to be appended (and
actually committed — the v23 lesson) before dispatch:

> ## 2026-XX-XX — registry schema v7 CLAIMED for branch-scoped users
> **CLAIMED: registry v7, by `<branch>`, for one column.**
> `ALTER TABLE users ADD COLUMN branch_scope_uid TEXT` — NULL = all branches
> (today's behaviour for every existing row, so the migration changes no
> decision). Holds the **branch `uid`** (wire identity, v13), never the local
> `branches.id`: users sync across devices and local branch ids are
> per-device (`_branch_uid`, retail_api.py:688; the `user_permission` uid
> lesson, sync_service.py docstring). Backfill: none needed — NULL is the
> correct value for every pre-v7 row. `_SYNCED_USER_COLUMNS`
> (user_accounts.py) gains `branch_scope_uid`, and sync_service's `user`
> apply branch gains the same column; an older build receiving the field
> ignores it (the apply side reads only its known column list — forward
> compatible by construction), enforcement on that device simply lags until
> it upgrades and the next `user` update backfills.

**Retail v24 — NOT claimed here, reserved by name for inter-branch transfers**
when that phase is actually scheduled (§5.5). Claiming a version for
unscheduled work is how v23's near-collision happened; this document
deliberately does not do it. Any implementer who discovers this design *does*
need a retail DDL change must claim v24 in ROADMAP.md before dispatch, per the
single-writer discipline.

---

## 5. The chain design

### 5.1 One company with many branches, or many companies? (question h)

**One company, many branches. Decided, not hedged.** Grounds:

- Every business table is already `(company_id, branch_id)`-scoped; branches
  have wire identity (`uid`) and sync as first-class entities; inventory
  balances are per-branch; metrics take a branch predicate; cash sessions are
  per-branch. The former model is *built*.
- The multi-company capability of one install is the wrong tool for a chain: a
  second `company_id` means separate books, separate customers, separate
  everything — that is for a landlord hosting two unrelated businesses on one
  machine, not five outlets of one market. And after the registry v4 rebind,
  `company_id` converges on the Owner-issued licence identity — the licence IS
  the company.
- **Commercial consequence that falls out for free:** a chain = one licence,
  device_limit sized for all stores, all devices in one licence-scoped sync
  stream. "Chain size" is then priced on the two axes that already exist or
  are added here — devices (built) and branches (`max_branches`, §6) — with no
  federation layer, no cross-licence reporting, nothing new invented.

What this means for scope: the chain feature is **not a new system**. It is
(i) point-of-capture correctness (§5.2 gap 1), (ii) a permission dimension
(§5.4), and (iii) screens over data that already converges (§5.3).

### 5.2 What is genuinely missing for 5 stores (question i) — verified, not assumed

**Missing — verified:**

1. **Device→branch pinning. The worst one, and the least obvious.** Sales
   accept `data.get('branch_id') or _default_branch(conn, cid)`
   (retail_api.py:3586) and `_default_branch` resolves to *the company's first
   branch by id* (line 616). Nothing anywhere records "this till stands in the
   Irbid store." Unless the frontend sends an explicit branch on every request,
   five stores' sales all file under branch 1 — per-branch reports would be
   confidently, silently wrong. Fix (wave C1, no schema): a device-local
   `branch_uid` setting in the existing `config.json`
   (`onboarding_routes._read_config/_write_config` — deliberately device-local,
   never synced, exactly the property required); a settings field to set it
   (admin/`retail.employees`-gated); resolution order in the branch-taking
   routes becomes *pinned branch → explicit request value (per §5.4 rights) →
   `_default_branch` self-heal*. `_default_branch` remains the last-resort
   self-heal with its hard-won no-sync-emission comment untouched.
2. **Branch-scoped permissions** — confirmed: the 8-code tuple has no branch
   dimension, `?branch_id=` on reports is a free parameter for anyone holding
   `retail.reports`, and mutations accept any branch id. §5.4.
3. **Inter-branch stock transfers** — confirmed absent (`transfer`: zero
   matches in retail schema/API). Deferred with a shape, §5.5.
4. **A comparison screen** — reports exist per-branch and all-branch, but
   nothing puts Store A next to Store B. Screen work only.

**NOT missing — exists, do not rebuild (each verified in §0/§5.3):**
multi-site data convergence (cloud relay, licence-scoped); branch CRUD +
sync + wire identity; per-branch inventory, metrics, cash sessions; staff and
permission propagation to every till (waves B2 stages 2b/3); catalog sync
(products/categories/customers/suppliers — the stale "products are not synced"
comment in `schema.py`'s DDL is contradicted by `sync_service.py:1369`'s live
`product` apply branch; trust the code); device limits; the restricted-mode
read-only philosophy.

### 5.3 How data from 5 stores reaches one place (question k) — what sync does today

Plainly: **every device holds the whole company already.** Each device writes
its mutations into a local `sync_outbox` (same transaction as the business
write), a background `SyncService` pushes signed batches to Owner's relay over
the internet and pulls everyone else's, and applies them locally. The stream is
**scoped by licence** — `owner/app/sync/routes.py` resolves `license_id` from
the verified installation and both push and pull are unconditionally bound to
it — so "all devices of one licence" is the sync domain, with no LAN
assumption anywhere (`relay_client.py` speaks HTTPS to `base_url`). Entity
coverage: category, product, customer, supplier, reorder_request, sale,
sale_item, payment, return, return_item, inventory_movement, **branch**, plus
users/user_permissions on the registry stream. Cursors, quarantine, pruning,
replay protection all exist server-side.

**Therefore the head-office view is a screen, not an architecture.** The
owner's laptop running the app, activated under the chain's licence, converges
all five stores' sales/stock/cash within the sync cadence and can already
filter every report by branch or see all branches (omit `?branch_id=`). Wave
C3 is: one "branches overview" page — revenue/transactions/returns per branch
side by side (all from `core/retail/metrics.py`, which already takes the
branch predicate), stock-position per branch, open cash sessions per branch —
plus the §E7 seat/branch usage line. Vanilla JS, served by Flask, matching the
existing reports pages. Honest caveats to print on the screen itself: figures
are as-of last sync (show per-device sync health — `get_active_health` is
already imported into retail_api for exactly this kind of display), and a
store that is offline shows stale, not zero.

### 5.4 Branch-scoped permissions with a FIXED capability tuple (question j)

The tuple must not grow: the eight codes are seeded one-row-per-code for every
account ("a code with no row is a code nobody holds"), so a ninth code strands
every existing account un-provisioned for it, needs a coordinated backfill on
every device, and — the deeper reason — **branch scope is not an authority, it
is a dimension on every authority.** "May see reports" and "for which branch"
are orthogonal; encoding branch into codes (`retail.reports.branch2`) turns 8
codes × N branches into a matrix that the fixed-vocabulary design exists to
forbid.

**Smallest correct change:** `users.branch_scope_uid` (registry v7, §4).
NULL = unscoped (today's behaviour; head office, admin, roaming supervisors).
Set = every branch-dimensioned decision for that user is coerced to that one
branch. Enforcement is the in-handler pattern the codebase already uses for
field-level authority (`session_has_capability` — "a discount is a FIELD on a
sale"; branch is likewise a field, not a route):

- a helper `session_branch_scope()` in `mt_auth.py` (admin → None, mirroring
  the existing admin bypass; resolves the scoped uid to the local branch id
  via the `branches.uid` lookup, fail-closed if unresolvable),
- **reads** (dashboard `?branch_id=`, reports, reorder lists): scope present →
  the filter is overwritten server-side with the scoped branch — never
  trusted from the query string,
- **mutations** that carry `branch_id` (sales, stock adjust, POs, cash
  sessions): scope present and requested branch ≠ scope → 403 with the
  standard capability-denied message shape,
- the employees screen gets a branch dropdown per user (writes through the
  existing `PUT .../role`-style admin routes' new sibling or a field on the
  existing update; admin-only, syncs via the v7 column).

A branch manager is then: role `manager` (the existing default capability set)
+ `branch_scope_uid` = their store. Head office is: `manager` or `admin` with
NULL scope. No new codes, no matrix, one column, and every pre-v7 row behaves
identically until an admin deliberately scopes someone.

Multi-branch-but-not-all scope (a supervisor over 2 of 5 stores) is
deliberately NOT in wave C2 — it needs a scope *table* rather than a column,
and no known customer shape demands it yet (YAGNI; the column upgrade path to
a table is mechanical if it ever does).

### 5.5 Inter-branch transfers — deferred, with the shape named

Not wave C anything. When scheduled, it is: a `stock_transfers` document table
(claim retail v24 in ROADMAP.md first), OUT movement at source and IN at
destination written as `inventory_movements` rows (the ledger is the truth —
`stock_reconciliation`'s doctrine), dispatch/receive as two acts by two users
so shrinkage in transit is visible, and sync via the existing outbox exactly
like `inventory_movement` already does. It is deferred because a 5-store chain
can operate (sell, replenish per-branch via POs, observe) without it, and
because it is the one chain feature with real financial-integrity surface
(stock in transit) deserving its own mutation-proof phase.

---

## 6. The Owner Control Center contract (their side, stated as a contract)

The Owner side belongs to the other collaborator. The product side of this
design requires from them exactly the following, and nothing else:

**C1 — Definitions.** Two rows via the existing catalog mechanism
(`EntitlementDefinition`): `max_users` (integer, "Maximum countable staff
accounts") and `max_branches` (integer, "Maximum active branches"). Seeded the
way `max_devices` already is in `catalog/services.py::_CANONICAL_ENTITLEMENTS`.

**C2 — Values.** For every licence that has *purchased* seats/branches, the
resolved entitlements dict that `resolve_entitlements` already embeds into
every assertion MUST carry the purchased **total** as a positive integer
(e.g. base 2 + 3 extra = `max_users: 5`). How they compute it — plan value +
add-on quantity (SubscriptionItem already has `quantity`; an
`EXTRA_USER_SEAT` add-on parallels the PLANNED `EXTRA_DEVICE`), or a
license-specific override typed by the operator — is their implementation
freedom. The wire contract is only the resolved integer.

**C3 — Semantics.** Absent and ≤ 0 both mean "unenforced" on the client (§2.2).
Therefore: never emit a negative; refuse to *store* a configured 0 (add a
`max_users`/`max_branches` clause to `_validate_typed_value` beside the
existing `max_devices` one, including a sane safety ceiling like the existing
`_SAFETY_MAX_DEVICE_LIMIT`); and understand that merely creating the
definition (C1) flips nobody's behaviour — enforcement starts per-licence when
a positive value is priced in. This asymmetry is the deployment-safety feature.

**C4 — Refresh path.** No new endpoint needed: check-in already re-resolves and
re-signs (checkin.py:109). They should confirm assertion TTL / check-in
interval in the offline policy is short enough that "I paid for a seat this
morning" arrives same-day, and that support can trigger/ask the customer to
trigger a manual check-in.

**C5 — Visibility (their UI, their timeline).** A licence page field for the
two values and, if they want it, the over-limit signal — the product cannot
report seat *usage* upstream today (the assertion/check-in payloads are
deliberately one-way on business data; `FORBIDDEN_ASSERTION_MARKERS` philosophy
— note check-in REQUEST content is theirs to extend, not this design's).

**C6 — Devices axis (if the §1.2 recommendation is taken).** Purely their
side: price `EXTRA_DEVICE` (already PLANNED in the catalog) and set
`device_limit`/slots per what `resolve_effective_device_limit` already reads.
Zero product-side work.

The conformance style between the two sides stays as it is everywhere else in
`licensing_contracts`: by hand + shared fixtures, never by import across
deployables (`canonical.py`'s doctrine). A fixture assertion with
`entitlements: {"max_users": 5}` should be added to the shared conformance
vectors so drift is caught the way canonicalization drift is.

---

## 7. Risks — how this goes wrong in a real shop

**What a dishonest customer tries, and what stops (or doesn't stop) them:**

1. **Shared logins to dodge seat fees.** Nothing technical stops it; §1.2 is
   the mitigation (don't make the incentive), plus the honest framing that
   what they lose is attribution — their own theft-dispute evidence.
2. **Editing `licensing.db`** (`entitlements_json`, or `current_state`).
   Works until the next check-in/assertion expiry, then the verified assertion
   overwrites it; sustained evasion requires staying offline, which walks them
   into RESTRICTED where sales stop. Same bounded exposure the licence state
   already accepts. Not worth further hardening (§2.1).
3. **Blocking the Owner endpoint at the firewall to freeze a good assertion.**
   Bounded by the signed offline policy's grace ladder — already built,
   already the answer for licence evasion generally.
4. **Clock rollback to stretch the assertion.** Caught: `trusted_time.
   detect_rollback` → CLOCK_REVIEW_REQUIRED, mutations stop.
5. **Restoring an old DB backup with more seats headroom / old entitlements.**
   The restored `licensing_state` is stale → next check-in overwrites;
   meanwhile the rollback/anchor machinery treats time oddities as review
   states. Seat *count* restored low is self-correcting on the next `users`
   sync pull.
6. **The offline two-device create race (§2.8).** Buys one-ish extra account,
   converges visibly, non-exploitable at scale; Owner sees truth at check-in
   if C5 is ever built. Accepted with eyes open.
7. **Creating users then downgrading the plan.** Lands in over-limit: everyone
   works, nothing new fits. The design deliberately makes this NOT a cheat —
   they got what they paid for while they paid for it.

**What an honest customer will be furious about, and the mitigation shipped
with the feature (not promised later):**

1. **"I paid for a seat and the app still says no."** Check-in latency (§2.7).
   Mitigation: seats banner shows "as of <last check-in>"; a refresh action;
   support script. If this ships without the refresh affordance it WILL be the
   #1 support call.
2. **The 0-means-zero catastrophe.** If the ≤ 0 rule is mis-implemented, the
   day Owner creates the definition, every shop's employee management dies at
   next check-in. This is the single highest-blast-radius line in the design —
   it gets the mutation-proof battery in §2.2, both directions.
3. **A cashier locked out mid-shift.** Only possible if someone "improves"
   enforcement onto login. The never-list in §2.6 exists for the code
   reviewer, verbatim.
4. **Branch scoping surprising a covering manager** ("I'm helping at the
   Zarqa store today and I'm blind"). Scope changes are admin-editable and
   sync to every till within the cadence; the C2 employees-screen work must
   include the scope field from day one, not as a follow-up, and unscoped
   (NULL) stays the default forever.
5. **Chain reports quietly wrong because tills weren't pinned to branches.**
   Wave C1 must ship *before* C3's comparison screen exists to be trusted; the
   overview screen should visibly flag sales recorded against the self-healed
   default branch when more than one branch exists ("N sales with no store
   assigned") rather than silently filing them under Main Branch.
6. **"I deactivated someone and still can't hire."** Only if the count
   includes `disabled` by mistake — §2.5 is explicit; test it.

**Ways the implementation itself goes wrong (for the verifier):** enforcement
added to the sync apply path (forks devices — E4's comment is the guard);
`branch_scope_uid` stored as local branch id instead of uid (silently scopes
users to a *different* store per device — the exact class of bug the
`user_permission` uid lesson documents); the seat COUNT left outside the
`BEGIN IMMEDIATE` (reintroduces the race the lock was added for); the refusal
string assembled at runtime (breaks the Arabic catalog, per the fixed-literal
i18n convention in `create_employee`).

---

## 8. Open questions for the owner — pricing calls only, not design calls

1. **Confirm the axis (§1.2).** Recommended: devices are the paid meter
   (50 JOD per extra device beyond 2); `max_users` set generously as a
   backstop. If he insists users are the meter at 50 JOD/head, the build is
   identical — only Owner catalog numbers differ. One sentence from him
   settles it.
2. **Does the admin count inside the included 2?** Recommended yes ("owner +
   one employee included"). Changes marketing copy and the included number,
   not the mechanism.
3. **Is 50 JOD per seat/device one-time or per subscription term?** The
   subscription/add-on machinery supports either; Owner-side pricing config
   only.
4. **Are branches monetized (`max_branches` priced per branch beyond 1), or
   free within a licence with only devices/seats charged?** Enforcement E5 is
   built either way; a value only gets priced in if he says yes.
5. **Clinic:** confirm seats stay unpriced (hence off, §2.9) for Clinic until
   he decides otherwise. Recommended: yes, per his own "don't work on Clinic."

---

*Implementation dispatch note: waves as ordered in §1 — (S1) entitlement reader
+ E1/E2 + tests + banner; (C1) branch pinning; (C2) registry v7 + scope
enforcement + employees-screen field; (C3) overview screen + E5. S1 and C1 are
independent and can run in parallel with disjoint file sets (S1: identity/ +
licensing_contracts/ + employees screen; C1: retail_api.py + settings screen +
config). C2 touches user_accounts.py/sync_service.py and must be serialized
after S1's identity edits. ROADMAP claims (registry v7; retail v24 only when
transfers are scheduled) go in — and get committed — before any dispatch.*
