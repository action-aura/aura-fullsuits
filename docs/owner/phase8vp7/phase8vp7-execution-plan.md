# Phase 8V-P7 — Execution Plan (as actually followed)

1. Hard entry gates: Android device confirmed real (`device` status); Windows capability confirmed
   (real machine, no Sandbox/Hyper-V -- disclosed, later partially superseded by the multi-instance
   discovery in Scenario 6).
2. Baseline confirmation (HEAD, tags, tree clean).
3. Artifact dependency analysis -- found Windows was also affected (new finding vs. Phase 8V-P6).
4. Version alignment (rc.4/versionCode 5) across all 8 canonical sources.
5. Build all 4 artifact families from final HEAD.
6. Restart Owner with wire-capture middleware, preflight, adb reverse.
7. Install/upgrade both Android apps in place; confirm identity continuity.
8. Physical smoke check: Scenario 3 reproduced cleanly on the rebuilt Clinic artifact.
9. Physical Scenario 2 (Retail): restriction proven physically and unconfounded; late renewal applied
   real Owner-side; **device disconnected before on-device confirmation**.
10. Windows Scenario 6: real multi-instance device-replacement proof, full PASS.
11. Windows Scenario 7: real plan-downgrade/overage/exception proof (Owner-side), full mechanics
    PASS; device-facing confirmation not reached (same disconnection).
12. Remaining Android-dependent gates (stale-assertion, backup/restore/export, invoice/payment, full
    Logcat/data-preservation) honestly disclosed as not reached, with real partial evidence recorded
    where it exists.
13. Re-ran product-backend regression after the version bump to confirm zero collateral impact.
14. Final decision, cleanup, commit -- no final tag (real, disclosed gaps remain).
