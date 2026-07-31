# Phase 8V-P5 — Physical Session Plan

1. Reconfirm device, Owner (with real wire-capture middleware), connectivity, product identity.
2. Scenario 2 (Retail): real `License.status -> SUSPENDED` (immediate, staff-driven restriction) ->
   confirm real local restriction + backend denial + data-still-readable -> real "late renewal"
   staff action (un-suspend + revive/extend subscription) -> confirm real ACTIVE_ONLINE restoration
   -> real 88.00 sale -> real return.
3. Scenario 3: reconfirm real PAST_DUE via the expiry-scan CLI (Owner tier, already proven
   mechanism) -> document the real structural finding that PAST_DUE alone does not locally restrict
   (by design) -> cross-reference Scenario 2's SUSPEND/backend-denial evidence as the shared,
   legitimate "restricted" proof this scenario also needs.
4. Scenario 5 (Clinic): create a dedicated short, real, non-default `OfflinePolicy`
   (`hard_expiry_behavior=RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA`, short grace/retry seconds),
   assign it to a fresh license, activate fresh, let real elapsed offline time exceed the policy's
   own grace+retry window (no check-in during the wait) to reach a real ineligible baseline, then
   create a real emergency extension and confirm it restores eligibility, then let the (also short)
   extension itself really expire and confirm reversion.
5. Scenario 6 (device replacement): use the real installed Windows Retail product (already proven
   in Phase 8V-P) as the second real device identity against the same physical Android installation's
   license, at its device limit.
6. Scenario 7 (plan downgrade): use the same two real installations (Android + Windows) to observe
   a real downgrade's effect physically on both.
7. Stale-assertion rejection: controlled real signed-response ordering test via the wire-capture
   middleware's own vantage point (delay delivery of an older, still-validly-signed envelope,
   apply a newer one first, then release the older one).
8. Backup/restore/export, Clinic invoice/payment integrity, Retail financial/return integrity --
   real on-device operations.
9. Local-deactivation UX: read source, classify, document.
10. Final traffic/Logcat/data-preservation review, final regression (including product backends this
    time), final artifact/manifest reconfirmation, cleanup, final decision, commit, tag decision.
