# Phase 7V-F — Final Validation Matrix (Part S)

Legend: PASS / FAIL / NOT VERIFIED / N/A. No mandatory row marked PASS without evidence cited in
this directory's sibling documents.

## Windows

| Row | Result |
|---|---|
| Clinic frozen build + installer | **PASS** |
| Retail frozen build + installer | **PASS** |
| Clinic rc.1→rc.2 upgrade w/ data (Phase 7V) | **PASS** (carried forward, re-confirmed not regressed) |
| Retail rc.1→rc.2 upgrade w/ data (this session) | **PASS** |
| Live restricted-mode enforcement, both products | **PASS** (post-fix) |
| Data preservation, both products | **PASS** |
| Commercial-build TLS-bypass control | **PASS** (re-confirmed, unaffected by this session) |
| Trusted-time offline/warning/restricted correctness | **PASS** (post-fix; **FAIL before the fix**, honestly documented) |

## Android

| Row | Clinic | Retail |
|---|---|---|
| Signed build (APK) | **PASS** | **PASS** |
| Certificate continuity | **PASS** (real, on-device pull + verify) | **PASS** (real, on-device pull + verify) |
| Physical signed upgrade | **PASS** (real, on-device) | **NOT VERIFIED** |
| Physical data preservation | **PASS** (real, on-device) | **NOT VERIFIED** |
| Physical activation | **NOT VERIFIED** | **NOT VERIFIED** |
| Physical check-in | **NOT VERIFIED** | **NOT VERIFIED** |
| Physical offline/warning/restricted | **NOT VERIFIED** | **NOT VERIFIED** |
| Physical suspend/reactivate/deactivate | **NOT VERIFIED** | **NOT VERIFIED** |
| Physical Kotlin/Python authority | **NOT VERIFIED** | **NOT VERIFIED** |
| Physical Logcat privacy | **NOT VERIFIED** | **NOT VERIFIED** |
| Shared licensing-core correctness (proven via Windows) | **PASS** | **PASS** |

## Owner

| Row | Result |
|---|---|
| Real signing key generation/activation | **PASS** |
| Real license issuance (2 rounds, synthetic) | **PASS** |
| Real short signed offline policy assignment | **PASS** |
| Real installation status transitions (suspend/reactivate) | **PASS** |
| No public exposure | **PASS** (localhost-only, `adb reverse` for device reachability) |

## Products (automated)

| Row | Result |
|---|---|
| Combined 46-file suite | **PASS** (46/46, re-confirmed twice this session) |
| New regression tests for the trusted-time fix | **PASS** (4 new tests, all passing) |
| Financial integrity (88.00 case) | **PASS** (direct + live-in-RESTRICTED-state) |

## Summary of mandatory gaps preventing unconditional PASS

1. Retail Android physical upgrade/lifecycle — not completed (device disconnected).
2. Clinic Android physical activation-onward lifecycle — not completed (device disconnected).
3. Both products' physical Kotlin/Python authority and Logcat privacy — not completed.
4. Android AAB not rebuilt in the final round (stale relative to the final APK).

Per the governing spec's Definition of Done, these gaps are sufficient to withhold the final
unconditional PASS and the closing tag this session.
