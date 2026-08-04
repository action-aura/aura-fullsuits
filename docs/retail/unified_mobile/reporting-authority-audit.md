# Aura Retail Unified Mobile — Reporting Authority Audit (M5.6.0)

Real, cited audit of `products/retail/backend/api/retail_api.py`'s dashboard/reporting routes, before designing any unified reporting behavior. Every claim cites an exact file:line.

## 1. Dashboard (`GET /dashboard/stats` → `dashboard_stats()`, `retail_api.py:98-187`)

| Metric | Formula | Returns-adjusted | Status filter |
|---|---|---|---|
| `today_sales`/`yest_sales`/`month_sales` | `SUM(total)` by `date(created_at)`, minus `SUM(refund_amount)` from `returns` for the same window | **Yes** (`:117-127`) | None |
| `today_transactions`/`month_transactions` | `COUNT(*)` on `sales` | N/A | None |
| `hourly_data`, `payment_methods` (today only) | `SUM(total)`/`COUNT(*)` grouped by hour/method | **No** (gross) | None |
| `recent_sales` (last 8, all-time) | plain `ORDER BY created_at DESC LIMIT 8` | N/A | None |
| `low_stock_alerts` | count only (product-inventory-authority-audit.md #6, already known) | N/A | `status='active'` |

**Real, unclamped negative-net-revenue behavior, proven by a real test**: `today_sales -= today_returns` etc. (`:125-127`) has no floor — `retail_pricing_test.py:396-416`'s `test_revenue_becoming_negative_is_not_clamped` drives `today_sales` to exactly `-500` and asserts it. This is the exact "negative net sales... do not clamp" requirement M5.6.13 already anticipates — it is a real, tested legacy behavior, not a new invention.

## 2. Sales-trend (`GET /reports/sales-trend` → `report_sales_trend()`, `:1023-1044`)

```sql
SELECT date(created_at) as day, COALESCE(SUM(total),0), COUNT(*), COALESCE(AVG(total),0)
FROM sales WHERE company_id=? AND date(created_at) >= date('now','localtime',?)
GROUP BY day ORDER BY day
```

- **Daily bucketing only** — no weekly or monthly option exists at all. **Real, confirmed gap** the governing spec already named ("no canonical weekly bucketing exists," "no canonical monthly bucketing exists").
- **Not returns-adjusted** — only touches `sales`, never `returns`. Real, confirmed gap ("current sales-trend behavior is not returns-adjusted").
- No status filter, no branch filter, no category filter — only `days` is a query param.

A sibling route, `report_summary` (`:1087-1130`), computes period comparisons in Python (`datetime.now() - timedelta(...)`, not SQLite `date('now',...)`) — also not returns-adjusted, also no status filter.

## 3. Top-products (`GET /reports/top-products` → `report_top_products()`, `:1046-1070`)

```sql
SELECT p.name, p.sku, SUM(si.quantity), SUM(si.line_total), SUM(si.quantity*p.cost_price), ...
FROM sale_items si JOIN products p ON si.product_id=p.id JOIN sales s ON si.sale_id=s.id
WHERE s.company_id=? GROUP BY p.id ORDER BY units_sold DESC LIMIT ?
```

- **Quantity-ranked only** (`ORDER BY units_sold DESC`, `:1063`) — revenue is computed and returned but never used for ordering. Real, confirmed gap ("top-products currently ranks quantity only").
- **No date filter, no branch filter, no category filter** — only `limit`. Real, confirmed gaps ("lacks date filter," "lacks Branch filter").
- **Not returns-adjusted** — sums `sale_items` only, never `return_items`.
- **Real discrepancy source found, worth flagging for the port**: top-products' `revenue` is `SUM(si.line_total)` (re-derived from line items), while the dashboard/sales-trend's revenue is `SUM(sales.total)` (the pricing engine's own Python-`Decimal`-accumulated total, written once at sale time). These are two independently-computed numbers that *should* reconcile but are not the same code path — a real reason the unified reporting authority must pick exactly ONE authoritative revenue source and use it everywhere, not silently port two.

## 4. Eligible sale/return statuses

**Every sale and return row is unconditionally `status='completed'`** — the schema default (`schema.py:200`,`:227`) and the only value ever written (`retail_api.py:763`,`:988` both hard-code the literal `'completed'`). Grepping the whole backend for `UPDATE sales SET status` returns zero matches — **there is no void/cancel/draft/pending/failed-sale concept at all in this backend today.** No reporting query filters by status anywhere, because there has never been anything to filter out. This is a real, confirmed absence, not an oversight to silently replicate as "no filtering needed forever" — the unified schema (`sales.status`, already `TEXT NOT NULL DEFAULT 'completed'`, `Sales.sq`) keeps the column for exactly this reason: a future milestone could introduce a real status value, and reporting must filter on it explicitly rather than assume single-status forever.

## 5. Timestamps/timezone — real, honest complexity

- `created_at` is written as **local time**, explicitly and deliberately (`retail_api.py:654-656`'s own comment: *"the column default is UTC; all report queries filter by local date — keeping them consistent avoids late-night sales falling on the wrong day"*) — `now_local = datetime.now().strftime(...)`, no tzinfo, server/host OS local clock.
- **Reporting-side date handling is inconsistent between routes**: `dashboard_stats` computes boundaries in Python `datetime.now()` (`:104-106`); `report_sales_trend`/`report_payment_methods` use SQLite's own `date('now','localtime',?)` modifier (`:1036`,`:1081`); `report_summary` uses yet another Python `datetime.now() - timedelta(...)` (`:1094-1095`). Three different mechanisms, same intent.
- **No real timezone concept exists at all** — no `pytz`/`zoneinfo`, no per-company/per-branch timezone setting, no UTC-offset math anywhere. "Local" means the server process's own OS clock. There is no distinction between "the store's timezone" and "the device's timezone" in this backend — they are the same thing by construction (one server, one clock).

## 6. Currency — real, honest scope

`sales` has **no currency column at all** (`schema.py:186-204`). Currency is a **company-level setting** (`retail_settings.base_currency`, default `'USD'`, `retail_api.py:1192-1193`) — the sale-creation response includes `_settings(conn, cid)['base_currency']` (`:806`) as the *current* setting at request time, never frozen onto the row. If a company changes its base currency later, historical sales carry no record of what currency they were actually transacted in. The `payments` ledger table has real `currency`/`fx_rate` columns (`:1233`) but they are explicitly unused plumbing — comment `:1170-1171` states the ledger schema is *"currency-...ready without forcing the UI now"*, `fx_rate` is always `1`. **No reporting/dashboard query anywhere groups, filters, or converts by currency.** Multi-currency is not a real, exercised concept in this backend today — single implicit currency, company-wide.

## 7. Existing tests

`retail_pricing_test.py` (dashboard-relevant): `test_month_revenue_calculation`, `test_revenue_becoming_negative_is_not_clamped` (real, unclamped-negative proof, cited above), `test_dashboard_breakdown_internally_consistent`. **No test exists for `report_sales_trend`, `report_top_products`, `report_payment_methods`, or `report_summary` at all.** No test proves timezone handling. No test proves currency separation (none exists to prove).

## 8. Authoritative revenue source

`sales.total` — computed once server-side by the pricing engine at sale-creation time (`Decimal`-accumulated, quantized, `retail_api.py:702-717`), written directly into the `sales.total` column (`:759-766`). Dashboard, sales-trend, payment-methods, and report-summary all read this column directly via `SUM(total)`. **Top-products is the one exception**, re-deriving revenue from `SUM(sale_items.line_total)` instead (§3's discrepancy finding).

## Field-by-field classification for the unified reporting authority

| Behavior | Classification | Reasoning |
|---|---|---|
| Returns-adjusted today/month sales (dashboard) | LEGACY_REQUIRED | Real, tested legacy behavior (`test_revenue_becoming_negative_is_not_clamped`) |
| Unclamped negative net sales | LEGACY_REQUIRED | Same test — a real, deliberate legacy invariant, not a bug to fix |
| Daily-only sales-trend bucketing | DEPRECATED (as the only option) | Real, confirmed gap — weekly/monthly buckets are `NEW_COMPLETE_PRODUCT_REQUIREMENT` |
| Sales-trend not returns-adjusted | DEPRECATED | Real, confirmed gap — the unified authority must be returns-adjusted (`net_sales` per M5.6.3) |
| Top-products quantity-only ranking | DEPRECATED (as the only option) | Net-revenue ranking is `NEW_COMPLETE_PRODUCT_REQUIREMENT`, quantity ranking itself is `LEGACY_REQUIRED` (both must coexist) |
| Top-products no date/branch/category filter | DEPRECATED | Real, confirmed gaps — all three are `NEW_COMPLETE_PRODUCT_REQUIREMENT` |
| Two independent revenue-computation code paths (`sales.total` vs `SUM(line_total)`) | DEPRECATED | Real discrepancy risk — the unified authority picks exactly one source (M5.6.1) |
| No sale/return status filtering | UNIFIED_REQUIRED (explicit, forward-looking) | Legacy has nothing to filter (single status), but the unified schema already carries a real `status` column — reporting must filter on it explicitly, not assume single-status forever |
| Single implicit currency, no per-row currency | UNIFIED_REQUIRED (structural discipline, not multi-currency support) | Currency separation is still required by contract (M5.6.4) even with one currency in practice — avoids a false combined total if a second currency ever appears |
| Server-local-time date bucketing, no explicit timezone | UNIFIED_REQUIRED (documented policy, not silently ported) | The legacy "local time" assumption is real and must be a named, explicit business-timezone policy in the unified contract (M5.6.2), not left implicit |
