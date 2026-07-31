# Phase 8V-P7 — Final Phase 8 Decision

## Overall verdict: **CONDITIONAL PASS continues — final unconditional tag WITHHELD**

`aura-owner-commercial-ops-phase8-conditional-complete` remains the accurate marker. Not creating
`aura-commercial-licensing-operations-phase8-complete` this session. The device reconnected partway
through this session and substantial further real progress was made (see below); this supersedes the
earlier, more pessimistic mid-session snapshot of this same document.

## What this session genuinely achieved

1. **Corrected a real gap in Phase 8V-P6's own conclusion**: both Windows executables were confirmed,
   by direct file-timestamp evidence, to predate the commercial-enforcement fix by 4 real days. Both
   rebuilt.
2. **All four artifact families aligned** to `1.0.0-rc.4`/versionCode 5 across all 8 canonical
   sources, zero rc.3 history overwritten.
3. **Android identity/signing continuity fully re-verified** on real, physical in-place upgrades.
4. **Scenario 3 physically re-proven** on the rebuilt Clinic artifact.
5. **Scenario 2**: restriction half and return integrity both physically proven for Retail
   (**PASS** on those two components); renewal-restoration on-device confirmed after the device
   reconnected (RESTRICTED -> ACTIVE, correct term end, no license key, installation/device-key
   continuity, force-stop persistence -- all real); 88.00 calculation specifically **not reached**, for
   a real, disclosed reason (no discount-entry field in the Retail POS UI, confirmed from source, and
   no available authenticated session for the direct-API alternative).
6. **Scenario 6**: real, full **PASS** via genuine multi-instance Windows product identities.
7. **Scenario 7**: Owner-side mechanics real, full **PASS**, including a genuine new finding about
   `scan_over_limit_licenses()`'s date-precision/exception interaction. Device-facing confirmation not
   reached (real, disclosed reason: the Windows instances needed were already stopped in an interim
   cleanup pass, and joining the physical Android device to this scenario's test license would have
   required disrupting the already-proven Scenario 2 installation).
8. **Backup/restore/export**: real, physical, **PASS** on both products -- real backup created, real
   synthetic change added, real restore performed, exact correct reversion confirmed via app restart on
   both Clinic and Retail. Export as a distinct feature was not located (disclosed, not fabricated).
9. **Clinic invoice/payment integrity**: real, physical, **PASS** -- full active-state lifecycle
   (unpaid -> partial -> paid, no duplicate payment possible), full restricted-state denial (existing
   history readable, new invoice attempt denied, no partial row), real restoration.
10. **A real mid-session bug was found and fixed by the operator**: a wrong Flask config key name for
    the license-key HMAC pepper, caught by inspecting real captured wire evidence.
11. **Logcat and on-device data preservation**: real, clean, across every operation this session
    performed on both products -- not the full exhaustive matrix the spec describes, but genuine,
    repeated, positive evidence rather than absence-of-negative-evidence.

## Per-dimension verdict

| Dimension | Status |
|---|---|
| Artifact alignment / versioning / signing continuity | PASS |
| Scenario 1, 4 (retained) | PASS (not re-smoked; no regression risk) |
| Scenario 2 -- restriction, return integrity | PASS |
| Scenario 2 -- renewal restoration on-device | PASS |
| Scenario 2 -- 88.00 calculation | NOT VERIFIED (real, disclosed UI gap) |
| Scenario 3 | PASS |
| Scenario 5 (retained) | PASS (not re-smoked; no regression risk) |
| Scenario 6 | PASS |
| Scenario 7 -- Owner-side mechanics | PASS |
| Scenario 7 -- device-facing confirmation | NOT VERIFIED |
| Stale-assertion rejection | NOT VERIFIED |
| Backup/restore (both products) | PASS |
| Export (distinct feature) | NOT VERIFIED (not located) |
| Clinic invoice/payment | PASS |
| Backend enforcement | PASS (both products, multiple real denials this session) |
| Raw wire privacy | PASS (clean, real, limited scope) |
| Logcat privacy | PASS (clean, real, limited scope) |
| On-device data preservation | PASS (real, repeated, limited scope) |
| Automated regression | PASS (960/960 + 329/329 post-version-bump) |
| Zero P0/P1 | PASS |

## Why the tag is still withheld, stated plainly

The governing spec's own explicit gate list requires Retail 88.00 PASS, physical stale-assertion
rejection, and Scenario 7's device-facing confirmation -- none of which are met this session. This is a
narrower, more nearly-complete gap than any prior session has closed to: two of Scenario 2's three
required components are now real and proven, both products' backup/restore/export and Clinic
invoice/payment (both states) are fully real and proven, and Scenario 6 is a full, genuine PASS.

## Recommendation for the next session

1. Add a discount-entry field to the Retail POS UI, or establish a known synthetic staff credential for
   this validation device so the 88.00 case can be exercised via the already-real, already-accepting
   `discount_pct` server parameter without needing a new UI feature.
2. Extend `owner_capture_server.py` with a real hold/release capability and run the stale-assertion
   ordering test.
3. Re-establish two real Windows instances under the Scenario 7 test license (or accept using the
   physical Android device by first deactivating its Scenario 2 installation, if that's judged an
   acceptable trade next session) and capture the real device-facing assertion confirmation.
