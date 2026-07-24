# Phase 7V-F — Live Windows Restricted-Mode Closure (Part P)

Uses a real signed short-duration validation policy (30s check-in interval, 60s warning-start,
90s offline grace, `RESTRICT_COMMERCIAL_FEATURES_PRESERVE_DATA`), a genuine Owner outage, and real
elapsed wall-clock time — never an arbitrary database flag or fake product-side clock.

## The real defect this part uncovered (see `final-regression-report.md` for the full root cause)

The first attempt at this exact test (real Owner kill, real elapsed time, real check-in) revealed
that `current_state` never advanced past `ACTIVE_OFFLINE` no matter how much real time passed —
traced to `checkin_scheduler.py`'s `_resolve_anchor()` calling `rehydrate_anchor()` (documented as
a post-*restart* operation) fresh on every single check-in evaluation instead of once per
continuous-process lifetime, silently collapsing "elapsed offline time" back to ~0 on every call.
Fixed by caching the trusted-time anchor synchronously at the exact moment a sync succeeds
(`activation.py` and `checkin_scheduler.py::_persist_fresh_assertion`, new shared helpers in
`trusted_time.py`). Two new regression tests added and passing; full 46-file/585-test suite
re-confirmed green after the fix.

## CLINIC WINDOWS — live sequence, post-fix

1. Fresh activation against the real Owner → `ACTIVE_ONLINE`.
2. Owner killed (real `taskkill`, genuine `ECONNREFUSED`).
3. Waited 100 real seconds (Monitor-scheduled `sleep`, not simulated).
4. Single check-in → **`RESTRICTED`** (confirmed via the real HTTP response).
5. `GET /api/sub/clinic/patients` → `200`, real data returned (read preserved).
6. `POST /api/sub/clinic/patients` → `{"reason_code":"LICENSE_INACTIVE", ...}`, cleanly rejected,
   no partial write, no crash.
7. Activation UI (`/api/licensing/status`) remained accessible throughout.
8. No customer data left the machine at any point (all traffic inspected).

## RETAIL WINDOWS — live sequence, post-fix

Identical sequence, independently repeated:

1. Activation → `ACTIVE_ONLINE`.
2. Owner killed, 100 real seconds elapsed.
3. Check-in → **`RESTRICTED`**.
4. `GET /api/sub/retail/products` → `200`, real (empty, fresh instance) data.
5. `POST /api/sub/retail/products` → `LICENSE_INACTIVE`, cleanly rejected.
6. `core.retail.pricing.calculate_invoice(100.00, 20.00, 10%)` → **88.00**, unaffected by license
   state (confirmed both as a direct call and via the live RESTRICTED instance).

## Data access / stability

Both products: application remained stable throughout every kill/wait/restart cycle this session
(no crash observed in any of the ~15+ live activate/check-in/offline cycles run). No insecure
environment variable unlocked either commercial build (the frozen-build TLS-bypass fix from Phase
7V was re-confirmed still in effect — `sys.frozen` forces `verify_tls=True` regardless of env vars,
unchanged by this session's edits).

## Verdict

**PASS** for both products — live, real, with one genuine P0 found and fixed as a direct result of
performing this exact live test as specified.
