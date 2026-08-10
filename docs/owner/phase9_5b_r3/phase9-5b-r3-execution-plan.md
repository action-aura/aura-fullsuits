# Phase 9.5B-R3 — Execution Plan

1. Entry gate (done).
2. M1 — reproduce the exact failing test from a clean full-suite run;
   record command/versions/env/order/stack trace.
3. M2 — root-cause and fix; prove via isolated + sequence + reversed-order
   + 3 consecutive full clean runs.
4. M3 — audit the 3 service-exception call paths; resolve each as Branch A
   (user-facing → stable code + presentation-boundary translation) or
   Branch B (operator-only → proven unreachable, reclassified).
5. M4 — build the real route-family matrix from blueprint registration.
6. M5 — real Playwright evidence for every family, 4 viewports, 2 locales.
7. M6 — keyboard/focus/accessibility revalidation, actively re-tested.
8. M7 — real `pip-audit` dependency scan across Owner/commercial_runtime/
   Retail/Clinic requirements.
9. M8 — real secret scan (`detect-secrets` or equivalent).
10. M9 — actually execute preflight/migration/scheduler/backup/logging/
    health checks, not just cite "unchanged."
11. M10 — re-run localization/API security tests after any M3 change.
12. M11 — complete final matrix, Owner suite 3 consecutive clean runs,
    plus commercial_runtime/Retail/Clinic.
13. M12 — final catalog/surface re-check.
14. M13 — legacy repo close-out re-check.
15. M14 — verdict amendments, final decision, handover, commit, tag.
