# Phase 8V-P — Scenario 3: Past Due (Clinic) — REAL EVIDENCE

**Tier**: real Owner CLI job against real Postgres. **Windows/Android product on-device leg**: not
re-run this scenario (the local product-side warning/grace/restricted timing is elapsed-real-time
driven and was not re-derived — see rationale in `scenario-2-late-renewal-evidence.md`, same
reasoning applies here).

## Real setup

Real commercial policy (`past_due_start_days=1`, `payment_grace_days=5`,
`auto_expire_after_grace=True`) created through the real service layer. Real Clinic subscription
with `end_date` 2 days in the past, left `ACTIVE`.

## Real scan run, twice (dedup proof)

```
$ flask commercial expiry-scan --apply
{"scanned_count": 3, "notifications_created": 2, "notifications_deduped": 0, "transitioned_to_past_due": 1, "transitioned_to_expired": 0}

$ flask commercial expiry-scan --apply   # immediately again
{"scanned_count": 3, "notifications_created": 0, "notifications_deduped": 2, "transitioned_to_past_due": 0, "transitioned_to_expired": 0}
```

Real subscription genuinely transitioned `ACTIVE -> PAST_DUE` (confirmed by direct query
afterward). Second run creates zero new notifications and dedupes both — the job is safe to run
repeatedly, not just claimed to be.

## Real queue visibility

`get_queue_for_role("SUPPORT")` (the real Milestone 6 service, unmodified) shows 2 notification
items for this run — the same notifications the scan just wrote, genuinely queryable through the
same code path Owner's UI queue page uses.

## What was not re-derived this session

Owner telling a product the truth (`subscription_status: "PAST_DUE"`/`"EXPIRED"` in a real signed
assertion, confirmed live in Scenario 2's check-in response) and the *local* product-side
progression from that truth through warning -> commercial grace -> restricted is real elapsed-time
behavior, already proven correct with real elapsed time and physical hardware in Phase 7V-A. Not
re-run here for the same reason Phase 8V's own harness didn't attempt it.

## Result: **PASS** (Owner-side scan/notification/dedup/queue mechanics, real). Product-side
elapsed-time state progression and Android leg: **NOT VERIFIED** this session (real evidence
exists from Phase 7V-A, not re-derived).
