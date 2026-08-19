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
- **If `OWNER_LICENSING_BASE_URL` is unset (the default), licensing is not
  enforced at all** — the app runs fully unlocked. This matters for local
  dev/testing: nothing is gated unless Owner is actually wired up.
- Feature gating uses a `require_license_capability(...)` decorator +
  `RETAIL_RESTRICTED_ALLOWLIST` — restricted/expired license state allows
  read-only operations (view, reports, backup), blocks mutations.

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
- `master` used to lag these branches by hundreds of files in `owner/`. As of
  2026-08-19 that is no longer true — master carries 536 `owner/` files
  against 543 on the UI lineage tip, and the remaining gap is small and
  specific (the sync engine, the department-nav UI, the licensing P0 fixes).
  **`docs/ops/branch-and-release-map.md` is the current, measured answer** to
  "what is on master and what is the latest" — read it before assuming
  anything about Owner branch state, and re-verify rather than trusting it,
  since branches move.
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

## What's real and solid right now

Barcode scanning (real hardware-scanner support, not a stub), AR/AP
(receivables, payables, statements, aging — unusual depth for a small POS),
multi-branch data model, Jordan e-invoicing, multi-device sync design
(outbox + UUID migration, correct eventual-consistency approach), Windows +
Android builds.

## What's genuinely missing (verified by reading the code, not assumed)

- No notification infrastructure at all — no SMS, WhatsApp, email, or push.
  Zero, not partial.
- No real RBAC — only a bare `role` string, no permission matrix.
- No shift/cash-drawer management (float in/out, X/Z reports).
- No promotions/discounts/loyalty engine — nothing in schema.
- No inter-branch stock transfer workflow (branches exist, transfers don't).
- No product variants (flat SKU/barcode only, no size/color grouping).
- iOS build requires a macOS host that doesn't exist in this dev environment yet.

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
