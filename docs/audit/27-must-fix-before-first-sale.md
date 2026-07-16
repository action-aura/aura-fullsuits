# Must-Fix Before First Sale

Everything in this list is Wave 0 or Wave 1 from `26`. If selling Clinic
first (the recommended path per `24`), the Retail-only items can be deferred
until Retail is the one being sold — but do not sell Retail until its own
items are done too.

## Applies to Retail (before selling Retail to anyone, including a pilot)

1. **AUDIT-001** — Wire `onboarding_bp` into `products/retail/backend/app.py`. Without this, Retail cannot be used at all.
2. **AUDIT-003 + AUDIT-002** — Move financial computation server-side in `create_sale()` using `core/retail/pricing.py`; this single fix also resolves Android's tax/discount omission.
3. **AUDIT-004** — Validate returns against the original sale (existence, company, quantity-vs-sold, duplicate-submission).
4. **AUDIT-005, 006, 008, 009** — Bundle with #2 (discount clamping, rounding consistency, quantity-sign validation, negative-stock prevention).
5. **AUDIT-007** — Return tax tracking, once #3 makes it possible to reverse the right figures.
6. **AUDIT-016, 017** — FK enforcement, transaction safety on PO receiving.
7. **AUDIT-022, 023** — Windows signing + installer, Android production signing.

## Applies to Clinic (before selling Clinic to anyone, including a pilot)

1. **AUDIT-011, 012** — Payment amount validation and idempotency protection. This is Clinic's single most important fix — everything else about Clinic is closer to ready than Retail, but unvalidated/duplicable payments is a real money-and-trust problem.
2. **AUDIT-013, 018** — Invoice idempotency, explicit transaction wrapping on `create_invoice`/`record_payment`.
3. **AUDIT-020** — Resolve (or explicitly, deliberately accept and document) the Secretary-can-read-clinical-notes finding before handling real patient data.
4. **AUDIT-022, 023** — Same signing/installer requirements as Retail.

## Applies to both

1. **AUDIT-019** — Backup and restore. Independently disqualifying for any paid relationship regardless of how the other findings resolve — losing a customer's entire sales/patient history with zero recovery path is not an acceptable risk to carry into a paid engagement.
2. **A real device/emulator testing pass for both Android apps** — not a registry issue by itself, but everything in this audit's Android findings is BUILD ONLY; no runtime claim should be made to a customer about either Android app until this happens.
3. **Documented customer-facing support process** — outside this codebase's fixable surface, but a release gate requirement nonetheless; someone needs to own "what happens when a customer has a problem" before either product is sold.

## What "must-fix" does NOT include

Licensing/activation enforcement, Owner Control Center, payment-gateway
integration, production VPS deployment, and automatic update distribution are
all **explicitly out of scope** for this list — they were out of scope for
every phase to date and this audit does not change that. A first sale can
happen with manual licensing/support processes; it cannot happen with broken
onboarding, wrong tax, unlimited free refunds, or unrecoverable data loss.
