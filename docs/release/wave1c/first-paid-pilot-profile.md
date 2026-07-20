# Wave 1C -- First Paid Pilot Profile (Part O)

Applies only if the relevant product clears Controlled Paid Pilot (Gate 3) -- see `release-gate-scorecard.md` for the per-platform verdicts this profile depends on.

## Aura Clinic -- recommended first paid pilot (see `launch-order-recommendation.md` for why Clinic is recommended first)

**Allowed customer type**: A single, small, independent clinic (1-3 doctors, 1-2 front-desk staff) willing to be a named, disclosed pilot customer under a limited pilot agreement -- not a multi-branch chain, not a hospital.

**Allowed platform**: Windows desktop as the primary/authoritative install (installed and supervised by the team). Android may be used in parallel by the same clinic's staff for read/lightweight workflows, understanding it has not been run on more than one physical device model.

**Allowed workflows**: Patient registration, appointments, visits, invoicing, payments, prescriptions (doctor-only writes), backup/restore (team-supervised).

**Unsupported workflows**: Multi-branch operation, e-prescription integration with any external pharmacy system, insurance-claim submission, any cloud sync, any Owner/licensing-portal interaction (none exists yet).

**Hardware requirements**: None beyond a standard Windows PC and/or Android phone/tablet -- Clinic has no scanner/printer dependency.

**Support requirements**: Founder/team-supervised installation; a direct, real-time support channel for the pilot duration; no unsupervised upgrade (team performs or directly oversees any upgrade).

**Backup requirements**: Team confirms a successful backup exists at least daily during the pilot window (no automated backup-health monitoring exists yet).

**Upgrade policy**: No silent/automatic upgrades. Any version change during the pilot is scheduled, team-supervised, and preceded by a fresh backup.

**Pilot duration**: Recommend 4-6 weeks -- long enough to exercise a full billing/appointment cycle including at least one real backup/restore drill, short enough to bound risk before committing to a longer paid term.

**Exit / rollback plan**: Previous installer build and the pre-pilot backup are retained by the team for the full pilot duration. If a P0/P1 defect surfaces, the team reinstalls the prior version and restores from the pre-incident backup; the customer is notified immediately, not after the fact.

**Explicit limitations the customer must acknowledge before the pilot begins** (per `clinic-privacy-release-gate.md` and `operational-supportability-gate.md`): front-desk/secretary staff can read (not write) clinical notes and visit history -- this is documented in `docs/security/clinic-rbac-matrix.md`, not a bug; Windows installer is currently unsigned (SmartScreen warning on first run, explained in advance); no formal customer-facing support/troubleshooting documentation exists yet -- support is via direct team contact only.

## Aura Retail -- allowed only as a second, later pilot (not recommended first -- see `launch-order-recommendation.md`)

**Allowed customer type**: A single store, single till/register (one physical POS device), retailer willing to accept the hardware-verification limitations below.

**Allowed platform**: Windows as the primary till. Android for camera-based barcode scanning only (the one physically-verified input method) -- not recommended as the primary till device given HID scanning and printing are both "protocol supported," not device-verified, on Android.

**Allowed workflows**: Sales, returns, stock/inventory, discounts, tax, backup/restore (team-supervised).

**Unsupported workflows**: Multi-branch/multi-till sync, direct thermal (ESC/POS) printing, any scanner beyond Android camera or a standard USB/Bluetooth HID keyboard-wedge device the team has pre-tested with that specific customer, national e-invoicing.

**Hardware requirements**: A Windows-installed printer (any driver-supported printer, via the OS print spooler -- not click-tested against a real dialog yet, so the team must personally verify print output with this customer's actual printer before relying on it) and, if a physical scanner is used, a keyboard-wedge HID device the team has personally tested (since no HID hardware has been verified in-house).

**Support / backup / upgrade / exit-rollback**: Identical model to Clinic's pilot terms above.

**Pilot duration**: Same 4-6 week recommendation, extended by however long it takes to complete a live print/scan hardware verification pass with this specific customer's equipment.

**Explicit limitations the customer must acknowledge**: no scanner or printer hardware has been verified in-house beyond one Android camera pass; Windows installer is unsigned; no customer-facing support documentation exists yet.

## Cross-cutting conditions for both products
No cloud sync, no remote Owner dashboard (none exists), no telemetry, no automatic updates -- consistent with what the product actually is today, not a promise of a future capability.
