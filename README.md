# Aura FullSuits

A lightweight, independently deployable commercial suite containing:

1. **Aura Retail / POS** (`products/retail/`)
2. **Aura Clinic** (`products/clinic/`)
3. **Aura Owner Control Center** (`owner/`) — commercial/operational management platform for the Action Aura team, not for customers

This is a fast commercial distribution layer extracted from the Action Aura Enterprise monorepo — not a rewrite. Retail and Clinic are the same working products, repackaged to run independently and be sold sooner. See `docs/migration/` for the extraction record.

Designing for Aura — the brand, the five themes with exact token values, the
rules and the tests that hold them, and the design work still open — is
`DESIGN.md`. Building for Aura is `CLAUDE.md`.

## Install and try Aura Retail on Windows

Grab the latest installer from
[Releases](https://github.com/action-aura/aura-fullsuits/releases). It is a
one-folder PyInstaller build wrapped by Inno Setup: nothing else needs to be on
the machine, no Python, no runtime.

Four things worth knowing before you run it, none of them discovered later:

- **It is unsigned.** No Authenticode certificate has been bought yet, so
  SmartScreen will say "Windows protected your PC". Choose More info, then Run
  anyway. Buying a certificate is on the launch list precisely so customers are
  never asked to do that.
- **It installs read-only until a licence is activated.** With no licence the
  till deliberately refuses to ring a sale or open a cash drawer, answering
  `403 LICENSE_INACTIVE`; every screen is still browsable. That is the licence
  gate working, not a fault. Activation needs an Owner Control Center to issue
  a key against.
- **Your data is never in Program Files.** Business data lives in
  `%LOCALAPPDATA%\AuraRetail`, and a normal uninstall does not touch it, so
  reinstalling loses nothing. Deleting it is a separate opt-in step that asks
  twice.
- **There is no update channel.** A later build cannot reach an existing
  install by itself; you install the new one over the top.

To build the installer yourself, from the repository root:

```
pyinstaller products/retail/packaging/aura_retail.spec --noconfirm
iscc products/retail/packaging/aura_retail_setup.iss
```

The spec file's header comment records exactly what has and has not been
verified about those two commands, including the run and uninstall proofs.

## What this is not

- Not the full Aura Core / Aura Hub architecture (see `docs/AURA_CORE_ARCHITECTURE.md` in the source repo — unrelated to this project).
- Not a monorepo tool experiment — plain directories, no forced tooling.
- Not a place for enterprise features (CRM, HR, Accounting, AI) — those stay in Action Aura Enterprise.

## Repository layout

```
products/retail/        Aura Retail backend, frontend, database schema, desktop packaging, tests
products/clinic/        Aura Clinic backend, frontend, database schema, desktop packaging, tests
commercial_runtime/     Shared: identity, licensing contracts, e-invoicing, sync, notifications, backup, security
owner/                  Owner Control Center backend + dashboard (customers, subscriptions, payments, licensing, employees, CRM, audit)
android/                Gradle project per product (android/aura-retail, android/aura-clinic), Kotlin + Compose
mobile/                 Kotlin Multiplatform app targeting Android and iOS from one codebase (supersedes android/ eventually)
requirements/           base.txt / retail.txt / clinic.txt / owner-server.txt / development.txt
deploy/                 Staging and production deployment material
scripts/                Build, brand, and operations scripts, including the two-clone git auto-sync tool
docs/                   Architecture, audit, corrections, e-invoicing, licensing, migration, ops, owner, privacy, release, security
```

This block used to describe directories that were planned and never built
(`owner_control_center/`, `deployment/`, a root `tests/`), with a note appended
at the bottom of this file admitting it was wrong rather than correcting it.
The layout above is what is actually on disk.

## Jordan JoFotara e-invoicing

Retail and Clinic can report invoices to Jordan's ISTD JoFotara national
e-invoicing system. As of 2026-09-08 this is **on by default** — a fresh
install queues invoices from the start, so a shop that later gets ISTD
credentials has its history rather than a gap. Until credentials are entered
the install runs `UnconfiguredProvider`, which queues and never transmits, so
nothing leaves the machine. `AURA_EINVOICING_DISABLED=1` turns the whole
subsystem off with no database or file access at all. See
`docs/einvoicing/phase1/`.

## Data ownership

Customer business data (products, sales, patients, invoices, etc.) stays local to the customer's installation. The Owner Control Center never stores or retrieves it — see `PRIVACY.md`.

## Keeping two local clones in sync

Working from two machines/devs against this repo? `scripts/sync/aura-sync.ps1`
(Windows) / `scripts/sync/aura-sync.sh` (macOS/Linux) auto-pulls, auto-commits,
and auto-pushes on an interval, and stops cleanly instead of guessing when it
hits a conflict. See `docs/ops/auto-sync.md`.

## Status

Commercial licensing and operations (Phase 8) is complete —
`docs/owner/phase8vp9/phase8-final-unconditional-decision-vp9.md`. Secure
staging and operational hardening (Phase 9) reached CONDITIONAL PASS: the real
work (structured logging, dependency-vulnerability remediation, real
backup/restore, real scheduler, real capacity testing) was verified against a
real local staging database and process, while real remote deployment remains
NOT VERIFIED. See
`docs/owner/phase9/PHASE9-SECURE-STAGING-AND-PILOT-READINESS-HANDOVER.md`.

Current work is the launch-readiness programme: multi-device sync, the phone
client, the Windows installer, and the design system. `ROADMAP.md` holds items
that have no assigned phase yet.
