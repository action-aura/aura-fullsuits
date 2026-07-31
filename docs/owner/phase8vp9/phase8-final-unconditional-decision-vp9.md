# Phase 8V-P9 — Final Decision

## Verdict: PASS — unconditional. Tag `aura-commercial-licensing-operations-phase8-complete` created.

## Dimension-by-dimension

| Dimension | Verdict | Evidence |
|---|---|---|
| Commercial-enforcement architecture (subscription states, RESTRICTED/SUSPENDED enforcement) | PASS (not reopened) | Carried forward from Phase 8V-P7/prior; unchanged this session |
| EmergencyExtension | PASS (not reopened) | Carried forward; not redesigned per explicit scope boundary |
| Scenarios 1, 3, 4, 5, 6 | PASS (not repeated) | Carried forward, no technical reason to re-run (no relevant code changed) |
| Scenario 2 (physical renewal) | PASS (not repeated) | Carried forward |
| Scenario 7 — Owner-side mechanics | PASS (not repeated) | Carried forward |
| Scenario 7 — device-facing completion | **PASS (new this session)** | `scenario7-device-facing-final.md`: real overage, real 75s temp-exception + real elapsed-time expiry, real activation-block (`DEVICE_LIMIT_REACHED`), real physical Android check-in, no local override |
| Retail discount contract | NOT APPLICABLE TO CURRENT ANDROID PRODUCT CONTRACT | `retail-discount-contract-decision.md`, Branch B, evidence-cited |
| Retail 88.00 backend case | PASS (backend only, re-confirmed) | `retail-88-final-evidence.md` |
| Export contract (Retail + Clinic) | NOT IN CURRENT PRODUCT CONTRACT | `export-contract-decision.md`, `export-recovery-reconfirmation.md`, Branch B both products |
| Temporary-exception precision | PASS (P2 fixed) | `temporary-exception-precision-final.md`; 2 new tests |
| License-pepper preflight | PASS (hardened) | `license-pepper-preflight-final.md`; 3 new checks, 5 new tests, live CLI confirmed |
| **Stale-assertion / replay protection** | **PASS (P1 gap found and fixed this session)** | `stale-assertion-physical-final.md` — real monotonicity guard added, physically proven against the real rc.5 product process via a genuine-signature replay harness, not a unit test |
| Android rc.4 -> rc.5 upgrade (Retail + Clinic) | PASS | `final-build-installation-report.md`, `final-artifact-and-manifest-report.md`; real `adb install -r`, version confirmed both in `dumpsys` and on-device UI |
| Windows rc.4 -> rc.5 rebuild (Retail + Clinic) | PASS | Same docs; real PyInstaller rebuild, "Building because ...checkin_scheduler.py changed" confirms the correct trigger |
| Signing/cert continuity | PASS | Byte-identical SHA-256 cert fingerprints vs rc.4 for both apps |
| Installation continuity (no key retransmission, no reinstall) | PASS | Real upgrade-in-place (`-r`), Windows real `AURA_APP_DATA` dirs reused with existing device keys |
| Backup/restore (Clinic + Retail) | PASS (not repeated) | Carried forward; underlying code unchanged this session |
| Retail return integrity | PASS (not repeated) | Carried forward (`RET-000001-5eba23a1`) |
| Data preservation (rc.4->rc.5 upgrade + all ops) | PASS | `final-data-preservation.md`; identical real dashboard counts before/after |
| Local deactivation | PASS (not repeated, P2 unchanged) | `local-deactivation-reconfirm.md`; code path untouched this session |
| Final complete regression | PASS | `final-regression-report.md`: 405/405 Owner, 235/235 commercial_runtime, 194/194 Retail, 135/135 Clinic (all fresh, final HEAD) + clean Android builds; pre-existing unrelated test-isolation artifact documented transparently, proven non-functional and pre-existing |
| Final artifact/manifest audit | PASS | `final-artifact-and-manifest-report.md`; SHA-256 for all 6 artifacts, version alignment confirmed |
| Wire-capture / no secret leakage | PASS | Redaction policy confirmed intact throughout; scanned for leaked keys/peppers, none found |
| Logcat review (physical device) | PASS | No app-caused exceptions around the real check-in; pre-existing unrelated device tombstones (Jan-May 2026, unrelated apps) ruled out as caused by this session |
| Cleanup | PASS | All real Windows/proxy instances and Owner stopped, adb tunnels cleared, git status clean (only real source + new docs) |
| Original `AuraEnterprise` repo | UNTOUCHED (confirmed by construction) | Never opened this session |
| Scope boundaries respected (no Phase 9, no public deployment, no forbidden items) | PASS | Confirmed — nothing in this session touched any forbidden area |

## Real, unplanned finding this session

Part K's own investigation surfaced a genuine, previously-undocumented P1 gap: no assertion
monotonicity/replay guard existed anywhere in the real client ingestion path. This was not something
the phase's entry evidence claimed was already solved — it was found, fixed minimally, unit-tested,
and then physically proven against the real rebuilt product using a real Owner-signed (not forged)
older assertion substituted via a transparent proxy in front of the real Owner. This is the most
significant substantive outcome of Phase 8V-P9.

## Conclusion

All mandatory gates pass. The unconditional tag is created at the final commit of this session's work.
