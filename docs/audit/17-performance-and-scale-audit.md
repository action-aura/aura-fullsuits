# Performance and Scale Audit

## What was actually measured (PROVEN) vs. what was not (UNVERIFIED)

The audit spec asks for tests at 1,000-50,000+ rows. **A reduced-scale synthetic
test was actually run in this pass** (not the full requested scale — time-boxed;
see limitations below), using a throwaway SQLite database in `.audit-temp/`
(deleted immediately after, per the audit's own temp-artifact rules) with a
schema mirroring Retail's `products`/`sales`/`sale_items` tables:

| Operation | Volume | Real measured time |
|---|---|---|
| Bulk insert products | 5,000 rows | 0.076s |
| Bulk insert sales | 3,000 rows | 0.047s |
| Bulk insert sale_items | 7,402 rows | 0.041s |
| Barcode-style lookup (`WHERE sku=? AND company_id=?`, **no index on `sku`**) | 20 lookups against the 5,000-row table | 13.3ms total / 0.67ms average |
| Dashboard-style aggregate (`COUNT(*), SUM(total)` over `sales`) | 3,000-row table | 1.00ms |

**This is a genuinely small-scale test (thousands, not tens of thousands of
rows) and was run on a single-user, unindexed, uncontended local SQLite file
with no concurrent access** — it demonstrates that nothing catastrophic
happens at this scale (no crash, no multi-second query), but it does **not**
prove performance at the 10,000/50,000/100,000-row scenarios the audit spec
actually asks for. Extrapolating linearly is not valid for the barcode-lookup
case specifically, because of the finding below.

## Real, evidence-based structural finding: no index on the barcode/SKU column

`products/retail/backend/database/schema.py` (per `06`, confirmed by the
data-integrity evidence sweep) declares **no `CREATE INDEX`** on
`products.sku`, `products.category_id`, `products.barcode`, or
`sales.created_at`/`sales.company_id` (beyond what a `UNIQUE` constraint
incidentally provides — `sales.sale_number` and `sales.idempotency_key` are
indexed as a side effect of their `UNIQUE` constraints, but `sku` has no such
constraint). A barcode scan at the POS is, today, a full table scan of
`products` filtered by `sku`/`company_id`. At 5,000 rows this audit measured
0.67ms average — invisible to a user. **At a real 50,000-100,000 product
catalog** (explicitly one of the audit's own target scenarios), a full table
scan for every single barcode scan, repeated dozens of times during a busy
shift, is a plausible, evidence-supported (not measured) source of POS lag —
this is a structural risk identified from the schema, not a benchmark result
at that scale. **Classified P2** — "high-volume performance failure" is
explicitly one of the audit's own P2 examples, and the schema-level evidence
(no index on the exact column a POS's most frequent query filters on)
directly supports that classification without needing the full 50k-row test
to justify flagging it.

## Clinic

Not independently benchmarked in this pass (time-boxed; Retail was prioritized
since it's the platform with the more POS-latency-sensitive workflow —
barcode scanning). `clinic_patients.patient_code` has a `UNIQUE` constraint
(incidentally indexed); patient-search-by-name (a likely common query) has no
declared index on `clinic_patients.name` — same structural risk class as
Retail's `sku`, not independently measured.

## What this audit did NOT do (explicit, per the spec's own honesty requirement)

- Did not generate or load 10,000+ invoices, 100,000+ invoice lines, 50,000+
  patients/appointments, or a large import file.
- Did not measure real application startup time end-to-end against a
  large database (only the Windows packaged-exe smoke tests' qualitative
  "starts quickly" observation exists, not a stopwatch measurement).
- Did not measure memory or CPU usage under load.
- Did not measure UI responsiveness (no device/browser interaction was
  performed).
- Did not benchmark Android at any scale (no device).

## Verdict

**UNVERIFIED at the scale the audit spec actually requires**, with one real,
small-scale data point (PROVEN, see table above) showing no catastrophic
behavior at thousands of rows, and one real, evidence-based structural risk
(PROVEN from schema, not from a benchmark) — missing indexes on the columns
most likely to matter at real commercial scale (barcode/SKU lookup, patient
name search). Neither product should be described as "scale-tested" or
"performance-validated" based on this audit; a dedicated load-testing pass
with the actual target row counts is recommended before selling into any
customer expected to reach a large catalog/patient base (Wave 2 work, `26`).
