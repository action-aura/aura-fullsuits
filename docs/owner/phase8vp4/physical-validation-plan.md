# Phase 8V-P4 — Physical Validation Plan

Device confirmed ready, Owner confirmed ready, connectivity mode chosen (`connectivity-and-url-
decision.md`). Execution order for the rest of this session:

1. Rebuild Clinic + Retail rc.3 with `-PownerLicensingBaseUrl=http://127.0.0.1:5551/api/licensing/v1`
   (Part E).
2. Verify signing certificate continuity on the new builds (Part F).
3. Install both products on the device (Part G) -- fresh install, since neither product has a prior
   signed version already on this device (first Android session for both).
4. Create synthetic local data baselines for both products (Part H).
5. Set up traffic-capture and Logcat-capture mechanisms (Part I).
6. Perform initial physical activation for both products (Part J).
7. Run all seven commercial-lifecycle scenarios in the order the governing brief lists them (Parts
   K-Q), each against real Owner UI actions and real on-device check-ins.
8. Persistence, backend-enforcement, traffic-privacy, Logcat-privacy, and data-preservation checks
   (Parts R-V).
9. Final regression (Part W), cleanup (Part X), final decision (Part Y), and -- only if every
   mandatory gate genuinely passes -- the final tag.

Scenario 6 (device replacement) will use the pattern this phase's own brief explicitly allows: the
physical Android phone as one real device identity, and a validated Windows installation (already
proven in Phase 8V-P) as the second, since only one physical Android device is available. Scenario 7
will likewise combine real installations across platforms where useful, consistent with the same
allowance.
