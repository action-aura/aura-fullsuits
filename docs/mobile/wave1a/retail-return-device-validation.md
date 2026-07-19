# Retail — Returns (Wave 1A, Part E)

## Test
On-device: opened Transactions, located `SALE-000005` (Test Product A, $110.00 total from the financial worked example), processed a full return.

## Backend confirmation
`GET /api/sub/retail/returns` confirms a real, persisted return:
- `RET-000002-32ae53c8`, sale_id 5 (`SALE-000005`), refund_amount **110.0**, refund_method `cash`, status `completed`, real client-generated idempotency_key `221bea18-c641-4a8f-be6d-084712a1e757`.

Two other returns were already present from earlier session testing (`RET-000001` against `SALE-000006`, and `RET-000003` against `SALE-000007`, both also `completed` with matching refund amounts) — consistent baseline, not concerning.

## Result
PASS — return correctly reversed the sale for the full authoritative amount, verified against the live backend (not just UI display).
