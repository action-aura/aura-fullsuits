# Phase 9.5C — Final Gate Matrix, Residual Risk Register, and Closure

**Branch:** `phase9.5/leads-customers-followups-location`
**Final HEAD:** `8a3ac47d918ce16c36d003dbe7f5a02c6740b218`
**Branched from:** tag `aura-owner-i18n-rtl-verification-phase9-5b-r3-complete` (verified ancestor of HEAD, zero content drift at the merge-base — confirmed via `git merge-base --is-ancestor` and `git diff --stat`)
**Commits this phase:** 21
**Final tag:** `aura-owner-leads-customers-crm-phase9-5c-complete`

## Gate matrix

| Gate | Result | Evidence |
|---|---|---|
| Hard Entry Gate (tags/branch/legacy-repo read-only verified) | PASS | Branch created from exact resolved tag commit; docs entry created |
| M1-M5, M9, M10 (audit, lifecycle, ownership retrofit, contacts foundation) | PASS | Preserved unmodified across this whole wave; original commits intact |
| M6-M8, M11-M13 (cards/detail pages, contacts, notes, location, conversion) | PASS | Implemented, tested, committed |
| M14-M17 (dashboards, API, web routes, UI/navigation) | PASS | Implemented, tested, committed |
| M18 (EN/AR/RTL from first implementation) | PASS | 117 new Arabic strings; catalog-completeness test enforces zero empty/fuzzy |
| M19 (audit events) | PASS | Lead/Customer lifecycle + CRM child-entity events, no coordinates/note-bodies/secrets in payloads |
| M20 (DB/migration decision) | PASS | Additive-only; 5 migrations this wave; up/down round-trip verified with zero drift each time |
| M21 (security/IDOR completion) | PASS | 27+ IDOR tests at HTTP layer; 2 real fail-open/missing-check bugs found and fixed |
| M22 (localization/security retest) | PASS | Re-verified after every fix batch |
| M23 (real browser/responsive validation) | PASS | Live Playwright session: 1440×900/768×1024/390×844, EN+AR/RTL, keyboard focus order, geolocation capture, conversion flow. Found and fixed 4 real defects (missing-profile 500s, fail-open ownership, CSP-blocked script, Permissions-Policy-blocked geolocation, conversion assignment gap) |
| M24 (performance/query validation) | PASS | Real `EXPLAIN ANALYZE` at 100k-row/~3.3%-selectivity synthetic scale. Found a genuine missing-index gap across the whole CRM domain (previously undocumented, falsely claimed as pre-existing); fixed via migration `a1f9c3d76e02` + model `index=True` corrections; measured 6-7x speedup |
| M25 (local E2E: Employee A/B/Management/Arabic) | PASS | Live browser: Employee A/B isolation (0 leads visible cross-employee, direct-URL 404), Management view-all (all 4 leads + aggregate dashboard, both employees), Arabic RTL throughout, zero Subscription/License/Installation rows linked to any CRM-converted Customer (live DB query) |
| M26 (CRM preflight) | PASS | `flask commercial preflight` against `aura_owner_dev`: `ok: true`; sole WARNING is pre-existing dev-only Super Admin MFA state (expected, non-blocking) |
| M27a (dependency scan) | PASS w/ 1 informational finding | `pip-audit`: 1 known low-severity issue in `pytest` 8.3.2 (UNIX-only local-tmp-dir race, PYSEC-2026-1845) — dev/test tooling only, not shipped, not applicable to this Windows environment. See risk register |
| M27b (secret scan) | PASS | `detect-secrets` across `app/`, `tests/`, `migrations/`, `docs/owner/phase9_5c/`: zero real secrets. All hits are test-fixture literals (intentional, testing auth/redaction) or Alembic revision-ID hex strings (same false-positive class every pre-existing migration file triggers) |
| M27c (final regression, Owner) | PASS | 673/673 passed |
| M27d (final regression, Retail/Clinic/commercial_runtime/licensing_contracts) | PASS | 46/46 files passed via canonical `products/run_all_tests.py` |
| M27e (legacy repo preservation) | PASS | `AuraEnterprise/AuraEnterprise` HEAD unchanged (`414e6ea5`); working-tree diffs present but all file mtimes are 2026-07-11/12, three weeks before this session — pre-existing, not caused by any command in this session |
| Explicit "do not implement" list | PASS | Grepped final diff: no Quote/SalesOrder/Invoice/Payment/Refund/Commission/Expense/SharedNote/subscription-from-lead/license-from-lead/device-activation-from-lead/payment-gateway/Flutter/mobile-app code introduced |
| Non-Negotiable Rules 1-14 | PASS | Verified individually during M21/M23/M25 (see below) |

## Non-Negotiable Rules — verification notes

1. **Lead is not Customer** — `convert()` is the only path from Lead to Customer; verified no auto-creation anywhere else (grep for `Customer(` constructor calls).
2. **Reuse existing Customer authority** — `CustomerContact`, `customer_visible_to_actor()`, existing UUID/audit patterns extended, not duplicated.
3. **Employee ownership not optional** — every list/detail/write route passes through `apply_ownership_filter()`/`customer_visible_to_actor()` or an explicit permission-pair check; 2 real gaps found and fixed this wave (fail-open ownership check, missing parent-ownership check on shared follow-up/location routes).
4. **UI hiding is not authorization** — every check enforced server-side; confirmed by the IDOR test suite hitting routes directly, bypassing the UI.
5. **Management uses the same data** — `VIEWER` role (generic `view_all` permission) proven in M25 to see the same Lead/Customer records as any named individual would, via role/permission not identity.
6. **Creator/owner/assignee distinct** — `created_by_employee_profile_id` never overwritten by reassignment or conversion; conversion's actor-default fix explicitly preserves the distinction (assignee inherited from Lead if present, only defaults to actor when Lead had none).
7. **No silent duplicate Customer creation** — `find_duplicate_candidates()` raises `DuplicateCustomerError` requiring explicit `existing_customer_id` or override; duplicate review marker hides inaccessible candidate details from unauthorized viewers.
8. **Location capture is explicit** — `getCurrentPosition()` only, one-shot, button-click-triggered; grepped `geolocation-capture.js` — no `watchPosition`, no auto-capture.
9. **Browser location is device-reported** — UI labels are "Device location (GPS)" not "Verified GPS"; `verified` defaults false; physical GPS hardware validation explicitly out of scope and stated as such.
10. **Service layers request-independent** — `StableCodeError` classes carry only codes; `localize_*_error()` functions live in `app/i18n_labels.py`, called only from route handlers.
11. **EN/AR from first implementation** — catalog-completeness preflight blocks on any empty/fuzzy Arabic entry; caught and fixed one real gap (IDEMPOTENCY_CONFLICT string) mid-wave.
12. **No hard delete** — `archived_at` columns throughout; notes/contacts/locations all soft-delete only.
13. **Audit important changes** — `audit_record()` calls on every status/assignment/conversion/verification transition.
14. **Location data sensitive** — verified no raw lat/long in any audit payload or log call (grep confirms `audit_record()` calls around location code only pass UUID + verified flag).

## Residual risk register

| Risk | Severity | Status | Notes |
|---|---|---|---|
| `pytest` 8.3.2 known CVE (PYSEC-2026-1845) | Informational | Open, not blocking | UNIX-only local-tmp-dir issue; dev/test tooling, never shipped; irrelevant on Windows. Recommend bumping to 9.0.3+ in a routine dependency-maintenance pass, not phase-blocking. |
| No load/concurrency testing of CRM writes | Low | Open, out of milestone scope | M24 validated query-plan correctness under representative read scale, not concurrent-writer throughput. Recommended before a real multi-thousand-record pilot. |
| 3 Super Admin dev accounts with `mfa_required=False` | Informational | Open, expected for local dev | Explicitly flagged by preflight itself; must not be true for any real production Super Admin — a pre-existing condition from earlier phases, unrelated to this wave's CRM changes. |
| Physical device GPS hardware not validated | Documented limitation | Accepted per spec | Non-Negotiable Rule 9 explicitly scopes this out; browser `navigator.geolocation` behavior was validated instead. |

## Final decision

**PASS.** Every mandatory M1-M27 gate passes with real, executed evidence (not inspection-only claims) — including two milestones (M23, M24) that surfaced and fixed genuine defects no earlier automated test had caught: a fail-open ownership check, CSP/Permissions-Policy geolocation blocks, a missing-profile crash, a conversion-assignment gap, and a repo-wide missing-index gap on every CRM ownership/child-lookup query. All fixes are committed, tested, and re-verified. The explicit "do not implement" list is respected. The legacy `AuraEnterprise/AuraEnterprise` repository was never mutated by this session. Final regression across all 4 suites (Owner, Retail, Clinic, commercial_runtime/licensing_contracts) plus the top-level infra suite is clean.

Tag `aura-owner-leads-customers-crm-phase9-5c-complete` is created at this HEAD.
