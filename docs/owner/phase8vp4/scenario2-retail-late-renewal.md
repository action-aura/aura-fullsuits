# Phase 8V-P4 — Scenario 2: Retail Late Renewal (Physical) — **CONDITIONAL PASS**

## Setup (real)

Retail subscription set `EXPIRED` for real via `transition_subscription()` (end_date backdated to
2026-07-20). Real device check-in immediately afterward:

```json
{"subscription_status": "EXPIRED", "current_state": "ACTIVE_ONLINE", ...}
```

**Real finding, not a bug**: the local `current_state` did **not** flip to `RESTRICTED` immediately.
Root-caused against `commercial_ops/state_resolution.py`: Owner's `resolve_commercial_state()`
correctly computed `EXPIRED` / `may_issue_assertion=False` for the *subscription*, but the device's
own signed assertion (issued minutes earlier, when the subscription still had a real future end
date) remains cryptographically valid on its own terms until it naturally expires -- Owner
correctly refuses to *reissue* a fresh one, but does not retroactively invalidate an
already-issued, still-time-valid one. This is the same "technical grace stays separate from
commercial grace" principle this project has documented since Phase 6 -- a subscription lapsing
does not instantly cut off a shop mid-day. Confirmed structurally correct, not exercised to its
full RESTRICTED end-state this session (would require either the already-issued assertion to
naturally expire, or a subscription that was already expired *before* the very first activation --
neither fits "start from ACTIVE_ONLINE" as this scenario's own precondition requires).

## Owner action: real late renewal, reviving EXPIRED -> ACTIVE

```
before: EXPIRED  2026-07-20
AFTER:  ACTIVE   2026-08-20   (renewal_status=APPLIED)
```

## Physical device check-in

```
Check-in complete.
Installation: e77bd448-b2be-4706-908a-d41d4a0b1f31   (unchanged)
Last check-in: 2026-07-30T20:45:18.071192+00:00
```

```json
{"assertion_expires_at": "2026-07-31T20:45:19.469769+00:00",
 "current_state": "ACTIVE_ONLINE",
 "installation_id": "e77bd448-b2be-4706-908a-d41d4a0b1f31",
 "subscription_status": "ACTIVE"}   // was EXPIRED
```

## Real sale executed post-renewal

Added `Synthetic Product One` ($100.00) to cart, charged $100.00 cash:

```
Payment successful. $100.00 collected. SALE-000001.
Stock: 20 -> 19 in stock (confirmed via product listing after the sale)
```

## What was and was not directly observed

| Requirement | Result |
|---|---|
| No re-entry of license key | **PASS** -- confirmed, protocol-level + Logcat |
| RESTRICTED -> ACTIVE_ONLINE transition, physically observed | **NOT OBSERVED** -- explained above; subscription-level EXPIRED->ACTIVE fully proven instead |
| Same installation ID / device key / slot count | **PASS** |
| Stale assertion cannot downgrade renewed state | Structurally guaranteed by the state machine (`checkin_succeeded -> ACTIVE_ONLINE`); not independently stress-tested this session |
| Retail data (product) unchanged by the renewal itself | **PASS** |
| The specific 88.00 (100 - 20% discount, +10% tax) case | **NOT EXERCISED** -- the mobile POS cart screen reached this session had no visible discount/tax input; a plain $100.00 cash sale was completed instead, proving the sale/stock pipeline works post-renewal. The 88.00 calculation itself was already proven correct on the real Windows Retail product in Phase 8V-P |
| New sale, stock mutation correct | **PASS** -- real SALE-000001, stock 20->19 |
| Force-stop/reopen persistence | **PASS** -- see `android-restart-persistence.md` |

## Result: **CONDITIONAL PASS** -- the renewal mechanics themselves (no key, continuity, real state
revival, real post-renewal sale) are fully proven; two specific sub-checks (visually observed
RESTRICTED state, the exact 88.00 case) were not reached this session for the structural reasons
above, disclosed rather than fabricated.
