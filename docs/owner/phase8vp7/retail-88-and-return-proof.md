# Phase 8V-P7 — Retail 88.00 and Return Proof — Final

## 88.00 case: NOT VERIFIED, real disclosed reason

Retail's Android POS UI has no discount-entry field (confirmed from source,
`RetailScreens.kt`: "no discount-entry UI exists in this source, so discount_pct stays at its default
of 0" -- the client sends only `product_id` + `quantity` per line). The real server route
(`POST /api/sub/retail/sales` in `retail_api.py`) does accept an optional per-line `discount_pct`, so
the 88.00 case (100.00 price, 20.00 discount = 20%, 10% tax on the discounted 80.00 = 88.00) is
reachable via a direct authenticated API call, but not through the UI as it exists today.

A direct `curl` attempt against the real local backend correctly returned `401 Authentication required`
-- the route requires a real staff session, and this session had no way to obtain one externally
(the already-authenticated session lives inside the Android app's own process and is not extractable to
an external HTTP client without either the original onboarding admin password, which no session
recorded, or creating a new staff account through an already-authenticated admin session, which needs
the same session to create). Not worked around by inserting rows directly into the local database, per
this session's own explicit prohibition on database-state fabrication.

## Return integrity: PASS, real, physical

See `scenario2-retail-late-renewal-final.md` for the full real evidence: `RET-000001-5eba23a1` created
against real `SALE-000002`, correct $100.00 refund amount (UI-computed, matched exactly), stock
restored by exactly the returned quantity (18 -> 19), net revenue correctly reduced ($100.00 ->
$0.00), real sale-return linkage. Duplicate-return protection specifically was not re-verified via a
second UI attempt this session -- disclosed, not assumed.

## Follow-up required (next session or product backlog)

Either: (a) add a discount-entry field to the Retail POS UI (a real product-completeness gap, not
strictly a Phase 8 licensing item, but blocks this specific financial-integrity proof), or (b) establish
a known, documented synthetic staff credential for this validation device so a direct authenticated API
call can exercise the 88.00 case without needing a new UI feature.
