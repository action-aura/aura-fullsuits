# Wave 1C -- Launch Order Recommendation (Part R)

## Recommendation: **Launch Aura Clinic first.** Aura Retail follows as a second, later pilot once real scanner/printer hardware has been verified with an actual customer's equipment.

## Basis

| Factor | Clinic | Retail | Verdict |
|---|---|---|---|
| Release gates | Financial PASS, security PASS, privacy PASS (2 disclosed non-blocking limitations), backup PASS, Android PASS, Windows CONDITIONAL (signing) | Same gate structure, same results -- both products pass identically on every gate this wave re-verified | **Tied** on gate outcomes |
| Financial risk | Payment idempotency/validation independently re-proven this wave (8/8 fresh cases); one open gap (AUDIT-013, invoice-creation idempotency) is non-blocking with a manual workaround | Sale/return authority independently re-proven this wave (4/4 fresh cases); no comparable open gap | **Slight edge to Retail**, but both are financially sound enough for a pilot |
| Privacy risk | Handles patient data -- higher inherent sensitivity; two disclosed, documented, non-blocking limitations (secretary read access, session-version staleness) | Handles business/sales data -- lower inherent sensitivity | **Edge to Retail** on inherent risk profile, but Clinic's gaps are documented, deliberate, and disclosed, not unknown |
| Hardware dependency | **None** -- no scanner, no printer, no POS hardware of any kind | Depends on scanner and/or printer for a realistic retail workflow, and **none of Retail's hardware claims are physically verified beyond one Android camera-scanning pass** (`hardware-commercial-claim-review.md`) | **Strong edge to Clinic** -- Clinic can be piloted with zero hardware-verification risk; Retail cannot be sold on an unqualified "works with your scanner/printer" claim yet |
| Support burden | Same missing self-service-docs gap as Retail (`operational-supportability-gate.md`) -- both require founder-supervised support equally | Same | **Tied** |
| Target-customer complexity | A small clinic's core workflow (patients, appointments, invoices, payments) is fully covered by what's built | A retail store's core workflow additionally depends on a scanner/printer working correctly on day one, which is exactly the unverified part | **Edge to Clinic** |
| Installation friction | Windows-only pilot is realistic and sufficient for a clinic (front desk + doctor) | A realistic retail till setup usually assumes a barcode scanner and receipt printer are working immediately -- the exact gap above | **Edge to Clinic** |
| Operational readiness | 13-step Windows smoke test (deepest verification of either product across every wave), real device-verified backup/restore ("working just fine, down to the smallest detail") | Comparable depth, no meaningful gap | **Tied**, both genuinely operationally ready modulo the shared support-docs gap |
| Speed to revenue | Ready today, contingent only on the disclosed Windows-signing/support conditions | Ready today for the software; realistically blocked on finding a pilot customer whose scanner/printer the team can personally pre-verify, adding lead time | **Edge to Clinic** -- fewer external dependencies before the first dollar can be collected |
| Residual defect profile | Zero unresolved P0/P1; 2 open P2s (AUDIT-013, AUDIT-020-documented-design) | Zero unresolved P0/P1; 1 open P2 (AUDIT-017, not a sale/payment path) | **Tied**, both genuinely clean at the P0/P1 level |

## Not a feature-count decision
Retail is not being deprioritized because it has fewer features -- it has a comparably mature, independently-verified financial core. It is deprioritized because **its realistic minimum viable pilot depends on physical hardware (scanner and/or printer) that has never been verified beyond one Android camera-scanning pass**, and selling an unqualified "your scanner/printer will work" claim without that verification would violate this wave's own instruction not to claim untested hardware as verified. Clinic has no equivalent external dependency standing between "the software is ready" and "a real customer can use it."

## What must happen before Retail's turn
1. Identify a pilot retail customer and personally (team-present) verify their actual scanner and printer against the Windows OS-spooler print path and either the physically-verified Android camera scanner or the customer's specific HID scanner model.
2. Once verified for that specific customer's hardware, the same Controlled Paid Pilot conditions applied to Clinic apply to Retail (see `first-paid-pilot-profile.md`).

## Parallel-launch verdict
**Not recommended as a simultaneous first move.** Running both pilots at once splits the same limited founder-supervised-support capacity (`operational-supportability-gate.md`) across two products with no self-service documentation yet -- sequencing reduces risk without meaningfully slowing revenue, since Retail's hardware-verification lead time would likely exceed however long it takes to stabilize one Clinic pilot first.

## "Launch neither yet" -- explicitly rejected
Both products clear every P0/P1-relevant release gate this wave re-verified. Waiting further would not fix anything real -- the remaining gaps (Windows signing, customer docs, hardware verification) are conditions to manage within a supervised pilot, not defects to fix before any pilot can begin.
