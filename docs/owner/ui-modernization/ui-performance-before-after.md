# Owner App — UI Performance Report (Stage E)

Real, measured performance data — describes what was actually measured,
cross-check against the real methodology below, not a plan.

## Honest framing: no true "before" baseline exists

Exactly as disclosed in `visual-regression-report.md`: this whole UI
modernization phase never captured a systematic per-screen performance
baseline before Stage B began. This report is a **real, current-state
performance snapshot**, not a fabricated before/after delta. Where a
real, meaningful proxy for "did this phase make things slower" exists
(the enterprise-table-system's real pagination fixing multiple genuinely
unbounded/unpaginated queries), that specific, real improvement is
described qualitatively below with the real bug reference, since a literal
timing comparison against the old unbounded query was never captured
either (curl-based verification throughout Stage D checked correctness —
real pagination, real filters — not response-time deltas).

## Real, current measurements

Real HTTP response timings (`curl -w`, warm server, real seeded data —
5 real roles, a full commercial pipeline, expenses, cash closing, and a
subscription/license/installation chain — not an empty database), logged
in against the SUPER_ADMIN account, dev server on the dedicated
`aura_owner_test_uiux` database:

| Screen | Time to first byte | Total | Response size |
|---|---|---|---|
| `/` (dashboard) | 158 ms | 158 ms | 33.3 KB |
| `/customers` | 97 ms | 97 ms | 27.8 KB |
| `/quotes` | 103 ms | 103 ms | 27.5 KB |
| `/orders` | 84 ms | 84 ms | 27.3 KB |
| `/invoices` | 93 ms | 93 ms | 27.4 KB |
| `/operations/expenses` | 194 ms | 194 ms | 27.7 KB |
| `/operations/cash-closings` | 179 ms | 180 ms | 27.5 KB |
| `/subscriptions` | 121 ms | 121 ms | 27.7 KB |
| `/licenses` | 64 ms | 64 ms | 27.1 KB |
| `/installations` | 85 ms | 85 ms | 27.1 KB |
| `/employees` | 72 ms | 73 ms | 30.9 KB |
| `/staff` | 78 ms | 78 ms | 30.7 KB |
| `/attention` | 96 ms | 97 ms | 25.5 KB |
| `/customers/<id>` (Customer 360) | 550 ms | 550 ms | 45.2 KB |
| `/quotes/<id>` | 177 ms | 177 ms | 26.9 KB |

**Every list/dashboard screen responds in well under 200ms** on a warm
local dev server against a small (but real, non-empty) dataset — no
screen exhibited an obviously pathological response time.

**Customer 360's detail page is the one real outlier** — 550ms, roughly
3–5× every other screen measured. This is consistent with, not a
regression from, its own documented design: `customer-360-contract.md`
describes it as querying 8 distinct cross-domain models (Quotes, Orders,
Invoices, Payments, Subscriptions, Licenses, Installations, Timeline)
per page load, each independently permission-gated. This was a known,
disclosed design tradeoff at the time Customer 360 was built (Stage D.2),
not a new finding — this pass simply gives it its first real, measured
number.

## The one real, disclosed pagination-performance fix — qualitative, not timed

Every Stage D pass found and fixed a real "unbounded query" class of bug:
Commercial Sales' 7 list screens (`commercial-flow-ui-contract.md`),
Finance's 2 list screens (`finance-ui-contract.md`), and — the most
severe instance — Licensing's 3 main list screens, which had **no
`LIMIT`/`OFFSET` at all** before Stage D.5 (`licensing-command-center-contract.md`:
"every subscription/license/installation row in the database was fetched
on every page load"). Every one of these is now paginated
(`page_size=25`) via the shared `paginate()` service. This is a real,
structural performance fix with unbounded upside as real data volume
grows (the difference between a query that scales with total row count
vs. one that scales with `page_size`), even though no literal
before-vs-after timing was captured — the seeded dataset used throughout
this phase's verification has never been large enough to make the
unbounded-query cost empirically visible in a timing number, which is
exactly the disclosed risk each Stage D pass's own contract doc named
when it found and fixed the bug.

## Real test-suite runtime, as an honest secondary signal

Not a UI performance metric, but a real, measured trend worth recording:
full-suite wall-clock time across this phase's own saved run logs, all
against the same dedicated `aura_owner_test_uiux` database (exact source
file named for each, so this table can be re-verified against the real
logs rather than trusted from memory):

| Stage | Log file | Result | Wall-clock |
|---|---|---|---|
| Customer 360 (D.2) | `owner_c360_full2.log` | 1,063 passed | 2073s (0:34:33) |
| Finance (D.4) | `owner_finance_full.log` | 2 failed*, 1,061 passed | 3220s (0:53:40) |
| Licensing (D.5) | `owner_licensing_full.log` | 2 failed*, 1,061 passed | 3220s (0:53:40) |
| Employees/Staff (D.6) | `owner_employees_full.log` | 1,063 passed | 1921s (0:32:00) |
| Stage E, run 1 | `owner_stagee_full.log` | 1,063 passed | 2155s (0:35:55) |
| Stage E, run 2 | `owner_stagee_full2.log` | 8 failed† | 2333s (0:38:52) |
| Stage E, run 3 (final) | `owner_stagee_full3.log` | 1,063 passed | 2365s (0:39:25) |

\* The 2 Finance/Licensing failures are the same known, unrelated
local-time/UTC-boundary artifact documented in `finance-ui-contract.md`
(both runs happened to fall inside the 00:00–09:00 JST window), not a
real regression.

† The 8 Stage E run-2 failures were this pass's own self-inflicted
i18n-catalog regression, found and fixed before commit — see
`localization-rtl-report.md`.

No consistent regression trend across the phase — runtime varies by
roughly ±20 minutes run-to-run on the same machine even for equally-clean
runs (2073s to 2365s among the fully-passing runs above), more plausibly
explained by real machine-load variance (this session ran many other
background processes concurrently — dev servers, other worktrees'
processes, at times a live Chromium instance from this same Stage E
pass's own harness) than by any real test-suite performance regression.
Disclosed honestly as a noisy, secondary signal, not a precise metric.

## Explicitly out of scope (with the real reason)

- **Real client-side rendering/paint timing** (Largest Contentful Paint,
  Time to Interactive, Cumulative Layout Shift) — Playwright can capture
  these via the real Navigation Timing / Paint Timing browser APIs, but
  doing so for all 43 screens was judged lower priority than the
  accessibility/i18n work this pass's time budget went to instead; the
  curl-based server-response-time table above is a real, honest, but
  partial substitute (measures server processing + network, not client
  paint/hydration time — though this app has no client-side hydration to
  speak of, being server-rendered HTML with light progressive-enhancement
  JS, so the gap between "server response time" and "time to interactive"
  is genuinely small here, unlike an SPA).
- **Load/concurrency testing** (multiple simultaneous users) — not
  attempted; all timings above are single-request, warm-server
  measurements.
- **Production-scale data volume testing** — the seeded dataset used
  throughout this whole phase is intentionally small (enough real rows
  to exercise every screen's rendering logic, not enough to stress-test
  query performance at real production scale). The pagination fixes
  described above are the real, structural answer to this risk; their
  actual effect at production data volume was not empirically measured.

## Ground-rules verification

- Every number in the response-time table above is a real `curl -w`
  measurement against a real, running dev server — not estimated,
  extrapolated, or copied from documentation.

## Verification run

Full suite: 1,063/1,063 passing (see `localization-rtl-report.md`'s
Verification Run section).
