# Phase 9 — Execution Plan

Order of execution this session, grouped to minimize rework:

1. Entry gate, branch, baseline docs (this set).
2. Milestone 1 — architecture and environment-separation design.
3. Milestone 2 — Retail test-suite isolation root-cause and fix (real engineering gate, done early
   since later regression milestones depend on a stable canonical runner).
4. Milestone 3 — deployment packaging (Gunicorn/WSGI, health/readiness endpoints).
5. Milestones 4-5 — host/network hardening templates, secret management.
6. Milestone 6 — PostgreSQL hardening (real, local).
7. Milestone 7 — backup automation + real restore drill (real, local).
8. Milestone 8 — logging/monitoring/alerting (real, local).
9. Milestone 9 — scheduled commercial operations.
10. Milestone 10 — security hardening review + real scans.
11. Milestone 11 — CI validation pipeline definition.
12. Milestones 12-14 — staging deployment / staging artifacts / private distribution: marked NOT
    VERIFIED per the local-only decision, with the full local-equivalent stack still produced where
    possible (e.g. the Compose stack itself IS deployed and validated locally, just not remotely).
13. Milestones 15-17 — pilot operating model, incident response, privacy/retention (documentation +
    real workflow code where applicable, no real customer).
14. Milestone 18 — capacity/resilience validation (real, local, synthetic).
15. Milestone 19 — final regression (real, fresh, from final HEAD).
16. Milestone 20 — final gate matrix and decision.
17. Commits per the suggested sequence; no Phase 9 tag this session (remote staging not verified).

Deviations from the literal milestone-by-milestone order will be noted inline where grouping is more
efficient (e.g. Docker/Compose artifacts are produced once in Milestone 3 and reused, not rebuilt per
milestone).
