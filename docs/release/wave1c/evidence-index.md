# Wave 1C -- Evidence Index (Part A)

Every claim in this wave's gate reports traces to one of the sources below. Historical documents are cited for trajectory/context; **current-state claims are always re-verified this wave**, never taken on the historical document's word alone.

## Historical baseline (Phase 3.5, pre-Wave-0 -- read for context, largely superseded)
`docs/audit/00`-`21`, `22-master-defect-registry.{md,csv,json}`, `23-enterprise-grade-scorecard.md`, `24-product-classification-and-verdict.md`, `25-release-gates.md`, `26-corrective-roadmap.md`, `27-must-fix-before-first-sale.md`, `28-safe-to-defer-until-after-revenue.md`, `AURA-FULLSUITS-COMPLETE-AUDIT-HANDOVER.md`. This audit found 29 defects (3 P0 + 4 P1 stop-ship, later AUDIT-030 added = 4 P0), classified both products "Prototype/unsafe" (Retail) and "Early MVP" (Clinic), and recommended Clinic as the first pilot candidate.

## Wave 0 corrective (fixed the stop-ship P0/P1 set)
`docs/corrections/wave0/*` -- AUDIT-001,002,003,004,005,006,008,009,011,012,016,018,019 marked `FIXED_AND_VERIFIED_WAVE0` in the registry's `.json`/`.csv`.

## Launcher corrective (AUDIT-030)
`docs/corrections/launcher/*` -- fixed and verified same phase.

## Wave 1A (physical Android device validation)
`docs/mobile/wave1a/*` -- 7 real defects found/fixed (MOB-001 through 006 fixed, MOB-007 registered/deferred), on one physical device (Infinix X6528).

## Wave 1B (commercial packaging)
`docs/release/wave1b/*`, `docs/hardware/*`, `docs/release/{versioning-policy,windows-code-signing-guide,android-production-signing-policy,android-key-backup-checklist,customer-data-preservation-policy}.md` -- release candidates built, installers/signing/hardware/receipt-printing added, REL-006 (cross-product port race) found and fixed.

## Architecture
`docs/architecture/financial-authority-contracts.md`, `docs/security/clinic-rbac-matrix.md`.

## Wave 1C (this wave -- fresh evidence generated)
| Document | What it independently re-proves |
|---|---|
| `release-candidate-identity-verification.md` | Tag/commit/checksum identity, zero drift |
| `automated-regression-report.md` | Full Python (285/285) + Android (85/85) + Windows launcher/regression rerun |
| `financial-release-gate-report.md` | 12/12 fresh adversarial financial cases, new test files added |
| `data-integrity-and-zero-loss-gate.md` | Install/upgrade/uninstall/crash/backup lifecycle, hands-on |
| `backup-and-recovery-gate.md` | Automated + manual corrupt-backup/cross-product-restore re-proof |
| `windows-release-gate-report.md` | Windows lifecycle, signing-impact decision |
| `android-release-gate-report.md` | Full rebuild, bit-identical to shipped artifacts, signature verification |
| `hardware-commercial-claim-review.md` | Per-channel claim wording re-audited against current code |
| `security-release-gate.md` | 18-point fresh code-level security re-check |
| `clinic-privacy-release-gate.md` | 14-point fresh privacy re-check |
| `localization-and-ux-gate.md` | Fresh localization test rerun + heuristic hardcoded-string scan |
| `operational-supportability-gate.md` | Direct documentation-inventory search |

## Independent verification agents dispatched this wave
Four separate, independently-reasoning verification passes were run in parallel against the live repository (not against each other's output): (1) Android full regression + signed rebuild, (2) financial-gate adversarial testing with new test files, (3) Windows install/upgrade/uninstall/crash/backup lifecycle, (4) security/privacy/hardware/localization code-level spot-check. Each was instructed to escalate, not silently fix, any real defect found. Zero new defects were found by any of the four.
