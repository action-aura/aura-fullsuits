# Aura FullSuits

A lightweight, independently deployable commercial suite containing:

1. **Aura Retail / POS** (`products/retail/`)
2. **Aura Clinic** (`products/clinic/`)
3. **Aura Owner Control Center** (`owner_control_center/`) — commercial/operational management platform for the Action Aura team, not for customers

This is a fast commercial distribution layer extracted from the Action Aura Enterprise monorepo — not a rewrite. Retail and Clinic are the same working products, repackaged to run independently and be sold sooner. See `docs/migration/` for the extraction record.

Designing for Aura — the brand, the five themes with exact token values, the
rules and the tests that hold them, and the design work still open — is
`DESIGN.md`. Building for Aura is `CLAUDE.md`.

## What this is not

- Not the full Aura Core / Aura Hub architecture (see `docs/AURA_CORE_ARCHITECTURE.md` in the source repo — unrelated to this project).
- Not a monorepo tool experiment — plain directories, no forced tooling.
- Not a place for enterprise features (CRM, HR, Accounting, AI) — those stay in Action Aura Enterprise.

## Repository layout

```
products/retail/       Aura Retail backend, frontend, database schema, desktop packaging, tests
products/clinic/       Aura Clinic backend, frontend, database schema, desktop packaging, tests
android/                Single shared Gradle project, two product flavors (retail / clinic)
commercial_runtime/     Shared: identity, installation, licensing contracts, telemetry, updates, diagnostics, security, networking
owner_control_center/   Owner-only backend + dashboard (customers, subscriptions, payments, installations, releases, support, audit)
deployment/             Docker / Nginx / systemd / backup scripts for a small VPS
scripts/                Build and validation scripts
tests/                  Cross-cutting integration/security/privacy/tenancy/smoke tests
docs/                   Architecture, deployment, operations, migration, privacy, commercial, handover docs
```

## Jordan JoFotara e-invoicing

Retail and Clinic can optionally report invoices to Jordan's ISTD JoFotara national e-invoicing system — off by default, no effect on any install that doesn't enable it. See `docs/einvoicing/phase1/`.

## Data ownership

Customer business data (products, sales, patients, invoices, etc.) stays local to the customer's installation. The Owner Control Center never stores or retrieves it — see `PRIVACY.md`.

## Keeping two local clones in sync

Working from two machines/devs against this repo? `scripts/sync/aura-sync.ps1`
(Windows) / `scripts/sync/aura-sync.sh` (macOS/Linux) auto-pulls, auto-commits,
and auto-pushes on an interval, and stops cleanly instead of guessing when it
hits a conflict. See `docs/ops/auto-sync.md`.

## Status

Under active extraction from Action Aura Enterprise. See `docs/migration/extraction-plan.md` for phase sequencing and `docs/migration/source-inventory.md` for what has moved so far.

## Current phase status (additive note, does not supersede the above — the repository layout above
reflects an earlier planning stage; the real current directories are `owner/`, `products/retail/`,
`products/clinic/`, `commercial_runtime/`, `android/`)

Commercial licensing/operations (Phase 8) is complete —
`docs/owner/phase8vp9/phase8-final-unconditional-decision-vp9.md`. Secure staging infrastructure and
operational hardening (Phase 9) reached CONDITIONAL PASS — real work (structured logging, dependency-
vulnerability remediation, real backup/restore, real scheduler, real capacity testing) verified against
a real local staging database and process; real remote deployment remains NOT VERIFIED (no cloud/VPS/
domain available in that session). See `docs/owner/phase9/PHASE9-SECURE-STAGING-AND-PILOT-READINESS-HANDOVER.md`.
