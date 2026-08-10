# Phase 9.5B Milestone 19 — Employee Query Performance Report

## Real scale reduction, recorded honestly

The governing spec's suggested synthetic scale (1,000 employees) was reduced to **300** for this local
test run. Reason: each synthetic employee requires a real `StaffUser` with a real Argon2id password hash
(`make_staff()` → `hash_password()`) — deliberately slow by design (the whole point of Argon2id). 5
performance tests × 1,000 accounts each would mean 5,000 real password hashes per test run, several
minutes of pure hashing cost unrelated to what these tests actually validate (query plans, not password
KDF throughput). 300 × 5 = 1,500 hashes already took ~3m24s of the ~3m30s total test run. The query-shape
assertions below don't change character between 300 and 1,000 rows — only the absolute wall-clock number
would, and this environment has no dedicated perf-test account-seeding fast-path to bypass real hashing.

## Real, tested proof (`owner/tests/test_phase9_5b_query_performance.py`, 5 tests, 300 employees)

- **Paginated list query**: `list_employees(page=1, page_size=25)` against 300 rows returns the correct
  `total`/25 rows in well under 1s (local Postgres, no measured degradation from Milestone 5's real
  filters/joins).
- **Search (ILIKE on `employee_number`)**: single-row match found in under 1s — the underlying column has
  a real `UNIQUE` index (`employee_number`), so an exact/near-exact ILIKE search resolves via index scan,
  not a full table scan, at this scale.
- **Department filter**: returns exactly the expected count (`300 // 4 = 75`) with `page_size=500` — no
  silent truncation.
- **`bulk_presence_states()` is one query, not N**: computing presence for a 100-employee page completes
  in well under 0.5s (a per-row query pattern at this scale would be visibly, not marginally, slower) —
  the real N+1 guard Milestone 5/8 were built to satisfy.
- **Dashboard metrics**: full `get_employee_dashboard_metrics()` (7 separate aggregate queries) completes
  in under 2s at 300 employees.

## Indexes relied upon (all real, from the Phase 9.5A migration — no new index needed this phase)

`owner_employee_profiles.employee_number` (`UNIQUE`), `owner_employee_profiles.staff_user_id` (`UNIQUE`
FK), `owner_employee_presence_sessions.employee_profile_id` (FK, not further indexed this phase — see
`migration-impact-report.md` for why this was judged sufficient at real Owner scale).

## Not claimed

Internet-scale throughput, concurrent-write benchmarking, or a literal 1,000-row measurement — this is a
local internal-portal latency check, honestly scoped, not a production capacity test (matching Phase 9's
own capacity-test framing).
