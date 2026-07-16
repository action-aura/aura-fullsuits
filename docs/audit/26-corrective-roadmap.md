# Corrective Roadmap

No corrections were implemented in this phase (analysis only). This is a
prioritized plan for the next phase, grouped per the audit's own wave
structure. Effort: XS/S/M/L/XL, as assigned in the master defect registry.

## WAVE 0 — Stop-ship defects

| Issue | Product | Reason it's Wave 0 | Dependency | Effort | Order |
|---|---|---|---|---|---|
| AUDIT-001 | Retail | P0 — blocks all first use | None | XS | 1st |
| AUDIT-003 | Retail | Financial P1, and the fix also resolves AUDIT-002 | None | M | 2nd |
| AUDIT-002 | Retail/Android | P0 — resolved as a side effect of AUDIT-003's fix | AUDIT-003 | (included in AUDIT-003) | — |
| AUDIT-004 | Retail | P0 — direct cash-loss vector, exploitable today | None | M | 3rd |
| AUDIT-005, 006, 008, 009 | Retail | Bundle with AUDIT-003's server-side-computation rewrite — same code path, cheaper to fix together than sequentially | AUDIT-003 | S each | With 2nd |
| AUDIT-011 | Clinic | Financial P1 | None | S | 4th |
| AUDIT-012 | Clinic | Financial P1 (duplicate payments explicitly named in the audit's own P1 examples) | None | S | 4th (parallel with 011) |
| AUDIT-019 | Both | No backup/restore — independently disqualifying per the audit's own P1 criteria and every gate from 2 upward | None | L | 5th (start in parallel, longest-running item) |

**Expected outcome of Wave 0**: Retail becomes actually usable by a real
customer for the first time (onboarding fixed) with correct financial
behavior on both platforms; Clinic's payment recording becomes safe against
the two most damaging failure modes (bad amounts, duplicates); both products
gain a real backup story. **Tests required**: extend `retail_pricing_test.py`
Part B with server-side-recomputation assertions; extend
`retail_pricing_test.py`/a new `retail_returns_test.py` with the abuse cases
from AUDIT-004; extend `clinic_workflow_test.py` with negative/overpayment/
duplicate-payment cases; a new backup/restore test harness once AUDIT-019 is
built.

## WAVE 1 — First paid customer blockers

| Issue | Product | Reason | Dependency | Effort | Order |
|---|---|---|---|---|---|
| AUDIT-007 | Retail | Return tax tracking — needed for correct post-fix reporting once AUDIT-004 lands | AUDIT-004 | M | 6th |
| AUDIT-013 | Clinic | Duplicate-invoice protection, same class as Wave 0's payment fixes | None | S | 6th (parallel) |
| AUDIT-016 | Retail | FK enforcement — cheap, closes a real data-integrity gap before scale | None | XS | 6th (parallel) |
| AUDIT-017 | Retail | Transaction safety on PO receiving | None | S | 7th |
| AUDIT-018 | Clinic | Transaction safety on the two money-writing routes — higher priority than AUDIT-017 given it's money, not inventory | None | S | 7th (parallel) |
| AUDIT-020 | Clinic | Privacy/role gap — must be resolved (or a deliberate policy documented) before any patient-data pilot | None | S | 7th (parallel) |
| AUDIT-022 | Both | Windows signing + installer | None | M | 8th |
| AUDIT-023 | Both | Android production signing | None | S | 8th (parallel) |
| AUDIT-009 | Retail | Server-side negative-stock prevention (if not already folded into AUDIT-003's rewrite) | AUDIT-003 | S | 8th (parallel) |

**Expected outcome**: every Gate 3 requirement this audit found failing is
addressed except "documented support" and "basic licensing readiness" (both
organizational/product-scope decisions outside this codebase's fixable
surface, and licensing is explicitly out of scope per every phase's own
exclusions). **Tests required**: signing verification via `apksigner`/
`signtool`; a real installer-based install/uninstall test on a clean VM;
regression run of the full existing suite plus everything added in Wave 0.

## WAVE 2 — Commercial quality

| Issue | Product | Reason | Dependency | Effort | Order |
|---|---|---|---|---|---|
| AUDIT-010 | Both | Fix the test-pollution bug so `pytest products/*/tests` becomes trustworthy again for ongoing development | None | S | 9th |
| AUDIT-015 | Clinic | Missing invoice states | None | S | 9th (parallel) |
| AUDIT-021 | Both | `FLAG_SECURE` on Android | None | XS | 9th (parallel) |
| AUDIT-026 | Both | Add the missing indexes before any customer approaches real catalog/patient scale | None | XS | 9th (parallel) |
| AUDIT-027 | Both | Remove/relabel the misleading "Backup & restore" placeholder (or leave it correctly labeled once AUDIT-019 ships) | AUDIT-019 | XS | After Wave 0's AUDIT-019 |
| AUDIT-014 | Clinic | Remove the dead Accounting-mirror code and its misleading comment | None | XS | 9th (parallel) |
| AUDIT-025 | Both | Synchronize version numbers | None | XS | 9th (parallel) |
| — | Both | A real device/emulator testing pass — the single largest coverage gap in this entire audit for Android (every runtime behavior on Android is currently BUILD ONLY, never TESTED) | None | L | 10th |
| — | Both | A real 50,000+-row performance/scale test, now that AUDIT-026's indexes exist to test against | AUDIT-026 | M | 11th |

## WAVE 3 — Enterprise maturity

| Item | Product | Reason | Dependency | Effort |
|---|---|---|---|---|
| Fine-grained RBAC (beyond Clinic's doctor-only gate and Retail's subsystem-level model) | Both | Needed for Gate 4's "mature RBAC" | None | L |
| Structured observability (metrics, health endpoint) | Both | Gate 4 requirement, currently absent | None | M |
| Controlled update mechanism | Both | Explicitly deferred by every phase to date; real product need before scaling | AUDIT-022/023 (signing) | XL |
| Formal disaster-recovery documentation + tested restore drills | Both | Beyond AUDIT-019's basic backup — a documented, rehearsed DR process | AUDIT-019 | M |
| AUDIT-024, 028, 029 | Retail | Low-severity hardening items, cheap to batch into a general security-hardening pass | None | XS each |
| Formal accessibility review | Both | Never assessed in any phase to date (`15`) | None | M |
| `commercial_runtime/licensing_contracts/` — wire actual enforcement | Both | Explicitly out of scope for every phase to date, including this one — noted here only as the eventual Wave 3+ destination for that scaffolding | None | XL |

## Recommended execution order (top-level)

Wave 0 (stop-ship) → Wave 1 (first-sale blockers) → Wave 2 (commercial
quality, including the currently-nonexistent Android device-testing pass) →
Wave 3 (enterprise maturity). Do not begin Owner Control Center / licensing
enforcement / production deployment work (all explicitly out of scope for
this audit and every prior phase) until at least Wave 1 is complete for
whichever product is being sold first.
