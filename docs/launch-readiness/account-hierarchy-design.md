# Account hierarchy for chains — owner, branch managers, workers — design

**Status:** DESIGN ONLY. No source file changed. Written 2026-08-30 against
`feat/launch-readiness` (worktree `ci-hardening-w0.3-continue`), registry
schema v6 on disk with **v7 claimed** (ROADMAP 2026-08-30) for
`users.branch_scope_uid`, retail v24 reserved by name (not claimed) for
transfers. Direct successor to
`docs/launch-readiness/seats-and-chain-design.md`; every deviation from that
document is flagged inline as **DEVIATION** with reasoning — there are two,
both caused by the owner's since-made decision that devices are the paid meter.

**Question index:** (a) §2 · (b) §4 · (c) §3.2 · (d) §5 · (e) §8 · (f) §9 ·
(g) §10. Deliverables: recommendation §1 · account matrix §3.4 · enforcement
points §4.2 · schema §6 · sync §7 · Owner CC contract §11 · risks §12 · owner
questions §13.

---

## 0. The brief's facts, verified — all true, three sharpened

Every given fact checks out against the code as read today. Three need
sharpening because they change the shape of the answer:

1. **"Admin-device single-writer" is POLICY, not property — by the design
   doc's own admission.** `multi-device-design.md` §2's scorecard for the
   chosen P2 architecture literally lists "single-writer is policy not
   property" as one of its two named debts. The comment in
   `user_accounts.py:200-204` (manager excludes `CAP_EMPLOYEES` because
   "`users` is an admin-device single-writer table") is therefore not stale —
   it is an accurate summary of a real limit — but it is a limit of the
   *conflict-resolution scheme*, not of the transport. The transport (wave B2:
   registry outbox, `user`/`user_permission` apply branches, quarantine) was
   built to move user rows between devices and already tolerates a second
   writer in every case except two, both traced concretely in §2. The comment
   overstates exactly one thing: it forbids all delegation, when what is
   actually unsafe is delegation *without the two fixes in §2.4*.

2. **The crux has a second half the brief did not name:
   `create_employee`'s `perms` dict is an unvalidated grant channel.**
   `onboarding_routes.py::create_employee` (~:647) accepts
   `data.get('permissions', {})` and INSERTs every non-'none' entry verbatim
   into `user_permissions` *before* role seeding runs — and because seeding is
   `INSERT OR IGNORE`, **the caller's explicit grants win over the role
   defaults**. Today that is unreachable by anyone but the single admin, so it
   is not a live hole. The moment any non-owner can reach this route, it is
   the single widest escalation path on the board (§4, G2). Any delegation
   design that widens the gate and forgets this dict ships broken.

3. **The admin can already write `users` from any device.** The admin's own
   row syncs (`create_admin` queues a `user` create; stage 2b), so the owner
   can log in on any till in the fleet and create/edit accounts there.
   "Admin-device single-writer" is therefore *already* violated by the product
   as shipped whenever the owner walks to a second till — the writer set today
   is "the admin's current device", which is any device. Delegation widens the
   frequency of multi-writer moments, not the class. This matters for honesty
   in §2: the failure modes below are reachable today, delegation just makes
   them routine instead of rare.

Also verified as given: roles at `user_accounts.py:105-122`, the eight-code
tuple and its seeding contract (:159-168, :234-295), `ROLE_CAPABILITIES`
(:221-225), the admin-only gate with no licence guard on
`POST /api/admin/employees`, no branch dimension anywhere, registry v7
claimed, `_default_branch` first-branch defect recorded, v24 reserved by name.

---

## 1. Recommendation up front

### 1.1 First, the framing correction — his words describe a model this product deliberately refuses

*"an owner account that creates admins for managers"* — in this codebase
**`admin` is the owner**. One admin per install, structurally: `create_admin`
is gated on "no valid admin exists yet", `ASSIGNABLE_ROLES` excludes `admin`
on purpose ("offering it here would be offering a second owner account
through the side door", user_accounts.py:117-122), `update_role` refuses to
promote anyone into it, and `update_status` refuses to disable it. That
refusal is load-bearing — it is what makes "the owner cannot be locked out"
and "nobody can mint a second owner" provable properties.

So what he calls "admins for the branches" must not be admins. They are
**branch-scoped managers with a staff-management grant** — the existing
`manager` role, plus the v7 `branch_scope_uid` column, plus a per-user
`retail.employees` capability row the owner switches on. That combination
gives a branch person exactly the powers he described (run the branch, hire
its workers) with none of the powers he did not ask for and would regret
(touch other branches, mint peers, grant capabilities, reach owner-only
approvals). Do not build a second admin tier; tell him it exists in spirit
and is safer in this form.

### 1.2 Reading A vs Reading B — resolved, not papered over

- **Reading A (centralised):** owner creates every account; each account is
  scoped to a branch via `branch_scope_uid`. This is what the codebase is one
  claimed migration away from. Cost: registry v7 + scope enforcement +
  employees-screen scope field — all of which is **already designed and
  claimed** (predecessor §5.4, wave C2). Incremental cost of A over "what was
  already planned": near zero.
- **Reading B (delegated):** owner creates branch managers; each branch
  manager creates workers for their own branch. Cost: everything in A, plus
  the delegated-gate rework of three routes with eight escalation guards
  (§4), the employee-number allocator fix (§2.4), one owner-facing toggle
  (§10), and a roster filter. Roughly one additional wave of work, all
  product-side, no schema beyond v7.

**Recommendation: build A now (waves C1/C2 as already ordered), then build B
as the immediately following wave (D), shipped default-off behind an explicit
per-manager owner toggle.** Not a hedge — a sequence, for three reasons:

1. **B is A plus guards.** Every B prerequisite (device→branch pinning, the
   scope column, scope enforcement, the allocator fix) is an A deliverable or
   a standalone bug fix. There is no version of B that skips A.
2. **The ops burden argument for B is real but back-loaded.** Onboarding 50
   staff is an afternoon, once — the invite-link flow works remotely (owner
   WhatsApps the setup link). What actually hurts is *churn*: cashier turnover
   in Jordanian retail is monthly per store, and under A every replacement
   routes through the owner's phone. A 5-store owner will tolerate that for
   the first month and hate it by the third. B exists for month three, which
   is exactly when a launch-readiness programme can afford to ship it.
3. **The codebase argument for A is really an argument against *unguarded*
   B.** §2 shows the single-writer constraint is two specific, fixable
   defects, not a wall. Once fixed, B is safe. Shipping B *first*, before the
   allocator fix and the quarantine screen exist, would be shipping the §2.2
   silent-fork failure as a routine event.

**Migration path A→B:** additive and trivial by construction. B introduces no
schema, no new role, no data migration — the owner toggles "may manage staff
at their branch" on a manager, and the delegated gate starts admitting them.
Toggling it off reverts to pure A. An install that never toggles it never
executes a single new code path (invisible-unless-opted-in, same as
e-invoicing and licensing).

**Build order:** D0 (allocator fix + `BEGIN IMMEDIATE`, §2.4 — can ride with
any wave, do it first) → C1 (device→branch pinning, already designed) → C2/A
(registry v7 + scope enforcement + scope UI) → D (delegation gate + guards +
toggle) → C3 (head-office comparison screen). The exceptions/quarantine
screen (already on ROADMAP as "the two exception queues need ONE screen")
should land no later than D — §2.2 explains why it stops being optional the
day two devices can both mint accounts.

**Deliberately NOT built:** a fourth role (§3.2); `max_users` enforcement
(§8, **DEVIATION** from predecessor, reasoned); per-branch seat quotas; a
user `delete` sync event (disable travels as `update`; the tombstone column
exists but stays unwired); the full 8-code permissions-matrix screen (§10 —
one toggle suffices); multi-branch scope (2-of-5 stores — still a YAGNI, per
predecessor §5.4, and delegation does not change that).

---

## 2. The single-writer problem (question a) — traced, verdict, fixes

### 2.1 What the machinery actually does with two writers

Verified by reading `sync_service.py`'s `user` branch (:2142-2272) and
`user_permission` branch (:2273-2356), `user_accounts._queue_user_sync_event`
/ `_queue_user_permission_sync_event`, and the emit sites in
`onboarding_routes.py`:

- Every account write queues a full-allowlist `user` event in the same
  transaction (stage 2b), into registry.db's own outbox (v5), drained by the
  second SyncService instance.
- Apply is an upsert on `uid` gated by `WHERE excluded.row_version >
  users.row_version` — reject-stale, never last-write-wins.
- A collision on `users`' two *non-wire* unique constraints — `email`
  (global), `UNIQUE(company_id, employee_id)` — is caught **by name** and
  parked in registry.db's `sync_apply_quarantine` (v6), cursor advancing.
- `user_permission` events carry `(user_uid, subsystem)`; an unresolvable
  `user_uid` is quarantined as `missing_parent:user`.

So: **two devices creating two *different* people concurrently is safe by
construction** — distinct uids, both INSERT halves land everywhere, both
propagate with working permission sets. The stream does not need a single
writer for creation *per se*. It needs it for two narrower things:

### 2.2 Failure one — the employee-number allocator guarantees a sticky, symmetric, silent fork

`create_employee` allocates `emp_id = f"EMP-{count + 1:04d}"` from a local
`COUNT(*)`. Trace the exact delegation scenario:

1. Owner's device (4 staff exist) creates `x@shop.jo` → `EMP-0005`.
   Branch till (same 4 staff, one sync interval behind or offline) creates
   `y@shop.jo` → **also `EMP-0005`**. Both commit locally; both queue
   `user` create + 8 `user_permission` creates.
2. Owner's device pulls B's event: INSERT new uid → raises `UNIQUE constraint
   failed: users.company_id, users.employee_id` → quarantined as
   `duplicate_employee_id`. The 8 permission events that follow resolve
   `user_uid` → nothing → quarantined `missing_parent:user`, eight times.
3. The branch till pulls A's event: **the same thing happens in mirror.**

Result: `x@` exists only on the owner's device, `y@` only on the branch till.
Every *other* device in the fleet keeps whichever event won the relay race
and quarantines the loser. Neither new hire can log in anywhere except the
till they were minted on. Nothing crashes, nothing is lost, and **nothing is
shown** — `sync_apply_quarantine` has no screen (ROADMAP 2026-08-29: the
retail quarantine's sibling `sync_conflicts` "has no read route at all", and
the registry quarantine is in the same state). Replay cannot self-heal it:
the payload's `employee_id` collides forever until a human renames one row,
and no human is told.

This is not a tail risk under delegation — it is the *common case*, because
both allocators derive from the same `COUNT` and diverge the moment any
device is a beat behind. **This, concretely, is what "a manager who could
mint accounts would be writing to a table their device is not the writer for"
actually breaks.** Not the sync stream — the number allocator feeding it.

### 2.3 Failure two — `row_version` cannot order two writers' edits to one row

`row_version` is a per-row counter bumped locally. Two devices editing the
same user row from version N both produce N+1; each side's apply of the
other's event fails the `excluded.row_version > users.row_version` test;
**both no-op, silently, and the fleet permanently disagrees about that row**
until any later edit anywhere reaches N+2 and steamrolls one side (a silent
lost update). The scenario that matters: owner disables a cashier at head
office in the same window the branch manager sets that cashier's PIN at the
branch — the *disable can fail to land on the branch till*, which is
precisely the outcome stage 2b's own comment says the emit exists to prevent
("a disabled cashier could keep transacting on a device that never heard
about it"). This is the real content of the single-writer rule: `row_version`
is a monotonic clock only while one device does the writing.

Note honestly: per §0.3 both failures are **reachable today** by an owner
logged into two tills. Delegation converts them from "rare, owner walked to
another till" to "routine, two people whose job is account management".

### 2.4 Verdict and what makes delegation safe

**Verdict: a real correctness failure, not a stale comment — but a bounded
one, with a two-part fix, not a wall.**

**Fix 1 (required, blocking for wave D): kill the allocator collision.**
Delegated creations mint `EMP-<dev4>-NNNN`, where `dev4` is four hex chars of
`commercial_runtime/identity/device_context.py::peek_local_device_uuid()` —
the install-stable device UUID that already exists *inside the identity
package* (no layering violation; `local_terminal_id()` in retail's schema.py
is a wrapper around this same value, and `multi-device-design.md` §4 already
prescribes exactly this shape for document numbers: "`SALE-<term4>-000101` …
A globally sequential number is never allocated offline"). This is
AUDIT-032B's pattern, third application (`sale_number`/`return_number`, then
`po_number`). Applied to the **delegated path only** — the admin path keeps
`EMP-NNNN` so Clinic and every existing Retail install stay byte-identical
(§9); the residual owner-on-two-tills race is today's status quo, now written
down. A device whose UUID was never generated (fresh install, pre-activation)
falls back to a random 4-hex fragment rather than colliding on a shared
constant.

Alongside it, `create_employee`/`update_status` adopt `BEGIN IMMEDIATE`
(predecessor §2.8's transplant — registry_db already runs WAL +
busy_timeout), which closes the same-device double-submit race on the email
check and the counter regardless of delegation.

**Fix 2 (structural, free): bound the writer set per row via the §3.4
matrix.** Under the matrix, a cashier row has exactly two possible writers —
the owner and that branch's one manager — and every *security-downgrading*
transition (role change, capability edit, scope change, re-enable) is
owner-only, so the §2.3 fork can delay a PIN or a disable but can never lose
a demotion or a revocation to a concurrent lesser edit. The residual
(owner-disable vs manager-PIN tie) is rare, self-heals on the next edit, and
is accepted **with eyes open and a line in the risk register**, not fixed —
upgrading `row_version` to a real ordering (vector clocks, site ids) is a
rewrite of wave B2's conflict model and is not warranted by two writers making
occasional co-located edits.

**Prerequisite promoted, not optional: the quarantine/exceptions screen.**
Two failure modes above end in `sync_apply_quarantine`. The ROADMAP already
concluded the two exception queues need one screen; wave D is the point where
"needs" becomes "blocks", because delegation manufactures quarantine events
from routine HR work (duplicate email when owner and manager both invite the
same person, §12). Minimum bar for D: the registry quarantine gets a read
route and rows on that combined screen.

**What is deliberately NOT done:** seat/limit enforcement in the sync apply
path (predecessor E4 stands — apply-side rejection forks devices; the comment
guarding it must survive this wave), and no attempt to make `users`
apply-side "validate" a delegated creation (an event from a peer is a fact
about that peer's registry, not a request — §4's guards live at origin only).

---

## 3. The account model (question c + deliverable 2)

### 3.1 The pieces

- **Role** answers "which powers" — unchanged three-role domain
  {admin, manager, cashier} + legacy alias.
- **`branch_scope_uid`** (registry v7) answers "which branches" — NULL =
  all branches (every existing row; head office; the owner), set = coerced to
  that one branch. Holds the branch **`uid`**, never the local id (the claim
  already records why).
- **The per-user `retail.employees` row** answers "may this manager run their
  branch's staff" — the existing `user_permissions` machinery, written by the
  existing `update_perms` route, synced by the existing stage-3 events.
  Nothing new is invented; the whole delegation feature is one grant the
  owner can already (in API terms) give.

A **branch manager** is therefore: `role='manager'` + `branch_scope_uid=<store>`
+ `user_permissions('retail.employees')='full'`. A plain manager, a
head-office manager, and a roaming supervisor are the same role with
different scope/grant combinations. **The vocabulary he asked for maps as:
"admins for the branches" → branch managers as defined here; "employees for
the workers" → cashiers.**

### 3.2 No fourth role — and this is the answer to question (c)

The delegation requirement does **not** overturn the predecessor's
"scope is a column, not a code" ruling; it stress-tests it and the ruling
holds. A fourth role ("branch_admin") was considered and rejected on four
grounds, one of which is decisive:

1. **`normalize_role()` maps any unrecognised value to `cashier`** — the
   deliberate fail-safe direction. During any mixed-version window (and this
   fleet is *designed* to run mixed versions — the v7 claim itself plans for
   an older build ignoring the new column), a `branch_admin` row syncing to an
   un-upgraded till would silently render that person a **cashier** there.
   A promotion that demotes the promotee on half the fleet is not a rollout
   hazard, it is a support fire. The scope column has the opposite property:
   unknown column → ignored → the person keeps working, enforcement merely
   lags. This alone decides it.
2. The role tuple is load-bearing in more places than the capability tuple:
   `ROLES`, `ASSIGNABLE_ROLES`, `ROLE_CAPABILITIES`, `update_role`'s fixed
   bilingual refusal literal, both products' role dropdowns, Clinic's
   `WHERE role='employee'` selections.
3. Capability rows already express per-user authority — that is what the
   table is *for*. A role that means "manager + one grant" duplicates state
   the grant already carries, and the two would drift.
4. `update_role`'s delete-and-reseed semantics give the right lifecycle for
   free: demoting a branch manager to cashier wipes the `retail.employees`
   grant with the other rows (a demotion SHOULD strip delegation), and
   promoting a cashier to manager does not silently confer it (the owner must
   deliberately re-toggle). Both directions are correct without a line of new
   code — pin each with a test.

**Cost of this choice, stated:** "branch manager" is not a value in a column;
it is a predicate over three fields. The employees screen must render it as
if it were a thing (a badge derived from the predicate), or owners will not
be able to see who runs what. Screen work, §10.

### 3.3 Scope semantics (unchanged from predecessor §5.4, restated as contract)

NULL scope = every branch (default forever; admin is always effectively
NULL). Set scope = reads coerced server-side, mutations refused for foreign
`branch_id`, per predecessor E6 — delegation adds *identity* routes to the
same scope discipline, nothing else. Multi-branch scope stays out (column →
table upgrade is mechanical if a real customer shape ever demands it).

### 3.4 The matrix — who may do what to whom

Actors: **OWNER** (role admin, scope ignored/NULL) · **BM** (branch manager:
manager + scope + `retail.employees`) · **MGR** (manager without the grant,
scoped or not) · **CSH** (cashier).

| Act | OWNER | BM | MGR / CSH |
|---|---|---|---|
| Create cashier | ✓ any branch, any scope value | ✓ **own branch only; role forced cashier; scope stamped = own; no `permissions` dict** | ✗ |
| Create manager / branch manager | ✓ (create manager, then scope + toggle) | ✗ | ✗ |
| Create admin | ✗ (structural, `create_admin` gate) | ✗ | ✗ |
| Disable account | ✓ any non-admin | ✓ cashiers of own branch only, never self | ✗ |
| **Re-enable** account | ✓ | ✗ **(owner-only, deliberate — see below)** | ✗ |
| Change role | ✓ manager↔cashier, never admin | ✗ | ✗ |
| Edit capability grants (`update_perms`) | ✓ | ✗ | ✗ |
| Set / clear PIN | ✓ any non-admin | ✓ cashiers of own branch only | ✗ |
| Set / clear `branch_scope_uid` | ✓ any non-admin | ✗ (including own) | ✗ |
| View roster (`get_employees`) | ✓ full | ✓ own-branch rows only | ✗ |
| Set / change passwords | nobody — self-service only (invite link, email reset). No admin route exists and none is added. | | |
| Own row | password/PIN self-service only, for everyone. No delegated act may target the actor's own row. | | |

**Re-enable is owner-only, asymmetrically to disable, on purpose.** Disable
is the safety action (a BM must be able to lock out a fired worker at
9 p.m. without phoning the owner); re-enable is the trust action (a worker
the *owner* disabled — suspected theft — must not be quietly reactivated by a
sympathetic or complicit branch manager). The asymmetry costs one phone call
per rehire and closes the single ugliest delegated-status attack. It also
keeps the (future, if ever priced) seat gate on the enable direction living
in exactly one privilege tier.

---

## 4. Privilege escalation (question b + deliverable 3) — every path, every guard

Threat model: a motivated, dishonest branch manager with a valid session on
their own till, able to craft raw requests (the frontend is advice, never
enforcement — this codebase's own doctrine).

### 4.1 The guards

- **G1 — role ceiling.** Delegated creator ⇒ requested role must normalize
  (via `normalize_role`, *not* string equality — the legacy `'employee'`
  alias must not sneak a naive `== 'cashier'` check) to `cashier`; anything
  else → 403.
  *Closes:* BM mints a manager, a peer BM, or (already structurally blocked
  twice over) an admin; BM grows the branch a management layer the owner
  never approved.
- **G2 — grant-channel refusal.** Delegated creator ⇒ a non-empty
  `permissions` dict is **refused with 403**, loudly — not silently ignored
  (a silent ignore trains callers to keep sending it and turns a future
  refactor's "restore the dict" into an invisible escalation). The created
  account gets exactly `ROLE_CAPABILITIES[cashier]` via
  `seed_capabilities_for_user` — which is a strict subset of the manager set,
  so **a delegated creator can never confer a capability they do not hold**;
  pinned by a test asserting the subset relation against the live constants.
  *Closes:* the §0.2 channel — cashier minted with `retail.cash.approve`
  (self-approval of own drawer via a stooge), with `retail.employees`
  (recursive delegation), with `retail.discount`/`retail.stock.adjust`
  (margin/shrinkage levers).
- **G3 — scope stamping.** Delegated creation stamps the new row's
  `branch_scope_uid` **from the creator's own row, read fresh from
  registry.db inside the same transaction** — never from the request body,
  which does not get a scope field on this path at all.
  *Closes:* BM plants an unscoped (all-branch) worker; BM plants a worker in
  another branch.
- **G4 — target rule.** Every delegated mutation (`update_status` disable,
  `update_pin`) requires: target row's `normalize_role == cashier` AND
  target `branch_scope_uid` == creator's scope AND target is not the actor.
  A NULL-scope cashier (head office) is *outside* every BM's reach by this
  rule — NULL matches nothing.
  *Closes:* disabling a fellow manager or another branch's staff (sabotage);
  PIN-resetting a head-office account; self-targeting.
- **G5 — the capability editor, the role changer, and the scope setter stay
  admin-only.** `update_perms`, `update_role`, and the new scope route keep
  the `mt_role == 'admin'` gate verbatim.
  *Closes:* BM grants self/others anything (G5+G2 together mean no delegated
  path writes `user_permissions` except role-default seeding); **the brief's
  named two-step — re-scope a victim account into your branch, then edit it —
  dies here**, because re-scoping is owner-only; BM widens own scope to NULL.
- **G6 — fresh authority read.** The delegated gate resolves the creator's
  role, scope, and `retail.employees` grant from registry.db **at request
  time, inside the transaction** — never from the session cookie (the cookie
  has no scope field and must not grow one). Revocation is belt-and-braces:
  `update_perms` and `update_role` already bump `session_version`, and the
  new scope route must too, so a revoked BM's very next request dies in
  `mt_login_required` (`_session_version_is_stale`) before any gate runs; G6
  catches the same-request window anyway.
  *Closes:* fired/demoted/descoped BM keeps minting on a live session.
- **G7 — password-reprompt boundary kept.** `CAP_EMPLOYEES` stays in
  `PASSWORD_ONLY_ACTIONS` (user_accounts.py:405-410): a PIN switch on a
  shared till can never stand in for the branch manager on any staff action —
  the cashier who walks up to an unlocked till mid-shift cannot hire.
  Frontend must wire the reprompt on the new BM screens (§10).
- **G8 — invite-link custody, the honest residual.** The delegated create
  returns the setup link to the creator, exactly as the admin path does — so
  a BM can complete a ghost worker's setup themselves and hold its
  credentials. **This cannot be closed technically** (whoever creates an
  account can always be first to its credential); it is *bounded*:
  the ghost is a cashier (G1/G2), so its money surface is sale-bound refunds
  (`create_return` recomputes from the original sale and caps at net sold)
  and its own drawer, whose variance still needs owner-held
  `CAP_CASH_APPROVE`; its creation is an audit row naming the BM
  (`CREATE_EMPLOYEE`, actor stamped); it occupies a visible roster line the
  owner's all-branch view shows. Optional hardening, cheap and worth doing
  since the email outbox now exists (v9, `commercial_runtime/notifications/`):
  delegated creates *email* the link to the worker's address and show it only
  when SMTP is unconfigured. Attribution laundering shrinks to "BM controls
  the inbox too", which no till software fixes.

### 4.2 Enforcement points — file and function

| # | What | Where | Change |
|---|---|---|---|
| D1 | Delegated create gate (G1, G2, G3, G6) + allocator | `commercial_runtime/identity/onboarding_routes.py::create_employee` | Gate becomes: admin → today's path byte-for-byte; else require `normalize_role(creator)=='manager'` AND `user_has_capability(conn, creator, CAP_EMPLOYEES)` AND creator `branch_scope_uid` NOT NULL, then G1/G2/G3; `EMP-<dev4>-NNNN` via `device_context.peek_local_device_uuid()`; whole body under `BEGIN IMMEDIATE`; audit row unchanged (actor already stamped). Both role *and* capability required, deliberately redundant: the pair keeps a stray hand-granted `retail.employees` on a cashier row inert, and keeps Clinic provably outside (§9). |
| D2 | Delegated disable (G4, G6) | same file, `::update_status` | Admin path untouched (including the owner-disable 409). Non-admin: G4 target rule; **only `status='disabled'` accepted** — the enable direction answers 403 for a delegated caller (matrix §3.4). |
| D3 | Delegated PIN (G4, G6) | same file, `::update_pin` | Same non-admin block as D2. `SET_PIN`/`CLEAR_PIN` audit rows already name the actor — that is the frame-up deterrent (§12). |
| D4 | Roster filter | same file, `::get_employees` | Admin: unchanged. Non-admin with the grant: rows `WHERE company_id=? AND branch_scope_uid=<creator's>` and `normalize_role != admin`. Everyone else: 403 as today. |
| D5 | Scope setter (G5) | same file, new `PUT /api/admin/employees/<id>/branch-scope` | Admin-only; validates the uid against `branches` **through the retail-facing caller**, not in identity code (identity stays product-agnostic — the route stores an opaque uid; the employees screen offers only real branches); refuses the admin's own row (the owner is never scoped); bumps `session_version`; bumps `row_version` + `_queue_user_sync_event` inline like every sibling. |
| D6 | Scope read helper | `commercial_runtime/identity/mt_auth.py::session_branch_scope()` (new, predecessor §5.4 shape) | Admin → None; else the user's `branch_scope_uid` read via the same fail-closed posture as `_read_capability`. Consumed by retail's E6 handlers and by D1–D4's G4/G3 checks. |
| D7 | Data-plane scope enforcement | `products/retail/backend/api/retail_api.py`, in-handler per branch-dimensioned route | Unchanged from predecessor E6 (reads coerced, mutations refused) — delegation adds no new data-plane rule; it *depends* on this one existing. |
| D8 | Explicitly NO enforcement | `commercial_runtime/sync/sync_service.py::_apply_event` `user`/`user_permission` branches | Origin-enforced only; predecessor E4's comment extends to name delegation ("apply-side ceiling/role checks fork devices"). |
| D9 | BM toggle + scope field + badge | employees screen (vanilla JS) + `update_perms` (unchanged server-side) | §10. |

Mutation-proof obligations (the ENGINEERING.md battery, named now so it is
costed now): each guard G1–G6 gets a deny-test **and** the allow-half (a
legitimate BM successfully creating an own-branch cashier — the test a
"deny everything" mutation fails); D2's enable-direction refusal gets both
directions; the G2 subset invariant pinned against the live constants; a
counting test that the gate *ran* (not just that a 403 came back) for at
least D1, per the "assert THE CHECK RAN" failure shape.

---

## 5. What the owner sees (question d)

Nothing new beyond the predecessor design — this question dissolves once
scope enforcement exists, and it is worth saying where the enforcement point
is so nobody invents a second one:

- **The owner** is `admin` → `session_branch_scope()` returns None → every
  report keeps its free `?branch_id=` (one branch or omit for all), and wave
  C3's comparison screen is just a consumer of that. The "all-branches view"
  is the absence of coercion, not a feature.
- **A branch manager** holds `retail.reports` (manager default) but a set
  scope → the D6 helper + D7 in-handler coercion **overwrite the query-string
  filter server-side** in `retail_api.py`'s report handlers. The query string
  is never trusted; there is no middleware layer — the enforcement point is
  in-handler, the `session_has_capability` pattern, exactly as predecessor
  E6/§5.4 specifies.
- **The roster split** is D4 in `onboarding_routes.py::get_employees`.
- Head-office staff who must see everything but are not the owner: `manager`
  with NULL scope — supported by construction, no code.

Precondition repeated because it is the expensive silent failure: C1 device→
branch pinning must land before any of this is *trusted* — today every till
files sales under the company's first branch (`_default_branch`,
ROADMAP 2026-08-30 defect entry), so a scoped manager's "own branch" report
would be confidently wrong about what their own store sold.

---

## 6. Schema and claims (deliverable 4)

**Registry v7, already claimed, is sufficient. This design needs no further
DDL anywhere.** Point by point:

- Branch scope: the claimed `users.branch_scope_uid TEXT` column, exactly as
  written (nullable, no default, no backfill, `_SYNCED_USER_COLUMNS` +
  `user` apply branch gain the column, older builds ignore it).
- Delegation authority: an existing `user_permissions` row — no DDL.
- The BM predicate: computed, never stored — no DDL.
- Allocator fix: a string-composition change in `create_employee` — no DDL
  (the `po_number` ledger entry records the identical lesson: AUDIT-032B
  fixes are mint-site composition, not schema).
- Re-enable asymmetry, roster filter, guards: route logic — no DDL.
- Retail v24: stays **reserved by name, unclaimed**, per the ledger. Nothing
  here touches retail.db's schema at all.

Ordering constraint from the v7 claim honoured and restated: v7 lands after
the identity-file edits of any preceding wave touching
`user_accounts.py`/`onboarding_routes.py` (serialize, don't parallelize, on
those two files — they are this design's hot files too), and scope
*enforcement* is inert until C1 pinning exists.

---

## 7. Sync implications (deliverable 5) — plainly

What happens to user rows across devices under this model:

1. **A worker minted at a branch till exists everywhere within the sync
   cadence, with a working permission set** — the stage 2b/3 machinery does
   this today; delegation adds no new event types, no new columns beyond v7's,
   and no apply-side behaviour. The `retail.employees` grant itself travels as
   an ordinary `user_permission` event, so toggling a BM on the owner's
   device arms the branch till within the cadence (and disarms it the same
   way, with `session_version` forcing re-login).
2. **Creation collisions stop being routine** once Fix 1 lands: distinct
   people always merge (distinct uids, now distinct employee_ids). The two
   residual quarantine shapes are `duplicate_email` (owner and BM both invite
   the same real person — genuinely a human decision, correctly parked) and
   the legacy-allocator overlap from admin-path creates (today's status quo).
   Quarantine is visible on the §2.4 screen, replayable, never dropped.
3. **Concurrent edits to one row can still fork** (§2.3) — bounded to
   owner+one-BM per cashier row, healing on the next edit, with every
   security-downgrading transition owner-only so a fork can never lose a
   revocation to a lesser concurrent edit. Documented residual, not fixed.
4. **Disable travels as an `update`** (status field, `session_version`
   MAX-merged so revocation survives any delivery order). There is still no
   `user` delete event; `deleted_at_utc` stays unwired. Nothing here changes
   that.
5. **The apply side never enforces the matrix.** A `user` event arriving with
   role manager from a peer is applied on `row_version` merit alone —
   guards live at origin (D8). Enforcing them on apply would fork the fleet
   over a commercial/authority dispute, which is quarantine-abuse (predecessor
   E4's reasoning, extended).
6. Clinic installs continue to queue registry events nothing drains (the
   recorded, deliberate ROADMAP posture); their payloads gain one NULL
   `branch_scope_uid` key per the v7 claim. See §9.

---

## 8. Does this change the seat/device design? (question e)

**The meter is unchanged: devices, 2 included, 50 JOD per extra — nothing in
per-branch staffing perturbs it.** Staffing scales headcount; the meter
charges tills; the §1.2-predecessor argument (accounts must stay abundant or
attribution dies) gets *stronger* under delegation, because branch managers
will mint accounts more freely than a bottlenecked owner ever did — which is
the desired outcome, every account being free attribution data.

**`max_users` — DEVIATION from the predecessor, stated plainly: do not build
it now. Not the Owner-side definition, not the reader, not the enforcement,
not the banner.** The predecessor recommended building it "anyway (nearly
free)" as an anti-abuse backstop *when seats might have been the meter*. With
devices settled as the meter, the backstop defends nothing: an install with
500 accounts on 2 paid devices costs the vendor nothing and hurts only its
own roster hygiene; the abuse that costs money (more tills) is already
cryptographically enforced at activation, and the abuse that costs the
*customer* (shared logins) is made worse, not better, by capping accounts.
"Nearly free" is still a week of two-sided work, a conformance vector, a
0-vs-absent semantic that carries a fleet-wide outage if mis-implemented
(the predecessor's own §2.2 blast-radius warning), and a support surface —
for a limit whose correct value is "generous enough to never fire", i.e. a
limit that earns its keep only by never being noticed. Keep the predecessor's
§2.2 wire rule (absent/≤0 = unenforced) as the *reserved contract* so the
feature can be added later without re-activation or contract bumps, and keep
`BEGIN IMMEDIATE` in `create_employee` (that half was a race fix, not a seat
feature). If the owner later prices back-office seats, the predecessor's
design lifts off the shelf intact.

**`max_branches` — keep the enforcement point (predecessor E5), let him
decide the price.** Devices meter *capacity* but not *chain-ness*: a 5-till
hypermarket and a 5-store chain both pay for 5 devices, yet the chain
consumes the genuinely new value (multi-site oversight, C3). `max_branches`
at `POST /branches` is the one honest knob for that, it is cheap, and the
≤0 rule keeps it dormant until priced. Whether branch 2+ costs money is §13
Q5 — a pricing call, and the mechanism is identical either way.

---

## 9. Clinic stays untouched (question f) — the proof, touchpoint by touchpoint

All of this lives in shared identity code, so the proof has to be per edit:

1. **The v7 column** — nullable, no default, nothing in Clinic reads it;
   the claim already mandates a Clinic-shaped migration test. Every Clinic
   row means "every branch", which is meaningless to a product with no
   branches — i.e. today.
2. **`create_employee`'s delegated branch is unreachable from Clinic, twice
   over.** The gate's first arm is `mt_role == 'admin'` → **today's code
   path, byte-for-byte, including every response body** — and Clinic's only
   account-creating actor is its admin. The delegated arm requires
   `normalize_role=='manager'` (Clinic writes only `admin`/`employee`; its
   suite selects `WHERE role='employee'`) **and** a `retail.employees` =
   'full' row (Clinic-role accounts seed it 'none' via the cashier default
   set, no Clinic screen calls `update_perms`, and the code is
   retail-namespaced). A Clinic non-admin therefore falls through to the same
   403 it gets today.
3. **The allocator** — discriminated `EMP-<dev4>-NNNN` only on the delegated
   arm; the admin arm keeps `EMP-{count+1:04d}` verbatim, so every id Clinic
   ever mints is byte-identical. (`BEGIN IMMEDIATE` wraps both arms — a pure
   race fix with no success/failure behaviour change, and the predecessor
   §2.9 already ruled that forking transaction shape by product would be
   wrong.)
4. **`update_status`/`update_pin`/`get_employees`** — same two-arm shape:
   admin arm untouched, delegated arm unreachable per (2).
5. **D5 (scope setter)** — a new route Clinic's frontend never calls; its
   existence changes no existing response.
6. **Enforcement D6/D7** — lives in `mt_auth.py` (a helper nothing calls
   unless a retail handler asks) and `retail_api.py` (Clinic never imports).
7. **Sync** — the one non-response-byte difference, named rather than hidden:
   per the v7 claim, `_SYNCED_USER_COLUMNS` gains `branch_scope_uid`, so the
   `user` events Clinic already queues (and never drains — the recorded
   ROADMAP posture) carry one extra always-NULL key in their JSON payloads.
   No Clinic behaviour, response, or test observes outbox payload bytes; if
   "byte-identical" is read to include them, the alternative (product-forking
   the column list in shared code) is exactly the layering smell the
   predecessor §2.9 rejected. Flagged for the verifier as the known,
   deliberate delta.
8. **`registry.db` file** — v7 migrates Clinic installs' registries too
   (shared file, shared chain); the migration is one ALTER, `ensure_schema_
   version`-wrapped with backup + integrity checks like every registry step,
   and item 1's test is what makes "migrated and unchanged" proven rather
   than asserted.

---

## 10. The onboarding reality (question g) — which screens this needs, honestly

The long comment in `user_accounts.py` is right: today the per-role defaults
ARE the access an account has, because `update_perms` has no screen. This
design **does not need the full 8-code permissions matrix screen** — and
deliberately avoids designs that would (a fourth role was partly rejected for
this). It needs exactly three UI items, all on the existing employees screen,
all shipping *inside* their waves, not after:

1. **Branch-scope field per user** (wave C2/A — already a committed part of
   the predecessor's C2 scope; writes D5). Unscoped stays the default
   forever; the predecessor's risk 4 (covering-manager blindness) stands.
2. **One toggle: "May manage staff at their own branch"** (wave D), rendered
   only on manager rows with a scope set, wired to the existing
   `update_perms` route with `subsystem='retail.employees'`,
   `access_level='full'|'none'`. This is the first screen that route has ever
   had — scoped to one code, which is the point: the owner gets the
   delegation lever without this wave inheriting the whole
   permission-matrix project. (That project remains real and remains
   unscheduled; this design neither needs it nor advances it.)
3. **The branch-manager badge + BM-facing reduced screen** (wave D): the
   roster a BM sees (D4) with create-cashier, disable, and PIN controls only,
   and the **password re-prompt** wired for every staff action (G7 — the
   server refuses a PIN-switched session's password-only actions; the screen
   must ask, or every BM action fails mysteriously on shared tills).

Plus the promoted prerequisite from §2.4: the combined exceptions screen
(quarantine + conflicts), already argued for on the ROADMAP; wave D is its
deadline. And the D9 copy detail worth one line so it is not discovered in
support: `update_role`'s reset semantics mean demote-then-repromote drops the
BM toggle — the screen should say so next to the role control.

---

## 11. What Owner Control Center must provide (deliverable 6)

**For this design: nothing. Zero Owner-side work.** Stated as a contract
because that is itself the contract:

- **C-A.** The account hierarchy, scope, delegation, guards, and allocator
  are entirely product-side; no assertion field, no entitlement, no endpoint,
  no catalog row changes. The other collaborator's queue takes nothing from
  this wave.
- **C-B.** The devices axis is already theirs and already built: price
  `EXTRA_DEVICE` (PLANNED in the catalog) and set `device_limit` per
  `resolve_effective_device_limit` — predecessor C6, unchanged, and now the
  *only* commercial work item, since max_users is deferred (§8).
- **C-C.** If `max_users` (or a priced `max_branches`) is ever wanted, the
  predecessor's §6 contract (C1–C5: definitions, resolved positive totals,
  absent/≤0-off semantics, refuse-to-store-0 validation, check-in refresh)
  applies verbatim — reserved, not commissioned.
- **C-D.** One awareness item, not a work item: user rows reaching the relay
  will now carry `branch_scope_uid` in payloads. Owner's relay stores events
  opaquely (`_build_event` requires only a well-formed entity id) — no schema
  or validation change needed; confirming that reading is a five-minute check
  on their side, not a task.

---

## 12. Risks — how this goes wrong in a real 5-store chain (deliverable 7)

**The dishonest branch manager tries, in rough order of appearance:**

1. **The ghost cashier** (G8): mints "Ahmad", completes setup himself, rings
   refunds under the ghost's PIN. Bounded: refunds are sale-bound and
   recomputed; drawer variance needs owner approval; the audit trail says
   which BM minted which account and when; the ghost is a roster line the
   owner can see. Residual: real, accepted, shrunk by emailing invite links.
   The owner's habit that actually closes it is a monthly roster review —
   put a "created by / created at" column on the roster so the review is a
   glance, not an investigation.
2. **PIN poisoning / frame-up**: resets a cashier's PIN to a value he knows,
   transacts as them. `SET_PIN` audit rows name him and timestamp the reset —
   a disputed transaction after a PIN reset by the accused's manager reads
   exactly like what it is. Deterrent, not prevention; §13 Q2 offers the
   owner the stricter option.
3. **Re-enable the accomplice the owner disabled** — dead (owner-only
   re-enable, matrix §3.4).
4. **Grant/scope games** — dead at G2/G5 (the re-scope-then-edit two-step
   dies because re-scoping is owner-only; capability edits never leave the
   owner's hands).
5. **Session games** — demoted/descoped BM racing his own revocation: dead at
   G6 + `session_version` staleness.
6. **Route-shopping**: hitting `update_perms`/`update_role`/D5 directly —
   unchanged admin-only gates; hitting `create_employee` with `role=employee`
   hoping the alias slips the ceiling — G1 normalizes first; sending a
   `permissions` dict — refused loudly (G2).

**What an honest owner would be furious about, with the shipped mitigation:**

1. **"My two new hires can't log in and nobody told me"** — §2.2. This is why
   the allocator fix is *blocking* for wave D and the quarantine screen is
   its deadline, not a follow-up. If D ships without both, this WILL be the
   first support call.
2. **"I promoted her to manager and the staff button vanished"** —
   `update_role`'s reset drops the BM toggle by design; the screen says so at
   the moment of the tap (§10.3), and re-toggling is two clicks.
3. **"My branch manager hired someone and I only found out at month-end"** —
   working as designed, but only tolerable if the roster shows created-by,
   and (optional, cheap now that the email outbox exists) the owner gets a
   notification per delegated creation. Flagged as a D nice-to-have.
4. **"I disabled him at head office and he rang sales for two more hours at
   the branch"** — sync cadence plus, rarely, the §2.3 tie. Honest posture:
   disable propagates at the sync cadence, is not instant, and the risk
   register says so. (This is today's behaviour for every status change; not
   new to delegation.)
5. **"The Zarqa manager can't cover in Irbid today"** — predecessor risk 4;
   scope changes are owner-editable and propagate at the cadence; unscoped
   stays the default and the covering-manager case is one owner edit.
6. **Chain reports confidently wrong** — predecessor risk 5, restated:
   C1 pinning precedes any trusted per-branch view, and the C3 screen flags
   default-branch-attributed sales.

**Ways the implementation itself goes wrong (for the adversarial verifier):**
the delegated gate reading role/scope from the session instead of the DB
(G6); G2 downgraded to a silent ignore; the scope stamped from a request
field that "temporarily" exists; the G1 check comparing raw strings so
`'employee'` slips through; guards added to the sync apply path (D8's
comment is the tripwire); the allocator fix applied to the admin arm too
(breaks §9.3's Clinic proof); `EMP-<dev4>` derived from anything other than
the persisted device uuid (a per-process value would collide with itself
across restarts); D5 forgetting the `session_version` bump (revoked scope
lingers on live sessions); the enable-direction 403 accidentally applied to
the admin arm (owner suddenly cannot re-enable anyone — the allow-half test
exists for exactly this); the roster filter leaking the admin row to BMs.

---

## 13. Open questions for the owner (deliverable 8) — decisions only he can make

1. **Sequence sign-off:** A now, delegation (B) as the next wave, default-off
   per-manager toggle — or does he want delegation in the first chain
   release? (Changes wave order only; the build is the same.)
2. **May branch managers set/clear worker PINs?** Recommended yes (ops
   reality at a remote store) with the audit deterrent; the strict
   alternative is owner-only PINs and a phone call per forgotten PIN.
3. **Re-enable owner-only** (recommended, §3.4) — confirm he accepts the
   phone call per rehire in exchange for "nobody can quietly reactivate an
   account I locked".
4. **Confirm dropping `max_users` entirely for now** (§8 — a deviation from
   the previous design made possible by his devices-are-the-meter decision;
   it saves both sides real work and the semantics stay reserved if he ever
   wants back-office seats priced).
5. **Are branches themselves priced** (`max_branches` beyond 1, the "chain
   tax"), or free with devices carrying all revenue? Mechanism ships either
   way, dormant until a value is priced in (carried from predecessor Q4).
6. **Should delegated account creations notify him** (email per creation vs
   roster review only)? Cheap now that the email outbox exists; some owners
   will find it reassuring, others noisy.

---

*Implementation dispatch note: D0 (allocator + BEGIN IMMEDIATE; identity
files) can ride ahead alone. C1 (retail_api.py + config + settings screen) is
independent of D0 — parallelizable with disjoint files. C2/A (registry v7 +
user_accounts.py + sync_service.py column + employees screen scope field)
serializes after D0 on the identity files, per the v7 claim's own ordering
note. D (delegation: onboarding_routes.py + mt_auth.py + employees screen +
exceptions screen) serializes after C2. `user_accounts.py` and
`onboarding_routes.py` are single-writer hot files across D0/C2/D — one agent
at a time, ROADMAP-claimed, same discipline as schema versions. Nothing here
takes a retail schema version; registry v7 is the only claim and it is
already on the ledger.*
