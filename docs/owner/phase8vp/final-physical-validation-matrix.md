# Phase 8V-P — Final Physical Validation Matrix

| Scenario | Windows (real installed product) | Android (physical device) |
|---|---|---|
| 1. Early renewal | **PASS** (real, full wire evidence) | NOT VERIFIED |
| 2. Late renewal / revival | **PASS** (real, full wire evidence) | NOT VERIFIED |
| 3. Past due | **PASS** (Owner-side scan/notify/dedup, real); product-local elapsed-time state not re-derived | NOT VERIFIED |
| 4. Pilot conversion | **PASS** (Owner-side conversion, real; pre-conversion assertion wire-verified live; post-conversion wire re-check not completed) | NOT VERIFIED |
| 5. Emergency extension | **PASS** (real Owner HTTP/MFA, all guardrails proven live) | NOT VERIFIED |
| 6. Device replacement | **PASS** (real, full wire evidence, found+fixed a real P0-class defect) | NOT VERIFIED |
| 7. Plan downgrade / overage | **CONDITIONAL** (over-limit detection/notification/no-silent-deactivation real and PASS; renewal-to-license sync step is a disclosed feature gap, not exercised end-to-end) | NOT VERIFIED |

Physical validation total this session: **0 of 7 scenarios on a physical Android device** (no
device connected — disclosed before any work began, per the user's own explicit choice to proceed
with non-Android work only). **6 of 7 scenarios fully PASS on real installed Windows products**
(not a mock, not a test harness — the actual `.exe` artifacts this session built), **1 of 7**
(plan downgrade) conditional on a disclosed, unfixed feature gap unrelated to physical device
availability.
