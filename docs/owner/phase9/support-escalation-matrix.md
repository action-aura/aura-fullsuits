# Phase 9 — Support Escalation Matrix

| Level | Who | Handles | Escalates to |
|---|---|---|---|
| L1 | Named internal pilot support owner (`controlled-pilot-operating-model.md`) | Customer-reported install/activation issues, general questions | L2 for anything requiring infra or code access |
| L2 | Infra operator (whoever manages the staging host/secrets) | P1/P2 infra incidents, `key-rotation-runbook.md` actions, restore drills | L3 for P0 or anything suggesting compromise |
| L3 | Whoever owns the signing keys / commercial-ops decisions | P0 incidents, signing-key rotation authorization, pilot go/no-go calls | External help only if genuinely needed (e.g. hosting provider outage) |

## Response time targets (a small supervised pilot, not a 24/7 SLA)

- P0: same business day, best-effort within business hours (explicitly not a 24/7 commitment — matches
  `controlled-pilot-operating-model.md`'s "business hours only" scope decision).
- P1: within 1 business day.
- P2: within 3 business days.

## Contact information

Not recorded in this document (would be real PII of internal staff) — maintained separately, referenced
here structurally only.
