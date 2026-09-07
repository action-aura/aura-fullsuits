# Aura FullSuits — AI Context Document

Written for any AI assistant (Claude Code or otherwise) working in this repo.
Purpose: understand what this project actually is, how it's actually built,
who's actually working on what, and what "a good suggestion" looks like here
— before touching code or proposing changes.

Root `README.md` is the short public-facing intro. This document is the deep
one: architecture, conventions, team dynamics, and known gaps, so a fresh AI
session doesn't have to re-derive them from scratch or guess.

## What this is

Aura FullSuits is Action Aura's commercial product suite, extracted from a
larger internal monorepo ("Action Aura Enterprise") to ship faster as
independent products:

- **Aura Retail** (`products/retail/`) — POS/retail management, the most
  actively developed product.
- **Aura Clinic** (`products/clinic/`) — clinic management, structurally
  mirrors Retail (`backend/desktop/frontend/packaging/tests`).
- **Aura Owner Control Center** (`owner/`) — internal admin platform for
  Action Aura itself (customers, subscriptions, licensing, payments,
  employee management, CRM, i18n/RTL, audit). Not customer-facing.

Each product is standalone: its own SQLite database, its own Flask backend,
its own desktop packaging. They share code only through `commercial_runtime/`
(identity/auth, licensing, e-invoicing, sync, backup, security) — not through
a shared monolith.

## Repository layout

```
products/retail/       Flask backend (raw sqlite3, no ORM) + vanilla JS frontend (no framework/bundler)
products/clinic/       Same shape as retail
commercial_runtime/    Shared: identity, licensing_contracts, einvoicing, sync, backup, security
owner/                 Owner Control Center backend + dashboard (Flask/Postgres, separate stack)
android/aura-retail/   Native Android (Kotlin, Jetpack Compose) embedding the Python backend via Chaquopy
android/aura-clinic/   Same shape, Clinic
requirements/          base.txt / retail.txt / clinic.txt / owner-server.txt / development.txt
scripts/sync/           aura-sync — two-clone git auto-sync tool (see docs/ops/auto-sync.md)
docs/                   architecture, audit, corrections, einvoicing, hardware, licensing, migration,
                        mobile, ops, owner, privacy, release, security
```

A **separate, newer mobile app** exists on an unmerged feature branch, not on
master: `mobile/aura-retail-unified` — Kotlin Multiplatform (KMP), Compose
Multiplatform, targeting Android *and* iOS from one shared codebase. Android
side builds; iOS source is correctly structured but not compilable without a
macOS host (no code gap, environment gap). This is meant to eventually
replace/supersede the older `android/aura-retail` native app — both currently
coexist.

## How the backend is actually built — don't assume standard tooling

- **No ORM. No Alembic. No SQLAlchemy.** Raw `sqlite3`, hand-written SQL,
  confirmed explicitly in `requirements/retail.txt`. Suggesting Django/
  SQLAlchemy-style patterns will not compile.
- **Schema migrations** use `PRAGMA user_version` versioning
  (`commercial_runtime/security/migration_safety.py`): a `*_SCHEMA_VERSION`
  constant per product, a single `_apply_*_alters(conn)` function, applied
  via `ensure_schema_version()` — which takes a live backup and runs
  `PRAGMA integrity_check` before *and* after every migration, and only
  advances the version marker on full success. This codebase is unusually
  paranoid about migration safety on purpose (read that file's docstring
  before proposing a schema change). Never hand-roll an unconditional
  `ALTER TABLE` outside this pattern.
- **Frontend is vanilla JS**, no React/Vue/build step. Files are served
  directly by Flask's `static_folder`, no bundling, no transpilation.
- **Desktop app** = the same Flask backend + a `pywebview`-based launcher
  (`products/*/desktop/launcher_*.py`) with graceful fallback to Edge/Chrome
  `--app` mode, then default browser. Packaging via PyInstaller +
  Inno Setup exists (`products/*/packaging/`) but is explicitly marked
  **not build-verified** in the spec file itself.
- **Every business table is `company_id`-scoped** (multi-tenant per
  install — one install *can* host more than one company) and, for Retail,
  further `branch_id`-scoped for physical locations. Any new table or query
  that skips this scoping is a bug, not a simplification.

## Licensing model

- Issued by the Owner Control Center (`owner/app/security/license_keys.py`),
  shown once to the admin, forwarded to the customer manually.
- Format: `AURA-<RETAIL|CLINIC>-...`, activated via
  `/api/licensing/activate` in the product app's own licensing UI
  (`products/*/frontend/licensing.html`).
- Device-bound (Ed25519 signed lease, `commercial_runtime/licensing_contracts/`),
  verified against a trust anchor.
- **An install with no licensing configured is READ-ONLY, not unlocked.** This
  paragraph used to claim the opposite — "if `OWNER_LICENSING_BASE_URL` is
  unset, licensing is not enforced at all, the app runs fully unlocked" — and
  that is false. Measured against a fresh install on 2026-08-24:

      GET  products / sales / cash-sessions      200
      POST sales            403  LICENSE_INACTIVE
      POST cash-sessions/open  403  LICENSE_INACTIVE
      POST products         403  LICENSE_INACTIVE
      POST returns          allowed (on the allowlist)

  With no licence record, `LicenseStateRepository.load()` returns nothing and
  the state resolves to `NOT_CONFIGURED`, which is in `DATA_PRESERVED_FAMILY`
  but **not** in `ACTIVE_FAMILY` (`licensing_contracts/state_machine.py`), so
  `evaluate_capability` denies anything outside the restricted allowlist. This
  is deliberate and pinned — see `test_pre_activation_states_deny_new_mutation`
  in `licensing_contracts/tests/test_capability_guard.py`, and the "Part Y"
  reasoning it cites. `require_license_capability` never reads
  `OWNER_LICENSING_BASE_URL` at all; it reads `licensing.db`.

  **So a dev machine or demo cannot ring a sale or open a cash drawer until a
  licence is activated.** Correcting this here because the old claim sent
  anyone reading it — human or AI — looking for a bug in the licence gate that
  is actually the gate working as designed.
- Feature gating uses a `require_license_capability(...)` decorator +
  `RETAIL_RESTRICTED_ALLOWLIST` — restricted/expired license state allows
  read-only operations (view, reports, backup, returns, customer payments),
  blocks mutations.
- **Value-shaped limits ride the assertion's `entitlements` dict**, not a
  new column: Owner's `resolve_entitlements` merges plan → add-on →
  per-licence override, the signed assertion carries the dict, the till
  stores it in `licensing_state.entitlements_json`, and a route reads it
  through `flask_guard.make_entitlement_reader`. `max_branches` (2026-09-06,
  the 250 JOD branch add-on) is the worked example: `create_branch` refuses
  past it with `403 BRANCH_LIMIT`; absent or 0 means no limit. Adding a new
  paid limit is a seeded definition, a plan/add-on value, and one gate —
  no migration anywhere.
- **A second device joins a shop with the key alone — it must not create an
  admin.** `POST /api/licensing/activate` needs no session; the identity
  rebind seeds `company_settings` with the licence id when no admin exists
  (`company_rebind.seed_company_settings_for_joining_device`), and the
  owner's real account then arrives by sync. The desktop first-run modal
  has a join mode for this (`app-shell.js` `_openFirstRun` /
  `_isJoinedDevice`, both `init()` and the 401 path go through it) and
  Android asks once after activation (`FirstRunDecision`,
  `JoinChoiceScreen`/`JoiningScreen`). "Device holds an Owner-issued
  `installation_id` and is not in a pre-activation state" is the signal on
  both — a data fact from `status_presenter.py`, never a hand-typed list of
  active states (the backend never emits a bare `ACTIVE`). Proven in a real
  browser on fresh tills 2026-09-06; design and measurements in
  `docs/launch-readiness/join-existing-shop-design.md`.

## E-invoicing (Jordan JoFotara/ISTD)

Opt-in, off by default, invisible to installs that never enable it
(`commercial_runtime/einvoicing/`). Deliberately uses its own dedicated
sequence for the number submitted to the tax authority — never reuses
either product's local document number — see
`docs/einvoicing/phase1/invoice-numbering-audit.md` for the full reasoning.
This is the reference pattern for any future "must talk to an external
authority/channel reliably" feature (see Sync and Notifications below).

## Sync (in progress — read this before touching shared files)

Multi-device/multi-terminal sync is being built in
`commercial_runtime/sync/` (outbox pattern: `sync_outbox`, `sync_cursor`
tables) plus a UUID migration for `products`/`customers`/`suppliers`
(moving off autoincrement IDs so records stay globally unique across
devices). This is real, tested infrastructure, not a stub — but it's
**mid-migration**: expect `*_new` staging tables alongside the originals
until it completes. If you hit a schema/migration error, check whether
you're running against a database that's been touched by more than one
branch's schema version before assuming it's a real bug — mixed-schema-state
from testing across branches produces misleading errors that don't
reproduce on a clean install.

## Team dynamics — read this before editing shared paths

Two people work in this repo. Git identity vs. GitHub login is *not*
1:1-obvious here — resolve authorship via GitHub's `commits` API
`author.login`, not local commit email strings, if it ever matters.

- One person does nearly all of the Owner Control Center work (`owner/` +
  `docs/owner/`, `docs/audit/`) — licensing/commercial-ops lifecycle,
  employee management, CRM, i18n/RTL, staging/deploy — across several
  long-lived feature branches (`phase*`/`phase9.5/*`), and increasingly also
  the new sync engine and unified mobile app (`commercial_runtime/sync/`,
  `mobile/aura-retail-unified`).
- `master` lags significantly behind these branches — hundreds of files'
  worth in `owner/` alone. Don't assume `master` reflects current reality
  for anything Owner-related.
- `scripts/sync/aura-sync.ps1` / `.sh` — a two-clone auto git sync tool,
  built this project, `master`-only by default with an opt-in
  `SYNC_BRANCHES=current` / `-TrackCurrentBranch` mode to follow whatever
  branch is checked out. See `docs/ops/auto-sync.md`.
- Before editing a shared/root-level file (`requirements/base.txt`,
  `.gitignore`, `README.md`, anything under `owner/` or `docs/owner/`),
  check active branches first — collision risk is real and has already
  surfaced concretely in this project (see `ROADMAP.md`'s supplier_id
  timing note as one example of "coordinate before a shared schema locks
  in").

## Conventions worth matching

- Heavy inline comments explaining **why**, not what — especially for
  anything that was a real, previously-shipped bug. Correction docs live
  under `docs/corrections/<area>/root-cause-analysis.md`. This repo takes
  "leave a trail" seriously; match that style rather than terse code with
  no rationale.
- Bug/audit findings are tracked as `AUDIT-NNN` references in code comments
  and docs, not just fixed silently.
- Phased/waved naming: `Phase 8`, `Phase 9`, `Phase 9.5*` (Owner), `Wave 1B`,
  `Milestone 1-11+` (KMP mobile). Don't invent new numbering schemes —
  `ROADMAP.md` exists specifically to hold new/unscheduled items until they
  get assigned a real Phase/Milestone by whoever owns that area.
- New async/external-facing features (notifications, further integrations)
  should follow the e-invoicing outbox pattern — proven, already in
  production — rather than a bespoke one-off integration.

## Themes and brand (2026-09-07)

Five sanctioned themes on both clients, same keys: `light` (Day), `sand`,
`dark` (Calm — the phone's original palette), `night`, `dusk`. A theme is
a block of TOKEN VALUES ONLY: on the desktop, `html[data-theme="…"]` blocks
in `css/main.css` between `[design-tokens-<name>:begin/end]` markers,
guarded by `retail_design_theme_safety_test.js` (structure, one sanitizer
over the frozen `THEME_NAMES` allowlist, boot mirror) and
`retail_design_contrast_test.js` (palette cross-products and the rendered
corpus, per theme); on Android, `AuraColors` palettes in `ui/theme/Color.kt`
behind the unchanged token names (getters over `AuraPalette.current`),
`DesktopTokenParityContractTest` comparing each palette to its desktop
block. Never add a theme-scoped paint rule, never a colour literal outside
`Color.kt`, never a second allowlist. Brand assets (mark, app icon, lockup,
intro) live under `products/retail/frontend/brand/` and are not yet wired
into any screen. The full design brief — philosophy, brand geometry, every
theme's token values, the recipe for adding a theme, and the open design
work — is `DESIGN.md` at the repo root; read it before any visual change.

## What's real and solid right now

Barcode scanning (real hardware-scanner support, not a stub), AR/AP
(receivables, payables, statements, aging — unusual depth for a small POS),
multi-branch data model, Jordan e-invoicing, multi-device sync design
(outbox + UUID migration, correct eventual-consistency approach), Windows +
Android builds.

## What's genuinely missing

Re-verified 2026-08-30 by grepping for each claim. **Three of the bullets this
list used to carry were false**, under a heading that said it had been verified
by reading the code. They are corrected below rather than quietly deleted,
because a wrong "missing" list is the most expensive kind of error in this
document: it is exactly what makes a session build a second copy of something
that already ships, or propose as new work something a colleague finished.

Genuinely missing — or, in the first case, genuinely HALF-BUILT, which needs
saying differently because "absent" would send a reader to write code that
already half-exists. Each re-verified 2026-09-01 against schema.py and
retail_api.py, not from memory:

- **Loyalty is ACCRUAL-ONLY, which is worse than absent.**
  `customers.loyalty_points` is incremented on every sale (`create_sale`'s
  accumulator UPDATE, retail_api.py) and **nothing anywhere can ever spend
  it**: zero redemption routes, zero `redeem` in the whole backend, zero in
  the frontend. So a shop accumulates a number that does nothing, and a
  customer who asks what their points are worth has no answer. Treat this as
  a half-feature to finish or hide, not as a gap to fill from scratch.
- **No coupons, no customer-group pricing, no buy-X-get-Y.** These are the
  parts of "promotions" that are genuinely still missing — see the correction
  below, because the promotions ENGINE itself now exists and this bullet used
  to deny it.
- **No inter-branch stock transfer workflow.** `branches` exist and stock is
  branch-scoped, but the word `transfer` does not appear in retail's schema or
  API at all.
- **A branch cannot be retired.** Rename/address/phone edits exist since
  2026-09-06 (`PUT /branches/<id>`, an Edit control on the desktop screen,
  the rename converges to other devices through the existing `branch`
  update event). Retiring one touches stock balances, the per-device branch
  pin and open cash drawers, and is still its own design.
- **No SMS and no push.** See the correction below — WhatsApp and email do
  exist, so this is now a narrow gap rather than a blanket one.
- **iOS build requires a macOS host** that does not exist in this dev
  environment yet. Environment gap, not a code gap.

### Corrections (round 2, 2026-09-01) — two MORE things it wrongly called missing

Re-verified by grepping schema.py and retail_api.py, the same way the round-1
corrections below were. Both bullets removed above had been true when written
and were shipped afterwards without the list being updated, which is precisely
the failure mode this section exists to catch — and it has now happened twice,
so treat any claim in this document older than the code as unverified.

- **The promotions ENGINE exists.** The old text said "no rules, no date
  windows, no buy-X-get-Y, no customer-group pricing, no coupons, nothing in
  schema". In fact retail schema **v23** landed promotions wave 1: 51
  references in schema.py, 47 in retail_api.py, and five routes including
  `GET /promotions/active`, which the POS resolves at checkout. There is a
  `promotions` nav entry gated on `CAP_DISCOUNT` (app-shell.js), reusing that
  existing capability deliberately rather than minting a new one — configuring
  a promotion is the same authority as typing a manual discount at the till.
  Still genuinely absent: coupons, customer-group pricing, buy-X-get-Y.
- **Product variants exist.** The old text said "`products` is a flat
  SKU/barcode row: no `parent_product_id`, no variant/option columns anywhere
  in schema.py". There is a `parent_product_id` column (schema.py:5052) with a
  partial index, a `variant_label`, a `GET /products/<pid>/variants` route, and
  a POS guard that refuses to sell a parent outright ("has variants — choose a
  specific variant to sell"). Retail schema **v25**.
  Restaurant-style **modifiers** are a separate matter: schema and the config
  API both shipped (**v26**), but the POS picker screen and the cart merge-key
  rework were deliberately cut to protect the money path, and the two modifier
  routes are parked in `retail_route_reachability_test.py`'s
  `INTENTIONALLY_UNREACHABLE` with that reason in writing.

### Corrections — three things this list wrongly called missing

- **Notification infrastructure EXISTS.** The old text said "no SMS, WhatsApp,
  email, or push. Zero, not partial." In fact `commercial_runtime/
  notifications/` carries WhatsApp settings, recipients and an **outbox**, plus
  an SMTP client for email, and `core/retail/whatsapp_hook.py` wires four real
  trigger points (low stock, shift close, daily sales summary, AR overdue).
  Schema migrations for it are in the chain (`_migrate_add_notifications_
  foundation`). It follows the e-invoicing outbox pattern, as this document
  recommends for new async features — because it is one of the things that
  established it.
- **RBAC EXISTS.** The old text said "only a bare `role` string, no permission
  matrix." There is a fixed capability tuple (`CAPABILITY_CODES`) and named
  roles in `commercial_runtime/identity/user_accounts.py`, enforced by
  decorators on the routes, with a whole-surface test
  (`retail_route_capability_matrix_test.py`) that fails when a new route is
  added without declaring its capability. `ROLE_MANAGER` deliberately excludes
  `CAP_EMPLOYEES` and `CAP_CASH_APPROVE` — see AUDIT-032, self-approval.
- **Shift / cash-drawer management EXISTS.** The old text said there was none,
  including "float in/out, X/Z reports." `cash_sessions` carries
  `opening_float` and `variance`, there is an X-report route
  (`cash_session_x_report`), a Z-report on close, a variance-approval route
  behind `CAP_CASH_APPROVE`, and Phase 4 bound the drawer to a TERMINAL rather
  than a branch. A ratchet test refuses any new cash-session route that has not
  declared its terminal scope.

## How to give a good suggestion here

1. Check whether it's already built before proposing it — this codebase has
   more real infrastructure than it looks like at first glance (see "solid"
   list above). Verify against actual files, not assumptions from a typical
   POS-system mental model.
2. Respect the "invisible unless opted in" philosophy — new features should
   default off and cost nothing to an install that doesn't use them, the
   same way e-invoicing and licensing enforcement both do.
3. Never propose an ORM, Alembic, or a framework-based frontend rewrite
   without being asked — that's not a fit-for-purpose disagreement, it's a
   different codebase.
4. Any schema change: use the versioned migration pattern, bump the version
   constant, never touch `ALTER TABLE` unconditionally outside it.
5. Check which branch/collaborator owns the area you're about to touch
   before editing shared files — this has been a live, real issue in this
   project, not a hypothetical.
