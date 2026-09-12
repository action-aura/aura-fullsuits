# Phase 3 — ledger truth (retail v15)

Designed 2026-08-23, ahead of the phase, so the decisions below are made once
rather than discovered mid-migration.

This is the phase that answers the owner's original complaint — *"the stock is
not accurate"* — and it answers it in a specific order: **it makes the
inaccuracy measurable before it makes it go away.** A shop that is told "your
stock is now correct" has been given a claim. A shop that is shown *which
twelve products disagree, by how much, and since when* has been given
something it can act on and check.

## The property Phase 3 establishes

After v15, `inventory_balances` is **provably derivable** from
`inventory_movements`. The cache stops being a second source of truth and
becomes what its name says.

That property is what Phase 5 depends on. Once devices exchange movements, each
one recomputes its own balances from the ledger it now shares — but only if the
ledger can reproduce the balances in the first place. On an install where it
cannot, sync would silently replace a correct-looking number with a different
correct-looking number, and nobody could say which was right.

## Why an install cannot derive its stock today

`stock_reconciliation.compute_drift` already measures it, and its SQL is
deliberate about the two directions that a naive query would hide:

* **a balance row with no movements behind it** — every pre-existing install,
  plus `_seed_retail`'s balance-without-movement, plus every product whose
  opening stock arrived through the importer;
* **movements with no balance row** — a balance deleted, or never created.

Both are visible because the query UNIONs the two key sets rather than joining
from one side.

## v15, precisely

One idempotent function appended last in `_migrate_retail_schema`, applied
through `ensure_schema_version` (live backup, `integrity_check` on both sides).
Additive only. **`RETAIL_SCHEMA_VERSION` 14 → 15, reserved in `ROADMAP.md`.**

### 1. Seed an opening count for every unbacked balance

For each `inventory_balances` row whose `(product_id, branch_id)` has no
movements, insert exactly one movement of type `opening_count` with
`quantity = quantity_on_hand`.

It must carry `created_at_utc` and a `reason` that says what it is, and it must
**not** be attributed to a person. Nobody counted this stock; the migration
inferred it. Stamping a real `actor_user_uid` on it would be the same
fabrication v13 refused when it left `created_at_utc` NULL on history.

### 2. The gate — this is the part that is not just a migration

Run `compute_drift` **before** and **after**, and **refuse to advance
`user_version` if post-seed drift is non-zero.**

A failing v15 does not mean "the migration is broken". It means **this shop's
ledger genuinely cannot reproduce its balances**, which is exactly the
condition the phase exists to surface. Do not "fix" a failing v15 by relaxing
the check — that converts a detected problem into an undetected one.

`ensure_schema_version` already refuses to advance the marker on failure, so
the mechanism exists; v15 supplies the assertion.

### 3. The NULL-branch rows decide the shape of the gate

`compute_drift` returns `repairable: False` for legacy movements whose
`branch_id` is NULL: `inventory_balances.branch_id` is `NOT NULL`, so there is
no balance row that could ever hold them. This is the detail that decides the
whole design, and getting it wrong makes the phase unshippable:

**If the gate demands global zero drift, an install carrying even one
NULL-branch movement can never pass it, and can never advance its schema
version again — for any future migration.** That is the same permanent-wedge
failure the v13 duplicate-`uid` defect produced, arrived at from a different
direction.

So:

* the gate is **zero drift among repairable rows**;
* NULL-branch movements are migrated where the answer is unambiguous — a
  company with exactly **one** branch has only one place they can belong;
* where it is ambiguous (more than one branch), they are **surfaced to the
  owner to assign**, counted explicitly, and excluded from the gate with that
  count recorded. Guessing a branch would put real stock in the wrong shop.

An install with ambiguous rows still advances to v15. It just carries a visible
"needs assignment" queue rather than a silent wrong answer.

## The screen

An owner-facing **Stock accuracy** view over `compute_drift`. The route already
exists (`inventory_reconciliation`, company-admin gated — correctly, because
its response is a full dump of the catalogue and stock position).

What it must show, and the framing matters:

* **which** `(product, branch)` disagree, with drift signed — positive means
  the balance claims MORE than the ledger can account for, which is the
  double-received-PO and resurrected-by-import signature;
* **net drift** as the headline, and **drift count** beside it. Net alone lies:
  a +10 and a −10 net to zero while two products are wrong.
* an honest empty state. "No drift" must be distinguishable from "not
  computed" — this programme has already shipped one screen where an error
  rendered as emptiness.

Repair stays **explicit and owner-initiated**. Automatic repair on a drifted
shop is how you turn a visible discrepancy into an invisible one.

## `repair_drift` needs its own transaction

`repair_drift` currently manages **no transaction of its own** — its docstring
says the caller must supply `BEGIN IMMEDIATE` together with the audit row. That
is a contract nothing enforces, and an unenforced contract about writing to
stock is a bad trade. It takes its own `BEGIN IMMEDIATE`, writes its audit row
inside it, and is safe to call directly.

## Tests

* an install with balances and no movements migrates, and drift becomes zero;
* an install with an ambiguous NULL-branch row **still advances**, with the
  count surfaced — the wedge case;
* a genuinely undeducible install **fails the gate and does not advance**;
* idempotent: a second run seeds nothing and drift stays zero;
* the seeded movements carry no fabricated actor;
* `repair_drift` under concurrent writes leaves either a complete repair or
  none, never half;
* the screen distinguishes no-drift from not-computed.

Two anti-vacuity guards, because this phase is unusually easy to test
vacuously: assert the fixture **has** drift before the migration (a shop with
no drift proves nothing about a gate), and assert the gate **fails** on a
deliberately undeducible fixture.

## What this phase does NOT do

It does not make stock correct. It makes stock **derivable and measurable**. A
shop whose physical shelves disagree with its ledger still has a counting
problem, and no migration can fix that — the honest deliverable is that the
software now agrees with itself and can show where it does not.

## Estimate

~1 day, most of it the NULL-branch case and the tests around the gate.
