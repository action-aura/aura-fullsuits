# Wave 1C -- Audit Scope

Wave 1C is a **release-gate re-audit and paid-pilot decision**, not a development wave. It evaluates the exact artifacts produced by Wave 1B against five release gates (Internal Testing, Controlled Pilot, Controlled Paid Pilot, General Paid SMB Release, Enterprise Grade) and issues an Owner-platform entry decision.

## In scope
- Re-verification of every claim in the Wave 1B document set against current evidence (rerun tests, recompute checksums, re-read code -- not just re-read prior reports).
- Financial correctness re-verification for Retail and Clinic, exercised against the real backend, not re-derived by hand.
- Data integrity / zero-data-loss evaluation across install, upgrade, uninstall, reinstall, crash, backup, restore.
- Security, privacy, hardware-claim, localization, and operational-supportability gates.
- Evidence-based scoring and a direct paid-pilot / launch-order / Owner-entry decision.

## Out of scope (unchanged from Wave 1B's own boundary, restated per this wave's explicit instruction)
No Owner Control Center, licensing, subscription enforcement, VPS deployment, telemetry, automatic updates, Jordan e-invoicing, WhatsApp/SMS automation, multi-branch sync, Aura Core integration, or new product features. No Windows code-signing certificate purchase or simulation. No claiming untested hardware as verified.

## Ground rule
This phase does not implement fixes for newly discovered defects. If a P0/P1 is found that makes safe verification impossible, it is registered, the affected gate is stopped, evidence is documented, and a focused corrective wave is recommended -- production code is not touched in this phase except for audit-only test harnesses explicitly permitted by the governing instructions.

## Baseline being audited
- Tag: `commercial-packaging-wave1b-complete`
- Commit: `157521c46bc89dee10d44dd097534fce7461d135`
- Branch: `master`
- Working tree at audit start: clean (`git status --porcelain` empty)
