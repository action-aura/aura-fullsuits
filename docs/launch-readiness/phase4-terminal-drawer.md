# Phase 4 — the cash drawer belongs to a till (retail v16)

Written after the fact, unlike `phase3-ledger-truth.md`, and the reason is worth
recording: Phase 4 was built by two agents whose network died before they
returned, so the code landed in the tree with **nobody having verified any of
it**. Four adversarial verifiers were run against the finished work instead of
alongside it. Everything in the "what nearly shipped" section below was found
that way — reproduced against real databases, not reasoned about.

Shipped in `c8e183f`. **`RETAIL_SCHEMA_VERSION` 15 → 16, reserved in `ROADMAP.md`.**
The first **non-additive** step in this chain: it drops a unique index, creates a
different one, and force-ends live rows.

## The bug, stated as arithmetic

A cash session was scoped to a **branch**.
`idx_cash_sessions_one_open_per_branch` enforced at most one open drawer per
`(company_id, branch_id)`. A shop running a desktop and a phone in one branch
therefore could not have two drawers, and takings rung on the phone folded into
whatever session was open — the desktop's.

That is not a UI annoyance. It is the Z-report reconciling a physical drawer
against money that was never in it. Measured by reverting
`_open_cash_session_id` to its pre-Phase-4 body and re-running the end-to-end
scenario through real routes:

    desktop x-report: cash_sales=1000.00  expected=1100.00   <- its float + BOTH tills
    phone   x-report: cash_sales=0.00     expected=50.00
    variances: [675.00, -700.00]          <- desk short by exactly the phone's 700

The desktop is short by precisely the phone's takings. A shop reading that
concludes its cashier stole 700, and the phone's cashier is 675 over with no
explanation. Both numbers are fiction produced by the schema.

## The property Phase 4 establishes

After v16, an open cash session is unique per `(company_id, terminal_id)`, and
**every write to a live drawer is own-terminal-only**. Only the device standing
at the till can witness cash physically leaving it.

Reads are deliberately *not* symmetrical:

| act | scope | why |
| --- | --- | --- |
| open / movement / close | own terminal only | cash leaving a drawer can only be witnessed at that drawer |
| read own drawer | `retail.cash.close` | the till operator needs their own state to work |
| read another till's drawer | `retail.reports` or `retail.cash.approve` | another till's drawer is a *report*, not state |
| approve a variance | `retail.cash.approve`, **any** terminal | otherwise the only person who could accept a shortfall is the person standing where it happened |

That last row is the point of the split. Approval is a back-office act on a
count that is already locked; requiring the approver to walk to the till would
make separation of duties physically impossible in a small shop.

## The ENDED / CLOSED distinction

v16 adds `ended_at` / `ended_by` / `ended_reason`, because one column pair was
being asked to record two different events.

* **ENDED** — the shift is over and the drawer was counted, but the variance has
  not been accepted by anyone. This is where a cashier's short count lands.
* **CLOSED** — someone with `retail.cash.approve` has looked at that count and
  accepted it.

A cashier who counts short must be able to finish and go home. A shortfall that
cannot be recorded is a shortfall that gets pocketed or invented, so
`close_cash_session` deliberately carries `retail.cash.close`, **not**
`retail.cash.approve` — what the approval authority changes is the *status* the
close produces, never whether the close is allowed.

`retail.cash.approve` is withheld from every role that holds
`retail.cash.close`, manager included, so no role can count its own drawer and
sign off its own shortfall (AUDIT-032).

## What a force-end may and may not write

The migration ends shifts the shop never ended. It writes `status`, `ended_at`,
`ended_by='System'`, `ended_reason` — **and nothing else**. Every absence is
load-bearing:

* a `variance` figure would claim somebody counted this drawer and found it
  short or over. Nobody counted it. `closing_float_counted`,
  `closing_float_expected` and `variance` stay NULL, and the reason text says
  `unverified_*`, so a consumer reading either one gets the same answer.
* `closed_at` / `closed_by` stay NULL, which is what keeps ENDED distinguishable
  from CLOSED on a row the migration touched. Filling in the close trail would
  launder a shift that never faced the approval question into one that had.

Each force-end also writes a per-session `audit_log` row carrying
`counted: false` and a plain-language note, with `user_id` NULL because no
person did it.

## What nearly shipped

Every item below was reproduced end to end. None was found by reading the code.

### Two ways a shop lost money silently

**A live drawer no device could touch.** `local_terminal_id()` is
`peek_local_device_uuid()`, which by design never *creates* `local_device.json`.
The only production writer is reachable through `GET /api/devices/me`, which the
shell calls during init — **after login**. `init_retail()` runs at **process
boot**. So on the first boot after upgrading, v16 ran with no terminal, left
every open drawer at `terminal_id IS NULL`, and advanced `user_version` to 16
anyway. The migration never runs again; the device identity appears seconds
later. The cashier's counted opening float is then unreadable, unclosable and
unsellable-into, permanently.

The fix is the general lesson of this phase: **`user_version = 16` stopped being
treated as proof that the bind happened.** `_v16_rebind_orphaned_open_drawers`
runs on *every* boot, is not version-gated, no-ops when there is still no device
identity, and *skips* rather than raises when this terminal already holds an
open drawer for that company — binding would violate the partial unique index,
and a boot must never crash over it.

**The overnight shop.** The stale sweep force-ended any drawer opened before
today's business date. A POS is precisely the software that trades past
midnight: a shop still serving at 01:00 that never configured
`business_day_start_hour` had the drawer its cashier was standing at ended by
the upgrade and recorded permanently as never counted. A drawer must now be a
full business day stale. That is safe for the constraint, because the collision
pass below it already resolves same-terminal contention — nothing depended on
the sweep to make the index creatable.

### Two ways the till refused to start

Both left the operator a raw SQLite string naming no shop, no shift and no
recovery — on paths `RetailCashDrawerBindError` was written to cover and did
not. `app.py:378` calls `init_retail()` unconditionally with no handler, so a
migration refusal *is* a POS that will not start, on every launch, forever.

* **A blank that was blank twice over.** Step 4 blanked with SQL `TRIM` (0x20
  only); step 5 skipped with Python `.strip()` (all whitespace). `''` and `'   '`
  agreed; `'\t'`, `'\t\t'`, `'\xa0'`, `'\n'` did not. Two open drawers on
  terminal `'\t'` are a *genuine* duplicate — SQLite's own index refuses them —
  but step 5 called them unnameable and declined to resolve them, so the probe
  then refused the migration. One predicate now, used by both.
  `'  X  '` and `'X'` remain **different** terminals, because they are different
  to the index, and over-grouping costs a shop a live till.
* **An index name is database-global; the check was table-local.**
  `_uid_index_shape` reads `PRAGMA index_list("cash_sessions")` and correctly
  returns None when the name is held by another table — so the bare
  `CREATE UNIQUE INDEX` raised `OperationalError: index ... already exists` out
  of `init_retail()`. Now looked up in `sqlite_master` and refused by name.

### The ordering guard the suite could not see

Swapping the two DDL statements passed **all 24 tests**. The damage is real:
with a power cut at the CREATE, the shipped order leaves the branch index
standing and a second drawer is refused; the swapped order leaves
`indexes after the interrupt: []` and accepts one — two open drawers on one
branch, the v10 disaster v16 exists to control.

The suite was blind for a structural reason, not an incidental one: the
interrupt test injected its failure at `_v16_collision_probe`, which runs
**upstream of both statements**, so it asserted an outcome at a point where the
ordering could not matter. Both statements now route through one seam and the
interrupt lands *between* them.

### A brand-new shop could not close its first drawer

Pre-existing, and squarely in this flow. `_cash_session_report` queries
`payments.direction`, a column the lazy `_ensure_credit_schema()` adds and no
drawer route called. A shop that opened the till as its first act and rang
nothing got a raw 500 — with the SQL string in the response body — from both the
X-report and the close, so the drawer could not be closed at all. The close-out
modal fetches the X-report, so the screen was dead too.

On the write side the schema call had to go **before** `BEGIN IMMEDIATE`:
doing it inside the transaction commits mid-transaction and silently breaks the
one-snapshot guarantee the close depends on.

## Tests that were red, blind, or lying

Recorded because this project keeps meeting the same three shapes, and Phase 4
produced one clean example of each.

* **A fixture that manufactured the state that hid the bug.**
  `current_cash_session`, `list_cash_sessions` and `get_cash_session` were
  ungated — `list_cash_sessions` handed every drawer in the company, with its
  opening float and its shortfall on each row, to anybody who could log in. They
  sat on an exemption list reading "return session STATE rather than a money
  report", which sounds true and is not. Nobody caught it because the runtime
  money sweep's fixture **never opens a cash session**, so every response it had
  ever measured was `null` or `[]`.
* **A guard whose pass condition was the bug signature.** The drawer's contrast
  check died on its own broken scrape — it read `rule.declarations` where the
  parser returns `decls` — before comparing a single pair. Forcing
  `--text-secondary` to `#fdfdfd` produced the *identical* message as a clean
  run. Only its anti-vacuity assertion kept it from reporting green on an empty
  list, and that assertion stays. The drawer's colours were correct throughout;
  only its guard was blind.
* **Asserting an outcome where the check running is what matters.** Eleven v15
  tests failed on a stale `== 15` whose message claimed *"the ordinary legacy
  shop was refused; the POS does not boot"* — fired whenever the version was
  anything but 15, **including when it was higher**. Changing 15 to 16 would
  relive this at v17, so the tests now ask the question each one means: refused
  is `== 14`, advanced past the gate is `>= 15`, landed on head is
  `== RETAIL_SCHEMA_VERSION`. `>= 15` cannot hide a failure —
  `migration_safety.py:107` writes `user_version` once, at the end, so any
  failure anywhere leaves the marker at 14.

Also worth recording: a *new* test was vacuous 22 hours a day.
`test_a_drawer_that_merely_crossed_midnight_two_hours_ago_stays_open` anchored
to `now() - 2h`, so outside 00:00–02:00 no boundary was crossed. It passed with
the fix reverted, while its docstring claimed "running this at any hour still
exercises the fix". Found by mutation, not by review.

## Verification

    retail 105 files / 105 passed   clinic 13   commercial_runtime 3   sync 3
    identity 9   licensing_contracts 22   einvoicing 14   notifications 10
    179 test files across eight suites, zero failures

The end-to-end scenario is the phase's real claim, and it is proven
*falsifiable*: reverting `_open_cash_session_id` reproduces the original defect
as the arithmetic at the top of this document.

## Left standing, deliberately

* **An admin closing a drawer self-approves in the same act.** Documented in
  `close_cash_session` as the intended owner path — a one-person shop has no
  second person to wait for, and refusing would leave it a drawer it can never
  close. The cashier path correctly stays `ended`/unverified, and every approval
  records `self_approved` on the response and in the audit line. **Wants a
  conscious sign-off from whoever owns AUDIT-032 before release.**
* **`app.py:378` still calls `init_retail()` unconditionally with no handler.**
  Both refusal paths this phase could reach are closed, but the boot policy
  itself is not this phase's to set.
* **A multi-branch shop running several branches from one install** has all but
  one open drawer force-ended by v16. Deliberate, explicitly tested, and no
  money is fabricated — but it is a real operational consequence, and whether
  such a deployment exists in the field could not be determined from the repo.

## What Phase 5 inherits

Phase 5 is where devices exchange data. Two things from here matter to it:

1. `cash_sessions` now has a **device-scoped** identity, so a session is
   attributable to the terminal that owned it rather than to a building. Without
   that, syncing drawers between devices would merge two shops' tills into one
   row and call it convergence.
2. The ENDED/CLOSED split means a synced drawer carries whether anybody has
   *accepted* its variance, separately from whether it was counted. A sync that
   collapsed those would replay a shop's unapproved shortfalls as approved.

Prerequisites remain as recorded in `phase5-prerequisites.md`.
