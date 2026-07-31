# Phase 8V-P5 — Scenario 2 (Retail Late Renewal) — Final

## Result: NOT VERIFIED (partial real evidence gathered; the scenario's own PASS bar not met)

## What was actually, physically done this session

- Real Retail license moved to `License.status = SUSPENDED` through the Owner service layer (a real,
  staff-driven, immediate restriction at the Owner/check-in-rejection tier).
- A real Retail sale (SALE-000002, $100) was completed on-device **while the license was genuinely
  SUSPENDED** in Owner's database. This is real, disclosed, and significant, but it is the **opposite**
  of what Scenario 2 needs at that step: it demonstrates that a bare `License.status = SUSPENDED`
  does not, by itself, produce local restriction on an already-active installation (same structural
  finding as `phase8vp5-baseline.md` and Scenario 3), rather than confirming a restricted-to-active
  recovery.
- Real un-suspend / restoration to `ACTIVE` was exercised at the Owner tier.

## What was not completed, and why

- **No genuine local RESTRICTED-to-ACTIVE transition was captured for the Retail product
  specifically.** The one physical RESTRICTED state reached this session (real, first time across all
  sessions) was reached via Scenario 5's dedicated short `OfflinePolicy`, assigned to the **Clinic**
  installation, not Retail. Reproducing the same mechanism on the Retail installation (assign a
  matching short policy, force real elapsed offline time, confirm RESTRICTED, then perform a real late
  renewal through the Owner UI, confirm real signed-assertion-driven RESTRICTED -> ACTIVE_ONLINE) was
  not carried out before session time ran out.
- **The 88.00 financial reference case (price 100.00, discount 20.00, tax 10% on the discounted 80.00,
  expected total 88.00) was not executed this session.** No sale exercising that exact combination was
  recorded.
- **Return integrity was not executed.** The Retail return/refund UI was not located during this
  session's UI exploration; a return flow (valid sale -> authorized return -> refund amount -> net
  revenue -> stock restoration -> no duplicate -> historical link preserved) needs its own follow-up
  session with the UI actually located first.

## Disposition

This scenario is reported honestly as **NOT VERIFIED** against the governing spec's own explicit bar
("Scenario 2 receives PASS only when real restricted-to-active transition, 88.00 case, AND return
integrity are all demonstrated"). This is one of the explicit, named reasons the final unconditional
Phase 8 tag is withheld this session (see `phase8-final-decision.md`). The real, valuable evidence
gathered here (License-suspension's actual practical non-effect on an active installation) is retained
and folded into the residual risk register rather than discarded, but it does not substitute for the
scenario's own required evidence.

## Follow-up required (next session)

1. Assign a short `OfflinePolicy` (same shape as Scenario 5's `phase8vp5-fast-test-2bb386`) to the
   Retail installation's license, force real elapsed offline time, confirm RESTRICTED on Retail
   specifically.
2. Through the Owner UI: create a real late renewal, apply the late-renewal start-date rule, link a
   CONFIRMED payment, approve, apply; on-device check-in; confirm RESTRICTED -> ACTIVE_ONLINE via a real
   signed assertion (not just a successful check-in resetting the offline timer -- verify the assertion's
   `renewal_status`/`term_end`/state version fields actually reflect the new term, the same rigor applied
   in Scenario 5's self-correction of its own initial confounded test).
3. Locate the Retail return/refund UI (likely under a sale-detail or history screen not yet explored);
   run the full 88.00 case and one authorized return.
