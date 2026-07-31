# Phase 8V-P5 — Final On-Device Data Preservation

## Result: NOT VERIFIED at the spec's full rigor — partial real evidence only

## Why full comparison could not be made

The governing spec's Part Q compares final on-device state against the Part E pre-scenario baselines
(minimum 3 patients/2 appointments/1 visit/1 prescription/2 invoices for Clinic; 5 products/2
categories/1 supplier/3 sales for Retail, including the 88.00 reference case). Those baselines were
never created this session (see `clinic-invoice-payment-integrity-final.md` and
`retail-financial-and-return-integrity.md`) -- there is nothing to diff a "final state" against at the
rigor the spec asks for.

## Real, disclosed, partial evidence available

- The real Retail sale completed during the SUSPEND test (SALE-000002, $100) was independently
  confirmed present and unaltered later in the session via a real `/api/licensing/status`-adjacent
  check, along with the installation's own long-term persistence across the multi-hour session gap
  (stronger evidence of continuity than a mere force-stop test, per `evidence-reuse-decision.md`).
- Installation identity (installation IDs, device key fingerprints) for both the reused Retail
  installation and the fresh Clinic installation created for Scenario 5 remained stable and consistent
  across every captured exchange in `raw-wire-evidence.md` -- no unexpected identity churn observed in
  the traffic that was captured.
- No stock, sale, invoice, or payment mutation was observed as a side effect of any commercial-state
  transition performed this session (SUSPEND, RESTRICTED, emergency extension) -- domain data was never
  touched by any of the licensing-layer operations exercised, consistent with the architecture (the two
  domains are structurally separate, per the source reading in `phase8vp5-baseline.md`).

## Disposition

Reported as NOT VERIFIED against the spec's own explicit comparison requirement. This is one of the
named reasons the final unconditional Phase 8 tag is withheld this session.
