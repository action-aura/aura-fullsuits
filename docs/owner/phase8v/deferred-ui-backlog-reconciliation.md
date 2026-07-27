# Phase 8V — Deferred UI Backlog Reconciliation

Every deferral notice left by Milestones 2, 4, 5, and 6 (grep for "No routes" / "deferred" across
`docs/owner/phase8/*.md`), reconciled against `phase8-service-route-map.md`:

| Milestone | Deferral notice | Status after Phase 8V |
|---|---|---|
| M2 | "JSON API only in this milestone -- no Jinja templates yet" (`commercial_ops/routes.py` docstring) | Closed -- HTML UI added alongside the existing JSON API (JSON API kept, unchanged, for any future programmatic caller) |
| M4 | "No routes/CLI written yet for pilots or emergency extensions" | Closed -- both get full UI |
| M5 | "No routes or CLI for pilots/emergency extensions yet (Milestone 5/6)" — carried forward | Closed |
| M5 | "No routes/CLI for pilots/emergency extensions" for activation policy/pending activations/device-slot exceptions | Closed |
| M6 | "No routes/CLI for viewing a role's queue interactively" | Closed |
| M6 | "No drill-down UI on the new dashboard counts" | Closed -- counts now link to their queue/list route |

## New permissions this phase adds

None. Every workflow above maps onto a permission code that already exists in
`app/staff/seed_data.py` (see the map doc's Permission column). No new `Permission`/`Role` rows, no
new migration.

## Gaps found during reconciliation that are NOT closed by this phase (documented, not silently skipped)

1. **Reconciliation "repair" workflow** (`approve_repair()`/`apply_repair()` in the governing brief's
   Part K) — no such service function exists anywhere in `commercial_ops/reconciliation.py`.
   Milestone 6 built `run_reconciliation()` as report-only-or-notify, by design, with no automated or
   staff-approved repair action of any kind (a reconciliation finding is *always* resolved by a human
   using the *existing* domain action the finding points at — e.g. `release_device_slot()` for an
   over-limit finding — never a generic "repair" verb). Inventing a new generic repair-approval
   service now would be new domain design, not UI closure, and is out of scope for a phase whose
   brief says "do not redesign the Phase 8 domain."
2. **Owner-side Arabic/RTL localization** — never existed for the Owner control room across Phases
   5-8. See `phase8v-scope-and-baseline.md`.
