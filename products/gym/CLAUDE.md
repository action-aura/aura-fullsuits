# Aura Gym — AI Context Document

Brief written 2026-08-24. **Kickoff answered the same day** — the ten open
questions are now decisions, not guesses. Sections still marked ⚠️ OPEN are
genuinely undecided and must not be answered by inference.

## What this is

Aura Gym — gym and fitness-centre management, the third product in Action Aura's
commercial suite alongside **Aura Retail** (POS) and **Aura Clinic**.

It is a sibling of those two, not a fresh start. It reuses `commercial_runtime/`
and mirrors Retail's structure, so someone who knows Retail can read this.

## Where the code lives — decided at kickoff

Gym lives at **`aura-fullsuits/products/gym/`**, beside `products/retail/` and
`products/clinic/`, importing the shared `commercial_runtime/` in place.

The standalone `aura-gym` repo is the origin of this brief only. Copying
`commercial_runtime/` into a separate repo was explicitly considered and
rejected: the copies drift, and a bug fixed in Retail stays broken in Gym until
someone remembers to copy it across. There is one copy of the shared runtime and
all three products import it.

## Reuse before building — this is the first rule, not an optimisation

The suite already owns, tested and in production, things a gym system needs:

| Need | Already exists | Do not rebuild |
|---|---|---|
| Login, users, roles, capabilities | `commercial_runtime/identity/` | auth, sessions, permission checks |
| Licensing / activation | `commercial_runtime/licensing_contracts/` | the whole licence lifecycle |
| Multi-device sync | `commercial_runtime/sync/` | the outbox pattern |
| Safe schema upgrades | `commercial_runtime/security/migration_safety.py` | backup + integrity-check + versioned migration |
| Backup / restore | `commercial_runtime/backup/` | |
| Jordan e-invoicing (JoFotara) | `commercial_runtime/einvoicing/` | if memberships are invoiced |

**Check whether something is already built before proposing it.** The suite has
more real infrastructure than it looks like from the outside.

## Architecture — mirror Retail exactly

- **Flask backend, raw `sqlite3`, hand-written SQL.** No ORM. No Alembic. No
  SQLAlchemy. Suggesting them will not compile against this stack.
- **Vanilla JS frontend.** No React/Vue, no bundler, no build step, no CDN —
  Flask serves the files directly and **gyms run offline**.
- **Schema migrations** use `PRAGMA user_version` versioning through
  `ensure_schema_version()`, which takes a live backup and runs
  `PRAGMA integrity_check` before *and* after, advancing the version marker only
  on full success. Read that file's docstring before proposing any schema
  change. Never hand-roll an unconditional `ALTER TABLE` outside this pattern.
- **Desktop app** = the same Flask backend plus a `pywebview` launcher with
  fallback to Edge/Chrome app mode, then the default browser.
- **Every business table is `company_id`-scoped and `branch_id`-scoped.** A table
  or query that skips either is a bug, not a simplification. Branch scoping is
  mandatory from the first migration even though only one branch exists today —
  see "Branching" below.
- **New features default OFF and cost nothing to an install that never enables
  them** — the way e-invoicing and licensing enforcement both do.

## The domain

A gym is not a shop. The central object is not a sale, it is **a membership that
is valid over a span of time** — and almost every hard bug lives in that span.

### Core entities

- **Member** — a person, belonging to one branch. Photo, contact, emergency
  contact, join date.
- **Membership** — one active per member: plan, start, end (inclusive), state,
  outstanding balance, accumulated frozen days.
- **Plan** — what is sold: monthly, quarterly, annual, class pack, day pass.
- **Check-in** — the daily operation, and the equivalent of Retail's sale. High
  volume, must be fast, **must work offline**.
- **Class / session** — scheduled, with a trainer, a capacity and bookings.
- **Trainer** — staff, with a schedule. Runs classes only; no personal training.
- **Payment** — against a membership, possibly partial.

## Kickoff decisions

Every one of these was asked and answered. Do not re-derive them, and do not
quietly widen them.

### Branching

One gym today, a second one possible later. **Every business table carries
`branch_id` from the first migration**, populated with a seeded default branch.
No branch-switching UI until there is a second branch.

**A membership belongs to the branch that sold it**, not to the company. If the
same person joins two gyms, that is two member records and two memberships. The
company does not own members; the branch does.

### Entitlement is a state machine, not a boolean

States: **`active` / `grace` / `expired` / `frozen`**.

At the end of a paid period a member enters **`grace`** — still admitted, with
reception shown a warning to collect payment. Entry is refused only once grace
runs out. Chosen over a hard stop specifically because retrofitting a state
machine onto a boolean is expensive.

⚠️ OPEN: grace length in days, and its default. Configurable per company.

### Dates — the rules, written down, because this is bug class #1

- **The end date is inclusive.** A membership ending 31 August works all of
  31 August. Paid through the 31st means the 31st is theirs.
- **The day boundary is midnight, local clock day.** Deliberately *not* a
  Retail-style trading day: this gym does not run sessions across midnight. If
  that ever changes, this decision has to be revisited, not patched around.
- **Entitlement is evaluated once, at check-in.** Not continuously. A member
  admitted at 23:55 is not ejected at 00:00. Say so in a comment at the point
  where it is evaluated.
- The gym's own date, not the raw device clock, decides all of the above.
  *(Retail shipped exactly this bug: reports read the device clock instead of
  the shop's.)*

### Freezes — unlimited, but audited

Staff may freeze a membership at any time, for any length, at their judgement.
**There is no system-enforced cap.** This was asked explicitly, the
extend-forever risk was stated, and unlimited was chosen.

Because the cap is absent, the audit trail is not optional:

- Every freeze records who applied it, when, and the exact days added.
- A membership shows its **running total of frozen days** wherever it is viewed.
- **Freezes must be idempotent against double-application.** Silently
  double-applying a freeze is the specific bug this brief names; a freeze carries
  an identity, and re-applying it is a no-op, not a second extension.

⚠️ OPEN: whether a freeze may be applied retroactively, and what happens when two
overlap. Unlimited does not mean undefined — both need answers before the freeze
code is written.

### One active membership at a time

A gym+pool offering is a *plan* that covers both, not two overlapping
memberships. Chosen because it keeps check-in to a single lookup, which is where
the entitlement bugs would otherwise live. Revisitable — but revisiting it
changes check-in, not just the schema.

**No personal training.** No session packs, no consumed balances, no countdown.

### Check-in

**Reception desk.** Staff look up the member, screen answers green/amber/red.

The decision to admit and the act of opening a door are **separate seams from
day one** — a turnstile or card reader is expected later and must plug into the
entitlement decision without rewriting it. Do not couple the entitlement check to
the desk UI.

Check-in **must work with no internet**, reconciling later — same shape as the
sync outbox. A door at 6am on a bad connection still has to admit members.

### Classes

Scheduled, with a trainer, a capacity, and bookings. Open-gym access as well.

Booking is a **concurrency problem**, not a form. Two receptionists booking the
last spot at the same moment must not both succeed. Use the same
`BEGIN IMMEDIATE` discipline the cash drawer uses — never an application-level
"check then insert", which loses the race silently.

⚠️ OPEN: cancellation window, no-show handling, waitlists.

### Payments

Cash at the desk, card at the desk, bank transfer, and **instalments** — all
recorded by staff against a membership. Instalments mean a membership can carry
an **outstanding balance**; that is part of the model, not an afterthought.

**No recurring card billing.** Considered and dropped. No payment gateway, no
stored cards, no failed-payment or card-expiry handling. Do not add one.

### Staff-only — members get no app

Everything happens at reception. No member accounts, no member passwords, no
external network exposure.

The data model should not *prevent* a read-only member view later, but nothing is
built for it now and no work is spent anticipating it.

### Licensing

Gym activates and licenses through the **Owner Control Center** (`owner/`),
exactly as Retail and Clinic do. Reuse `commercial_runtime/licensing_contracts/`
whole.

### Localisation

**Arabic and English, with RTL.** Same as its siblings.

Locale keys must exist in **both** catalogues or an Arabic operator gets English
back. Enforced by a test that fails on catalogue divergence, not by discipline.

## Conventions to match

- **Heavy inline comments explaining WHY**, not what — especially for anything
  that was a real, previously shipped bug. This suite takes "leave a trail"
  seriously.
- Bug findings tracked as `AUDIT-NNN` references in code and docs, not fixed
  silently.
- Correction docs under `docs/corrections/<area>/root-cause-analysis.md`.

## Engineering standard

`~/.claude/ENGINEERING.md` applies here in full — mutation-prove every guard, run
the real thing before claiming done, never weaken a test to go green, reserve
single-writer resources before parallel work. It is not optional and it is not
project-specific.

## Still open

Carried forward deliberately. Each is a real fork, not a detail:

1. Grace period length in days, and its default.
2. Retroactive freezes — allowed or not? Two overlapping freezes — what happens?
3. Class cancellation window, no-show policy, waitlists.
4. Whether memberships are invoiced through JoFotara e-invoicing.
