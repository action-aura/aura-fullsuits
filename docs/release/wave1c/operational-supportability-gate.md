# Wave 1C -- Operational Supportability Gate (Part L)

## Method
Direct inventory of what customer-facing/support-facing documentation actually exists in the repository, checked by search (`find`/`grep`), not assumed from prior wave summaries.

## What exists today

| Item | Status | Evidence |
|---|---|---|
| Installation guide (customer-facing) | **NOT FOUND** | No file matching install/setup guide conventions outside the barcode scanner guide below |
| Onboarding guide (customer-facing) | **NOT FOUND** | -- |
| Backup guide (customer-facing) | **NOT FOUND** | `customer-data-preservation-policy.md` documents the *policy*, not a step-by-step customer guide |
| Restore guide (customer-facing) | **NOT FOUND** | Same gap |
| Scanner setup guide | **PASS** | `docs/hardware/barcode-scanner-setup-guide.md` (Wave 1B, Part N) |
| Printer setup guide | **NOT FOUND** | No dedicated customer-facing printer setup guide; `receipt-printer-architecture.md` and `receipt-printer-compatibility-matrix.md` are engineering/audit documents, not customer instructions |
| Upgrade guide | **NOT FOUND** | `windows-upgrade-data-preservation-report.md` is an internal verification report, not a customer instruction sheet |
| Log-collection guide (for support triage) | **NOT FOUND** | -- |
| Safe troubleshooting guide | **NOT FOUND** | -- |
| Version/checksum verification instructions (customer-facing) | **NOT FOUND** | Checksums exist in `release-candidate-manifest.md` (internal); no customer-facing "how to verify what you downloaded" instructions |
| Known limitations (customer-facing) | **PARTIAL** | Exists in engineering form (`wave1b-residual-risk-register.md`, this wave's own residual-risk register) but not rewritten as customer-facing plain language |
| Customer data preservation policy | **PASS** | `docs/release/customer-data-preservation-policy.md` -- genuinely customer-appropriate language |
| Support contact placeholder | **NOT FOUND** | No support email/phone/contact placeholder found anywhere in the repository |
| Issue escalation process | **NOT FOUND** | -- |
| Rollback procedure (operator-facing, for the team, not the customer) | **PARTIAL** | Technically possible (reinstall previous installer, restore from backup) but never written down as a procedure |

## Assessment
This is a real, material gap. Everything that exists is **engineering and audit documentation** -- accurate, thorough, and honest, but written for the team that built the product, not for a customer or a support agent handling a live incident. Only two documents in the entire repository are genuinely customer-facing: `customer-data-preservation-policy.md` and `barcode-scanner-setup-guide.md`.

## First-pilot support requirements (per spec)
Given the above, a first paid pilot is only responsible if it substitutes **founder-supervised, white-glove support** for the missing self-service documentation:
- **Supervised install** -- founder/team present (in person or remote screen-share) for the first installation, not a self-service download link.
- **Remote support availability** -- a direct, real-time channel (not a ticket queue) for the pilot period.
- **Daily backup check** -- the team manually confirms a backup exists and is restorable during the pilot window, since there is no automated backup-health monitoring.
- **Upgrade approval** -- no upgrade should be pushed to the pilot customer without the team present, given the lack of a written upgrade guide.
- **Incident log** -- the team keeps its own record of anything that goes wrong during the pilot (this document set, plus a running notes file, satisfies this).
- **Rollback artifact** -- the previous installer/backup is kept on hand for the duration of the pilot so a manual rollback is possible if needed.
- **Customer acknowledgement of limitations** -- the customer must be shown (verbally or via `first-paid-pilot-profile.md`) the exact list of what is and isn't supported before the pilot begins.

## Verdict
- **Gate 1 (Internal Testing)**: **PASS** -- no customer-facing documentation is required at this gate.
- **Gate 2 (Controlled Pilot)**: **CONDITIONAL PASS** -- acceptable only with founder-supervised support substituting for missing self-service docs (above).
- **Gate 3 (Controlled Paid Pilot)**: **CONDITIONAL PASS** -- same condition, now enforceable as an explicit pilot-agreement term (see `first-paid-pilot-profile.md`), since money is now involved and the customer must be told plainly what support model they are getting.
- **Gate 4 (General Paid SMB Release)**: **FAIL** -- general release implies unsupervised or lightly-supervised installation by strangers the team cannot hand-hold individually. Without an installation guide, backup/restore guide, troubleshooting guide, and a real support contact/escalation process, this gate cannot pass regardless of how correct the underlying product is.

This is registered as a residual risk / must-fix-before-general-release item, not silently absorbed into a passing verdict.
