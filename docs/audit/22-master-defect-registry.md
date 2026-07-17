# Master Defect Registry

30 issues (AUDIT-030 added in Phase 3.7, launcher corrective wave — see
below). Full structured data (all fields) in `22-master-defect-registry.json`
and `22-master-defect-registry.csv` — this document is a human-readable index.
Severity: **P0**=4, **P1**=4, **P2**=11, **P3**=6, **P4**=5.
Release blockers (Gate 3, paid SMB): AUDIT-001, 002, 003, 004, 011, 012, 019,
022, 023, 030.

## P0 — Catastrophic (4)

| ID | Title | Product | Confidence |
|---|---|---|---|
| AUDIT-001 | Retail has no self-service onboarding/first-user path (any platform) | Retail | PROVEN |
| AUDIT-002 | Android Retail POS omits tax and discount on every sale | Retail/Android | PROVEN |
| AUDIT-004 | `create_return()` has no validation against the original sale (linkage/quantity/duplicate) | Retail | PROVEN |
| AUDIT-030 | Both Windows launchers' startup readiness check polls a route no server has ever served, killing or orphaning a healthy server | Retail+Clinic | PROVEN |

## P1 — Critical (4)

| ID | Title | Product | Confidence |
|---|---|---|---|
| AUDIT-003 | `create_sale()` trusts 100% of client-submitted financial totals | Retail | PROVEN |
| AUDIT-011 | Clinic `record_payment` has no amount validation | Clinic | PROVEN |
| AUDIT-012 | Clinic `record_payment` has no idempotency protection | Clinic | PROVEN |
| AUDIT-019 | No backup or restore capability exists anywhere | Both | PROVEN |

## P2 — High (11)

| ID | Title | Product |
|---|---|---|
| AUDIT-005 | Retail discount not clamped (can exceed subtotal / go negative) | Retail |
| AUDIT-007 | Retail returns don't separately track tax (reporting gap) | Retail |
| AUDIT-008 | Negative sale-line quantity inflates inventory disguised as a sale | Retail |
| AUDIT-009 | Server never prevents negative stock in `create_sale` | Retail |
| AUDIT-013 | Clinic invoice creation has no idempotency protection | Clinic |
| AUDIT-016 | Retail's SQLite connections never enable `PRAGMA foreign_keys=ON` | Retail |
| AUDIT-017 | `receive_purchase_order` has no transaction wrapper | Retail |
| AUDIT-018 | Clinic's `create_invoice`/`record_payment` have no explicit transaction wrapper | Clinic |
| AUDIT-020 | Clinic's `GET /patients/<id>` has no role gate (over-broad clinical read access) | Clinic |
| AUDIT-022 | Windows builds unsigned, no installer | Both |
| AUDIT-026 | No index on `products.sku`/`clinic_patients.name` — future performance risk | Both |

## P3 — Medium (6)

| ID | Title | Product |
|---|---|---|
| AUDIT-006 | Two rounding strategies coexist in Retail (dormant) | Retail |
| AUDIT-010 | Cross-file pytest run causes spurious failures (test-infra) | Both |
| AUDIT-015 | Clinic invoices have no cancelled/voided/draft state | Clinic |
| AUDIT-021 | Neither Android app sets `FLAG_SECURE` | Both |
| AUDIT-023 | Android staging/release APKs unsigned (known, documented) | Both |
| AUDIT-027 | Android "Backup & restore" settings row is a non-functional placeholder | Both |

## P4 — Low (5)

| ID | Title | Product |
|---|---|---|
| AUDIT-014 | Dead, always-failing "mirror into Accounting" call in Clinic | Clinic |
| AUDIT-024 | Unparameterized SQL identifiers in Retail's `.db` import preview (low severity) | Retail |
| AUDIT-025 | App version numbers not synchronized across platforms | Both |
| AUDIT-028 | `delete_product`'s branch-decision query not company-scoped (not exploitable) | Retail |
| AUDIT-029 | Secret-key file's `chmod` call is a no-op on Windows | Both |

## Phase 3.7 corrective status: AUDIT-030 (launcher watchdog, added and fixed same phase)

AUDIT-030 was discovered during the Wave 0 Windows packaged smoke test
(both launchers' server-readiness check always failed, killing or
orphaning an otherwise healthy server ~45s after a successful start — see
`docs/corrections/launcher/`), assigned a stable ID, classified
release-blocking, then fixed and verified within the same corrective
phase (Phase 3.7): `status: FIXED_AND_VERIFIED_LAUNCHER_PHASE` in the
`.json`/`.csv` copies. Root cause confirmed with direct evidence (not
inferred), fix verified by 15 unit tests plus a real 10+ minute packaged
long-run smoke test for both products. See
`docs/corrections/launcher/WINDOWS-LAUNCHER-CORRECTIVE-HANDOVER.md`.

## Wave 0 corrective status (added; original findings above are unchanged)

The stop-ship P0/P1 set plus directly-dependent issues were corrected in
Phase 3.6 ("Corrective Wave 0"). Full per-issue resolution evidence
(commit, test, doc) is recorded in the `status`/`wave0_resolution` fields
of the `.json`/`.csv` copies of this registry, and narrated in
`docs/corrections/wave0/`. Summary:

| ID | Wave 0 status |
|---|---|
| AUDIT-001, 002, 003, 004, 005, 006, 008, 009 | FIXED_AND_VERIFIED_WAVE0 |
| AUDIT-011, 012, 016, 018, 019 | FIXED_AND_VERIFIED_WAVE0 |
| AUDIT-010 | DEFERRED_WAVE0 (Option B — see `docs/corrections/wave0/wave0-residual-risk-register.md`) |
| all other IDs (007, 013, 014, 015, 017, 020–029) | unchanged, still OPEN — out of Wave 0 scope |

This does **not** mean either product has cleared its release gates —
AUDIT-022/023 (unsigned Windows/Android builds) remain OPEN and are named
release blockers in this same registry. See
`docs/corrections/wave0/WAVE0-CORRECTIVE-HANDOVER.md` for the honest
overall status.

## Notes on this registry

- **Every issue is marked PROVEN** (or, for AUDIT-026's scale-impact claim,
  explicitly split into a PROVEN schema fact plus a HIGH-CONFIDENCE INFERENCE
  about consequence at scale) — no issue in this registry is speculative.
  Nothing was downgraded to make the audit look better, nothing was inflated
  to make it look worse, per the audit's own instructions.
- **Dependencies matter for sequencing**: AUDIT-002 depends on AUDIT-003
  (fixing server-side computation for Retail fixes both at once); AUDIT-007
  depends on AUDIT-004 (can't reverse tax on a return until returns are
  validated against real sale data); AUDIT-027 depends on AUDIT-019 (remove
  the placeholder once, and only once, backup is either built or the row is
  removed).
- **AUDIT-001 is very likely the cheapest fix in the entire registry** (est.
  effort XS) relative to its severity (P0) — see `21`'s analysis that it is
  most plausibly a two-line omission, not a missing feature.
- See `26-corrective-roadmap.md` for how these are sequenced into waves, and
  `27`/`28` for the must-fix-before-sale vs. safe-to-defer split.
